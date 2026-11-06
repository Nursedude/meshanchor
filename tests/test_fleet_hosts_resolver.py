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
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from utils.fleet_hosts import (  # noqa: E402
    _parse_fleet_hosts_text as parse_fleet_hosts_text,
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

    def test_shell_read_failure_after_resolve_is_rc2(self, tmp_path, monkeypatch, capsys):
        """r7f F4, moved with the read (r8): a read that fails AFTER the
        resolve is a refusal (rc 11 from ``--shell`` -> lib rc 2), never
        rc 0 with an empty list."""
        import utils.fleet_hosts as fhm
        f = tmp_path / "fl"
        f.write_text("g\n")
        monkeypatch.setenv("MESHANCHOR_FLEET_HOSTS", str(f))

        def boom(self, *a, **k):
            raise PermissionError(13, "Permission denied")
        monkeypatch.setattr(Path, "read_text", boom)
        rc = fhm._shell_main(["--shell"])
        out = capsys.readouterr().out
        assert rc == fhm.SHELL_RC_UNREADABLE and str(f) in out and "unreadable" in out

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


# ── r7h (non-author pass on the r7g fixes) ────────────────────────────────


class TestSymlinkOnThePath:
    """r7h #1/#2: a symlink LOOP in a PARENT component read as ABSENT in the
    shell (rc 1 -> fell through) while python refused with the raw strerror
    (measured, both twins). Dangling parents stay absent in both (ENOENT)."""

    def test_parent_loop_refuses_in_both_with_one_reason(self, tmp_path):
        home = tmp_path / "home"
        (home / ".config").mkdir(parents=True)
        d = home / ".config" / "meshanchor"
        d.symlink_to(d)
        out, err = _run_shell_rc({"HOME": str(home)})
        with pytest.raises(FleetHostsUnreadable) as ei:
            resolve_fleet_hosts_file(env={"HOME": str(home)},
                                     etc_dir=str(tmp_path / "no-etc"))
        assert out["rc"] == "2", (out, err)
        assert ei.value.reason == "symlink loop" and "(symlink loop)" in out["err"]

    def test_parent_dangling_is_absent_in_both(self, tmp_path):
        home = tmp_path / "home"
        (home / ".config").mkdir(parents=True)
        (home / ".config" / "meshanchor").symlink_to(tmp_path / "gone")
        out, err = _run_shell_rc({"HOME": str(home)})
        assert out["rc"] in ("0", "1") and out["err"] == "", (out, err)
        assert resolve_fleet_hosts_file(env={"HOME": str(home)},
                                        etc_dir=str(tmp_path / "no-etc")) is None

    def test_the_paths_own_text_cannot_steer_the_reason(self, tmp_path):
        """r7h #3: only the strerror after the last ': ' is matched."""
        link = tmp_path / "x Permission denied symbolic links"
        link.symlink_to(tmp_path / "gone")
        out, _ = _run_shell_rc({"MESHANCHOR_FLEET_HOSTS": str(link), "HOME": str(tmp_path)})
        assert out["rc"] == "2" and "(dangling symlink)" in out["err"], out

    def test_an_unknown_errno_passes_through_raw_in_both(self, tmp_path):
        """r7h #5: ENAMETOOLONG is neither dangling nor a loop."""
        link = tmp_path / "lnk"
        link.symlink_to(tmp_path / ("n" * 300))
        out, _ = _run_shell_rc({"MESHANCHOR_FLEET_HOSTS": str(link), "HOME": str(tmp_path)})
        with pytest.raises(FleetHostsUnreadable) as ei:
            resolve_fleet_hosts_file(env={"MESHANCHOR_FLEET_HOSTS": str(link)})
        assert ei.value.reason == "File name too long"
        assert out["rc"] == "2" and "(File name too long)" in out["err"], out

    def test_the_probe_never_opens_the_target(self, tmp_path):
        """r7h #4: a list that is a link to a FIFO must be refused at once —
        a reader would block with no writer. Through the REAL resolve path."""
        fifo = tmp_path / "fifo"
        os.mkfifo(fifo)
        link = tmp_path / "lnk"
        link.symlink_to(fifo)
        out, err = _run_shell_rc({"MESHANCHOR_FLEET_HOSTS": str(link), "HOME": str(tmp_path)})
        assert out["rc"] == "2" and "(not a regular file)" in out["err"], (out, err)

    def test_a_too_long_name_refuses_in_both(self, tmp_path):
        """r7i #1: ENAMETOOLONG on a PLAIN path read ABSENT in the shell
        (the ancestor walk found a searchable parent) while python refused."""
        long = tmp_path / ("n" * 300)
        out, err = _run_shell_rc({"MESHANCHOR_FLEET_HOSTS": str(long), "HOME": str(tmp_path)})
        with pytest.raises(FleetHostsUnreadable) as ei:
            resolve_fleet_hosts_file(env={"MESHANCHOR_FLEET_HOSTS": str(long)})
        assert ei.value.reason == "File name too long"
        assert out["rc"] == "2" and "(File name too long)" in out["err"], (out, err)


# ── r8: the shell lib ASKS the python resolver (operator 2026-11-05) ─────


class TestShellAsksPython:
    """After four review rounds (r7f..r7i) each found a new path shape on
    which a shell re-implementation disagreed with python, the shell lib
    became a caller of ``fleet_hosts.py --shell``. These pin the seam."""

    def test_a_resolver_crash_is_a_refusal_never_no_list(self, tmp_path):
        """python's own failure codes (1 traceback, 2 usage) must not read
        as rc 1 "no list" — a resolver that could not run says so."""
        fake = tmp_path / "bin"
        fake.mkdir()
        (fake / "python3").write_text("#!/bin/sh\nexit 1\n")
        (fake / "python3").chmod(0o755)
        path = "%s:%s" % (fake, os.environ.get("PATH", "/usr/bin:/bin"))
        out, err = _run_shell_rc({"PATH": path, "HOME": str(tmp_path)})
        assert out["rc"] == "2" and "could not be resolved" in out["err"], (out, err)

    def test_an_unexported_override_still_wins(self, tmp_path):
        """The lib read shell variables; the python child only sees the
        environment — so the lib must pass the overrides explicitly."""
        f = tmp_path / "ov"
        f.write_text("only-me\n")
        script = ('MESHANCHOR_FLEET_HOSTS="$1"; export -n MESHANCHOR_FLEET_HOSTS; '
                  '. "{lib}"; fleet_hosts_resolve; echo "rc=$? list=$FLEET_HOSTS_LIST"'
                  ).format(lib=_LIB)
        p = subprocess.run(["bash", "-c", script, "bash", str(f)], capture_output=True,
                           text=True, timeout=30,
                           env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                                "HOME": str(tmp_path)})
        assert "rc=0 list=only-me" in p.stdout, (p.stdout, p.stderr)

    def test_a_trailing_slash_override_gets_one_answer(self, tmp_path):
        """r7i review #2 (measured): 'file/' — python used the file, the
        shell read it absent. One implementation now; pin the agreement."""
        f = tmp_path / "ov"
        f.write_text("a b\n")
        val = str(f) + "/"
        out, err = _run_shell_rc({"MESHANCHOR_FLEET_HOSTS": val, "HOME": str(tmp_path)})
        py = resolve_fleet_hosts_file(env={"MESHANCHOR_FLEET_HOSTS": val})
        assert out["rc"] == "0" and out["file"] == str(py), (out, err, py)

    def test_the_list_is_parsed_once_crlf_and_comments(self, tmp_path):
        f = tmp_path / "ov"
        f.write_bytes(b"moc1\r\nmoc2  # retired\r\n#x\n")
        p = subprocess.run(["bash", "-c", '. "$1"; fleet_hosts_resolve; printf "%s|" $FLEET_HOSTS_LIST',
                            "bash", str(_LIB)], capture_output=True, text=True, timeout=30,
                           env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                                "MESHANCHOR_FLEET_HOSTS": str(f)})
        assert p.stdout == "moc1|moc2|", (p.stdout, p.stderr)


# ── r8 review (one pass, stop rule pre-set): real-box wrong decisions ─────


def _sh_full(env, lib=None):
    script = ('. "$1"; fleet_hosts_resolve; rc=$?; '
              'printf "rc=%s\\nfile=%s\\nerr=%s\\nwhy=%s\\n" "$rc" "$FLEET_HOSTS_FILE" '
              '"$FLEET_HOSTS_ERROR" "$FLEET_HOSTS_WHY"')
    full = {"PATH": os.environ.get("PATH", "/usr/bin:/bin")}
    full.update(env)
    p = subprocess.run(["bash", "-c", script, "bash", str(lib or _LIB)],
                       capture_output=True, text=True, timeout=30, env=full,
                       errors="surrogateescape")
    return dict(ln.split("=", 1) for ln in p.stdout.splitlines() if "=" in ln), p.stderr


class TestShellSeamReview:
    def test_a_non_utf8_path_is_used_not_refused(self, tmp_path):
        """r8 #1 (reviewer measured): str(path) with surrogates crashed the
        locale encoder under en_US.UTF-8 -> rc 2 for a READABLE list."""
        d = os.fsencode(str(tmp_path)) + b"/l\xff"
        with open(d, "wb") as fh:
            fh.write(b"a b\n")
        val = os.fsdecode(d)
        out, err = _sh_full({"MESHANCHOR_FLEET_HOSTS": val, "LC_ALL": "en_US.UTF-8",
                             "HOME": str(tmp_path)})
        assert out["rc"] == "0" and out["file"] == val, (out, err)

    def test_a_lib_without_its_python_refuses_by_name(self, tmp_path):
        """r8 #4: never build the resolver path from a failed cd."""
        lib = tmp_path / "scripts" / "lib" / "fleet_hosts.sh"
        lib.parent.mkdir(parents=True)
        lib.write_text(_LIB.read_text())
        out, err = _sh_full({"HOME": str(tmp_path)}, lib=lib)
        assert out["rc"] == "2" and "not found beside this lib" in out["err"], (out, err)

    def test_a_hung_resolver_ends_as_a_refusal(self, tmp_path):
        """r8 #3: a hung interpreter must not hang every deploy script."""
        fake = tmp_path / "bin"
        fake.mkdir()
        (fake / "python3").write_text("#!/bin/sh\nexec sleep 20\n")
        (fake / "python3").chmod(0o755)
        import time
        t0 = time.monotonic()
        out, err = _sh_full({"PATH": "%s:%s" % (fake, os.environ.get("PATH", "")),
                             "FLEET_HOSTS_RESOLVE_TIMEOUT": "1", "HOME": str(tmp_path)})
        assert time.monotonic() - t0 < 10
        assert out["rc"] == "2" and "exited 124" in out["err"], (out, err)

    def test_no_list_says_what_was_checked_from_the_resolver(self, tmp_path):
        """r8 #2: the WHY comes from python's own tier list — with HOME
        unset it must not name a per-user file it never reads."""
        out, _ = _sh_full({"MESHANCHOR_FLEET_HOSTS": str(tmp_path / "nope")})
        assert out["rc"] == "1" and "MESHANCHOR_FLEET_HOSTS=" in out["why"] \
            and "does not exist" in out["why"], out
        out, _ = _sh_full({})
        if out["rc"] == "1":            # the box's real /etc tier may answer
            assert "HOME is unset" in out["why"] and ".config" not in out["why"], out

    def test_a_silent_rc0_is_a_refusal_not_an_empty_list(self, tmp_path):
        """r8 suite catch: a shadowed `timeout` stub exited 0 without running
        python; the lib returned rc 0 with an EMPTY list ("lists nobody")."""
        fake = tmp_path / "bin"
        fake.mkdir()
        (fake / "timeout").write_text("#!/bin/sh\nexit 0\n")
        (fake / "timeout").chmod(0o755)
        out, err = _sh_full({"PATH": "%s:%s" % (fake, os.environ.get("PATH", "")),
                             "HOME": str(tmp_path)})
        assert out["rc"] == "2" and "rc 0 with no file" in out["err"], (out, err)



# ── r9 (non-author pass on the r8 fixes) ───────────────────────────────────


class TestCorruptListRefused:
    """r9 #1 (measured): a power-loss NUL fill read as ONE garbage host in
    python and as an EMPTY list (rc 0) in the shell; 'mo\\0\\0c2' read as
    'moc2' in the shell. A host name is ASCII — refuse, in both, the same way."""

    @pytest.mark.parametrize("data", [b"\0\0\0\0\n", b"mo\0\0c2\nmoc3\n",
                                      b"moc\xff\n", b"moc\x01x\n"])
    def test_refused_in_both(self, tmp_path, data):
        f = tmp_path / "fl"
        f.write_bytes(data)
        env = {"MESHANCHOR_FLEET_HOSTS": str(f), "HOME": str(tmp_path)}
        out, err = _sh_full(env)
        with pytest.raises(FleetHostsUnreadable) as ei:
            resolve_fleet_hosts(env=env)
        assert "not a host name" in ei.value.reason
        assert out["rc"] == "2" and "not a host name" in out["err"], (out, err)

    def test_bytes_inside_a_comment_stay_harmless(self, tmp_path):
        f = tmp_path / "fl"
        f.write_bytes(b"moc  # caf\xe9 \0\nmoc1\n")
        env = {"MESHANCHOR_FLEET_HOSTS": str(f), "HOME": str(tmp_path)}
        assert resolve_fleet_hosts(env=env) == ["moc", "moc1"]
        out, err = _sh_full(env)
        assert out["rc"] == "0", (out, err)

    def test_a_newline_in_the_path_is_refused_by_the_python_api_too(self, tmp_path):
        """r9 #7: the shell refused it; the python API returned the file."""
        d = tmp_path / "a\nb"
        d.write_text("moc\n")
        with pytest.raises(FleetHostsUnreadable):
            resolve_fleet_hosts_file(env={"MESHANCHOR_FLEET_HOSTS": str(d)})

    def test_cdpath_cannot_move_the_lib_off_its_python(self, tmp_path):
        """r9 #6: with CDPATH set, a relative source made `cd` print a path
        (two lines in the capture) or land in ANOTHER repo's src/utils."""
        f = tmp_path / "fl"
        f.write_text("moc\n")
        # CDPATH only bites when it holds the SAME relative path (measured
        # red on the r8 lib only with this tree present: rc 2).
        (tmp_path / "scripts" / "lib").mkdir(parents=True)
        (tmp_path / "src" / "utils").mkdir(parents=True)
        repo = _LIB.parent.parent.parent
        p = subprocess.run(["bash", "-c", '. scripts/lib/fleet_hosts.sh; fleet_hosts_resolve; echo "rc=$?"'],
                           cwd=str(repo), capture_output=True, text=True, timeout=30,
                           env={"PATH": os.environ.get("PATH", ""), "CDPATH": str(tmp_path),
                                "MESHANCHOR_FLEET_HOSTS": str(f)})
        assert "rc=0" in p.stdout, (p.stdout, p.stderr)



# ── r11: ONE reader (operator 2026-11-05, after r10 + Fable found private
#    readers turning a refusal into "no fleet") ─────────────────────────────


class TestOneReader:
    """Every fleet-host-list read goes through utils.fleet_hosts. A module
    that parses the list itself can swallow the refusal (`except OSError:
    return []`) — r10 found five, the guard below found a sixth."""

    # Files that split '#' comments for a DIFFERENT file — not the fleet list.
    ALLOW = {
        "src/utils/fleet_hosts.py": "the reader itself",
        "src/mini_dudeai/dreams.py": "parses /etc/hosts lines, not the list",
        "src/utils/watchdog_probes_peer_cron.py": "parses truth_spool_targets",
        "scripts/gen_fleet_hosts.py": "parses the generated /etc/hosts block",
        "scripts/dep_advisory_check.py": "parses the advisory ACCEPT list (read_accepted)",
        "scripts/lint.py": "parses source comments, not the list",
    }

    def _py_files(self):
        root = _LIB.parent.parent.parent
        for top in ("src", "scripts"):
            for p in sorted((root / top).rglob("*.py")):
                yield p.relative_to(root).as_posix(), p.read_text(errors="replace")

    def test_nobody_else_names_the_parser(self):
        bad = [rel for rel, text in self._py_files()
               if rel != "src/utils/fleet_hosts.py" and "parse_fleet_hosts_text" in text]
        assert not bad, "parse the list via read_fleet_hosts_file, not privately: %s" % bad

    def test_no_private_comment_splitting_reader_of_the_list(self):
        bad = [rel for rel, text in self._py_files()
               if _py_private_parser(text) and rel not in self.ALLOW]
        assert not bad, ("a private fleet-list parser — read it with "
                         "utils.fleet_hosts.read_fleet_hosts_file: %s" % bad)


class TestReaderEdges:
    def test_a_bom_is_not_a_corrupt_list(self, tmp_path):
        """r10 (VERIFIED by both reviewers): an editor's UTF-8 BOM refused
        the whole fleet."""
        f = tmp_path / "fl"
        f.write_bytes(b"\xef\xbb\xbfmoc1\nmoc2\n")
        env = {"MESHANCHOR_FLEET_HOSTS": str(f), "HOME": str(tmp_path)}
        assert resolve_fleet_hosts(env=env) == ["moc1", "moc2"]
        out, err = _sh_full(env)
        assert out["rc"] == "0", (out, err)

    def test_absent_is_file_not_found_not_a_refusal(self, tmp_path):
        from utils.fleet_hosts import read_fleet_hosts_file
        with pytest.raises(FileNotFoundError):
            read_fleet_hosts_file(tmp_path / "nope")

    def test_the_lib_sourced_through_a_symlink_finds_its_python(self, tmp_path):
        """Fable r10 #7 (VERIFIED): BASH_SOURCE was the link's path."""
        link = tmp_path / "fh.sh"
        link.symlink_to(_LIB)
        f = tmp_path / "fl"
        f.write_text("moc\n")
        out, err = _sh_full({"MESHANCHOR_FLEET_HOSTS": str(f), "HOME": str(tmp_path)}, lib=link)
        assert out["rc"] == "0", (out, err)


def test_ma_fleet_check_refuses_a_corrupt_list_and_splits_whitespace(tmp_path, monkeypatch, capsys):
    """r11 / Fable r10 #3: MA provision_role had its own line parser —
    "moc1 moc2" was ONE host, and a NUL-filled list was accepted."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "_r11_ma_pr", Path(__file__).resolve().parent.parent / "scripts" / "provision_role.py")
    pr = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = pr
    spec.loader.exec_module(pr)
    ok = tmp_path / "ok"
    ok.write_text("moc1 moc2\n")
    assert pr.parse_fleet_hosts(ok) == ["moc1", "moc2"]
    assert pr.parse_fleet_hosts(tmp_path / "absent") == []
    bad = tmp_path / "bad"
    bad.write_bytes(b"\0\0\0\n")
    monkeypatch.setenv("MESHANCHOR_FLEET_HOSTS", str(bad))
    # no ssh can leave this test under ANY version of the code (2026-11-05)
    nossh = tmp_path / "nossh"
    nossh.mkdir()
    (nossh / "ssh").write_text("#!/bin/sh\nexit 255\n")
    (nossh / "ssh").chmod(0o755)
    monkeypatch.setenv("PATH", "%s:%s" % (nossh, os.environ.get("PATH", "")))
    assert pr.main(["--fleet-check"]) == 2
    assert "not a host name" in capsys.readouterr().err



# ── r13 (Fable r12) ────────────────────────────────────────────────────────


class TestR13ReaderEdges:
    def test_a_dangling_symlink_is_refused_by_the_reader_too(self, tmp_path):
        """Fable r12 #4 (VERIFIED): the reader called it ABSENT while
        _usable refused it — the TUI then read STANDALONE."""
        from utils.fleet_hosts import read_fleet_hosts_file
        link = tmp_path / "fl"
        link.symlink_to(tmp_path / "gone")
        with pytest.raises(FleetHostsUnreadable) as ei:
            read_fleet_hosts_file(link)
        assert ei.value.reason == "dangling symlink"
        with pytest.raises(FileNotFoundError):
            read_fleet_hosts_file(tmp_path / "absent")
        with pytest.raises(FileNotFoundError):     # ENOTDIR = absent, as _usable
            (tmp_path / "afile").write_text("x\n")
            read_fleet_hosts_file(tmp_path / "afile" / "fleet_hosts")

    @pytest.mark.parametrize("sep", ["\x0b", "\x0c", "\x1f", "\u0085", "\u2028", "\u00a0"])
    def test_an_odd_separator_is_refused_not_split(self, tmp_path, sep):
        """Fable r12 #7 (VERIFIED): 'moc\\x1f1' became ['moc', '1']."""
        f = tmp_path / "fl"
        f.write_text("a.invalid%sb.invalid\n" % sep, encoding="utf-8")
        with pytest.raises(FleetHostsUnreadable):
            resolve_fleet_hosts(env={"MESHANCHOR_FLEET_HOSTS": str(f)})

    def test_space_tab_crlf_still_split(self, tmp_path):
        f = tmp_path / "fl"
        f.write_bytes(b"a.invalid b.invalid\tc.invalid\r\nd.invalid\n")
        assert resolve_fleet_hosts(env={"MESHANCHOR_FLEET_HOSTS": str(f)}) == [
            "a.invalid", "b.invalid", "c.invalid", "d.invalid"]


class TestOneReaderShell:
    """r13: the one-reader guard covers SHELL too — Fable r12 found three
    `done < "$HOSTS_FILE"` readers the *.py guard could not see."""

    def test_no_shell_script_reads_a_hosts_file_itself(self):
        root = _LIB.parent.parent.parent
        bad = []
        for p in sorted((root / "scripts").rglob("*.sh")):
            if p == _LIB:
                continue
            bad += ["%s:%d" % (p.relative_to(root), n)
                    for n in _sh_private_reader_lines(p.read_text(errors="replace"))]
        assert not bad, "read the list via scripts/lib/fleet_hosts.sh: %s" % bad



# ── r15: the one-reader guards, widened and PROVEN to fail (Fable r14 O1) ──
# A guard that never failed is not evidence: TestGuardsAreFalsifiable plants
# every copy shape Fable showed slipping through and requires each flagged.

_SH_TOOLS = (r'\b(cat|sed|awk|grep|egrep|head|tail|xargs|mapfile|readarray|cut|tr|sort|uniq|wc|'
             r'while|python3?|perl)\b')
_SH_TOOL_RX = re.compile(_SH_TOOLS)
_SH_REDIR_RX = re.compile(r'(?<![<\d])\d?<\s*(?!\()(?P<t>\S+)')
_SH_LIB_OUT = re.compile(r'\$\{?FLEET_HOSTS_(LIST|ERROR|WHY)\b\}?')
_SH_LITERAL = re.compile(r'fleet_hosts\b(?!\.sh|\.example)', re.I)
_SH_HOSTVAR = re.compile(r'\$\{?(\w*(hosts|fleet_file)\w*)', re.I)          # redirect target: any
_SH_FILEVAR = re.compile(r'\$\{?\w*(hosts(_?(file|path))?|fleet_file)(?!\w)', re.I)  # tool argument
_SH_SEG_LEAD = re.compile(r'^[\s(]*(?:\w+=)?["\']?(?:\$\()?\s*(\S+)')
_SH_ASSIGN = re.compile(r'^\s*(?:local\s+|export\s+)?(\w+)=(.*)$')
_PY_SPLIT = re.compile(
    r"""(split|partition|rpartition|startswith|find|index)\(\s*\(?\s*['"]#['"]"""
    r"""|re\.sub\(\s*r?['"]#|==\s*['"]#['"]|comments\s*=\s*True|split\(\s*COMMENT\b""")


def _sh_private_reader_lines(text):
    """Line numbers of shell code that reads a fleet hosts FILE itself
    (r15, widened r17 after Fable r16 planted 16 realistic shapes). The lib's
    OUTPUT ($FLEET_HOSTS_LIST/ERROR/WHY) and here-strings are fine. KNOWN
    LIMIT: a generically named variable never assigned from a fleet_hosts
    path in this file (`done < "$LIST"`) is invisible to a line scanner."""
    lines = text.splitlines()
    suspect = set()
    for line in lines:
        m = _SH_ASSIGN.match(line)
        if m and _SH_LITERAL.search(m.group(2)):
            suspect.add(m.group(1))
        for seg in line.split(";"):
            m = _SH_ASSIGN.match(seg)
            if m and _SH_LITERAL.search(m.group(2)):
                suspect.add(m.group(1))
    sus_rx = re.compile(r'\$\{?(%s)\b' % "|".join(map(re.escape, sorted(suspect)))) if suspect else None

    def mentions_list(s, file_only=False):
        rx = _SH_FILEVAR if file_only else _SH_HOSTVAR
        return bool(_SH_LITERAL.search(s) or rx.search(s)
                    or (sus_rx and sus_rx.search(s)))

    def tool_reads_list(code):
        # per pipeline segment; a segment led by echo/printf only PRINTS
        # host CONTENTS ($BOXES, "$HOSTS") — that is not reading the file.
        for seg in re.split(r'(?<!\|)\|(?!\|)', code):
            m = _SH_SEG_LEAD.match(seg)
            lead = m.group(1) if m else ""
            if lead in ("echo", "printf"):
                continue
            if _SH_TOOL_RX.search(seg) and mentions_list(seg, file_only=True):
                return True
        return False

    out = []
    for n, line in enumerate(lines, 1):
        if line.lstrip().startswith("#"):
            continue
        code = _SH_LIB_OUT.sub("", line.replace("<<<", "\x00"))
        code = re.sub(r'(echo|printf|say|die|warn|log)\s+"[^"]*"', "", code)
        hit = any(mentions_list(m.group("t")) for m in _SH_REDIR_RX.finditer(code))
        if not hit and tool_reads_list(code):
            hit = True
        if hit:
            out.append(n)
    return out


def _py_private_parser(text):
    return bool(re.search("fleet_hosts", text, re.I)) and bool(_PY_SPLIT.search(text))


class TestGuardsAreFalsifiable:
    @pytest.mark.parametrize("snippet", [
        'done < "$HOSTS_FILE"',
        'done 3< "$HOSTS_FILE"',
        'while read -r h; do :; done < "$hosts_file"',
        'done < ${FLEET_FILE_HOSTS}',
        'cat "$HOSTS_FILE" | while read -r h; do :; done',
        'grep -v "^#" "$HOSTS_FILE"',
        "awk '{print $1}' \"$MY_HOSTS\"",
        'xargs -a "$HOSTS_FILE" -n1 echo',
        'mapfile -t hosts < "$FLEET_HOSTS_FILE"',
        'sed "s/#.*//" "$FLEET_HOSTS_FILE"',
        'hosts=$(cat "$fleet_file")',
        'done < ~/.config/meshforge/fleet_hosts',
        'done < "$HOME/.config/meshforge/fleet_hosts"',
        'F=~/.config/meshforge/fleet_hosts; done < "$F"',
        'done < "${CFG_DIR}/fleet_hosts"',
        "for h in $(grep -v '^#' ~/.config/meshforge/fleet_hosts); do :; done",
        'FLEET=~/.config/meshforge/fleet_hosts; cat "$FLEET"',
        'cat "${MESHFORGE_FLEET_HOSTS:-$HOME/.config/meshforge/fleet_hosts}"',
        'python3 - "$HOSTS_FILE" <<EOF',
        'ssh "$h" cat .config/meshforge/fleet_hosts',
        'HOSTS=$(sed -e "s/#.*//" -- "$FLEET_HOSTS_FILE")',
        'mapfile -t hosts <"$HOSTS_FILE"',
        'done < $HOSTS_FILE',
        'done <"${hosts_file}"',
        'while read -r h; do :; done <"$FLEET_HOSTS_FILE" ; cat x',
        'readarray -t H < <(grep -v "^#" "$FLEET_HOSTS_FILE")',
    ])
    def test_shell_guard_flags_every_copy_shape(self, snippet):
        assert _sh_private_reader_lines(snippet), snippet

    def test_the_known_limit_is_written_down(self):
        """`done < "$LIST"` with no fleet_hosts assignment in sight is the
        documented blind spot of a line scanner — pinned so a future widening
        that closes it updates the docstring too."""
        assert _sh_private_reader_lines('done < "$LIST"') == []
        assert "KNOWN" in (_sh_private_reader_lines.__doc__ or "")

    @pytest.mark.parametrize("snippet", [
        'done <<< "$FLEET_HOSTS_LIST"',
        'done 3< <(printf "%s\\n" "$FLEET_HOSTS_LIST")',
        'echo "fleet_hosts: $FLEET_HOSTS_ERROR" >&2',
        'BOXES="$(printf \'%s\\n\' $BOXES | awk \'NF\' | tr \'\\n\' \' \')"',
        'echo "probing $(echo "$HOSTS" | wc -w) hosts" >&2',
        'python3 - "$SORT_BY" "$hosts_seen" "$hosts_with_data"',
        'echo "no list - create ~/.config/meshforge/fleet_hosts" >&2',
        '    cp contrib/fleet/fleet_hosts.example ~/.config/meshforge/fleet_hosts',
        '. "$HERE/lib/fleet_hosts.sh"',
        '# done < "$HOSTS_FILE"  (a comment)',
    ])
    def test_shell_guard_passes_the_lib_output(self, snippet):
        assert _sh_private_reader_lines(snippet) == [], snippet

    @pytest.mark.parametrize("snippet", [
        "x = line.split('#', 1)[0]  # fleet_hosts",
        'h, _, _ = line.partition("#")  # fleet_hosts',
        "if s.startswith('#'): pass  # fleet_hosts",
        're.sub(r"#.*", "", line).split()  # fleet_hosts',
        'line[:line.find("#")].split()  # fleet_hosts',
        'p = os.environ.get("MESHFORGE_FLEET_HOSTS")\nh = [t for t in ln.split("#")[0].split()]',
        'shlex.split(line, comments=True)  # fleet_hosts',
        'line.strip().startswith(("#", ";"))  # fleet_hosts',
        'COMMENT = "#"\nline.split(COMMENT)[0]  # fleet_hosts',
        'tok[0] == "#"  # fleet_hosts',
        'line.split("#", maxsplit=1)[0]  # fleet_hosts',
        'line.split( "#" )[0]  # fleet_hosts',
        "line.split('#')[0]  # FLEET_HOSTS only uppercase",
    ])
    def test_py_guard_flags_every_copy_shape(self, snippet):
        assert _py_private_parser(snippet), snippet



# ── r17 (Fable r16): an EXISTING empty list is refused at the seam ─────────


class TestEmptyListRefused:
    @pytest.mark.parametrize("content", [b"", b"# nobody yet\n", b"\n  \n"])
    def test_python_and_shell_refuse_an_empty_list(self, tmp_path, content):
        f = tmp_path / "fl"
        f.write_bytes(content)
        env = {"MESHANCHOR_FLEET_HOSTS": str(f), "HOME": str(tmp_path)}
        with pytest.raises(FleetHostsUnreadable) as ei:
            resolve_fleet_hosts(env=env)
        assert "lists no hosts" in ei.value.reason
        out, err = _sh_full(env)
        assert out["rc"] == "2" and "lists no hosts" in out["err"], (out, err)

    def test_the_reader_refuses_empty_by_default_and_shows_it_on_request(self, tmp_path):
        """r19 (Fable r18): consumers that resolve-then-READ bypassed the
        resolver's check, so the READER refuses by default; only the TUI
        wizard, which must SHOW the file, opts in with allow_empty."""
        from utils.fleet_hosts import read_fleet_hosts_file
        f = tmp_path / "fl"
        f.write_text("# nobody\n")
        with pytest.raises(FleetHostsUnreadable) as ei:
            read_fleet_hosts_file(f)
        assert "lists no hosts" in ei.value.reason
        assert read_fleet_hosts_file(f, allow_empty=True) == []

    def test_a_chatty_wrapper_line_never_becomes_the_file(self, tmp_path):
        """Fable r16 O3 (VERIFIED): a python3 wrapper printing a line first
        made that line FLEET_HOSTS_FILE and the real path a HOST."""
        fake = tmp_path / "bin"
        fake.mkdir()
        real = subprocess.run(["sh", "-c", "command -v python3"], capture_output=True,
                              text=True, timeout=10).stdout.strip()
        (fake / "python3").write_text('#!/bin/sh\necho "pyenv: warning foo"\nexec %s "$@"\n' % real)
        (fake / "python3").chmod(0o755)
        f = tmp_path / "fl"
        f.write_text("a.invalid\n")
        out, err = _sh_full({"PATH": "%s:%s" % (fake, os.environ.get("PATH", "")),
                             "MESHANCHOR_FLEET_HOSTS": str(f), "HOME": str(tmp_path)})
        assert out["rc"] == "2" and out["file"] == "", (out, err)



def test_r19_ma_fleet_check_refuses_an_empty_list(tmp_path, monkeypatch, capsys):
    """Fable r18 F1 (VERIFIED on MA too): 'fleet invariants OK' rc 0."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "_r19_ma_pr", Path(__file__).resolve().parent.parent / "scripts" / "provision_role.py")
    pr = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = pr
    spec.loader.exec_module(pr)
    empty = tmp_path / "fl"
    empty.write_text("# nobody\n")
    monkeypatch.setenv("MESHANCHOR_FLEET_HOSTS", str(empty))
    nossh = tmp_path / "nossh"
    nossh.mkdir()
    (nossh / "ssh").write_text("#!/bin/sh\nexit 255\n")
    (nossh / "ssh").chmod(0o755)
    monkeypatch.setenv("PATH", "%s:%s" % (nossh, os.environ.get("PATH", "")))
    assert pr.main(["--fleet-check"]) == 2
    assert "lists no hosts" in capsys.readouterr().err
