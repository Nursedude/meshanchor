"""No fleet list (resolver rc 1) — every consumer says the resolver's OWN
reason AND advice that can work, from ONE implementation
(``utils.fleet_hosts.explain_absent``; shell: ``fleet_hosts_absent_say``).

Port of the MeshForge twin's 2026-11-06 fix (MF ``5d73d48b``), in SHAPE not
bytes: MeshAnchor has its own namespace (``~/.config/meshanchor``,
``/etc/meshanchor``, ``$MESHANCHOR_FLEET_HOSTS``), no per-repo tier and no
example file, so MF's sister-repo branch and ``cp`` line do not apply here.

The defect: with ``$MESHANCHOR_FLEET_HOSTS`` set to a MISSING path, the
consumers advised "create ~/.config/meshanchor/fleet_hosts" — false, a set
override is authoritative, so that file is never read.

Isolation: HOME is a scratch dir, overrides name ``*.invalid`` paths, every
network tool on PATH is a stub that records a call and fails, and a box
carrying a real /etc/meshanchor tier SKIPS the script run (a script cannot
redirect the /etc tier and would resolve a REAL fleet).
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from utils.fleet_hosts import explain_absent  # noqa: E402

NET_TOOLS = ("ssh", "scp", "rsync", "curl", "wget", "ping", "nc", "ncat",
             "socat", "sshpass", "nmap")
_etc_tier = [p for p in (Path("/etc/meshanchor/fleet_hosts"),) if os.path.lexists(p)]
needs_no_etc_tier = pytest.mark.skipif(
    bool(_etc_tier), reason="this box carries %s - the script would resolve "
    "a REAL fleet; never run it here" % _etc_tier)


def _run(script: str, tmp_path: Path, env_extra: dict, home: bool = True):
    d = tmp_path / "bin"
    d.mkdir()
    for name in NET_TOOLS:
        p = d / name
        p.write_text("#!/bin/sh\necho \"$0 $*\" >> \"%s\"\nexit 99\n"
                     % (tmp_path / "net_called"))
        p.chmod(0o755)
    env = {"PATH": "%s:%s" % (d, os.environ.get("PATH", "/usr/bin:/bin"))}
    if home:
        (tmp_path / "home").mkdir()
        env["HOME"] = str(tmp_path / "home")
    env.update(env_extra)
    r = subprocess.run(["bash", str(ROOT / "scripts" / f"{script}.sh")],
                       env=env, capture_output=True, text=True, timeout=60,
                       cwd=str(tmp_path))
    called = tmp_path / "net_called"
    assert not called.exists(), "a network tool was invoked: %s" % called.read_text()
    return r


@needs_no_etc_tier
def test_missing_override_names_it_and_never_says_create_home_list(tmp_path):
    missing = str(tmp_path / "fleet_hosts.invalid")
    r = _run("lab_traffic_rollup", tmp_path, {"MESHANCHOR_FLEET_HOSTS": missing})
    assert r.returncode == 2, (r.returncode, r.stderr)
    assert missing in r.stderr and "authoritative" in r.stderr, r.stderr
    assert ".config/meshanchor/fleet_hosts" not in r.stderr, r.stderr


@needs_no_etc_tier
def test_no_override_says_what_was_checked_and_where_to_create(tmp_path):
    r = _run("lab_traffic_rollup", tmp_path, {})
    assert r.returncode == 2, (r.returncode, r.stderr)
    home_list = str(tmp_path / "home" / ".config" / "meshanchor" / "fleet_hosts")
    assert "checked " in r.stderr, r.stderr
    assert "Create one at %s (" % home_list in r.stderr, r.stderr
    assert r.stderr.count("no fleet") == 1, r.stderr


@needs_no_etc_tier
def test_home_unset_points_at_etc_never_at_a_home_list(tmp_path):
    r = _run("lab_traffic_rollup", tmp_path, {}, home=False)
    assert r.returncode == 2, (r.returncode, r.stderr)
    assert "HOME is unset" in r.stderr, r.stderr
    assert "Create /etc/meshanchor/fleet_hosts" in r.stderr, r.stderr
    assert ".config/meshanchor" not in r.stderr, r.stderr


# ── explain_absent: THE words, pure (env + etc_dir injected) ────────────

def test_both_overrides_set_advice_names_both(tmp_path):
    env = {"HOME": str(tmp_path), "MESHANCHOR_FLEET_HOSTS": "/x/a.invalid",
           "FLEET_HOSTS": "/x/b.invalid"}
    why, advice = explain_absent(env=env, etc_dir=str(tmp_path / "etc"))
    assert "/x/a.invalid" in why
    assert "$MESHANCHOR_FLEET_HOSTS and $FLEET_HOSTS" in " ".join(advice)


def test_legacy_alias_alone_is_authoritative_too(tmp_path):
    why, advice = explain_absent(env={"HOME": str(tmp_path), "FLEET_HOSTS": "/x/b.invalid"},
                                 etc_dir=str(tmp_path / "etc"))
    assert "$FLEET_HOSTS=/x/b.invalid" in why
    assert not any(".config/meshanchor" in a for a in advice)


def test_advice_names_only_meshanchor_paths(tmp_path):
    """A shape port: never MF's namespace, per-repo tier, or example file."""
    for env in ({"HOME": str(tmp_path)}, {}):
        why, advice = explain_absent(env=env, etc_dir=str(tmp_path / "etc"))
        text = "\n".join([why] + advice)
        assert "meshforge" not in text.lower(), text


def test_advice_is_one_line_per_entry_ascii(tmp_path):
    for env in ({"HOME": str(tmp_path)}, {}, {"MESHANCHOR_FLEET_HOSTS": "/x.invalid"}):
        why, advice = explain_absent(env=env, etc_dir=str(tmp_path))
        for line in [why] + advice:
            assert "\n" not in line and line.isascii(), line


def test_shell_rc10_line1_is_the_why_and_the_rest_is_advice(tmp_path):
    """--shell contract: $FLEET_HOSTS_WHY stays ONE line (honest_status and
    friends read it as a status line); advice rides after it."""
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"),
           "HOME": str(tmp_path), "MESHANCHOR_FLEET_HOSTS": str(tmp_path / "x.invalid")}
    r = subprocess.run(["bash", "-c", '. "$1"; set -u; fleet_hosts_resolve; rc=$?; '
                        'printf "rc=%s\\nWHY=%s\\n---\\n%s\\n" "$rc" "$FLEET_HOSTS_WHY" '
                        '"$FLEET_HOSTS_ADVICE"', "_",
                        str(ROOT / "scripts" / "lib" / "fleet_hosts.sh")],
                       env=env, capture_output=True, text=True, timeout=60)
    out = r.stdout
    assert "rc=1" in out, (out, r.stderr)
    head, advice = out.split("\n---\n", 1)
    why_lines = head.split("WHY=", 1)[1].splitlines()
    assert len(why_lines) == 1 and "does not exist" in why_lines[0], out
    assert "The override is authoritative" in advice, out


def test_rollup_pane_uses_the_resolver_words(monkeypatch, tmp_path):
    import utils.fleet_hosts as fh
    calls = []
    monkeypatch.setattr(fh, "explain_absent",
                        lambda *a, **k: calls.append(1) or ("WHY-MARK", ["ADVICE-MARK"]))
    import mini_dudeai.rollup as rollup
    # pin: NO list resolves (never this box's real one)
    monkeypatch.setenv("MESHANCHOR_FLEET_HOSTS", str(tmp_path / "x.invalid"))
    seen = {}
    monkeypatch.setattr(rollup, "_main_render",
                        lambda args, now, no_data: seen.setdefault("t", no_data) and 0)
    rollup.main([])
    assert calls and "WHY-MARK" in seen["t"] and "ADVICE-MARK" in seen["t"]
    assert ".config/meshanchor/fleet_hosts" not in seen["t"]


def test_one_message_no_consumer_hardcodes_its_own_advice():
    offenders = []
    for p in sorted((ROOT / "scripts").glob("*.sh")):
        t = p.read_text(errors="replace")
        if "fleet_hosts_resolve" not in t:
            continue
        for needle in ("Create one at", "create ~/.config/meshanchor/fleet_hosts"):
            if needle in t:
                offenders.append("%s: %r" % (p.name, needle))
    assert not offenders, offenders
    rollup = (ROOT / "src" / "mini_dudeai" / "rollup.py").read_text()
    assert "create ~/.config/meshanchor/fleet_hosts" not in rollup


def test_rollup_pane_never_calls_a_list_it_just_read_absent(monkeypatch, tmp_path, capsys):
    """Port review #1 (2026-11-06, REPRODUCED): an EXISTING list naming only
    this box (meshanchor-server's authored list is `localhost`) yields no
    postures; the pane said "no fleet list found ... Create one at <it>"."""
    import utils.fleet_hosts as fh
    import mini_dudeai.rollup as rollup
    lst = tmp_path / "fleet_hosts"
    lst.write_text("localhost\n")
    monkeypatch.setenv("MESHANCHOR_FLEET_HOSTS", str(lst))
    monkeypatch.setattr(fh, "explain_absent",
                        lambda *a, **k: pytest.fail("explain_absent for a list that exists"))
    monkeypatch.setattr(rollup, "collect_fleet", lambda now: [])
    assert rollup.main([]) == 0
    out = capsys.readouterr().out
    assert str(lst) in out and "names no other box" in out, out
    assert "Create" not in out and "does not exist" not in out, out


def test_rollup_pane_absent_list_still_explains(monkeypatch, tmp_path, capsys):
    import mini_dudeai.rollup as rollup
    missing = tmp_path / "x.invalid"
    monkeypatch.setenv("MESHANCHOR_FLEET_HOSTS", str(missing))
    monkeypatch.setattr(rollup, "collect_fleet", lambda now: [])
    assert rollup.main([]) == 0
    out = capsys.readouterr().out
    assert str(missing) in out and "authoritative" in out, out


def test_explain_absent_never_raises_and_says_so(monkeypatch):
    """Twin of MeshForge d003d497: a wording failure must never become a
    traceback or skip a refusal; it says so, with no advice built on inputs
    it could not read."""
    import utils.fleet_hosts as fh

    def boom(*a, **k):
        raise RuntimeError("no home for this uid")
    monkeypatch.setattr(fh, "_explain_absent", boom)
    why, advice = fh.explain_absent()
    assert "could not be worded" in why and "RuntimeError" in why
    assert advice == []


def test_the_why_is_one_line_even_from_a_newline_home(tmp_path):
    odd = tmp_path / "a\nb"
    why, advice = explain_absent(env={"HOME": str(odd)}, etc_dir=str(tmp_path / "etc"))
    for line in [why] + advice:
        assert "\n" not in line and "\r" not in line, line
