"""RNS Diagnostics reports a NomadNet conflict only when NomadNet OWNS the
shared-instance socket (2026-09-25, port of MeshForge a9d41365): it fired
whenever a nomadnet process existed and the Fix flow offered `pkill -f nomadnet`."""
import builtins
import io
import os
import sys
from types import SimpleNamespace

HERE = os.path.dirname(os.path.abspath(__file__))
for p in (HERE, os.path.join(HERE, "..", "src"), os.path.join(HERE, "..", "src", "launcher_tui")):
    if p not in sys.path:
        sys.path.insert(0, p)

import handlers.rns_diagnostics as rd  # noqa: E402
from utils.paths import ReticulumPaths  # noqa: E402

REAL_OPEN = builtins.open


def _conflict(monkeypatch, ss_line, cmdline=None):
    monkeypatch.setattr(ReticulumPaths, "get_configured_instance_name", classmethod(lambda cls: "default"))
    monkeypatch.setattr(rd.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout=ss_line + "\n", returncode=0))

    def fake_open(path, *a, **k):
        if str(path).startswith("/proc/") and cmdline is not None:
            return io.BytesIO(cmdline.encode())
        return REAL_OPEN(path, *a, **k)
    monkeypatch.setattr(builtins, "open", fake_open)
    return rd.RNSDiagnosticsHandler.__new__(rd.RNSDiagnosticsHandler)._check_lxmf_app_conflict()


def test_rnsd_owner_is_no_conflict(monkeypatch):
    line = 'u_str LISTEN 0 5 @rns/default 1111 * 0 users:(("rnsd",pid=101,fd=5))'
    assert _conflict(monkeypatch, line) is None


def test_nomadnet_owner_is_the_conflict(monkeypatch):
    line = 'u_str LISTEN 0 5 @rns/default 2222 * 0 users:(("python3",pid=202,fd=5))'
    cmd = "/usr/bin/python3\x00/home/u/.local/bin/nomadnet\x00--rnsconfig\x00/etc/reticulum"
    assert _conflict(monkeypatch, line, cmd) == "NomadNet"


def test_no_listener_is_no_conflict(monkeypatch):
    assert _conflict(monkeypatch, "") is None
