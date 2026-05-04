"""Phase 6.3 — meshforge-maps endpoint config (host/port/timeout).

Three layers under test:

1. `utils.meshforge_maps_config.MapsConfig` + `load_maps_config` /
   `save_maps_config` — defaults match Phase 6 hardcoded values, settings
   override is honored, validation rejects bad input, on-disk corruption
   falls back to defaults rather than crashing the TUI.
2. `MapsConfig.build_client()` — produces a `MeshforgeMapsClient` whose
   host / port / timeout reflect the config (used by the handler in 3).
3. `launcher_tui.handlers.meshforge_maps.MeshforgeMapsHandler` — `_client()`
   reads from settings rather than hardcoding localhost; the new
   `mf_endpoint` menu item is wired to `_configure_endpoint`; the prompt
   validators surface a fixable error rather than writing junk.
"""

import json
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

SRC_DIR = os.path.join(os.path.dirname(__file__), "..", "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)
# tests/ holds shared test helpers (handler_test_utils.FakeDialog).
TESTS_DIR = os.path.dirname(__file__)
if TESTS_DIR not in sys.path:
    sys.path.insert(0, TESTS_DIR)

from utils.meshforge_maps_client import (
    DEFAULT_HOST,
    DEFAULT_PORT,
    DEFAULT_TIMEOUT,
    MeshforgeMapsClient,
)
from utils.meshforge_maps_config import (
    DEFAULTS,
    SETTINGS_NAME,
    MapsConfig,
    MapsConfigError,
    load_maps_config,
    reset_maps_config,
    save_maps_config,
)
from launcher_tui.handlers.meshforge_maps import (
    MeshforgeMapsHandler,
    _format_status,
)


# ─────────────────────────────────────────────────────────────────────
# Fixtures — every test gets an isolated config dir so the user's real
# ~/.config/meshanchor/meshforge_maps.json never leaks in.
# ─────────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def isolated_config_dir(tmp_path, monkeypatch):
    """Redirect SettingsManager's default CONFIG_DIR to a tmp dir."""
    monkeypatch.setattr("utils.common.CONFIG_DIR", tmp_path)
    yield tmp_path


def _settings_file(tmp_path):
    return tmp_path / f"{SETTINGS_NAME}.json"


# ─────────────────────────────────────────────────────────────────────
# 1. Config module — defaults, save, load, validation
# ─────────────────────────────────────────────────────────────────────


class TestDefaults:
    def test_defaults_match_phase6_hardcoded_values(self):
        """The whole point of this phase is non-breaking: localhost
        deployments must keep working without writing a settings file."""
        assert DEFAULTS["host"] == DEFAULT_HOST == "localhost"
        assert DEFAULTS["port"] == DEFAULT_PORT == 8808
        assert DEFAULTS["timeout"] == DEFAULT_TIMEOUT == 3.0

    def test_load_returns_defaults_when_no_settings_file(self, isolated_config_dir):
        assert not _settings_file(isolated_config_dir).exists()
        cfg = load_maps_config()
        assert cfg.host == DEFAULT_HOST
        assert cfg.port == DEFAULT_PORT
        assert cfg.timeout == DEFAULT_TIMEOUT


class TestSaveAndLoad:
    def test_save_persists_all_fields(self, isolated_config_dir):
        cfg = save_maps_config(host="maps.lan", port=9999, timeout=5.5)
        assert cfg.host == "maps.lan"
        assert cfg.port == 9999
        assert cfg.timeout == 5.5

        # Round-trips through load.
        reloaded = load_maps_config()
        assert reloaded == cfg

    def test_save_allows_partial_update(self, isolated_config_dir):
        save_maps_config(host="first.lan", port=1111, timeout=2.5)
        # Now only update port.
        cfg = save_maps_config(port=2222)
        assert cfg.host == "first.lan"
        assert cfg.port == 2222
        assert cfg.timeout == 2.5

    def test_save_with_no_args_is_a_noop(self, isolated_config_dir):
        save_maps_config(host="orig.lan", port=5000, timeout=4.0)
        cfg = save_maps_config()
        assert cfg.host == "orig.lan"
        assert cfg.port == 5000
        assert cfg.timeout == 4.0

    def test_save_writes_json_to_disk(self, isolated_config_dir):
        save_maps_config(host="probe.lan", port=8443, timeout=6.0)
        path = _settings_file(isolated_config_dir)
        assert path.exists()
        on_disk = json.loads(path.read_text())
        assert on_disk["host"] == "probe.lan"
        assert on_disk["port"] == 8443
        assert on_disk["timeout"] == 6.0

    def test_reset_restores_defaults(self, isolated_config_dir):
        save_maps_config(host="custom.lan", port=9000, timeout=10.0)
        cfg = reset_maps_config()
        assert cfg.host == DEFAULT_HOST
        assert cfg.port == DEFAULT_PORT
        assert cfg.timeout == DEFAULT_TIMEOUT
        # And the on-disk JSON now matches defaults.
        assert load_maps_config() == cfg


class TestLoadFromCorruptOrInvalid:
    def test_load_from_corrupt_json_falls_back_to_defaults(
        self, isolated_config_dir
    ):
        path = _settings_file(isolated_config_dir)
        path.write_text("{ this is not valid json ")
        cfg = load_maps_config()
        assert cfg.host == DEFAULT_HOST
        assert cfg.port == DEFAULT_PORT
        assert cfg.timeout == DEFAULT_TIMEOUT

    def test_load_with_bad_port_uses_default(self, isolated_config_dir):
        """Port out of range shouldn't lock the user out of the TUI."""
        path = _settings_file(isolated_config_dir)
        path.write_text(json.dumps({
            "host": "good.lan",
            "port": 999999,           # invalid
            "timeout": 3.0,
        }))
        cfg = load_maps_config()
        assert cfg.host == "good.lan"
        assert cfg.port == DEFAULT_PORT  # fallen back
        assert cfg.timeout == 3.0

    def test_load_with_bad_host_uses_default(self, isolated_config_dir):
        path = _settings_file(isolated_config_dir)
        path.write_text(json.dumps({
            "host": "",               # invalid
            "port": 8808,
            "timeout": 3.0,
        }))
        cfg = load_maps_config()
        assert cfg.host == DEFAULT_HOST
        assert cfg.port == 8808

    def test_load_with_bad_timeout_uses_default(self, isolated_config_dir):
        path = _settings_file(isolated_config_dir)
        path.write_text(json.dumps({
            "host": "good.lan",
            "port": 8808,
            "timeout": -1.0,           # invalid
        }))
        cfg = load_maps_config()
        assert cfg.timeout == DEFAULT_TIMEOUT


class TestValidation:
    def test_save_rejects_empty_host(self, isolated_config_dir):
        with pytest.raises(MapsConfigError):
            save_maps_config(host="")

    def test_save_rejects_host_with_invalid_chars(self, isolated_config_dir):
        with pytest.raises(MapsConfigError):
            save_maps_config(host="bad host!")

    def test_save_rejects_host_starting_with_dash(self, isolated_config_dir):
        with pytest.raises(MapsConfigError):
            save_maps_config(host="-rude.lan")

    def test_save_rejects_overlong_host(self, isolated_config_dir):
        with pytest.raises(MapsConfigError):
            save_maps_config(host="a" * 254)

    def test_save_rejects_port_zero(self, isolated_config_dir):
        with pytest.raises(MapsConfigError):
            save_maps_config(port=0)

    def test_save_rejects_negative_port(self, isolated_config_dir):
        with pytest.raises(MapsConfigError):
            save_maps_config(port=-1)

    def test_save_rejects_port_above_65535(self, isolated_config_dir):
        with pytest.raises(MapsConfigError):
            save_maps_config(port=70000)

    def test_save_rejects_non_int_port(self, isolated_config_dir):
        with pytest.raises(MapsConfigError):
            save_maps_config(port="not-a-port")

    def test_save_rejects_zero_timeout(self, isolated_config_dir):
        with pytest.raises(MapsConfigError):
            save_maps_config(timeout=0)

    def test_save_rejects_negative_timeout(self, isolated_config_dir):
        with pytest.raises(MapsConfigError):
            save_maps_config(timeout=-3.0)

    def test_save_rejects_timeout_above_60s(self, isolated_config_dir):
        """A 60s probe timeout already feels like an eternity for a TUI;
        anything bigger is almost certainly user error."""
        with pytest.raises(MapsConfigError):
            save_maps_config(timeout=120.0)

    def test_save_accepts_ipv4(self, isolated_config_dir):
        cfg = save_maps_config(host="192.168.1.42")
        assert cfg.host == "192.168.1.42"

    def test_save_accepts_ipv6_brackets_optional(self, isolated_config_dir):
        # Permissive: ":" allowed for IPv6-style addresses.
        cfg = save_maps_config(host="fe80::1")
        assert cfg.host == "fe80::1"

    def test_failed_save_does_not_corrupt_existing_config(
        self, isolated_config_dir
    ):
        save_maps_config(host="good.lan", port=8808, timeout=3.0)
        with pytest.raises(MapsConfigError):
            save_maps_config(port=999999)
        # Confirm earlier good config is intact.
        cfg = load_maps_config()
        assert cfg.host == "good.lan"
        assert cfg.port == 8808


class TestMapsConfig:
    def test_validate_accepts_default_values(self):
        cfg = MapsConfig(
            host=DEFAULT_HOST, port=DEFAULT_PORT, timeout=DEFAULT_TIMEOUT,
        )
        cfg.validate()  # no exception

    def test_validate_rejects_bad_port(self):
        cfg = MapsConfig(host="ok.lan", port=0, timeout=3.0)
        with pytest.raises(MapsConfigError):
            cfg.validate()

    def test_build_client_uses_configured_values(self):
        cfg = MapsConfig(host="custom.lan", port=12345, timeout=7.5)
        client = cfg.build_client()
        assert isinstance(client, MeshforgeMapsClient)
        assert client.host == "custom.lan"
        assert client.port == 12345
        assert client.timeout == 7.5
        assert client.web_url == "http://custom.lan:12345"

    def test_is_frozen(self):
        cfg = MapsConfig(host="x", port=1, timeout=1.0)
        with pytest.raises(Exception):  # FrozenInstanceError or AttributeError
            cfg.host = "y"  # type: ignore[misc]


# ─────────────────────────────────────────────────────────────────────
# 2. Handler — _client() reads from settings, not hardcoded localhost
# ─────────────────────────────────────────────────────────────────────


class TestHandlerUsesSettings:
    def test_client_uses_default_when_no_settings_file(
        self, isolated_config_dir
    ):
        h = MeshforgeMapsHandler()
        client = h._client()
        assert client.host == DEFAULT_HOST
        assert client.port == DEFAULT_PORT
        assert client.timeout == DEFAULT_TIMEOUT

    def test_client_picks_up_persisted_override(self, isolated_config_dir):
        save_maps_config(host="far.lan", port=9090, timeout=4.0)
        h = MeshforgeMapsHandler()
        client = h._client()
        assert client.host == "far.lan"
        assert client.port == 9090
        assert client.timeout == 4.0

    def test_client_rebuilt_each_call(self, isolated_config_dir):
        """No caching: changing settings should reflect on the next probe
        without having to recreate the handler."""
        h = MeshforgeMapsHandler()
        first = h._client()
        save_maps_config(host="elsewhere.lan", port=7777)
        second = h._client()
        assert first.host == DEFAULT_HOST
        assert second.host == "elsewhere.lan"
        assert second.port == 7777


class TestMenuItems:
    def test_menu_includes_endpoint_config_row(self):
        h = MeshforgeMapsHandler()
        keys = [item[0] for item in h.menu_items()]
        assert "mf_endpoint" in keys

    def test_endpoint_row_has_no_per_row_flag(self):
        """Section-level `maps` flag still does the gating; per-row stays None
        to match the rest of maps_viz."""
        h = MeshforgeMapsHandler()
        items = {item[0]: item for item in h.menu_items()}
        assert items["mf_endpoint"][2] is None

    def test_menu_keeps_phase6_items(self):
        h = MeshforgeMapsHandler()
        keys = [item[0] for item in h.menu_items()]
        assert keys[:2] == ["mf_status", "mf_open"]

    def test_execute_dispatches_endpoint(self):
        h = MeshforgeMapsHandler()
        ctx = MagicMock()
        h.ctx = ctx
        h.execute("mf_endpoint")
        ctx.safe_call.assert_called_once()
        args = ctx.safe_call.call_args.args
        assert args[0] == "Configure Maps Endpoint"
        assert args[1] == h._configure_endpoint


# ─────────────────────────────────────────────────────────────────────
# 3. Endpoint config dialog — input plumbing through SettingsManager
# ─────────────────────────────────────────────────────────────────────


class TestConfigureEndpointDialog:
    def _make_handler(self, dialog):
        h = MeshforgeMapsHandler()
        ctx = MagicMock()
        ctx.dialog = dialog
        h.ctx = ctx
        return h

    def test_back_exits_immediately(self, isolated_config_dir):
        from handler_test_utils import FakeDialog

        dialog = FakeDialog()
        dialog._menu_returns = ["back"]
        h = self._make_handler(dialog)
        h._configure_endpoint()
        # Settings still default — nothing written.
        assert not _settings_file(isolated_config_dir).exists()

    def test_set_host_persists_value(self, isolated_config_dir):
        from handler_test_utils import FakeDialog

        dialog = FakeDialog()
        dialog._menu_returns = ["host", "back"]
        dialog._inputbox_returns = ["other.lan"]
        h = self._make_handler(dialog)
        h._configure_endpoint()
        cfg = load_maps_config()
        assert cfg.host == "other.lan"

    def test_set_port_persists_value(self, isolated_config_dir):
        from handler_test_utils import FakeDialog

        dialog = FakeDialog()
        dialog._menu_returns = ["port", "back"]
        dialog._inputbox_returns = ["9090"]
        h = self._make_handler(dialog)
        h._configure_endpoint()
        cfg = load_maps_config()
        assert cfg.port == 9090

    def test_set_timeout_persists_value(self, isolated_config_dir):
        from handler_test_utils import FakeDialog

        dialog = FakeDialog()
        dialog._menu_returns = ["timeout", "back"]
        dialog._inputbox_returns = ["7.5"]
        h = self._make_handler(dialog)
        h._configure_endpoint()
        cfg = load_maps_config()
        assert cfg.timeout == 7.5

    def test_invalid_port_shows_msgbox_and_does_not_persist(
        self, isolated_config_dir
    ):
        from handler_test_utils import FakeDialog

        dialog = FakeDialog()
        dialog._menu_returns = ["port", "back"]
        dialog._inputbox_returns = ["999999"]
        h = self._make_handler(dialog)
        h._configure_endpoint()
        # Settings file untouched (default port still applies on load).
        assert load_maps_config().port == DEFAULT_PORT
        # Last msgbox carries the validation error.
        assert dialog.last_msgbox_title == "Invalid Port"

    def test_invalid_host_shows_msgbox_and_does_not_persist(
        self, isolated_config_dir
    ):
        from handler_test_utils import FakeDialog

        dialog = FakeDialog()
        dialog._menu_returns = ["host", "back"]
        dialog._inputbox_returns = ["not a host!"]
        h = self._make_handler(dialog)
        h._configure_endpoint()
        assert load_maps_config().host == DEFAULT_HOST
        assert dialog.last_msgbox_title == "Invalid Host"

    def test_invalid_timeout_shows_msgbox_and_does_not_persist(
        self, isolated_config_dir
    ):
        from handler_test_utils import FakeDialog

        dialog = FakeDialog()
        dialog._menu_returns = ["timeout", "back"]
        dialog._inputbox_returns = ["abc"]
        h = self._make_handler(dialog)
        h._configure_endpoint()
        assert load_maps_config().timeout == DEFAULT_TIMEOUT
        assert dialog.last_msgbox_title == "Invalid Timeout"

    def test_reset_restores_defaults_after_confirm(
        self, isolated_config_dir
    ):
        from handler_test_utils import FakeDialog

        save_maps_config(host="custom.lan", port=9000, timeout=10.0)
        dialog = FakeDialog()
        dialog._menu_returns = ["reset", "back"]
        dialog._yesno_returns = [True]
        h = self._make_handler(dialog)
        h._configure_endpoint()
        cfg = load_maps_config()
        assert cfg.host == DEFAULT_HOST
        assert cfg.port == DEFAULT_PORT
        assert cfg.timeout == DEFAULT_TIMEOUT

    def test_reset_aborted_keeps_existing_values(self, isolated_config_dir):
        from handler_test_utils import FakeDialog

        save_maps_config(host="custom.lan", port=9000, timeout=10.0)
        dialog = FakeDialog()
        dialog._menu_returns = ["reset", "back"]
        dialog._yesno_returns = [False]
        h = self._make_handler(dialog)
        h._configure_endpoint()
        cfg = load_maps_config()
        assert cfg.host == "custom.lan"
        assert cfg.port == 9000
        assert cfg.timeout == 10.0

    def test_blank_input_keeps_current_value(self, isolated_config_dir):
        """Pressing Enter on an empty inputbox should not write."""
        from handler_test_utils import FakeDialog

        save_maps_config(host="orig.lan", port=8000, timeout=3.5)
        dialog = FakeDialog()
        dialog._menu_returns = ["host", "back"]
        dialog._inputbox_returns = [""]
        h = self._make_handler(dialog)
        h._configure_endpoint()
        cfg = load_maps_config()
        assert cfg.host == "orig.lan"

    def test_status_format_renders_overridden_url(self, isolated_config_dir):
        """When the endpoint is overridden, the unavailable hint and the
        available render both reflect the configured host/port."""
        from utils.meshforge_maps_client import MapsServiceStatus

        save_maps_config(host="ops.lan", port=8443)
        # Unavailable variant
        out = _format_status(MapsServiceStatus(
            available=False, host="ops.lan", port=8443,
            error="connection refused",
        ))
        assert "NOT REACHABLE" in out
        # Available variant
        out2 = _format_status(MapsServiceStatus(
            available=True, host="ops.lan", port=8443, version="0.7",
        ))
        assert "http://ops.lan:8443" in out2


# ─────────────────────────────────────────────────────────────────────
# 4. Backward compat — Phase 6 behaviour preserved when no override exists
# ─────────────────────────────────────────────────────────────────────


class TestPhase6BackwardCompat:
    def test_handler_open_browser_uses_localhost_by_default(
        self, isolated_config_dir
    ):
        """Without a settings file, the handler still opens localhost:8808.
        This is the load-bearing non-breaking guarantee for Phase 6.3."""
        h = MeshforgeMapsHandler()
        ctx = MagicMock()
        ctx.wait_for_enter = MagicMock()
        h.ctx = ctx
        with patch(
            "launcher_tui.handlers.meshforge_maps.webbrowser.open"
        ) as mock_open:
            h._open_browser()
        mock_open.assert_called_once_with(f"http://{DEFAULT_HOST}:{DEFAULT_PORT}")

    def test_handler_open_browser_uses_overridden_url(
        self, isolated_config_dir
    ):
        save_maps_config(host="elsewhere.lan", port=9090)
        h = MeshforgeMapsHandler()
        ctx = MagicMock()
        ctx.wait_for_enter = MagicMock()
        h.ctx = ctx
        with patch(
            "launcher_tui.handlers.meshforge_maps.webbrowser.open"
        ) as mock_open:
            h._open_browser()
        mock_open.assert_called_once_with("http://elsewhere.lan:9090")
