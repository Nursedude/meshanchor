"""
Tests for RNS-Meshtastic bridge service.

Run: python3 -m pytest tests/test_rns_bridge.py -v
"""

import pytest
import threading
import time
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock, PropertyMock
from queue import Queue

from src.gateway.rns_bridge import (
    BridgedMessage,
    RNSMeshtasticBridge,
)
from src.gateway.config import GatewayConfig


class TestBridgedMessage:
    """Tests for BridgedMessage dataclass."""

    def test_defaults(self):
        """Test default message values."""
        msg = BridgedMessage(
            source_network="meshtastic",
            source_id="!abcd1234",
            destination_id=None,
            content="Hello"
        )

        assert msg.source_network == "meshtastic"
        assert msg.source_id == "!abcd1234"
        assert msg.content == "Hello"
        assert msg.title is None
        assert msg.is_broadcast is False
        assert msg.timestamp is not None
        assert msg.metadata == {}

    def test_with_all_fields(self):
        """Test message with all fields."""
        ts = datetime(2026, 1, 9, 12, 0, 0)
        msg = BridgedMessage(
            source_network="rns",
            source_id="abc123",
            destination_id="def456",
            content="Test message",
            title="Test Title",
            timestamp=ts,
            is_broadcast=True,
            metadata={"priority": "high"}
        )

        assert msg.source_network == "rns"
        assert msg.title == "Test Title"
        assert msg.timestamp == ts
        assert msg.is_broadcast is True
        assert msg.metadata == {"priority": "high"}

    def test_auto_timestamp(self):
        """Test automatic timestamp on creation."""
        before = datetime.now()
        msg = BridgedMessage(
            source_network="meshtastic",
            source_id="test",
            destination_id=None,
            content="Test"
        )
        after = datetime.now()

        assert before <= msg.timestamp <= after

    def test_auto_metadata(self):
        """Test automatic empty metadata dict."""
        msg = BridgedMessage(
            source_network="meshtastic",
            source_id="test",
            destination_id=None,
            content="Test"
        )

        # Should be a new dict, not None
        assert msg.metadata is not None
        assert isinstance(msg.metadata, dict)


class TestRNSMeshtasticBridge:
    """Tests for RNSMeshtasticBridge class."""

    @pytest.fixture
    def mock_config(self):
        """Create a mock config for testing."""
        config = GatewayConfig()
        config.enabled = True
        return config

    @pytest.fixture
    def bridge(self, mock_config):
        """Create a bridge instance with mocked dependencies."""
        with patch('src.gateway.rns_bridge.UnifiedNodeTracker'):
            with patch.object(GatewayConfig, 'load', return_value=mock_config):
                bridge = RNSMeshtasticBridge(config=mock_config)
                yield bridge
                # Cleanup
                if bridge._running:
                    bridge._running = False

    def test_init(self, bridge):
        """Test bridge initialization."""
        assert bridge._running is False
        assert bridge._connected_rns is False
        assert bridge.stats['messages_mesh_to_rns'] == 0
        assert bridge.stats['messages_rns_to_mesh'] == 0

    def test_is_running_property(self, bridge):
        """Test is_running property."""
        assert bridge.is_running is False

        bridge._running = True
        assert bridge.is_running is True

    def test_is_connected_property(self, bridge):
        """Test is_connected property."""
        assert bridge.is_connected is False

        # Meshtastic handler connected
        if bridge._mesh_handler:
            bridge._mesh_handler._connected = True
            assert bridge.is_connected is True
            bridge._mesh_handler._connected = False

        # RNS connected
        bridge._connected_rns = True
        assert bridge.is_connected is True

    def test_get_status(self, bridge):
        """Test get_status returns correct structure."""
        status = bridge.get_status()

        assert 'running' in status
        assert 'enabled' in status
        assert 'meshtastic_connected' in status
        assert 'rns_connected' in status
        assert 'statistics' in status
        assert 'node_stats' in status

    def test_get_status_with_uptime(self, bridge):
        """Test get_status calculates uptime."""
        bridge.stats['start_time'] = datetime.now() - timedelta(seconds=60)

        status = bridge.get_status()

        assert status['uptime_seconds'] is not None
        assert status['uptime_seconds'] >= 60

    def test_register_message_callback(self, bridge):
        """Test registering message callbacks."""
        callback = MagicMock()

        bridge.register_message_callback(callback)

        assert callback in bridge._message_callbacks

    def test_register_status_callback(self, bridge):
        """Test registering status callbacks."""
        callback = MagicMock()

        bridge.register_status_callback(callback)

        assert callback in bridge._status_callbacks

    def test_send_to_meshtastic_no_handler(self, bridge):
        """Test send fails when handler not initialized."""
        bridge._mesh_handler = None

        result = bridge.send_to_meshtastic("Test message")

        assert result is False

    def test_send_to_rns_not_connected(self, bridge):
        """Test send fails when not connected."""
        bridge._connected_rns = False

        result = bridge.send_to_rns("Test message")

        assert result is False

    def test_test_connection_structure(self, bridge):
        """Test test_connection returns correct structure."""
        with patch.object(bridge, '_test_rns', return_value=False):
            result = bridge.test_connection()

        assert 'meshtastic' in result
        assert 'rns' in result
        assert 'connected' in result['meshtastic']
        assert 'error' in result['meshtastic']

    def test_stop_when_not_running(self, bridge):
        """Test stop does nothing when not running."""
        bridge._running = False

        # Should not raise
        bridge.stop()

    def test_message_queues_initialized(self, bridge):
        """Test message queues are initialized."""
        assert isinstance(bridge._mesh_to_rns_queue, Queue)
        assert isinstance(bridge._rns_to_mesh_queue, Queue)

    def test_stats_initialization(self, bridge):
        """Test statistics are properly initialized."""
        assert bridge.stats['messages_mesh_to_rns'] == 0
        assert bridge.stats['messages_rns_to_mesh'] == 0
        assert bridge.stats['errors'] == 0
        assert bridge.stats['start_time'] is None


class TestBridgeStartStop:
    """Tests for bridge start/stop lifecycle."""

    def test_start_sets_running(self):
        """Test start sets running flag."""
        with patch('src.gateway.rns_bridge.UnifiedNodeTracker') as mock_tracker:
            mock_tracker_instance = MagicMock()
            mock_tracker.return_value = mock_tracker_instance

            config = GatewayConfig()
            config.enabled = False  # Disable to avoid thread spawning

            bridge = RNSMeshtasticBridge(config=config)
            result = bridge.start()

            assert result is True
            assert bridge._running is True
            assert bridge.stats['start_time'] is not None
            mock_tracker_instance.start.assert_called_once()

            bridge._running = False  # Cleanup

    def test_start_when_already_running(self):
        """Test start returns True if already running."""
        with patch('src.gateway.rns_bridge.UnifiedNodeTracker'):
            config = GatewayConfig()
            config.enabled = False

            bridge = RNSMeshtasticBridge(config=config)
            bridge._running = True

            result = bridge.start()

            assert result is True

    def test_stop_clears_running(self):
        """Test stop clears running flag."""
        with patch('src.gateway.rns_bridge.UnifiedNodeTracker') as mock_tracker:
            mock_tracker_instance = MagicMock()
            mock_tracker.return_value = mock_tracker_instance

            config = GatewayConfig()
            config.enabled = False

            bridge = RNSMeshtasticBridge(config=config)
            bridge._running = True

            bridge.stop()

            assert bridge._running is False
            mock_tracker_instance.stop.assert_called_once()


class TestBridgeCallbacks:
    """Tests for callback notification."""

    def test_notify_status_calls_callbacks(self):
        """Test _notify_status calls all registered callbacks."""
        with patch('src.gateway.rns_bridge.UnifiedNodeTracker'):
            config = GatewayConfig()
            bridge = RNSMeshtasticBridge(config=config)

            callback1 = MagicMock()
            callback2 = MagicMock()
            bridge.register_status_callback(callback1)
            bridge.register_status_callback(callback2)

            bridge._notify_status("test_event")

            # Callbacks receive event and status dict
            assert callback1.call_count == 1
            assert callback2.call_count == 1
            assert callback1.call_args[0][0] == "test_event"
            assert callback2.call_args[0][0] == "test_event"

    def test_notify_message_calls_callbacks(self):
        """Test _notify_message calls all registered callbacks."""
        with patch('src.gateway.rns_bridge.UnifiedNodeTracker'):
            config = GatewayConfig()
            bridge = RNSMeshtasticBridge(config=config)

            callback = MagicMock()
            bridge.register_message_callback(callback)

            msg = BridgedMessage(
                source_network="meshtastic",
                source_id="test",
                destination_id=None,
                content="Hello"
            )
            bridge._notify_message(msg)

            callback.assert_called_once_with(msg)

    def test_callback_error_handling(self):
        """Test callbacks don't break on error."""
        with patch('src.gateway.rns_bridge.UnifiedNodeTracker'):
            config = GatewayConfig()
            bridge = RNSMeshtasticBridge(config=config)

            bad_callback = MagicMock(side_effect=Exception("Callback error"))
            good_callback = MagicMock()

            bridge.register_status_callback(bad_callback)
            bridge.register_status_callback(good_callback)

            # Should not raise
            bridge._notify_status("test")

            # Good callback should still be called
            good_callback.assert_called_once()


class TestRNSConnectionFlow:
    """Tests for RNS connection and LXMF setup flow.

    Validates the fix for the gateway bridge failing to connect to RNS
    when rnsd is running (previously gave up permanently instead of
    connecting as a shared instance client).
    """

    @pytest.fixture
    def bridge(self):
        """Create a bridge with mocked dependencies."""
        with patch('src.gateway.rns_bridge.UnifiedNodeTracker'):
            config = GatewayConfig()
            config.enabled = True
            bridge = RNSMeshtasticBridge(config=config)
            yield bridge
            if bridge._running:
                bridge._running = False

    def test_connect_rns_with_pre_initialized(self, bridge):
        """When RNS is pre-initialized, _connect_rns should set up LXMF."""
        bridge._rns_pre_initialized = True

        mock_rns = MagicMock()
        mock_lxmf = MagicMock()
        mock_identity = MagicMock()
        mock_rns.Identity.return_value = mock_identity
        mock_rns.Identity.from_file.return_value = mock_identity

        mock_router = MagicMock()
        mock_lxmf.LXMRouter.return_value = mock_router
        mock_source = MagicMock()
        mock_router.register_delivery_identity.return_value = mock_source

        with patch.dict('sys.modules', {'RNS': mock_rns, 'LXMF': mock_lxmf}):
            bridge._connect_rns()

        assert bridge._connected_rns is True
        assert bridge._lxmf_router is mock_router
        assert bridge._lxmf_source is mock_source

    def test_connect_rns_import_error_is_permanent(self, bridge):
        """When RNS/LXMF not installed, failure is permanent."""
        bridge._rns_pre_initialized = False

        with patch('builtins.__import__', side_effect=ImportError("No module")):
            bridge._connect_rns()

        assert bridge._connected_rns is False
        assert bridge._rns_init_failed_permanently is True

    def test_connect_rns_rnsd_detected_tries_client(self, bridge):
        """When rnsd is detected and RNS not pre-initialized, should try client connection."""
        bridge._rns_pre_initialized = False

        mock_rns = MagicMock()
        mock_lxmf = MagicMock()
        mock_identity = MagicMock()
        mock_rns.Identity.return_value = mock_identity
        mock_rns.Identity.from_file.return_value = mock_identity

        mock_router = MagicMock()
        mock_lxmf.LXMRouter.return_value = mock_router
        mock_source = MagicMock()
        mock_router.register_delivery_identity.return_value = mock_source

        with patch.dict('sys.modules', {'RNS': mock_rns, 'LXMF': mock_lxmf}):
            with patch('utils.gateway_diagnostic.find_rns_processes', return_value=[12345]):
                bridge._connect_rns()

        # Should connect as client, NOT give up
        assert bridge._connected_rns is True
        assert bridge._rns_via_rnsd is True
        assert bridge._rns_init_failed_permanently is False
        mock_rns.Reticulum.assert_called_once()

    def test_connect_rns_no_permanent_failure_on_rnsd_port_conflict(self, bridge):
        """Port conflict with rnsd running should NOT be permanent failure."""
        bridge._rns_pre_initialized = False

        mock_rns = MagicMock()
        mock_lxmf = MagicMock()
        port_error = OSError("Address already in use")
        port_error.errno = 98
        mock_rns.Reticulum.side_effect = port_error

        with patch.dict('sys.modules', {'RNS': mock_rns, 'LXMF': mock_lxmf}):
            with patch('utils.gateway_diagnostic.find_rns_processes', return_value=[12345]):
                with patch('utils.gateway_diagnostic.handle_address_in_use_error',
                          return_value={'rns_pids': [12345]}):
                    bridge._connect_rns()

        # Should be retriable, not permanent
        assert bridge._rns_init_failed_permanently is False
        assert bridge._rns_via_rnsd is True

    def test_connect_rns_already_running_proceeds_to_lxmf(self, bridge):
        """'Already running' during Reticulum init should proceed to LXMF setup."""
        bridge._rns_pre_initialized = False

        mock_rns = MagicMock()
        mock_lxmf = MagicMock()
        mock_rns.Reticulum.side_effect = Exception("Cannot reinitialise Reticulum")

        mock_identity = MagicMock()
        mock_rns.Identity.return_value = mock_identity
        mock_rns.Identity.from_file.return_value = mock_identity

        mock_router = MagicMock()
        mock_lxmf.LXMRouter.return_value = mock_router
        mock_source = MagicMock()
        mock_router.register_delivery_identity.return_value = mock_source

        with patch.dict('sys.modules', {'RNS': mock_rns, 'LXMF': mock_lxmf}):
            with patch('utils.gateway_diagnostic.find_rns_processes', return_value=[]):
                bridge._connect_rns()

        # Should proceed to LXMF setup since RNS singleton is active
        assert bridge._connected_rns is True
        assert bridge._rns_init_failed_permanently is False

    def test_setup_lxmf_creates_identity_and_router(self, bridge):
        """_setup_lxmf should create identity, router, and set connected."""
        mock_rns = MagicMock()
        mock_lxmf = MagicMock()

        mock_identity = MagicMock()
        mock_rns.Identity.return_value = mock_identity

        mock_router = MagicMock()
        mock_lxmf.LXMRouter.return_value = mock_router
        mock_source = MagicMock()
        mock_router.register_delivery_identity.return_value = mock_source

        bridge._setup_lxmf(mock_rns, mock_lxmf)

        assert bridge._connected_rns is True
        assert bridge._identity is mock_identity
        assert bridge._lxmf_router is mock_router
        assert bridge._lxmf_source is mock_source
        mock_router.register_delivery_callback.assert_called_once()
        mock_router.announce.assert_called_once()

    def test_rns_loop_logs_permanent_failure(self, bridge):
        """_rns_loop should log when permanent failure prevents retries."""
        bridge._running = True
        bridge._rns_init_failed_permanently = True

        # Run one iteration then stop
        def stop_after_wait(timeout):
            bridge._running = False
            return True  # Simulate event set

        bridge._stop_event = MagicMock()
        bridge._stop_event.wait = stop_after_wait

        with patch('src.gateway.rns_bridge.logger') as mock_logger:
            bridge._rns_loop()

        # Should have logged the permanent failure warning
        mock_logger.warning.assert_any_call(
            "RNS initialization failed permanently - "
            "bridge will not attempt reconnection. "
            "Check RNS/LXMF installation and logs above."
        )


class TestHeadlessFunctions:
    """Tests for module-level headless gateway functions."""

    def test_get_gateway_stats_no_bridge(self):
        """get_gateway_stats returns defaults when no bridge active."""
        import src.gateway.rns_bridge as bridge_mod
        original = bridge_mod._active_bridge
        try:
            bridge_mod._active_bridge = None
            stats = bridge_mod.get_gateway_stats()
            assert stats['running'] is False
            assert stats['meshtastic_connected'] is False
            assert stats['rns_connected'] is False
        finally:
            bridge_mod._active_bridge = original

    def test_get_gateway_stats_uses_mesh_handler(self):
        """get_gateway_stats uses _mesh_handler.is_connected, not _connected_mesh."""
        import src.gateway.rns_bridge as bridge_mod
        original = bridge_mod._active_bridge

        mock_bridge = MagicMock()
        mock_bridge._running = True
        mock_bridge._connected_rns = False
        mock_handler = MagicMock()
        mock_handler.is_connected = True
        mock_bridge._mesh_handler = mock_handler
        mock_bridge.get_status.return_value = {
            'statistics': {},
            'uptime_seconds': 10,
        }
        mock_bridge.health.get_summary.return_value = {}
        mock_bridge.delivery_tracker.get_stats.return_value = {}

        try:
            bridge_mod._active_bridge = mock_bridge
            stats = bridge_mod.get_gateway_stats()
            assert stats['meshtastic_connected'] is True
        finally:
            bridge_mod._active_bridge = original

    def test_start_gateway_headless_uses_mesh_handler(self):
        """start_gateway_headless uses _mesh_handler.is_connected."""
        import src.gateway.rns_bridge as bridge_mod
        original = bridge_mod._active_bridge

        with patch('src.gateway.rns_bridge.UnifiedNodeTracker'):
            with patch('src.gateway.rns_bridge.RNSMeshtasticBridge') as MockBridge:
                mock_instance = MagicMock()
                mock_instance._running = False
                mock_instance.start.return_value = True
                mock_handler = MagicMock()
                mock_handler.is_connected = False
                mock_instance._mesh_handler = mock_handler
                mock_instance._connected_rns = False
                MockBridge.return_value = mock_instance

                try:
                    bridge_mod._active_bridge = None
                    result = bridge_mod.start_gateway_headless()
                    assert result is True
                finally:
                    bridge_mod._active_bridge = original
