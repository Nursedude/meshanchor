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


def _run(monkeypatch, view, system=(), user=(), unknown=(), uid=1000):
    def presence(u, user=False, **k):
        if u in unknown:
            return "unknown"
        return "installed" if u in (user_units if user else system) else "absent"
    user_units = user
    monkeypatch.setattr(logs, "service_unit_presence", presence)
    monkeypatch.setattr(logs, "_journal_uid", lambda: uid)
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
    assert "_SYSTEMD_USER_UNIT=nomadnet.service _UID=1000" in cmd
    assert "_SYSTEMD_UNIT=rnsd.service" in cmd and "meshtasticd" not in cmd


# MF 2026-10-08 port (non-author review, VERIFIED on MF): a failed check is
# "could not check", never "not installed"; user units match the OPERATOR's
# uid (journalctl --user-unit filters on the caller's — root under sudo).

def test_a_failed_check_is_could_not_check_never_absent(monkeypatch):
    out, ran = _run(monkeypatch, "_view_error_logs", system=("rnsd",), unknown=("nomadnet",))
    assert "Could not check" in out and "nomadnet" in out.split("Could not check")[1]
    assert "_SYSTEMD_USER_UNIT=nomadnet.service" in " ".join(ran[0])


def test_every_check_failing_never_says_none_exist(monkeypatch):
    out, ran = _run(monkeypatch, "_view_boot_messages",
                    unknown=tuple(logs.LogsHandler.MESH_UNITS))
    assert "None of the mesh units exist" not in out and ran


def test_no_units_at_all_does_not_run_an_unfiltered_journal(monkeypatch):
    out, ran = _run(monkeypatch, "_view_boot_messages")
    assert "None of the mesh units exist" in out and ran == []


def test_rnsd_screen_reads_the_logfile_and_strips_nuls(monkeypatch, tmp_path):
    (tmp_path / "logfile").write_bytes(b"\x00\x00[2026-09-24 16:35:04] [Error]    torn down\n")
    monkeypatch.setattr(logs.ReticulumPaths, "get_config_dir", classmethod(lambda cls: tmp_path))
    out, ran = _run(monkeypatch, "_view_rnsd_recent", system=("rnsd",))
    assert "[Error]    torn down" in out and "2 NUL byte(s) stripped" in out
    assert ran and ran[0][:3] == ["journalctl", "-u", "rnsd"]



def test_coredumps_and_object_messages_are_matched_like_dash_u(monkeypatch):
    out, ran = _run(monkeypatch, "_view_error_logs", system=("meshtasticd",), user=("nomadnet",))
    cmd = " ".join(ran[0])
    assert "COREDUMP_UNIT=meshtasticd.service" in cmd and "OBJECT_SYSTEMD_UNIT=meshtasticd.service" in cmd
    assert "COREDUMP_USER_UNIT=nomadnet.service" in cmd


def test_root_without_sudo_user_never_says_a_user_unit_is_absent(monkeypatch):
    monkeypatch.setattr(logs, "_operator_known", lambda: False)
    out, ran = _run(monkeypatch, "_view_error_logs", system=("rnsd",))
    assert "nomadnet" in out.split("Could not check")[1]
    assert "Not installed on this box:" not in out
