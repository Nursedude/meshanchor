"""Tests for MeshCoreCLIHandler — meshcore-cli passthrough + daemon handoff.

The handler's load-bearing invariant is the daemon ownership handoff: when
``meshanchor-daemon.service`` is active it owns the radio (Issue #33), so
running ``meshcore-cli`` must stop the daemon, run the CLI, and restart
the daemon — even if the CLI fails. These tests pin that flow plus the
connection-arg builder for serial / tcp / ble configs.
"""

from __future__ import annotations

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

from handler_test_utils import FakeDialog, make_handler_context  # noqa: E402


def _make_handler():
    from handlers.meshcore_cli import MeshCoreCLIHandler
    h = MeshCoreCLIHandler()
    ctx = make_handler_context(src_dir=str(SRC))
    h.set_context(ctx)
    return h


def _serial_config(**overrides):
    cfg = SimpleNamespace(
        connection_type="serial",
        device_path="/dev/ttyUSB0",
        baud_rate=115200,
        tcp_host="",
        tcp_port=4000,
    )
    for k, v in overrides.items():
        setattr(cfg, k, v)
    return cfg


def _tcp_config(**overrides):
    cfg = SimpleNamespace(
        connection_type="tcp",
        device_path="",
        baud_rate=115200,
        tcp_host="meshcore.local",
        tcp_port=5000,
    )
    for k, v in overrides.items():
        setattr(cfg, k, v)
    return cfg


def _ble_config(**overrides):
    cfg = SimpleNamespace(
        connection_type="ble",
        device_path="MeshCore-AB12",
        baud_rate=115200,
        tcp_host="",
        tcp_port=4000,
    )
    for k, v in overrides.items():
        setattr(cfg, k, v)
    return cfg


# ── find_meshcore_cli ────────────────────────────────────────────────


class TestFindMeshcoreCli:
    def test_returns_path_from_which(self, tmp_path):
        from utils import cli as cli_mod
        with patch.object(cli_mod, "shutil") as fake_shutil:
            fake_shutil.which.return_value = "/usr/local/bin/meshcore-cli"
            result = cli_mod.find_meshcore_cli()
        assert result == "/usr/local/bin/meshcore-cli"

    def test_returns_none_when_missing(self):
        from utils import cli as cli_mod
        with patch.object(cli_mod, "shutil") as fake_shutil, \
             patch("os.path.isfile", return_value=False), \
             patch("os.access", return_value=False), \
             patch("os.environ", {}):
            fake_shutil.which.return_value = None
            with patch.object(Path, "is_dir", return_value=False):
                result = cli_mod.find_meshcore_cli()
        assert result is None


# ── _connection_args ─────────────────────────────────────────────────


class TestConnectionArgs:
    def test_serial_includes_device_and_baud(self):
        h = _make_handler()
        with patch.object(h, "_meshcore_config", return_value=_serial_config()):
            args = h._connection_args()
        assert args == ["-s", "/dev/ttyUSB0", "-b", "115200"]

    def test_tcp_includes_host_and_port(self):
        h = _make_handler()
        with patch.object(h, "_meshcore_config", return_value=_tcp_config()):
            args = h._connection_args()
        assert args == ["-t", "meshcore.local", "-p", "5000"]

    def test_ble_uses_dash_a(self):
        h = _make_handler()
        with patch.object(h, "_meshcore_config", return_value=_ble_config()):
            args = h._connection_args()
        assert args == ["-a", "MeshCore-AB12"]

    def test_no_config_shows_msgbox_and_returns_none(self):
        h = _make_handler()
        with patch.object(h, "_meshcore_config", return_value=None):
            assert h._connection_args() is None
        assert h.ctx.dialog.last_msgbox_title == "MeshCore Config Missing"

    def test_serial_with_blank_device_path_rejects(self):
        h = _make_handler()
        cfg = _serial_config(device_path="")
        with patch.object(h, "_meshcore_config", return_value=cfg):
            assert h._connection_args() is None
        assert h.ctx.dialog.last_msgbox_title == "Bad Config"

    def test_tcp_with_blank_host_rejects(self):
        h = _make_handler()
        cfg = _tcp_config(tcp_host="")
        with patch.object(h, "_meshcore_config", return_value=cfg):
            assert h._connection_args() is None
        assert h.ctx.dialog.last_msgbox_title == "Bad Config"

    def test_unknown_type_rejects(self):
        h = _make_handler()
        cfg = SimpleNamespace(
            connection_type="zigbee", device_path="x",
            baud_rate=0, tcp_host="", tcp_port=0,
        )
        with patch.object(h, "_meshcore_config", return_value=cfg):
            assert h._connection_args() is None
        assert "Unknown" in (h.ctx.dialog.last_msgbox_text or "")


# ── _pause_daemon_for_cli / _resume_daemon ───────────────────────────


class TestDaemonHandoff:
    def test_pause_returns_false_when_daemon_inactive(self):
        h = _make_handler()
        with patch.object(h, "_is_daemon_active", return_value=False):
            assert h._pause_daemon_for_cli("test") is False
        # No stop attempted — operator wasn't even prompted.
        assert all(c[0] != "yesno" for c in h.ctx.dialog.calls)

    def test_pause_returns_true_when_user_confirms_and_stop_succeeds(self):
        h = _make_handler()
        h.ctx.dialog._yesno_returns = [True]
        with patch.object(h, "_is_daemon_active", return_value=True), \
             patch.object(h, "_stop_daemon", return_value=(True, "stopped")):
            assert h._pause_daemon_for_cli("test") is True

    def test_pause_returns_none_when_user_declines(self):
        h = _make_handler()
        h.ctx.dialog._yesno_returns = [False]
        with patch.object(h, "_is_daemon_active", return_value=True), \
             patch.object(h, "_stop_daemon") as stop:
            assert h._pause_daemon_for_cli("test") is None
            stop.assert_not_called()

    def test_pause_returns_none_when_stop_fails(self):
        h = _make_handler()
        h.ctx.dialog._yesno_returns = [True]
        with patch.object(h, "_is_daemon_active", return_value=True), \
             patch.object(h, "_stop_daemon", return_value=(False, "boom")):
            assert h._pause_daemon_for_cli("test") is None
        assert h.ctx.dialog.last_msgbox_title == "Daemon Stop Failed"

    def test_resume_calls_start_service(self):
        h = _make_handler()
        with patch.object(h, "_start_daemon", return_value=(True, "ok")) as start:
            h._resume_daemon()
            start.assert_called_once()


# ── _run_cli_command — end-to-end handoff ────────────────────────────


class TestRunCliCommand:
    def test_full_handoff_stops_and_starts_daemon(self):
        h = _make_handler()
        h.ctx.dialog._yesno_returns = [True]
        h.ctx.wait_for_enter = lambda: None  # don't block on stdin
        with patch.object(h, "_cli_path", return_value="/bin/meshcore-cli"), \
             patch.object(h, "_meshcore_config", return_value=_serial_config()), \
             patch.object(h, "_is_daemon_active", return_value=True), \
             patch.object(h, "_stop_daemon", return_value=(True, "")) as stop, \
             patch.object(h, "_start_daemon", return_value=(True, "")) as start, \
             patch("subprocess.run") as run:
            run.return_value = SimpleNamespace(returncode=0, stdout="ok", stderr="")
            h._run_cli_command("infos", title="t")
            stop.assert_called_once()
            start.assert_called_once()
            run.assert_called_once()
            argv = run.call_args[0][0]
            assert argv[0] == "/bin/meshcore-cli"
            assert argv[1:5] == ["-s", "/dev/ttyUSB0", "-b", "115200"]
            assert argv[5:] == ["infos"]
            # No shell=True ever.
            assert run.call_args.kwargs.get("shell") is None
            # Timeout enforced.
            assert run.call_args.kwargs.get("timeout") == h.CLI_TIMEOUT

    def test_subprocess_failure_still_restarts_daemon(self):
        h = _make_handler()
        h.ctx.dialog._yesno_returns = [True]
        h.ctx.wait_for_enter = lambda: None
        import subprocess as _sub
        with patch.object(h, "_cli_path", return_value="/bin/meshcore-cli"), \
             patch.object(h, "_meshcore_config", return_value=_serial_config()), \
             patch.object(h, "_is_daemon_active", return_value=True), \
             patch.object(h, "_stop_daemon", return_value=(True, "")), \
             patch.object(h, "_start_daemon", return_value=(True, "")) as start, \
             patch("subprocess.run", side_effect=_sub.TimeoutExpired("x", 30)):
            h._run_cli_command("infos", title="t")
            # Daemon must come back even when CLI bombs.
            start.assert_called_once()

    def test_no_cli_aborts_with_msgbox_and_no_daemon_action(self):
        h = _make_handler()
        with patch.object(h, "_cli_path", return_value=None), \
             patch.object(h, "_stop_daemon") as stop, \
             patch.object(h, "_start_daemon") as start:
            h._run_cli_command("infos", title="t")
            stop.assert_not_called()
            start.assert_not_called()
        assert h.ctx.dialog.last_msgbox_title == "meshcore-cli Not Found"

    def test_user_declines_abort_no_subprocess(self):
        h = _make_handler()
        h.ctx.dialog._yesno_returns = [False]
        with patch.object(h, "_cli_path", return_value="/bin/meshcore-cli"), \
             patch.object(h, "_meshcore_config", return_value=_serial_config()), \
             patch.object(h, "_is_daemon_active", return_value=True), \
             patch.object(h, "_stop_daemon") as stop, \
             patch("subprocess.run") as run:
            h._run_cli_command("infos", title="t")
            stop.assert_not_called()
            run.assert_not_called()

    def test_daemon_inactive_skips_stop_but_still_runs_cli(self):
        h = _make_handler()
        h.ctx.wait_for_enter = lambda: None
        with patch.object(h, "_cli_path", return_value="/bin/meshcore-cli"), \
             patch.object(h, "_meshcore_config", return_value=_serial_config()), \
             patch.object(h, "_is_daemon_active", return_value=False), \
             patch.object(h, "_stop_daemon") as stop, \
             patch.object(h, "_start_daemon") as start, \
             patch("subprocess.run") as run:
            run.return_value = SimpleNamespace(returncode=0, stdout="", stderr="")
            h._run_cli_command("infos", title="t")
            stop.assert_not_called()
            start.assert_not_called()  # No-op pause → no resume
            run.assert_called_once()

    def test_quoting_via_shlex_keeps_text_intact(self):
        h = _make_handler()
        h.ctx.wait_for_enter = lambda: None
        with patch.object(h, "_cli_path", return_value="/bin/meshcore-cli"), \
             patch.object(h, "_meshcore_config", return_value=_serial_config()), \
             patch.object(h, "_is_daemon_active", return_value=False), \
             patch("subprocess.run") as run:
            run.return_value = SimpleNamespace(returncode=0, stdout="", stderr="")
            # Multi-word text inside quotes must survive as one arg.
            h._run_cli_command("msg 'KH6 XYZ' hello world", title="t")
            argv = run.call_args[0][0]
            # After connection args, command tokens preserve quoting:
            tail = argv[argv.index("115200") + 1:]  # skip past "-b 115200"
            assert tail == ["msg", "KH6 XYZ", "hello", "world"]


# ── Input validation ────────────────────────────────────────────────


class TestValidateText:
    def test_rejects_newline(self):
        from handlers.meshcore_cli import MeshCoreCLIHandler
        assert MeshCoreCLIHandler._validate_text("hi\nthere") is False

    def test_rejects_tab(self):
        from handlers.meshcore_cli import MeshCoreCLIHandler
        assert MeshCoreCLIHandler._validate_text("hi\tthere") is False

    def test_accepts_printable_unicode(self):
        from handlers.meshcore_cli import MeshCoreCLIHandler
        assert MeshCoreCLIHandler._validate_text("aloha 🌺") is True

    def test_accepts_empty(self):
        # Empty is valid at this layer; callers reject upstream.
        from handlers.meshcore_cli import MeshCoreCLIHandler
        assert MeshCoreCLIHandler._validate_text("") is True


# ── Registration ────────────────────────────────────────────────────


class TestHandlerRegistration:
    def test_handler_is_registered_in_batch_21(self):
        from handlers import get_all_handlers
        ids = [c.handler_id for c in get_all_handlers()]
        assert "meshcore_cli" in ids

    def test_handler_protocol(self):
        from handlers.meshcore_cli import MeshCoreCLIHandler
        h = MeshCoreCLIHandler()
        assert h.handler_id == "meshcore_cli"
        assert h.menu_section == "meshcore"
        items = h.menu_items()
        assert len(items) == 1
        action, _label, flag = items[0]
        assert action == "meshcore_cli"
        # Phase 8.3 invariant — meshcore-section flag must be "meshcore" or None.
        assert flag in (None, "meshcore")
