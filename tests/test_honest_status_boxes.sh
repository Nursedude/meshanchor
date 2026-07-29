#!/usr/bin/env bash
# Behavior test for honest_status.sh's FLEET legs (MF review port 2026-07-28).
#
# Pins the two classifications the MF adversarial review fixed in both twins:
#
#   SHA-drift leg — empty git output used to conflate three states under one
#   label. Now: box DOWN = unreach (UNKNOWN); box UP with no repo = excluded
#   from the denominator, reported; box UP with the repo but git ERRORING
#   (dubious ownership over ssh, git missing, corrupt .git) = stays IN the
#   denominator as unverified — never a PASS that silently skipped the box.
#
#   Watchdog leg — a watchdog unit that is INSTALLED but not running is a
#   FAULT (FAIL), not merely "unverifiable": a crashlooping watchdog read as
#   soft UNKNOWN trains the operator to ignore the exact box that lost its
#   observer (the MF #82 class). LoadState=not-found stays unverifiable.
#
# Drives the REAL script with a stub ssh so each box's answer is scriptable.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
SCRIPT="$HERE/../scripts/honest_status.sh"
REAL_PYTHON3="$(command -v python3)"; export REAL_PYTHON3
fails=0

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
SB="$TMP/bin"; mkdir -p "$SB"

# Fake repo — a REAL git repo so HEAD resolves (see the MF twin's harness).
FAKE_REPO="$TMP/repo"; mkdir -p "$FAKE_REPO/tests" "$FAKE_REPO/scripts"
printf 'import sys\nsys.exit(0)\n' > "$FAKE_REPO/scripts/lint.py"
git -C "$FAKE_REPO" init -q 2>/dev/null
git -C "$FAKE_REPO" -c user.email=t@t -c user.name=t commit -q --allow-empty -m init 2>/dev/null
FAKE_HEAD="$(git -C "$FAKE_REPO" rev-parse HEAD 2>/dev/null)"; export FAKE_HEAD

cat > "$SB/python3" <<'EOF'
#!/usr/bin/env bash
for a in "$@"; do
  if [ "$a" = "pytest" ]; then echo "1 passed in 0.10s"; exit 0; fi
done
exec "$REAL_PYTHON3" "$@"
EOF

# Stub ssh: the box name selects a canned personality. The watchdog answer
# shape is: <ActiveState> <LoadState> ---BOSEP--- [blackouts json].
cat > "$SB/ssh" <<'EOF'
#!/usr/bin/env bash
box=""; for a in "$@"; do case "$a" in -*|*=*) ;; *) box="$a"; break;; esac; done
cmd="${!#}"
case "$box" in
  box-down) exit 255 ;;                        # unreachable
  box-good)
    case "$cmd" in
      *rev-parse*) echo "HSUP"; echo "$FAKE_HEAD" ;;
      *BOSEP*) echo "active"; echo "loaded"; echo "---BOSEP---"
               echo '{"active": []}' ;;
    esac ;;
  box-norepo)                                  # up; no repo, no watchdog UNIT
    case "$cmd" in
      *rev-parse*) echo "HSUP"; echo "HSNOREPO" ;;
      *BOSEP*) echo "inactive"; echo "not-found"; echo "---BOSEP---" ;;
    esac ;;
  box-giterr)                                  # repo PRESENT, git itself errors
    case "$cmd" in
      *rev-parse*) echo "HSUP"; echo "HSGITERR" ;;
      *BOSEP*) echo "active"; echo "loaded"; echo "---BOSEP---"
               echo '{"active": []}' ;;
    esac ;;
  box-wdfail)                                  # unit INSTALLED but not running
    case "$cmd" in
      *rev-parse*) echo "HSUP"; echo "$FAKE_HEAD" ;;
      *BOSEP*) echo "failed"; echo "loaded"; echo "---BOSEP---" ;;
    esac ;;
  box-blackout)                                # healthy daemon, ACTIVE blackout
    case "$cmd" in
      *rev-parse*) echo "HSUP"; echo "$FAKE_HEAD" ;;
      *BOSEP*) echo "active"; echo "loaded"; echo "---BOSEP---"
               echo '{"active": [{"kind": "http_dead"}]}' ;;
    esac ;;
esac
exit 0
EOF
printf '#!/usr/bin/env bash\nexit 1\n' > "$SB/gh"
printf '#!/usr/bin/env bash\nexit 1\n' > "$SB/curl"
chmod +x "$SB"/*

FAKE_HOME="$TMP/home"; mkdir -p "$FAKE_HOME"

run() {  # env: HONEST_BOXES selects the fleet
  PATH="$SB:$PATH" HOME="$FAKE_HOME" XDG_STATE_HOME="" \
    MESHANCHOR_REPO="$FAKE_REPO" FAKE_HEAD="$FAKE_HEAD" \
    bash "$SCRIPT" --quick "$@" 2>&1
}

check() { if [ -n "$2" ]; then echo "PASS: $1"; else echo "FAIL: $1"; fails=1; fi; }

# ── SHA-drift: three states, three labels ────────────────────────────────
out="$(HONEST_BOXES="box-good box-giterr" run)"
check "git-error box keeps the drift leg UNKNOWN, never PASS" \
  "$(echo "$out" | grep -E 'fleet SHA drift' | grep -q 'UNKNOWN' && echo ok)"
check "and is named git-error with the repo present, not no-repo/unreach" \
  "$(echo "$out" | grep -q 'box-giterr:git-error(repo present)' && echo ok)"

out="$(HONEST_BOXES="box-good box-norepo" run)"
check "no-repo box excluded from the drift denominator (1/1, not 1/2)" \
  "$(echo "$out" | grep -E 'fleet SHA drift' | grep -q '1/1' && echo ok)"
check "and named no-repo, never silently dropped" \
  "$(echo "$out" | grep -q 'box-norepo:no-repo' && echo ok)"

out="$(HONEST_BOXES="box-good box-down" run)"
check "a DOWN box keeps the drift leg UNKNOWN" \
  "$(echo "$out" | grep -E 'fleet SHA drift' | grep -q 'UNKNOWN\|reachable' && echo ok)"

# ── watchdog: installed-but-dead is a FAULT, absent unit is not ──────────
out="$(HONEST_BOXES="box-good box-wdfail" run)"
check "installed-but-dead watchdog unit FAILS the leg" \
  "$(echo "$out" | grep -E 'watchdog' | grep -q 'FAIL' && echo ok)"
check "and names the box + unit state" \
  "$(echo "$out" | grep -q 'box-wdfail:WATCHDOG-UNIT-failed' && echo ok)"

out="$(HONEST_BOXES="box-good box-norepo" run)"
check "unit not-found stays unverifiable (UNKNOWN), no false FAIL" \
  "$(echo "$out" | grep -E 'watchdog signals' | grep -q 'UNKNOWN' && echo ok)"

# ── the leg's pre-existing behavior survives the change ──────────────────
out="$(HONEST_BOXES="box-good box-blackout" run)"
check "an active blackout still FAILS the leg" \
  "$(echo "$out" | grep -E 'watchdog' | grep -q 'FAIL' && echo ok)"
out="$(HONEST_BOXES="box-good" run)"
check "a clean fleet still reads watchdog PASS" \
  "$(echo "$out" | grep -E 'watchdog signals' | grep -q 'PASS' && echo ok)"

echo "---"
if [ "$fails" = 0 ]; then echo "ALL PASS"; exit 0; else echo "FAILED"; exit 1; fi
