"""Tests for RadioMenuHandler — Meshtastic Quick-Look extensions (MN-1b).

The MN-1b scope decision was: ship a small Meshtastic Quick-Look instead
of the full MeshForge meshtasticd config editor port. After auditing the
pre-existing ``RadioMenuHandler``, only three changes were needed:

  1. ``presets`` action was a dead-end delegating to a meshtasticd
     sub-handler that doesn't exist in MeshAnchor. Replaced with an
     inline preset picker driven by ``utils.lora_presets``.
  2. ``hw-config`` action had the same dead-end. Replaced with a help
     msgbox explaining the manual HAT-selection process and pointing at
     the meshtasticd web UI.
  3. New ``webui`` action surfaces the ``:9443`` URL with an xdg-open
     offer when ``$DISPLAY`` is set, or an SSH-tunnel hint when headless.

These tests exercise the three new code paths plus a regression guard
against the dead-end re-appearing.
"""

import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(SRC / "launcher_tui"))
sys.path.insert(0, os.path.dirname(__file__))

from handler_test_utils import FakeDialog, make_handler_context


def _make_handler():
    from handlers.radio_menu import RadioMenuHandler
    h = RadioMenuHandler()
    ctx = make_handler_context(src_dir=str(SRC))
    ctx.get_meshtastic_cli = lambda: "meshtastic"
    h.set_context(ctx)
    return h


# ── Preset picker (replaces dead-end "presets" delegation) ──────────


class TestPresetPicker:

    def test_back_returns_without_cli_call(self):
        h = _make_handler()
        h.ctx.dialog._menu_returns = ["back"]
        with patch.object(h, "_radio_run") as mock:
            h._radio_preset_picker()
            mock.assert_not_called()

    def test_choice_calls_meshtastic_set_modem_preset(self):
        h = _make_handler()
        # Pick LONG_FAST, confirm yes
        h.ctx.dialog._menu_returns = ["LONG_FAST"]
        h.ctx.dialog._yesno_returns = [True]
        with patch.object(h, "_radio_run") as mock:
            h._radio_preset_picker()
            mock.assert_called_once()
            cmd, _title = mock.call_args[0]
            assert "lora.modem_preset" in cmd
            assert "LONG_FAST" in cmd
            assert "--host" in cmd and "localhost" in cmd

    def test_user_decline_aborts_apply(self):
        h = _make_handler()
        h.ctx.dialog._menu_returns = ["LONG_FAST"]
        h.ctx.dialog._yesno_returns = [False]
        with patch.object(h, "_radio_run") as mock:
            h._radio_preset_picker()
            mock.assert_not_called()

    def test_menu_lists_all_meshtastic_presets(self):
        from utils.lora_presets import MESHTASTIC_PRESETS
        h = _make_handler()
        h.ctx.dialog._menu_returns = ["back"]
        h._radio_preset_picker()

        menu_calls = [c for c in h.ctx.dialog.calls if c[0] == "menu"]
        choices = menu_calls[0][1][2]
        choice_tags = {tag for tag, _label in choices}
        for preset_name in MESHTASTIC_PRESETS:
            assert preset_name in choice_tags, f"Preset {preset_name!r} missing from picker"

    def test_recommended_preset_marked_with_star(self):
        h = _make_handler()
        h.ctx.dialog._menu_returns = ["back"]
        h._radio_preset_picker()
        menu_calls = [c for c in h.ctx.dialog.calls if c[0] == "menu"]
        choices = menu_calls[0][1][2]
        # MEDIUM_FAST is the recommended preset per utils/lora_presets.py
        recommended = [
            label for tag, label in choices if tag == "MEDIUM_FAST"
        ]
        assert recommended, "MEDIUM_FAST not in picker"
        assert "*" in recommended[0]

    def test_warning_surfaced_when_present(self):
        """SHORT_TURBO has a regulatory warning that must appear in the
        confirm dialog so the operator sees it before applying."""
        h = _make_handler()
        h.ctx.dialog._menu_returns = ["SHORT_TURBO"]
        h.ctx.dialog._yesno_returns = [False]  # decline so no cli call
        with patch.object(h, "_radio_run"):
            h._radio_preset_picker()
        yesno_calls = [c for c in h.ctx.dialog.calls if c[0] == "yesno"]
        body = yesno_calls[0][1][1]
        assert "WARNING" in body
        assert "500" in body  # 500kHz hint from the warning text


# ── HAT help (replaces dead-end "hw-config" delegation) ─────────────


class TestHatHelp:

    def test_shows_help_msgbox(self):
        h = _make_handler()
        h._radio_hat_help()
        msgbox_calls = [c for c in h.ctx.dialog.calls if c[0] == "msgbox"]
        assert len(msgbox_calls) == 1
        title, body = msgbox_calls[0][1][0], msgbox_calls[0][1][1]
        assert title == "Hardware HAT Selection"
        # Must mention the canonical manual process + web UI alternative
        assert "available.d" in body
        assert "config.d" in body
        assert "9443" in body

    def test_does_not_call_meshtastic_cli(self):
        h = _make_handler()
        with patch.object(h, "_radio_run") as mock_run, \
             patch("subprocess.run") as mock_sp:
            h._radio_hat_help()
            mock_run.assert_not_called()
            mock_sp.assert_not_called()


# ── Web UI shortcut ────────────────────────────────────────────────


class TestWebUI:

    def test_headless_path_prints_url_and_tunnel_hint(self, monkeypatch):
        """No DISPLAY/WAYLAND_DISPLAY → operator gets the URL + an SSH
        tunnel hint, not a browser launch attempt."""
        monkeypatch.delenv("DISPLAY", raising=False)
        monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
        h = _make_handler()
        with patch("subprocess.Popen") as mock_pop:
            h._radio_open_webui()
            mock_pop.assert_not_called()
        msgbox_calls = [c for c in h.ctx.dialog.calls if c[0] == "msgbox"]
        body = msgbox_calls[0][1][1]
        assert "9443" in body
        assert "ssh -L" in body  # tunnel hint

    def test_display_path_offers_xdg_open(self, monkeypatch):
        monkeypatch.setenv("DISPLAY", ":0")
        h = _make_handler()
        h.ctx.dialog._yesno_returns = [True]  # accept the open offer
        with patch(
            "handlers.radio_menu.shutil.which", return_value="/usr/bin/xdg-open"
        ), patch("subprocess.Popen") as mock_pop:
            h._radio_open_webui()
            mock_pop.assert_called_once()
            cmd = mock_pop.call_args[0][0]
            assert cmd[0] == "xdg-open"
            assert "http://localhost:9443" in cmd

    def test_display_path_user_declines_open(self, monkeypatch):
        monkeypatch.setenv("DISPLAY", ":0")
        h = _make_handler()
        h.ctx.dialog._yesno_returns = [False]
        with patch(
            "handlers.radio_menu.shutil.which", return_value="/usr/bin/xdg-open"
        ), patch("subprocess.Popen") as mock_pop:
            h._radio_open_webui()
            mock_pop.assert_not_called()

    def test_xdg_open_failure_surfaces_url(self, monkeypatch):
        """If xdg-open errors, the URL must still reach the operator via
        msgbox so they can copy/paste it manually."""
        monkeypatch.setenv("DISPLAY", ":0")
        h = _make_handler()
        h.ctx.dialog._yesno_returns = [True]
        with patch(
            "handlers.radio_menu.shutil.which", return_value="/usr/bin/xdg-open"
        ), patch("subprocess.Popen", side_effect=OSError("nope")):
            h._radio_open_webui()
        # Must have at least one msgbox carrying the URL or the error
        all_msgbox_text = "".join(
            args[1] for kind, args, _ in h.ctx.dialog.calls if kind == "msgbox"
        )
        assert "9443" in all_msgbox_text or "xdg-open" in all_msgbox_text

    def test_no_display_when_xdg_open_missing(self, monkeypatch):
        """DISPLAY set but xdg-open not installed → headless path still
        applies (msgbox with URL + tunnel hint, no Popen attempt)."""
        monkeypatch.setenv("DISPLAY", ":0")
        h = _make_handler()
        with patch(
            "handlers.radio_menu.shutil.which", return_value=None
        ), patch("subprocess.Popen") as mock_pop:
            h._radio_open_webui()
            mock_pop.assert_not_called()


# ── Dispatch routing (regression guard) ────────────────────────────


class TestDispatchRouting:

    def test_presets_routes_to_inline_picker_not_dead_end(self):
        """Regression guard: the prior MN-1b implementation delegated
        'presets' to a nonexistent meshtasticd_radio sub-handler. Confirm
        the new dispatch entry routes to _radio_preset_picker (an inline
        method) instead of attempting cross-section dispatch."""
        h = _make_handler()
        # Build the choice list as the menu would, picking 'presets' then 'back'
        h.ctx.dialog._menu_returns = ["presets", "back"]
        with patch("subprocess.run") as mock_sp, \
             patch.object(h, "_radio_preset_picker") as mock_picker:
            # Patch the CLI version probe so the menu loop completes once
            mock_sp.return_value = MagicMock(returncode=0)
            h._radio_menu()
            mock_picker.assert_called_once()

    def test_hw_config_routes_to_inline_help_not_dead_end(self):
        h = _make_handler()
        h.ctx.dialog._menu_returns = ["hw-config", "back"]
        with patch("subprocess.run") as mock_sp, \
             patch.object(h, "_radio_hat_help") as mock_help:
            mock_sp.return_value = MagicMock(returncode=0)
            h._radio_menu()
            mock_help.assert_called_once()

    def test_webui_routes_to_inline_open(self):
        h = _make_handler()
        h.ctx.dialog._menu_returns = ["webui", "back"]
        with patch("subprocess.run") as mock_sp, \
             patch.object(h, "_radio_open_webui") as mock_open:
            mock_sp.return_value = MagicMock(returncode=0)
            h._radio_menu()
            mock_open.assert_called_once()

    def test_no_delegate_to_meshtasticd_method_remains(self):
        """The pre-MN-1b _delegate_to_meshtasticd method is gone — its
        entire reason for existing was the dead-end that this PR fixes.
        Catching its return would mean someone re-introduced the
        nonexistent meshtasticd_radio dispatch."""
        from handlers.radio_menu import RadioMenuHandler
        assert not hasattr(RadioMenuHandler, "_delegate_to_meshtasticd"), (
            "_delegate_to_meshtasticd was removed in MN-1b — its only "
            "purpose was the dead-end fixed in this PR. If a new use "
            "case appears, route through the registry directly instead."
        )

    def test_menu_includes_webui_choice(self):
        """The webui entry must show up in the rendered menu — guard
        against future refactors that drop the row."""
        h = _make_handler()
        h.ctx.dialog._menu_returns = ["back"]
        with patch("subprocess.run") as mock_sp:
            mock_sp.return_value = MagicMock(returncode=0)
            h._radio_menu()
        menu_calls = [c for c in h.ctx.dialog.calls if c[0] == "menu"]
        choices = menu_calls[0][1][2]
        tags = {tag for tag, _ in choices}
        assert "webui" in tags
