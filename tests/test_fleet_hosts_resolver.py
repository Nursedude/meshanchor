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
