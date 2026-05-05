"""Tests for _lxmf_utils.py — LXMF exclusivity by config dir (MN-5).

The bug fix this exercises: the prior version checked port 37428 LISTEN,
which is always rnsd when rnsd is running, producing a false-positive
"another LXMF client is using port 37428" warning on every NomadNet launch.
The new version walks /proc for actual nomadnet/sideband/meshchatx PIDs
and warns only when their --config dir matches the new launch.

These tests stub /proc by patching ``_iter_proc_pids`` + ``_read_cmdline``
so they're hermetic.
"""

import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(SRC / "launcher_tui"))
sys.path.insert(0, os.path.dirname(__file__))

from handler_test_utils import FakeDialog


# ── _argv_client_name ──────────────────────────────────────────────


class TestArgvClientName:

    def test_simple_invocation_recognized(self):
        from handlers._lxmf_utils import _argv_client_name
        assert _argv_client_name(["/usr/local/bin/nomadnet"]) == "nomadnet"
        assert _argv_client_name(["/usr/bin/sideband"]) == "sideband"
        assert _argv_client_name(["meshchatx"]) == "meshchatx"

    def test_python_wrapper_inspects_argv1(self):
        from handlers._lxmf_utils import _argv_client_name
        assert _argv_client_name(["python3", "/opt/nomadnet/nomadnet"]) == "nomadnet"
        assert _argv_client_name(["/usr/bin/python", "sideband"]) == "sideband"

    def test_other_processes_not_matched(self):
        from handlers._lxmf_utils import _argv_client_name
        assert _argv_client_name(["bash"]) is None
        assert _argv_client_name(["/usr/sbin/rnsd"]) is None
        assert _argv_client_name(["python3", "manage.py"]) is None

    def test_empty_argv(self):
        from handlers._lxmf_utils import _argv_client_name
        assert _argv_client_name([]) is None


# ── _argv_config_dir ──────────────────────────────────────────────


class TestArgvConfigDir:

    def test_explicit_config_space_separated(self, monkeypatch):
        from handlers import _lxmf_utils as mod
        monkeypatch.setattr(mod, "get_real_user_home", lambda: Path("/home/test"))
        result = mod._argv_config_dir(
            ["nomadnet", "--config", "/srv/lxmf-noc"], "nomadnet"
        )
        assert result == "/srv/lxmf-noc"

    def test_explicit_config_equals_form(self, monkeypatch):
        from handlers import _lxmf_utils as mod
        monkeypatch.setattr(mod, "get_real_user_home", lambda: Path("/home/test"))
        result = mod._argv_config_dir(
            ["nomadnet", "--config=/srv/lxmf-noc"], "nomadnet"
        )
        assert result == "/srv/lxmf-noc"

    def test_default_config_falls_back_to_home_default(self, tmp_path, monkeypatch):
        from handlers import _lxmf_utils as mod
        monkeypatch.setattr(mod, "get_real_user_home", lambda: tmp_path)
        # No --config in argv
        result = mod._argv_config_dir(["nomadnet"], "nomadnet")
        assert result is not None
        assert result.endswith(".nomadnetwork")

    def test_unknown_client_no_default(self, monkeypatch):
        from handlers import _lxmf_utils as mod
        monkeypatch.setattr(mod, "get_real_user_home", lambda: Path("/home/test"))
        result = mod._argv_config_dir(["mystery"], "mystery")
        assert result is None


# ── find_competing_clients ─────────────────────────────────────────


class TestFindCompetingClients:

    def test_no_processes_no_conflicts(self, monkeypatch):
        from handlers import _lxmf_utils as mod
        monkeypatch.setattr(mod, "_iter_proc_pids", lambda: iter([]))
        monkeypatch.setattr(mod, "get_real_user_home", lambda: Path("/home/test"))
        assert mod.find_competing_clients(None) == []

    def test_self_pid_suppressed(self, monkeypatch):
        """The launcher's own PID never appears as a competitor."""
        from handlers import _lxmf_utils as mod
        own_pid = str(os.getpid())
        monkeypatch.setattr(mod, "_iter_proc_pids", lambda: iter([own_pid]))
        # If we were to read our own cmdline and it happened to mention
        # nomadnet (as it might for an integration test), we still must not
        # self-flag.
        monkeypatch.setattr(
            mod, "_read_cmdline",
            lambda pid: ["nomadnet", "--config", "/home/test/.nomadnetwork"],
        )
        monkeypatch.setattr(mod, "get_real_user_home", lambda: Path("/home/test"))
        assert mod.find_competing_clients(None) == []

    def test_conflict_on_default_dir(self, monkeypatch):
        from handlers import _lxmf_utils as mod
        monkeypatch.setattr(mod, "_iter_proc_pids", lambda: iter(["12345"]))
        monkeypatch.setattr(
            mod, "_read_cmdline",
            lambda pid: ["nomadnet"] if pid == "12345" else [],
        )
        monkeypatch.setattr(mod, "get_real_user_home", lambda: Path("/home/test"))
        result = mod.find_competing_clients(None)
        assert len(result) == 1
        pid, client, cfg = result[0]
        assert pid == "12345"
        assert client == "nomadnet"
        assert cfg.endswith(".nomadnetwork")

    def test_no_conflict_when_config_dirs_differ(self, monkeypatch):
        """Two LXMF clients with different --config dirs coexist OK."""
        from handlers import _lxmf_utils as mod
        monkeypatch.setattr(mod, "_iter_proc_pids", lambda: iter(["12345"]))
        monkeypatch.setattr(
            mod, "_read_cmdline",
            lambda pid: ["nomadnet", "--config", "/srv/other-noc"],
        )
        monkeypatch.setattr(mod, "get_real_user_home", lambda: Path("/home/test"))
        # New launch is going to ~/.nomadnetwork (default); existing is /srv/other-noc
        assert mod.find_competing_clients(None) == []

    def test_conflict_when_explicit_config_matches(self, monkeypatch):
        from handlers import _lxmf_utils as mod
        monkeypatch.setattr(mod, "_iter_proc_pids", lambda: iter(["12345"]))
        monkeypatch.setattr(
            mod, "_read_cmdline",
            lambda pid: ["nomadnet", "--config=/srv/lxmf"],
        )
        monkeypatch.setattr(mod, "get_real_user_home", lambda: Path("/home/test"))
        result = mod.find_competing_clients("/srv/lxmf")
        assert len(result) == 1
        assert result[0][0] == "12345"

    def test_non_lxmf_processes_skipped(self, monkeypatch):
        from handlers import _lxmf_utils as mod
        monkeypatch.setattr(mod, "_iter_proc_pids", lambda: iter(["100", "101", "102"]))
        cmds = {
            "100": ["bash"],
            "101": ["/usr/sbin/rnsd"],
            "102": ["python3", "/opt/web/manage.py"],
        }
        monkeypatch.setattr(mod, "_read_cmdline", lambda pid: cmds.get(pid, []))
        monkeypatch.setattr(mod, "get_real_user_home", lambda: Path("/home/test"))
        assert mod.find_competing_clients(None) == []

    def test_rnsd_on_port_37428_never_flagged(self, monkeypatch):
        """Regression guard for the bug this rewrite fixes: rnsd holds the
        :37428 LISTEN socket and the OLD check used to false-warn on it.
        With the new check, rnsd is never an LXMF client and never appears
        as a competitor — even when running."""
        from handlers import _lxmf_utils as mod
        monkeypatch.setattr(mod, "_iter_proc_pids", lambda: iter(["999"]))
        monkeypatch.setattr(
            mod, "_read_cmdline", lambda pid: ["/usr/local/bin/rnsd"],
        )
        monkeypatch.setattr(mod, "get_real_user_home", lambda: Path("/home/test"))
        assert mod.find_competing_clients(None) == []


# ── ensure_lxmf_exclusive ──────────────────────────────────────────


class TestEnsureLxmfExclusive:

    def test_non_nomadnet_passes_through(self):
        from handlers._lxmf_utils import ensure_lxmf_exclusive
        # Even with conflicts present, a non-nomadnet starting_app returns
        # True without prompting. Currently only nomadnet triggers the check.
        dialog = FakeDialog()
        assert ensure_lxmf_exclusive(dialog, "sideband") is True
        assert dialog.calls == []

    def test_no_conflicts_returns_true_without_prompt(self, monkeypatch):
        from handlers._lxmf_utils import ensure_lxmf_exclusive
        monkeypatch.setattr(
            "handlers._lxmf_utils.find_competing_clients",
            lambda _cfg: [],
        )
        dialog = FakeDialog()
        assert ensure_lxmf_exclusive(dialog, "nomadnet") is True
        assert dialog.calls == []

    def test_conflict_prompts_user_and_honors_yes(self, monkeypatch):
        from handlers._lxmf_utils import ensure_lxmf_exclusive
        monkeypatch.setattr(
            "handlers._lxmf_utils.find_competing_clients",
            lambda _cfg: [("12345", "nomadnet", "/home/test/.nomadnetwork")],
        )
        dialog = FakeDialog()
        dialog._yesno_returns = [True]
        assert ensure_lxmf_exclusive(dialog, "nomadnet") is True
        # Title must mention identity collision (not port collision)
        yesno_calls = [c for c in dialog.calls if c[0] == "yesno"]
        assert any("Identity Conflict" in args[0] for _, args, _ in yesno_calls)

    def test_conflict_prompts_user_and_honors_no(self, monkeypatch):
        from handlers._lxmf_utils import ensure_lxmf_exclusive
        monkeypatch.setattr(
            "handlers._lxmf_utils.find_competing_clients",
            lambda _cfg: [("12345", "nomadnet", "/home/test/.nomadnetwork")],
        )
        dialog = FakeDialog()
        dialog._yesno_returns = [False]
        assert ensure_lxmf_exclusive(dialog, "nomadnet") is False

    def test_dialog_text_lists_every_conflict(self, monkeypatch):
        from handlers._lxmf_utils import ensure_lxmf_exclusive
        monkeypatch.setattr(
            "handlers._lxmf_utils.find_competing_clients",
            lambda _cfg: [
                ("100", "nomadnet", "/home/test/.nomadnetwork"),
                ("200", "sideband", "/home/test/.nomadnetwork"),
            ],
        )
        dialog = FakeDialog()
        dialog._yesno_returns = [True]
        ensure_lxmf_exclusive(dialog, "nomadnet")
        yesno_calls = [c for c in dialog.calls if c[0] == "yesno"]
        body = yesno_calls[0][1][1]  # (title, body)
        assert "PID 100" in body and "PID 200" in body
        assert "nomadnet" in body and "sideband" in body

    def test_config_dir_argument_threaded_through(self, monkeypatch):
        """The config_dir kwarg must be passed to find_competing_clients."""
        from handlers._lxmf_utils import ensure_lxmf_exclusive
        captured = {}

        def fake_find(target):
            captured["target"] = target
            return []

        monkeypatch.setattr(
            "handlers._lxmf_utils.find_competing_clients", fake_find
        )
        ensure_lxmf_exclusive(FakeDialog(), "nomadnet", config_dir="/srv/lxmf")
        assert captured["target"] == "/srv/lxmf"


# ── _read_cmdline ─────────────────────────────────────────────────


class TestReadCmdline:

    def test_real_self_cmdline_parses(self):
        """Sanity check on the actual /proc reader using our own PID."""
        from handlers._lxmf_utils import _read_cmdline
        argv = _read_cmdline(str(os.getpid()))
        # Must be non-empty and contain at least the python interpreter
        assert len(argv) >= 1

    def test_nonexistent_pid_returns_empty(self):
        from handlers._lxmf_utils import _read_cmdline
        # PID 0 is the kernel scheduler — /proc/0 doesn't exist
        assert _read_cmdline("0") == []

    def test_garbage_pid_returns_empty(self):
        from handlers._lxmf_utils import _read_cmdline
        assert _read_cmdline("does-not-exist-99999999") == []
