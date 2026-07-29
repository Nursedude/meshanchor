#!/usr/bin/env bash
# pytest_tmp_prune.sh — keep pytest's temp tree bounded, because /tmp is RAM.
#
# Ported from the MeshForge twin 2026-07-29. PREVENTIVE here — read the
# provenance of each claim below before treating any number as MeshAnchor's.
#
# WHY (measured 2026-07-28 on the MESHFORGE manager box, NOT on this fleet):
# /tmp is a tmpfs on every fleet Pi, so pytest's per-test temp tree is
# unreclaimable RAM, not disk. A green MF full-suite run left ~5,272 entries
# (~79 MB of shmem), and run dirs accumulated to 37 over two days — 3.0 GB,
# which drove user.slice to 91% of its 8 GB cap and tripped memory_cap_engaged.
#
# MESHANCHOR'S OWN EXPOSURE (measured 2026-07-29 on meshanchor-server, the only
# box carrying this repo's venv and therefore the only one that runs the
# suite): /tmp IS a tmpfs, 1.9 GB on a 3.8 GB box — so the ceiling is HALF the
# machine's RAM, proportionally worse than the MF manager. Currently there are
# ZERO pytest run dirs there (5.4 MB used), so nothing has accumulated yet.
# This script is insurance against a class already observed on the sister
# fleet, not a fix for an observed MeshAnchor incident. Do not cite it as one.
#
# Why not fix it in pytest's own config: on MF, setting
# tmp_path_retention_policy to "failed" cut a run to 64 KB, but pytest
# truncates temp dir names to 30 chars, so same-named tests in different
# classes collide and deleting a passing test's dir frees the number for reuse
# — 4 tests then read a previous test's state file and failed. Measured there,
# then reverted. A flaky suite is strictly worse than 79 MB.
#
# Why not rely on pytest's own retention: it works when observed, but was
# unexplained over the window where 37 dirs survived on MF, and it runs at
# atexit — and this interpreter's shutdown is demonstrably unreliable on this
# fleet (the exit-status race pinned by tests/test_honest_status_suite_leg.sh,
# which THIS repo reproduced during that port). This prune does not depend on
# knowing the answer.
#
# SAFETY: never touches a run dir whose .lock is younger than pytest's own
# LOCK_TIMEOUT — that dir may belong to a pytest running right now.
#
# Usage (crontab idiom — the script emits its own verdict; the `||` guard
# catches the case where the script itself dies before it can speak):
#   40 */6 * * * /opt/meshanchor/scripts/pytest_tmp_prune.sh >/dev/null 2>&1 \
#     || /opt/meshanchor/scripts/cron_verdict.sh pytest_tmp_prune FAIL wrapper_crashed
set -u

KEEP="${PYTEST_TMP_KEEP:-3}"

# The lock window is pytest's LOCK_TIMEOUT, DERIVED at runtime — not a second
# copy of it (honest_failure_modes #5: two consumers of one constant WILL
# drift). It already HAD: this was hardcoded 10800 with the comment "pytest's
# own LOCK_TIMEOUT (3h)", but pytest's LOCK_TIMEOUT is 60*60*24*3 — THREE DAYS,
# 259200s. Off by 24x, so the safety property stated in this header ("never
# touches a run dir whose .lock is younger than pytest's own LOCK_TIMEOUT")
# was violated for every lock aged between 3 hours and 3 days: the pruner
# would delete the temp tree of a run pytest still considers live. Found by
# review 2026-07-28, by asking the constant's owner instead of re-typing it.
#
# LOCK_FALLBACK_S is test-pinned to the live value, so if upstream changes it
# the suite says so rather than the pruner silently going unsafe again.
LOCK_FALLBACK_S=259200                             # pytest LOCK_TIMEOUT (3 days)
_pytest_lock_timeout() {
  python3 - <<'PY' 2>/dev/null
try:
    from _pytest.pathlib import LOCK_TIMEOUT
    print(int(LOCK_TIMEOUT))
except Exception:
    pass
PY
}
LOCK_MAX_AGE_S="${PYTEST_TMP_LOCK_AGE_S:-$(_pytest_lock_timeout)}"
LOCK_MAX_AGE_S="${LOCK_MAX_AGE_S:-$LOCK_FALLBACK_S}"
CONCERN_MB="${PYTEST_TMP_CONCERN_MB:-1024}"        # freed >= this => say so out loud
BASE="${PYTEST_TMP_BASE:-${TMPDIR:-/tmp}/pytest-of-$(id -un)}"
VERDICT="${CRON_VERDICT_BIN:-$(dirname "$0")/cron_verdict.sh}"

say() {  # $1 = status, $2 = message
  if [ -x "$VERDICT" ]; then "$VERDICT" pytest_tmp_prune "$1" "$2" >/dev/null 2>&1; fi
  echo "pytest_tmp_prune: $1 — $2"
}

# A box that never runs the suite has no tree. That is not an error and not a
# silence to explain — it is nothing to do.
if [ ! -d "$BASE" ]; then
  say OK "no pytest temp tree at $BASE — nothing to do"
  exit 0
fi

now=$(date +%s)
before_kb=$(du -sk "$BASE" 2>/dev/null | cut -f1); before_kb=${before_kb:-0}

# Newest-first BY RUN NUMBER, not mtime: the number is the sequence pytest
# itself assigns, and mtime moves when anything inside is touched.
mapfile -t dirs < <(ls -d "$BASE"/pytest-[0-9]* 2>/dev/null \
                    | sed 's/.*pytest-//' | sort -rn | sed "s|^|$BASE/pytest-|")
total=${#dirs[@]}

pruned=0; skipped_locked=0; failed=0
i=0
for d in "${dirs[@]}"; do
  i=$((i + 1))
  [ "$i" -le "$KEEP" ] && continue          # keep the newest $KEEP
  lock="$d/.lock"
  if [ -e "$lock" ]; then
    lock_age=$(( now - $(stat -c %Y "$lock" 2>/dev/null || echo 0) ))
    if [ "$lock_age" -lt "$LOCK_MAX_AGE_S" ]; then
      skipped_locked=$((skipped_locked + 1))   # a live run may own this
      continue
    fi
  fi
  if rm -rf "$d" 2>/dev/null; then pruned=$((pruned + 1)); else failed=$((failed + 1)); fi
done

after_kb=$(du -sk "$BASE" 2>/dev/null | cut -f1); after_kb=${after_kb:-0}
freed_mb=$(( (before_kb - after_kb) / 1024 ))
remain=$(ls -d "$BASE"/pytest-[0-9]* 2>/dev/null | wc -l)
detail="pruned $pruned of $total dir(s), freed ${freed_mb} MB, $remain remain"
[ "$skipped_locked" -gt 0 ] && detail="$detail, $skipped_locked skipped (lock < ${LOCK_MAX_AGE_S}s — live run)"

if [ "$failed" -gt 0 ]; then
  say FAIL "$detail, $failed could NOT be removed"
  exit 1
fi
if [ "$freed_mb" -ge "$CONCERN_MB" ]; then
  # Routine pruning is OK and stays quiet. Freeing a GB+ means accumulation
  # outran this cadence — the condition that actually cost memory. Say it, and
  # self-clear next run (the fleet_hosts_drift convention).
  say CONCERN "$detail — accumulation outran the prune cadence"
  exit 0
fi
say OK "$detail"
exit 0
