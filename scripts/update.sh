#!/bin/bash
#
# MeshAnchor Quick Update Script
#
# Updates an existing MeshAnchor installation in-place.
# Preserves all configuration and user data.
#
# Usage:
#   cd /opt/meshanchor && sudo ./scripts/update.sh
#   sudo ./scripts/update.sh              # from any directory
#   sudo ./scripts/update.sh --branch alpha  # update to specific branch
#
# What it does:
#   1. Fetches latest code from GitHub
#   2. Updates Python dependencies if changed
#   3. Reinstalls desktop integration (icons/launchers)
#   4. Updates systemd service files (rnsd, meshanchor, the *-user.service
#      templates a copier may deploy). Templates their installer RENDERS
#      (meshcore-chat, nomadnet-tmux, meshchatx: `# MF-DEPLOY: rendered`) are
#      NOT refreshed here — Software Updates > User Unit Templates shows which
#      are stale.
#   5. Preserves: /etc/meshanchor/, ~/.config/meshanchor/, radio configs
#
# What it does NOT do:
#   - Reinstall meshtasticd or rnsd (use install_noc.sh for that)
#   - Modify radio configurations
#   - Touch message queue or user settings
#

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

# Defaults
INSTALL_DIR="/opt/meshanchor"
BRANCH=""
SKIP_DESKTOP=false
SKIP_DEPS=false

# Kept for the post-pull self re-exec (lib/update_reexec.sh).
_MA_ORIG_ARGS=("$@")

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --branch|-b)
            BRANCH="$2"
            shift 2
            ;;
        --skip-desktop)
            SKIP_DESKTOP=true
            shift
            ;;
        --skip-deps)
            SKIP_DEPS=true
            shift
            ;;
        --help|-h)
            echo "MeshAnchor Update Script"
            echo ""
            echo "Usage: sudo $0 [options]"
            echo ""
            echo "Options:"
            echo "  --branch, -b BRANCH   Update to specific branch (default: current)"
            echo "  --skip-desktop        Skip desktop integration update"
            echo "  --skip-deps           Skip Python dependency update"
            echo "  --help, -h            Show this help"
            echo ""
            echo "Examples:"
            echo "  sudo $0                    # Update current branch"
            echo "  sudo $0 -b alpha           # Switch to alpha branch"
            echo "  sudo $0 -b main            # Switch to main branch"
            exit 0
            ;;
        *)
            echo -e "${RED}Unknown option: $1${NC}"
            echo "Use --help for usage"
            exit 1
            ;;
    esac
done

echo -e "${CYAN}"
echo "╔═══════════════════════════════════════════════════════════╗"
echo "║              MeshAnchor Quick Update                       ║"
echo "╚═══════════════════════════════════════════════════════════╝"
echo -e "${NC}"

# Check root
if [[ $EUID -ne 0 ]]; then
    echo -e "${RED}Error: This script must be run as root${NC}"
    echo "Please run: sudo $0"
    exit 1
fi

# Find installation
if [[ ! -d "$INSTALL_DIR" ]] && [[ ! -L "$INSTALL_DIR" ]]; then
    # Try current directory
    if [[ -f "./src/launcher_tui/main.py" ]]; then
        INSTALL_DIR="$(pwd)"
    else
        echo -e "${RED}Error: MeshAnchor not found at $INSTALL_DIR${NC}"
        echo "Run this script from your MeshAnchor directory, or install first with:"
        echo "  sudo bash scripts/install_noc.sh"
        exit 1
    fi
fi

cd "$INSTALL_DIR"

# Resolve symlinks
INSTALL_DIR="$(pwd -P)"
echo -e "Installation: ${GREEN}$INSTALL_DIR${NC}"

# Get current state
CURRENT_BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "unknown")
CURRENT_COMMIT=$(git rev-parse --short HEAD 2>/dev/null || echo "unknown")
# Re-executed after updating itself: count changes from where the FIRST run
# started, or the pull reads as a no-op and the daemons never restart.
if [[ -n "${MESHANCHOR_UPDATE_FROM:-}" ]] \
        && git cat-file -e "${MESHANCHOR_UPDATE_FROM}^{commit}" 2>/dev/null; then
    CURRENT_COMMIT="$MESHANCHOR_UPDATE_FROM"
fi
echo -e "Current branch: ${CYAN}$CURRENT_BRANCH${NC} ($CURRENT_COMMIT)"

# ─────────────────────────────────────────────────────────────────
# Step 1: Fetch updates
# ─────────────────────────────────────────────────────────────────
echo ""
echo -e "${CYAN}[1/5] Fetching updates...${NC}"

git config --global --add safe.directory "$INSTALL_DIR" 2>/dev/null || true

if ! git fetch origin 2>&1; then
    echo -e "${YELLOW}Warning: Could not fetch from origin${NC}"
    echo "Check your network connection"
fi

# Switch branch if requested
if [[ -n "$BRANCH" ]] && [[ "$BRANCH" != "$CURRENT_BRANCH" ]]; then
    echo -e "Switching to branch: ${GREEN}$BRANCH${NC}"

    # Check if branch exists
    if git show-ref --verify --quiet "refs/remotes/origin/$BRANCH"; then
        git checkout "$BRANCH" 2>/dev/null || git checkout -b "$BRANCH" "origin/$BRANCH"
        CURRENT_BRANCH="$BRANCH"
    else
        echo -e "${RED}Error: Branch '$BRANCH' not found${NC}"
        echo "Available branches:"
        git branch -r | grep -v HEAD | sed 's/origin\//  /'
        exit 1
    fi
fi

# Pull updates
echo "Pulling latest changes..."
# Whether the pull brought CODE changes (src / pyproject / requirements) — gates
# the on-pull restart of the heavy system daemons below so a docs-only pull does
# not bounce the NOC. Stays 0 on a no-op or a failed pull.
CODE_CHANGED=0
if git pull origin "$CURRENT_BRANCH" 2>&1; then
    NEW_COMMIT=$(git rev-parse --short HEAD)
    if [[ "$CURRENT_COMMIT" != "$NEW_COMMIT" ]]; then
        echo -e "${GREEN}Updated: $CURRENT_COMMIT → $NEW_COMMIT${NC}"
        if git diff --name-only "$CURRENT_COMMIT" "$NEW_COMMIT" 2>/dev/null \
                | grep -qE '^src/|^pyproject\.toml$|^requirements.*\.txt$'; then
            CODE_CHANGED=1
        fi

        # Show what changed
        echo ""
        echo "Recent changes:"
        git log --oneline "$CURRENT_COMMIT..$NEW_COMMIT" 2>/dev/null | head -10
    else
        echo -e "${GREEN}Already up to date${NC}"
    fi
else
    echo -e "${YELLOW}Warning: Pull failed, continuing with current code${NC}"
fi

# The pull may have replaced THIS script; bash would keep running the old text
# (#151 re-test). Re-run the new one, once.
# shellcheck source=lib/update_reexec.sh
. "$INSTALL_DIR/scripts/lib/update_reexec.sh"
ma_reexec_if_self_changed "$CURRENT_COMMIT" "$(git rev-parse --short HEAD)" \
    "$INSTALL_DIR" scripts/update.sh "${_MA_ORIG_ARGS[@]}"

# ─────────────────────────────────────────────────────────────────
# Step 2: Update Python dependencies
# ─────────────────────────────────────────────────────────────────
echo ""
echo -e "${CYAN}[2/5] Checking Python dependencies...${NC}"

if [[ "$SKIP_DEPS" == "true" ]]; then
    echo -e "${YELLOW}Skipped (--skip-deps)${NC}"
else
    VENV_DIR="$INSTALL_DIR/venv"

    if [[ -d "$VENV_DIR" ]]; then
        # Check if requirements.txt changed
        REQ_HASH=$(md5sum requirements.txt 2>/dev/null | cut -d' ' -f1)
        CACHED_HASH=""
        if [[ -f "$VENV_DIR/.requirements_hash" ]]; then
            CACHED_HASH=$(cat "$VENV_DIR/.requirements_hash")
        fi

        if [[ "$REQ_HASH" != "$CACHED_HASH" ]]; then
            echo "Requirements changed, updating..."
            "$VENV_DIR/bin/pip" install -q --upgrade pip
            "$VENV_DIR/bin/pip" install -q -r requirements.txt
            echo "$REQ_HASH" > "$VENV_DIR/.requirements_hash"
            echo -e "${GREEN}Dependencies updated${NC}"
        else
            echo -e "${GREEN}Dependencies up to date${NC}"
        fi
    else
        echo -e "${YELLOW}No venv found, skipping dependency update${NC}"
        echo "Run install_noc.sh to set up Python environment"
    fi
fi

# ─────────────────────────────────────────────────────────────────
# Step 3: Update desktop integration
# ─────────────────────────────────────────────────────────────────
echo ""
echo -e "${CYAN}[3/5] Updating desktop integration...${NC}"

if [[ "$SKIP_DESKTOP" == "true" ]]; then
    echo -e "${YELLOW}Skipped (--skip-desktop)${NC}"
elif [[ -f "$INSTALL_DIR/scripts/install-desktop.sh" ]]; then
    bash "$INSTALL_DIR/scripts/install-desktop.sh" 2>&1 | grep -E "^(Installing|Updated|✓)" || true
    echo -e "${GREEN}Desktop integration updated${NC}"
else
    echo -e "${YELLOW}Desktop script not found, skipping${NC}"
fi

# ─────────────────────────────────────────────────────────────────
# Step 4: Update service files
# ─────────────────────────────────────────────────────────────────
echo ""
echo -e "${CYAN}[4/5] Updating systemd service files...${NC}"

SVC_UPDATED=false

# Update the system unit files from repo templates (only those already installed
# in /etc/systemd/system/). meshanchor-daemon.service is the headless NOC daemon
# enabled at boot; meshanchor.service is the on-demand TUI launcher — refresh both
# files if present so a unit-file change (e.g. ExecStart) actually lands.
_MA_REFRESH_UNITS=(meshanchor-daemon.service meshanchor.service)
# shellcheck source=lib/unit_render.sh
. "$INSTALL_DIR/scripts/lib/unit_render.sh"
if ma_refresh_installed_units "$INSTALL_DIR/scripts" /etc/systemd/system "${_MA_REFRESH_UNITS[@]}"; then
    SVC_UPDATED=true
fi
# meshanchor-map.service is REPAIR-only (#151 re-test): only install_noc.sh
# ever rendered it, so a box kept the developer's login. Re-rendered only when
# broken (placeholder / SUDO_USER not a user here) — a working map may serve a
# different port than the shipped coexist :5002, and a refresh would move it.
if ma_repair_installed_units "$INSTALL_DIR/scripts" /etc/systemd/system meshanchor-map.service; then
    SVC_UPDATED=true
fi

# Update rnsd.service (system-level) if it exists
if [[ -f /etc/systemd/system/rnsd.service ]]; then
    RNSD_BIN=$(command -v rnsd 2>/dev/null || echo "/usr/local/bin/rnsd")
    # Only update if the current service lacks crash-loop protection
    if ! grep -q "StartLimitBurst" /etc/systemd/system/rnsd.service 2>/dev/null; then
        cat > /etc/systemd/system/rnsd.service << RNSD_SVC
[Unit]
Description=Reticulum Network Stack Daemon
Documentation=https://reticulum.network
After=network-online.target
Wants=network-online.target

# Stop crash-looping after 5 failures in 60 seconds
StartLimitIntervalSec=60
StartLimitBurst=5

[Service]
Type=simple
User=root
ExecStart=${RNSD_BIN} --service
Restart=on-failure
RestartSec=5

StandardOutput=journal
StandardError=journal
SyslogIdentifier=rnsd

[Install]
WantedBy=multi-user.target
RNSD_SVC
        echo -e "  ${GREEN}✓ rnsd.service updated (added crash-loop protection)${NC}"
        SVC_UPDATED=true
    else
        echo -e "  ${GREEN}✓ rnsd.service already current${NC}"
    fi
fi

# Deploy the rnsd IPv6-DAD wait drop-in. Without it, RNS AutoInterface binds a
# still-`tentative` fe80 address at boot and rnsd exits 255. Ported from
# MeshForge 2026-09-26 (fb140f58; root cause MF becd34cb, 2026-09-10): the
# drop-in was hand-deployed there, one box never got it, and it crashed on a
# reboot. Written only when the content differs, then picked up by the
# daemon-reload below. rnsd is NOT restarted here: the gate acts at rnsd's
# next start, and a restart just to apply it would open the #69 race window.
RNSD_LL_TMPL="$INSTALL_DIR/templates/systemd/rnsd.service.d/10-wait-for-ipv6-ll.conf"
RNSD_LL_DST="/etc/systemd/system/rnsd.service.d/10-wait-for-ipv6-ll.conf"
if [[ -f /etc/systemd/system/rnsd.service && -f "$RNSD_LL_TMPL" ]]; then
    # The template names /opt/meshanchor; follow a checkout that lives elsewhere.
    RNSD_LL_BODY="$(sed "s#/opt/meshanchor/#${INSTALL_DIR}/#g" "$RNSD_LL_TMPL")"
    # A box carrying BOTH checkouts (MeshForge + MeshAnchor) must not flip the
    # file between the two on every update: an installed copy that differs only
    # in which checkout's wait_for_ipv6_ll.sh it runs, where that script exists
    # and is executable, is current. Any other difference is rewritten.
    RNSD_LL_NORM='s#^ExecStartPre=[^ ]*/scripts/wait_for_ipv6_ll\.sh#ExecStartPre=@WAIT@#'
    RNSD_LL_CUR="$(sed -nE 's#^ExecStartPre=([^ ]*/scripts/wait_for_ipv6_ll\.sh).*#\1#p' "$RNSD_LL_DST" 2>/dev/null)"
    if [[ -f "$RNSD_LL_DST" && -n "$RNSD_LL_CUR" && -x "$RNSD_LL_CUR" \
          && "$(sed -E "$RNSD_LL_NORM" "$RNSD_LL_DST")" == "$(printf '%s\n' "$RNSD_LL_BODY" | sed -E "$RNSD_LL_NORM")" ]]; then
        echo -e "  ${GREEN}✓ rnsd IPv6-DAD wait drop-in already current (runs ${RNSD_LL_CUR})${NC}"
    elif [[ "$(cat "$RNSD_LL_DST" 2>/dev/null)" != "$RNSD_LL_BODY" ]]; then
        mkdir -p "$(dirname "$RNSD_LL_DST")"
        printf '%s\n' "$RNSD_LL_BODY" > "$RNSD_LL_DST"
        chmod 644 "$RNSD_LL_DST"
        echo -e "  ${GREEN}✓ rnsd IPv6-DAD wait drop-in installed (applies at rnsd's next start)${NC}"
        SVC_UPDATED=true
    else
        echo -e "  ${GREEN}✓ rnsd IPv6-DAD wait drop-in already current${NC}"
    fi
fi

# Deploy user-level service templates
#
# Ported from MeshForge 2026-09-10 (lead repo; MF scripts/update.sh + the
# resolve_operator_user() chokepoint in scripts/install_noc.sh, 7916d12d).
#
# $SUDO_USER and $USER are ADVISORY — a root cron or a systemd unit sets
# NEITHER, and `eval echo "~"` then expands to the invoking user's home, so
# every unit below lands under the wrong account while this still prints
# "✓ User service templates deployed". That false success is the defect; the
# empty value is only how it gets there (honest_failure_modes #1).
#
# Fall back to the real uid, which is the thing rather than a claim about it.
# When even that yields no operator — root with no sudo context — there IS no
# right answer, so SKIP the block loudly instead of deploying somewhere.
# One resolver for user AND system units (unit_render.sh; #151 review F6).
# shellcheck source=lib/unit_render.sh
. "$INSTALL_DIR/scripts/lib/unit_render.sh"
REAL_USER="$(ma_resolve_unit_user "" 2>/dev/null || true)"
if [[ -z "$REAL_USER" || "$REAL_USER" == "root" ]]; then
    echo -e "  ${YELLOW}⚠ No operator login resolved (SUDO_USER='$SUDO_USER' USER='$USER' id -un='$(id -un 2>/dev/null || true)')${NC}"
    echo -e "  ${YELLOW}  Skipping user-service deployment — re-run as: sudo bash scripts/update.sh${NC}"
    REAL_USER=""
fi
REAL_HOME=$(eval echo "~${REAL_USER}")
REAL_UID="$(id -u "${REAL_USER}" 2>/dev/null || echo "")"
USER_SYSTEMD_DIR="${REAL_HOME}/.config/systemd/user"

# User-scope systemctl that bridges a sudo/root context to the operator's user
# bus (Issue #45 documented incantation). Used to daemon-reload + restart changed
# USER units so a `git pull` actually lands the new code on the running daemon —
# the #79 deploy gap (ported from MeshForge: nothing restarted the user daemons
# after a pull, leaving them on OLD code until a hand-restart).
run_user_systemctl() {
    if [[ "$(id -un)" == "${REAL_USER}" ]]; then
        systemctl --user "$@"
    elif [[ -n "${REAL_UID}" ]]; then
        sudo -u "${REAL_USER}" -H \
            env "XDG_RUNTIME_DIR=/run/user/${REAL_UID}" \
                "DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/${REAL_UID}/bus" \
            systemctl --user "$@"
    else
        return 1
    fi
}

USER_SVC_UPDATED=false
# -n "$REAL_USER" is the gate: with no resolvable operator the target below is
# the invoking user's home, and deploying there then reporting success is worse
# than not deploying. The warning above already said why.
if [[ -n "$REAL_USER" && -d "$INSTALL_DIR/templates/systemd" ]]; then
    mkdir -p "$USER_SYSTEMD_DIR"
    # BEGIN user-template deploy (one rule: utils/user_unit_templates.py)
    # A template that DECLARES `# MF-DEPLOY: rendered TOKEN by <installer>`
    # is rendered by that installer and never copied raw here (2026-10-21: a raw
    # copy overwrote meshcore-chat's working unit; the next restart ran the
    # literal token). The plan is computed by the module the TUI uses too; if it
    # cannot run, NOTHING is copied and that is said — never a raw fallback.
    uut_rc=0
    uut_plan="$(PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$INSTALL_DIR/src" timeout 30 python3 -m utils.user_unit_templates plan "$INSTALL_DIR/templates/systemd" "$USER_SYSTEMD_DIR" 2>&1)" || uut_rc=$?
    if [[ $uut_rc -ne 0 ]]; then
        echo -e "  ${YELLOW}⚠ user-unit plan failed (rc=$uut_rc) — no *-user.service template copied: ${uut_plan:0:200}${NC}"
    else
        while IFS=$'\t' read -r uut_act uut_a uut_b uut_c uut_say; do
            case "$uut_act" in
                copy) cp "$uut_a" "$USER_SYSTEMD_DIR/$uut_b" 2>/dev/null || true ;;
                skip)
                    # the plan decides when a skip is worth saying (installed
                    # here, or the template itself is wrong) — the TUI prints
                    # the same set
                    if [[ "$uut_say" == "1" ]]; then
                        echo -e "  ${YELLOW}↷ ${uut_a}: not copied (${uut_b}) — ${uut_c}${NC}"
                    fi ;;
            esac
        done <<< "$uut_plan"
    fi
    # END user-template deploy
    # The MeshChatX wrapper is copied by install_meshchatx.sh (TUI-driven),
    # so a pull alone never reached it: the #69 rnsd-owner wait (2026-10-26)
    # would sit in the repo while the box kept the old wrapper. Refresh it
    # ONLY where it is already installed, byte-compared like the installer;
    # no restart — it takes effect on the next start, which is the boot race.
    mcx_src="$INSTALL_DIR/templates/python/meshchatx_wrapper.sh"
    mcx_dst="$REAL_HOME/.config/meshanchor/meshchatx_wrapper.sh"
    # Root writes into a user-owned dir: a symlink there is refused (never
    # followed), and `install` replaces the file rather than writing through it.
    if [[ -L "$mcx_dst" ]]; then
        echo -e "  ${YELLOW}⚠ $mcx_dst is a symlink — NOT refreshed (root never writes through a user-owned link); inspect it${NC}"
    elif [[ -f "$mcx_dst" && -f "$mcx_src" ]] && ! cmp -s "$mcx_src" "$mcx_dst"; then
        if install -o "$REAL_USER" -g "$(id -gn "$REAL_USER")" -m 0755 \
                "$mcx_src" "$mcx_dst"; then
            echo -e "  ${GREEN}✓ MeshChatX wrapper refreshed (applies on its next start)${NC}"
        else
            echo -e "  ${YELLOW}⚠ MeshChatX wrapper NOT refreshed — run: sudo bash $INSTALL_DIR/scripts/install_meshchatx.sh --refresh${NC}"
        fi
    fi
    chown -R "${REAL_USER}:" "$USER_SYSTEMD_DIR" 2>/dev/null || true
    USER_SVC_UPDATED=true
    if [[ $uut_rc -eq 0 ]]; then
        echo -e "  ${GREEN}✓ User service templates deployed${NC}"
    else
        echo -e "  ${YELLOW}⚠ *-user.service templates NOT refreshed (plan failed above)${NC}"
    fi
fi

# The pytest_tmp_prune cron (/tmp is RAM on the Pis). It was hand-installed box
# by box and one box never got it (2026-10-18). Adds it only where no line
# mentions the script; a commented line is a deliberate absence and is kept.
# Same helper fleet_sync.sh runs on every fleet box.
if [[ -n "$REAL_USER" && -f "$INSTALL_DIR/scripts/ensure_prune_cron.sh" ]]; then
    if [[ "$(id -un)" == "${REAL_USER}" ]]; then
        prune_out="$(timeout 30 bash "$INSTALL_DIR/scripts/ensure_prune_cron.sh" --apply 2>&1)" || true
    else
        prune_out="$(timeout 30 sudo -u "${REAL_USER}" -H bash "$INSTALL_DIR/scripts/ensure_prune_cron.sh" --apply 2>&1)" || true
    fi
    # `|| true`: this file runs under set -e, and a cron that cannot be
    # installed must not abort the update before the user-unit restarts below
    # (review F1). Only the three healthy statuses earn a tick; anything else —
    # a helper error, sudo refusing, a timeout's empty output — warns (F2).
    case "$prune_out" in
        present*|opted_out*|added*) echo -e "  ${GREEN}✓ prune cron: ${prune_out%%:*}${NC}" ;;
        *) echo -e "  ${YELLOW}⚠ prune cron: ${prune_out:-no output (timeout 30s?)}${NC}" ;;
    esac
fi

# Reload systemd if anything changed
if $SVC_UPDATED; then
    systemctl daemon-reload 2>/dev/null || true
    echo -e "  ${GREEN}✓ systemctl daemon-reload${NC}"
fi

# Restart changed USER daemons on the operator's bus so the running daemon picks
# up the new code (the #79 deploy gap, ported from MeshForge). try-restart only
# touches an already-active unit, so an operator-disabled/stopped daemon stays
# stopped (intent honored), and a box that does not run the unit is a clean
# no-op. meshanchor-echo runs MeshAnchor code (lab.lxmf_echo); the interactive
# tmux panes (meshcore-chat, nomadnet) are operator-driven via the TUI and the
# external-app wrappers (meshchatx, nomadnet, rnsd) are not this repo's code —
# so none of those are auto-restarted here.
if $USER_SVC_UPDATED; then
    if run_user_systemctl daemon-reload 2>/dev/null; then
        run_user_systemctl try-restart meshanchor-echo.service 2>/dev/null || true
        # mini-dudeai runs MeshAnchor code (the byte-locked engine + the MA
        # meshanchor_fleet preset), so a pull that changes it must reach the
        # running daemon — the #79 deploy-restart gap (WS-A). try-restart is a
        # clean no-op on a box that doesn't run it.
        run_user_systemctl try-restart meshanchor-mini-dudeai.service 2>/dev/null || true
        echo -e "  ${GREEN}✓ MeshAnchor user daemons refreshed (try-restart)${NC}"
    else
        echo -e "  ${YELLOW}⚠ Could not reach the operator user bus to restart user daemons.${NC}"
        echo -e "  ${YELLOW}  As ${REAL_USER}: systemctl --user restart meshanchor-echo.service${NC}"
    fi
fi

# System daemons that run THIS repo's code (the NOC + map). MeshAnchor has no
# fleet_sync.sh, so update.sh owns their on-pull restart (the system-daemon half
# of the #79 deploy gap). Gated on an actual CODE change (computed in step 1):
# bouncing the NOC on a docs-only pull is disruptive, whereas the lightweight
# echo responder above is try-restarted unconditionally. The helper keeps a
# literal unit name on each call line so the §3b-ii deploy-restart guard can see
# the wiring; try-restart no-ops when the unit is inactive and honors an
# operator-disabled unit (it never STARTS a stopped daemon).
try_restart_system_unit() {
    local unit="$1"
    if systemctl list-unit-files "$unit" 2>/dev/null | grep -q "^$unit"; then
        if systemctl try-restart "$unit" >/dev/null 2>&1; then
            echo -e "  ${GREEN}✓ $unit restarted (code changed)${NC}"
        else
            echo -e "  ${YELLOW}⚠ $unit try-restart failed — restart it manually${NC}"
        fi
    fi
}
if [[ "$CODE_CHANGED" == "1" ]]; then
    # meshanchor-daemon.service (src/daemon.py --foreground) is the headless NOC
    # daemon ENABLED at boot (deploy_noc.sh ENABLE_UNITS) — restart THIS one, not
    # meshanchor.service (the on-demand TUI launcher that deploy_noc.sh never
    # auto-enables; restarting it would kill an attached session and it picks up
    # new code on the next manual start). Fixed 2026-06-20: the old line restarted
    # the launcher, leaving the running daemon on stale code (the #79 gap).
    try_restart_system_unit meshanchor-daemon.service
    try_restart_system_unit meshanchor-map.service
fi

# ─────────────────────────────────────────────────────────────────
# Step 5: Verify installation
# ─────────────────────────────────────────────────────────────────
echo ""
echo -e "${CYAN}[5/5] Verifying installation...${NC}"

# Check version
VERSION=$(python3 -c "import sys; sys.path.insert(0, '$INSTALL_DIR/src'); from __version__ import __version__; print(__version__)" 2>/dev/null || echo "unknown")
echo -e "Version: ${GREEN}$VERSION${NC}"

# Quick import test
if python3 -c "import sys; sys.path.insert(0, '$INSTALL_DIR/src'); from gateway.circuit_breaker import CircuitBreaker" 2>/dev/null; then
    echo -e "Circuit breaker: ${GREEN}OK${NC}"
else
    echo -e "Circuit breaker: ${YELLOW}Not available${NC}"
fi

if python3 -c "import sys; sys.path.insert(0, '$INSTALL_DIR/src'); from gateway.bridge_health import BridgeStatus" 2>/dev/null; then
    echo -e "Bridge health: ${GREEN}OK${NC}"
else
    echo -e "Bridge health: ${YELLOW}Not available${NC}"
fi

# ─────────────────────────────────────────────────────────────────
# Done
# ─────────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}╔═══════════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║              Update complete!                             ║${NC}"
echo -e "${GREEN}╚═══════════════════════════════════════════════════════════╝${NC}"
echo ""
echo "Branch: $CURRENT_BRANCH"
echo "Version: $VERSION"
echo ""
echo "To run MeshAnchor:"
echo "  meshanchor              # If desktop installed"
echo "  $INSTALL_DIR/scripts/meshanchor-launcher.sh"
echo "    (use the launcher, not a bare sudo python3 — it keeps root bytecode out of the repo)"
echo ""
