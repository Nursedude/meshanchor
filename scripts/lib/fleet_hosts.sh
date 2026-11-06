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
# operator: "quietly failing is not an answer"). Same reasons, same words as
# src/utils/fleet_hosts.py.
#
# File format: hosts separated by whitespace/newlines; '#' starts a comment
# anywhere on the line, so "moc1  # retired" parses as host "moc1".
#
# set -u safe. POSIX sh compatible (no arrays, no bashisms).

_fh_refuse() {   # PATH WHY
  FLEET_HOSTS_ERROR="fleet host list $1 is unreadable ($2) - refusing to fall through to another list"
  printf 'fleet_hosts: %s\n' "$FLEET_HOSTS_ERROR" >&2
  return 2
}

# _fh_usable PATH — 0 readable regular file; 1 provably absent; 2 present
# but unusable, OR uncheckable. Mirrors utils.fleet_hosts._usable.
_fh_usable() {
  if [ -f "$1" ] && [ -r "$1" ]; then return 0; fi
  if [ ! -e "$1" ] && [ ! -L "$1" ]; then
    # [ -e ] is also false when a parent cannot be searched (EACCES) — that
    # is NOT absence. Walk up to the nearest directory that answers.
    _fh_d="$(dirname -- "$1")"
    while :; do
      if [ -d "$_fh_d" ]; then
        [ -x "$_fh_d" ] && return 1
        _fh_refuse "$1" "cannot be checked: a parent directory is not searchable"
        return 2
      fi
      _fh_d="$(dirname -- "$_fh_d")"
    done
  fi
  if [ -L "$1" ] && [ ! -e "$1" ]; then
    # r7g F1: [ -e ] is false for a dangling link, a loop AND a target under
    # an unsearchable dir — ask the kernel which (C locale = fixed strerror).
    case "$(LC_ALL=C cat -- "$1" 2>&1 >/dev/null)" in
      *"symbolic links"*) _fh_why="symlink loop" ;;
      *"ermission denied"*) _fh_why="symlink target cannot be checked: a parent directory is not searchable" ;;
      *) _fh_why="dangling symlink" ;;
    esac
  elif [ -d "$1" ]; then _fh_why="a directory, not a file"
  elif [ ! -f "$1" ]; then _fh_why="not a regular file"
  else _fh_why="permission denied"; fi
  _fh_refuse "$1" "$_fh_why"
}

fleet_hosts_resolve() {
  FLEET_HOSTS_FILE=""
  FLEET_HOSTS_LIST=""
  FLEET_HOSTS_ERROR=""
  for _fh_env in "${MESHANCHOR_FLEET_HOSTS:-}" "${FLEET_HOSTS:-}"; do
    if [ -n "$_fh_env" ]; then
      _fh_usable "$_fh_env"; _fh_rc=$?
      [ "$_fh_rc" -eq 0 ] || return "$_fh_rc"  # authoritative: no fall-through
      FLEET_HOSTS_FILE="$_fh_env"
      break
    fi
  done
  if [ -z "$FLEET_HOSTS_FILE" ]; then
    for _fh_f in "${HOME:+$HOME/.config/meshanchor/fleet_hosts}" \
                 /etc/meshanchor/fleet_hosts; do
      [ -n "$_fh_f" ] || continue
      _fh_usable "$_fh_f"; _fh_rc=$?
      [ "$_fh_rc" -eq 2 ] && return 2
      [ "$_fh_rc" -eq 0 ] && { FLEET_HOSTS_FILE="$_fh_f"; break; }
    done
  fi
  [ -n "$FLEET_HOSTS_FILE" ] || return 1
  # The read is checked: a failure must not answer rc 0 with an EMPTY list.
  if ! _fh_raw="$(sed 's/#.*//' "$FLEET_HOSTS_FILE")"; then
    _fh_refuse "$FLEET_HOSTS_FILE" "read failed"
    FLEET_HOSTS_FILE=""
    return 2
  fi
  FLEET_HOSTS_LIST="$(printf '%s\n' "$_fh_raw" | tr -s ' \t' '\n' | grep -v '^$')"
  return 0
}
