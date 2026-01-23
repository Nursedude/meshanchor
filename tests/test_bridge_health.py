"""Tests for BridgeHealthMonitor and error classification."""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import time
import threading
from unittest.mock import patch

import pytest

from gateway.bridge_health import (
    BridgeHealthMonitor,
    classify_error,
    ConnectionEvent,
    ErrorEvent,
)


class TestClassifyError:
    """Test error classification logic."""

    def test_transient_connection_reset(self):
        assert classify_error(ConnectionResetError("Connection reset by peer")) == "transient"

    def test_transient_broken_pipe(self):
        assert classify_error(BrokenPipeError("Broken pipe")) == "transient"

    def test_transient_timeout(self):
        assert classify_error(TimeoutError("Connection timed out")) == "transient"

    def test_transient_connection_refused(self):
        assert classify_error(ConnectionRefusedError("Connection refused")) == "transient"

    def test_transient_os_error(self):
        assert classify_error(OSError("Network unreachable")) == "transient"

    def test_permanent_signal_error(self):
        err = RuntimeError("signal only works in main thread")
        assert classify_error(err) == "permanent"

    def test_permanent_reinitialise(self):
        err = Exception("Cannot reinitialise RNS")
        assert classify_error(err) == "permanent"

    def test_permanent_permission_denied(self):
        err = PermissionError("Permission denied")
        assert classify_error(err) == "permanent"

    def test_unknown_generic_error(self):
        err = ValueError("Something unexpected")
        assert classify_error(err) == "unknown"

    def test_transient_address_in_use(self):
        err = OSError("Address already in use")
        assert classify_error(err) == "transient"


class TestBridgeHealthMonitor:
    """Test health monitoring and metrics."""

    def test_initial_state(self):
        """Monitor starts with no connections and zero counts."""
        health = BridgeHealthMonitor()
        summary = health.get_summary()

        assert summary["connections"]["meshtastic"]["connected"] is False
        assert summary["connections"]["rns"]["connected"] is False
        assert summary["messages"]["mesh_to_rns"] == 0
        assert summary["messages"]["rns_to_mesh"] == 0

    def test_connection_event_connected(self):
        """Records connection and updates state."""
        health = BridgeHealthMonitor()
        health.record_connection_event("meshtastic", "connected")

        summary = health.get_summary()
        assert summary["connections"]["meshtastic"]["connected"] is True
        assert summary["connections"]["meshtastic"]["reconnect_count"] == 1

    def test_connection_event_disconnected(self):
        """Records disconnection and tracks uptime."""
        health = BridgeHealthMonitor()
        health.record_connection_event("meshtastic", "connected")
        time.sleep(0.1)
        health.record_connection_event("meshtastic", "disconnected", "test")

        summary = health.get_summary()
        assert summary["connections"]["meshtastic"]["connected"] is False
        assert summary["connections"]["meshtastic"]["last_disconnected"] is not None

    def test_multiple_reconnections(self):
        """Tracks reconnection count."""
        health = BridgeHealthMonitor()

        for _ in range(3):
            health.record_connection_event("rns", "connected")
            health.record_connection_event("rns", "disconnected")

        summary = health.get_summary()
        assert summary["connections"]["rns"]["reconnect_count"] == 3

    def test_message_sent_counting(self):
        """Counts sent messages by direction."""
        health = BridgeHealthMonitor()

        health.record_message_sent("mesh_to_rns")
        health.record_message_sent("mesh_to_rns")
        health.record_message_sent("rns_to_mesh")

        summary = health.get_summary()
        assert summary["messages"]["mesh_to_rns"] == 2
        assert summary["messages"]["rns_to_mesh"] == 1

    def test_message_failed_counting(self):
        """Counts failed messages and requeue status."""
        health = BridgeHealthMonitor()

        health.record_message_failed("mesh_to_rns", requeued=True)
        health.record_message_failed("mesh_to_rns", requeued=False)
        health.record_message_failed("rns_to_mesh", requeued=True)

        summary = health.get_summary()
        assert summary["messages"]["failed_mesh_to_rns"] == 2
        assert summary["messages"]["failed_rns_to_mesh"] == 1
        assert summary["messages"]["requeued"] == 2

    def test_record_error_classification(self):
        """Records and classifies errors."""
        health = BridgeHealthMonitor()

        cat1 = health.record_error("meshtastic", ConnectionResetError("reset"))
        cat2 = health.record_error("rns", RuntimeError("signal only works in main thread"))

        assert cat1 == "transient"
        assert cat2 == "permanent"

    def test_error_rate_windowed(self):
        """Error rate only counts recent errors."""
        health = BridgeHealthMonitor()

        health.record_error("meshtastic", OSError("timeout"))
        health.record_error("meshtastic", OSError("reset"))

        errors = health.get_error_rate(window_seconds=60)
        assert errors["transient"] == 2
        assert errors["permanent"] == 0

    def test_message_rate_calculation(self):
        """Message rate calculated over window."""
        health = BridgeHealthMonitor()

        for _ in range(10):
            health.record_message_sent("mesh_to_rns")

        rate = health.get_message_rate(window_seconds=60)
        assert rate == 10.0  # 10 messages in 60s = 10/min

    def test_uptime_percent_connected(self):
        """Uptime includes current connected time."""
        health = BridgeHealthMonitor()
        health.record_connection_event("meshtastic", "connected")
        time.sleep(0.1)

        uptime = health.get_uptime_percent("meshtastic")
        assert uptime > 0  # Should be positive

    def test_uptime_percent_never_connected(self):
        """Uptime is 0% for never-connected service."""
        health = BridgeHealthMonitor()
        time.sleep(0.05)

        uptime = health.get_uptime_percent("rns")
        assert uptime == 0.0

    def test_is_healthy_when_connected(self):
        """Bridge is healthy with active connection and low errors."""
        health = BridgeHealthMonitor()
        health.record_connection_event("meshtastic", "connected")

        assert health.is_healthy() is True

    def test_is_healthy_when_disconnected(self):
        """Bridge is unhealthy when nothing connected."""
        health = BridgeHealthMonitor()
        assert health.is_healthy() is False

    def test_is_healthy_high_error_rate(self):
        """Bridge is unhealthy with excessive errors."""
        health = BridgeHealthMonitor()
        health.record_connection_event("meshtastic", "connected")

        # Flood with errors
        for _ in range(15):
            health.record_error("meshtastic", OSError("fail"))

        assert health.is_healthy() is False

    def test_window_size_limits_memory(self):
        """Rolling windows don't grow unbounded."""
        health = BridgeHealthMonitor(window_size=10)

        for i in range(100):
            health.record_connection_event("meshtastic", "retry")
            health.record_error("meshtastic", OSError(f"err {i}"))

        assert len(health._connection_events) <= 10
        assert len(health._error_events) <= 10

    def test_thread_safety(self):
        """Concurrent access doesn't corrupt state."""
        health = BridgeHealthMonitor()
        errors = []

        def writer(prefix):
            try:
                for i in range(50):
                    health.record_message_sent("mesh_to_rns")
                    health.record_connection_event("meshtastic", "retry")
                    health.record_error("rns", OSError(f"{prefix}_{i}"))
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=writer, args=(f"t{i}",)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        summary = health.get_summary()
        assert summary["messages"]["mesh_to_rns"] == 250  # 5 threads * 50

    def test_summary_has_all_fields(self):
        """Summary includes all expected metric sections."""
        health = BridgeHealthMonitor()
        health.record_connection_event("meshtastic", "connected")
        health.record_message_sent("mesh_to_rns")

        summary = health.get_summary()

        assert "uptime_seconds" in summary
        assert "connections" in summary
        assert "messages" in summary
        assert "errors" in summary
        assert "meshtastic" in summary["connections"]
        assert "rns" in summary["connections"]
        assert "rate_per_min" in summary["messages"]
