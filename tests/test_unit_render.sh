#!/usr/bin/env bash
# Behavior test for scripts/lib/unit_render.sh (#151 item 1, KN4YDL 2026-10-10).
#
# Shipped system units carried the developer's login (SUDO_USER=, User=,
# /home/<login>/), so on any other host the daemon read config from a home
# that does not exist there while the TUI used the real one — silently. Units
# now carry __MESHANCHOR_USER__/__MESHANCHOR_HOME__ and every installer
# renders through this helper. Properties: the operator comes from an
# explicit override, else the INSTALLED unit's pin when that user EXISTS here
# (a deploy keeps the box's user), else SUDO_USER/USER/uid; root and unknown
# users are refused; a placeholder can never reach /etc.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
LIB="$HERE/../scripts/lib/unit_render.sh"
fails=0
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
ME="$(id -un)"; MYHOME="$(getent passwd "$ME" | cut -d: -f6)"
GHOST="nosuchuser_mf151"

check() { # name, condition-exit-code
  if [ "$2" -eq 0 ]; then echo "ok   $1"; else echo "FAIL $1"; fails=$((fails+1)); fi
}

# shellcheck source=/dev/null
. "$LIB"

SRC="$TMP/demo.service"
printf '[Service]\nEnvironment=SUDO_USER=__MESHANCHOR_USER__\nExecStartPre=+/bin/mkdir -p __MESHANCHOR_HOME__/.config/meshanchor\n' > "$SRC"
mkdir -p "$TMP/etc"

# 1. render substitutes both placeholders with the real user + getent home
ma_render_unit "$SRC" "$TMP/etc/demo.service" "$ME" >/dev/null 2>&1
check "render exit 0" $?
grep -q "^Environment=SUDO_USER=$ME\$" "$TMP/etc/demo.service"; check "user substituted" $?
grep -qF "$MYHOME/.config/meshanchor" "$TMP/etc/demo.service"; check "home substituted (getent)" $?
! grep -q "__MESHANCHOR_" "$TMP/etc/demo.service"; check "no placeholder left" $?

# 1b. Group= comes from the user's PRIMARY group, not assumed == login (review F3)
printf '[Service]\nUser=__MESHANCHOR_USER__\nGroup=__MESHANCHOR_GROUP__\n' > "$TMP/grp.service"
ma_render_unit "$TMP/grp.service" "$TMP/etc/grp.service" "$ME" >/dev/null 2>&1
grep -q "^Group=$(id -gn "$ME")\$" "$TMP/etc/grp.service"; check "group = primary group" $?

# 2. refusals: root, unknown user, empty — and the destination is untouched
echo SENTINEL > "$TMP/etc/keep.service"
for bad in root "$GHOST" "" "a/b"; do
  ma_render_unit "$SRC" "$TMP/etc/keep.service" "$bad" >/dev/null 2>&1
  [ $? -ne 0 ]; check "refuses user '$bad'" $?
done
[ "$(cat "$TMP/etc/keep.service")" = SENTINEL ]; check "refused render leaves dst untouched" $?

# 3. a source with an unknown placeholder is refused (never reaches /etc)
printf 'X=__MESHANCHOR_TYPO__\n' > "$TMP/typo.service"
ma_render_unit "$TMP/typo.service" "$TMP/etc/typo.service" "$ME" >/dev/null 2>&1
[ $? -ne 0 ] && [ ! -e "$TMP/etc/typo.service" ]; check "leftover placeholder refused" $?

# 4. resolution order
printf '[Service]\nEnvironment=SUDO_USER=%s\n' "$ME" > "$TMP/etc/pinned.service"
[ "$(SUDO_USER='' MESHANCHOR_OPERATOR='' ma_resolve_unit_user "$TMP/etc/pinned.service")" = "$ME" ]
check "installed pin of an EXISTING user is kept" $?
printf '[Service]\nEnvironment=SUDO_USER=%s\n' "$GHOST" > "$TMP/etc/ghost.service"
[ "$(SUDO_USER="$ME" MESHANCHOR_OPERATOR='' ma_resolve_unit_user "$TMP/etc/ghost.service")" = "$ME" ]
check "installed pin of a MISSING user is ignored (the #151 box)" $?
[ "$(SUDO_USER=root USER=root MESHANCHOR_OPERATOR="$ME" ma_resolve_unit_user "$TMP/etc/ghost.service")" = "$ME" ]
check "explicit MESHANCHOR_OPERATOR wins" $?
out="$(SUDO_USER='' USER=root MESHANCHOR_OPERATOR='' ma_resolve_unit_user /nonexistent 2>/dev/null)"; rc=$?
if [ "$(id -u)" -ne 0 ]; then
  [ "$rc" -eq 0 ] && [ "$out" = "$ME" ]; check "falls back to the real uid" $?
fi

# 5. every shipped unit that names an operator uses the placeholder (guard)
REPO="$HERE/.."
bad="$(grep -nE '^(User|Group)=|SUDO_USER=|/home/[a-z_]' "$REPO"/scripts/*.service "$REPO"/templates/systemd/*.service 2>/dev/null \
  | grep -vE '=root$|__MESHANCHOR_(USER|HOME|GROUP)__|:[0-9]+:#')"
[ -z "$bad" ]; check "no literal login in shipped units${bad:+: $bad}" $?

# 6. no doc installs a placeholder unit verbatim (review F1: INSTALL_FLEET.md
#    told users to `install` the fleet units -> a literal placeholder in /etc)
ph_units="$(grep -l "__MESHANCHOR_" "$REPO"/scripts/*.service | xargs -n1 basename | tr '\n' '|' | sed 's/|$//')"
docbad="$(grep -rnE "(install|cp) .*($ph_units)" "$REPO" --include='*.md' 2>/dev/null | grep -v unit_render)"
docbad2="$(grep -rnE -B2 "^ */opt/meshanchor/scripts/($ph_units) *\\\\?$" "$REPO" --include='*.md' 2>/dev/null | grep -E "(install|cp) " | grep -v unit_render)"
[ -z "$docbad$docbad2" ]; check "no doc installs a placeholder unit verbatim${docbad:+: $docbad}${docbad2:+: $docbad2}" $?

echo "fails=$fails"
[ "$fails" -eq 0 ] || exit 1
echo "ALL PASS"
