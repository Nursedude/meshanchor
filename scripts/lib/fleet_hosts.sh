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
#   - no python mirror: no MA python consumer reads this namespace (MA's mini
#     rollup deliberately reads the MESHFORGE-namespaced list — see the port
#     commit message)
#
# Usage:
#   . "<repo>/scripts/lib/fleet_hosts.sh"
#   if fleet_hosts_resolve; then
#     echo "$FLEET_HOSTS_FILE"   # the file that won the resolution order
#     echo "$FLEET_HOSTS_LIST"   # hosts, one per line, comments stripped
#   fi                            # rc 1 = no list found anywhere
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
# File format: hosts separated by whitespace/newlines; '#' starts a comment
# anywhere on the line, so "moc1  # retired" parses as host "moc1".
#
# set -u safe. POSIX sh compatible (no arrays, no bashisms).

fleet_hosts_resolve() {
  FLEET_HOSTS_FILE=""
  FLEET_HOSTS_LIST=""
  for _fh_env in "${MESHANCHOR_FLEET_HOSTS:-}" "${FLEET_HOSTS:-}"; do
    if [ -n "$_fh_env" ]; then
      if [ -f "$_fh_env" ] && [ -r "$_fh_env" ]; then
        FLEET_HOSTS_FILE="$_fh_env"
        break
      fi
      return 1   # explicit override that doesn't resolve = no list, loudly
    fi
  done
  if [ -z "$FLEET_HOSTS_FILE" ]; then
    for _fh_f in "${HOME:+$HOME/.config/meshanchor/fleet_hosts}" \
                 /etc/meshanchor/fleet_hosts; do
      [ -n "$_fh_f" ] && [ -f "$_fh_f" ] && [ -r "$_fh_f" ] && { FLEET_HOSTS_FILE="$_fh_f"; break; }
    done
  fi
  [ -n "$FLEET_HOSTS_FILE" ] || return 1
  FLEET_HOSTS_LIST="$(sed 's/#.*//' "$FLEET_HOSTS_FILE" | tr -s ' \t' '\n' | grep -v '^$')"
  return 0
}
