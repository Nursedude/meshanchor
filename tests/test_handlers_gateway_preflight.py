"""Tests for the Gateway Pre-Flight handler (MN-2).

Ported from MeshForge tests/test_gateway_preflight.py with MeshAnchor path
adjustments (~/.config/meshforge/ → ~/.config/meshanchor/) and patched-symbol
target updates (handlers.gateway_preflight._gateway_config_path,
_gateway_identity_path).

Verifies handler registration and individual check functions. Mocks external
services so tests run offline.
"""

import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Put src/launcher_tui on path so `from handlers import ...` works
SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(SRC / "launcher_tui"))
sys.path.insert(0, os.path.dirname(__file__))

from handler_test_utils import FakeDialog, make_handler_context


def _make_handler():
    from handlers.gateway_preflight import GatewayPreflightHandler
    h = GatewayPreflightHandler()
    ctx = make_handler_context()
    h.set_context(ctx)
    return h


# ── Structure / registration ────────────────────────────────────────


class TestGatewayPreflightStructure:

    def test_handler_registered(self):
        from handlers import get_all_handlers
        names = [c.__name__ for c in get_all_handlers()]
        assert "GatewayPreflightHandler" in names

    def test_handler_protocol_contract(self):
        from handlers.gateway_preflight import GatewayPreflightHandler
        h = GatewayPreflightHandler()
        assert h.handler_id == "gateway_preflight"
        assert h.menu_section == "mesh_networks"
        items = h.menu_items()
        actions = [i[0] for i in items]
        assert "preflight" in actions
        assert "export" in actions

    def test_all_menu_items_gated_on_gateway_flag(self):
        from handlers.gateway_preflight import GatewayPreflightHandler
        h = GatewayPreflightHandler()
        for tag, _desc, flag in h.menu_items():
            assert flag == "gateway", f"Tag {tag!r} should be gated on 'gateway', got {flag!r}"

    def test_execute_unknown_action_is_safe(self):
        h = _make_handler()
        h.execute("nonexistent")  # should not raise


# ── _check_lxmf ────────────────────────────────────────────────────


class TestCheckLxmf:

    def test_present(self, monkeypatch):
        h = _make_handler()
        fake_rns = MagicMock(__version__="1.1.4")
        fake_lxmf = MagicMock(__version__="0.9.4")

        def fake_safe_import(name):
            return {"RNS": (fake_rns, True), "LXMF": (fake_lxmf, True)}[name]

        monkeypatch.setattr("handlers.gateway_preflight.safe_import", fake_safe_import)
        status, msg, fix = h._check_lxmf()
        assert "1.1.4" in msg and "0.9.4" in msg
        assert fix is None

    def test_lxmf_missing(self, monkeypatch):
        h = _make_handler()

        def fake_safe_import(name):
            if name == "RNS":
                return (MagicMock(), True)
            return (None, False)

        monkeypatch.setattr("handlers.gateway_preflight.safe_import", fake_safe_import)
        status, msg, fix = h._check_lxmf()
        assert "lxmf" in msg.lower()
        assert fix is not None and "pip3 install" in fix

    def test_both_missing(self, monkeypatch):
        h = _make_handler()
        monkeypatch.setattr(
            "handlers.gateway_preflight.safe_import",
            lambda name: (None, False),
        )
        status, msg, fix = h._check_lxmf()
        assert "rns" in msg.lower() and "lxmf" in msg.lower()
        assert fix is not None


# ── _check_channel_uplink ──────────────────────────────────────────


class TestCheckChannelUplink:

    def test_parsing_extracts_uplinked_channel(self):
        h = _make_handler()
        fake_info = '''Primary channel URL: ...
Channels:
  Index 0: PRIMARY psk=default { "psk": "AQ==", "name": "", "uplinkEnabled": false, "downlinkEnabled": false }
  Index 2: SECONDARY psk=secret { "psk": "xxx", "name": "meshanchor", "uplinkEnabled": true, "downlinkEnabled": true }
'''
        with patch.object(h, "_run_meshtastic_info", return_value=fake_info):
            (status, msg, _fix), uplinked = h._check_channel_uplink()
        assert uplinked == ["meshanchor"]
        assert "meshanchor" in msg

    def test_none_enabled_is_fail_with_fix_hint(self):
        h = _make_handler()
        fake_info = '''Channels:
  Index 0: PRIMARY psk=default { "name": "", "uplinkEnabled": false, "downlinkEnabled": false }
'''
        with patch.object(h, "_run_meshtastic_info", return_value=fake_info):
            (status, msg, fix), uplinked = h._check_channel_uplink()
        assert uplinked == []
        assert fix is not None and "uplink_enabled" in fix

    def test_meshtastic_info_unreachable_returns_warn(self):
        """When meshtastic --info fails, channel check warns instead of failing."""
        h = _make_handler()
        with patch.object(h, "_run_meshtastic_info", return_value=None):
            (status, msg, fix), uplinked = h._check_channel_uplink()
        assert uplinked == []
        assert "could not query" in msg.lower()


# ── _check_gateway_config_channel ──────────────────────────────────


class TestCheckGatewayConfigChannel:

    def test_mismatch_is_fail(self, tmp_path, monkeypatch):
        h = _make_handler()
        cfg_path = tmp_path / "gateway.json"
        cfg_path.write_text(json.dumps({
            "mqtt_bridge": {"channel": "LongFast"},
            "meshtastic": {"mqtt_channel": "LongFast"},
        }))
        monkeypatch.setattr(
            "handlers.gateway_preflight._gateway_config_path", lambda: cfg_path
        )
        status, msg, fix = h._check_gateway_config_channel(["meshanchor"])
        assert "LongFast" in msg and "meshanchor" in msg
        assert fix is not None

    def test_match_is_ok(self, tmp_path, monkeypatch):
        h = _make_handler()
        cfg_path = tmp_path / "gateway.json"
        cfg_path.write_text(json.dumps({"mqtt_bridge": {"channel": "meshanchor"}}))
        monkeypatch.setattr(
            "handlers.gateway_preflight._gateway_config_path", lambda: cfg_path
        )
        status, msg, fix = h._check_gateway_config_channel(["meshanchor"])
        assert fix is None
        assert "matches" in msg.lower() or "meshanchor" in msg

    def test_missing_config_file_is_warn(self, tmp_path, monkeypatch):
        h = _make_handler()
        missing = tmp_path / "no-such-gateway.json"
        monkeypatch.setattr(
            "handlers.gateway_preflight._gateway_config_path", lambda: missing
        )
        status, msg, fix = h._check_gateway_config_channel([])
        assert "not found" in msg.lower()


# ── _check_nomadnet_identity_match ─────────────────────────────────


class TestCheckNomadnetIdentityMatch:

    def test_match(self, tmp_path, monkeypatch):
        h = _make_handler()
        cfg_path = tmp_path / "gateway.json"
        cfg_path.write_text(json.dumps({
            "rns": {"default_lxmf_destination": "d69f7e802960b39561768588fc6e6082"},
        }))
        (tmp_path / ".nomadnetwork").mkdir()
        (tmp_path / ".nomadnetwork" / "logfile").write_text(
            "[2026-04-18 08:00] [Notice] LXMF Router ready to receive on: <d69f7e802960b39561768588fc6e6082>\n"
        )
        monkeypatch.setattr(
            "handlers.gateway_preflight._gateway_config_path", lambda: cfg_path
        )
        monkeypatch.setattr(
            "handlers.gateway_preflight.get_real_user_home", lambda: tmp_path
        )
        status, msg, fix = h._check_nomadnet_identity_match()
        assert fix is None
        assert "matches" in msg.lower()

    def test_mismatch(self, tmp_path, monkeypatch):
        h = _make_handler()
        cfg_path = tmp_path / "gateway.json"
        cfg_path.write_text(json.dumps({
            "rns": {"default_lxmf_destination": "a" * 32},
        }))
        (tmp_path / ".nomadnetwork").mkdir()
        (tmp_path / ".nomadnetwork" / "logfile").write_text(
            "LXMF Router ready to receive on: <" + "b" * 32 + ">\n"
        )
        monkeypatch.setattr(
            "handlers.gateway_preflight._gateway_config_path", lambda: cfg_path
        )
        monkeypatch.setattr(
            "handlers.gateway_preflight.get_real_user_home", lambda: tmp_path
        )
        status, msg, fix = h._check_nomadnet_identity_match()
        assert fix is not None and "update" in fix.lower()


# ── _check_gateway_identity ────────────────────────────────────────


class TestCheckGatewayIdentity:

    def test_identity_missing_is_warn(self, tmp_path, monkeypatch):
        h = _make_handler()
        missing = tmp_path / "gateway_identity"
        monkeypatch.setattr(
            "handlers.gateway_preflight._gateway_identity_path", lambda: missing
        )
        status, msg, fix = h._check_gateway_identity()
        assert "not found" in msg.lower()
        assert fix is not None

    def test_identity_present_no_rns_module_is_warn(self, tmp_path, monkeypatch):
        h = _make_handler()
        existing = tmp_path / "gateway_identity"
        existing.write_bytes(b"\x00" * 32)
        monkeypatch.setattr(
            "handlers.gateway_preflight._gateway_identity_path", lambda: existing
        )
        monkeypatch.setattr(
            "handlers.gateway_preflight.safe_import",
            lambda name: (None, False),
        )
        status, msg, fix = h._check_gateway_identity()
        assert "RNS not importable" in msg


# ── Registry integration ──────────────────────────────────────────


class TestGatewayPreflightRegistry:

    def test_visible_when_gateway_flag_on(self):
        from handler_protocol import TUIContext
        from handler_registry import HandlerRegistry
        from handlers import get_all_handlers

        ctx = TUIContext(dialog=FakeDialog(), feature_flags={"gateway": True})
        reg = HandlerRegistry(ctx)
        for cls in get_all_handlers():
            reg.register(cls())
        tags = [tag for tag, _desc in reg.get_menu_items("mesh_networks")]
        assert "preflight" in tags
        assert "export" in tags

    def test_hidden_when_gateway_flag_off(self):
        from handler_protocol import TUIContext
        from handler_registry import HandlerRegistry
        from handlers import get_all_handlers

        ctx = TUIContext(dialog=FakeDialog(), feature_flags={"gateway": False})
        reg = HandlerRegistry(ctx)
        for cls in get_all_handlers():
            reg.register(cls())
        tags = [tag for tag, _desc in reg.get_menu_items("mesh_networks")]
        assert "preflight" not in tags
        assert "export" not in tags
