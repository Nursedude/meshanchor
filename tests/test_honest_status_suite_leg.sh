#!/usr/bin/env bash
# Behavior test for honest_status.sh's SUITE leg (ported from the MF twin
# 2026-07-29).
#
# WHY: pytest's process exit status is not trustworthy on this fleet. Measured
# on the MeshForge suite 2026-07-28 — the interpreter exits 0 while pytest's
# own pytest_sessionfinish hook reports `ExitCode.TESTS_FAILED: 1`,
# testsfailed=1. Byte-identical output, ~50% of runs, and it disappears when
# instrumentation adds work at shutdown, so it is a shutdown race, not
# bookkeeping. That is a property of the interpreter and the fleet, not of one
# repo: MeshAnchor runs both.
#
# The gate survives that by luck when the run also prints FAILED lines. It does
# NOT survive a lost exit code beside an INTERNALERROR (which starts
# "INTERNALERROR>" and matches neither ^FAILED nor ^ERROR), nor a crash with no
# summary at all — rc=0 + nfail=0 reads as PASS. Those cases are pinned below.
#
# Drives the REAL script with a stub pytest whose OUTPUT and EXIT CODE are set
# independently, which is the only way to reproduce "says failed, exits 0".
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
SCRIPT="$HERE/../scripts/honest_status.sh"
REAL_PYTHON3="$(command -v python3)"; export REAL_PYTHON3
STATE_SUBDIR="meshanchor"   # MF twin uses "meshforge"
fails=0

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
SB="$TMP/bin"; mkdir -p "$SB"

# Fake repo. NOT cosmetic: honest_status prefers $REPO/venv/bin/python, an
# ABSOLUTE path that routes around this PATH stub wherever that venv exists —
# on this fleet that is meshanchor-server, the box where the suite actually
# runs. A harness pointed at the real repo silently stops stubbing there and
# its assertions can no longer fail (the MF 49c0b703 lesson).
FAKE_REPO="$TMP/repo"; mkdir -p "$FAKE_REPO/tests" "$FAKE_REPO/scripts"
printf 'import sys\nsys.exit(0)\n' > "$FAKE_REPO/scripts/lint.py"

cat > "$SB/python3" <<'EOF'
#!/usr/bin/env bash
for a in "$@"; do
  if [ "$a" = "pytest" ]; then
    printf '%b' "${FAKE_PYTEST_OUT:-}"
    exit "${FAKE_PYTEST_RC:-0}"
  fi
done
exec "$REAL_PYTHON3" "$@"
EOF
for c in gh ssh curl; do printf '#!/usr/bin/env bash\nexit 1\n' > "$SB/$c"; done
chmod +x "$SB"/*

FAKE_HOME="$TMP/home"; mkdir -p "$FAKE_HOME"

run() {  # $1 = fake rc, $2 = fake stdout
  PATH="$SB:$PATH" HOME="$FAKE_HOME" XDG_STATE_HOME="" HONEST_BOXES="hs-dummy" \
    MESHANCHOR_REPO="$FAKE_REPO" FAKE_PYTEST_RC="$1" FAKE_PYTEST_OUT="$2" \
    bash "$SCRIPT" 2>&1 | grep "full suite"
}

check() { if [ -n "$2" ]; then echo "PASS: $1"; else echo "FAIL: $1"; fails=1; fi; }

# ── the measured bug: exit code lost, output says it failed ───────────────
out="$(run 0 'FAILED tests/t.py::test_x - boom\n1 failed, 10 passed in 1.00s\n')"
check "lost exit code + FAILED lines => FAIL" \
  "$(echo "$out" | grep -q 'FAIL' && echo ok)"

# ── THE HOLE: lost exit code + INTERNALERROR (matches neither ^FAILED nor
#    ^ERROR) and no summary. Old logic: rc=0, nfail=0 => PASS. ─────────────
out="$(run 0 'INTERNALERROR> Traceback (most recent call last):\nINTERNALERROR> RuntimeError: plugin blew up\n')"
check "lost exit code + INTERNALERROR is never PASS" \
  "$(echo "$out" | grep -q 'PASS' && echo '' || echo ok)"

# ── THE OTHER HOLE: crash with no summary at all. ────────────────────────
out="$(run 0 '')"
check "no summary line => UNKNOWN, not PASS" \
  "$(echo "$out" | grep -q 'UNKNOWN' && echo ok)"

# ── summary says failed but no FAILED lines (torn/-q output) ─────────────
out="$(run 0 '1 failed, 3 passed in 0.50s\n')"
check "summary reporting failures => FAIL even with rc=0" \
  "$(echo "$out" | grep -q 'FAIL' && echo ok)"

# ── disagreement the other way: clean output, non-zero code ──────────────
out="$(run 1 '10 passed in 1.00s\n')"
check "nonzero exit with clean output => FAIL (trust the worse signal)" \
  "$(echo "$out" | grep -q 'FAIL' && echo ok)"

# ── a broken invocation must not read green ──────────────────────────────
out="$(run 0 'no tests ran in 0.01s\n')"
check "'no tests ran' => UNKNOWN, nothing was verified" \
  "$(echo "$out" | grep -q 'UNKNOWN' && echo ok)"

# ── and the genuinely healthy run still passes ───────────────────────────
out="$(run 0 '1234 passed, 1 skipped in 42.00s\n')"
check "healthy suite still reads PASS" \
  "$(echo "$out" | grep -q 'PASS' && echo ok)"

# ── non-green runs preserve the log for forensics ────────────────────────
logf="$FAKE_HOME/.local/state/$STATE_SUBDIR/hs_failures/last_failure.log"
rm -f "$logf"
run 0 'INTERNALERROR> boom\n' >/dev/null
check "an UNKNOWN/FAIL run preserves the pytest log" \
  "$([ -f "$logf" ] && echo ok)"

echo "---"
if [ "$fails" = 0 ]; then echo "ALL PASS"; exit 0; else echo "FAILED"; exit 1; fi
