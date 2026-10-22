#!/usr/bin/env bash
# Render a shipped systemd unit for THIS host's operator (#151 item 1).
#
# Shipped units carried the developer's login (SUDO_USER=, User=, /home/<login>)
# and were cp'd verbatim, so on any other host the daemon read config from a
# home that does not exist there while the TUI used the real one — silently.
# Units now carry placeholders, filled here:
#   __MESHANCHOR_USER__  the operator login
#   __MESHANCHOR_HOME__  that login's home, from getent (not assumed /home/<u>)
#   __MESHANCHOR_GROUP__ that login's primary group (not assumed == login)
#
# Operator resolution (ma_resolve_unit_user), first hit wins:
#   1. $MESHANCHOR_OPERATOR            explicit override
#   2. the INSTALLED unit's SUDO_USER pin, only if that user EXISTS here — a
#      deploy keeps the box's user, but an old unit carrying a login from
#      another host (the #151 box) is not carried forward
#   3. $SUDO_USER, then $USER, then the real uid
# root and unknown users are refused. A render never leaves a placeholder in
# the destination; a refused render leaves the destination untouched.
#
# Sourced by install_noc.sh / update.sh / deploy_noc.sh. For units no
# installer handles (meshcore-radio, fleet-collector, fleet-watchdog):
#   sudo bash scripts/lib/unit_render.sh scripts/<unit>.service [dest-dir]

_ma_valid_login() {
    # shadow useradd's charset; getent is the existence check. Excludes the
    # sed-unsafe set (| & \ whitespace).
    [[ -n "$1" && "$1" != "root" && "$1" =~ ^[a-zA-Z0-9_.][a-zA-Z0-9_.-]*\$?$ ]] \
        && getent passwd "$1" >/dev/null 2>&1
}

ma_resolve_unit_user() {
    local installed="${1:-}" pin u
    if [[ -n "${MESHANCHOR_OPERATOR:-}" ]]; then
        _ma_valid_login "$MESHANCHOR_OPERATOR" && { echo "$MESHANCHOR_OPERATOR"; return 0; }
        echo "unit_render: MESHANCHOR_OPERATOR='$MESHANCHOR_OPERATOR' is root or not a user here" >&2
        return 1
    fi
    if [[ -n "$installed" && -f "$installed" ]]; then
        pin="$(sed -n 's/^Environment=SUDO_USER=\([^[:space:]]*\).*/\1/p' "$installed" | tail -1)"
        if _ma_valid_login "$pin"; then echo "$pin"; return 0; fi
        [[ -n "$pin" ]] && echo "unit_render: ignoring installed pin SUDO_USER='$pin' (not a user on this host)" >&2
    fi
    for u in "${SUDO_USER:-}" "${USER:-}" "$(id -un 2>/dev/null)"; do
        _ma_valid_login "$u" && { echo "$u"; return 0; }
    done
    echo "unit_render: no operator login resolved (SUDO_USER='${SUDO_USER:-}' USER='${USER:-}'); set MESHANCHOR_OPERATOR=<user>" >&2
    return 1
}

ma_render_unit() {
    local src="$1" dst="$2" user="$3" home group home_esc tmp
    if ! _ma_valid_login "$user"; then
        echo "unit_render: refusing '$user' for $(basename "$dst") (root, empty or not a user here)" >&2
        return 1
    fi
    home="$(getent passwd "$user" | cut -d: -f6)"
    [[ -n "$home" ]] || { echo "unit_render: no home for '$user'" >&2; return 1; }
    group="$(id -gn "$user" 2>/dev/null)"
    [[ -n "$group" ]] || { echo "unit_render: no primary group for '$user'" >&2; return 1; }
    home_esc="$(printf '%s' "$home" | sed 's/[&|\\]/\\&/g')"
    tmp="$(mktemp "$(dirname "$dst")/.unit_render.XXXXXX")" || return 1
    if ! sed -e "s|__MESHANCHOR_USER__|$user|g" -e "s|__MESHANCHOR_HOME__|$home_esc|g" \
             -e "s|__MESHANCHOR_GROUP__|$group|g" "$src" > "$tmp"; then
        rm -f "$tmp"; return 1
    fi
    if grep -q "__MESHANCHOR_" "$tmp"; then
        echo "unit_render: $(basename "$src") has an unknown placeholder: $(grep -o '__MESHANCHOR_[A-Z_]*__' "$tmp" | sort -u | tr '\n' ' ')" >&2
        rm -f "$tmp"; return 1
    fi
    chmod 0644 "$tmp" && mv -f "$tmp" "$dst"
}

# Refresh each unit ALREADY installed in <dst-dir> from <src-dir> — never
# installs a new one (a deploy must not change WHICH services run). Placeholder
# units render for this host's operator; a refused render keeps the installed
# unit, loudly. Returns 0 if any unit was updated, 1 if none.
#   ma_refresh_installed_units <src-dir> <dst-dir> <unit>...
ma_refresh_installed_units() {
    local src_dir="$1" dst_dir="$2" unit u updated=1
    shift 2
    for unit in "$@"; do
        [[ -f "$dst_dir/$unit" && -f "$src_dir/$unit" ]] || continue
        # Never replace a unit that runs a DIFFERENT PROGRAM under the same
        # name (2026-10-21: the TUI launcher's unit overwrote the running NOC
        # orchestrator's meshanchor.service; the next restart crash-looped it
        # 2,172 times). utils.user_unit_templates is the one rule.
        local sp_out sp_rc=0
        sp_out="$(PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$(dirname "${BASH_SOURCE[0]}")/../../src" timeout 30 python3 -m utils.user_unit_templates same-program "$dst_dir/$unit" "$src_dir/$unit" 2>/dev/null)" || sp_rc=$?
        if [[ $sp_rc -ne 0 ]]; then
            echo -e "  ${YELLOW:-}⚠ $unit NOT replaced — installed vs template: ${sp_out:-same-program check could not run (rc=$sp_rc)}; decide which program owns the name, then install it by hand${NC:-}"
            continue
        fi
        if grep -q "__MESHANCHOR_" "$src_dir/$unit"; then
            if u="$(ma_resolve_unit_user "$dst_dir/$unit")" \
                    && ma_render_unit "$src_dir/$unit" "$dst_dir/$unit" "$u"; then
                echo -e "  ${GREEN:-}✓ $unit updated (operator: $u)${NC:-}"
                updated=0
            else
                echo -e "  ${YELLOW:-}⚠ $unit NOT updated — no operator resolved; installed unit kept. Set MESHANCHOR_OPERATOR=<user> and re-run${NC:-}"
            fi
        else
            cp "$src_dir/$unit" "$dst_dir/$unit"
            echo -e "  ${GREEN:-}✓ $unit updated${NC:-}"
            updated=0
        fi
    done
    return "$updated"
}

# True if an INSTALLED unit is in a #151-broken shape: a leftover placeholder,
# or a SUDO_USER pin naming a login that does not exist on this host.
ma_unit_is_broken() {
    local installed="$1" pin
    grep -q "__MESHANCHOR_" "$installed" && return 0
    pin="$(sed -n 's/^Environment=SUDO_USER=\([^[:space:]]*\).*/\1/p' "$installed" | tail -1)"
    [[ -n "$pin" ]] && ! _ma_valid_login "$pin"
}

# Like ma_refresh_installed_units, but only for units in a broken shape. For
# units whose shipped file differs from a working install on purpose (the map:
# shipped on the coexist :5002, meshanchor-server serves :5000) — a blanket
# refresh would move a live service. Returns 0 if any unit was repaired.
#   ma_repair_installed_units <src-dir> <dst-dir> <unit>...
ma_repair_installed_units() {
    local src_dir="$1" dst_dir="$2" unit broken=()
    shift 2
    for unit in "$@"; do
        [[ -f "$dst_dir/$unit" ]] && ma_unit_is_broken "$dst_dir/$unit" && broken+=("$unit")
    done
    [[ ${#broken[@]} -gt 0 ]] || return 1
    ma_refresh_installed_units "$src_dir" "$dst_dir" "${broken[@]}"
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    set -u
    src="${1:?usage: unit_render.sh <unit-file> [dest-dir]}"
    dst="${2:-/etc/systemd/system}/$(basename "$src")"
    user="$(ma_resolve_unit_user "$dst")" || exit 1
    ma_render_unit "$src" "$dst" "$user" || exit 1
    echo "rendered $(basename "$src") for $user -> $dst (run: systemctl daemon-reload)"
fi
