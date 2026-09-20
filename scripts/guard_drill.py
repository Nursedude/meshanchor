#!/usr/bin/env python3
"""Prove each scanner-based regression guard FIRES on a live violation.

Not part of the test suite — it spawns one pytest subprocess per contract, so
it costs real time and belongs on demand, not on every run.

    python3 scripts/guard_drill.py [repo_root]

Why it exists: on 2026-08-05 every scanner-based guard in
tests/test_regression_guards.py was found inert — SRC_DIR was
`<repo>/tests/../src`, so every scanned path contained `/tests/` and the
guards' own `/tests/` skip discarded every match. They had never worked, from
the file's first commit. All of them still PASSED, which is the point: a guard
that has never failed is not evidence that it works.

Fixing the shared cause was not enough. Re-drilling afterwards found two
contracts still silent for reasons of their own — a numeric `KNOWN_EXCEPTIONS`
budget that had become pure unused slack, and a test that collected violations
and then did nothing with them. Only a planted violation finds those.

Run this after touching the guards, the scanner, or any ALLOWLIST.

Layer D (ported from MeshForge 2026-09-20) also drills the LAUNCHER: it runs
the real scripts/meshanchor-launcher.sh through symlinks named like the
installed commands, with a fake `sudo` first on PATH that prints the argv
instead of executing it, and asserts the exact argv main.py receives —
MeshAnchor's OWN contract, which differs from MeshForge's (see Layer D).
Run it after touching the launcher script or the installer's
/usr/local/bin commands — a `bash -n` cannot see a wrong argv.
"""
import os
import subprocess
import sys

# Default to the repo THIS file lives in. Until 2026-09-20 the default was a
# literal "/opt/meshforge" carried over from the twin, so `python3
# scripts/guard_drill.py` from /opt/meshanchor drilled MeshForge and reported
# on it under the wrong name (caught when the launcher layer refused: every
# case WRONG, self-test MISSING — the pinned contract did its job).
REPO = (sys.argv[1] if len(sys.argv) > 1
        else os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
APP = "meshforge" if REPO.rstrip("/").endswith("meshforge") else "meshanchor"
DRILL = os.path.join(REPO, "src", "utils", "_audit_drill_tmp.py")

HEADER = '"""TEMPORARY audit drill file. Removed by the harness."""\n'

CASES = [
    ("TestTCPConnectionContract", HEADER + (
        "from meshtastic.tcp_interface import TCPInterface\n\n\n"
        "def drill():\n"
        "    return TCPInterface(hostname='localhost')\n")),
    ("TestRNSReticulumChokepoint", HEADER + (
        "import RNS\n\n\n"
        "def drill():\n"
        "    return RNS.Reticulum(configdir='/etc/reticulum')\n")),
    ("TestServiceCheckContract", HEADER + (
        "import subprocess\n\n\n"
        "def drill():\n"
        "    return subprocess.run(['systemctl', 'is-active', 'rnsd'],\n"
        "                          capture_output=True, timeout=5)\n")),
    ("TestPathHomeContract", HEADER + (
        "from pathlib import Path\n\n\n"
        "def drill():\n"
        "    cfg = Path.home() / '.config'\n"
        "    return cfg\n")),
    ("TestNoShellTrue", HEADER + (
        "import subprocess\n\n\n"
        "def drill(x):\n"
        "    return subprocess.run(f'echo {x}', shell=True, timeout=5)\n")),
    ("TestPipInvocationContract", HEADER + (
        "import subprocess\n\n\n"
        "def drill():\n"
        "    return subprocess.run(['pip3', 'install', 'meshtastic'], timeout=60)\n")),
    ("TestSqliteConnectContract", HEADER + (
        "import sqlite3\n\n\n"
        "def drill(path):\n"
        "    return sqlite3.connect(path)\n")),
    ("TestBackupNeverOverwrites", HEADER + (
        "from pathlib import Path\n\n\n"
        "def drill(backup_dir: Path, backup_id: str) -> Path:\n"
        "    return backup_dir / f'{backup_id}.json'\n")),
]

results = []
for guard, body in CASES:
    try:
        with open(DRILL, "w", encoding="utf-8") as fh:
            fh.write(body)
        proc = subprocess.run(
            [sys.executable, "-m", "pytest",
             f"tests/test_regression_guards.py::{guard}", "-q", "--no-header",
             "-p", "no:cacheprovider"],
            cwd=REPO, capture_output=True, text=True, timeout=300)
        # pytest exits NON-ZERO for a node id that does not exist (4 = usage
        # error, 5 = nothing collected) — the same shape as "the guard fired".
        # Verified 2026-09-07: a bogus class gives rc=4, so a RENAMED OR
        # DELETED guard was being counted in "N/M contracts fired" and the
        # drill exited 0. The drill that exists to prove guards work would
        # bless a guard that does not exist ("a drill that defeats a guard
        # must first assert the guard EXISTS").
        missing = (proc.returncode in (4, 5)
                   or "no tests ran" in proc.stdout
                   or "collected 0 items" in proc.stdout)
        fired = (not missing) and proc.returncode != 0
        named = "_audit_drill_tmp" in proc.stdout
        results.append((guard, fired, named, missing))
    finally:
        if os.path.exists(DRILL):
            os.remove(DRILL)

assert not os.path.exists(DRILL), "drill file left behind!"

print(f"### {APP}: does each contract fire on a live violation?\n")
silent = []
missing_guards = []
for guard, fired, named, missing in results:
    if missing:
        verdict = "MISSING — no such contract; NOTHING was drilled"
        missing_guards.append(guard)
    elif fired and named:
        verdict = "FIRES  (and names the file)"
    elif fired:
        verdict = "fires  (did NOT name the drill file — check it caught the right thing)"
    else:
        verdict = "SILENT — a live violation passed"
        silent.append(guard)
    print(f"  {guard:34s} {verdict}")

# Layer C: announce the SCOPE actually exercised, not just the outcome. A drill
# that ran against nothing must not read as a clean sweep.
drilled = len(results) - len(missing_guards)
print(f"\nguard_drill: drilled {drilled} of {len(CASES)} declared contract(s); "
      f"{drilled - len(silent)} fired.")
if not results or drilled == 0:
    print("UNKNOWN: no contract was actually drilled — this proves NOTHING.")
    sys.exit(2)
if missing_guards:
    print("MISSING (renamed or deleted — the drill could not test them): "
          + ", ".join(missing_guards))
if silent:
    print("SILENT: " + ", ".join(silent))


# ---------------------------------------------------------------------------
# Layer D: the LAUNCHER argv contract — run the real script, intercept the
# privileged exec, assert the exact argv.
#
# PORTED from MeshForge guard_drill.py (MF 942b2a8a, 2026-09-20) — with
# MeshAnchor's OWN contract pinned, not MeshForge's. Read this before
# "fixing" a WRONG line by copying MF's expectations across: the two
# launchers are different programs today.
#
#   MeshForge  (after MF efdc6615): script routes through src/launcher.py,
#              `meshanchor-tui`/`tui` pass `--tui`, PYTHONPYCACHEPREFIX rides
#              every privileged launch, installer SYMLINKS /usr/local/bin/*
#              to the script.
#   MeshAnchor (this file, as of fc9f5f50): scripts/meshanchor-launcher.sh
#              goes STRAIGHT to src/launcher_tui/main.py with bare
#              `sudo python3`, forwards "$@" unchanged under EVERY name, no
#              pycache prefix, and install.sh still writes /usr/local/bin/
#              meshanchor + meshanchor-tui as GENERATED COPIES (`cat >`) that
#              run a DIFFERENT program (src/launcher.py) — the two-program
#              drift MF closed on 2026-09-20 is still open here.
#
# WHY the drill exists: MF's launch-path change was deployed 9/9 having been
# verified with `bash -n` and `help` only; the adversarial review found two
# argv defects by RUNNING it. A syntax check cannot see a wrong argv. This is
# the harness that found them: a fake `sudo` first on PATH that PRINTS its
# argv instead of executing it, invoked through symlinks carrying the
# installed names so any basename dispatch is exercised for real.
#
# Doctrine: a drill that has never failed is not evidence. So it proves
# ITSELF on a temp COPY of the script with the `"$@"` forwarding removed
# (never the tracked file — a `git checkout` "restore" on a dirty file
# reverted a session's own edits on 2026-09-20). If that copy passes, the
# drill is inert and exits non-zero.
#
# ⚠️ Coverage, stated honestly (calibrated_claims "what would still pass
# this check if the feature were dead?"): this drills the SCRIPT. On a box
# where /usr/local/bin/meshanchor is a generated copy, the command operators
# type is NOT this script and this drill says nothing about it — the drill
# prints that as a WARN rather than letting "5/5 OK" read as fleet coverage.
# ---------------------------------------------------------------------------
import shutil
import stat
import tempfile

LAUNCHER = os.path.join(REPO, "scripts", f"{APP}-launcher.sh")
MAIN_PY = f"/opt/{APP}/src/launcher_tui/main.py"
# (installed name, argv, EXACT intercepted argv). MeshAnchor's launcher
# forwards "$@" to main.py verbatim under every name; `tui` is consumed.
# Flags are ones main.py actually declares (--debug, --no-startup-checks).
LAUNCHER_CASES = [
    (f"{APP}-tui", [], f"python3 {MAIN_PY}"),
    (f"{APP}-tui", ["--no-startup-checks"], f"python3 {MAIN_PY} --no-startup-checks"),
    (APP, ["tui", "--no-startup-checks"], f"python3 {MAIN_PY} --no-startup-checks"),
    (APP, [], f"python3 {MAIN_PY}"),
    (APP, ["--debug"], f"python3 {MAIN_PY} --debug"),
]
# The line the self-test removes the forwarding from. If the launcher is
# ever rewritten (e.g. ported to MF's launcher.py + --tui shape), this marker
# goes missing and the self-test reports MISSING rather than passing.
FORWARD_LINE = 'exec sudo python3 "$script" "$@"'
FAKE = "#!/bin/bash\necho \"FAKE-EXEC: $*\"\n"


def _run_launcher(script, name, argv):
    """Invoke `script` through a symlink called `name`, with a fake sudo
    first on PATH. Returns the intercepted argv line (or the raw output)."""
    with tempfile.TemporaryDirectory(prefix="guard_drill_launcher_") as d:
        p = os.path.join(d, "sudo")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(FAKE)
        os.chmod(p, os.stat(p).st_mode | stat.S_IXUSR)
        link = os.path.join(d, name)
        os.symlink(script, link)
        env = dict(os.environ, PATH=d + os.pathsep + os.environ.get("PATH", ""))
        proc = subprocess.run([link] + argv, env=env, capture_output=True,
                              text=True, timeout=30)
        out = (proc.stdout + proc.stderr).strip()
        for line in out.splitlines():
            if line.startswith("FAKE-EXEC: "):
                return line[len("FAKE-EXEC: "):]
        return out or f"(no output, rc={proc.returncode})"


print(f"\n### {APP}: does the launcher hand main.py the argv it documents?\n")
launcher_failed = []
launcher_ran = 0
if os.geteuid() == 0:
    # The EUID==0 branch is `exec python3 "$script"` — no sudo to intercept,
    # so a fake sudo would let the real TUI launch. Refuse, loudly.
    print("  UNKNOWN — running as root: the launcher's root branch execs "
          "python3 directly and cannot be intercepted; run this unprivileged")
elif not os.path.exists(LAUNCHER):
    print(f"  MISSING — {LAUNCHER} does not exist; NOTHING was drilled")
else:
    for name, argv, expected in LAUNCHER_CASES:
        label = " ".join([name] + argv)
        line = _run_launcher(LAUNCHER, name, argv)
        ok = line.strip() == expected
        launcher_ran += 1
        if not ok:
            launcher_failed.append(label)
        print(f"  {label:40s} {'OK    ' if ok else 'WRONG '} -> …{line[-72:]}")

    # Self-test: the drill must FAIL on a copy that drops "$@".
    with tempfile.TemporaryDirectory(prefix="guard_drill_broken_") as d:
        broken = os.path.join(d, os.path.basename(LAUNCHER))
        with open(LAUNCHER, encoding="utf-8") as fh:
            src = fh.read()
        if FORWARD_LINE not in src:
            print(f"  {'self-test':40s} MISSING — forwarding line not found in "
                  "the launcher; the script changed shape, re-pin "
                  "LAUNCHER_CASES + FORWARD_LINE (do NOT copy MF's)")
            launcher_failed.append("self-test:marker-missing")
        else:
            with open(broken, "w", encoding="utf-8") as fh:
                fh.write(src.replace(FORWARD_LINE, 'exec sudo python3 "$script"'))
            shutil.copymode(LAUNCHER, broken)
            line = _run_launcher(broken, f"{APP}-tui", ["--no-startup-checks"])
            if line.strip() == f"python3 {MAIN_PY} --no-startup-checks":
                print(f"  {'self-test':40s} SILENT — a copy WITHOUT \"$@\" "
                      "forwarding still passed; this drill proves nothing")
                launcher_failed.append("self-test:inert")
            else:
                print(f"  {'self-test':40s} FIRES  (copy without \"$@\" "
                      f"-> …{line[-40:]})")

    # Coverage notes — what this drill did NOT prove.
    installed = f"/usr/local/bin/{APP}"
    if os.path.islink(installed) and os.path.realpath(installed) == os.path.realpath(LAUNCHER):
        print(f"  {'installed command':40s} covered — {installed} symlinks to the script")
    elif os.path.exists(installed):
        print(f"  {'installed command':40s} WARN   — {installed} is a generated "
              "COPY, not this script: what operators type was NOT drilled "
              "(install.sh `cat >`; MF closed this drift in 4e726548)")
    else:
        print(f"  {'installed command':40s} absent — {installed} not installed here")
    if not os.path.exists(os.path.join(REPO, "scripts", "lib", "pycache_prefix.sh")):
        print(f"  {'pycache prefix':40s} not adopted — privileged launches "
              "write root-owned __pycache__ into the repo (MF b29e26ae)")

print(f"\nguard_drill: launcher drilled {launcher_ran} of {len(LAUNCHER_CASES)} "
      f"argv case(s); {launcher_ran - len([f for f in launcher_failed if not f.startswith('self-test')])} correct.")
if launcher_ran == 0:
    print("UNKNOWN: the launcher was not drilled — this proves NOTHING.")
    sys.exit(2)
if launcher_failed:
    print("LAUNCHER WRONG: " + ", ".join(launcher_failed)
          + f"\n  fix: scripts/{APP}-launcher.sh launch_tui must forward \"$@\" "
          "to src/launcher_tui/main.py unchanged; if the launcher was "
          "deliberately reshaped, re-pin LAUNCHER_CASES + FORWARD_LINE here")

if silent or missing_guards or launcher_failed:
    sys.exit(1)
