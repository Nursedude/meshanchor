#!/usr/bin/env bash
# honest_status.sh — operator-owned verification gate (MeshAnchor).
#
# Ported from MeshForge 2026-06-23. Re-checks dev + fleet state from EXTERNAL
# ground truth so you never have to trust an AI summary: GitHub CI, git SHAs
# over ssh, the live HTTP API, real test/lint exit codes. The AI asserts green;
# THIS re-derives it from systems the AI (and the local harness) can't fabricate.
#
# Two tiers, kept distinct so `exit 0` stays meaningful:
#   VERIFICATION — CI, fleet SHA, full suite, lint, live-honesty, watchdog
#     WEDGE. A FAIL here means the code/deploy I claimed is not green.
#   FLEET WARNINGS — watchdog DEGRADED signals. Real conditions, surfaced
#     LOUD, but not necessarily this code's fault; they do not by themselves
#     fail the verification verdict.
#
# Cardinal rule (honest_failure_modes #2): UNKNOWN (box unreachable, gh not
# authenticated, endpoint absent, NO FLEET CONFIGURED) is NEVER counted as
# PASS — unobservable is not healthy. A WARN is never hidden.
#
#   exit 0 = all verification PASSED, nothing UNKNOWN (warnings surfaced)
#   exit 1 = a verification check FAILED (incl. a watchdog WEDGE) — not green
#   exit 2 = no failures but something couldn't be verified — NOT green
#   --strict promotes WARNINGS to failures (exit 1)
#
# MeshAnchor note: MA's fleet presence is config-driven. The fleet legs (SHA
# drift, live conf_rate, watchdog) read the host list from $HONEST_BOXES, else
# the MA fleet_hosts file (~/.config/meshanchor/fleet_hosts or
# /etc/meshanchor/fleet_hosts). With NO list configured those legs report
# UNKNOWN (not PASS) — the honest signal that MA's fleet view isn't wired here
# yet. The verdict marker is the one MA's scripts/claim_gate.py already reads.
#
# Usage:
#   bash scripts/honest_status.sh             # full (runs the local suite, ~2 min)
#   bash scripts/honest_status.sh --quick     # skip the local suite (UNKNOWN for it)
#   bash scripts/honest_status.sh --strict    # fleet warnings also fail the gate
#   HONEST_BOXES="moc moc1" bash scripts/honest_status.sh   # override fleet list
set -u

REPO="${MESHANCHOR_REPO:-/opt/meshanchor}"
GH_REPO="${MESHANCHOR_GH_REPO:-Nursedude/meshanchor}"

# Fleet host list: explicit env wins; else the MA fleet_hosts file; else empty
# (fleet legs report UNKNOWN — never a false PASS on an unconfigured fleet).
BOXES="${HONEST_BOXES:-}"
if [ -z "$BOXES" ]; then
  for f in "$HOME/.config/meshanchor/fleet_hosts" /etc/meshanchor/fleet_hosts; do
    if [ -f "$f" ]; then
      BOXES=$(grep -vE '^\s*#|^\s*$' "$f" 2>/dev/null | awk '{print $1}' | tr '\n' ' ')
      break
    fi
  done
fi

# Watchdog signals — MeshAnchor emits silence as BLACKOUT rows in
# fleet_history.db, surfaced live at /fleet/blackouts. There is NO watchdog.json
# writer (that path was a MeshForge port artifact — reader with no writer,
# honest_failure #4). The endpoint path + the daemon that populates it are
# overridable ONLY so the gate's own logic can be exercised against fixtures;
# production is the default.
BLACKOUTS_PATH="${HONEST_BLACKOUTS_PATH:-/fleet/blackouts}"
WD_UNIT="${HONEST_WD_UNIT:-meshanchor-fleet-watchdog}"
RUN_TESTS=1
STRICT=0
for arg in "$@"; do
  case "$arg" in
    --quick)  RUN_TESTS=0 ;;
    --strict) STRICT=1 ;;
  esac
done

SSH="ssh -o ConnectTimeout=8 -o BatchMode=yes"
pass=0; fail=0; unknown=0; warns=0
ok()    { printf '  %-22s \033[32mPASS\033[0m    %s\n' "$1" "$2"; pass=$((pass+1)); }
bad()   { printf '  %-22s \033[31mFAIL\033[0m    %s\n' "$1" "$2"; fail=$((fail+1)); }
unk()   { printf '  %-22s \033[33mUNKNOWN\033[0m %s\n' "$1" "$2"; unknown=$((unknown+1)); }
warnf() { printf '  %-22s \033[33mWARN\033[0m    %s\n' "$1" "$2"; warns=$((warns+1)); }

HEAD=$(git -C "$REPO" rev-parse --short HEAD 2>/dev/null || echo "?")
HEADFULL=$(git -C "$REPO" rev-parse HEAD 2>/dev/null || echo "?")
echo "honest_status — $REPO @ $HEAD  ($(date -u +%Y-%m-%dT%H:%M:%SZ))"
echo

# 1. CI conclusion for the EXACT current HEAD (external, harness-immune).
if command -v gh >/dev/null 2>&1; then
  if gh run list -R "$GH_REPO" --branch main --limit 30 \
       --json headSha,conclusion,status,databaseId >/tmp/.hs_ci_ma.json 2>/dev/null; then
    read -r ST CC RID < <(HEADFULL="$HEADFULL" python3 - <<'PY' 2>/dev/null
import json, os, sys
sha = os.environ["HEADFULL"]
try:
    runs = json.load(open("/tmp/.hs_ci_ma.json"))
except Exception:
    sys.exit()
for r in runs:
    if r.get("headSha") == sha:
        print(r.get("status",""), r.get("conclusion",""), r.get("databaseId",""))
        break
PY
)
    if [ -z "${ST:-}" ]; then unk "CI($HEAD)" "no CI run found for this SHA (pushed yet?)"
    elif [ "$ST" != "completed" ]; then unk "CI($HEAD)" "run $RID still $ST"
    elif [ "$CC" = "success" ]; then ok "CI($HEAD)" "run $RID success"
    else bad "CI($HEAD)" "run $RID conclusion=$CC"; fi
  else
    unk "CI($HEAD)" "gh present but 'gh run list' failed (auth?)"
  fi
else
  unk "CI($HEAD)" "gh not installed — cannot verify CI externally"
fi

# 2. Fleet SHA drift — each box's HEAD vs this repo's HEAD (external).
if [ -z "$BOXES" ]; then
  unk "fleet SHA drift" "no fleet configured (set HONEST_BOXES or ~/.config/meshanchor/fleet_hosts)"
else
  matched=0; reached=0; total=0; desc=""
  for b in $BOXES; do
    total=$((total+1))
    s=$($SSH "$b" "git -C $REPO rev-parse HEAD" 2>/dev/null)
    if [ -z "$s" ]; then desc="$desc $b:unreach"; continue; fi
    reached=$((reached+1))
    if [ "$s" = "$HEADFULL" ]; then matched=$((matched+1)); else desc="$desc $b:${s:0:7}"; fi
  done
  drifted=$((reached - matched))
  if [ "$drifted" -gt 0 ]; then bad "fleet SHA drift" "$matched/$total @ $HEAD;$desc"
  elif [ "$reached" -lt "$total" ]; then unk "fleet SHA drift" "$matched/$reached reachable @ $HEAD;$desc"
  else ok "fleet SHA drift" "$matched/$total @ $HEAD"; fi
fi

# 3. Full local suite — real exit code + count (file-routed, never a streamed tail).
if [ "$RUN_TESTS" = 1 ]; then
  python3 -m pytest "$REPO/tests/" -q -p no:cacheprovider >/tmp/.hs_pytest_ma 2>&1; rc=$?
  summ=$(grep -E "[0-9]+ (passed|failed|error)" /tmp/.hs_pytest_ma | tail -1)
  nfail=$(grep -cE "^FAILED|^ERROR" /tmp/.hs_pytest_ma)
  if [ "$rc" = 0 ] && [ "$nfail" = 0 ]; then ok "full suite" "$summ (exit 0)"
  else
    # Surface the failing test id(s) (already in the captured file, just not
    # shown before) AND preserve the log to a durable path — NOT /tmp (RTC-less
    # Pis clear it on reboot), overwrite-on-failure-ONLY so a later green run
    # can't clobber it. Every honest_status run samples the suite under
    # concurrent load (CI + ssh probes) — the exact trigger for the rare timing
    # flake (GH #144) — so this is how its traceback finally gets captured.
    names=$(grep -E "^FAILED|^ERROR" /tmp/.hs_pytest_ma | sed -E 's/^(FAILED|ERROR) //; s/ -.*//' | head -3 | paste -sd' ' -)
    fdir="${XDG_STATE_HOME:-$HOME/.local/state}/meshanchor/hs_failures"; saved=""
    mkdir -p "$fdir" 2>/dev/null && cp /tmp/.hs_pytest_ma "$fdir/last_failure.log" 2>/dev/null \
      && saved=" — saved $fdir/last_failure.log"
    bad "full suite" "exit $rc, $nfail FAILED/ERROR${names:+ ($names)}$saved — $summ"
  fi
else
  unk "full suite" "skipped (--quick) — not verified"
fi

# 4. Lint — real exit code.
python3 "$REPO/scripts/lint.py" --all >/tmp/.hs_lint_ma 2>&1; rc=$?
if [ "$rc" = 0 ]; then ok "lint" "exit 0"
else bad "lint" "exit $rc — $(grep -E '\[E\]' /tmp/.hs_lint_ma | tail -1)"; fi

# 5. Live honesty assert — no displayed confirmation_rate may exceed 1.0
#    (the #74 false-green: a rate reading >1.0 = ">100% confirmed").
if [ -z "$BOXES" ]; then
  unk "live conf_rate<=1.0" "no fleet configured — cannot poll /api/gateway/delivery"
else
  viol=""; checked=0; det=""
  for b in $BOXES; do
    j=$($SSH "$b" "curl -s --max-time 8 http://localhost:5000/api/gateway/delivery 2>/dev/null" 2>/dev/null)
    [ -z "$j" ] && continue
    checked=$((checked+1))
    v=$(printf '%s' "$j" | python3 -c 'import sys,json
try: d=json.load(sys.stdin)
except Exception: print("PARSE"); sys.exit()
r=d.get("confirmation_rate")
if r is None: print("none")
elif isinstance(r,(int,float)) and not isinstance(r,bool) and r>1.0: print("VIOL=%.3f"%r)
else: print("%.3f"%r if isinstance(r,(int,float)) else "shape?")' 2>/dev/null)
    det="$det $b:$v"
    case "$v" in VIOL*) viol="$viol$b:$v ";; esac
  done
  if [ -n "$viol" ]; then bad "live conf_rate<=1.0" "$viol"
  elif [ "$checked" = 0 ]; then unk "live conf_rate<=1.0" "no box served /api/gateway/delivery"
  else ok "live conf_rate<=1.0" "$checked checked;$det"; fi
fi

# 6. Watchdog — MeshAnchor emits silence as BLACKOUT rows (fleet_history.db,
#    surfaced live at /fleet/blackouts), NOT a watchdog.json file. An ACTIVE
#    blackout (http_dead / daemon_dead / frozen / no_data) is real silence =
#    FAIL. Guard the false-green: a DEAD watchdog daemon can't record blackouts,
#    so empty-active from a dead watchdog is UNKNOWN, never clean (absence-of-
#    signal ≠ healthy, honest_failure #2). Unreachable/unparseable = UNKNOWN.
if [ -z "$BOXES" ]; then
  unk "watchdog signals" "no fleet configured — cannot poll $BLACKOUTS_PATH across the fleet"
else
  active_t=0; clean=0; unverif=0; sigdesc=""
  btotal=$(echo $BOXES | wc -w)
  for b in $BOXES; do
    # ONE round-trip: is the watchdog daemon alive, and the live blackout state.
    raw=$($SSH "$b" "systemctl is-active $WD_UNIT 2>/dev/null; echo '---BOSEP---'; curl -s --max-time 8 http://localhost:5000$BLACKOUTS_PATH 2>/dev/null" 2>/dev/null)
    wdstate=$(printf '%s\n' "$raw" | sed -n '1p')
    body=$(printf '%s\n' "$raw" | awk 'f{print} /^---BOSEP---$/{f=1}')
    if [ "$wdstate" != "active" ]; then
      unverif=$((unverif+1)); sigdesc="$sigdesc $b:watchdog-${wdstate:-unknown}"; continue
    fi
    if [ -z "$body" ]; then unverif=$((unverif+1)); sigdesc="$sigdesc $b:unreach"; continue; fi
    p=$(printf '%s' "$body" | python3 -c 'import sys,json
try: d=json.load(sys.stdin)
except Exception: print("PARSE"); sys.exit()
a=d.get("active")
if not isinstance(a,list): print("SHAPE"); sys.exit()
print("%d %s"%(len(a), ",".join(str(x.get("kind","?")) for x in a)))' 2>/dev/null)
    if [ -z "$p" ] || [ "$p" = "PARSE" ] || [ "$p" = "SHAPE" ]; then
      unverif=$((unverif+1)); sigdesc="$sigdesc $b:unparseable"; continue
    fi
    n=$(printf '%s' "$p" | awk "{print \$1}"); kinds=$(printf '%s' "$p" | cut -d' ' -f2-)
    if [ "${n:-0}" = 0 ]; then clean=$((clean+1))
    else active_t=$((active_t+n)); sigdesc="$sigdesc $b:[$kinds]"; fi
  done
  if [ "$active_t" -gt 0 ]; then bad "watchdog (blackout)" "$active_t active blackout(s) across fleet:$sigdesc"
  elif [ "$unverif" -gt 0 ]; then unk "watchdog signals" "$clean/$btotal clean, $unverif unverifiable (dead watchdog / unreachable):$sigdesc"
  else ok "watchdog signals" "$clean/$btotal clean, 0 active blackouts"; fi
fi

echo
total_checks=$((pass+fail+unknown+warns))
SUM="$pass/$total_checks PASS"
[ "$warns"   -gt 0 ] && SUM="$SUM, $warns WARN"
[ "$unknown" -gt 0 ] && SUM="$SUM, $unknown UNKNOWN"
[ "$fail"    -gt 0 ] && SUM="$SUM, $fail FAIL"

if [ "$fail" -gt 0 ]; then
  verdict_rc=1; verdict_msg="$SUM  (proven not-green)"
elif [ "$STRICT" = 1 ] && [ "$warns" -gt 0 ]; then
  verdict_rc=1; verdict_msg="$SUM  (--strict: warnings treated as failures — not clean)"
elif [ "$unknown" -gt 0 ]; then
  verdict_rc=2; verdict_msg="$SUM  (could not fully verify — NOT green)"
elif [ "$warns" -gt 0 ]; then
  verdict_rc=0; verdict_msg="$SUM  (code+deploy verified; $warns fleet warning(s) surfaced above — read them)"
else
  verdict_rc=0; verdict_msg="$SUM  (fully verified green)"
fi
echo "--> $verdict_msg"

# Durable verdict marker — the unfabricatable record scripts/claim_gate.py reads
# (~/.cache/meshanchor/honest_verdict.json). Best-effort: a marker-write failure
# must NEVER change the verdict the operator just saw, but it leaves a stderr
# witness (honest_failure_modes #9). A missing/old marker reads as "this HEAD is
# unverified" downstream — the safe direction.
VERDICT_PATH="${HONEST_VERDICT_PATH:-${HOME:-/tmp}/.cache/meshanchor/honest_verdict.json}"
if ! HV_RC="$verdict_rc" HV_MSG="$verdict_msg" HV_HEAD="$HEADFULL" \
     HV_FULL="$RUN_TESTS" HV_STRICT="$STRICT" HV_PATH="$VERDICT_PATH" \
     python3 - <<'PY' 2>/dev/null
import json, os, tempfile, time
p = os.environ["HV_PATH"]
d = os.path.dirname(os.path.abspath(p)) or "."
os.makedirs(d, exist_ok=True)
payload = json.dumps({
    "head_full": os.environ.get("HV_HEAD", ""),
    "exit_code": int(os.environ.get("HV_RC", "2") or 2),
    "ts": time.time(),
    "iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    "summary": os.environ.get("HV_MSG", ""),
    "ran_full_suite": os.environ.get("HV_FULL") == "1",
    "strict": os.environ.get("HV_STRICT") == "1",
}, indent=2)
fd, tmp = tempfile.mkstemp(dir=d, prefix=os.path.basename(p) + ".", suffix=".tmp")
try:
    with os.fdopen(fd, "w") as f:
        f.write(payload); f.flush(); os.fsync(f.fileno())
    os.replace(tmp, p)
except BaseException:
    try:
        os.unlink(tmp)
    except OSError:
        pass
    raise
PY
then
  echo "honest_status: WARN — could not write verdict marker $VERDICT_PATH" \
       "(claim-gate will treat this HEAD as unverified)" >&2
fi

exit "$verdict_rc"
