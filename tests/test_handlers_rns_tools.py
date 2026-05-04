"""
Unit tests for RNSToolsHandler (MN-3).

The handler is a thin wrapper around four read-only RNS CLI tools
(rnstatus / rnpath / rnid / rnprobe) plus inputbox-driven hash queries.
Tests cover:

* Structure: handler_id, menu_section, single-tag menu_items, "rns" flag.
* Dispatch: ``execute("tools")`` opens the submenu, all five sub-actions
  resolve to private methods.
* Hash validation: invalid hex / non-32-char input never reaches the
  subprocess layer.
* Subprocess plumbing: ``_run_and_print`` handles success, non-zero exit,
  TimeoutExpired, FileNotFoundError, and generic OSError without raising.
* Identity path: missing-identity early-exit prints a hint instead of
  invoking rnid.

These are smoke tests — they don't try to mock rnsd's runtime state.
"""

import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src', 'launcher_tui'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.dirname(__file__))

from handler_test_utils import FakeDialog, make_handler_context


def _make_handler():
    from handlers.rns_tools import RNSToolsHandler
    h = RNSToolsHandler()
    ctx = make_handler_context()
    h.set_context(ctx)
    return h


# ── Structure ───────────────────────────────────────────────────────


class TestRNSToolsStructure:

    def test_handler_id(self):
        h = _make_handler()
        assert h.handler_id == "rns_tools"

    def test_menu_section(self):
        h = _make_handler()
        assert h.menu_section == "rns"

    def test_menu_items_single_tag(self):
        h = _make_handler()
        items = h.menu_items()
        assert len(items) == 1
        tag, desc, flag = items[0]
        assert tag == "tools"
        assert flag == "rns"
        # Description should hint at what's distinct about this submenu
        # (round-trip probe + hash lookup) so users can tell it apart
        # from the inline RNS items in rns_menu.
        assert "Advanced" in desc or "round-trip" in desc.lower()

    def test_execute_unknown_action_does_not_raise(self):
        h = _make_handler()
        h.execute("nonexistent")


# ── Dispatch ────────────────────────────────────────────────────────


class TestRNSToolsDispatch:

    def test_execute_tools_opens_submenu(self):
        h = _make_handler()
        with patch.object(h, '_tools_menu') as mock:
            h.execute("tools")
            mock.assert_called_once()

    def test_tools_menu_back_exits(self):
        h = _make_handler()
        h.ctx.dialog._menu_returns = ["back"]
        h._tools_menu()
        # Should have shown the menu exactly once before exiting
        menu_calls = [c for c in h.ctx.dialog.calls if c[0] == "menu"]
        assert len(menu_calls) == 1

    def test_tools_menu_none_exits(self):
        """User pressing Esc/Cancel returns None — must exit cleanly."""
        h = _make_handler()
        h.ctx.dialog._menu_returns = [None]
        h._tools_menu()

    def test_tools_menu_status_dispatches(self):
        h = _make_handler()
        h.ctx.dialog._menu_returns = ["status", "back"]
        with patch.object(h, '_show_status') as mock:
            h._tools_menu()
            mock.assert_called_once()

    def test_tools_menu_paths_dispatches(self):
        h = _make_handler()
        h.ctx.dialog._menu_returns = ["paths", "back"]
        with patch.object(h, '_show_paths') as mock:
            h._tools_menu()
            mock.assert_called_once()

    def test_tools_menu_lookup_dispatches(self):
        h = _make_handler()
        h.ctx.dialog._menu_returns = ["lookup", "back"]
        with patch.object(h, '_lookup_path') as mock:
            h._tools_menu()
            mock.assert_called_once()

    def test_tools_menu_identity_dispatches(self):
        h = _make_handler()
        h.ctx.dialog._menu_returns = ["identity", "back"]
        with patch.object(h, '_show_gateway_identity') as mock:
            h._tools_menu()
            mock.assert_called_once()

    def test_tools_menu_probe_dispatches(self):
        h = _make_handler()
        h.ctx.dialog._menu_returns = ["probe", "back"]
        with patch.object(h, '_probe_destination') as mock:
            h._tools_menu()
            mock.assert_called_once()


# ── Hash validation ────────────────────────────────────────────────


class TestRNSToolsHashValidation:

    def test_lookup_rejects_short_hash(self):
        h = _make_handler()
        h.ctx.dialog._inputbox_returns = ["abc123"]
        with patch.object(h, '_run_and_print') as mock:
            h._lookup_path()
            mock.assert_not_called()
        assert h.ctx.dialog.last_msgbox_title == "Invalid Hash"

    def test_lookup_rejects_non_hex(self):
        h = _make_handler()
        # 32 chars but with a 'g'
        h.ctx.dialog._inputbox_returns = ["g" * 32]
        with patch.object(h, '_run_and_print') as mock:
            h._lookup_path()
            mock.assert_not_called()
        assert h.ctx.dialog.last_msgbox_title == "Invalid Hash"

    def test_lookup_accepts_valid_hash(self):
        h = _make_handler()
        valid = "0123456789abcdef0123456789abcdef"
        h.ctx.dialog._inputbox_returns = [valid]
        with patch.object(h, '_run_and_print', return_value=0) as mock, \
             patch.object(h.ctx, 'wait_for_enter'):
            h._lookup_path()
            mock.assert_called_once()
            args, _ = mock.call_args
            assert args[0] == ["rnpath", valid]

    def test_lookup_lowercases_hash(self):
        h = _make_handler()
        h.ctx.dialog._inputbox_returns = ["ABCDEF" + "0" * 26]
        with patch.object(h, '_run_and_print', return_value=0) as mock, \
             patch.object(h.ctx, 'wait_for_enter'):
            h._lookup_path()
            args, _ = mock.call_args
            # rnpath should receive lowercase
            assert args[0][1] == ("ABCDEF" + "0" * 26).lower()

    def test_lookup_empty_input_returns_silently(self):
        h = _make_handler()
        h.ctx.dialog._inputbox_returns = [""]
        with patch.object(h, '_run_and_print') as mock:
            h._lookup_path()
            mock.assert_not_called()

    def test_probe_rejects_short_hash(self):
        h = _make_handler()
        h.ctx.dialog._inputbox_returns = ["deadbeef"]
        with patch.object(h, '_run_and_print') as mock:
            h._probe_destination()
            mock.assert_not_called()

    def test_probe_invokes_rnprobe_with_lxmf_aspect(self):
        h = _make_handler()
        valid = "0123456789abcdef0123456789abcdef"
        h.ctx.dialog._inputbox_returns = [valid]
        with patch.object(h, '_run_and_print', return_value=0) as mock, \
             patch.object(h.ctx, 'wait_for_enter'):
            h._probe_destination()
            args, _ = mock.call_args
            cmd = args[0]
            assert cmd[0] == "rnprobe"
            assert "lxmf.delivery" in cmd
            assert valid in cmd


# ── Subprocess plumbing ────────────────────────────────────────────


class TestRNSToolsSubprocess:

    def test_run_and_print_success(self, capsys):
        h = _make_handler()
        fake_proc = MagicMock(returncode=0, stdout="ok\n", stderr="")
        with patch("subprocess.run", return_value=fake_proc):
            rc = h._run_and_print(["rnstatus"], timeout=5)
        assert rc == 0
        out = capsys.readouterr().out
        assert "ok" in out

    def test_run_and_print_nonzero_exit_prints_code(self, capsys):
        h = _make_handler()
        fake_proc = MagicMock(returncode=2, stdout="", stderr="boom\n")
        with patch("subprocess.run", return_value=fake_proc):
            rc = h._run_and_print(["rnstatus"], timeout=5)
        assert rc == 2
        out = capsys.readouterr().out
        assert "exit code 2" in out
        assert "boom" in out

    def test_run_and_print_timeout(self, capsys):
        h = _make_handler()
        with patch(
            "subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="rnstatus", timeout=5),
        ):
            rc = h._run_and_print(["rnstatus"], timeout=5)
        assert rc == -1
        assert "timed out" in capsys.readouterr().out

    def test_run_and_print_command_not_found(self, capsys):
        h = _make_handler()
        with patch("subprocess.run", side_effect=FileNotFoundError()):
            rc = h._run_and_print(["rnstatus"], timeout=5)
        assert rc == -1
        out = capsys.readouterr().out
        assert "not found" in out
        assert "Install RNS" in out

    def test_run_and_print_oserror(self, capsys):
        h = _make_handler()
        with patch("subprocess.run", side_effect=OSError("nope")):
            rc = h._run_and_print(["rnstatus"], timeout=5)
        assert rc == -1
        assert "Failed to run" in capsys.readouterr().out


# ── Gateway identity path ──────────────────────────────────────────


class TestRNSToolsGatewayIdentity:

    def test_missing_identity_shows_hint_no_subprocess(self, capsys):
        h = _make_handler()
        # Patch the canonical resolver to point at a path that doesn't exist
        fake_path = Path("/tmp/_meshanchor_test_does_not_exist/gateway_identity")
        with patch("handlers.rns_tools.get_identity_path", return_value=fake_path), \
             patch.object(h.ctx, 'wait_for_enter'), \
             patch("subprocess.run") as mock_run:
            h._show_gateway_identity()
            mock_run.assert_not_called()
        out = capsys.readouterr().out
        assert "No gateway identity found" in out

    def test_existing_identity_runs_rnid(self, tmp_path, capsys):
        h = _make_handler()
        fake_identity = tmp_path / "gateway_identity"
        fake_identity.write_bytes(b"\x00" * 32)
        fake_proc = MagicMock(returncode=0, stdout="hash\n", stderr="")
        with patch("handlers.rns_tools.get_identity_path", return_value=fake_identity), \
             patch.object(h.ctx, 'wait_for_enter'), \
             patch("subprocess.run", return_value=fake_proc) as mock_run:
            h._show_gateway_identity()
            mock_run.assert_called_once()
            cmd = mock_run.call_args[0][0]
            assert cmd[0] == "rnid"
            assert "lxmf.delivery" in cmd
            assert str(fake_identity) in cmd


# ── Registry integration ──────────────────────────────────────────


class TestRNSToolsRegistry:

    def test_handler_registered(self):
        from handlers import get_all_handlers
        names = [cls.__name__ for cls in get_all_handlers()]
        assert "RNSToolsHandler" in names

    def test_handler_visible_in_rns_section_when_flag_on(self):
        from handler_protocol import TUIContext
        from handler_registry import HandlerRegistry
        from handlers import get_all_handlers

        ctx = TUIContext(dialog=FakeDialog(), feature_flags={"rns": True})
        reg = HandlerRegistry(ctx)
        for cls in get_all_handlers():
            reg.register(cls())
        tags = [tag for tag, _desc in reg.get_menu_items("rns")]
        assert "tools" in tags

    def test_handler_hidden_when_rns_flag_off(self):
        from handler_protocol import TUIContext
        from handler_registry import HandlerRegistry
        from handlers import get_all_handlers

        ctx = TUIContext(dialog=FakeDialog(), feature_flags={"rns": False})
        reg = HandlerRegistry(ctx)
        for cls in get_all_handlers():
            reg.register(cls())
        tags = [tag for tag, _desc in reg.get_menu_items("rns")]
        assert "tools" not in tags
