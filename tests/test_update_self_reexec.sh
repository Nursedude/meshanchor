#!/usr/bin/env bash
# Behavior test for update.sh's self-re-exec + unit refresh (#151 re-test,
# KN4YDL 2026-10-19).
#
# update.sh `git pull`s ITSELF. Bash keeps reading the file it opened and git
# replaces the file, so every step after the pull ran the PRE-update script:
# the old step 4 cp'd the new placeholder unit verbatim, and the daemon came
# up looking for /home/__MESHANCHOR_USER__/. And meshanchor-map.service was
# never refreshed by update.sh at all, so a box kept the developer's login.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$HERE/.."
fails=0
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
ME="$(id -un)"
GHOST="nosuchuser_ma151"

check() { # name, condition-exit-code
  if [ "$2" -eq 0 ]; then echo "ok   $1"; else echo "FAIL $1"; fails=$((fails+1)); fi
}

# ── 1. the real mechanism: a script that git-replaces itself ─────────────────
# v1 pulls v2 over itself, then prints its step-4 line. Without the re-exec
# the OLD text runs ("step4 v1"); with it, the NEW one does.
R="$TMP/repo"; mkdir -p "$R/scripts/lib"
cp "$REPO/scripts/lib/update_reexec.sh" "$R/scripts/lib/" 2>/dev/null
git -C "$R" init -q && git -C "$R" config user.email t@t && git -C "$R" config user.name t
mk_update() { # version
  cat > "$R/scripts/update.sh" <<EOF
#!/usr/bin/env bash
set -e
cd "\$(dirname "\$0")/.."
. scripts/lib/update_reexec.sh
FROM=\$(git rev-parse --short HEAD)
[ -n "\${MESHANCHOR_UPDATE_FROM:-}" ] && FROM="\$MESHANCHOR_UPDATE_FROM"
git checkout -q main 2>/dev/null || true
ma_reexec_if_self_changed "\$FROM" "\$(git rev-parse --short HEAD)" "\$PWD" scripts/update.sh "\$@"
echo "step4 $1 from=\$FROM args=\$*"
EOF
}
git -C "$R" checkout -q -b main
mk_update v1; git -C "$R" add -A; git -C "$R" commit -qm v1; A="$(git -C "$R" rev-parse --short HEAD)"
mk_update v2; git -C "$R" commit -qam v2
git -C "$R" checkout -q -b old "$A"
out="$(bash "$R/scripts/update.sh" --skip-deps 2>&1)"
echo "$out" | grep -q "^step4 v2 " ; check "new script's later steps run after a self-update" $?
echo "$out" | grep -q "^step4 v1 " ; [ $? -ne 0 ]; check "old script's later steps do NOT run" $?
echo "$out" | grep -q "from=$A " ; check "change range still counted from the ORIGINAL commit" $?
echo "$out" | grep -q "args=--skip-deps$" ; check "arguments survive the re-exec" $?

# 1-control: the same self-update WITHOUT the re-exec runs the stale text.
# If this ever stops reproducing, test 1 proves nothing — say so, don't pass.
git -C "$R" checkout -q old
sed -i '/ma_reexec_if_self_changed/d' "$R/scripts/update.sh"
out="$(bash "$R/scripts/update.sh" 2>&1)"
echo "$out" | grep -q "^step4 v1 "; check "control: without the re-exec the OLD text runs (the bug)" $?
git -C "$R" checkout -q -- scripts/update.sh; git -C "$R" checkout -q main

# 1b. unchanged script → no re-exec (one run, and it is this one)
out="$(bash "$R/scripts/update.sh" 2>&1)"
[ "$(echo "$out" | grep -c '^step4')" = 1 ]; check "no re-exec when update.sh did not change" $?

# 1c. loop guard: already re-executed → never again
. "$REPO/scripts/lib/update_reexec.sh" 2>/dev/null
out="$( MESHANCHOR_UPDATE_REEXEC=1 ma_reexec_if_self_changed "$A" main "$R" scripts/update.sh; echo STAYED )"
[ "$out" = STAYED ]; check "re-exec happens at most once" $?

# ── 2. unit refresh never installs a new unit ─────────────────────────────────
S="$TMP/src"; E="$TMP/etc"; mkdir -p "$S" "$E"
printf '[Service]\nEnvironment=SUDO_USER=__MESHANCHOR_USER__\nExecStart=new --port 5002\n' > "$S/meshanchor-map.service"
printf '[Service]\nEnvironment=SUDO_USER=__MESHANCHOR_USER__\n' > "$S/meshanchor-daemon.service"
# shellcheck source=/dev/null
. "$REPO/scripts/lib/unit_render.sh"
SUDO_USER="$ME" MESHANCHOR_OPERATOR='' ma_refresh_installed_units "$S" "$E" meshanchor-daemon.service >/dev/null 2>&1
[ ! -e "$E/meshanchor-daemon.service" ]; check "a unit not installed is not installed by a refresh" $?

# ── 2a. map is REPAIR-only: a working unit keeps its port ────────────────────
# meshanchor-server's map runs on :5000 while the shipped unit is on the
# coexist :5002 — a blanket refresh would move a live map. Only the broken
# shapes (#151) are re-rendered.
repair() { SUDO_USER="$ME" MESHANCHOR_OPERATOR='' ma_repair_installed_units "$S" "$E" meshanchor-map.service >/dev/null 2>&1; }
printf '[Service]\nEnvironment=SUDO_USER=%s\n' "$GHOST" > "$E/meshanchor-map.service"
repair; check "repair reports an update for a ghost-login unit" $?
grep -q "^Environment=SUDO_USER=$ME\$" "$E/meshanchor-map.service"; check "ghost-login map unit rendered for this host's operator (the #151 box)" $?
printf '[Service]\nEnvironment=SUDO_USER=__MESHANCHOR_USER__\n' > "$E/meshanchor-map.service"
repair; grep -q "^Environment=SUDO_USER=$ME\$" "$E/meshanchor-map.service"; check "placeholder map unit repaired" $?
printf '[Service]\nEnvironment=SUDO_USER=%s\nExecStart=old --port 5000\n' "$ME" > "$E/meshanchor-map.service"
repair; [ $? -ne 0 ]; check "a healthy map unit reports no update" $?
grep -q "port 5000" "$E/meshanchor-map.service"; check "a healthy map unit is left alone (port kept)" $?
printf '[Service]\nExecStart=old --port 5000\n' > "$E/meshanchor-map.service"
repair; grep -q "port 5000" "$E/meshanchor-map.service"; check "a map unit with no SUDO_USER pin is left alone" $?

# 2b. update.sh itself refreshes the map unit and re-execs after the pull
grep -qE 'ma_repair_installed_units .*meshanchor-map\.service' "$REPO/scripts/update.sh"; check "update.sh repairs meshanchor-map.service" $?
grep -qE '^_MA_REFRESH_UNITS=.*meshanchor-map' "$REPO/scripts/update.sh"; [ $? -ne 0 ]; check "update.sh does NOT blanket-refresh the map unit" $?
pull_ln="$(grep -n 'git pull origin' "$REPO/scripts/update.sh" | head -1 | cut -d: -f1)"
reexec_ln="$(grep -n 'ma_reexec_if_self_changed ' "$REPO/scripts/update.sh" | head -1 | cut -d: -f1)"
step2_ln="$(grep -n '\[2/5\]' "$REPO/scripts/update.sh" | head -1 | cut -d: -f1)"
[ -n "$reexec_ln" ] && [ "$reexec_ln" -gt "$pull_ln" ] && [ "$reexec_ln" -lt "$step2_ln" ]
check "update.sh re-execs after the pull, before step 2" $?

echo
[ "$fails" -eq 0 ] && echo "ALL PASS" || echo "$fails FAILED"
exit "$fails"
