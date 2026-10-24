"""MeshChatX listen address is an operator setting the installer never clobbers.

The wrapper is re-rendered verbatim by install_meshchatx.sh and the TUI, so a
hand edit of ``--host`` is silently reverted. ``MESHCHATX_HOST`` in
``~/.config/meshanchor/meshchatx.env`` (read as data, never sourced) is the
one knob; absent → 127.0.0.1. Runs the real wrapper against a fake binary.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
WRAPPER = REPO_ROOT / "templates" / "python" / "meshchatx_wrapper.sh"


def _run(tmp_path, env_text=None):
    home = tmp_path / "home"
    (home / ".local" / "bin").mkdir(parents=True)
    (home / ".config" / "meshanchor").mkdir(parents=True)
    fake = home / ".local" / "bin" / "meshchatx"
    fake.write_text('#!/usr/bin/env bash\nprintf "%s\\n" "$@"\n')
    fake.chmod(0o755)
    # Pin the rpc_key precondition: never read this box's /etc/reticulum.
    cfg = tmp_path / "rns_config"
    cfg.write_text("[reticulum]\n  rpc_key = " + "a" * 64 + "\n")
    script = tmp_path / "wrapper.sh"
    script.write_text(WRAPPER.read_text().replace("/etc/reticulum/config",
                                                  str(cfg)))
    if env_text is not None:
        (home / ".config" / "meshanchor" / "meshchatx.env").write_text(env_text)
    env = {"HOME": str(home), "PATH": os.environ.get("PATH", "/usr/bin:/bin")}
    return subprocess.run(["bash", str(script)], env=env, capture_output=True,
                          text=True, timeout=30)


def _host(stdout):
    args = stdout.split("\n")
    return args[args.index("--host") + 1]


def test_no_env_file_binds_loopback(tmp_path):
    r = _run(tmp_path)
    assert r.returncode == 0, r.stderr
    assert _host(r.stdout) == "127.0.0.1"
    assert "reachable beyond this box" not in r.stderr


def test_env_file_opens_the_lan_and_says_so(tmp_path):
    r = _run(tmp_path, "# lab only\nMESHCHATX_HOST=0.0.0.0\n")
    assert r.returncode == 0, r.stderr
    assert _host(r.stdout) == "0.0.0.0"
    assert "reachable beyond this box" in r.stderr


def test_quoted_value_is_accepted(tmp_path):
    r = _run(tmp_path, 'MESHCHATX_HOST="192.168.1.10"\n')
    assert r.returncode == 0, r.stderr
    assert _host(r.stdout) == "192.168.1.10"


@pytest.mark.parametrize("bad", ["$(touch /tmp/x)", "lan", "0.0.0.0;rm"])
def test_a_non_ip_value_refuses_loud_never_falls_back(tmp_path, bad):
    r = _run(tmp_path, f"MESHCHATX_HOST={bad}\n")
    assert r.returncode == 2
    assert "not an IP literal" in r.stderr
    assert "--host" not in r.stdout


def test_env_file_is_read_as_data_not_sourced(tmp_path):
    marker = tmp_path / "sourced"
    r = _run(tmp_path, f"touch {marker}\nMESHCHATX_HOST=127.0.0.1\n")
    assert r.returncode == 0, r.stderr
    assert not marker.exists()
