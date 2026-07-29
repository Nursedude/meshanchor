#!/usr/bin/env bash
# Behavior test for the honest_status.sh suite-failure preservation (2026-07-11,
# GH #144 follow-up). Drives the REAL script with a fake `python3` that injects a
# pytest failure, and asserts:
#   1. the failing run's log is preserved to a durable (non-/tmp) path,
#   2. the failing test id is surfaced in the output line,
#   3. a subsequent GREEN run does NOT clobber the preserved failure log.
# Fleet legs are neutralised (HONEST_BOXES="" → UNKNOWN, no ssh); gh stubbed off.
#
# ⚠️ It drives a FAKE repo, and that is load-bearing (2026-07-29). honest_status
# prefers `$REPO/venv/bin/python` — an ABSOLUTE path that routes straight around
# this harness's PATH stub wherever that venv exists. Pointed at the real repo it
# therefore stopped stubbing on meshanchor-server, the ONLY box with the MA venv
# and the only one that runs this suite: observed there running THREE real nested
# pytest suites for 10+ minutes instead of the injected fixture. It was the MF
# 49c0b703 defect, still live here. The final check below asserts the stub was
# actually used, so a future regression fails loudly instead of quietly testing
# nothing.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
SCRIPT="$HERE/../scripts/honest_status.sh"
STATE_SUBDIR="meshanchor"   # MF twin uses "meshforge"
REAL_PYTHON3="$(command -v python3)"; export REAL_PYTHON3
fails=0

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
SB="$TMP/bin"; mkdir -p "$SB"
# fake python3: `-m pytest` → injected pass/fail per FAKE_PYTEST_FAIL; else real.
cat > "$SB/python3" <<'EOF'
#!/usr/bin/env bash
for a in "$@"; do
  if [ "$a" = "pytest" ]; then
    if [ "${FAKE_PYTEST_FAIL:-1}" = "1" ]; then
      echo "FAILED tests/test_fake_flake.py::test_timing_race - AssertionError: injected"
      echo "1 failed, 42 passed in 3.00s"
      exit 1
    fi
    echo "43 passed in 3.00s"; exit 0
  fi
done
exec "$REAL_PYTHON3" "$@"
EOF
printf '#!/usr/bin/env bash\nexit 1\n' > "$SB/gh"   # no CI reachable → UNKNOWN
chmod +x "$SB"/*

FAKE_HOME="$TMP/home"; mkdir -p "$FAKE_HOME"
logf="$FAKE_HOME/.local/state/$STATE_SUBDIR/hs_failures/last_failure.log"

# Fake repo: no venv/bin/python inside it, so honest_status falls back to
# `python3` and the PATH stub above is what actually answers.
FAKE_REPO="$TMP/repo"; mkdir -p "$FAKE_REPO/tests" "$FAKE_REPO/scripts"
printf 'import sys\nsys.exit(0)\n' > "$FAKE_REPO/scripts/lint.py"

run() {  # $1 = FAKE_PYTEST_FAIL
  PATH="$SB:$PATH" HOME="$FAKE_HOME" XDG_STATE_HOME="" HONEST_BOXES="" \
    MESHANCHOR_REPO="$FAKE_REPO" FAKE_PYTEST_FAIL="$1" bash "$SCRIPT" 2>&1
}

check() { if [ -n "$2" ]; then echo "PASS: $1"; else echo "FAIL: $1"; fails=1; fi; }

# ── failing run ──
out_fail="$(run 1)"
check "persistent failure log written (not /tmp)" "$([ -f "$logf" ] && echo ok)"
check "preserved log holds the FAILED traceback" "$(grep -q 'test_timing_race' "$logf" 2>/dev/null && echo ok)"
check "output line surfaces the failing test id" "$(echo "$out_fail" | grep -q 'test_timing_race' && echo ok)"

# ── subsequent GREEN run must NOT clobber the preserved failure log ──
before="$(cat "$logf" 2>/dev/null)"
out_ok="$(run 0)"
after="$(cat "$logf" 2>/dev/null)"
check "green run leaves the failure log intact" "$([ -f "$logf" ] && [ "$before" = "$after" ] && echo ok)"
check "green run reports the suite as passing" "$(echo "$out_ok" | grep -Eq 'full suite.*(exit 0|passed)' && echo ok)"

# ── the harness must prove it STUBBED — the failure mode is silence ──────
# "43 passed in 3.00s" is a string only the fake python3 can emit. If the real
# interpreter ran instead, every assertion above was measuring the real suite
# and this file was testing nothing at all.
check "the fake pytest was actually invoked (stub not bypassed)" \
  "$(echo "$out_ok" | grep -q '43 passed in 3.00s' && echo ok)"

echo "---"
if [ "$fails" = 0 ]; then echo "ALL PASS"; exit 0; else echo "FAILED"; exit 1; fi
