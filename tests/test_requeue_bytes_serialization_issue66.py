"""
Issue #66 regression: _requeue_failed_message must accept bytes in
CanonicalMessage.content and msg.metadata without dropping the message.

The MeshAnchor mirror of Issue #66 step 3 (commit 0cbf7923) copied the
ack synthesis surface but skipped the bytes->str guard that MeshForge has
in _rns_bridge_xform.py. Production journal evidence on meshanchor-server
(2026-05-18) showed:

    [gateway.rns_bridge] ERROR: Failed to persist message for retry:
        Object of type bytes is not JSON serializable

firing every 60-120 seconds. The failure is at QueuedMessage.to_dict
(message_queue.py:378), which calls json.dumps(self.payload). Raw bytes
in `payload['message']` or anywhere inside `payload['metadata']` raise
TypeError, the catch at _requeue_failed_message logs WARNING and returns
False, and the message is dropped with no retry record. No row in the
queue means the ack-pending substrate has no input, which is the
operator-visible "sync/ack is unreliable" symptom.

These three tests fail against the broken pre-fix code and pass once the
content + metadata bytes coercion is in place. Reuses the queue-wiring
pattern from tests/test_ack_synthesis_e2e_issue66.py.
"""

import json
import sys
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


def _mock_gateway_config_min():
    """Minimal config matching the existing integration-test fixture."""
    config = MagicMock()
    config.enabled = True
    config.auto_start = False
    config.bridge_mode = "message_bridge"
    config.default_route = "bidirectional"
    config.routing_rules = []
    config.log_level = "DEBUG"
    config.log_messages = True
    config.rns = MagicMock()
    config.rns.config_dir = None
    config.meshtastic = MagicMock()
    config.meshtastic.channel = 0
    config.meshtastic.connection_type = "tcp"
    config.meshtastic.host = "localhost"
    config.meshtastic.port = 4403
    config.meshtastic.failover_enabled = False
    config.meshtastic.load_balancer_enabled = False
    config.meshtastic.gateway_heartbeat_enabled = False
    return config


@pytest.fixture
def bridge_with_real_queue(tmp_path):
    """Bridge with a real PersistentMessageQueue wired in (radio handlers mocked)."""
    with patch("gateway.rns_bridge.GatewayConfig") as MockConfig, \
         patch("gateway.rns_bridge.UnifiedNodeTracker"), \
         patch("gateway.rns_bridge.BridgeHealthMonitor"), \
         patch("gateway.rns_bridge.DeliveryTracker"), \
         patch("gateway.rns_bridge.MeshtasticHandler") as MockHandler, \
         patch("gateway.rns_bridge.ReconnectStrategy") as MockReconnect, \
         patch("gateway.rns_bridge.HAS_CIRCUIT_BREAKER", False), \
         patch("gateway.rns_bridge.CircuitBreakerRegistry", None), \
         patch("gateway.rns_bridge.HAS_PERSISTENT_QUEUE", False), \
         patch("gateway.message_routing.CLASSIFIER_AVAILABLE", False), \
         patch("gateway.rns_bridge.HAS_SERVICE_CHECK", False), \
         patch("gateway.rns_bridge.HAS_EVENT_BUS", False), \
         patch("gateway.rns_bridge.HAS_RNS_SNIFFER", False):

        mock_config = _mock_gateway_config_min()
        MockConfig.load.return_value = mock_config
        MockHandler.return_value = MagicMock(is_connected=False)
        MockReconnect.for_rns.return_value = MagicMock()

        from gateway.rns_bridge import RNSMeshtasticBridge
        b = RNSMeshtasticBridge(config=mock_config)

        from gateway.message_queue import PersistentMessageQueue
        db_path = tmp_path / "requeue_bytes.db"
        b._persistent_queue = PersistentMessageQueue(db_path=str(db_path))
        # Issue #67: enqueue() now refuses destinations without a
        # registered sender. Wire a no-op sender so the bytes-coercion
        # path under test reaches the DB row insert it's pinning.
        b._persistent_queue.register_sender("meshtastic", lambda payload: True)
        b._persistent_queue.register_sender("meshcore", lambda payload: True)
        b._persistent_queue.register_sender("rns", lambda payload: True)
        try:
            yield b, db_path
        finally:
            b._persistent_queue.stop_processing()


def _build_msg(content, metadata=None):
    """Build a CanonicalMessage that mirrors the inbound shape that bites in prod."""
    from gateway.canonical_message import CanonicalMessage
    return CanonicalMessage(
        source_network="rns",
        source_address="deadbeefcafef00d",
        destination_address="!aabbccdd",
        destination_network="meshtastic",
        content=content,
        metadata=metadata or {},
    )


def _read_payload_row(db_path):
    """Read back the single payload row that _requeue_failed_message wrote."""
    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute("SELECT payload FROM messages").fetchall()
        return [json.loads(r[0]) for r in rows]
    finally:
        conn.close()


class TestRequeueBytesContentIssue66:
    """The content-side half of the journal-trace fix."""

    def test_requeue_with_bytes_content_persists(self, bridge_with_real_queue):
        bridge, db_path = bridge_with_real_queue
        msg = _build_msg(content=b"hello mesh")

        assert bridge._requeue_failed_message(msg, destination="meshtastic") is True

        payloads = _read_payload_row(db_path)
        assert len(payloads) == 1
        assert payloads[0]["message"] == "hello mesh"

    def test_requeue_lossy_bytes_does_not_crash(self, bridge_with_real_queue):
        bridge, db_path = bridge_with_real_queue
        msg = _build_msg(content=b"\xff\xfe\xfd")

        assert bridge._requeue_failed_message(msg, destination="meshtastic") is True

        payloads = _read_payload_row(db_path)
        assert len(payloads) == 1
        # errors='replace' substitutes U+FFFD for un-decodable bytes; the
        # exact replacement string is implementation-defined but must be str.
        assert isinstance(payloads[0]["message"], str)
        assert payloads[0]["message"] != ""

    def test_requeue_with_non_string_non_bytes_content_persists(self, bridge_with_real_queue):
        """Defensive: content arrives as None (or any non-str/bytes) -> empty string, not crash."""
        bridge, db_path = bridge_with_real_queue
        msg = _build_msg(content=None)

        assert bridge._requeue_failed_message(msg, destination="meshtastic") is True

        payloads = _read_payload_row(db_path)
        assert len(payloads) == 1
        assert payloads[0]["message"] == ""


class TestRequeueBytesMetadataIssue66:
    """The metadata-side half — what actually trips the journal trace today."""

    def test_requeue_with_bytes_in_metadata_persists(self, bridge_with_real_queue):
        bridge, db_path = bridge_with_real_queue
        msg = _build_msg(
            content="plain text",
            metadata={
                "lxmf_title": b"weather report",
                "lxmf_sender_key": b"\x01\x02\x03",
                "hop_count": 2,
            },
        )

        assert bridge._requeue_failed_message(msg, destination="meshtastic") is True

        payloads = _read_payload_row(db_path)
        assert len(payloads) == 1
        meta = payloads[0]["metadata"]
        assert meta["lxmf_title"] == "weather report"
        assert isinstance(meta["lxmf_sender_key"], str)
        assert meta["hop_count"] == 2  # non-bytes passthrough preserved

    def test_requeue_with_nested_bytes_metadata_persists(self, bridge_with_real_queue):
        bridge, db_path = bridge_with_real_queue
        msg = _build_msg(
            content="plain text",
            metadata={
                "lxmf_fields": {"raw": b"nested", "tag": "keep-me"},
                "trace": [b"hop-a", b"hop-b"],
            },
        )

        assert bridge._requeue_failed_message(msg, destination="meshtastic") is True

        payloads = _read_payload_row(db_path)
        assert len(payloads) == 1
        meta = payloads[0]["metadata"]
        assert meta["lxmf_fields"] == {"raw": "nested", "tag": "keep-me"}
        assert meta["trace"] == ["hop-a", "hop-b"]
