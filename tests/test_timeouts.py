"""
Tests for utils.timeouts — centralized timeout constants.

Verifies:
1. All constants are positive numbers
2. Backward-compatible re-exports from defaults.py match
3. No circular imports
4. Module-level constants in consumer files match canonical source
"""

import os
import sys

import pytest

# Ensure src directory is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))


class TestTimeoutConstants:
    """Verify all timeout constants are valid positive numbers."""

    def test_subprocess_timeouts_positive(self):
        from utils.timeouts import (
            SUBPROCESS_QUICK,
            SUBPROCESS_MEDIUM,
            SUBPROCESS_DEFAULT,
            SUBPROCESS_LONG,
            SUBPROCESS_INSTALL,
        )
        for name, val in [
            ("SUBPROCESS_QUICK", SUBPROCESS_QUICK),
            ("SUBPROCESS_MEDIUM", SUBPROCESS_MEDIUM),
            ("SUBPROCESS_DEFAULT", SUBPROCESS_DEFAULT),
            ("SUBPROCESS_LONG", SUBPROCESS_LONG),
            ("SUBPROCESS_INSTALL", SUBPROCESS_INSTALL),
        ]:
            assert isinstance(val, (int, float)), f"{name} should be numeric"
            assert val > 0, f"{name} should be positive"

    def test_subprocess_ordering(self):
        from utils.timeouts import (
            SUBPROCESS_QUICK,
            SUBPROCESS_MEDIUM,
            SUBPROCESS_DEFAULT,
            SUBPROCESS_LONG,
            SUBPROCESS_INSTALL,
        )
        assert SUBPROCESS_QUICK < SUBPROCESS_MEDIUM
        assert SUBPROCESS_MEDIUM < SUBPROCESS_DEFAULT
        assert SUBPROCESS_DEFAULT < SUBPROCESS_LONG
        assert SUBPROCESS_LONG < SUBPROCESS_INSTALL

    def test_http_timeouts_positive(self):
        from utils.timeouts import (
            TCP_CONNECT,
            HTTP_CONNECT,
            HTTP_READ,
            HTTP_PROTOBUF_TX,
            HTTP_PROTOBUF_SESSION,
        )
        for name, val in [
            ("TCP_CONNECT", TCP_CONNECT),
            ("HTTP_CONNECT", HTTP_CONNECT),
            ("HTTP_READ", HTTP_READ),
            ("HTTP_PROTOBUF_TX", HTTP_PROTOBUF_TX),
            ("HTTP_PROTOBUF_SESSION", HTTP_PROTOBUF_SESSION),
        ]:
            assert isinstance(val, (int, float)), f"{name} should be numeric"
            assert val > 0, f"{name} should be positive"

    def test_mqtt_timeouts_positive(self):
        from utils.timeouts import (
            MQTT_CONNECT,
            MQTT_RECONNECT_INITIAL,
            MQTT_RECONNECT_MAX,
            MQTT_LOCAL_RECONNECT_INITIAL,
            MQTT_LOCAL_RECONNECT_MAX,
        )
        for name, val in [
            ("MQTT_CONNECT", MQTT_CONNECT),
            ("MQTT_RECONNECT_INITIAL", MQTT_RECONNECT_INITIAL),
            ("MQTT_RECONNECT_MAX", MQTT_RECONNECT_MAX),
            ("MQTT_LOCAL_RECONNECT_INITIAL", MQTT_LOCAL_RECONNECT_INITIAL),
            ("MQTT_LOCAL_RECONNECT_MAX", MQTT_LOCAL_RECONNECT_MAX),
        ]:
            assert isinstance(val, (int, float)), f"{name} should be numeric"
            assert val > 0, f"{name} should be positive"

    def test_mqtt_reconnect_ordering(self):
        from utils.timeouts import (
            MQTT_RECONNECT_INITIAL,
            MQTT_RECONNECT_MAX,
            MQTT_LOCAL_RECONNECT_INITIAL,
            MQTT_LOCAL_RECONNECT_MAX,
        )
        assert MQTT_RECONNECT_INITIAL < MQTT_RECONNECT_MAX
        assert MQTT_LOCAL_RECONNECT_INITIAL < MQTT_LOCAL_RECONNECT_MAX

    def test_socket_timeouts_positive(self):
        from utils.timeouts import SOCKET_CONNECT, GPSD_CONNECT, CONNECTIVITY_CHECK
        for name, val in [
            ("SOCKET_CONNECT", SOCKET_CONNECT),
            ("GPSD_CONNECT", GPSD_CONNECT),
            ("CONNECTIVITY_CHECK", CONNECTIVITY_CHECK),
        ]:
            assert isinstance(val, (int, float)), f"{name} should be numeric"
            assert val > 0, f"{name} should be positive"

    def test_service_timeouts_positive(self):
        from utils.timeouts import SERVICE_CHECK, SERVICE_RESTART_WAIT
        assert SERVICE_CHECK > 0
        assert SERVICE_RESTART_WAIT > 0

    def test_database_timeouts_positive(self):
        from utils.timeouts import SQLITE_BUSY
        assert SQLITE_BUSY > 0

    def test_delivery_timeouts_positive(self):
        from utils.timeouts import DELIVERY_CONFIRMATION, MESSAGE_STALE, TACTICAL_CHUNK
        assert DELIVERY_CONFIRMATION > 0
        assert MESSAGE_STALE > 0
        assert TACTICAL_CHUNK > 0

    def test_circuit_breaker_timeout_positive(self):
        from utils.timeouts import CIRCUIT_RECOVERY
        assert CIRCUIT_RECOVERY > 0

    def test_thread_timeouts_positive(self):
        from utils.timeouts import THREAD_JOIN, THREAD_JOIN_LONG
        assert THREAD_JOIN > 0
        assert THREAD_JOIN_LONG > 0
        assert THREAD_JOIN < THREAD_JOIN_LONG

    def test_node_status_thresholds_positive(self):
        from utils.timeouts import NODE_ONLINE, NODE_STALE
        assert NODE_ONLINE > 0
        assert NODE_STALE > 0
        assert NODE_ONLINE < NODE_STALE

    def test_external_api_timeouts_positive(self):
        from utils.timeouts import EXTERNAL_API, DX_TELNET, AGENT_HEARTBEAT
        assert EXTERNAL_API > 0
        assert DX_TELNET > 0
        assert AGENT_HEARTBEAT > 0


class TestBackwardCompatibility:
    """Verify re-exports from defaults.py match canonical values."""

    def test_tcp_connect_reexport(self):
        from utils.timeouts import TCP_CONNECT
        from utils.defaults import TCP_CONNECT_TIMEOUT_SEC
        assert TCP_CONNECT_TIMEOUT_SEC == TCP_CONNECT

    def test_subprocess_default_reexport(self):
        from utils.timeouts import SUBPROCESS_DEFAULT
        from utils.defaults import SUBPROCESS_DEFAULT_TIMEOUT_SEC
        assert SUBPROCESS_DEFAULT_TIMEOUT_SEC == SUBPROCESS_DEFAULT

    def test_mqtt_connect_reexport(self):
        from utils.timeouts import MQTT_CONNECT
        from utils.defaults import MQTT_CONNECT_TIMEOUT_SEC
        assert MQTT_CONNECT_TIMEOUT_SEC == MQTT_CONNECT


class TestConsumerMigration:
    """Verify consumer modules use values from canonical source."""

    def test_gps_integration_uses_canonical(self):
        from utils.timeouts import GPSD_CONNECT
        from utils.gps_integration import GPSD_TIMEOUT
        assert GPSD_TIMEOUT == GPSD_CONNECT

    def test_offline_sync_uses_canonical(self):
        from utils.timeouts import CONNECTIVITY_CHECK
        from utils.offline_sync import CONNECTIVITY_TIMEOUT
        assert CONNECTIVITY_TIMEOUT == CONNECTIVITY_CHECK

    def test_node_inventory_uses_canonical(self):
        from utils.timeouts import NODE_ONLINE, NODE_STALE
        from utils.node_inventory import ONLINE_TIMEOUT_SEC, STALE_TIMEOUT_SEC
        assert ONLINE_TIMEOUT_SEC == NODE_ONLINE
        assert STALE_TIMEOUT_SEC == NODE_STALE

    def test_circuit_breaker_uses_canonical(self):
        from utils.timeouts import CIRCUIT_RECOVERY
        from gateway.circuit_breaker import CircuitBreakerRegistry
        assert CircuitBreakerRegistry.DEFAULT_RECOVERY_TIMEOUT == CIRCUIT_RECOVERY


class TestNoCircularImports:
    """Verify timeouts module doesn't create circular import chains."""

    def test_import_timeouts_standalone(self):
        """timeouts module should import without any other MeshAnchor module."""
        import importlib
        # Force fresh import
        mod = importlib.import_module('utils.timeouts')
        assert hasattr(mod, 'SUBPROCESS_DEFAULT')

    def test_import_defaults_with_timeouts(self):
        """defaults.py should import cleanly with timeouts re-exports."""
        import importlib
        mod = importlib.import_module('utils.defaults')
        assert hasattr(mod, 'TCP_CONNECT_TIMEOUT_SEC')
