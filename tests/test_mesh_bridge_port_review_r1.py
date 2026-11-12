"""Review r1 of the BR-T #4–#18 port (b2e0fb85, contextless code-review
medium, 2026-11-11). Findings measured by a driver before fixing:
F2 (a DM row with no destination was RETRIED — counter bumped per attempt —
before the queue dead-lettered it), F3 (the six new counters were not
pre-seeded: healthy-zero vs meter-absent, hfm #9), F8 (MA-only no_sender /
unsendable rejections: no counter, an unthrottled WARNING per message), F9
(dm_destination computed twice on the RX path).
"""
import logging
from unittest.mock import patch

import pytest

from gateway import mesh_bridge as mb


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    """Queues under tmp, never the live ~/.config/meshanchor/mesh_bridge_queues."""
    monkeypatch.setattr(mb, "get_real_user_home", lambda: tmp_path)


@pytest.fixture
def bridge():
    from gateway.config import GatewayConfig, MeshtasticBridgeConfig, MeshtasticConfig
    cfg = GatewayConfig()
    cfg.mesh_bridge = MeshtasticBridgeConfig(
        enabled=True,
        primary=MeshtasticConfig(preset="LONG_FAST", name="lf", host="192.0.2.1"),
        secondary=MeshtasticConfig(preset="SHORT_TURBO", name="st", host="192.0.2.2"),
    )
    return mb.MeshtasticPresetBridge(config=cfg)


class _Radio:
    def __init__(self):
        self.sent = []

    def sendText(self, *a, **k):
        self.sent.append((a, k))
        return object()


def _status(q, msg_id):
    from utils.db_helpers import connect_tuned
    conn = connect_tuned(q._db_path)
    try:
        return conn.execute("SELECT status FROM messages WHERE id=?",
                            (msg_id,)).fetchone()[0]
    finally:
        conn.close()


NEW_COUNTERS = ("dm_no_destination", "direction_filtered", "queue_full_rejected",
                "receive_errors", "link_down_deferrals", "loop_fatal_caught",
                "queue_no_sender_rejected", "queue_unsendable_rejected")


def test_F3_every_new_counter_is_pre_seeded_zero(bridge):
    missing = [k for k in NEW_COUNTERS if k not in bridge.stats]
    assert not missing, f"meter-absent reads like healthy-zero: {missing}"
    assert all(bridge.stats[k] == 0 for k in NEW_COUNTERS)


def test_F2_a_dm_with_no_destination_is_refused_on_the_first_attempt(bridge):
    from gateway.message_queue import MessagePriority
    q = bridge._primary_to_secondary
    radio = _Radio()
    bridge._secondary_interface, bridge._secondary_connected = radio, True
    msg = mb.BridgedMeshMessage(
        source_preset="LONG_FAST", source_id="!aaaa0001", destination_id="",
        content="no dest", channel=0, is_broadcast=False)
    msg_id = q.enqueue(msg.to_payload(), destination="secondary",
                       priority=MessagePriority.HIGH, source="meshtastic")
    q.process_once(batch_size=5)
    assert _status(q, msg_id) == "dead_letter"        # one attempt, no retry
    assert bridge.stats["dm_no_destination"] == 1     # counted once
    assert radio.sent == []                           # never sendText(None)


@pytest.mark.parametrize("reason", ["no_sender", "unsendable"])
def test_F8_ma_only_rejections_are_counted_and_throttled(bridge, caplog, reason):
    q = bridge._primary_to_secondary
    pkt = {"decoded": {"portnum": "TEXT_MESSAGE_APP", "payload": b"x"},
           "fromId": "!aaaa0001", "toId": "^all", "to": 0xFFFFFFFF, "channel": 0}
    caplog.set_level(logging.DEBUG, logger="gateway.mesh_bridge")
    with patch.object(q, "enqueue_with_reason", return_value=(None, reason)):
        for i in range(5):
            pkt["decoded"]["payload"] = f"x{i}".encode()
            bridge._process_receive(pkt, "primary", "secondary", q)
    assert bridge.stats[f"queue_{reason}_rejected"] == 5
    warns = [r for r in caplog.records if r.levelno == logging.WARNING
             and "REJECTED by the queue" in r.getMessage()]
    assert len(warns) == 1, "one WARNING per rejection kind per interval"
    drops = [r.getMessage() for r in caplog.records
             if r.getMessage().startswith(f"mesh_bridge drop leg=primary reason={reason}:")]
    assert len(drops) == 5                            # every loss still named


def test_F9_rx_path_reads_the_dm_destination_once(bridge):
    q = bridge._primary_to_secondary
    pkt = {"decoded": {"portnum": "TEXT_MESSAGE_APP", "payload": b"hi"},
           "fromId": "!aaaa0001", "toId": "!1234abcd", "to": 0x1234ABCD, "channel": 0}
    with patch.object(mb, "dm_destination", wraps=mb.dm_destination) as spy:
        bridge._process_receive(pkt, "primary", "secondary", q)
    assert spy.call_count == 1
