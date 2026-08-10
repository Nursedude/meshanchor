"""
Tests for RNS-Meshtastic Bridge Service (rns_bridge.py).

Covers BridgedMessage, RNSMeshtasticBridge init/properties/state,
circuit breaker delegation, MQTT filtering, routing rules (legacy +
classifier), message bridging loops, callback systems, persistent queue
integration, and module-level headless helper functions.

All external dependencies (RNS, LXMF, meshtastic, pubsub) are mocked.

Run: python3 -m pytest tests/test_rns_bridge.py -v
"""

import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from queue import Queue, Full, Empty
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from gateway.bridge_health import BridgeStatus, MessageOrigin


# ---------------------------------------------------------------------------
# BridgedMessage
# ---------------------------------------------------------------------------

class TestBridgedMessage:
    """Tests for BridgedMessage dataclass."""

    def _make_msg(self, **kwargs):
        from gateway.rns_bridge import BridgedMessage
        defaults = dict(
            source_network="meshtastic",
            source_id="!aabb0042",
            destination_id=None,
            content="Hello world",
        )
        defaults.update(kwargs)
        return BridgedMessage(**defaults)

    def test_defaults(self):
        msg = self._make_msg()
        assert msg.source_network == "meshtastic"
        assert msg.content == "Hello world"
        assert msg.title is None
        assert msg.is_broadcast is False
        assert msg.via_internet is False
        assert msg.origin == MessageOrigin.UNKNOWN

    def test_post_init_sets_timestamp(self):
        msg = self._make_msg()
        assert msg.timestamp is not None
        assert isinstance(msg.timestamp, datetime)

    def test_auto_timestamp_is_recent(self):
        before = datetime.now()
        msg = self._make_msg()
        after = datetime.now()
        assert before <= msg.timestamp <= after

    def test_post_init_sets_metadata(self):
        msg = self._make_msg()
        assert msg.metadata == {}

    def test_explicit_timestamp_preserved(self):
        ts = datetime(2026, 1, 1, 12, 0, 0)
        msg = self._make_msg(timestamp=ts)
        assert msg.timestamp == ts

    def test_explicit_metadata_preserved(self):
        msg = self._make_msg(metadata={"channel": 3})
        assert msg.metadata == {"channel": 3}

    def test_with_all_fields(self):
        ts = datetime(2026, 1, 9, 12, 0, 0)
        msg = self._make_msg(
            source_network="rns",
            source_id="abc123",
            destination_id="def456",
            content="Test message",
            title="Test Title",
            timestamp=ts,
            is_broadcast=True,
            metadata={"priority": "high"},
        )
        assert msg.source_network == "rns"
        assert msg.title == "Test Title"
        assert msg.timestamp == ts
        assert msg.is_broadcast is True
        assert msg.metadata == {"priority": "high"}

    def test_should_bridge_default(self):
        msg = self._make_msg()
        assert msg.should_bridge() is True

    def test_should_bridge_mqtt_filter_off(self):
        msg = self._make_msg(via_internet=True)
        assert msg.should_bridge(filter_mqtt=False) is True

    def test_should_bridge_filters_mqtt_via_internet(self):
        msg = self._make_msg(via_internet=True)
        assert msg.should_bridge(filter_mqtt=True) is False

    def test_should_bridge_filters_mqtt_origin(self):
        msg = self._make_msg(origin=MessageOrigin.MQTT)
        assert msg.should_bridge(filter_mqtt=True) is False

    def test_should_bridge_allows_radio_origin(self):
        msg = self._make_msg(origin=MessageOrigin.RADIO)
        assert msg.should_bridge(filter_mqtt=True) is True

    def test_should_bridge_allows_radio_not_via_internet(self):
        msg = self._make_msg(via_internet=False, origin=MessageOrigin.RADIO)
        assert msg.should_bridge(filter_mqtt=True) is True


# ---------------------------------------------------------------------------
# Helpers for bridge construction with full mocking
# ---------------------------------------------------------------------------

def _mock_gateway_config(**overrides):
    """Create a mock GatewayConfig with sensible defaults."""
    config = MagicMock()
    config.enabled = overrides.get("enabled", True)
    config.auto_start = False
    config.bridge_mode = overrides.get("bridge_mode", "message_bridge")
    config.default_route = overrides.get("default_route", "bidirectional")
    config.routing_rules = overrides.get("routing_rules", [])
    config.log_level = "DEBUG"
    config.log_messages = True
    config.enable_websocket = overrides.get("enable_websocket", False)
    config.rns = MagicMock()
    config.rns.config_dir = None
    config.meshtastic = MagicMock()
    config.meshtastic.channel = 0
    config.meshtastic.connection_type = "tcp"
    config.meshtastic.host = "localhost"
    config.meshtastic.port = 4403
    # Disable dual-radio features by default in unit tests
    config.meshtastic.failover_enabled = False
    config.meshtastic.load_balancer_enabled = False
    config.meshtastic.gateway_heartbeat_enabled = False
    return config


@pytest.fixture
def bridge():
    """Create a fully-mocked RNSMeshtasticBridge for unit testing."""
    with patch("gateway.rns_bridge.GatewayConfig") as MockConfig, \
         patch("gateway.rns_bridge.UnifiedNodeTracker") as MockTracker, \
         patch("gateway.rns_bridge.BridgeHealthMonitor") as MockHealth, \
         patch("gateway.rns_bridge.DeliveryTracker") as MockDelivery, \
         patch("gateway.rns_bridge.MeshtasticHandler") as MockHandler, \
         patch("gateway.rns_bridge.ReconnectStrategy") as MockReconnect, \
         patch("gateway.rns_bridge.HAS_CIRCUIT_BREAKER", True), \
         patch("gateway.rns_bridge.CircuitBreakerRegistry") as MockCB, \
         patch("gateway.rns_bridge.HAS_PERSISTENT_QUEUE", False), \
         patch("gateway.message_routing.CLASSIFIER_AVAILABLE", False), \
         patch("gateway.rns_bridge.HAS_SERVICE_CHECK", False), \
         patch("gateway.rns_bridge.HAS_EVENT_BUS", False), \
         patch("gateway.rns_bridge.HAS_RNS_SNIFFER", False):

        mock_config = _mock_gateway_config()
        MockConfig.load.return_value = mock_config

        mock_handler = MagicMock()
        mock_handler.is_connected = False
        MockHandler.return_value = mock_handler

        mock_reconnect = MagicMock()
        MockReconnect.for_rns.return_value = mock_reconnect

        mock_cb_registry = MagicMock()
        MockCB.return_value = mock_cb_registry

        from gateway.rns_bridge import RNSMeshtasticBridge
        b = RNSMeshtasticBridge(config=mock_config)
        yield b


@pytest.fixture
def bridge_no_cb():
    """Bridge with circuit breaker disabled."""
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

        mock_config = _mock_gateway_config()
        MockConfig.load.return_value = mock_config
        MockHandler.return_value = MagicMock(is_connected=False)
        MockReconnect.for_rns.return_value = MagicMock()

        from gateway.rns_bridge import RNSMeshtasticBridge
        b = RNSMeshtasticBridge(config=mock_config)
        yield b


# ---------------------------------------------------------------------------
# RNSMeshtasticBridge — initial state
# ---------------------------------------------------------------------------

class TestBridgeInit:
    """Tests for bridge initialization and default state."""

    def test_not_running_initially(self, bridge):
        assert bridge.is_running is False

    def test_not_connected_initially(self, bridge):
        assert bridge.is_connected is False

    def test_stats_initialized(self, bridge):
        assert bridge.stats['messages_mesh_to_rns'] == 0
        assert bridge.stats['messages_rns_to_mesh'] == 0
        assert bridge.stats['errors'] == 0
        assert bridge.stats['bounced'] == 0
        assert bridge.stats['start_time'] is None

    def test_queues_created(self, bridge):
        assert isinstance(bridge._mesh_to_rns_queue, Queue)
        assert isinstance(bridge._rns_to_mesh_queue, Queue)

    def test_callbacks_empty(self, bridge):
        assert bridge._message_callbacks == []
        assert bridge._status_callbacks == []

    def test_rns_state_flags(self, bridge):
        assert bridge._connected_rns is False
        assert bridge._rns_via_rnsd is False
        assert bridge._rns_init_failed_permanently is False
        assert bridge._rns_pre_initialized is False

    def test_mqtt_filter_off_by_default(self, bridge):
        assert bridge._filter_mqtt_messages is False


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------

class TestBridgeProperties:
    """Tests for bridge property methods."""

    def test_is_running_reflects_state(self, bridge):
        bridge._running = True
        assert bridge.is_running is True
        bridge._running = False
        assert bridge.is_running is False

    def test_is_connected_when_mesh_connected(self, bridge):
        bridge._mesh_handler.is_connected = True
        assert bridge.is_connected is True

    def test_is_connected_when_rns_connected(self, bridge):
        bridge._connected_rns = True
        assert bridge.is_connected is True

    def test_is_connected_neither(self, bridge):
        bridge._mesh_handler.is_connected = False
        bridge._connected_rns = False
        assert bridge.is_connected is False

    def test_bridge_status_delegates_to_health(self, bridge):
        bridge.health.get_bridge_status.return_value = BridgeStatus.HEALTHY
        assert bridge.bridge_status == BridgeStatus.HEALTHY

    def test_is_fully_healthy_delegates(self, bridge):
        bridge.health.is_bridge_fully_healthy.return_value = True
        assert bridge.is_fully_healthy is True


# ---------------------------------------------------------------------------
# Circuit breaker delegation
# ---------------------------------------------------------------------------

class TestCircuitBreaker:
    """Tests for circuit breaker methods."""

    def test_can_send_to_delegates(self, bridge):
        bridge._circuit_breaker.can_send.return_value = True
        assert bridge.can_send_to("!abc123") is True
        bridge._circuit_breaker.can_send.assert_called_once_with("!abc123")

    def test_can_send_to_blocked(self, bridge):
        bridge._circuit_breaker.can_send.return_value = False
        assert bridge.can_send_to("!abc123") is False

    def test_can_send_to_no_circuit_breaker(self, bridge_no_cb):
        assert bridge_no_cb.can_send_to("!abc123") is True

    def test_record_send_success(self, bridge):
        bridge.record_send_success("!abc123")
        bridge._circuit_breaker.record_success.assert_called_once_with("!abc123")

    def test_record_send_success_no_cb(self, bridge_no_cb):
        bridge_no_cb.record_send_success("!abc123")  # Should not raise

    def test_record_send_failure(self, bridge):
        bridge.record_send_failure("!abc123", "timeout")
        bridge._circuit_breaker.record_failure.assert_called_once_with("!abc123", "timeout")

    def test_record_send_failure_no_cb(self, bridge_no_cb):
        bridge_no_cb.record_send_failure("!abc123", "err")  # Should not raise

    def test_get_open_circuits(self, bridge):
        bridge._circuit_breaker.get_open_circuits.return_value = {"!abc": {}}
        result = bridge.get_open_circuits()
        assert "!abc" in result

    def test_get_open_circuits_no_cb(self, bridge_no_cb):
        assert bridge_no_cb.get_open_circuits() == {}


# ---------------------------------------------------------------------------
# MQTT filtering
# ---------------------------------------------------------------------------

class TestMQTTFiltering:
    """Tests for MQTT message filtering."""

    def test_set_filter_mqtt_enable(self, bridge):
        bridge.set_filter_mqtt(True)
        assert bridge._filter_mqtt_messages is True

    def test_set_filter_mqtt_disable(self, bridge):
        bridge.set_filter_mqtt(True)
        bridge.set_filter_mqtt(False)
        assert bridge._filter_mqtt_messages is False


# ---------------------------------------------------------------------------
# get_status
# ---------------------------------------------------------------------------

class TestGetStatus:
    """Tests for get_status method."""

    def test_status_when_not_running(self, bridge):
        status = bridge.get_status()
        assert status['running'] is False
        assert status['meshtastic_connected'] is False
        assert status['rns_connected'] is False
        assert status['uptime_seconds'] is None

    def test_status_when_running(self, bridge):
        bridge._running = True
        bridge.stats['start_time'] = datetime.now()
        bridge._connected_rns = True
        bridge._mesh_handler.is_connected = True

        status = bridge.get_status()
        assert status['running'] is True
        assert status['meshtastic_connected'] is True
        assert status['rns_connected'] is True
        assert status['uptime_seconds'] is not None
        assert status['uptime_seconds'] >= 0

    def test_status_contains_statistics(self, bridge):
        bridge.stats['messages_mesh_to_rns'] = 5
        status = bridge.get_status()
        assert status['statistics']['messages_mesh_to_rns'] == 5

    def test_status_contains_node_stats(self, bridge):
        bridge.node_tracker.get_stats.return_value = {"total": 10}
        status = bridge.get_status()
        assert status['node_stats'] == {"total": 10}

    def test_status_enabled_from_config(self, bridge):
        status = bridge.get_status()
        assert status['enabled'] is True

    def test_status_rns_via_rnsd(self, bridge):
        bridge._rns_via_rnsd = True
        status = bridge.get_status()
        assert status['rns_via_rnsd'] is True

    def test_status_with_uptime_calculation(self, bridge):
        bridge.stats['start_time'] = datetime.now() - timedelta(seconds=60)
        status = bridge.get_status()
        assert status['uptime_seconds'] >= 60


# ---------------------------------------------------------------------------
# Callbacks
# ---------------------------------------------------------------------------

class TestCallbacks:
    """Tests for callback registration and notification."""

    def test_register_message_callback(self, bridge):
        cb = MagicMock()
        bridge.register_message_callback(cb)
        assert cb in bridge._message_callbacks

    def test_register_status_callback(self, bridge):
        cb = MagicMock()
        bridge.register_status_callback(cb)
        assert cb in bridge._status_callbacks

    def test_notify_message_calls_callbacks(self, bridge):
        cb1 = MagicMock()
        cb2 = MagicMock()
        bridge.register_message_callback(cb1)
        bridge.register_message_callback(cb2)

        from gateway.rns_bridge import BridgedMessage
        msg = BridgedMessage(
            source_network="rns",
            source_id="abc123",
            destination_id=None,
            content="test",
        )
        bridge._notify_message(msg)
        cb1.assert_called_once_with(msg)
        cb2.assert_called_once_with(msg)

    def test_notify_message_handles_callback_error(self, bridge):
        bad_cb = MagicMock(side_effect=RuntimeError("cb fail"))
        good_cb = MagicMock()
        bridge.register_message_callback(bad_cb)
        bridge.register_message_callback(good_cb)

        from gateway.rns_bridge import BridgedMessage
        msg = BridgedMessage(
            source_network="rns", source_id="abc", destination_id=None, content="x"
        )
        bridge._notify_message(msg)
        # Good callback should still be called despite bad one failing
        good_cb.assert_called_once_with(msg)

    def test_notify_status_calls_callbacks(self, bridge):
        cb = MagicMock()
        bridge.register_status_callback(cb)
        bridge._notify_status("started")
        assert cb.call_count == 1
        assert cb.call_args[0][0] == "started"

    def test_notify_status_handles_callback_error(self, bridge):
        bad_cb = MagicMock(side_effect=RuntimeError("status cb fail"))
        good_cb = MagicMock()
        bridge.register_status_callback(bad_cb)
        bridge.register_status_callback(good_cb)
        bridge._notify_status("stopped")
        good_cb.assert_called_once()


# ---------------------------------------------------------------------------
# send_to_meshtastic
# ---------------------------------------------------------------------------

@pytest.mark.usefixtures("allow_local_radio_tx")
class TestSendToMeshtastic:
    """Tests for send_to_meshtastic."""

    def test_delegates_to_handler(self, bridge):
        bridge._mesh_handler.send_text.return_value = True
        result = bridge.send_to_meshtastic("Hello", "!dest", 2)
        assert result is True
        bridge._mesh_handler.send_text.assert_called_once_with("Hello", "!dest", 2)

    def test_returns_false_no_handler(self, bridge):
        from gateway.config import MeshtasticEgressConfig
        bridge._mesh_handler = None
        # A REAL disabled config, not the MagicMock default. The mock answered
        # every attribute truthily, so `eg.enabled and eg.host` passed and this
        # test entered the remote-egress branch it claims is disabled — then
        # returned False only because the HTTP send to a nonsense host failed.
        # It asserted the right thing for the wrong reason; the RF egress guard
        # surfaced it by refusing the send (2026-08-09).
        bridge.config.meshtastic_egress = MeshtasticEgressConfig()
        assert bridge.send_to_meshtastic("Hello") is False

    def test_remote_egress_when_no_handler(self, bridge):
        """No local radio + meshtastic_egress enabled -> send_text_direct."""
        from gateway.config import MeshtasticEgressConfig
        bridge._mesh_handler = None
        bridge.config.meshtastic_egress = MeshtasticEgressConfig(
            enabled=True, host="10.0.0.5", port=9443, tls=True, channel_index=2
        )
        with patch("gateway.meshtastic_protobuf_client.send_text_direct",
                   return_value=True) as mock_send:
            result = bridge.send_to_meshtastic("Hello", channel=0)
        assert result is True
        mock_send.assert_called_once()
        kwargs = mock_send.call_args.kwargs
        assert kwargs["host"] == "10.0.0.5"
        # egress uses its OWN channel_index (peer gateway's), not the passed channel
        assert kwargs["channel_index"] == 2
        # want_ack defaults True so the lossy LongFast egress hop engages
        # meshtasticd's implicit-ACK rebroadcast (reliable command delivery).
        assert kwargs["want_ack"] is True

    def test_remote_egress_want_ack_override_honored(self, bridge):
        """want_ack=False in the egress config is passed through verbatim
        (airtime-conscious opt-out of the rebroadcast reliability)."""
        from gateway.config import MeshtasticEgressConfig
        bridge._mesh_handler = None
        bridge.config.meshtastic_egress = MeshtasticEgressConfig(
            enabled=True, host="10.0.0.5", port=9443, tls=True,
            channel_index=2, want_ack=False,
        )
        with patch("gateway.meshtastic_protobuf_client.send_text_direct",
                   return_value=True) as mock_send:
            bridge.send_to_meshtastic("Hello", channel=0)
        assert mock_send.call_args.kwargs["want_ack"] is False

    def test_remote_egress_skipped_when_disabled(self, bridge):
        from gateway.config import MeshtasticEgressConfig
        bridge._mesh_handler = None
        bridge.config.meshtastic_egress = MeshtasticEgressConfig(
            enabled=False, host="10.0.0.5", channel_index=2
        )
        with patch("gateway.meshtastic_protobuf_client.send_text_direct") as mock_send:
            assert bridge.send_to_meshtastic("Hello") is False
        mock_send.assert_not_called()


# ---------------------------------------------------------------------------
# send_to_rns
# ---------------------------------------------------------------------------

class TestSendToRNS:
    """Tests for send_to_rns."""

    def test_returns_false_not_connected(self, bridge):
        bridge._connected_rns = False
        assert bridge.send_to_rns("msg") is False

    def test_returns_false_no_lxmf_source(self, bridge):
        bridge._connected_rns = True
        bridge._lxmf_source = None
        assert bridge.send_to_rns("msg") is False

    def test_broadcast_returns_false(self, bridge):
        bridge._connected_rns = True
        bridge._lxmf_source = MagicMock()
        # No destination hash -> broadcast
        assert bridge.send_to_rns("broadcast msg", None) is False


# ---------------------------------------------------------------------------
# Routing rules — legacy
# ---------------------------------------------------------------------------

class TestRoutingLegacy:
    """Tests for _should_bridge_legacy routing logic."""

    def _make_bridge_with_rules(self, rules, default_route="bidirectional", enabled=True):
        """Create bridge with specific routing rules."""
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

            mock_config = _mock_gateway_config(
                routing_rules=rules,
                default_route=default_route,
                enabled=enabled,
            )
            MockConfig.load.return_value = mock_config
            MockHandler.return_value = MagicMock(is_connected=False)
            MockReconnect.for_rns.return_value = MagicMock()

            from gateway.rns_bridge import RNSMeshtasticBridge
            return RNSMeshtasticBridge(config=mock_config)

    def _make_rule(self, **kwargs):
        from gateway.config import RoutingRule
        return RoutingRule(**kwargs)

    def _make_msg(self, source_network="meshtastic", source_id="!aabb0042",
                  content="hello", destination_id=None, is_broadcast=False):
        from gateway.rns_bridge import BridgedMessage
        return BridgedMessage(
            source_network=source_network,
            source_id=source_id,
            destination_id=destination_id,
            content=content,
            is_broadcast=is_broadcast,
        )

    def test_disabled_config_blocks_all(self):
        b = self._make_bridge_with_rules([], enabled=False)
        msg = self._make_msg()
        assert b._router.should_bridge(msg) is False

    def test_no_rules_default_bidirectional(self):
        b = self._make_bridge_with_rules([], default_route="bidirectional")
        msg = self._make_msg()
        assert b._router._should_bridge_legacy(msg) is True

    def test_no_rules_default_blocks(self):
        b = self._make_bridge_with_rules([], default_route="none")
        msg = self._make_msg()
        assert b._router._should_bridge_legacy(msg) is False

    def test_matching_rule_passes(self):
        rule = self._make_rule(name="all", direction="bidirectional")
        b = self._make_bridge_with_rules([rule])
        msg = self._make_msg()
        assert b._router._should_bridge_legacy(msg) is True

    def test_direction_filter_mesh_to_rns_blocks_rns_source(self):
        rule = self._make_rule(name="m2r", direction="mesh_to_rns")
        b = self._make_bridge_with_rules([rule], default_route="none")
        msg_rns = self._make_msg(source_network="rns")
        assert b._router._should_bridge_legacy(msg_rns) is False

    def test_direction_filter_rns_to_mesh_blocks_mesh_source(self):
        rule = self._make_rule(name="r2m", direction="rns_to_mesh")
        b = self._make_bridge_with_rules([rule], default_route="none")
        msg_mesh = self._make_msg(source_network="meshtastic")
        assert b._router._should_bridge_legacy(msg_mesh) is False

    def test_direction_filter_allows_correct_direction(self):
        rule = self._make_rule(name="m2r", direction="mesh_to_rns")
        b = self._make_bridge_with_rules([rule], default_route="none")
        msg = self._make_msg(source_network="meshtastic")
        assert b._router._should_bridge_legacy(msg) is True

    def test_source_filter_regex(self):
        rule = self._make_rule(name="src", direction="bidirectional", source_filter="!aabb.*")
        b = self._make_bridge_with_rules([rule], default_route="none")
        msg_match = self._make_msg(source_id="!aabb0042")
        msg_no_match = self._make_msg(source_id="!ccdd0099")
        assert b._router._should_bridge_legacy(msg_match) is True
        assert b._router._should_bridge_legacy(msg_no_match) is False

    def test_dest_filter_regex(self):
        rule = self._make_rule(name="dst", direction="bidirectional", dest_filter="!dest.*")
        b = self._make_bridge_with_rules([rule], default_route="none")
        msg_match = self._make_msg(destination_id="!dest1234")
        msg_no_match = self._make_msg(destination_id="!other")
        assert b._router._should_bridge_legacy(msg_match) is True
        assert b._router._should_bridge_legacy(msg_no_match) is False

    def test_message_filter_regex(self):
        rule = self._make_rule(name="msg", direction="bidirectional", message_filter="URGENT.*")
        b = self._make_bridge_with_rules([rule], default_route="none")
        msg_match = self._make_msg(content="URGENT: help needed")
        msg_no_match = self._make_msg(content="casual chat")
        assert b._router._should_bridge_legacy(msg_match) is True
        assert b._router._should_bridge_legacy(msg_no_match) is False

    def test_disabled_rule_skipped(self):
        rule = self._make_rule(name="off", direction="bidirectional", enabled=False)
        b = self._make_bridge_with_rules([rule], default_route="none")
        msg = self._make_msg()
        assert b._router._should_bridge_legacy(msg) is False

    def test_invalid_regex_skipped(self):
        rule = self._make_rule(name="bad", direction="bidirectional", source_filter="[invalid")
        b = self._make_bridge_with_rules([rule], default_route="none")
        msg = self._make_msg()
        assert b._router._should_bridge_legacy(msg) is False

    def test_multiple_rules_first_match_wins(self):
        rule1 = self._make_rule(name="r1", direction="bidirectional", source_filter="!aabb.*")
        rule2 = self._make_rule(name="r2", direction="bidirectional")  # matches all
        b = self._make_bridge_with_rules([rule1, rule2], default_route="none")
        msg = self._make_msg(source_id="!ccdd0099")
        # rule1 doesn't match source, but rule2 matches all
        assert b._router._should_bridge_legacy(msg) is True

    def test_recompiles_when_rules_change(self):
        rule = self._make_rule(name="r1", direction="bidirectional")
        b = self._make_bridge_with_rules([rule], default_route="none")
        # First call compiles
        msg = self._make_msg()
        assert b._router._should_bridge_legacy(msg) is True
        # Add new rule and verify recompilation
        new_rule = self._make_rule(name="r2", direction="bidirectional", source_filter="!xyz.*")
        b.config.routing_rules.append(new_rule)
        msg2 = self._make_msg(source_id="!xyz9999")
        assert b._router._should_bridge_legacy(msg2) is True


# ---------------------------------------------------------------------------
# _compile_routing_rules
# ---------------------------------------------------------------------------

class TestCompileRoutingRules:
    """Tests for _compile_routing_rules."""

    def test_compiles_valid_patterns(self, bridge):
        from gateway.config import RoutingRule
        rule = RoutingRule(name="test", source_filter="!aabb.*", dest_filter="", message_filter="hello")
        bridge.config.routing_rules = [rule]
        compiled = bridge._router._compile_routing_rules()
        assert "test" in compiled
        assert 'source_filter' in compiled['test']
        assert compiled['test']['source_filter'] is not None

    def test_marks_invalid_patterns_none(self, bridge):
        from gateway.config import RoutingRule
        rule = RoutingRule(name="bad", source_filter="[invalid")
        bridge.config.routing_rules = [rule]
        compiled = bridge._router._compile_routing_rules()
        assert compiled['bad']['source_filter'] is None

    def test_empty_patterns_not_compiled(self, bridge):
        from gateway.config import RoutingRule
        rule = RoutingRule(name="empty", source_filter="", dest_filter="", message_filter="")
        bridge.config.routing_rules = [rule]
        compiled = bridge._router._compile_routing_rules()
        assert compiled['empty'] == {}


# ---------------------------------------------------------------------------
# _process_mesh_to_rns
# ---------------------------------------------------------------------------

class TestProcessMeshToRNS:
    """Tests for Mesh->RNS message processing."""

    def test_success_updates_stats(self, bridge):
        from gateway.rns_bridge import BridgedMessage
        bridge.config.rns.get_lxmf_destinations.return_value = [
            "6b1a0120941444587d7d1dc1bf6d64d7",
        ]
        msg = BridgedMessage(
            source_network="meshtastic", source_id="!aabb0042",
            destination_id=None, content="test msg",
        )

        with patch.object(bridge, 'send_to_rns', return_value=True):
            bridge._process_mesh_to_rns(msg)

        assert bridge.stats['messages_mesh_to_rns'] == 1
        bridge.health.record_message_sent.assert_called_once_with("mesh_to_rns")

    def test_failure_broadcast_no_error(self, bridge):
        from gateway.rns_bridge import BridgedMessage
        msg = BridgedMessage(
            source_network="meshtastic", source_id="!aabb0042",
            destination_id=None, content="broadcast",
            is_broadcast=True,
        )

        with patch.object(bridge, 'send_to_rns', return_value=False):
            bridge._process_mesh_to_rns(msg)

        assert bridge.stats['errors'] == 0

    def test_failure_unicast_increments_errors(self, bridge):
        from gateway.rns_bridge import BridgedMessage
        msg = BridgedMessage(
            source_network="meshtastic", source_id="!aabb0042",
            destination_id="!dest", content="unicast",
        )

        with patch.object(bridge, 'send_to_rns', return_value=False), \
             patch.object(bridge, '_requeue_failed_message', return_value=False):
            bridge._process_mesh_to_rns(msg)

        assert bridge.stats['errors'] == 1

    def test_exception_requeues_and_tracks(self, bridge):
        from gateway.rns_bridge import BridgedMessage
        msg = BridgedMessage(
            source_network="meshtastic", source_id="!aabb0042",
            destination_id=None, content="fail",
        )

        with patch.object(bridge, 'send_to_rns', side_effect=RuntimeError("boom")), \
             patch.object(bridge, '_requeue_failed_message', return_value=True):
            bridge._process_mesh_to_rns(msg)

        assert bridge.stats['errors'] == 1
        bridge.health.record_message_failed.assert_called_once_with("mesh_to_rns", requeued=True)

    def test_prefix_includes_source_id(self, bridge):
        from gateway.rns_bridge import BridgedMessage
        bridge.config.rns.get_lxmf_destinations.return_value = [
            "6b1a0120941444587d7d1dc1bf6d64d7",
        ]
        msg = BridgedMessage(
            source_network="meshtastic", source_id="!aabb0042",
            destination_id=None, content="hello",
        )
        sent_content = None

        def capture_send(content, dest_hash=None):
            nonlocal sent_content
            sent_content = content
            return True

        with patch.object(bridge, 'send_to_rns', side_effect=capture_send):
            bridge._process_mesh_to_rns(msg)

        assert sent_content.startswith("[Mesh:0042] ")

    def test_broadcast_fans_out_to_each_default_destination(self, bridge):
        """default_lxmf_destination as a LIST → broadcast sends to EVERY peer.

        Regression for the meshanchor-server case: the leg ran but every broadcast
        dropped because the str-only path ignored the configured list. Ported from
        MeshForge (lead repo)."""
        from gateway.rns_bridge import BridgedMessage
        bridge.config.rns.get_lxmf_destinations.return_value = [
            "3dfbdb5d24c6de195ae4f3c0f56b5ea5",
            "f68c2f56cb61527b6c9ad603b9a5009a",
        ]
        msg = BridgedMessage(
            source_network="meshtastic", source_id="!aabb0042",
            destination_id=None, content="hello mesh", is_broadcast=True,
        )
        sent_hashes = []

        def capture_send(content, dest_hash=None):
            sent_hashes.append(dest_hash)
            return True

        with patch.object(bridge, 'send_to_rns', side_effect=capture_send):
            bridge._process_mesh_to_rns(msg)

        # one send per configured destination, each the decoded hex
        assert len(sent_hashes) == 2
        assert bytes.fromhex("3dfbdb5d24c6de195ae4f3c0f56b5ea5") in sent_hashes
        assert bytes.fromhex("f68c2f56cb61527b6c9ad603b9a5009a") in sent_hashes
        assert bridge.stats['messages_mesh_to_rns'] == 1

    def test_meshcore_broadcast_fans_out_to_rns(self, bridge):
        """MeshCore->RNS broadcast fans out to each default_lxmf_destination too.

        Regression for the live-verify gap: the MeshCore path (meshcore_bridge_mixin)
        called send_to_rns with no destination, so MC broadcasts dropped even with the
        list configured — and MeshCore is meshanchor-server's PRIMARY network, so this
        is the path that actually fires. Ported from MeshForge."""
        from gateway.rns_bridge import BridgedMessage
        bridge.config.rns.get_lxmf_destinations.return_value = [
            "3dfbdb5d24c6de195ae4f3c0f56b5ea5",
            "f68c2f56cb61527b6c9ad603b9a5009a",
        ]
        msg = BridgedMessage(
            source_network="meshcore", source_id="!mc01",
            destination_id=None, content="mc broadcast", is_broadcast=True,
        )
        sent_to = []

        def capture(content, dest_hash=None):
            sent_to.append(dest_hash)
            return True

        with patch.object(bridge, 'send_to_rns', side_effect=capture), \
             patch.object(bridge, 'send_to_meshtastic', return_value=True):
            bridge._process_meshcore_to_bridge(msg)

        assert len(sent_to) == 2
        assert bytes.fromhex("3dfbdb5d24c6de195ae4f3c0f56b5ea5") in sent_to
        assert bytes.fromhex("f68c2f56cb61527b6c9ad603b9a5009a") in sent_to


# ---------------------------------------------------------------------------
# _process_rns_to_mesh
# ---------------------------------------------------------------------------

class TestProcessRNSToMesh:
    """Tests for RNS->Mesh message processing."""

    def test_success_updates_stats(self, bridge):
        from gateway.rns_bridge import BridgedMessage
        msg = BridgedMessage(
            source_network="rns", source_id="abcdef01",
            destination_id=None, content="from rns",
        )

        with patch.object(bridge, 'send_to_meshtastic', return_value=True):
            bridge._process_rns_to_mesh(msg)

        assert bridge.stats['messages_rns_to_mesh'] == 1
        bridge.health.record_message_sent.assert_called_once_with("rns_to_mesh")

    def test_failure_increments_errors(self, bridge):
        from gateway.rns_bridge import BridgedMessage
        msg = BridgedMessage(
            source_network="rns", source_id="abcdef01",
            destination_id=None, content="fail msg",
        )

        with patch.object(bridge, 'send_to_meshtastic', return_value=False), \
             patch.object(bridge, '_requeue_failed_message', return_value=False):
            bridge._process_rns_to_mesh(msg)

        assert bridge.stats['errors'] == 1

    def test_exception_requeues(self, bridge):
        from gateway.rns_bridge import BridgedMessage
        msg = BridgedMessage(
            source_network="rns", source_id="abcdef01",
            destination_id=None, content="boom",
        )

        with patch.object(bridge, 'send_to_meshtastic', side_effect=RuntimeError("err")), \
             patch.object(bridge, '_requeue_failed_message', return_value=True):
            bridge._process_rns_to_mesh(msg)

        assert bridge.stats['errors'] == 1
        bridge.health.record_message_failed.assert_called_once_with("rns_to_mesh", requeued=True)

    def test_prefix_includes_rns_source(self, bridge):
        from gateway.rns_bridge import BridgedMessage
        msg = BridgedMessage(
            source_network="rns", source_id="abcdef01",
            destination_id=None, content="hello",
        )
        sent_content = None

        def capture_send(content, destination=None, channel=0):
            nonlocal sent_content
            sent_content = content
            return True

        with patch.object(bridge, 'send_to_meshtastic', side_effect=capture_send):
            bridge._process_rns_to_mesh(msg)

        assert sent_content.startswith("[RNS:abcd] ")

    def test_multiline_content_chunked_not_truncated(self, bridge):
        """Ported chunking (MeshForge 0066470): multi-line RNS content over the
        Meshtastic byte cap is split into multiple ≤228 B packets, not cut to
        one. Regression for silently-dropped lines past the cap."""
        from gateway.rns_bridge import BridgedMessage
        body = "\n".join(f"{i:2d}. node-{i} score {i*7}" for i in range(40))
        assert len(body.encode("utf-8")) > 228  # would have truncated
        msg = BridgedMessage(source_network="rns", source_id="abcdef01",
                             destination_id=None, content=body)
        sent = []

        def capture(content, destination=None, channel=0):
            sent.append(content)
            return True

        with patch.object(bridge, 'send_to_meshtastic', side_effect=capture):
            bridge._process_rns_to_mesh(msg)

        assert len(sent) > 1                                  # actually chunked
        assert all(len(c.encode("utf-8")) <= 228 for c in sent)  # each within cap
        assert sent[0].startswith("[RNS:abcd] ")              # prefix on chunk 0
        # No content lost: every source line survives across the chunks.
        joined = "\n".join(sent)
        for i in range(40):
            assert f"node-{i} score {i*7}" in joined
        assert bridge.stats['messages_rns_to_mesh'] == 1      # one logical message

    def test_partial_chunk_failure_requeues_failed_chunks(self, bridge):
        """On partial direct-send failure the FAILED chunks are re-queued (each
        byte-bounded), not the whole un-chunked original."""
        from gateway.rns_bridge import BridgedMessage
        body = "\n".join(f"line {i} " + "x" * 50 for i in range(20))
        msg = BridgedMessage(source_network="rns", source_id="abcdef01",
                             destination_id=None, content=body)
        calls = {"n": 0}

        def send(content, destination=None, channel=0):
            calls["n"] += 1
            return calls["n"] == 1  # first chunk sends, rest fail

        captured = {}

        def fake_requeue_chunks(chunks, target="meshtastic"):
            captured["chunks"] = list(chunks)
            captured["target"] = target
            return True

        with patch.object(bridge, 'send_to_meshtastic', side_effect=send), \
             patch.object(bridge, '_requeue_failed_chunks', side_effect=fake_requeue_chunks):
            bridge._process_rns_to_mesh(msg)

        assert bridge.stats['errors'] == 1
        assert captured["target"] == "meshtastic"
        assert len(captured["chunks"]) >= 1                   # only the failed ones
        assert all(len(c.encode("utf-8")) <= 228 for c in captured["chunks"])
        bridge.health.record_message_failed.assert_called_with("rns_to_mesh", requeued=True)


class TestChunkForMesh:
    """Unit tests for the ported chunk_for_mesh primitive."""

    def test_short_message_single_chunk(self):
        from gateway.base_handler import chunk_for_mesh
        assert chunk_for_mesh("hello") == ["hello"]

    def test_empty_is_empty_list(self):
        from gateway.base_handler import chunk_for_mesh
        assert chunk_for_mesh("") == []

    def test_multiline_splits_within_cap_lossless(self):
        from gateway.base_handler import chunk_for_mesh
        text = "\n".join(f"row {i}: " + "y" * 30 for i in range(30))
        chunks = chunk_for_mesh(text)
        assert len(chunks) > 1
        assert all(len(c.encode("utf-8")) <= 228 for c in chunks)
        assert "\n".join(chunks) == text  # newline-joined chunks reconstruct input

    def test_single_oversize_word_char_split(self):
        from gateway.base_handler import chunk_for_mesh
        chunks = chunk_for_mesh("z" * 500, max_bytes=50)
        assert len(chunks) >= 10
        assert all(len(c.encode("utf-8")) <= 50 for c in chunks)
        assert "".join(chunks) == "z" * 500


# ---------------------------------------------------------------------------
# _requeue_failed_message
# ---------------------------------------------------------------------------

class TestRequeueFailedMessage:
    """Tests for _requeue_failed_message."""

    def test_no_persistent_queue_returns_false(self, bridge):
        bridge._persistent_queue = None
        from gateway.rns_bridge import BridgedMessage
        msg = BridgedMessage(
            source_network="meshtastic", source_id="!abc",
            destination_id=None, content="test",
        )
        assert bridge._requeue_failed_message(msg, "rns") is False

    def test_with_persistent_queue_enqueues(self, bridge):
        mock_queue = MagicMock()
        bridge._persistent_queue = mock_queue

        from gateway.rns_bridge import BridgedMessage
        msg = BridgedMessage(
            source_network="meshtastic", source_id="!abc",
            destination_id="!dest", content="retry me",
            metadata={"channel": 1},
        )
        result = bridge._requeue_failed_message(msg, "rns")
        assert result is True
        mock_queue.enqueue.assert_called_once()

    def test_enqueue_exception_returns_false(self, bridge):
        mock_queue = MagicMock()
        mock_queue.enqueue.side_effect = RuntimeError("db error")
        bridge._persistent_queue = mock_queue

        from gateway.rns_bridge import BridgedMessage
        msg = BridgedMessage(
            source_network="meshtastic", source_id="!abc",
            destination_id=None, content="fail",
        )
        assert bridge._requeue_failed_message(msg, "rns") is False

    def test_channel_lifted_into_payload_top_level(self, bridge):
        """Channel-0 Public leak follow-up (2026-05-20). The MeshCore
        persistent-queue sender reads ``payload.get('channel')`` from the
        OUTER dict; if we leave channel only in metadata, the replay path
        falls through ``_resolve_channel(None)`` → slot 0 (Public). Lift
        it into the payload at enqueue time so the slot survives a
        disconnect/reconnect cycle."""
        mock_queue = MagicMock()
        mock_queue.enqueue.return_value = "msg-123"
        bridge._persistent_queue = mock_queue

        from gateway.rns_bridge import BridgedMessage
        msg = BridgedMessage(
            source_network="meshtastic", source_id="!abc",
            destination_id=None, content="private chat",
            metadata={"channel": 1, "other": "preserved"},
        )
        ok = bridge._requeue_failed_message(msg, "meshcore")
        assert ok is True
        call_kwargs = mock_queue.enqueue.call_args.kwargs
        payload = call_kwargs["payload"]
        # Top-level channel set from metadata.channel — the replay path
        # consults this BEFORE _resolve_channel can default to 0.
        assert payload["channel"] == 1
        # Metadata still intact (other consumers may need it).
        assert payload["metadata"]["channel"] == 1
        assert payload["metadata"]["other"] == "preserved"

    def test_channel_lift_skipped_when_metadata_missing(self, bridge):
        """No channel info → no top-level 'channel' key. Don't fabricate
        one — the downstream _resolve_channel will still default to 0
        and log at DEBUG, surfacing the missing-info caller."""
        mock_queue = MagicMock()
        mock_queue.enqueue.return_value = "msg-x"
        bridge._persistent_queue = mock_queue

        from gateway.rns_bridge import BridgedMessage
        msg = BridgedMessage(
            source_network="meshtastic", source_id="!abc",
            destination_id=None, content="no-channel",
        )
        bridge._requeue_failed_message(msg, "meshcore")
        payload = mock_queue.enqueue.call_args.kwargs["payload"]
        assert "channel" not in payload

    def test_channel_lift_handles_unparsable_metadata(self, bridge):
        """metadata['channel'] = 'oops' (non-int) → don't crash; skip the
        lift. _resolve_channel handles the unparsable case downstream."""
        mock_queue = MagicMock()
        mock_queue.enqueue.return_value = "msg-y"
        bridge._persistent_queue = mock_queue

        from gateway.rns_bridge import BridgedMessage
        msg = BridgedMessage(
            source_network="meshtastic", source_id="!abc",
            destination_id=None, content="bad-meta",
            metadata={"channel": "not-an-int"},
        )
        ok = bridge._requeue_failed_message(msg, "meshcore")
        assert ok is True
        payload = mock_queue.enqueue.call_args.kwargs["payload"]
        assert "channel" not in payload

    def test_channel_override_overrides_metadata_lift(self, bridge):
        """channel_override kwarg (used by the cross-protocol bridge_loop
        during disconnect-window requeue) wins over metadata.channel.
        Without this, the replay path leaks the SOURCE protocol's channel
        index into the destination slot — Issue #37 leak class via the
        requeue path (live-discovered 2026-05-20)."""
        mock_queue = MagicMock()
        mock_queue.enqueue.return_value = "msg-z"
        bridge._persistent_queue = mock_queue

        from gateway.rns_bridge import BridgedMessage
        msg = BridgedMessage(
            source_network="meshtastic", source_id="!abc",
            destination_id=None, content="hello",
            metadata={"channel": 0},  # source channel — must be overridden
        )
        ok = bridge._requeue_failed_message(
            msg, "meshcore", channel_override=1
        )
        assert ok is True
        payload = mock_queue.enqueue.call_args.kwargs["payload"]
        # Override wins.
        assert payload["channel"] == 1

    def test_channel_override_handles_unparsable(self, bridge):
        """If channel_override is non-int, don't crash; just skip
        writing the top-level channel. Belt-and-suspenders for callers
        that pass a value from a stringly-typed config field."""
        mock_queue = MagicMock()
        mock_queue.enqueue.return_value = "msg-w"
        bridge._persistent_queue = mock_queue

        from gateway.rns_bridge import BridgedMessage
        msg = BridgedMessage(
            source_network="meshtastic", source_id="!abc",
            destination_id=None, content="x",
            metadata={},
        )
        ok = bridge._requeue_failed_message(
            msg, "meshcore", channel_override="garbage"  # type: ignore[arg-type]
        )
        assert ok is True
        payload = mock_queue.enqueue.call_args.kwargs["payload"]
        assert "channel" not in payload


# ---------------------------------------------------------------------------
# Cross-protocol bridge → MeshCore channel resolution (channel-0 Public leak
# follow-up, 2026-05-20). The bridge mixin lives on RNSMeshtasticBridge via
# MRO; testing through the bridge fixture covers the real call shape.
# ---------------------------------------------------------------------------

class TestBridgeToMeshcoreChannelLeak:
    """Regression coverage for the channel-leak shape that survived the
    Issue #35 DM-drop fix.

    Final resolution (post-verify discovery 2026-05-20): the resolver
    is CONFIG-ONLY. msg.metadata['channel'] is the SOURCE protocol's
    channel index (Meshtastic packet.channel always populates it, default
    0); it has no semantic mapping to a MeshCore slot. Honoring metadata
    preserved the leak — Meshtastic ch0 broadcasts kept landing on
    MeshCore slot 0 (Public). Matches the symmetric MC→Mesh direction
    in `_process_meshcore_to_bridge` which already uses
    `config.meshtastic.channel` exclusively."""

    def test_resolve_uses_config_target_channel(self, bridge):
        """config.bridge_target_channel=1 → returns 1; metadata ignored."""
        bridge.config.meshcore.bridge_target_channel = 1
        from gateway.rns_bridge import BridgedMessage
        msg = BridgedMessage(
            source_network="meshtastic", source_id="!abc",
            destination_id=None, content="x",
            metadata={"channel": 2},  # source channel ignored
        )
        assert bridge._resolve_bridge_target_channel(msg) == 1

    def test_resolve_returns_negative_when_config_unset(self, bridge):
        """Pre-fix path implicitly returned 0 here → leak. Now -1 → drop.
        Metadata is irrelevant — config alone decides the slot."""
        bridge.config.meshcore.bridge_target_channel = -1
        from gateway.rns_bridge import BridgedMessage
        msg = BridgedMessage(
            source_network="meshtastic", source_id="!abc",
            destination_id=None, content="x",
            metadata={"channel": 5},
        )
        assert bridge._resolve_bridge_target_channel(msg) == -1

    def test_resolve_meshtastic_channel_zero_does_not_leak(self, bridge):
        """The exact pre-fix leak shape: Meshtastic packet on channel 0
        bridges to MeshCore. The old resolver returned 0 (Public). The
        config-only resolver returns 1 (the configured private slot)."""
        bridge.config.meshcore.bridge_target_channel = 1
        from gateway.rns_bridge import BridgedMessage
        msg = BridgedMessage(
            source_network="meshtastic", source_id="!abc",
            destination_id=None, content="leaky on ch0",
            metadata={"channel": 0, "snr": -120.0},
        )
        assert bridge._resolve_bridge_target_channel(msg) == 1

    def test_resolve_unparsable_config_falls_to_drop(self, bridge):
        """If config target_channel can't be int-coerced, treat as unset."""
        bridge.config.meshcore.bridge_target_channel = "garbage"
        from gateway.rns_bridge import BridgedMessage
        msg = BridgedMessage(
            source_network="meshtastic", source_id="!abc",
            destination_id=None, content="x",
            metadata={},
        )
        assert bridge._resolve_bridge_target_channel(msg) == -1

    def test_resolve_config_zero_is_explicit_opt_in_to_public(self, bridge):
        """Operator explicitly setting bridge_target_channel=0 is a
        legitimate opt-in to broadcasting on slot 0 (the fix doesn't
        ban slot 0, it bans the *implicit* default)."""
        bridge.config.meshcore.bridge_target_channel = 0
        from gateway.rns_bridge import BridgedMessage
        msg = BridgedMessage(
            source_network="meshtastic", source_id="!abc",
            destination_id=None, content="x",
            metadata={"channel": 7},
        )
        assert bridge._resolve_bridge_target_channel(msg) == 0

    def test_send_to_meshcore_broadcast_no_channel_drops(self, bridge):
        """send_to_meshcore("hi") with no destination and no channel must
        DROP rather than silently broadcast to slot 0. Mirrors the
        Issue #35 DM-drop discipline at the wrapper level."""
        bridge._meshcore_handler = MagicMock()
        bridge._meshcore_handler.send_text = MagicMock(return_value=True)
        ok = bridge.send_to_meshcore("hi")
        assert ok is False
        bridge._meshcore_handler.send_text.assert_not_called()
        assert bridge.stats.get('meshcore_bridge_default_channel_drop') == 1

    def test_send_to_meshcore_broadcast_explicit_channel_sends(self, bridge):
        """Explicit channel=N reaches handler.send_text(msg, None, N)."""
        bridge._meshcore_handler = MagicMock()
        bridge._meshcore_handler.send_text = MagicMock(return_value=True)
        ok = bridge.send_to_meshcore("hi", channel=2)
        assert ok is True
        bridge._meshcore_handler.send_text.assert_called_once_with("hi", None, 2)

    def test_send_to_meshcore_dm_ignores_channel_default(self, bridge):
        """DMs pass the destination; channel arg is irrelevant. The
        sentinel default must NOT cause DMs to drop."""
        bridge._meshcore_handler = MagicMock()
        bridge._meshcore_handler.send_text = MagicMock(return_value=True)
        ok = bridge.send_to_meshcore("hi", destination="contact-abc")
        assert ok is True
        bridge._meshcore_handler.send_text.assert_called_once_with(
            "hi", "contact-abc", -1
        )

    def test_process_bridge_to_meshcore_uses_config_not_metadata(self, bridge):
        """End-to-end: Meshtastic packet on channel 2 lands on the
        operator-configured slot 1, NOT the source channel index.
        Pre-fix the resolver honored metadata first and re-introduced
        the leak when packet.channel happened to be 0."""
        from gateway.bridge_health import SubsystemState
        from gateway.rns_bridge import BridgedMessage

        bridge.config.meshcore.bridge_target_channel = 1
        bridge._meshcore_handler = MagicMock()
        bridge._meshcore_handler.send_text = MagicMock(return_value=True)
        bridge.health.get_subsystem_state = MagicMock(
            return_value=SubsystemState.HEALTHY
        )

        msg = BridgedMessage(
            source_network="meshtastic", source_id="!abc",
            destination_id=None, content="hello",
            metadata={"channel": 2},  # source channel; must NOT route to slot 2
        )
        bridge._process_bridge_to_meshcore(msg)
        bridge._meshcore_handler.send_text.assert_called_once()
        args, _ = bridge._meshcore_handler.send_text.call_args
        # args = (bridged_content, destination, channel)
        assert args[1] is None
        # Config target 1, NOT source metadata 2.
        assert args[2] == 1

    def test_process_bridge_to_meshcore_meshtastic_ch0_does_not_leak(self, bridge):
        """The EXACT pre-fix leak shape: Meshtastic packet on channel 0
        (the default), bridged to MeshCore. Old behaviour: handler called
        with channel=0 → MeshCore slot 0 = Public. New behaviour: handler
        called with the configured private slot."""
        from gateway.bridge_health import SubsystemState
        from gateway.rns_bridge import BridgedMessage

        bridge.config.meshcore.bridge_target_channel = 1
        bridge._meshcore_handler = MagicMock()
        bridge._meshcore_handler.send_text = MagicMock(return_value=True)
        bridge.health.get_subsystem_state = MagicMock(
            return_value=SubsystemState.HEALTHY
        )

        msg = BridgedMessage(
            source_network="meshtastic", source_id="!abc",
            destination_id=None, content="HawaiiNet broadcast",
            metadata={"channel": 0, "snr": -100.0},
        )
        bridge._process_bridge_to_meshcore(msg)
        args, _ = bridge._meshcore_handler.send_text.call_args
        assert args[2] == 1, "Meshtastic ch0 must land on configured slot, not slot 0"

    def test_process_bridge_to_meshcore_drops_when_no_channel(self, bridge):
        """Pre-fix leak shape: no metadata channel + no config target →
        used to call send_to_meshcore(content) → slot 0 broadcast.
        Now: drop + counter, handler never invoked."""
        from gateway.bridge_health import SubsystemState
        from gateway.rns_bridge import BridgedMessage

        bridge.config.meshcore.bridge_target_channel = -1
        bridge._meshcore_handler = MagicMock()
        bridge._meshcore_handler.send_text = MagicMock(return_value=True)
        bridge.health.get_subsystem_state = MagicMock(
            return_value=SubsystemState.HEALTHY
        )

        msg = BridgedMessage(
            source_network="meshtastic", source_id="!abc",
            destination_id=None, content="leaky",
            metadata={},
        )
        bridge._process_bridge_to_meshcore(msg)
        bridge._meshcore_handler.send_text.assert_not_called()
        assert bridge.stats.get('meshcore_bridge_default_channel_drop') == 1

    def test_process_bridge_to_meshcore_uses_config_default(self, bridge):
        """Config-set target_channel kicks in when metadata is empty."""
        from gateway.bridge_health import SubsystemState
        from gateway.rns_bridge import BridgedMessage

        bridge.config.meshcore.bridge_target_channel = 1
        bridge._meshcore_handler = MagicMock()
        bridge._meshcore_handler.send_text = MagicMock(return_value=True)
        bridge.health.get_subsystem_state = MagicMock(
            return_value=SubsystemState.HEALTHY
        )

        msg = BridgedMessage(
            source_network="meshtastic", source_id="!abc",
            destination_id=None, content="via-config",
            metadata={},
        )
        bridge._process_bridge_to_meshcore(msg)
        bridge._meshcore_handler.send_text.assert_called_once()
        args, _ = bridge._meshcore_handler.send_text.call_args
        assert args[2] == 1

    def test_process_bridge_to_meshcore_drops_meshcore_origin_echo(self, bridge):
        """Split-horizon (p4 self-echo, 2026-05-26): content carrying a
        MeshCore-origin marker round-tripped from MeshCore and must NOT be
        re-injected onto MeshCore. Two real echo shapes from the daemon
        chat buffer: a bare [ch0:p4] tag and a [MC:p4] nested after a wire
        prefix. Both must be dropped (no send) and counted."""
        from gateway.bridge_health import SubsystemState
        from gateway.rns_bridge import BridgedMessage

        for content in (
            "[ch0:p4] live love",                              # bare fan-out tag
            "[meshtastic ch2:!32962f10] [MC:p4] live love",    # nested MC marker
        ):
            bridge.config.meshcore.bridge_target_channel = 1
            bridge._meshcore_handler = MagicMock()
            bridge._meshcore_handler.send_text = MagicMock(return_value=True)
            bridge.health.get_subsystem_state = MagicMock(
                return_value=SubsystemState.HEALTHY
            )
            before = bridge.stats.get('meshcore_bridge_echo_loop_drop', 0)

            msg = BridgedMessage(
                source_network="rns", source_id="aaa2365f7990",
                destination_id=None, content=content, metadata={},
            )
            bridge._process_bridge_to_meshcore(msg)

            bridge._meshcore_handler.send_text.assert_not_called()
            assert bridge.stats['meshcore_bridge_echo_loop_drop'] == before + 1, content

    def test_process_bridge_to_meshcore_allows_non_meshcore_origin(self, bridge):
        """Regression guard: genuine RNS/Meshtastic-origin content (no
        MeshCore marker) still bridges to MeshCore — the echo guard must
        not over-drop legitimate forward delivery."""
        from gateway.bridge_health import SubsystemState
        from gateway.rns_bridge import BridgedMessage

        bridge.config.meshcore.bridge_target_channel = 1
        bridge._meshcore_handler = MagicMock()
        bridge._meshcore_handler.send_text = MagicMock(return_value=True)
        bridge.health.get_subsystem_state = MagicMock(
            return_value=SubsystemState.HEALTHY
        )

        msg = BridgedMessage(
            source_network="rns", source_id="deadbeef",
            destination_id=None, content="genuine nomadnet message",
            metadata={},
        )
        bridge._process_bridge_to_meshcore(msg)
        bridge._meshcore_handler.send_text.assert_called_once()
        assert bridge.stats.get('meshcore_bridge_echo_loop_drop', 0) == 0

    def test_process_bridge_to_meshcore_defers_owned_source_to_reemit(self, bridge):
        """Reply-doubling guard (2026-05-26): Meshtastic-origin content
        ([meshtastic ch..] tag) from a source the meshtastic_reemit bridge
        OWNS is dropped here — reemit delivers it cleanly as [Mesh:..], so
        re-injecting via the generic path would double it on MeshCore."""
        from gateway.bridge_health import SubsystemState
        from gateway.rns_bridge import BridgedMessage

        bridge.config.meshtastic_reemit.enabled = True
        bridge.config.meshtastic_reemit.source_identities = [
            "aaa2365f799cc28aa7697df943096074"
        ]
        bridge.config.meshcore.bridge_target_channel = 1
        bridge._meshcore_handler = MagicMock()
        bridge._meshcore_handler.send_text = MagicMock(return_value=True)
        bridge.health.get_subsystem_state = MagicMock(
            return_value=SubsystemState.HEALTHY
        )

        msg = BridgedMessage(
            source_network="rns",
            source_id="aaa2365f799cc28aa7697df943096074",  # owned by reemit
            destination_id=None,
            content="[meshtastic ch2:!a2e95ba4] 0.1in.",
            metadata={},
        )
        bridge._process_bridge_to_meshcore(msg)
        bridge._meshcore_handler.send_text.assert_not_called()
        assert bridge.stats['meshcore_bridge_reemit_dedup_drop'] == 1

    def test_process_bridge_to_meshcore_keeps_unowned_source(self, bridge):
        """Coverage-safety: the SAME Meshtastic-origin content from a source
        NOT in source_identities must still be delivered — the generic path
        is its sole delivery, so the dedup guard must not touch it."""
        from gateway.bridge_health import SubsystemState
        from gateway.rns_bridge import BridgedMessage

        bridge.config.meshtastic_reemit.enabled = True
        bridge.config.meshtastic_reemit.source_identities = [
            "aaa2365f799cc28aa7697df943096074"
        ]
        bridge.config.meshcore.bridge_target_channel = 1
        bridge._meshcore_handler = MagicMock()
        bridge._meshcore_handler.send_text = MagicMock(return_value=True)
        bridge.health.get_subsystem_state = MagicMock(
            return_value=SubsystemState.HEALTHY
        )

        msg = BridgedMessage(
            source_network="rns",
            source_id="deadbeefcafe0000deadbeefcafe0000",  # NOT owned by reemit
            destination_id=None,
            content="[meshtastic ch2:!99887766] hello from uncovered gw",
            metadata={},
        )
        bridge._process_bridge_to_meshcore(msg)
        bridge._meshcore_handler.send_text.assert_called_once()
        assert bridge.stats.get('meshcore_bridge_reemit_dedup_drop', 0) == 0

    # ─── Bridge_loop disconnect-window requeue path (bug 2 fix 2026-05-20) ───

    def test_bridge_loop_disconnect_requeues_with_resolved_channel(self, bridge):
        """When mc_state is DISCONNECTED, the bridge_loop must resolve the
        target channel BEFORE requeueing — otherwise the replay path lifts
        the SOURCE metadata.channel and the leak shape returns. Pin: the
        requeue payload's `channel` field comes from `config.bridge_target_channel`
        (the resolver result), NOT from the source metadata.

        Test mechanics: the bridge_loop runs `while self._running:` and only
        re-evaluates the flag at the top, so flipping `_running=False` has
        to happen from inside a function the loop calls on EACH iteration.
        `_drain_persistent_queue` runs only every 150 iters (~30s) — pinning
        the flag there hangs under CI's 30s pytest-timeout (Issue #36 ate
        an earlier debug attempt). Patch the resolver itself so the flag
        flips on the SAME iteration that processes the msg."""
        from gateway.bridge_health import SubsystemState
        from gateway.rns_bridge import BridgedMessage

        bridge.config.meshcore.bridge_target_channel = 1
        bridge._meshcore_handler = MagicMock()
        bridge.health.get_subsystem_state = MagicMock(
            return_value=SubsystemState.DISCONNECTED
        )

        captured = {}
        def fake_resolve(m):
            captured['msg_seen_by_resolver'] = m
            # Stop AFTER this iteration completes (so the requeue still fires).
            bridge._running = False
            return 1  # The configured target slot.

        def fake_requeue(m, dest, *, channel_override=None):
            captured['msg'] = m
            captured['destination'] = dest
            captured['channel_override'] = channel_override
            return True

        msg = BridgedMessage(
            source_network="meshtastic", source_id="!abc",
            destination_id=None, content="should not leak on replay",
            metadata={"channel": 0},  # source Meshtastic ch0
        )
        bridge._bridge_to_meshcore_queue.put(msg)
        bridge._running = True

        with patch.object(bridge, '_process_mesh_to_rns'), \
             patch.object(bridge, '_process_rns_to_mesh'), \
             patch.object(bridge, '_resolve_bridge_target_channel', side_effect=fake_resolve), \
             patch.object(bridge, '_requeue_failed_message', side_effect=fake_requeue):
            bridge._bridge_loop()

        assert captured.get('destination') == "meshcore"
        # The KEY assertion: override is the CONFIG value (1, returned by
        # the resolver), not the SOURCE metadata channel (0). Pre-fix the
        # requeue would lift the 0 and the replay would land on Public.
        assert captured.get('channel_override') == 1

    def test_bridge_loop_disconnect_drops_when_no_channel_resolvable(self, bridge):
        """When mc_state is DISCONNECTED AND the resolver returns -1
        (no metadata channel, no config target), the bridge_loop must
        DROP with the same counter the healthy path uses. Operators can
        monitor a single counter on /api/stats for both leak paths."""
        from gateway.bridge_health import SubsystemState
        from gateway.rns_bridge import BridgedMessage

        bridge.config.meshcore.bridge_target_channel = -1
        bridge._meshcore_handler = MagicMock()
        bridge.health.get_subsystem_state = MagicMock(
            return_value=SubsystemState.DISCONNECTED
        )

        def fake_resolve(m):
            # Drop path: return -1 AND stop the loop on the same iteration.
            bridge._running = False
            return -1

        msg = BridgedMessage(
            source_network="meshtastic", source_id="!abc",
            destination_id=None, content="must be dropped",
            metadata={},  # no channel info — resolver returns -1
        )
        bridge._bridge_to_meshcore_queue.put(msg)
        bridge._running = True

        with patch.object(bridge, '_process_mesh_to_rns'), \
             patch.object(bridge, '_process_rns_to_mesh'), \
             patch.object(bridge, '_resolve_bridge_target_channel', side_effect=fake_resolve), \
             patch.object(bridge, '_requeue_failed_message') as mock_requeue:
            bridge._bridge_loop()

        # Requeue NEVER called — drop path took over.
        mock_requeue.assert_not_called()
        # Counter shared with the healthy-path drop, so operators see one
        # signal whether the leak attempted to fire from `_process_bridge_to_meshcore`
        # or from this disconnect-window code.
        assert bridge.stats.get('meshcore_bridge_default_channel_drop') == 1


# ---------------------------------------------------------------------------
# enqueue_message
# ---------------------------------------------------------------------------

class TestEnqueueMessage:
    """Tests for enqueue_message method."""

    def test_no_queue_falls_back_to_direct_meshtastic(self, bridge):
        bridge._persistent_queue = None

        with patch.object(bridge, 'send_to_meshtastic', return_value=True):
            result = bridge.enqueue_message("hi", "!dest", dest_type="meshtastic")
        assert result == "direct"

    def test_no_queue_falls_back_to_direct_rns(self, bridge):
        bridge._persistent_queue = None

        with patch.object(bridge, 'send_to_rns', return_value=True):
            result = bridge.enqueue_message("hi", "dest_hash", dest_type="rns",
                                           destination_hash="aabbccdd")
        assert result == "direct"

    def test_no_queue_direct_failure_returns_none(self, bridge):
        bridge._persistent_queue = None

        with patch.object(bridge, 'send_to_meshtastic', return_value=False):
            result = bridge.enqueue_message("hi", "!dest", dest_type="meshtastic")
        assert result is None

    def test_with_queue_enqueues(self, bridge):
        mock_queue = MagicMock()
        mock_queue.enqueue.return_value = "msg-123"
        bridge._persistent_queue = mock_queue

        # enqueue_message moved to BridgeSendMixin (2026-06-09 rns_bridge
        # split) — it resolves MessagePriority in its new defining module,
        # so the patch target moves with it.
        with patch("gateway.bridge_send_mixin.MessagePriority") as MockPriority:
            MockPriority.NORMAL = "normal"
            MockPriority.HIGH = "high"
            MockPriority.LOW = "low"
            MockPriority.URGENT = "urgent"
            result = bridge.enqueue_message("hi", "!dest", dest_type="meshtastic", priority="normal")

        assert result == "msg-123"
        mock_queue.enqueue.assert_called_once()


# ---------------------------------------------------------------------------
# get_queue_stats
# ---------------------------------------------------------------------------

class TestGetQueueStats:
    """Tests for get_queue_stats."""

    def test_no_queue_returns_empty(self, bridge):
        bridge._persistent_queue = None
        assert bridge.get_queue_stats() == {}

    def test_with_queue_delegates(self, bridge):
        mock_queue = MagicMock()
        mock_queue.get_stats.return_value = {"pending": 5}
        bridge._persistent_queue = mock_queue
        assert bridge.get_queue_stats() == {"pending": 5}


# ---------------------------------------------------------------------------
# test_connection
# ---------------------------------------------------------------------------

class TestTestConnection:
    """Tests for test_connection method."""

    def test_both_disconnected(self, bridge):
        bridge._mesh_handler.test_connection.return_value = False
        with patch.object(bridge, '_test_rns', return_value=False):
            result = bridge.test_connection()
        assert result['meshtastic']['connected'] is False
        assert result['rns']['connected'] is False

    def test_meshtastic_connected(self, bridge):
        bridge._mesh_handler.test_connection.return_value = True
        with patch.object(bridge, '_test_rns', return_value=False):
            result = bridge.test_connection()
        assert result['meshtastic']['connected'] is True
        assert result['rns']['connected'] is False

    def test_rns_connected(self, bridge):
        bridge._mesh_handler.test_connection.return_value = False
        with patch.object(bridge, '_test_rns', return_value=True):
            result = bridge.test_connection()
        assert result['meshtastic']['connected'] is False
        assert result['rns']['connected'] is True

    def test_meshtastic_error(self, bridge):
        bridge._mesh_handler.test_connection.side_effect = RuntimeError("fail")
        with patch.object(bridge, '_test_rns', return_value=True):
            result = bridge.test_connection()
        assert result['meshtastic']['connected'] is False
        assert result['meshtastic']['error'] == "fail"
        assert result['rns']['connected'] is True

    def test_rns_error(self, bridge):
        bridge._mesh_handler.test_connection.return_value = True
        with patch.object(bridge, '_test_rns', side_effect=RuntimeError("rns fail")):
            result = bridge.test_connection()
        assert result['meshtastic']['connected'] is True
        assert result['rns']['connected'] is False
        assert result['rns']['error'] == "rns fail"


# ---------------------------------------------------------------------------
# on_meshtastic_receive compatibility shim
# ---------------------------------------------------------------------------

class TestOnMeshtasticReceive:
    """Tests for _on_meshtastic_receive compatibility shim."""

    def test_delegates_to_handler(self, bridge):
        packet = {"decoded": {"text": "hello"}}
        bridge._on_meshtastic_receive(packet)
        bridge._mesh_handler._on_receive.assert_called_once_with(packet)

    def test_no_handler_no_error(self, bridge):
        bridge._mesh_handler = None
        bridge._on_meshtastic_receive({"decoded": {}})  # Should not raise


# ---------------------------------------------------------------------------
# _get_rns_destination
# ---------------------------------------------------------------------------

class TestGetRNSDestination:
    """Tests for _get_rns_destination."""

    def test_returns_rns_hash_if_found(self, bridge):
        mock_node = MagicMock()
        mock_node.rns_hash = b'\xab\xcd\xef'
        bridge.node_tracker.get_node_by_mesh_id.return_value = mock_node
        result = bridge._get_rns_destination("!aabb0042")
        assert result == b'\xab\xcd\xef'

    def test_returns_none_if_not_found(self, bridge):
        bridge.node_tracker.get_node_by_mesh_id.return_value = None
        assert bridge._get_rns_destination("!aabb0042") is None

    def test_returns_none_if_no_rns_hash(self, bridge):
        mock_node = MagicMock(spec=[])  # No rns_hash attribute
        bridge.node_tracker.get_node_by_mesh_id.return_value = mock_node
        assert bridge._get_rns_destination("!aabb0042") is None


# ---------------------------------------------------------------------------
# Routing stats / classification
# ---------------------------------------------------------------------------

class TestRoutingStats:
    """Tests for routing stats and classification methods."""

    def test_get_routing_stats_no_classifier(self, bridge):
        bridge._router._classifier = None
        stats = bridge.get_routing_stats()
        assert 'messages_mesh_to_rns' in stats
        assert 'classifier' not in stats

    def test_get_last_classification_none(self, bridge):
        bridge._router._last_classification = None
        assert bridge.get_last_classification() is None

    def test_get_last_classification_returns_dict(self, bridge):
        mock_result = MagicMock()
        mock_result.to_dict.return_value = {"category": "bridge_rns", "confidence": 0.9}
        bridge._router._last_classification = mock_result
        result = bridge.get_last_classification()
        assert result["category"] == "bridge_rns"

    def test_fix_routing_no_classifier(self, bridge):
        bridge._router._classifier = None
        assert bridge.fix_routing("msg-1", "bridge_rns") is False

    def test_fix_routing_no_fix_registry(self, bridge):
        bridge._router._classifier = MagicMock()
        bridge._router._classifier.fix_registry = None
        assert bridge.fix_routing("msg-1", "bridge_rns") is False


# ---------------------------------------------------------------------------
# start / stop lifecycle
# ---------------------------------------------------------------------------

class TestStartStop:
    """Tests for start/stop lifecycle."""

    def test_start_sets_running(self, bridge):
        with patch.object(bridge, '_start_websocket_server'), \
             patch.object(bridge, '_init_rns_main_thread'):
            bridge.start()
        assert bridge._running is True
        assert bridge.stats['start_time'] is not None

    def test_start_when_already_running(self, bridge):
        bridge._running = True
        result = bridge.start()
        assert result is True

    def test_start_returns_true(self, bridge):
        with patch.object(bridge, '_start_websocket_server'), \
             patch.object(bridge, '_init_rns_main_thread'):
            result = bridge.start()
        assert result is True

    def test_stop_clears_state(self, bridge):
        bridge._running = True
        bridge._mesh_handler = MagicMock()
        bridge._persistent_queue = MagicMock()

        with patch.object(bridge, '_disconnect_rns'), \
             patch.object(bridge, '_stop_websocket_server'):
            bridge.stop()

        assert bridge._running is False
        bridge._persistent_queue.stop_processing.assert_called_once()

    def test_stop_when_not_running(self, bridge):
        bridge._running = False
        bridge.stop()  # Should not raise

    def test_start_starts_node_tracker(self, bridge):
        with patch.object(bridge, '_start_websocket_server'), \
             patch.object(bridge, '_init_rns_main_thread'):
            bridge.start()
        bridge.node_tracker.start.assert_called_once()

    def test_stop_stops_node_tracker(self, bridge):
        bridge._running = True
        with patch.object(bridge, '_disconnect_rns'), \
             patch.object(bridge, '_stop_websocket_server'):
            bridge.stop()
        bridge.node_tracker.stop.assert_called_once()

    def test_stop_disconnects_mesh_handler(self, bridge):
        bridge._running = True
        with patch.object(bridge, '_disconnect_rns'), \
             patch.object(bridge, '_stop_websocket_server'):
            bridge.stop()
        bridge._mesh_handler.disconnect.assert_called_once()

    def test_stop_sets_stop_event(self, bridge):
        bridge._running = True
        with patch.object(bridge, '_disconnect_rns'), \
             patch.object(bridge, '_stop_websocket_server'):
            bridge.stop()
        assert bridge._stop_event.is_set()


# ---------------------------------------------------------------------------
# RNS connection flow
# ---------------------------------------------------------------------------

class TestRNSConnectionFlow:
    """Tests for RNS connection and LXMF setup flow."""

    def test_connect_rns_import_error_is_permanent(self, bridge):
        bridge._rns_pre_initialized = False

        with patch('gateway._rns_bridge_connection._HAS_RNS', False):
            bridge._connect_rns()

        assert bridge._connected_rns is False
        assert bridge._rns_init_failed_permanently is True

    def test_disconnect_rns_clears_all_state(self, bridge):
        bridge._reticulum = MagicMock()
        bridge._lxmf_router = MagicMock()
        bridge._lxmf_source = MagicMock()
        bridge._identity = MagicMock()
        bridge._connected_rns = True

        with patch.dict('sys.modules', {'RNS': MagicMock()}):
            bridge._disconnect_rns()

        assert bridge._reticulum is None
        assert bridge._lxmf_router is None
        assert bridge._lxmf_source is None
        assert bridge._identity is None
        assert bridge._connected_rns is False

    def test_disconnect_rns_handles_no_reticulum(self, bridge):
        bridge._reticulum = None
        bridge._disconnect_rns()  # Should not raise
        assert bridge._connected_rns is False

    def test_rns_loop_logs_permanent_failure(self, bridge):
        bridge._running = True
        bridge._rns_init_failed_permanently = True

        def stop_after_wait(timeout):
            bridge._running = False
            return True

        bridge._stop_event = MagicMock()
        bridge._stop_event.wait = stop_after_wait

        # Should log warning and exit
        bridge._rns_loop()
        # Verify it ran without error (no assertion on logger needed)

    def test_suppress_signal_noop_on_main_thread(self, bridge):
        """Signal suppression is a no-op passthrough on the main thread."""
        import signal
        original = signal.signal
        with bridge._suppress_signal_in_thread():
            assert signal.signal is original

    def test_suppress_signal_replaces_in_background_thread(self, bridge):
        """Signal suppression replaces signal.signal in background threads."""
        import signal
        results = {}

        def _check():
            with bridge._suppress_signal_in_thread():
                results['replaced'] = signal.signal is not _original
                # Should return SIG_DFL instead of raising ValueError
                results['retval'] = signal.signal(signal.SIGTERM, signal.SIG_DFL)
            results['restored'] = signal.signal is _original

        _original = signal.signal
        t = threading.Thread(target=_check)
        t.start()
        t.join(timeout=5)

        assert results.get('replaced') is True
        assert results.get('retval') == signal.SIG_DFL
        assert results.get('restored') is True

    def test_connect_rns_lxmf_signal_error_handled(self, bridge):
        """LXMF signal error in background thread is suppressed by context manager."""
        import signal as _signal_mod
        bridge._rns_pre_initialized = True

        mock_rns = MagicMock()
        mock_lxmf = MagicMock()

        # Simulate LXMF.LXMRouter() calling signal.signal() internally
        def lxmf_router_init(*args, **kwargs):
            # This would fail in a real background thread without suppression
            _signal_mod.signal(_signal_mod.SIGTERM, _signal_mod.SIG_DFL)
            return MagicMock()

        mock_lxmf.LXMRouter = lxmf_router_init

        with patch('gateway._rns_bridge_connection._RNS_mod', mock_rns), \
             patch('gateway._rns_bridge_connection._LXMF_mod', mock_lxmf), \
             patch('gateway._rns_bridge_connection._HAS_RNS', True), \
             patch('gateway._rns_bridge_connection._HAS_LXMF', True), \
             patch('gateway._rns_bridge_connection.check_service', return_value=MagicMock(available=True)):
            # Run from background thread to trigger suppression
            errors = []
            def _run():
                try:
                    bridge._connect_rns()
                except Exception as e:
                    errors.append(e)

            t = threading.Thread(target=_run)
            t.start()
            t.join(timeout=5)

            assert not errors, f"Unexpected errors: {errors}"


# ---------------------------------------------------------------------------
# Module-level headless helpers
# ---------------------------------------------------------------------------

class TestHeadlessHelpers:
    """Tests for module-level helper functions (extracted to gateway_cli.py)."""

    def test_is_gateway_running_no_bridge(self):
        import gateway.gateway_cli as cli
        original = cli._active_bridge
        try:
            cli._active_bridge = None
            assert cli.is_gateway_running() is False
        finally:
            cli._active_bridge = original

    def test_is_gateway_running_with_bridge(self):
        import gateway.gateway_cli as cli
        original = cli._active_bridge
        try:
            mock_bridge = MagicMock()
            mock_bridge._running = True
            cli._active_bridge = mock_bridge
            assert cli.is_gateway_running() is True
        finally:
            cli._active_bridge = original

    def test_is_gateway_running_stopped_bridge(self):
        import gateway.gateway_cli as cli
        original = cli._active_bridge
        try:
            mock_bridge = MagicMock()
            mock_bridge._running = False
            cli._active_bridge = mock_bridge
            assert cli.is_gateway_running() is False
        finally:
            cli._active_bridge = original

    def test_get_gateway_stats_no_bridge(self):
        import gateway.gateway_cli as cli
        original = cli._active_bridge
        try:
            cli._active_bridge = None
            stats = cli.get_gateway_stats()
            assert stats['running'] is False
            assert stats['status'] == 'Not started'
        finally:
            cli._active_bridge = original

    def test_get_gateway_stats_with_bridge(self):
        import gateway.gateway_cli as cli
        original = cli._active_bridge
        try:
            mock_bridge = MagicMock()
            mock_bridge._running = True
            mock_bridge._mesh_handler.is_connected = True
            mock_bridge._connected_rns = False
            mock_bridge.get_status.return_value = {
                'statistics': {
                    'messages_mesh_to_rns': 3,
                    'messages_rns_to_mesh': 1,
                    'errors': 0,
                    'bounced': 0,
                },
                'uptime_seconds': 120.0,
            }
            mock_bridge.health.get_summary.return_value = {"status": "ok"}
            mock_bridge.delivery_tracker.get_stats.return_value = {"delivered": 2}
            cli._active_bridge = mock_bridge

            stats = cli.get_gateway_stats()
            assert stats['running'] is True
            assert stats['messages_mesh_to_rns'] == 3
            assert stats['health'] == {"status": "ok"}
            assert stats['delivery'] == {"delivered": 2}
        finally:
            cli._active_bridge = original

    def test_stop_gateway_headless_no_bridge(self):
        import gateway.gateway_cli as cli
        original = cli._active_bridge
        try:
            cli._active_bridge = None
            assert cli.stop_gateway_headless() is True
        finally:
            cli._active_bridge = original

    def test_stop_gateway_headless_with_bridge(self):
        import gateway.gateway_cli as cli
        original = cli._active_bridge
        try:
            mock_bridge = MagicMock()
            cli._active_bridge = mock_bridge
            assert cli.stop_gateway_headless() is True
            mock_bridge.stop.assert_called_once()
            assert cli._active_bridge is None
        finally:
            cli._active_bridge = original

    def test_stop_gateway_headless_error(self):
        import gateway.gateway_cli as cli
        original = cli._active_bridge
        try:
            mock_bridge = MagicMock()
            mock_bridge.stop.side_effect = RuntimeError("stop fail")
            cli._active_bridge = mock_bridge
            assert cli.stop_gateway_headless() is False
        finally:
            cli._active_bridge = original

    def test_reexport_from_rns_bridge(self):
        """Verify backward-compatible re-export from rns_bridge."""
        import gateway.rns_bridge as mod
        assert hasattr(mod, 'start_gateway_headless')
        assert hasattr(mod, 'stop_gateway_headless')
        assert hasattr(mod, 'get_gateway_stats')
        assert hasattr(mod, 'is_gateway_running')


# ---------------------------------------------------------------------------
# Thread safety of callbacks
# ---------------------------------------------------------------------------

class TestCallbackThreadSafety:
    """Tests for thread safety of callback systems."""

    def test_concurrent_callback_registration(self, bridge):
        """Multiple threads register callbacks concurrently."""
        errors = []

        def register_callbacks():
            try:
                for _ in range(20):
                    bridge.register_message_callback(lambda msg: None)
                    bridge.register_status_callback(lambda s, d: None)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=register_callbacks) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert len(errors) == 0
        assert len(bridge._message_callbacks) == 100
        assert len(bridge._status_callbacks) == 100

    def test_concurrent_notify_and_register(self, bridge):
        """Notification while registration is happening should not crash."""
        errors = []
        from gateway.rns_bridge import BridgedMessage

        def register_loop():
            try:
                for _ in range(50):
                    bridge.register_message_callback(lambda msg: None)
            except Exception as e:
                errors.append(e)

        def notify_loop():
            try:
                msg = BridgedMessage(
                    source_network="rns", source_id="abc",
                    destination_id=None, content="x"
                )
                for _ in range(50):
                    bridge._notify_message(msg)
            except Exception as e:
                errors.append(e)

        t1 = threading.Thread(target=register_loop)
        t2 = threading.Thread(target=notify_loop)
        t1.start()
        t2.start()
        t1.join(timeout=5)
        t2.join(timeout=5)

        assert len(errors) == 0


# ---------------------------------------------------------------------------
# _bridge_loop
# ---------------------------------------------------------------------------

class TestBridgeLoop:
    """Tests for _bridge_loop message processing."""

    def test_processes_mesh_to_rns_queue(self, bridge):
        from gateway.rns_bridge import BridgedMessage
        msg = BridgedMessage(
            source_network="meshtastic", source_id="!abc",
            destination_id=None, content="test",
        )
        bridge._mesh_to_rns_queue.put(msg)
        bridge._running = True

        processed = []

        def mock_process(m):
            processed.append(m)
            bridge._running = False  # Stop after first iteration

        with patch.object(bridge, '_process_mesh_to_rns', side_effect=mock_process), \
             patch.object(bridge, '_process_rns_to_mesh'):
            bridge._bridge_loop()

        assert len(processed) == 1
        assert processed[0].content == "test"

    def test_processes_rns_to_mesh_queue(self, bridge):
        from gateway.rns_bridge import BridgedMessage
        msg = BridgedMessage(
            source_network="rns", source_id="abc",
            destination_id=None, content="rns msg",
        )
        bridge._rns_to_mesh_queue.put(msg)
        bridge._running = True

        processed = []

        def mock_process(m):
            processed.append(m)
            bridge._running = False

        with patch.object(bridge, '_process_mesh_to_rns'), \
             patch.object(bridge, '_process_rns_to_mesh', side_effect=mock_process):
            bridge._bridge_loop()

        assert len(processed) == 1

    def test_bridge_loop_handles_exception(self, bridge):
        """Bridge loop should not crash on exception."""
        bridge._running = True
        call_count = 0

        def failing_get(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                raise RuntimeError("queue error")
            bridge._running = False
            raise Empty()

        with patch.object(bridge._mesh_to_rns_queue, 'get', side_effect=failing_get):
            bridge._bridge_loop()

        # Should have continued past the errors
        assert call_count >= 2


# ---------------------------------------------------------------------------
# _test_rns
# ---------------------------------------------------------------------------

class TestTestRNS:
    """Tests for _test_rns method."""

    @patch('gateway.rns_bridge._HAS_RNS', True)
    def test_returns_true_when_importable(self, bridge):
        assert bridge._test_rns() is True

    @patch('gateway.rns_bridge._HAS_RNS', False)
    def test_returns_false_when_not_importable(self, bridge):
        assert bridge._test_rns() is False


# ---------------------------------------------------------------------------
# _REGEX_INPUT_LIMIT
# ---------------------------------------------------------------------------

class TestRegexInputLimit:
    """Tests for regex input length bounding."""

    def test_limit_is_set(self):
        from gateway.message_routing import MessageRouter
        assert MessageRouter._REGEX_INPUT_LIMIT == 512


# ---------------------------------------------------------------------------
# WebSocket server integration
# ---------------------------------------------------------------------------

class TestWebSocketServer:
    """Tests for WebSocket server start/stop."""

    def test_start_websocket_handles_import_error(self, bridge):
        with patch("gateway.rns_bridge.HAS_RNS_SNIFFER", False):
            # Should not crash when websocket module not available
            bridge._start_websocket_server()

    def test_stop_websocket_when_not_started(self, bridge):
        bridge._websocket_started = False
        bridge._stop_websocket_server()  # Should not raise

    def test_start_websocket_default_off_skips_bind(self, bridge):
        """Default config.enable_websocket=False must not call into the
        websocket server start path. meshanchor-map.service is the
        canonical :5001 owner; binding here causes EADDRINUSE."""
        bridge.config.enable_websocket = False
        with patch("gateway.rns_bridge.start_websocket_server") as mock_start, \
             patch("gateway.rns_bridge.is_websocket_available", return_value=True), \
             patch("gateway.rns_bridge.HAS_WEBSOCKET", True):
            bridge._start_websocket_server()
        mock_start.assert_not_called()
        assert bridge._websocket_started is False

    def test_start_websocket_opt_in_calls_bind(self, bridge):
        """When enable_websocket=True (standalone-daemon profile) the
        original bind path runs."""
        bridge.config.enable_websocket = True
        with patch("gateway.rns_bridge.start_websocket_server",
                   return_value=True) as mock_start, \
             patch("gateway.rns_bridge.is_websocket_available", return_value=True), \
             patch("gateway.rns_bridge.HAS_WEBSOCKET", True):
            bridge._start_websocket_server()
        mock_start.assert_called_once_with(port=5001)
        assert bridge._websocket_started is True


# ---------------------------------------------------------------------------
# _on_lxmf_receive — sniffer-capture branch (port of MeshForge #1162)
# ---------------------------------------------------------------------------

class TestLXMFReceiveSnifferCapture:
    """The traffic-inspection sniffer hook in _on_lxmf_receive must accept
    both bytes and str content. LXMessage.content arrives as bytes for
    binary LXMF payloads; pre-fix the .encode('utf-8') call raised
    AttributeError, dropping the capture (delivery itself was unaffected
    but observability missed the message). Mirrors the MeshForge fix in
    PR #1163 / Issue #1162."""

    @staticmethod
    def _make_message(content):
        msg = MagicMock()
        msg.source_hash = b"\x3d\xfb\xdb\x5d" + b"\x00" * 12
        msg.content = content
        msg.title = None
        msg.stamp = None
        msg.fields = None
        return msg

    def _run_with_sniffer(self, bridge, message):
        sniffer = MagicMock()
        sniffer._running = True
        with patch("gateway.rns_bridge.HAS_RNS_SNIFFER", True), \
             patch("gateway.rns_bridge.get_rns_sniffer", return_value=sniffer), \
             patch("gateway.rns_bridge.UnifiedNode"), \
             patch("commands.messaging.store_incoming"):
            bridge._on_lxmf_receive(message)
        return sniffer

    def test_bytes_content_does_not_raise(self, bridge):
        """Regression for MeshForge #1162: bytes content must pass through."""
        sniffer = self._run_with_sniffer(bridge, self._make_message(b"hello-bytes"))
        sniffer._store_packet.assert_called_once()
        captured = sniffer._store_packet.call_args[0][0]
        assert captured.payload == b"hello-bytes"
        assert captured.payload_size == len(b"hello-bytes")

    def test_str_content_is_utf8_encoded(self, bridge):
        sniffer = self._run_with_sniffer(bridge, self._make_message("hello-str-Ω"))
        sniffer._store_packet.assert_called_once()
        captured = sniffer._store_packet.call_args[0][0]
        assert captured.payload == "hello-str-Ω".encode("utf-8")
        assert captured.payload_size == len("hello-str-Ω".encode("utf-8"))

    def test_none_content_yields_empty_payload(self, bridge):
        sniffer = self._run_with_sniffer(bridge, self._make_message(None))
        sniffer._store_packet.assert_called_once()
        captured = sniffer._store_packet.call_args[0][0]
        assert captured.payload == b""
        assert captured.payload_size == 0


# ---------------------------------------------------------------------------
# Issue #66: application-layer ack synthesis on the bridge
# ---------------------------------------------------------------------------

class TestAckSynthesisIssue66:
    """
    _format_ack_text / _emit_ack_to_origin / _maybe_emit_ack_for_msgid /
    _sweep_overdue_acks — the rns_bridge-side wiring that turns LXMF
    delivery proofs (and timeouts) into synthetic ACK CanonicalMessages
    routed back to the origin sender. Symmetric to MeshForge step 3b.
    """

    def test_format_ack_text_delivered(self, bridge):
        text = bridge._format_ack_text("abcdef0123456789", "delivered")
        assert text == "[delivered: abcdef01]"

    def test_format_ack_text_failed(self, bridge):
        assert bridge._format_ack_text("abcdef01", "failed") == "[failed: abcdef01]"

    def test_format_ack_text_timeout(self, bridge):
        assert bridge._format_ack_text("abcdef01", "timeout") == "[timeout: abcdef01]"

    def test_format_ack_text_unknown_kind(self, bridge):
        assert bridge._format_ack_text("abcdef01", "weird") == "[weird: abcdef01]"

    def test_emit_ack_to_origin_meshtastic_dispatches_to_handler(self, bridge):
        mock_handler = MagicMock()
        mock_handler.send_text.return_value = True
        bridge._mesh_handler = mock_handler

        ok = bridge._emit_ack_to_origin(
            "abcdef01234567", "meshtastic", "!aabbccdd", "delivered",
        )
        assert ok is True
        mock_handler.send_text.assert_called_once_with(
            "[delivered: abcdef01]", destination="!aabbccdd", channel=0,
        )

    def test_emit_ack_to_origin_meshcore_dispatches_to_handler(self, bridge):
        mock_handler = MagicMock()
        mock_handler.send_text.return_value = True
        bridge._meshcore_handler = mock_handler

        ok = bridge._emit_ack_to_origin(
            "abcdef01", "meshcore", "publickey-prefix", "failed",
        )
        assert ok is True
        mock_handler.send_text.assert_called_once_with(
            "[failed: abcdef01]", destination="publickey-prefix",
        )

    def test_emit_ack_to_origin_rns_converts_hex_to_bytes(self, bridge):
        """
        rns origin → send_to_rns receives the destination_hash as bytes.
        ack synthesis stores origin_address as the hex string of the LXMF
        destination hash; the dispatch site must convert it back.
        """
        with patch.object(bridge, 'send_to_rns', return_value=True) as ms:
            ok = bridge._emit_ack_to_origin(
                "abcdef01", "rns", "deadbeef00112233", "delivered",
            )
        assert ok is True
        ms.assert_called_once()
        args, kwargs = ms.call_args
        assert args[0] == "[delivered: abcdef01]"
        assert kwargs['destination_hash'] == bytes.fromhex("deadbeef00112233")

    def test_emit_ack_to_origin_rns_bad_hex_returns_false(self, bridge):
        with patch.object(bridge, 'send_to_rns', return_value=True) as ms:
            ok = bridge._emit_ack_to_origin(
                "abcdef01", "rns", "not-hex-zzz", "delivered",
            )
        assert ok is False
        ms.assert_not_called()

    def test_emit_ack_to_origin_unknown_network_returns_false(self, bridge):
        assert bridge._emit_ack_to_origin(
            "abcdef01", "carrier-pigeon", "addr", "delivered",
        ) is False

    def test_emit_ack_to_origin_no_meshtastic_handler_returns_false(self, bridge):
        bridge._mesh_handler = None
        assert bridge._emit_ack_to_origin(
            "abcdef01", "meshtastic", "!aabbccdd", "delivered",
        ) is False

    def test_emit_ack_to_origin_handler_exception_returns_false(self, bridge):
        mock_handler = MagicMock()
        mock_handler.send_text.side_effect = RuntimeError("radio offline")
        bridge._mesh_handler = mock_handler
        assert bridge._emit_ack_to_origin(
            "abcdef01", "meshtastic", "!aabbccdd", "delivered",
        ) is False

    def test_maybe_emit_ack_no_queue_returns_false(self, bridge):
        bridge._persistent_queue = None
        assert bridge._maybe_emit_ack_for_msgid("abc", "delivered") is False

    def test_maybe_emit_ack_no_pending_record_returns_false(self, bridge):
        bridge._persistent_queue = MagicMock()
        bridge._persistent_queue.mark_acked.return_value = None
        with patch.object(bridge, '_emit_ack_to_origin') as me:
            ok = bridge._maybe_emit_ack_for_msgid("abc", "delivered")
        assert ok is False
        me.assert_not_called()

    def test_maybe_emit_ack_routes_to_origin(self, bridge):
        bridge._persistent_queue = MagicMock()
        bridge._persistent_queue.mark_acked.return_value = {
            'message_id': 'abc',
            'origin_network': 'meshcore',
            'origin_address': 'pubkey-abc',
        }
        with patch.object(bridge, '_emit_ack_to_origin', return_value=True) as me:
            ok = bridge._maybe_emit_ack_for_msgid("abc", "delivered")
        assert ok is True
        me.assert_called_once_with(
            "abc",
            origin_network='meshcore',
            origin_address='pubkey-abc',
            kind='delivered',
        )

    def test_maybe_emit_ack_mark_acked_exception_does_not_raise(self, bridge):
        bridge._persistent_queue = MagicMock()
        bridge._persistent_queue.mark_acked.side_effect = RuntimeError("db gone")
        with patch.object(bridge, '_emit_ack_to_origin') as me:
            ok = bridge._maybe_emit_ack_for_msgid("abc", "delivered")
        assert ok is False
        me.assert_not_called()

    def test_sweep_overdue_emits_timeout_and_marks(self, bridge):
        bridge._persistent_queue = MagicMock()
        bridge._persistent_queue.find_overdue_acks.return_value = [
            {
                'message_id': 'aaa',
                'origin_network': 'meshtastic',
                'origin_address': '!aa',
                'timeout_at': '2026-05-18T10:00:00',
            },
            {
                'message_id': 'bbb',
                'origin_network': 'meshcore',
                'origin_address': 'bb',
                'timeout_at': '2026-05-18T10:00:01',
            },
        ]
        bridge._persistent_queue.mark_timeout.return_value = True
        with patch.object(bridge, '_emit_ack_to_origin', return_value=True) as me:
            count = bridge._sweep_overdue_acks()
        assert count == 2
        assert me.call_count == 2
        for call in me.call_args_list:
            assert call.kwargs['kind'] == 'timeout'
        assert bridge._persistent_queue.mark_timeout.call_count == 2

    def test_sweep_skips_when_mark_timeout_loses_race(self, bridge):
        bridge._persistent_queue = MagicMock()
        bridge._persistent_queue.find_overdue_acks.return_value = [{
            'message_id': 'aaa',
            'origin_network': 'meshtastic',
            'origin_address': '!aa',
            'timeout_at': '2026-05-18T10:00:00',
        }]
        bridge._persistent_queue.mark_timeout.return_value = False
        with patch.object(bridge, '_emit_ack_to_origin') as me:
            count = bridge._sweep_overdue_acks()
        assert count == 0
        me.assert_not_called()

    def test_sweep_no_queue_returns_zero(self, bridge):
        bridge._persistent_queue = None
        assert bridge._sweep_overdue_acks() == 0

    def test_sweep_find_overdue_exception_returns_zero(self, bridge):
        bridge._persistent_queue = MagicMock()
        bridge._persistent_queue.find_overdue_acks.side_effect = RuntimeError("db")
        assert bridge._sweep_overdue_acks() == 0

    def test_enqueue_message_with_ack_calls_register_pending_ack(self, bridge):
        bridge._persistent_queue = MagicMock()
        bridge._persistent_queue.enqueue.return_value = "msg-id-1"

        msg_id = bridge.enqueue_message(
            "weather pls",
            destination="!aabbccdd",
            dest_type="meshtastic",
            ack_required=True,
            ack_origin_network="meshcore",
            ack_origin_address="pubkey-abc",
        )
        assert msg_id == "msg-id-1"
        bridge._persistent_queue.register_pending_ack.assert_called_once_with(
            "msg-id-1",
            origin_network="meshcore",
            origin_address="pubkey-abc",
            timeout_seconds=300,
        )

    def test_enqueue_message_without_ack_does_not_register(self, bridge):
        bridge._persistent_queue = MagicMock()
        bridge._persistent_queue.enqueue.return_value = "msg-id-2"

        bridge.enqueue_message(
            "ordinary message",
            destination="!aabbccdd",
            dest_type="meshtastic",
        )
        bridge._persistent_queue.register_pending_ack.assert_not_called()

    def test_enqueue_message_ack_required_without_origin_skips(self, bridge):
        bridge._persistent_queue = MagicMock()
        bridge._persistent_queue.enqueue.return_value = "msg-id-3"

        bridge.enqueue_message(
            "weather pls", destination="!aabbccdd",
            dest_type="meshtastic", ack_required=True,
            ack_origin_address="some-addr",
        )
        bridge._persistent_queue.register_pending_ack.assert_not_called()

    def test_enqueue_message_register_pending_ack_exception_does_not_raise(
        self, bridge,
    ):
        bridge._persistent_queue = MagicMock()
        bridge._persistent_queue.enqueue.return_value = "msg-id-4"
        bridge._persistent_queue.register_pending_ack.side_effect = (
            RuntimeError("db")
        )

        msg_id = bridge.enqueue_message(
            "weather pls", destination="!aabbccdd",
            dest_type="meshtastic", ack_required=True,
            ack_origin_network="meshcore", ack_origin_address="abc",
        )
        assert msg_id == "msg-id-4"


# ---------------------------------------------------------------------------
# Phase 2 (2026-05-24): MeshCore channel broadcasts → Meshtastic bot activation
#
# Inbound MeshCore channel text bakes a "<channel> <sender>: <text>" header
# into the body (source_address empty → src_label 'unknown'). The old egress
# produced "[MC:unknown] meshanchor p4: wx"; the meshing-around bot strips the
# leading "[...]" tag and is left with "meshanchor p4: wx" — the command is no
# longer at index 0, so explicitCmd=True never fires. The header lift makes
# the egress "[MC:p4] wx" so the bare command lands at index 0 while the "[MC:"
# prefix is preserved for the re-emit loop guard.
# ---------------------------------------------------------------------------

class TestMeshcoreChannelHeaderParse:
    """parse_meshcore_channel_header — pure string helper."""

    def _parse(self, content):
        from gateway.meshcore_bridge_mixin import parse_meshcore_channel_header
        return parse_meshcore_channel_header(content)

    def test_channel_and_sender(self):
        assert self._parse("meshanchor p4: wx") == ("p4", "wx")

    def test_sender_only_header(self):
        assert self._parse("p4: cmd") == ("p4", "cmd")

    def test_colon_in_body_split_on_first(self):
        # ':' inside the body (clock time) must survive — split on FIRST ': '
        assert self._parse("meshanchor p4: meet at 5:30 sharp") == (
            "p4", "meet at 5:30 sharp"
        )

    def test_no_header_passthrough(self):
        assert self._parse("just talking story") == (
            "", "just talking story"
        )

    def test_empty_body_passthrough(self):
        # header present but empty body → don't mangle into "[MC:p4] "
        assert self._parse("meshanchor p4: ") == ("", "meshanchor p4: ")

    def test_leading_colon_passthrough(self):
        assert self._parse(": orphan") == ("", ": orphan")

    def test_multiword_sender_uses_last_token(self):
        # channel + multi-token name → last token is the provenance label
        assert self._parse("meshanchor John Doe: hi") == ("Doe", "hi")


class TestMeshcoreEgressHeaderReformat:
    """_process_meshcore_to_bridge reformats channel broadcasts so the
    bridged Meshtastic text carries a bare command at index 0."""

    def _broadcast(self, content, source_address=""):
        from types import SimpleNamespace
        return SimpleNamespace(
            source_address=source_address,
            content=content,
            is_broadcast=True,
            via_internet=False,
        )

    def _capture_egress(self, bridge):
        captured = {}

        def fake_send(text, channel=None):
            captured["text"] = text
            return True

        bridge.send_to_meshtastic = fake_send
        bridge.send_to_rns = MagicMock(return_value=False)
        return captured

    def test_broadcast_lifts_command_to_index_zero(self, bridge):
        captured = self._capture_egress(bridge)
        bridge._process_meshcore_to_bridge(self._broadcast("meshanchor p4: wx"))
        assert captured["text"] == "[MC:p4] wx"
        # loop-guard prefix preserved
        assert captured["text"].startswith("[MC:")
        # after the bot strips the leading "[...]" tag, the command is index 0
        bare = captured["text"].split("] ", 1)[1]
        assert bare.split()[0] == "wx"

    def test_broadcast_without_header_keeps_unknown_label(self, bridge):
        captured = self._capture_egress(bridge)
        bridge._process_meshcore_to_bridge(
            self._broadcast("just talking story")
        )
        assert captured["text"] == "[MC:unknown] just talking story"

    def test_broadcast_colon_in_body_preserved(self, bridge):
        captured = self._capture_egress(bridge)
        bridge._process_meshcore_to_bridge(
            self._broadcast("meshanchor p4: meet at 5:30")
        )
        assert captured["text"] == "[MC:p4] meet at 5:30"

    def test_dm_not_reparsed(self, bridge):
        # A non-broadcast (DM) whose body contains ': ' must NOT be reparsed —
        # DM text is raw, with no firmware-prepended sender header.
        from types import SimpleNamespace
        captured = self._capture_egress(bridge)
        dm = SimpleNamespace(
            source_address="deadbeef12c0ffee",
            content="eta: 5 min",
            is_broadcast=False,
            via_internet=False,
        )
        bridge._process_meshcore_to_bridge(dm)
        assert captured["text"] == "[MC:deadbeef] eta: 5 min"


# ---------------------------------------------------------------------------
# Dual-path dedup + tag-every-chunk (mirror of MeshForge 1494e8f/f02ad82)
# ---------------------------------------------------------------------------

_ML_REPLY = "\n".join(
    f"line{i:02d} the quick brown fox jumps over the lazy dog and keeps running"
    for i in range(1, 8)
)


class TestRecentRfTxRegistry:
    """Content-normalized recently-transmitted registry (parity port)."""

    def _registry(self, **kw):
        from gateway.base_handler import RecentRfTxRegistry
        return RecentRfTxRegistry(**kw)

    def test_register_then_seen(self):
        r = self._registry()
        r.register("hello mesh")
        assert r.seen_within("hello mesh", 60.0) is True

    def test_unregistered_not_seen(self):
        r = self._registry()
        assert r.seen_within("never sent", 60.0) is False

    def test_window_expiry(self):
        r = self._registry()
        r.register("old content")
        key = next(iter(r._entries))
        r._entries[key] -= 61.0
        assert r.seen_within("old content", 60.0) is False

    def test_leading_bridge_tag_normalized(self):
        r = self._registry()
        r.register("Bot CMD?:ping, bbshelp")
        assert r.seen_within("[RNS:abcd] Bot CMD?:ping, bbshelp", 60.0)
        assert r.seen_within("[Mesh:SHORT_TURBO] Bot CMD?:ping, bbshelp", 60.0)
        assert r.seen_within("[MC:p4] Bot CMD?:ping, bbshelp", 60.0)

    def test_whitespace_collapsed(self):
        r = self._registry()
        r.register("two  words   here")
        assert r.seen_within("two words here", 60.0)

    def test_empty_and_tag_only_never_match(self):
        r = self._registry()
        r.register("")
        r.register("[RNS:xxxx] ")
        assert r.seen_within("", 60.0) is False

    def test_bounded_entries(self):
        r = self._registry(max_entries=3)
        for i in range(6):
            r.register(f"content {i}")
        assert len(r._entries) <= 3

    def test_module_singleton_accessor(self):
        import gateway.base_handler as bh
        assert bh.get_rf_tx_registry() is bh._rf_tx_registry


class TestChunkForMeshPrefix:
    """Tag-every-chunk: the bridge tag is the echo-loop invariant and must
    ride EVERY chunk (untagged tails bypassed the guards — MF 2026-06-04)."""

    def test_every_chunk_carries_prefix(self):
        from gateway.base_handler import chunk_for_mesh
        chunks = chunk_for_mesh(_ML_REPLY, prefix="[RNS:abcd] ")
        assert len(chunks) >= 2
        assert all(c.startswith("[RNS:abcd] ") for c in chunks)

    def test_budget_includes_prefix(self):
        from gateway.base_handler import chunk_for_mesh
        chunks = chunk_for_mesh(_ML_REPLY, max_bytes=100, prefix="[RNS:abcd] ")
        assert all(len(c.encode("utf-8")) <= 100 for c in chunks)

    def test_single_fit_returns_prefixed_message(self):
        from gateway.base_handler import chunk_for_mesh
        assert chunk_for_mesh("short", prefix="[RNS:abcd] ") == ["[RNS:abcd] short"]

    def test_content_reassembles_without_loss(self):
        from gateway.base_handler import chunk_for_mesh
        prefix = "[RNS:abcd] "
        chunks = chunk_for_mesh(_ML_REPLY, max_bytes=80, prefix=prefix)
        stripped = [c[len(prefix):] for c in chunks]
        assert " ".join(" ".join(stripped).split()) == " ".join(_ML_REPLY.split())

    def test_no_prefix_behavior_unchanged(self):
        from gateway.base_handler import chunk_for_mesh
        assert chunk_for_mesh(_ML_REPLY) == chunk_for_mesh(_ML_REPLY, prefix="")

    def test_absurd_prefix_falls_back_untagged(self):
        from gateway.base_handler import chunk_for_mesh
        huge = "[RNS:" + "x" * 300 + "] "
        assert chunk_for_mesh(_ML_REPLY, prefix=huge) == chunk_for_mesh(_ML_REPLY)


class TestRnsToMeshTaggedChunks:
    """R→M now tags every chunk + suppresses on registry hit (gated)."""

    def _fresh_registry(self, monkeypatch):
        import gateway.base_handler as bh
        fresh = bh.RecentRfTxRegistry()
        monkeypatch.setattr(bh, "_rf_tx_registry", fresh)
        return fresh

    def _msg(self, content):
        from gateway.rns_bridge import BridgedMessage
        return BridgedMessage(
            source_network="rns", source_id="abcdef01",
            destination_id=None, content=content,
        )

    def test_prefix_on_every_chunk(self, bridge, monkeypatch):
        self._fresh_registry(monkeypatch)
        sent = []
        with patch.object(bridge, 'send_to_meshtastic',
                          side_effect=lambda c, channel=0: sent.append(c) or True):
            bridge._process_rns_to_mesh(self._msg(_ML_REPLY))
        assert len(sent) >= 2
        assert all(c.startswith("[RNS:abcd] ") for c in sent)

    def test_suppressed_on_registry_hit(self, bridge, monkeypatch):
        reg = self._fresh_registry(monkeypatch)
        reg.register("dup content from mesh_bridge")
        bridge.config.rns.dual_path_dedup_enabled = True
        bridge.config.rns.dual_path_dedup_window_sec = 60
        with patch.object(bridge, 'send_to_meshtastic', return_value=True) as send:
            bridge._process_rns_to_mesh(self._msg("dup content from mesh_bridge"))
        send.assert_not_called()
        assert bridge.stats['rns_to_mesh_dual_path_suppressed'] == 1

    def test_no_hit_delivers_normally(self, bridge, monkeypatch):
        self._fresh_registry(monkeypatch)
        bridge.config.rns.dual_path_dedup_enabled = True
        bridge.config.rns.dual_path_dedup_window_sec = 60
        with patch.object(bridge, 'send_to_meshtastic', return_value=True) as send:
            bridge._process_rns_to_mesh(self._msg("rf missed this one"))
        send.assert_called()
        assert bridge.stats['rns_to_mesh_dual_path_suppressed'] == 0

    def test_flag_off_hit_still_delivers(self, bridge, monkeypatch):
        reg = self._fresh_registry(monkeypatch)
        reg.register("dup content")
        # bridge fixture config.rns is a MagicMock — strict is-True gate = OFF.
        with patch.object(bridge, 'send_to_meshtastic', return_value=True) as send:
            bridge._process_rns_to_mesh(self._msg("dup content"))
        send.assert_called()


class TestDispatchTimeDedupRecheck:
    """Dispatch-time dual-path dedup re-check (mirror of MeshForge 2d205b7).

    The enqueue-side check races the other TX path's registration; by
    dispatch time (past TX pacing) the registry is settled. Applied to all
    three dispatch callbacks: MQTTBridgeHandler.queue_send,
    MQTTBridgeHandler.publish_to_mqtt (MA's live R→M path), and
    MeshtasticHandler.queue_send. Flag-gated strict-True; DMs exempt.
    """

    def _fresh_registry(self, monkeypatch):
        import gateway.base_handler as bh
        fresh = bh.RecentRfTxRegistry()
        monkeypatch.setattr(bh, "_rf_tx_registry", fresh)
        return fresh

    def _mqtt_handler(self, dedup_on=True):
        import threading
        from types import SimpleNamespace
        from gateway.mqtt_bridge_handler import MQTTBridgeHandler
        h = MQTTBridgeHandler.__new__(MQTTBridgeHandler)
        h.config = SimpleNamespace(rns=SimpleNamespace(
            dual_path_dedup_enabled=dedup_on,
            dual_path_dedup_window_sec=60,
        ))
        h.stats = {}
        h._stats_lock = threading.Lock()
        h.send_text = MagicMock(return_value=True)
        h._connected = True
        h._client = MagicMock()
        return h

    def test_queue_send_suppresses_on_hit(self, monkeypatch):
        reg = self._fresh_registry(monkeypatch)
        h = self._mqtt_handler(dedup_on=True)
        reg.register("Tonight: rain.")
        assert h.queue_send({"message": "[RNS:abcd] Tonight: rain.",
                             "destination": None, "channel": 2}) is True
        h.send_text.assert_not_called()
        assert h.stats["dispatch_dedup_suppressed"] == 1

    def test_queue_send_flag_off_sends(self, monkeypatch):
        reg = self._fresh_registry(monkeypatch)
        h = self._mqtt_handler(dedup_on=False)
        reg.register("Tonight: rain.")
        assert h.queue_send({"message": "[RNS:abcd] Tonight: rain.",
                             "destination": None, "channel": 2}) is True
        h.send_text.assert_called_once()

    def test_queue_send_dm_never_suppressed(self, monkeypatch):
        reg = self._fresh_registry(monkeypatch)
        h = self._mqtt_handler(dedup_on=True)
        reg.register("private reply")
        assert h.queue_send({"message": "private reply",
                             "destination": "!b03bb70c", "channel": 2}) is True
        h.send_text.assert_called_once()

    def test_publish_to_mqtt_suppresses_on_hit(self, monkeypatch):
        """MA's live R→M dispatch path (destination='mqtt')."""
        reg = self._fresh_registry(monkeypatch)
        h = self._mqtt_handler(dedup_on=True)
        reg.register("race content")
        assert h.publish_to_mqtt({"message": "[ch0:p4] race content",
                                  "channel": 0}) is True
        h._client.publish.assert_not_called()
        assert h.stats["dispatch_dedup_suppressed"] == 1

    def test_publish_to_mqtt_flag_off_publishes(self, monkeypatch):
        reg = self._fresh_registry(monkeypatch)
        h = self._mqtt_handler(dedup_on=False)
        h._mqtt_lock = __import__("threading").Lock()
        h.config.mqtt_bridge = MagicMock(
            root_topic="msh", region="US", channel="meshanchor")
        h._client.publish.return_value = MagicMock(rc=0)
        reg.register("race content")
        assert h.publish_to_mqtt({"message": "[ch0:p4] race content",
                                  "channel": 0}) is True
        h._client.publish.assert_called_once()


class TestSeenOnRfRegistration:
    """RX-time registration (seen-on-RF, mirror of MeshForge b645fa7).

    A broadcast heard via MQTT is ON this radio's mesh whoever TX'd it —
    including another box's radio on the same RF segment, which this box's
    own TX bookkeeping can never see. Registering at RX lets the
    inject-side checks suppress the relay copy of the same content.
    """

    def _fresh_registry(self, monkeypatch):
        import gateway.base_handler as bh
        fresh = bh.RecentRfTxRegistry()
        monkeypatch.setattr(bh, "_rf_tx_registry", fresh)
        return fresh

    def _rx_handler(self):
        import threading
        from gateway.mqtt_bridge_handler import MQTTBridgeHandler
        h = MQTTBridgeHandler.__new__(MQTTBridgeHandler)
        h.config = MagicMock()
        h._message_queue = None
        h._should_bridge = None
        h._message_callback = None
        h.stats = {"errors": 0}
        h._stats_lock = threading.Lock()
        return h

    def _rx(self, handler, text, to=0xFFFFFFFF):
        handler._bridge_text_message(
            {"sender": "!ebfa1b11", "to": to,
             "payload": {"text": text}, "channel": 0},
            topic="msh/US/2/json/meshanchor/!ebfa1b11",
        )

    def test_broadcast_rx_registers(self, monkeypatch):
        reg = self._fresh_registry(monkeypatch)
        self._rx(self._rx_handler(), "plain user message")
        assert reg.seen_within("plain user message", 60.0)

    def test_tagged_rx_registers_normalized(self, monkeypatch):
        """A peer box's [Mesh:..]-tagged TX heard on RF registers
        normalized, so the relay copy of the same content matches."""
        reg = self._fresh_registry(monkeypatch)
        self._rx(self._rx_handler(), "[Mesh:LONG_FAST:2f10] Cmd")
        assert reg.seen_within("Cmd", 60.0)
        assert reg.seen_within("[RNS:abcd] Cmd", 60.0)

    def test_dm_rx_does_not_register(self, monkeypatch):
        reg = self._fresh_registry(monkeypatch)
        self._rx(self._rx_handler(), "private note", to=0x32962F10)
        assert not reg.seen_within("private note", 60.0)


# ---------------------------------------------------------------------------
# MF Issue #74 port: circuit breaker actually wired into the send paths
# ---------------------------------------------------------------------------

@pytest.mark.usefixtures("allow_rns_tx")
class TestCircuitBreakerWiringIssue74:
    """The breaker was write-only (worse than MeshForge's: with no
    bounded_rpc/trip_open port, NOTHING ever touched it after
    construction). These tests pin the gate and outcome recording in
    both send paths, plus the reset-on-reconnect. Port of MeshForge's
    TestCircuitBreakerWiringIssue74."""

    @staticmethod
    def _fake_rns_lxmf_modules():
        fake_rns = MagicMock(name="RNS")
        fake_rns.Transport.has_path.return_value = True
        fake_rns.Identity.recall.return_value = MagicMock(name="dest_identity")
        fake_rns.Destination.OUT = "OUT"
        fake_rns.Destination.SINGLE = "SINGLE"
        fake_rns.Destination.return_value = MagicMock(name="destination")
        fake_lxmf = MagicMock(name="LXMF")
        fake_lxmf.LXMessage.return_value = MagicMock(name="LXMessage_instance")
        return fake_rns, fake_lxmf

    def _prime(self, bridge):
        bridge._connected_rns = True
        bridge._lxmf_source = MagicMock(name="lxmf_source")
        bridge._lxmf_router = MagicMock(name="lxmf_router")

    def test_send_to_rns_blocked_when_circuit_open(self, bridge):
        """Open circuit -> send returns False WITHOUT touching any RNS RPC."""
        import sys
        fake_rns, fake_lxmf = self._fake_rns_lxmf_modules()
        self._prime(bridge)
        bridge._circuit_breaker.can_send.return_value = False
        with patch.dict(sys.modules, {"RNS": fake_rns, "LXMF": fake_lxmf}):
            result = bridge.send_to_rns("hello", b"\xab" * 16)
        assert result is False
        fake_rns.Transport.has_path.assert_not_called()
        fake_rns.Transport.request_path.assert_not_called()

    def test_send_to_rns_records_success(self, bridge):
        import sys
        fake_rns, fake_lxmf = self._fake_rns_lxmf_modules()
        self._prime(bridge)
        with patch.dict(sys.modules, {"RNS": fake_rns, "LXMF": fake_lxmf}):
            assert bridge.send_to_rns("hello", b"\xab" * 16) is True
        bridge._circuit_breaker.record_success.assert_called_once_with(
            (b"\xab" * 16).hex()[:8]
        )

    def test_send_to_rns_records_failure_on_exception(self, bridge):
        import sys
        fake_rns, fake_lxmf = self._fake_rns_lxmf_modules()
        fake_lxmf.LXMessage.side_effect = RuntimeError("ctor boom")
        self._prime(bridge)
        with patch.dict(sys.modules, {"RNS": fake_rns, "LXMF": fake_lxmf}):
            assert bridge.send_to_rns("hello", b"\xab" * 16) is False
        dest, err = bridge._circuit_breaker.record_failure.call_args[0]
        assert dest == (b"\xab" * 16).hex()[:8]
        assert "ctor boom" in err

    def test_send_to_rns_records_failure_on_no_path(self, bridge):
        import sys
        fake_rns, fake_lxmf = self._fake_rns_lxmf_modules()
        fake_rns.Transport.has_path.return_value = False
        self._prime(bridge)
        # Short-circuit the 50-iteration path-wait loop.
        bridge._stop_event.set()
        with patch.dict(sys.modules, {"RNS": fake_rns, "LXMF": fake_lxmf}):
            assert bridge.send_to_rns("hello", b"\xab" * 16) is False
        bridge._stop_event.clear()
        bridge._circuit_breaker.record_failure.assert_called_once_with(
            (b"\xab" * 16).hex()[:8], "no path"
        )

    def test_queue_send_rns_raises_retriable_when_circuit_open(self, bridge):
        """Queue path RAISES with a retriable-pattern message so
        RetryPolicy classifies it transient (backoff + retry after the
        recovery window) instead of the unknown-error one-shot retry a
        bare `return False` would get."""
        self._prime(bridge)
        bridge._circuit_breaker.can_send.return_value = False
        payload = {"message": "hi", "destination_hash": b"\xab" * 16}
        with pytest.raises(RuntimeError, match="temporarily unavailable"):
            bridge._queue_send_rns(payload)

    def test_queue_send_rns_records_success(self, bridge):
        import sys
        fake_rns, fake_lxmf = self._fake_rns_lxmf_modules()
        self._prime(bridge)
        payload = {"message": "hi", "destination_hash": b"\xab" * 16}
        with patch.dict(sys.modules, {"RNS": fake_rns, "LXMF": fake_lxmf}):
            assert bridge._queue_send_rns(payload) is True
        bridge._circuit_breaker.record_success.assert_called_once_with(
            (b"\xab" * 16).hex()[:8]
        )

    def test_queue_send_rns_records_failure_on_exception(self, bridge):
        import sys
        fake_rns, fake_lxmf = self._fake_rns_lxmf_modules()
        fake_lxmf.LXMessage.side_effect = RuntimeError("queue boom")
        self._prime(bridge)
        payload = {"message": "hi", "destination_hash": b"\xab" * 16}
        with patch.dict(sys.modules, {"RNS": fake_rns, "LXMF": fake_lxmf}):
            assert bridge._queue_send_rns(payload) is False
        dest, err = bridge._circuit_breaker.record_failure.call_args[0]
        assert dest == (b"\xab" * 16).hex()[:8]
        assert "queue boom" in err

    def test_reconnect_resets_circuit_breaker(self, bridge):
        """A fresh RNS transport invalidates stale per-destination OPEN
        state — reconnect success calls reset_all()."""
        bridge._running = True
        bridge._connected_rns = False
        bridge._rns_init_failed_permanently = False
        bridge._rns_reconnect.should_retry.return_value = True
        bridge._rns_reconnect.attempts = 0
        bridge._circuit_breaker.reset_all.return_value = 2

        def _fake_connect():
            bridge._connected_rns = True
        bridge._connect_rns = _fake_connect

        def _stop_loop():
            bridge._running = False
        bridge._maybe_start_lxmf_broadcast = _stop_loop

        bridge._rns_loop()
        bridge._circuit_breaker.reset_all.assert_called_once()


# ---------------------------------------------------------------------------
# MF Issue #74 port: durable CONFIRMED/DROPPED from LXMF callbacks (both paths)
# ---------------------------------------------------------------------------

@pytest.mark.usefixtures("allow_rns_tx")
class TestDeliveryCounterCallbacksIssue74:
    """Both send paths must pin LXMF delivery callbacks to a msg_id that
    records durable CONFIRMED (proof) / DROPPED (failure) — the queue
    path via the injected _queue_msg_id (Fork C syn/ack), so
    history_for(queue_id) joins QUEUED → SENT → CONFIRMED. Without the
    queue-path wiring the confirmation ring is biased (SENT-without-
    CONFIRMED for all queue traffic) and the stall check false-alarms."""

    @pytest.fixture(autouse=True)
    def _isolated_counters(self, tmp_path, monkeypatch):
        from gateway import delivery_counters as _dc
        monkeypatch.setenv(
            "MESHANCHOR_DELIVERY_COUNTERS_DB",
            str(tmp_path / "counters.db"),
        )
        _dc._reset_singleton_for_tests()
        yield
        _dc._reset_singleton_for_tests()

    @staticmethod
    def _fake_rns_lxmf():
        fake_rns = MagicMock(name="RNS")
        fake_rns.Transport.has_path.return_value = True
        fake_rns.Identity.recall.return_value = MagicMock(name="dest_identity")
        fake_rns.Destination.OUT = "OUT"
        fake_rns.Destination.SINGLE = "SINGLE"
        fake_rns.Destination.return_value = MagicMock(name="destination")
        fake_lxmf = MagicMock(name="LXMF")
        fake_lxm = MagicMock(name="LXMessage_instance")
        fake_lxmf.LXMessage.return_value = fake_lxm
        return fake_rns, fake_lxmf, fake_lxm

    def _prime(self, bridge):
        bridge._connected_rns = True
        bridge._lxmf_source = MagicMock(name="lxmf_source")
        bridge._lxmf_router = MagicMock(name="lxmf_router")
        bridge._maybe_emit_ack_for_msgid = MagicMock()

    def test_queue_send_pins_callbacks_to_queue_msg_id(self, bridge):
        """End-to-end: queue row id flows from the dispatched payload
        through callback registration to the CONFIRMED counter."""
        import sys
        from gateway import delivery_counters as _dc
        fake_rns, fake_lxmf, fake_lxm = self._fake_rns_lxmf()
        self._prime(bridge)
        queue_id = "1700000000000-abcd1234-0001"
        payload = {
            "message": "hi",
            "destination_hash": b"\xab" * 16,
            "_queue_msg_id": queue_id,
        }
        with patch.dict(sys.modules, {"RNS": fake_rns, "LXMF": fake_lxmf}):
            assert bridge._queue_send_rns(payload) is True
        assert fake_lxm.register_delivery_callback.called
        assert fake_lxm.register_failed_callback.called
        # Fire the delivery proof — CONFIRMED lands under the QUEUE id.
        delivered_cb = fake_lxm.register_delivery_callback.call_args[0][0]
        delivered_cb(MagicMock(name="receipt"))
        hist = [e.state for e in _dc.get_singleton().history_for(queue_id)]
        assert _dc.DeliveryState.CONFIRMED in hist

    def test_queue_send_failed_receipt_records_drop(self, bridge):
        import sys
        from gateway import delivery_counters as _dc
        fake_rns, fake_lxmf, fake_lxm = self._fake_rns_lxmf()
        self._prime(bridge)
        payload = {"message": "hi", "destination_hash": b"\xab" * 16,
                   "_queue_msg_id": "q-1"}
        with patch.dict(sys.modules, {"RNS": fake_rns, "LXMF": fake_lxmf}):
            bridge._queue_send_rns(payload)
        failed_cb = fake_lxm.register_failed_callback.call_args[0][0]
        receipt = MagicMock()
        receipt.failure_reason = "no_path"
        failed_cb(receipt)
        snap = _dc.get_singleton().snapshot()
        assert snap["drop_reasons"]["rns_delivery_failed"] == 1

    def test_direct_send_records_confirmed(self, bridge):
        import sys
        from gateway import delivery_counters as _dc
        fake_rns, fake_lxmf, fake_lxm = self._fake_rns_lxmf()
        self._prime(bridge)
        with patch.dict(sys.modules, {"RNS": fake_rns, "LXMF": fake_lxmf}):
            assert bridge.send_to_rns("hello", b"\xab" * 16) is True
        delivered_cb = fake_lxm.register_delivery_callback.call_args[0][0]
        delivered_cb(MagicMock(name="receipt"))
        snap = _dc.get_singleton().snapshot()
        assert snap["state_totals"]["confirmed"] == 1


class TestRetentionPinsWired20260803:
    """The bridge must actually ARM node retention.

    node_tracker keeps TTL eviction inert until set_retention_pins() is
    called, so this wiring is what makes the population cap live. Verified on
    the constructed object rather than by reading rns_bridge.py — a registered
    call is not a running call (calibrated_claims #7).
    """

    def _build(self, rns_overrides):
        from gateway.config import GatewayConfig
        cfg = GatewayConfig()
        for k, v in rns_overrides.items():
            setattr(cfg.rns, k, v)
        with patch("gateway.rns_bridge.UnifiedNodeTracker") as MockTracker, \
             patch("gateway.rns_bridge.BridgeHealthMonitor"), \
             patch("gateway.rns_bridge.DeliveryTracker"), \
             patch("gateway.rns_bridge.ReconnectStrategy"):
            from gateway.rns_bridge import RNSMeshtasticBridge
            RNSMeshtasticBridge(config=cfg)
            return MockTracker.return_value.set_retention_pins

    def test_pins_are_armed_on_construction(self):
        call = self._build({"propagation_node": "3968A2EEAC25E2E7A7961F25842D3D85"})
        assert call.called, "tracker retention never armed — eviction stays inert forever"
        pins = set(call.call_args[0][0])
        assert "3968a2eeac25e2e7a7961f25842d3d85" in pins, pins

    def test_lxmf_destinations_are_pinned(self):
        call = self._build({
            "propagation_node": "",
            "default_lxmf_destination": ["bb" * 16, "cc" * 16],
        })
        pins = set(call.call_args[0][0])
        assert {"bb" * 16, "cc" * 16} <= pins, pins

    def test_armed_even_when_nothing_is_configured(self):
        """An EMPTY pin list still arms eviction — 'no pins' is a result, not
        a reason to leave the cap switched off."""
        call = self._build({"propagation_node": "",
                            "default_lxmf_destination": ""})
        assert call.called
        assert list(call.call_args[0][0]) == []
