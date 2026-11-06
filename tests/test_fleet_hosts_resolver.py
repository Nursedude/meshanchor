"""MeshAnchor fleet_hosts resolver — unit pins + shell↔python parity.

MA's resolution chain lives in exactly TWO implementations:

    scripts/lib/fleet_hosts.sh      (lab_traffic_rollup, honest_status)
    src/utils/fleet_hosts.py        (mini rollup → daemon --preset auto)

Two is the floor (python cannot source bash), so this file is the drift
guard: the parity tests feed BOTH the same fixture tree and require the
same answer. Ported from the MF twin's convergence pattern 2026-07-29,
closing the WS-A artifact where the mini rollup read the MESHFORGE
namespace verbatim.

Every assertion is pinned — env, HOME, and (python-side) the /etc tier are
injected; shell parity cases resolve BEFORE the /etc tier, which cannot be
injected into the shell lib without root.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from utils.fleet_hosts import (  # noqa: E402
    parse_fleet_hosts_text,
    resolve_fleet_hosts,
    resolve_fleet_hosts_file,
)

_LIB = Path(__file__).resolve().parent.parent / "scripts" / "lib" / "fleet_hosts.sh"


class TestParser:
    def test_trailing_comment_yields_the_host(self):
        assert parse_fleet_hosts_text("moc1  # retired\n") == ["moc1"]

    def test_comments_blanks_whitespace(self):
        assert parse_fleet_hosts_text("# all\n\nmoc moc3\t# x\n") == ["moc", "moc3"]


class TestPythonResolver:
    def test_home_tier_then_etc(self, tmp_path):
        home = tmp_path / "home"
        (home / ".config" / "meshanchor").mkdir(parents=True)
        (home / ".config" / "meshanchor" / "fleet_hosts").write_text("h-host\n")
        etc = tmp_path / "etc"
        etc.mkdir()
        (etc / "fleet_hosts").write_text("e-host\n")
        assert resolve_fleet_hosts(env={"HOME": str(home)},
                                   etc_dir=str(etc)) == ["h-host"]
        (home / ".config" / "meshanchor" / "fleet_hosts").unlink()
        assert resolve_fleet_hosts(env={"HOME": str(home)},
                                   etc_dir=str(etc)) == ["e-host"]

    def test_env_override_authoritative_and_aliased(self, tmp_path):
        home = tmp_path / "home"
        (home / ".config" / "meshanchor").mkdir(parents=True)
        (home / ".config" / "meshanchor" / "fleet_hosts").write_text("real\n")
        etc = tmp_path / "no-etc"
        ov = tmp_path / "ov"
        ov.write_text("ov-host\n")
        env = {"HOME": str(home), "MESHANCHOR_FLEET_HOSTS": str(ov)}
        assert resolve_fleet_hosts(env=env, etc_dir=str(etc)) == ["ov-host"]
        env = {"HOME": str(home), "FLEET_HOSTS": str(ov)}
        assert resolve_fleet_hosts(env=env, etc_dir=str(etc)) == ["ov-host"]
        # set-but-missing: NO fall-through to the box's real config
        env = {"HOME": str(home),
               "MESHANCHOR_FLEET_HOSTS": str(tmp_path / "nope")}
        assert resolve_fleet_hosts_file(env=env, etc_dir=str(etc)) is None
        assert resolve_fleet_hosts(env=env, etc_dir=str(etc)) == []

    def test_home_unset_skips_user_tier(self, tmp_path):
        etc = tmp_path / "etc"
        etc.mkdir()
        (etc / "fleet_hosts").write_text("etc-only\n")
        assert resolve_fleet_hosts(env={}, etc_dir=str(etc)) == ["etc-only"]


def _run_shell(env: dict) -> tuple[str, list[str]]:
    script = (
        'set -u; . "{lib}"; '
        'if fleet_hosts_resolve; then '
        '  printf "%s\\n" "$FLEET_HOSTS_FILE"; '
        '  printf "%s\\n" "$FLEET_HOSTS_LIST"; '
        'fi'
    ).format(lib=_LIB)
    full_env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin")}
    full_env.update(env)
    proc = subprocess.run(["bash", "-c", script], capture_output=True,
                          text=True, timeout=30, env=full_env)
    lines = proc.stdout.splitlines()
    if not lines:
        return "", []
    return lines[0], [h for h in lines[1:] if h]


class TestShellPythonParity:
    def _both(self, tmp_path, env):
        sf, sh = _run_shell(env)
        pf = resolve_fleet_hosts_file(env=env, etc_dir=str(tmp_path / "no-etc"))
        ph = resolve_fleet_hosts(env=env, etc_dir=str(tmp_path / "no-etc"))
        return (sf, sh), (str(pf) if pf else "", ph)

    def test_home_tier_and_comment_parsing(self, tmp_path):
        home = tmp_path / "home"
        (home / ".config" / "meshanchor").mkdir(parents=True)
        (home / ".config" / "meshanchor" / "fleet_hosts").write_text(
            "moc  # primary\nmoc3 moc5\n")
        shell, py = self._both(tmp_path, {"HOME": str(home)})
        assert shell == py
        assert shell[1] == ["moc", "moc3", "moc5"]

    def test_override_and_alias_parity(self, tmp_path):
        ov = tmp_path / "ov"
        ov.write_text("only-host\n")
        for key in ("MESHANCHOR_FLEET_HOSTS", "FLEET_HOSTS"):
            shell, py = self._both(tmp_path, {key: str(ov)})
            assert shell == py
            assert shell[1] == ["only-host"]

    def test_missing_override_fails_both(self, tmp_path):
        home = tmp_path / "home"
        (home / ".config" / "meshanchor").mkdir(parents=True)
        (home / ".config" / "meshanchor" / "fleet_hosts").write_text("real\n")
        env = {"HOME": str(home),
               "MESHANCHOR_FLEET_HOSTS": str(tmp_path / "nope")}
        shell, py = self._both(tmp_path, env)
        assert shell == ("", [])
        assert py == ("", [])


class TestRollupWiring:
    def test_rollup_wrapper_reads_the_ma_namespace(self, tmp_path):
        from mini_dudeai.rollup import resolve_fleet_hosts as rollup_resolve
        ov = tmp_path / "hosts"
        ov.write_text("peer-a\npeer-b\n")
        assert rollup_resolve({"MESHANCHOR_FLEET_HOSTS": str(ov)}) == \
            ["peer-a", "peer-b"]
        # the MESHFORGE key must be DEAD here — the WS-A artifact regression
        assert rollup_resolve({"MESHFORGE_FLEET_HOSTS": str(ov),
                               "HOME": str(tmp_path)}) == []

    def test_fanout_skips_self_entries(self, tmp_path):
        """meshanchor-server's authored list contains exactly `localhost`;
        ssh'ing it would duplicate the directly-read local row."""
        from mini_dudeai.rollup import collect_fleet

        calls = []

        def runner(host, timeout_s):  # rollup's injectable ssh seam
            calls.append(host)
            return 255, "", ""

        ov = tmp_path / "hosts"
        ov.write_text("localhost\npeer-x\n")
        postures = collect_fleet(
            now_ts=1000.0, runner=runner,
            env={"MESHANCHOR_FLEET_HOSTS": str(ov)},
            local_state_path=str(tmp_path / "absent-state.json"))
        swept = " ".join(str(c) for c in calls)
        assert "peer-x" in swept
        assert "localhost" not in swept
        assert all(p.get("host") != "localhost" for p in postures)


# --- the generated /etc/hosts fleet block (ported from MeshForge 2026-09-07) ---
# The block is written by MeshForge's gen_fleet_hosts.py, ONE per box, and both
# twins READ it. It is the only membership registry present on every box, so
# mini's _known_fleet_subjects leans on it when fleet_hosts does not resolve.

_HOSTS_BLOCK_FIXTURE = """\
127.0.0.1\tlocalhost
# BEGIN meshforge fleet (generated by scripts/gen_fleet_hosts.py)
10.0.0.1\tboxa.mf.internal boxa
10.0.0.2\tboxb.mf.internal boxb    # via ip_fallback
# END meshforge fleet
10.0.0.99\thandadded.example
"""


def test_parse_hosts_block_reads_the_delimited_block():
    from utils.fleet_hosts import parse_hosts_block
    assert parse_hosts_block(_HOSTS_BLOCK_FIXTURE) == [
        ("10.0.0.1", ["boxa.mf.internal", "boxa"]),
        ("10.0.0.2", ["boxb.mf.internal", "boxb"]),
    ]


def test_parse_hosts_block_ignores_everything_outside_the_block():
    """Widening what counts as a fleet member is the one direction a
    membership registry must never fail in — localhost is not a peer."""
    from utils.fleet_hosts import parse_hosts_block
    addrs = [a for a, _ in parse_hosts_block(_HOSTS_BLOCK_FIXTURE)]
    assert "127.0.0.1" not in addrs
    assert "10.0.0.99" not in addrs


def test_parse_hosts_block_torn_block_yields_nothing():
    """BEGIN without END = damaged artifact; know less, never guess more."""
    from utils.fleet_hosts import parse_hosts_block
    torn = _HOSTS_BLOCK_FIXTURE.replace("# END meshforge fleet\n", "")
    assert parse_hosts_block(torn) == []


def test_hosts_block_markers_are_not_namespaced_to_meshanchor():
    """⚠️ MeshForge writes this block. Namespacing these literals would make
    this reader find no block at all — silently."""
    from utils.fleet_hosts import HOSTS_BLOCK_BEGIN, HOSTS_BLOCK_END
    assert "meshforge" in HOSTS_BLOCK_BEGIN
    assert "meshforge" in HOSTS_BLOCK_END
    assert "meshanchor" not in (HOSTS_BLOCK_BEGIN + HOSTS_BLOCK_END).lower()


# ── r7e #2 port (MF a7d198f0 + r7f d4fb041f): a PRESENT-but-unreadable or
# UNCHECKABLE tier ERRORS in both twins, never falls through ─────────────
# Operator 2026-11-05: "quietly failing is not an answer". Absent stays
# "next tier"; present-but-unusable (permission, a directory, a dangling
# symlink) or uncheckable (a parent directory not searchable) is an error
# naming the path: python FleetHostsUnreadable, shell rc 2 + FLEET_HOSTS_ERROR.

import pytest  # noqa: E402

from utils.fleet_hosts import FleetHostsUnreadable  # noqa: E402

_ROOT = hasattr(os, "geteuid") and os.geteuid() == 0
needs_nonroot = pytest.mark.skipif(_ROOT, reason="root reads a mode-000 file")


def _home_list(tmp_path, text="generic\n"):
    home = tmp_path / "home"
    (home / ".config" / "meshanchor").mkdir(parents=True)
    f = home / ".config" / "meshanchor" / "fleet_hosts"
    f.write_text(text)
    return home, f


def _run_shell_rc(env: dict):
    script = ('set -u; . "{lib}"; fleet_hosts_resolve; rc=$?; '
              'printf "rc=%s\\nfile=%s\\nerr=%s\\n" "$rc" "$FLEET_HOSTS_FILE" '
              '"${{FLEET_HOSTS_ERROR:-}}"').format(lib=_LIB)
    full_env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin")}
    full_env.update(env)
    p = subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                       timeout=30, env=full_env)
    return dict(ln.split("=", 1) for ln in p.stdout.splitlines() if "=" in ln), p.stderr


class TestPresentButUnreadableTier:
    @needs_nonroot
    def test_unreadable_home_tier_raises_not_falls_through_to_etc(self, tmp_path):
        home, f = _home_list(tmp_path)
        etc = tmp_path / "etc"
        etc.mkdir()
        (etc / "fleet_hosts").write_text("etc-host\n")
        f.chmod(0)
        try:
            with pytest.raises(FleetHostsUnreadable) as ei:
                resolve_fleet_hosts(env={"HOME": str(home)}, etc_dir=str(etc))
            out, err = _run_shell_rc({"HOME": str(home)})
        finally:
            f.chmod(0o600)
        assert ei.value.path == f and str(f) in str(ei.value)
        assert out["rc"] == "2" and out["file"] == ""
        assert str(f) in out["err"] and str(f) in err

    def test_a_directory_at_the_tier_raises_in_both(self, tmp_path):
        home = tmp_path / "home"
        (home / ".config" / "meshanchor" / "fleet_hosts").mkdir(parents=True)
        with pytest.raises(FleetHostsUnreadable):
            resolve_fleet_hosts_file(env={"HOME": str(home)},
                                     etc_dir=str(tmp_path / "no-etc"))
        assert _run_shell_rc({"HOME": str(home)})[0]["rc"] == "2"

    def test_a_dangling_symlink_raises_in_both(self, tmp_path):
        home = tmp_path / "home"
        (home / ".config" / "meshanchor").mkdir(parents=True)
        (home / ".config" / "meshanchor" / "fleet_hosts").symlink_to(tmp_path / "gone")
        with pytest.raises(FleetHostsUnreadable):
            resolve_fleet_hosts_file(env={"HOME": str(home)},
                                     etc_dir=str(tmp_path / "no-etc"))
        assert _run_shell_rc({"HOME": str(home)})[0]["rc"] == "2"

    @needs_nonroot
    def test_unreadable_override_raises_in_both(self, tmp_path):
        ov = tmp_path / "ov"
        ov.write_text("x\n")
        ov.chmod(0)
        try:
            with pytest.raises(FleetHostsUnreadable):
                resolve_fleet_hosts_file(env={"MESHANCHOR_FLEET_HOSTS": str(ov)},
                                         etc_dir=str(tmp_path / "no-etc"))
            out, _ = _run_shell_rc({"MESHANCHOR_FLEET_HOSTS": str(ov)})
        finally:
            ov.chmod(0o600)
        assert out["rc"] == "2"

    @needs_nonroot
    def test_an_unsearchable_parent_is_uncheckable_in_both(self, tmp_path):
        home, _ = _home_list(tmp_path)
        locked = home / ".config" / "meshanchor"
        locked.chmod(0)
        try:
            with pytest.raises(FleetHostsUnreadable) as ei:
                resolve_fleet_hosts_file(env={"HOME": str(home)},
                                         etc_dir=str(tmp_path / "no-etc"))
            out, _ = _run_shell_rc({"HOME": str(home)})
        finally:
            locked.chmod(0o755)
        assert "not searchable" in str(ei.value) and "not searchable" in out["err"]
        assert out["rc"] == "2"
        assert "exists" not in str(ei.value) and "exists" not in out["err"]

    def test_a_file_where_a_parent_should_be_is_absent_in_both(self, tmp_path):
        home = tmp_path / "home"
        home.mkdir()
        (home / ".config").write_text("a file\n")
        assert resolve_fleet_hosts_file(env={"HOME": str(home)},
                                        etc_dir=str(tmp_path / "no-etc")) is None
        assert _run_shell_rc({"HOME": str(home)})[0]["rc"] == "1"

    def test_absent_and_missing_override_unchanged(self, tmp_path):
        home = tmp_path / "home"
        home.mkdir()
        assert _run_shell_rc({"HOME": str(home)})[0] == {"rc": "1", "file": "", "err": ""}
        assert resolve_fleet_hosts_file(env={"HOME": str(home)},
                                        etc_dir=str(tmp_path / "no-etc")) is None
        home2, _ = _home_list(tmp_path / "b")
        env = {"HOME": str(home2), "MESHANCHOR_FLEET_HOSTS": str(tmp_path / "nope")}
        assert _run_shell_rc(env)[0]["rc"] == "1"
        assert resolve_fleet_hosts_file(env=env, etc_dir=str(tmp_path / "no-etc")) is None

    def test_a_read_failure_after_resolve_raises_not_empty(self, tmp_path, monkeypatch):
        home, _ = _home_list(tmp_path)

        def boom(self, *a, **k):
            raise PermissionError(13, "denied")
        monkeypatch.setattr(Path, "read_text", boom)
        with pytest.raises(FleetHostsUnreadable):
            resolve_fleet_hosts(env={"HOME": str(home)}, etc_dir=str(tmp_path / "e"))

    def test_shell_read_failure_after_resolve_is_rc2(self, tmp_path):
        home, _ = _home_list(tmp_path)
        fake = tmp_path / "bin"
        fake.mkdir()
        (fake / "sed").write_text("#!/bin/sh\necho 'sed: read error' >&2\nexit 4\n")
        (fake / "sed").chmod(0o755)
        out, _ = _run_shell_rc({"HOME": str(home),
                                "PATH": f"{fake}:{os.environ.get('PATH', '/usr/bin:/bin')}"})
        assert out["rc"] == "2" and out["file"] == ""

    def test_it_is_an_oserror(self):
        assert issubclass(FleetHostsUnreadable, OSError)


# --- consumers (MA has three python ones + two shell) -------------------------

_BAD = Path("/nowhere/meshanchor/fleet_hosts")


def _boom(*_a, **_k):
    raise FleetHostsUnreadable(_BAD, "permission denied")


@pytest.fixture
def raising(monkeypatch):
    import utils.fleet_hosts as fh
    monkeypatch.setattr(fh, "resolve_fleet_hosts_file", _boom)
    monkeypatch.setattr(fh, "resolve_fleet_hosts", _boom)


def test_rollup_pane_is_unknown_not_a_fleet_of_one(raising, capsys):
    from mini_dudeai import rollup
    assert rollup.main([]) == 2
    out = capsys.readouterr().out
    assert "UNKNOWN" in out and str(_BAD) in out
    assert rollup.main(["--deep"]) == 2


def test_preset_auto_raises_rather_than_reading_standalone(raising):
    """MA's rule: hosts -> meshanchor_fleet, none -> standalone. The list
    ALWAYS decides here (no NATS branch, unlike MF), so an unreadable list
    must never be read as 'none declared'."""
    from mini_dudeai.daemon import _resolve_preset_name
    with pytest.raises(FleetHostsUnreadable):
        _resolve_preset_name("auto")
    assert _resolve_preset_name("meshanchor_fleet") == "meshanchor_fleet"


def test_daemon_refuses_to_start_on_preset_auto(raising):
    from mini_dudeai import daemon
    with pytest.raises(SystemExit) as ei:
        daemon.main(["--preset", "auto"])
    assert str(_BAD) in str(ei.value.code)


@needs_nonroot
def test_dreams_witness_now_sees_the_real_resolver(tmp_path, monkeypatch):
    """The F8 witness (0d44d139) only fired on MA for an exception; with the
    resolver raising, an unreadable list is now named."""
    from mini_dudeai.dreams import _known_fleet_subjects_ex
    ov = tmp_path / "fleet_hosts"
    ov.write_text("x\n")
    ov.chmod(0)
    monkeypatch.setenv("MESHANCHOR_FLEET_HOSTS", str(ov))
    monkeypatch.delenv("FLEET_HOSTS", raising=False)
    try:
        _, _, blind = _known_fleet_subjects_ex(hosts_path=str(tmp_path / "hosts"))
    finally:
        ov.chmod(0o600)
    assert blind and str(ov) in blind


# ── r7g (non-author pass on this port): symlink reasons agree ─────────────


class TestSymlinkReasonParity:
    """r7g F1: a symlink LOOP read "a parent directory is not searchable" in
    python and "dangling symlink" in the shell (measured, both twins)."""

    def _both(self, tmp_path, link):
        out, err = _run_shell_rc({"MESHANCHOR_FLEET_HOSTS": str(link),
                                  "HOME": str(tmp_path)})
        with pytest.raises(FleetHostsUnreadable) as ei:
            resolve_fleet_hosts_file(env={"MESHANCHOR_FLEET_HOSTS": str(link)})
        assert out["rc"] == "2", (out, err)
        return ei.value.reason, out["err"]

    def test_symlink_loop(self, tmp_path):
        link = tmp_path / "loop"
        link.symlink_to(link)
        py, sh = self._both(tmp_path, link)
        assert py == "symlink loop" and "(symlink loop)" in sh, (py, sh)

    def test_dangling(self, tmp_path):
        link = tmp_path / "lnk"
        link.symlink_to(tmp_path / "gone")
        py, sh = self._both(tmp_path, link)
        assert py == "dangling symlink" and "(dangling symlink)" in sh, (py, sh)

    @needs_nonroot
    def test_target_under_an_unsearchable_dir(self, tmp_path):
        locked = tmp_path / "locked"
        locked.mkdir()
        (locked / "t").write_text("x\n")
        link = tmp_path / "lnk"
        link.symlink_to(locked / "t")
        locked.chmod(0)
        try:
            py, sh = self._both(tmp_path, link)
        finally:
            locked.chmod(0o755)
        want = "symlink target cannot be checked: a parent directory is not searchable"
        assert py == want and "(%s)" % want in sh, (py, sh)


def test_daemon_pinned_preset_never_imports_the_fleet_resolver(monkeypatch):
    """r7g F3: only `auto` consults the list."""
    from mini_dudeai import daemon
    monkeypatch.setitem(sys.modules, "utils.fleet_hosts", None)
    seen = {}

    def fake_import(name):
        seen["name"] = name
        raise SystemExit(0)
    monkeypatch.setattr(daemon, "_import_preset", fake_import)
    with pytest.raises(SystemExit) as ei:
        daemon.main(["--preset", "meshanchor_fleet"])
    assert ei.value.code == 0 and seen["name"] == "meshanchor_fleet"
