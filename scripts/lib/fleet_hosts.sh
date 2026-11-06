# fleet_hosts.sh — THE MeshAnchor fleet_hosts resolver. Source it; never copy it.
#
# Ported from the MF twin's convergence 2026-07-29 (MF a9b471d0): MF collapsed
# ~13 independent copies of its resolution chain into one shell lib + one
# python mirror after the copies were shown to disagree about the env
# override, comment parsing, and HOME defaulting (honest_failure_modes #5).
# MeshAnchor's two shell consumers (lab_traffic_rollup.sh, honest_status.sh)
# carried the same copy-drift shape in miniature; this is their one resolver.
#
# DELIBERATE divergences from the MF lib (documented in the MF twin map —
# don't "fix" them without a consumer that needs the change):
#   - meshanchor namespace (~/.config/meshanchor, /etc/meshanchor,
#     $MESHANCHOR_FLEET_HOSTS)
#   - NO per-repo fleet_hosts.<basename> tier: no MA consumer scopes by repo,
#     and an unused tier is speculative surface
#
# Usage:
#   . "<repo>/scripts/lib/fleet_hosts.sh"
#   if fleet_hosts_resolve; then
#     echo "$FLEET_HOSTS_FILE"   # the file that won the resolution order
#     echo "$FLEET_HOSTS_LIST"   # hosts, one per line, comments stripped
#   fi                            # rc 1 = no list found anywhere:
#                                 #   $FLEET_HOSTS_WHY says what was checked
#                                 #   ($FLEET_HOSTS_ADVICE: what to do) -
#                                 #   print both: fleet_hosts_absent_say
#                                 # rc 2 = a list is present but unreadable
#                                 #   or uncheckable: $FLEET_HOSTS_ERROR says
#                                 #   which and why (also said on stderr)
#
# Resolution order:
#   $MESHANCHOR_FLEET_HOSTS  — AUTHORITATIVE when set: a missing/unreadable
#   $FLEET_HOSTS               override FAILS the resolve rather than falling
#                              through to the box's real config (a degraded
#                              state must not read as a valid value;
#                              FLEET_HOSTS is the legacy alias
#                              lab_traffic_rollup documented)
#   ~/.config/meshanchor/fleet_hosts   (skipped when HOME is unset — cron/
#                                       daemon context, never defaulted)
#   /etc/meshanchor/fleet_hosts
#
# A tier that is ABSENT passes to the next one; a PRESENT-but-unusable or
# UNCHECKABLE tier fails with rc 2 (ported 2026-11-05 from MF r7e #2 + r7f;
# operator: "quietly failing is not an answer"). The reasons are
# src/utils/fleet_hosts.py's own words — it decides (see below).
#
# File format: hosts separated by whitespace/newlines; '#' starts a comment
# anywhere on the line, so "moc1  # retired" parses as host "moc1".
#
# ONE implementation (operator 2026-11-05). This lib used to re-derive the
# decision in shell; four review rounds (r7f..r7i) each found a new path
# shape on which it disagreed with the python resolver — an unsearchable
# parent, a symlink loop, ENAMETOOLONG, a trailing slash. It now ASKS
# src/utils/fleet_hosts.py (`--shell`) for the decision, the reason and the
# parsed list. Python's own failure (python3 missing, a traceback) is rc 2 —
# a resolver that could not run is never "no list".
#
# set -u and set -e safe. Needs bash (BASH_SOURCE) and python3; every caller
# is a bash script and every box runs python3.

# Located from THIS file, never from a failed cd (r8 review #4: a failed cd
# made the path "/fleet_hosts.py" — an unrelated file would have decided).
_FH_PY=""
# readlink -f (Fable r10 #7): sourced through a symlink, BASH_SOURCE is the
# LINK — its directory has no src/utils beside it.
_fh_self="$(readlink -f -- "${BASH_SOURCE[0]}" 2>/dev/null)" || _fh_self="${BASH_SOURCE[0]}"
if _fh_d="$(CDPATH= cd -- "$(dirname -- "$_fh_self")/../../src/utils" 2>/dev/null && pwd)" \
    && [ -f "$_fh_d/fleet_hosts.py" ]; then
  _FH_PY="$_fh_d/fleet_hosts.py"
fi

fleet_hosts_resolve() {
  FLEET_HOSTS_FILE=""
  FLEET_HOSTS_LIST=""
  FLEET_HOSTS_ERROR=""
  FLEET_HOSTS_WHY=""
  FLEET_HOSTS_ADVICE=""
  if [ -z "$_FH_PY" ]; then
    FLEET_HOSTS_ERROR="fleet host list could not be resolved (src/utils/fleet_hosts.py not found beside this lib) - refusing to guess"
    printf 'fleet_hosts: %s\n' "$FLEET_HOSTS_ERROR" >&2
    return 2
  fi
  # timeout (r8 review #3): every deploy and gate script calls this; a hung
  # interpreter must end as a refusal (rc 124 -> the * arm), not a hang.
  # -I: no script dir, cwd, user site or PYTHON* env on sys.path. HOME and
  # the overrides are passed EXPLICITLY — they may be unexported shell vars,
  # and the shell contract is $HOME (unset = skip the user tier), not the
  # sudo-real home.
  _fh_rc=0
  _fh_out="$(env HOME="${HOME:-}" MESHANCHOR_FLEET_HOSTS="${MESHANCHOR_FLEET_HOSTS:-}" \
      FLEET_HOSTS="${FLEET_HOSTS:-}" timeout "${FLEET_HOSTS_RESOLVE_TIMEOUT:-30}" python3 -I "$_FH_PY" --shell)" || _fh_rc=$?
  case "$_fh_rc" in
    0)
      # First line must be the format marker (r17): a wrapper that chatters
      # on stdout can never turn its stray line into FLEET_HOSTS_FILE.
      case "$_fh_out" in
        "fleet_hosts/1
"*)
          _fh_out="${_fh_out#*
}"
          FLEET_HOSTS_FILE="${_fh_out%%
*}"
          case "$_fh_out" in *"
"*) FLEET_HOSTS_LIST="${_fh_out#*
}" ;; esac
          return 0 ;;
      esac
      # rc 0 with NO file line: the resolver did not run (a shadowed
      # `timeout`/`python3`). An empty list here would read "lists nobody".
      FLEET_HOSTS_ERROR="fleet host list could not be resolved (python resolver answered rc 0 with no file) - refusing to guess" ;;   # also: no format marker
    10) # line 1 = why (one line, for a status line); the rest = advice
      FLEET_HOSTS_WHY="${_fh_out%%
*}"
      case "$_fh_out" in *"
"*) FLEET_HOSTS_ADVICE="${_fh_out#*
}" ;; esac
      return 1 ;;
    11) FLEET_HOSTS_ERROR="$_fh_out" ;;
    *) FLEET_HOSTS_ERROR="fleet host list could not be resolved (python resolver $_FH_PY exited $_fh_rc) - refusing to guess" ;;
  esac
  printf 'fleet_hosts: %s\n' "$FLEET_HOSTS_ERROR" >&2
  return 2
}

# fleet_hosts_absent_say <self> - THE message for rc 1 (no list resolved).
# Call it right after fleet_hosts_resolve returned 1, then exit as you like.
# Both lines come from the python resolver (explain_absent): the reason it
# found no list, and advice that matches the SAME inputs - a set override is
# authoritative (never "create ~/.config/..."), HOME unset points at /etc.
# Shape port of the MeshForge twin (MF 5d73d48b, 2026-11-06).
fleet_hosts_absent_say() {
  printf '%s: %s\n' "${1:-fleet}" "${FLEET_HOSTS_WHY:-no fleet host list (the resolver gave no reason)}" >&2
  [ -n "${FLEET_HOSTS_ADVICE:-}" ] && printf '%s\n' "$FLEET_HOSTS_ADVICE" >&2
  return 0
}
