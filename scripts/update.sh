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
#   4. Updates systemd service files (rnsd, meshanchor, nomadnet)
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
for _unit in meshanchor-daemon.service meshanchor.service; do
    if [[ -f "/etc/systemd/system/$_unit" ]] && [[ -f "$INSTALL_DIR/scripts/$_unit" ]]; then
        cp "$INSTALL_DIR/scripts/$_unit" "/etc/systemd/system/$_unit"
        echo -e "  ${GREEN}✓ $_unit updated${NC}"
        SVC_UPDATED=true
    fi
done

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

# Deploy user-level service templates
REAL_USER="${SUDO_USER:-$USER}"
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
if [[ -d "$INSTALL_DIR/templates/systemd" ]]; then
    mkdir -p "$USER_SYSTEMD_DIR"
    for tmpl in "$INSTALL_DIR/templates/systemd/"*-user.service; do
        if [[ -f "$tmpl" ]]; then
            svc_name=$(basename "$tmpl" | sed 's/-user\.service/.service/')
            cp "$tmpl" "$USER_SYSTEMD_DIR/$svc_name" 2>/dev/null || true
        fi
    done
    chown -R "${REAL_USER}:" "$USER_SYSTEMD_DIR" 2>/dev/null || true
    USER_SVC_UPDATED=true
    echo -e "  ${GREEN}✓ User service templates deployed${NC}"
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
echo "  sudo python3 $INSTALL_DIR/src/launcher_tui/main.py"
echo ""
