"""The MeshChatX TUI shows where the web UI ACTUALLY listens.

Review of a4a83703 (W1/S2): the status screen printed
``http://127.0.0.1:8000/`` whatever the bind, so a specific-IP bind showed a
URL that refuses, and a LAN-open bind was visible only in the journal. The
address now comes from the same ``ss`` LISTEN line that proves liveness —
measured, not re-parsed from the env file.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src" / "launcher_tui"))
sys.path.insert(0, str(REPO_ROOT / "src"))

from handlers import _meshchatx_service_ops as ops  # noqa: E402


@pytest.mark.parametrize("addrs,url,lan", [
    (["127.0.0.1:8000"], "http://127.0.0.1:8000/", False),
    (["0.0.0.0:8000"], "http://127.0.0.1:8000/", True),
    (["*:8000"], "http://127.0.0.1:8000/", True),
    (["192.168.1.10:8000"], "http://192.168.1.10:8000/", True),
    (["[::1]:8000"], "http://[::1]:8000/", False),
    (["[fd00::4]:8000"], "http://[fd00::4]:8000/", True),
    (["127.0.0.2:8000"], "http://127.0.0.2:8000/", False),
    ([], "http://127.0.0.1:8000/", False),
])
def test_url_and_lan_flag_follow_the_real_bind(addrs, url, lan):
    assert ops.local_url(addrs, 8000) == url
    assert ops.lan_open(addrs) is lan


_SS = (
    'LISTEN 0 128 0.0.0.0:8000 0.0.0.0:* users:(("meshchatx",pid=4242,fd=15))\n'
    'LISTEN 0 128 127.0.0.1:8000 0.0.0.0:* users:(("other",pid=42420,fd=3))\n'
)


def _mixin_with_ss(stdout, rc=0):
    m = ops.MeshChatXServiceOpsMixin()
    run = SimpleNamespace(returncode=rc, stdout=stdout)
    return m, patch.object(ops.subprocess, "run", return_value=run), \
        patch.object(ops.shutil, "which", return_value="/usr/bin/ss")


def test_listen_addrs_are_the_units_own_sockets():
    m, p_run, p_which = _mixin_with_ss(_SS)
    with p_run, p_which:
        # pid=4242 must not also match pid=42420
        assert m._listen_addrs(4242, 8000) == ["0.0.0.0:8000"]
        assert m._port_bound_by_pid(4242, 8000) is True


def test_no_listener_means_not_bound():
    m, p_run, p_which = _mixin_with_ss("")
    with p_run, p_which:
        assert m._listen_addrs(4242, 8000) == []
        assert m._port_bound_by_pid(4242, 8000) is False


def test_status_block_says_lan_open_and_the_real_url(capsys):
    m = ops.MeshChatXServiceOpsMixin()
    state = {"unit_installed": True, "wrapper_installed": True,
             "active": True, "enabled": True, "sub_state": "running",
             "main_pid": 4242, "n_restarts": 0, "port_bound": True,
             "port": 8000, "error": None, "listen": ["192.168.1.10:8000"]}
    with patch.object(ops.MeshChatXServiceOpsMixin,
                      "_meshchatx_service_state", return_value=state):
        m._print_service_state_block()
    out = capsys.readouterr().out
    assert "http://192.168.1.10:8000/" in out
    assert "127.0.0.1:8000" not in out
    assert "LAN-open" in out
    assert "LAN-open" in m._service_state_line(state)


def test_loopback_bind_is_not_called_lan_open(capsys):
    m = ops.MeshChatXServiceOpsMixin()
    state = {"unit_installed": True, "wrapper_installed": True,
             "active": True, "enabled": True, "sub_state": "running",
             "main_pid": 4242, "n_restarts": 0, "port_bound": True,
             "port": 8000, "error": None, "listen": ["127.0.0.1:8000"]}
    with patch.object(ops.MeshChatXServiceOpsMixin,
                      "_meshchatx_service_state", return_value=state):
        m._print_service_state_block()
    assert "LAN-open" not in capsys.readouterr().out
    assert "LAN-open" not in m._service_state_line(state)
