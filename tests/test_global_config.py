"""Tests for src/utils/global_config.py — MeshForge ecosystem global.ini reader.

Mirrors the NOC reference test suite (Nursedude/meshforge commit 569a490
tests/test_global_config.py) adapted to MeshAnchor's import paths.

Run: python3 -m pytest tests/test_global_config.py -v
"""

import logging
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from src.utils.global_config import (
    _coerce_bool,
    _coerce_float,
    _coerce_int,
    global_config_path,
    load_global_overrides,
)


# =============================================================================
# Coerce helpers
# =============================================================================

class TestCoerceHelpers:
    def test_coerce_int_valid(self):
        assert _coerce_int("1883", 0) == 1883

    def test_coerce_int_invalid_returns_default(self):
        assert _coerce_int("garbage", 0) == 0

    def test_coerce_int_empty_returns_default(self):
        assert _coerce_int("", 42) == 42

    def test_coerce_float_valid(self):
        assert _coerce_float("3.14") == pytest.approx(3.14)

    def test_coerce_float_invalid_returns_none(self):
        assert _coerce_float("garbage") is None

    def test_coerce_bool_none_input_returns_none(self):
        assert _coerce_bool(None) is None

    def test_coerce_bool_blank_string_returns_none(self):
        assert _coerce_bool("") is None
        assert _coerce_bool("   ") is None

    def test_coerce_bool_truthy_strings(self):
        for v in ("true", "True", "TRUE", "yes", "1", "on"):
            assert _coerce_bool(v) is True, f"expected True for {v!r}"

    def test_coerce_bool_false_string(self):
        assert _coerce_bool("false") is False
        assert _coerce_bool("no") is False
        assert _coerce_bool("0") is False

    def test_coerce_bool_explicit_false_distinguishable_from_none(self):
        # None means "global said nothing"; False means "global said off"
        result = _coerce_bool("false")
        assert result is not None
        assert result is False


# =============================================================================
# global_config_path
# =============================================================================

class TestGlobalConfigPath:
    def test_path_ends_with_meshforge_global_ini(self):
        p = global_config_path()
        assert p.name == "global.ini"
        assert p.parent.name == "meshforge"

    def test_path_contains_config_segment(self):
        p = global_config_path()
        assert ".config" in str(p)

    @patch.dict(os.environ, {"SUDO_USER": "testuser"}, clear=False)
    def test_path_under_sudo_user(self, tmp_path):
        import pwd
        try:
            pw = pwd.getpwnam("testuser")
            expected_home = Path(pw.pw_dir)
        except KeyError:
            # testuser doesn't exist on this machine; patch get_real_user_home
            with patch(
                "src.utils.global_config.get_real_user_home",
                return_value=Path("/home/testuser"),
            ):
                p = global_config_path()
                assert str(p).startswith("/home/testuser")
            return

        p = global_config_path()
        assert str(p).startswith(str(expected_home))


# =============================================================================
# load_global_overrides — reader behaviour
# =============================================================================

class TestLoadGlobalOverridesReader:
    def test_missing_file_returns_empty_dict(self, tmp_path):
        result = load_global_overrides(tmp_path / "nonexistent.ini")
        assert result == {}

    def test_malformed_ini_returns_empty_dict_and_logs_debug(
        self, tmp_path, caplog
    ):
        bad = tmp_path / "bad.ini"
        bad.write_text("\x00\x01\x02\x03")
        with caplog.at_level(logging.DEBUG, logger="src.utils.global_config"):
            result = load_global_overrides(bad)
        assert result == {}

    def test_empty_file_returns_empty_dict(self, tmp_path):
        empty = tmp_path / "global.ini"
        empty.write_text("")
        assert load_global_overrides(empty) == {}

    def test_non_mqtt_sections_ignored(self, tmp_path):
        ini = tmp_path / "global.ini"
        ini.write_text("[node]\nshort_name = WH6GXZ\n[region]\npreset = US915\n")
        assert load_global_overrides(ini) == {}

    def test_broker_sets_mqtt_broker_and_infers_enabled(self, tmp_path):
        ini = tmp_path / "global.ini"
        ini.write_text("[mqtt]\nbroker = 192.168.1.10\n")
        result = load_global_overrides(ini)
        assert result["mqtt_broker"] == "192.168.1.10"
        assert result["mqtt_enabled"] is True

    def test_explicit_enabled_false_overrides_inference(self, tmp_path):
        ini = tmp_path / "global.ini"
        ini.write_text("[mqtt]\nbroker = 192.168.1.10\nenabled = false\n")
        result = load_global_overrides(ini)
        assert result["mqtt_broker"] == "192.168.1.10"
        assert result["mqtt_enabled"] is False

    def test_blank_broker_emits_no_keys(self, tmp_path):
        ini = tmp_path / "global.ini"
        ini.write_text("[mqtt]\nbroker =\n")
        result = load_global_overrides(ini)
        assert "mqtt_broker" not in result
        assert "mqtt_enabled" not in result

    def test_zero_port_skipped(self, tmp_path):
        ini = tmp_path / "global.ini"
        ini.write_text("[mqtt]\nbroker = localhost\nport = 0\n")
        result = load_global_overrides(ini)
        assert "mqtt_port" not in result

    def test_garbage_port_skipped(self, tmp_path):
        ini = tmp_path / "global.ini"
        ini.write_text("[mqtt]\nbroker = localhost\nport = notanumber\n")
        result = load_global_overrides(ini)
        assert "mqtt_port" not in result

    def test_valid_port_coerced_to_int(self, tmp_path):
        ini = tmp_path / "global.ini"
        ini.write_text("[mqtt]\nbroker = localhost\nport = 1883\n")
        result = load_global_overrides(ini)
        assert result["mqtt_port"] == 1883
        assert isinstance(result["mqtt_port"], int)

    def test_no_broker_no_enabled_key(self, tmp_path):
        ini = tmp_path / "global.ini"
        ini.write_text("[mqtt]\nport = 1883\n")
        result = load_global_overrides(ini)
        # Port alone doesn't imply enabled; no broker → no mqtt_enabled key
        assert "mqtt_enabled" not in result
        assert result.get("mqtt_port") == 1883


# =============================================================================
# DaemonConfig integration — global.ini seeding is applied before YAML
# =============================================================================

class TestDaemonConfigGlobalSeeding:
    def test_global_ini_seeds_daemon_config(self, tmp_path):
        ini = tmp_path / "global.ini"
        ini.write_text("[mqtt]\nbroker = 10.0.0.1\nport = 1884\n")

        from src.daemon_config import DaemonConfig

        with patch(
            "src.daemon_config.load_global_overrides",
            return_value={"mqtt_broker": "10.0.0.1", "mqtt_port": 1884, "mqtt_enabled": True},
        ):
            config = DaemonConfig.load()

        assert config.mqtt_broker == "10.0.0.1"
        assert config.mqtt_port == 1884
        assert config.mqtt_enabled is True

    def test_yaml_overrides_global_ini(self, tmp_path):
        """Per-app YAML config wins over global.ini (loaded after global seeding)."""
        from src.daemon_config import DaemonConfig

        def fake_load_yaml(self_config, path):
            self_config.mqtt_broker = "yaml-broker"

        with patch(
            "src.daemon_config.load_global_overrides",
            return_value={"mqtt_broker": "global-broker", "mqtt_enabled": True},
        ):
            with patch.object(DaemonConfig, "_load_yaml", fake_load_yaml):
                fake_yaml = tmp_path / "daemon.yaml"
                fake_yaml.write_text("")
                with patch.object(
                    DaemonConfig, "_config_search_paths", return_value=[fake_yaml]
                ):
                    config = DaemonConfig.load()

        assert config.mqtt_broker == "yaml-broker"
