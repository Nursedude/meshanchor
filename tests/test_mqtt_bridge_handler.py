"""Tests for the Thread-2 step-4 /e/ ACK pipeline in MQTTBridgeHandler
(ported from MeshForge 204da9e). Honest Meshtastic delivery confirmation in
mqtt_bridge mode: a wantAck DM's ROUTING_APP ACK is decoded from the encrypted
/e/ ServiceEnvelope topic → delivery_counters CONFIRMED / DROPPED, with no
fromradio read.
"""

import os
import sys
import threading
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from gateway.config import GatewayConfig
from gateway.mqtt_bridge_handler import MQTTBridgeHandler


def _build(ack_enabled=True):
    config = GatewayConfig()
    config.rns.meshtastic_ack_consumption_enabled = ack_enabled
    return MQTTBridgeHandler(
        config=config, node_tracker=MagicMock(), health=MagicMock(),
        stop_event=threading.Event(), stats={},
        stats_lock=threading.Lock(), message_queue=MagicMock())


class TestAckConsumptionStartup:
    """ACTIVE when crypto is available; an honest INERT warning only when not."""

    def test_active_log_when_crypto_available(self, caplog):
        import logging
        with patch('gateway.mqtt_bridge_handler.crypto_available',
                   return_value=True):
            with caplog.at_level(logging.INFO):
                h = _build(ack_enabled=True)
        assert any("ACTIVE" in r.message and "/e/" in r.message
                   for r in caplog.records)
        assert not [r for r in caplog.records if "INERT" in r.message]
        assert h.ack_tracker is not None

    def test_inert_warning_when_crypto_unavailable(self, caplog):
        import logging
        with patch('gateway.mqtt_bridge_handler.crypto_available',
                   return_value=False):
            with caplog.at_level(logging.WARNING):
                _build(ack_enabled=True)
        assert any("INERT" in r.message for r in caplog.records)

    def test_silent_when_flag_off(self, caplog):
        import logging
        with caplog.at_level(logging.INFO):
            _build(ack_enabled=False)
        assert not [r for r in caplog.records
                    if "ACK consumption" in r.message or "INERT" in r.message]


class TestEServiceEnvelopeAckIngestion:
    """A decoded ROUTING_APP that matches an in-flight DM → CONFIRMED/DROPPED;
    TX registers the packet_id; cost-guarded decode."""

    def _rec(self, monkeypatch):
        rec = MagicMock()
        monkeypatch.setattr('gateway.delivery_counters.record', rec)
        return rec

    def test_confirmed_on_positive_ack(self, monkeypatch):
        from gateway.delivery_counters import DeliveryState
        rec = self._rec(monkeypatch)
        h = _build()
        h.ack_tracker.register(0xAA01, "msg-7")
        dp = MagicMock(request_id=0xAA01)
        dp.routing_error_name.return_value = "NONE"
        h._handle_routing_envelope(dp)
        rec.assert_called_once_with(DeliveryState.CONFIRMED,
                                    msg_id="msg-7", protocol="meshtastic")
        assert h.stats.get("mesh_ack_confirmed") == 1

    def test_dropped_on_nak(self, monkeypatch):
        from gateway.delivery_counters import DeliveryState, DropReason
        rec = self._rec(monkeypatch)
        h = _build()
        h.ack_tracker.register(0xBB02, "msg-9")
        dp = MagicMock(request_id=0xBB02)
        dp.routing_error_name.return_value = "MAX_RETRANSMIT"
        h._handle_routing_envelope(dp)
        args, kwargs = rec.call_args
        assert args[0] == DeliveryState.DROPPED
        assert kwargs["msg_id"] == "msg-9"
        assert kwargs["drop_reason"] == DropReason.RETRIES_EXHAUSTED
        assert kwargs["note"] == "meshtastic_nak:MAX_RETRANSMIT"
        assert h.stats.get("mesh_ack_failed") == 1

    def test_unmatched_routing_is_noop(self, monkeypatch):
        rec = self._rec(monkeypatch)
        h = _build()
        dp = MagicMock(request_id=0xDEAD)
        dp.routing_error_name.return_value = "NONE"
        h._handle_routing_envelope(dp)
        rec.assert_not_called()

    def test_protobuf_message_skips_when_disabled(self, monkeypatch):
        h = _build(ack_enabled=False)
        called = {"n": 0}
        monkeypatch.setattr('gateway.mqtt_bridge_handler.decode_service_envelope',
                            lambda *a, **k: called.__setitem__("n", called["n"]+1))
        h._handle_protobuf_message("msh/2/e/LongFast/!x", b"\x00")
        assert called["n"] == 0

    def test_protobuf_message_skips_when_no_pending(self, monkeypatch):
        h = _build(ack_enabled=True)
        called = {"n": 0}
        monkeypatch.setattr('gateway.mqtt_bridge_handler.decode_service_envelope',
                            lambda *a, **k: called.__setitem__("n", called["n"]+1))
        h._handle_protobuf_message("msh/2/e/LongFast/!x", b"\x00")
        assert called["n"] == 0

    def test_protobuf_message_decodes_routing_when_pending(self, monkeypatch):
        from gateway.delivery_counters import DeliveryState
        rec = self._rec(monkeypatch)
        h = _build(ack_enabled=True)
        h.ack_tracker.register(0xCAFE, "msg-x")
        dp = MagicMock(request_id=0xCAFE, is_routing=True)
        dp.routing_error_name.return_value = "NONE"
        monkeypatch.setattr('gateway.mqtt_bridge_handler.decode_service_envelope',
                            lambda *a, **k: dp)
        h._handle_protobuf_message("msh/2/e/LongFast/!x", b"\x01\x02")
        rec.assert_called_once_with(DeliveryState.CONFIRMED,
                                    msg_id="msg-x", protocol="meshtastic")

    def test_maybe_register_skips_broadcast(self):
        h = _build()
        h._maybe_register_ack(0x1234, 0xFFFFFFFF, None, record_sent=False)
        assert h.ack_tracker.pending_count() == 0

    def test_maybe_register_dm(self, monkeypatch):
        self._rec(monkeypatch)
        h = _build()
        h._maybe_register_ack(0x1234, 0xAABB0001, "q-1", record_sent=False)
        assert h.ack_tracker.resolve(0x1234) == ("q-1", "meshtastic")

    def test_channel_keys_default_downlink_extra(self):
        h = _build()
        h.config.meshtastic.downlink_psk = "Zm9vYmFy"
        h.config.meshtastic.channel_keys = ["YmF6cXV4", "Zm9vYmFy"]
        keys = h._channel_keys()
        from utils.meshtastic_se_crypto import DEFAULT_KEY_B64
        assert keys[0] == DEFAULT_KEY_B64
        assert "Zm9vYmFy" in keys and "YmF6cXV4" in keys
        assert keys.count("Zm9vYmFy") == 1
