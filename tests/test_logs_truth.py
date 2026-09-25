"""Logs screens say what they CAN'T see (2026-09-25, port of MeshForge e52a04f6).

`journalctl -u <absent unit>` prints "-- No entries --" (reads as quiet); a
user-scope unit never appears under -u; `rnsd --service` logs to its config
dir's 'logfile', so the rnsd screen (journal only) never showed an RNS error."""
import contextlib
import io
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
for p in (HERE, os.path.join(HERE, "..", "src"), os.path.join(HERE, "..", "src", "launcher_tui")):
    if p not in sys.path:
        sys.path.insert(0, p)

from handler_test_utils import FakeDialog, make_handler_context  # noqa: E402
import handlers.logs as logs  # noqa: E402


def _run(monkeypatch, view, system=(), user=()):
    monkeypatch.setattr(logs, "is_service_unit_installed",
                        lambda u, **k: u in (user if k.get("user") else system))
    ran = []
    monkeypatch.setattr(logs.subprocess, "run", lambda cmd, **k: ran.append(cmd))
    monkeypatch.setattr(logs, "clear_screen", lambda: None)
    h = logs.LogsHandler()
    h.set_context(make_handler_context(dialog=FakeDialog()))
    h.ctx.wait_for_enter = lambda *a, **k: None
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        getattr(h, view)()
    return out.getvalue(), ran


def test_absent_and_user_units_are_named_not_silent(monkeypatch):
    out, ran = _run(monkeypatch, "_view_error_logs", system=("rnsd", "mosquitto"), user=("nomadnet",))
    assert "Not installed on this box: meshtasticd" in out
    cmd = " ".join(ran[0])
    assert "--user-unit nomadnet" in cmd and "-u rnsd" in cmd and "meshtasticd" not in cmd


def test_no_units_at_all_does_not_run_an_unfiltered_journal(monkeypatch):
    out, ran = _run(monkeypatch, "_view_boot_messages")
    assert "None of the mesh units exist" in out and ran == []


def test_rnsd_screen_reads_the_logfile_and_strips_nuls(monkeypatch, tmp_path):
    (tmp_path / "logfile").write_bytes(b"\x00\x00[2026-09-24 16:35:04] [Error]    torn down\n")
    monkeypatch.setattr(logs.ReticulumPaths, "get_config_dir", classmethod(lambda cls: tmp_path))
    out, ran = _run(monkeypatch, "_view_rnsd_recent", system=("rnsd",))
    assert "[Error]    torn down" in out and "2 NUL byte(s) stripped" in out
    assert ran and ran[0][:3] == ["journalctl", "-u", "rnsd"]
