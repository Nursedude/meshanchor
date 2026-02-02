"""
Tests for Device Persistence

Tests auto-reconnect and device memory functionality.
"""

import pytest
import time
from unittest.mock import patch, MagicMock
from pathlib import Path
import tempfile
import json

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


class TestDevicePersistence:
    """Tests for DevicePersistence class."""

    @pytest.fixture(autouse=True)
    def reset_singleton(self):
        """Reset singleton before each test."""
        from utils.device_persistence import DevicePersistence
        DevicePersistence.reset_instance()
        yield
        DevicePersistence.reset_instance()

    @pytest.fixture
    def persistence(self):
        """Get a fresh DevicePersistence instance."""
        from utils.device_persistence import DevicePersistence
        return DevicePersistence.get_instance()

    def test_singleton_pattern(self):
        """Test that get_instance returns same instance."""
        from utils.device_persistence import DevicePersistence
        p1 = DevicePersistence.get_instance()
        p2 = DevicePersistence.get_instance()
        assert p1 is p2

    def test_no_last_device_initially(self, persistence):
        """Test that there's no last device initially."""
        assert persistence.has_last_device() is False
        assert persistence.get_last_device() is None

    def test_record_successful_connection(self, persistence):
        """Test recording a successful connection."""
        persistence.record_connection(
            connection_type="tcp",
            address="localhost:4403",
            device_info={"firmware": "2.3.0"},
            success=True
        )

        assert persistence.has_last_device() is True
        last = persistence.get_last_device()
        assert last["connection_type"] == "tcp"
        assert last["address"] == "localhost:4403"
        assert last["device_info"]["firmware"] == "2.3.0"

    def test_record_failed_connection(self, persistence):
        """Test that failed connections don't become last device."""
        persistence.record_connection(
            connection_type="tcp",
            address="badhost:4403",
            success=False,
            error_message="Connection refused"
        )

        # Should not have a last device
        assert persistence.has_last_device() is False

        # But should be in history
        history = persistence.get_connection_history()
        assert len(history) == 1
        assert history[0].success is False
        assert history[0].error_message == "Connection refused"

    def test_connection_history(self, persistence):
        """Test connection history tracking."""
        # Record several connections
        for i in range(5):
            persistence.record_connection(
                connection_type="tcp",
                address=f"host{i}:4403",
                success=i % 2 == 0  # Alternating success/fail
            )

        history = persistence.get_connection_history(count=10)
        assert len(history) == 5

        # Most recent should be first
        assert "host4" in history[0].address

    def test_clear_last_device(self, persistence):
        """Test clearing last device."""
        persistence.record_connection(
            connection_type="tcp",
            address="localhost:4403",
            success=True
        )

        assert persistence.has_last_device() is True
        persistence.clear_last_device()
        assert persistence.has_last_device() is False

    def test_auto_reconnect_enabled(self, persistence):
        """Test auto-reconnect flag."""
        # Should be enabled by default
        assert persistence.auto_reconnect_enabled is True

        persistence.auto_reconnect_enabled = False
        assert persistence.auto_reconnect_enabled is False

    def test_preferred_connection_type(self, persistence):
        """Test preferred connection type setting."""
        # Should be None (auto) by default
        assert persistence.preferred_connection_type is None

        persistence.preferred_connection_type = "tcp"
        assert persistence.preferred_connection_type == "tcp"

        # Invalid type should raise
        with pytest.raises(ValueError):
            persistence.preferred_connection_type = "invalid"

    def test_get_reconnect_config_tcp(self, persistence):
        """Test getting reconnect config for TCP."""
        persistence.record_connection(
            connection_type="tcp",
            address="192.168.1.100:4403",
            success=True
        )

        config = persistence.get_reconnect_config()
        assert config is not None
        assert config["connection_type"] == "TCP"
        assert config["host"] == "192.168.1.100"
        assert config["port"] == 4403

    def test_get_reconnect_config_serial(self, persistence):
        """Test getting reconnect config for serial."""
        persistence.record_connection(
            connection_type="serial",
            address="/dev/ttyUSB0",
            success=True
        )

        config = persistence.get_reconnect_config()
        assert config is not None
        assert config["connection_type"] == "SERIAL"
        assert config["serial_port"] == "/dev/ttyUSB0"

    def test_get_reconnect_config_ble(self, persistence):
        """Test getting reconnect config for BLE."""
        persistence.record_connection(
            connection_type="ble",
            address="AA:BB:CC:DD:EE:FF",
            success=True
        )

        config = persistence.get_reconnect_config()
        assert config is not None
        assert config["connection_type"] == "BLE"
        assert config["ble_address"] == "AA:BB:CC:DD:EE:FF"

    def test_get_reconnect_config_disabled(self, persistence):
        """Test that reconnect config returns None when disabled."""
        persistence.record_connection(
            connection_type="tcp",
            address="localhost:4403",
            success=True
        )

        persistence.auto_reconnect_enabled = False
        config = persistence.get_reconnect_config()
        assert config is None

    def test_get_stats(self, persistence):
        """Test getting persistence statistics."""
        # Record some connections
        persistence.record_connection("tcp", "host1:4403", success=True)
        persistence.record_connection("tcp", "host2:4403", success=False)
        persistence.record_connection("tcp", "host3:4403", success=True)

        stats = persistence.get_stats()
        assert stats["has_last_device"] is True
        assert stats["history_entries"] == 3
        assert stats["successful_connections"] == 2
        assert stats["failed_connections"] == 1

    def test_history_limit(self, persistence):
        """Test that history is limited to max entries."""
        # Record more than the limit
        for i in range(30):
            persistence.record_connection(
                connection_type="tcp",
                address=f"host{i}:4403",
                success=True
            )

        history = persistence.get_connection_history(count=100)
        # Default max is 20
        assert len(history) <= 20


class TestConnectionRecord:
    """Tests for ConnectionRecord dataclass."""

    def test_to_dict(self):
        """Test serialization."""
        from utils.device_persistence import ConnectionRecord

        record = ConnectionRecord(
            connection_type="tcp",
            address="localhost:4403",
            timestamp=1234567890.0,
            success=True,
            device_info={"firmware": "2.3.0"},
            error_message=None
        )

        data = record.to_dict()
        assert data["connection_type"] == "tcp"
        assert data["address"] == "localhost:4403"
        assert data["timestamp"] == 1234567890.0
        assert data["success"] is True

    def test_from_dict(self):
        """Test deserialization."""
        from utils.device_persistence import ConnectionRecord

        data = {
            "connection_type": "serial",
            "address": "/dev/ttyUSB0",
            "timestamp": 1234567890.0,
            "success": False,
            "device_info": {},
            "error_message": "Device not found"
        }

        record = ConnectionRecord.from_dict(data)
        assert record.connection_type == "serial"
        assert record.address == "/dev/ttyUSB0"
        assert record.success is False
        assert record.error_message == "Device not found"


class TestDevicePersistenceConvenience:
    """Tests for convenience functions."""

    @pytest.fixture(autouse=True)
    def reset_singleton(self):
        """Reset singleton before each test."""
        from utils.device_persistence import DevicePersistence
        DevicePersistence.reset_instance()
        yield
        DevicePersistence.reset_instance()

    def test_get_device_persistence(self):
        """Test convenience function returns singleton."""
        from utils.device_persistence import get_device_persistence, DevicePersistence

        p1 = get_device_persistence()
        p2 = DevicePersistence.get_instance()
        assert p1 is p2
