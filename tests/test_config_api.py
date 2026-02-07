"""Tests for Configuration API (src/utils/config_api.py).

Tests:
- RESTful operations (GET, PUT, DELETE, PATCH)
- Validation with various validators
- Change callbacks
- Audit logging
- HTTP server functionality
- Factory functions

Author: Claude Code (Opus 4.5)
Session: claude/config-api-restful-ETusS
"""

import copy
import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from utils.config_api import (
    ConfigurationAPI,
    ConfigValidator,
    ConfigResult,
    ConfigChangeType,
    ConfigChange,
    ConfigSchema,
    ValidationResult,
    ConfigAPIServer,
    create_gateway_config_api,
)


# =============================================================================
# Fixtures
# =============================================================================


class MockSettingsManager:
    """Mock SettingsManager for testing."""

    def __init__(self, name="test", defaults=None):
        self._name = name
        self._defaults = copy.deepcopy(defaults) if defaults else {}
        self._settings = copy.deepcopy(self._defaults)

    def all(self):
        return copy.deepcopy(self._settings)

    def save(self):
        return True

    def reset(self):
        self._settings = copy.deepcopy(self._defaults)


@pytest.fixture
def settings_manager():
    """Create a mock settings manager with test defaults."""
    return MockSettingsManager(
        name="test",
        defaults={
            "gateway": {
                "host": "localhost",
                "port": 8080,
            },
            "rns": {
                "port": 37428,
                "timeout": 30.0,
            },
            "logging": {
                "level": "INFO",
                "file": "/var/log/meshforge.log",
            },
        }
    )


@pytest.fixture
def api(settings_manager):
    """Create a ConfigurationAPI instance."""
    return ConfigurationAPI(settings_manager)


# =============================================================================
# Basic GET Tests
# =============================================================================


class TestConfigGet:
    """Tests for configuration GET operations."""

    def test_get_root(self, api, settings_manager):
        """Test getting entire configuration."""
        result = api.get("")
        assert result == settings_manager.all()

    def test_get_nested_path(self, api):
        """Test getting nested configuration value."""
        result = api.get("gateway.port")
        assert result == 8080

    def test_get_section(self, api):
        """Test getting configuration section."""
        result = api.get("rns")
        assert result == {"port": 37428, "timeout": 30.0}

    def test_get_nonexistent_path(self, api):
        """Test getting nonexistent path returns None."""
        result = api.get("nonexistent.path")
        assert result is None

    def test_get_returns_deep_copy(self, api):
        """Test that GET returns a deep copy."""
        result = api.get("gateway")
        result["port"] = 9999
        assert api.get("gateway.port") == 8080  # Original unchanged


# =============================================================================
# PUT Tests
# =============================================================================


class TestConfigPut:
    """Tests for configuration PUT operations."""

    def test_put_simple_value(self, api):
        """Test putting a simple value."""
        result = api.put("gateway.port", 9000)
        assert result.success
        assert result.path == "gateway.port"
        assert result.value == 9000
        assert result.previous_value == 8080
        assert result.change_type == ConfigChangeType.SET

    def test_put_creates_nested_path(self, api):
        """Test that PUT creates nested path if not exists."""
        result = api.put("new.nested.value", "test")
        assert result.success
        assert api.get("new.nested.value") == "test"

    def test_put_to_root_fails(self, api):
        """Test that PUT to root path fails."""
        result = api.put("", {"key": "value"})
        assert not result.success
        assert "root path" in result.error.lower()

    def test_put_with_validation_success(self, api):
        """Test PUT with passing validation."""
        api.register_validator("gateway.port", ConfigValidator.port_validator())
        result = api.put("gateway.port", 8081)
        assert result.success

    def test_put_with_validation_failure(self, api):
        """Test PUT with failing validation."""
        api.register_validator("gateway.port", ConfigValidator.port_validator())
        result = api.put("gateway.port", 99999)
        assert not result.success
        assert "65535" in result.error


# =============================================================================
# DELETE Tests
# =============================================================================


class TestConfigDelete:
    """Tests for configuration DELETE operations."""

    def test_delete_value(self, api):
        """Test deleting a configuration value."""
        result = api.delete("gateway.port")
        assert result.success
        assert result.previous_value == 8080
        assert result.change_type == ConfigChangeType.DELETE
        assert api.get("gateway.port") is None

    def test_delete_nonexistent_path(self, api):
        """Test deleting nonexistent path fails."""
        result = api.delete("nonexistent.path")
        assert not result.success
        assert "not found" in result.error.lower()

    def test_delete_root_fails(self, api):
        """Test that DELETE on root path fails."""
        result = api.delete("")
        assert not result.success


# =============================================================================
# PATCH Tests
# =============================================================================


class TestConfigPatch:
    """Tests for configuration PATCH operations."""

    def test_patch_section(self, api):
        """Test patching a configuration section."""
        result = api.patch("gateway", {"timeout": 60})
        assert result.success
        assert api.get("gateway.timeout") == 60
        assert api.get("gateway.port") == 8080  # Original value preserved

    def test_patch_root(self, api):
        """Test patching root configuration."""
        result = api.patch("", {"new_key": "new_value"})
        assert result.success
        assert api.get("new_key") == "new_value"

    def test_patch_with_validation(self, api):
        """Test PATCH with validation."""
        api.register_validator("gateway.port", ConfigValidator.port_validator())
        result = api.patch("gateway", {"port": 99999})
        assert not result.success


# =============================================================================
# RESET Tests
# =============================================================================


class TestConfigReset:
    """Tests for configuration RESET operations."""

    def test_reset_entire_config(self, api, settings_manager):
        """Test resetting entire configuration."""
        api.put("gateway.port", 9999)
        result = api.reset()
        assert result.success
        assert result.change_type == ConfigChangeType.RESET
        assert api.get("gateway.port") == 8080  # Back to default

    def test_reset_specific_path(self, api, settings_manager):
        """Test resetting specific path to default."""
        api.put("gateway.port", 9999)
        result = api.reset("gateway.port")
        assert result.success
        assert api.get("gateway.port") == 8080


# =============================================================================
# Validator Tests
# =============================================================================


class TestValidators:
    """Tests for configuration validators."""

    def test_port_validator_valid(self):
        """Test port validator with valid port."""
        validator = ConfigValidator.port_validator()
        result = validator(8080)
        assert result.valid

    def test_port_validator_invalid_type(self):
        """Test port validator with invalid type."""
        validator = ConfigValidator.port_validator()
        result = validator("8080")
        assert not result.valid
        assert "integer" in result.error.lower()

    def test_port_validator_out_of_range(self):
        """Test port validator with out of range value."""
        validator = ConfigValidator.port_validator()
        result = validator(99999)
        assert not result.valid

    def test_port_validator_reserved(self):
        """Test port validator with reserved port."""
        validator = ConfigValidator.port_validator()
        result = validator(80)
        assert not result.valid
        assert "reserved" in result.error.lower()

    def test_integer_validator(self):
        """Test integer validator."""
        validator = ConfigValidator.integer_validator(min_val=1, max_val=100)
        assert validator(50).valid
        assert not validator(0).valid
        assert not validator(101).valid
        assert not validator("50").valid

    def test_float_validator(self):
        """Test float validator."""
        validator = ConfigValidator.float_validator(min_val=0.0, max_val=1.0)
        assert validator(0.5).valid
        assert validator(0).valid  # int accepted
        assert not validator(-0.1).valid
        assert not validator(1.1).valid

    def test_string_validator(self):
        """Test string validator."""
        validator = ConfigValidator.string_validator(min_length=3, max_length=10)
        assert validator("hello").valid
        assert not validator("hi").valid
        assert not validator("this is too long").valid

    def test_string_validator_pattern(self):
        """Test string validator with pattern."""
        validator = ConfigValidator.string_validator(pattern=r"^[a-z]+$")
        assert validator("hello").valid
        assert not validator("Hello").valid
        assert not validator("hello123").valid

    def test_boolean_validator(self):
        """Test boolean validator."""
        validator = ConfigValidator.boolean_validator()
        assert validator(True).valid
        assert validator(False).valid
        assert not validator(1).valid
        assert not validator("true").valid

    def test_enum_validator(self):
        """Test enum validator."""
        validator = ConfigValidator.enum_validator(["debug", "info", "warning", "error"])
        assert validator("info").valid
        assert not validator("verbose").valid

    def test_ip_address_validator(self):
        """Test IP address validator."""
        validator = ConfigValidator.ip_address_validator()
        assert validator("192.168.1.1").valid
        assert validator("::1").valid
        assert not validator("not.an.ip").valid
        assert not validator("256.0.0.1").valid

    def test_hostname_validator(self):
        """Test hostname validator."""
        validator = ConfigValidator.hostname_validator()
        assert validator("localhost").valid
        assert validator("node1.mesh.local").valid
        assert not validator("-invalid").valid

    def test_path_validator(self):
        """Test path validator."""
        validator = ConfigValidator.path_validator()
        assert validator("/var/log/test.log").valid

    def test_composite_validator(self):
        """Test composite validator."""
        validator = ConfigValidator.composite(
            ConfigValidator.integer_validator(min_val=1),
            ConfigValidator.integer_validator(max_val=100)
        )
        assert validator(50).valid
        assert not validator(0).valid
        assert not validator(101).valid


# =============================================================================
# Callback Tests
# =============================================================================


class TestCallbacks:
    """Tests for change callbacks."""

    def test_path_callback(self, api):
        """Test path-specific callback."""
        callback_data = {}

        def callback(old_value, new_value):
            callback_data["old"] = old_value
            callback_data["new"] = new_value

        api.on_change("gateway.port", callback)
        api.put("gateway.port", 9000)

        assert callback_data["old"] == 8080
        assert callback_data["new"] == 9000

    def test_global_callback(self, api):
        """Test global change callback."""
        changes = []

        def callback(change: ConfigChange):
            changes.append(change)

        api.on_any_change(callback)
        api.put("gateway.port", 9000)
        api.put("rns.timeout", 60.0)

        assert len(changes) == 2
        assert changes[0].path == "gateway.port"
        assert changes[1].path == "rns.timeout"

    def test_callback_not_fired_on_validation_failure(self, api):
        """Test callback not fired when validation fails."""
        callback_called = [False]

        def callback(old_value, new_value):
            callback_called[0] = True

        api.register_validator("gateway.port", ConfigValidator.port_validator())
        api.on_change("gateway.port", callback)
        api.put("gateway.port", 99999)  # Invalid

        assert not callback_called[0]

    def test_remove_callback(self, api):
        """Test removing a callback."""
        callback_count = [0]

        def callback(old_value, new_value):
            callback_count[0] += 1

        api.on_change("gateway.port", callback)
        api.put("gateway.port", 9000)
        assert callback_count[0] == 1

        api.remove_callback("gateway.port", callback)
        api.put("gateway.port", 9001)
        assert callback_count[0] == 1  # Not incremented


# =============================================================================
# Audit Log Tests
# =============================================================================


class TestAuditLog:
    """Tests for audit logging."""

    def test_audit_log_records_changes(self, api):
        """Test that changes are recorded in audit log."""
        api.put("gateway.port", 9000)
        api.put("rns.timeout", 60.0)

        log = api.get_audit_log(limit=10)
        assert len(log) == 2
        assert log[0].path == "rns.timeout"  # Most recent first
        assert log[1].path == "gateway.port"

    def test_audit_log_path_filter(self, api):
        """Test audit log path filtering."""
        api.put("gateway.port", 9000)
        api.put("rns.timeout", 60.0)

        log = api.get_audit_log(path_filter="gateway")
        assert len(log) == 1
        assert log[0].path == "gateway.port"

    def test_audit_log_limit(self, api):
        """Test audit log limit."""
        for i in range(10):
            api.put(f"test.key{i}", i)

        log = api.get_audit_log(limit=5)
        assert len(log) == 5


# =============================================================================
# Schema Tests
# =============================================================================


class TestSchema:
    """Tests for configuration schema."""

    def test_register_schema_int(self, api):
        """Test registering integer schema."""
        schema = ConfigSchema(
            path="test.count",
            type=int,
            min_value=0,
            max_value=100
        )
        api.register_schema(schema)

        # Valid value
        result = api.put("test.count", 50)
        assert result.success

        # Invalid value
        result = api.put("test.count", 200)
        assert not result.success

    def test_register_schema_enum(self, api):
        """Test registering enum schema."""
        schema = ConfigSchema(
            path="logging.level",
            type=str,
            allowed_values=["DEBUG", "INFO", "WARNING", "ERROR"]
        )
        api.register_schema(schema)

        result = api.put("logging.level", "DEBUG")
        assert result.success

        result = api.put("logging.level", "VERBOSE")
        assert not result.success


# =============================================================================
# Utility Method Tests
# =============================================================================


class TestUtilityMethods:
    """Tests for utility methods."""

    def test_list_paths(self, api):
        """Test listing all configuration paths."""
        paths = api.list_paths()
        assert "gateway" in paths
        assert "gateway.host" in paths
        assert "gateway.port" in paths
        assert "rns" in paths
        assert "rns.port" in paths

    def test_list_paths_with_prefix(self, api):
        """Test listing paths with prefix filter."""
        paths = api.list_paths("gateway")
        assert all(p.startswith("gateway") for p in paths)
        assert "gateway.host" in paths
        assert "rns.port" not in paths

    def test_export_json(self, api):
        """Test exporting configuration as JSON."""
        json_str = api.export_json()
        config = json.loads(json_str)
        assert config["gateway"]["port"] == 8080

    def test_import_json(self, api):
        """Test importing configuration from JSON."""
        new_config = {"test": {"key": "value"}}
        result = api.import_json(json.dumps(new_config), validate=False)
        assert result.success
        assert api.get("test.key") == "value"

    def test_import_json_invalid(self, api):
        """Test importing invalid JSON."""
        result = api.import_json("not valid json")
        assert not result.success
        assert "Invalid JSON" in result.error

    def test_validate_without_applying(self, api):
        """Test validating value without applying."""
        api.register_validator("gateway.port", ConfigValidator.port_validator())

        # Valid
        result = api.validate("gateway.port", 8081)
        assert result.valid

        # Invalid
        result = api.validate("gateway.port", 99999)
        assert not result.valid

        # Original unchanged
        assert api.get("gateway.port") == 8080


# =============================================================================
# Factory Function Tests
# =============================================================================


class TestFactoryFunctions:
    """Tests for factory functions."""

    def test_create_gateway_config_api(self):
        """Test gateway config API factory."""
        api = create_gateway_config_api()
        assert api is not None

        # Check validators are registered
        result = api.validate("rns.port", 99999)
        assert not result.valid

        result = api.validate("rns.port", 37428)
        assert result.valid


# =============================================================================
# Thread Safety Tests
# =============================================================================


class TestThreadSafety:
    """Tests for thread safety."""

    def test_concurrent_puts(self, api):
        """Test concurrent PUT operations."""
        results = []
        errors = []

        def writer(key: str, value: int):
            try:
                for i in range(100):
                    result = api.put(f"{key}.{i}", value)
                    results.append(result.success)
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=writer, args=("thread1", 1)),
            threading.Thread(target=writer, args=("thread2", 2)),
        ]

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        assert all(results)

    def test_concurrent_get_put(self, api):
        """Test concurrent GET and PUT operations."""
        errors = []

        def reader():
            try:
                for _ in range(100):
                    api.get("gateway.port")
            except Exception as e:
                errors.append(e)

        def writer():
            try:
                for i in range(100):
                    api.put("gateway.port", 8080 + i)
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=reader),
            threading.Thread(target=writer),
        ]

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors


# =============================================================================
# HTTP Server Tests (if available)
# =============================================================================


class TestHTTPServer:
    """Tests for Configuration API HTTP server."""

    @pytest.fixture
    def server(self, api):
        """Create and start a test server."""
        server = ConfigAPIServer(api, host="127.0.0.1", port=0)  # Port 0 = auto-assign

        # We can't easily test with port 0, so skip if server doesn't support it
        # In a real test, you'd use a specific port
        yield server

    def test_server_creation(self, api):
        """Test server can be created."""
        server = ConfigAPIServer(api, port=18081)
        assert server is not None
        assert not server.is_running


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
