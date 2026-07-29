#!/usr/bin/env bash
# Behavior test for scripts/lib/fleet_hosts.sh — MA's ONE fleet_hosts
# resolver (ported from the MF twin's convergence, 2026-07-29). Both shell
# consumers (lab_traffic_rollup.sh, honest_status.sh) source it; this pins
# the resolution semantics they now share.
#
# Every case here resolves BEFORE the /etc tier (not injectable without
# root), so no verdict depends on the machine running the suite
# (feedback_tests_must_pin_ambient_state).
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
LIB="$HERE/../scripts/lib/fleet_hosts.sh"
fails=0

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT

check() { if [ -n "$2" ]; then echo "PASS: $1"; else echo "FAIL: $1"; fails=1; fi; }

# Drive the real lib in a clean shell. $1..$n = KEY=VALUE env entries.
# Prints "<file>|<hosts space-joined>" on resolve, "NORESOLVE" on rc 1.
run_lib() {
  env -i PATH="$PATH" "$@" bash -c '
    set -u
    . "'"$LIB"'"
    if fleet_hosts_resolve; then
      printf "%s|%s\n" "$FLEET_HOSTS_FILE" "$(printf "%s\n" "$FLEET_HOSTS_LIST" | tr "\n" " " | sed "s/ *$//")"
    else
      echo NORESOLVE
    fi'
}

# ── env override: authoritative, parsed, aliased ─────────────────────────
printf 'moc  # primary\nmoc1 moc2\n# retired: moc9\n' > "$TMP/ov"
out="$(run_lib MESHANCHOR_FLEET_HOSTS="$TMP/ov")"
check "override wins and comments/whitespace parse ('moc moc1 moc2')" \
  "$([ "$out" = "$TMP/ov|moc moc1 moc2" ] && echo ok)"

out="$(run_lib FLEET_HOSTS="$TMP/ov")"
check "legacy \$FLEET_HOSTS alias honored (lab_traffic_rollup's documented one)" \
  "$([ "$out" = "$TMP/ov|moc moc1 moc2" ] && echo ok)"

printf 'primary-host\n' > "$TMP/ov2"
out="$(run_lib MESHANCHOR_FLEET_HOSTS="$TMP/ov2" FLEET_HOSTS="$TMP/ov")"
check "MESHANCHOR_FLEET_HOSTS outranks the legacy alias" \
  "$(echo "$out" | grep -q 'primary-host' && echo ok)"

# ── a SET but missing override is authoritative — no silent fall-through ─
FAKE_HOME="$TMP/home"; mkdir -p "$FAKE_HOME/.config/meshanchor"
printf 'real-config-host\n' > "$FAKE_HOME/.config/meshanchor/fleet_hosts"
out="$(run_lib MESHANCHOR_FLEET_HOSTS="$TMP/nope" HOME="$FAKE_HOME")"
check "set-but-missing override => NORESOLVE, never the box's real config" \
  "$([ "$out" = "NORESOLVE" ] && echo ok)"

# ── the home tier ────────────────────────────────────────────────────────
out="$(run_lib HOME="$FAKE_HOME")"
check "home tier resolves (~/.config/meshanchor/fleet_hosts)" \
  "$(echo "$out" | grep -q 'real-config-host' && echo ok)"

printf 'kept  # trailing comment\n' > "$FAKE_HOME/.config/meshanchor/fleet_hosts"
out="$(run_lib HOME="$FAKE_HOME")"
check "trailing comment yields the host, not a garbage token" \
  "$(echo "$out" | grep -q '|kept$' && echo ok)"

echo "---"
if [ "$fails" = 0 ]; then echo "ALL PASS"; exit 0; else echo "FAILED"; exit 1; fi
