"""Tests for the gateway preflight template module (MN-2).

Covers template loading, live-state capture, drift comparison, and export.
External services (RNS / LXMF / meshtasticd / mosquitto) are mocked so tests
run offline.
"""

import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(SRC / "launcher_tui"))


# ── load_default_template / list_templates ────────────────────────


class TestTemplateLoading:

    def test_load_default_returns_none_when_dir_missing(self, tmp_path, monkeypatch):
        from handlers import _gateway_preflight_template as tmpl
        monkeypatch.setattr(tmpl, "TEMPLATE_DIR", tmp_path / "missing")
        assert tmpl.load_default_template() is None
        assert tmpl.list_templates() == []

    def test_load_default_returns_none_when_dir_empty(self, tmp_path, monkeypatch):
        from handlers import _gateway_preflight_template as tmpl
        empty = tmp_path / "templates"
        empty.mkdir()
        monkeypatch.setattr(tmpl, "TEMPLATE_DIR", empty)
        assert tmpl.load_default_template() is None
        assert tmpl.list_templates() == []

    def test_load_default_picks_first_alphabetically(self, tmp_path, monkeypatch):
        from handlers import _gateway_preflight_template as tmpl
        d = tmp_path / "templates"
        d.mkdir()
        (d / "z_last.json").write_text(json.dumps({"name": "z"}))
        (d / "a_first.json").write_text(json.dumps({"name": "a"}))
        monkeypatch.setattr(tmpl, "TEMPLATE_DIR", d)
        loaded = tmpl.load_default_template()
        assert loaded == {"name": "a"}

    def test_load_default_handles_corrupt_json(self, tmp_path, monkeypatch):
        from handlers import _gateway_preflight_template as tmpl
        d = tmp_path / "templates"
        d.mkdir()
        (d / "bad.json").write_text("{ this is not json")
        monkeypatch.setattr(tmpl, "TEMPLATE_DIR", d)
        assert tmpl.load_default_template() is None


# ── capture_live_state ─────────────────────────────────────────────


class TestCaptureLiveState:

    def _patch_externals(self, monkeypatch, tmp_path,
                         rns_present=True, lxmf_present=True,
                         services_active=True, shared_instance_available=True):
        from handlers import _gateway_preflight_template as tmpl

        rns_mod = MagicMock(__version__="1.1.4")
        lxmf_mod = MagicMock(__version__="0.9.4")

        def fake_safe_import(name):
            if name == "RNS":
                return (rns_mod, rns_present)
            if name == "LXMF":
                return (lxmf_mod, lxmf_present)
            return (None, False)

        fake_status = MagicMock(available=services_active)
        fake_info = {"available": shared_instance_available, "detail": "unix-socket"}

        monkeypatch.setattr(tmpl, "safe_import", fake_safe_import)
        monkeypatch.setattr(tmpl, "check_service", lambda _: fake_status)
        monkeypatch.setattr(tmpl, "get_rns_shared_instance_info", lambda: fake_info)
        # Pin home so the function looks for ~/.config/meshanchor and .nomadnetwork
        # under tmp_path instead of the real user home.
        monkeypatch.setattr(
            "utils.paths.MeshAnchorPaths.get_config_dir",
            classmethod(lambda cls: tmp_path / ".config" / "meshanchor"),
        )
        monkeypatch.setattr(
            "utils.paths.get_real_user_home", lambda: tmp_path
        )

    def test_capture_with_no_meshtastic_info(self, tmp_path, monkeypatch):
        from handlers import _gateway_preflight_template as tmpl
        self._patch_externals(monkeypatch, tmp_path)
        state = tmpl.capture_live_state(info_text=None)
        assert "captured_at" in state
        # Meshtastic block stays empty when info_text is None
        assert state["meshtastic"] == {}
        # Packages always populated regardless
        assert state["packages"]["rns"]["installed"] is True
        assert state["packages"]["lxmf"]["installed"] is True
        # Services were patched to active
        assert state["services"]["meshtasticd"] == "active"

    def test_capture_extracts_region_preset_channel(self, tmp_path, monkeypatch):
        from handlers import _gateway_preflight_template as tmpl
        self._patch_externals(monkeypatch, tmp_path)
        info_text = (
            '"region": "US",'
            '"modemPreset": "LONG_FAST",'
            '"channelNum": 20,'
            'Index 0: PRIMARY { "name": "meshanchor", "uplinkEnabled": true, "downlinkEnabled": true }'
        )
        state = tmpl.capture_live_state(info_text=info_text)
        assert state["meshtastic"]["region"] == "US"
        assert state["meshtastic"]["modem_preset"] == "LONG_FAST"
        assert state["meshtastic"]["channel_num"] == 20
        assert any(c["name"] == "meshanchor"
                   for c in state["meshtastic"]["bridge_channels"])

    def test_capture_reads_gateway_config(self, tmp_path, monkeypatch):
        from handlers import _gateway_preflight_template as tmpl
        self._patch_externals(monkeypatch, tmp_path)
        cfg_dir = tmp_path / ".config" / "meshanchor"
        cfg_dir.mkdir(parents=True)
        (cfg_dir / "gateway.json").write_text(json.dumps({
            "bridge_mode": "meshcore_bridge",
            "mqtt_bridge": {"channel": "LongFast", "region": "US"},
            "rns": {"default_lxmf_destination": "abc123"},
        }))
        state = tmpl.capture_live_state()
        assert state["gateway"]["bridge_mode"] == "meshcore_bridge"
        assert state["gateway"]["mqtt_channel"] == "LongFast"
        assert state["gateway"]["default_lxmf_destination"] == "abc123"
        assert state["gateway"]["default_lxmf_destination_set"] is True

    def test_capture_reads_nomadnet_logfile(self, tmp_path, monkeypatch):
        from handlers import _gateway_preflight_template as tmpl
        self._patch_externals(monkeypatch, tmp_path)
        nn = tmp_path / ".nomadnetwork"
        nn.mkdir()
        (nn / "logfile").write_text(
            "[Notice] LXMF Router ready to receive on: <" + "f" * 32 + ">\n"
        )
        state = tmpl.capture_live_state()
        assert state["nomadnet"]["lxmf_identity"] == "f" * 32

    def test_capture_when_packages_missing(self, tmp_path, monkeypatch):
        from handlers import _gateway_preflight_template as tmpl
        self._patch_externals(monkeypatch, tmp_path,
                              rns_present=False, lxmf_present=False)
        state = tmpl.capture_live_state()
        assert state["packages"]["rns"]["installed"] is False
        assert state["packages"]["lxmf"]["installed"] is False


# ── check_template_drift ───────────────────────────────────────────


class TestCheckTemplateDrift:

    def test_returns_empty_for_metadata_only_template(self):
        from handlers import _gateway_preflight_template as tmpl
        template = {"name": "minimal", "description": "metadata only"}
        results = tmpl.check_template_drift(template, {})
        assert results == []

    def test_service_match_is_ok(self):
        from handlers import _gateway_preflight_template as tmpl
        template = {"services": {"rnsd": {"expected": "active"}}}
        live = {"services": {"rnsd": "active"}}
        results = tmpl.check_template_drift(template, live)
        assert len(results) == 1
        glyph, msg, fix = results[0]
        assert "active" in msg
        assert fix is None

    def test_service_mismatch_has_systemctl_hint(self):
        from handlers import _gateway_preflight_template as tmpl
        template = {"services": {"rnsd": {"expected": "active"}}}
        live = {"services": {"rnsd": "inactive"}}
        results = tmpl.check_template_drift(template, live)
        glyph, msg, fix = results[0]
        assert "expected active" in msg
        assert fix is not None and "systemctl start" in fix

    def test_package_min_version_pass(self):
        from handlers import _gateway_preflight_template as tmpl
        template = {"packages": {"rns": {"min_version": "1.0.0"}}}
        live = {"packages": {"rns": {"installed": True, "version": "1.1.4"}}}
        results = tmpl.check_template_drift(template, live)
        _, msg, fix = results[0]
        assert ">= 1.0.0" in msg
        assert fix is None

    def test_package_min_version_fail(self):
        from handlers import _gateway_preflight_template as tmpl
        template = {"packages": {"rns": {"min_version": "2.0.0", "install": "pip3 install rns"}}}
        live = {"packages": {"rns": {"installed": True, "version": "1.1.4"}}}
        results = tmpl.check_template_drift(template, live)
        _, msg, fix = results[0]
        assert ">= 2.0.0" in msg
        assert fix == "pip3 install rns"

    def test_meshtastic_bridge_channel_name_match(self):
        from handlers import _gateway_preflight_template as tmpl
        template = {"meshtastic": {"bridge_channel_name": {"expected": "meshanchor"}}}
        live = {"meshtastic": {"bridge_channels": [{"index": 2, "name": "meshanchor"}]}}
        results = tmpl.check_template_drift(template, live)
        _, msg, fix = results[0]
        assert "'meshanchor'" in msg and "present" in msg
        assert fix is None

    def test_nomadnet_identity_in_recipient_list(self):
        from handlers import _gateway_preflight_template as tmpl
        template = {
            "nomadnet": {
                "identity_matches_default_lxmf_destination": {"expected": True}
            }
        }
        nomad_hash = "f" * 32
        live = {
            "nomadnet": {"lxmf_identity": nomad_hash},
            "gateway": {"default_lxmf_destination": ["a" * 32, nomad_hash]},
        }
        results = tmpl.check_template_drift(template, live)
        _, msg, fix = results[0]
        assert "match" in msg.lower()
        assert "2 recipients" in msg
        assert fix is None

    def test_severity_warn_uses_warn_glyph(self):
        from handlers import _gateway_preflight_template as tmpl
        template = {"services": {"mosquitto": {"expected": "active", "severity": "warn"}}}
        live = {"services": {"mosquitto": "inactive"}}
        results = tmpl.check_template_drift(template, live)
        glyph, _msg, _fix = results[0]
        # Warn glyph contains the yellow ANSI escape; fail glyph contains red
        assert "\033[33m" in glyph


# ── export_current_as_template ─────────────────────────────────────


class TestExport:

    def test_export_writes_timestamped_file(self, tmp_path):
        from handlers import _gateway_preflight_template as tmpl
        live = {"captured_at": "2026-05-04T00:00:00", "meshtastic": {}}
        target = tmpl.export_current_as_template(live, target_dir=tmp_path)
        assert target.exists()
        assert target.parent == tmp_path
        # Filename should start with "exported_" and end with ".json"
        assert target.name.startswith("exported_")
        assert target.suffix == ".json"
        loaded = json.loads(target.read_text())
        assert loaded == live

    def test_export_default_target_uses_meshanchor_config_dir(self, tmp_path, monkeypatch):
        from handlers import _gateway_preflight_template as tmpl
        monkeypatch.setattr(
            "utils.paths.MeshAnchorPaths.get_config_dir",
            classmethod(lambda cls: tmp_path / ".config" / "meshanchor"),
        )
        target = tmpl.export_current_as_template({"captured_at": "x"})
        assert ".config/meshanchor/templates" in str(target)
        assert target.exists()


# ── run_meshtastic_info ────────────────────────────────────────────


class TestRunMeshtasticInfo:

    def test_returns_stdout_on_success(self):
        from handlers import _gateway_preflight_template as tmpl
        fake_proc = MagicMock(returncode=0, stdout="ok")
        with patch("subprocess.run", return_value=fake_proc):
            assert tmpl.run_meshtastic_info() == "ok"

    def test_returns_none_on_nonzero_exit(self):
        from handlers import _gateway_preflight_template as tmpl
        fake_proc = MagicMock(returncode=1, stdout="")
        with patch("subprocess.run", return_value=fake_proc):
            assert tmpl.run_meshtastic_info() is None

    def test_returns_none_when_meshtastic_not_installed(self):
        from handlers import _gateway_preflight_template as tmpl
        with patch("subprocess.run", side_effect=FileNotFoundError()):
            assert tmpl.run_meshtastic_info() is None
