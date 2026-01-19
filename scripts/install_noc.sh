#!/bin/bash
#
# MeshForge NOC Stack Installer
#
# Installs the complete NOC stack:
#   - meshtasticd (Meshtastic daemon)
#   - Reticulum/RNS (rnsd)
#   - MeshForge (orchestrates everything)
#
# Usage:
#   sudo bash scripts/install_noc.sh
#
# Options:
#   --skip-meshtasticd    Don't install meshtasticd
#   --skip-rns            Don't install Reticulum
#   --client-only         Only install MeshForge as client (no daemons)
#

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

# Defaults
INSTALL_MESHTASTICD=true
INSTALL_RNS=true
INSTALL_DIR="/opt/meshforge"
VENV_DIR="$INSTALL_DIR/venv"

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --skip-meshtasticd)
            INSTALL_MESHTASTICD=false
            shift
            ;;
        --skip-rns)
            INSTALL_RNS=false
            shift
            ;;
        --client-only)
            INSTALL_MESHTASTICD=false
            INSTALL_RNS=false
            shift
            ;;
        *)
            echo -e "${RED}Unknown option: $1${NC}"
            exit 1
            ;;
    esac
done

echo -e "${CYAN}"
echo "╔═══════════════════════════════════════════════════════════╗"
echo "║        MeshForge NOC Stack Installer                      ║"
echo "║        Network Operations Center for Mesh Networks        ║"
echo "╚═══════════════════════════════════════════════════════════╝"
echo -e "${NC}"

# Check root
if [[ $EUID -ne 0 ]]; then
   echo -e "${RED}Error: This script must be run as root${NC}"
   echo "Please run: sudo bash $0"
   exit 1
fi

# ─────────────────────────────────────────────────────────────────
# Detect existing installations
# ─────────────────────────────────────────────────────────────────
echo -e "${CYAN}[1/8] Checking existing installations...${NC}"

MESHTASTICD_EXISTS=false
RNS_EXISTS=false
MESHFORGE_EXISTS=false

if systemctl list-unit-files | grep -q meshtasticd; then
    MESHTASTICD_EXISTS=true
    echo -e "  ${YELLOW}⚡ meshtasticd already installed${NC}"
fi

if command -v rnsd &> /dev/null; then
    RNS_EXISTS=true
    echo -e "  ${YELLOW}⚡ Reticulum (RNS) already installed${NC}"
fi

if [[ -d "$INSTALL_DIR" ]]; then
    MESHFORGE_EXISTS=true
    echo -e "  ${YELLOW}⚡ MeshForge already installed${NC}"
fi

if ! $MESHTASTICD_EXISTS && ! $RNS_EXISTS && ! $MESHFORGE_EXISTS; then
    echo -e "  ${GREEN}✓ Fresh installation${NC}"
fi

# ─────────────────────────────────────────────────────────────────
# Handle existing meshtasticd
# ─────────────────────────────────────────────────────────────────
if $MESHTASTICD_EXISTS && $INSTALL_MESHTASTICD; then
    echo ""
    echo -e "${YELLOW}═══ Existing meshtasticd Detected ═══${NC}"
    echo ""
    echo "  MeshForge can work in different modes:"
    echo ""
    echo -e "  ${BOLD}1)${NC} Take ownership ${GREEN}(Recommended)${NC}"
    echo "     MeshForge manages meshtasticd as part of NOC stack"
    echo ""
    echo -e "  ${BOLD}2)${NC} Connect as client"
    echo "     Use existing meshtasticd without managing it"
    echo ""
    echo -e "  ${BOLD}3)${NC} Skip meshtasticd setup"
    echo "     Install MeshForge only, configure later"
    echo ""

    if [[ -c /dev/tty ]]; then
        read -p "  Select mode [1/2/3] (default: 1): " -n 1 -r mode_choice < /dev/tty
        echo ""
        case $mode_choice in
            2)
                INSTALL_MESHTASTICD=false
                echo -e "  ${CYAN}→ Client mode selected${NC}"
                ;;
            3)
                INSTALL_MESHTASTICD=false
                echo -e "  ${CYAN}→ Skipping meshtasticd setup${NC}"
                ;;
            *)
                echo -e "  ${GREEN}→ Taking ownership of meshtasticd${NC}"
                ;;
        esac
    fi
fi

# ─────────────────────────────────────────────────────────────────
# System dependencies
# ─────────────────────────────────────────────────────────────────
echo -e "${CYAN}[2/8] Installing system dependencies...${NC}"
apt-get update -qq
apt-get install -y -qq \
    python3 python3-pip python3-venv \
    git wget curl \
    libusb-1.0-0 \
    &>/dev/null

echo -e "  ${GREEN}✓ System dependencies installed${NC}"

# ─────────────────────────────────────────────────────────────────
# Detect PEP 668 (externally-managed-environment)
# ─────────────────────────────────────────────────────────────────
PIP_ARGS=""
if python3 -c "import sys; sys.exit(0 if any('EXTERNALLY-MANAGED' in open(f).read() for f in __import__('glob').glob(sys.prefix + '/**/EXTERNALLY-MANAGED', recursive=True)) else 1)" 2>/dev/null; then
    echo -e "${YELLOW}  Detected: Externally managed Python (PEP 668)${NC}"
    PIP_ARGS="--break-system-packages"
fi

# ─────────────────────────────────────────────────────────────────
# Install meshtasticd
# ─────────────────────────────────────────────────────────────────
if $INSTALL_MESHTASTICD; then
    echo -e "${CYAN}[3/8] Installing meshtasticd...${NC}"

    # Install meshtastic Python package (includes meshtasticd)
    pip3 install $PIP_ARGS -q meshtastic

    # Create systemd service if not exists
    if ! systemctl list-unit-files | grep -q meshtasticd.service; then
        echo "  Creating meshtasticd systemd service..."

        cat > /etc/systemd/system/meshtasticd.service << 'MESHTASTICD_SERVICE'
[Unit]
Description=Meshtastic Daemon
Documentation=https://meshtastic.org
After=network.target

[Service]
Type=simple
User=root
ExecStart=/usr/local/bin/meshtasticd
Restart=on-failure
RestartSec=5

# Auto-detect device
Environment=MESHTASTIC_PORT=auto

[Install]
WantedBy=multi-user.target
MESHTASTICD_SERVICE

        systemctl daemon-reload
    fi

    # Create udev rules for USB radio devices
    if [[ ! -f /etc/udev/rules.d/99-meshtastic.rules ]]; then
        echo "  Creating udev rules for radio devices..."
        cat > /etc/udev/rules.d/99-meshtastic.rules << 'UDEV_RULES'
# Meshtastic USB devices
# T-Beam, Heltec, RAK, etc.

# Silicon Labs CP210x
SUBSYSTEM=="tty", ATTRS{idVendor}=="10c4", ATTRS{idProduct}=="ea60", MODE="0666", GROUP="dialout"

# CH340/CH341
SUBSYSTEM=="tty", ATTRS{idVendor}=="1a86", ATTRS{idProduct}=="7523", MODE="0666", GROUP="dialout"

# FTDI
SUBSYSTEM=="tty", ATTRS{idVendor}=="0403", ATTRS{idProduct}=="6001", MODE="0666", GROUP="dialout"

# ESP32 native USB
SUBSYSTEM=="tty", ATTRS{idVendor}=="303a", ATTRS{idProduct}=="1001", MODE="0666", GROUP="dialout"
UDEV_RULES

        udevadm control --reload-rules
        udevadm trigger
    fi

    echo -e "  ${GREEN}✓ meshtasticd installed${NC}"
else
    echo -e "${CYAN}[3/8] Skipping meshtasticd...${NC}"
    echo -e "  ${YELLOW}⊘ Skipped${NC}"
fi

# ─────────────────────────────────────────────────────────────────
# Install Reticulum (RNS)
# ─────────────────────────────────────────────────────────────────
if $INSTALL_RNS; then
    echo -e "${CYAN}[4/8] Installing Reticulum (RNS)...${NC}"

    pip3 install $PIP_ARGS -q rns

    # Create systemd service if not exists
    if ! systemctl list-unit-files | grep -q rnsd.service; then
        echo "  Creating rnsd systemd service..."

        cat > /etc/systemd/system/rnsd.service << 'RNSD_SERVICE'
[Unit]
Description=Reticulum Network Stack Daemon
Documentation=https://reticulum.network
After=network.target

[Service]
Type=simple
User=root
ExecStart=/usr/local/bin/rnsd -v
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
RNSD_SERVICE

        systemctl daemon-reload
    fi

    echo -e "  ${GREEN}✓ Reticulum installed${NC}"
else
    echo -e "${CYAN}[4/8] Skipping Reticulum...${NC}"
    echo -e "  ${YELLOW}⊘ Skipped${NC}"
fi

# ─────────────────────────────────────────────────────────────────
# Install/Update MeshForge
# ─────────────────────────────────────────────────────────────────
echo -e "${CYAN}[5/8] Installing MeshForge...${NC}"

if [[ -d "$INSTALL_DIR" ]]; then
    echo "  Updating existing installation..."
    git config --global --add safe.directory "$INSTALL_DIR" 2>/dev/null || true
    cd "$INSTALL_DIR"
    git pull -q || echo -e "  ${YELLOW}Warning: Could not pull updates${NC}"
else
    echo "  Cloning repository..."
    git clone -q https://github.com/Nursedude/meshforge.git "$INSTALL_DIR"
    git config --global --add safe.directory "$INSTALL_DIR" 2>/dev/null || true
    cd "$INSTALL_DIR"
fi

echo -e "  ${GREEN}✓ MeshForge source ready${NC}"

# ─────────────────────────────────────────────────────────────────
# Python dependencies
# ─────────────────────────────────────────────────────────────────
echo -e "${CYAN}[6/8] Installing Python dependencies...${NC}"

# Use virtual environment
if [[ ! -d "$VENV_DIR" ]]; then
    python3 -m venv "$VENV_DIR" --system-site-packages
fi

"$VENV_DIR/bin/pip" install -q --upgrade pip
"$VENV_DIR/bin/pip" install -q -r requirements.txt

echo -e "  ${GREEN}✓ Python dependencies installed${NC}"

# ─────────────────────────────────────────────────────────────────
# Create NOC configuration
# ─────────────────────────────────────────────────────────────────
echo -e "${CYAN}[7/8] Configuring NOC mode...${NC}"

# Determine mode based on what we installed
NOC_MODE="local"
if ! $INSTALL_MESHTASTICD; then
    NOC_MODE="client"
fi

# Create config directory
CONFIG_DIR="/etc/meshforge"
mkdir -p "$CONFIG_DIR"

# Create NOC config
cat > "$CONFIG_DIR/noc.yaml" << NOC_CONFIG
# MeshForge NOC Configuration
# Generated by install_noc.sh on $(date)

noc:
  mode: "$NOC_MODE"  # local | client | remote-only

  services:
    meshtasticd:
      managed: $INSTALL_MESHTASTICD
      auto_start: $INSTALL_MESHTASTICD

    rnsd:
      managed: $INSTALL_RNS
      auto_start: $INSTALL_RNS

  startup:
    auto_start_services: true
    health_check_interval: 30
    restart_on_failure: true
    max_restart_attempts: 3
NOC_CONFIG

echo -e "  ${GREEN}✓ NOC mode: $NOC_MODE${NC}"

# ─────────────────────────────────────────────────────────────────
# Create system commands and services
# ─────────────────────────────────────────────────────────────────
echo -e "${CYAN}[8/8] Creating system integration...${NC}"

# Main command
cat > /usr/local/bin/meshforge << 'MESHFORGE_CMD'
#!/bin/bash
cd /opt/meshforge
exec sudo /opt/meshforge/venv/bin/python src/launcher.py "$@"
MESHFORGE_CMD
chmod +x /usr/local/bin/meshforge

# NOC orchestrator command
cat > /usr/local/bin/meshforge-noc << 'NOC_CMD'
#!/bin/bash
cd /opt/meshforge
exec sudo /opt/meshforge/venv/bin/python -m core.orchestrator "$@"
NOC_CMD
chmod +x /usr/local/bin/meshforge-noc

# Update systemd service to use orchestrator
cat > /etc/systemd/system/meshforge.service << 'MESHFORGE_SERVICE'
[Unit]
Description=MeshForge Mesh Network Operations Center
Documentation=https://github.com/Nursedude/meshforge
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/meshforge
# Orchestrator manages meshtasticd and rnsd
ExecStart=/opt/meshforge/venv/bin/python -m core.orchestrator --start --monitor
ExecStop=/opt/meshforge/venv/bin/python -m core.orchestrator --stop
Restart=on-failure
RestartSec=10

Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
MESHFORGE_SERVICE

systemctl daemon-reload

echo -e "  ${GREEN}✓ System integration complete${NC}"

# ─────────────────────────────────────────────────────────────────
# Detect radio hardware
# ─────────────────────────────────────────────────────────────────
echo ""
echo -e "${CYAN}Detecting radio hardware...${NC}"

RADIO_FOUND=false
for dev in /dev/ttyUSB* /dev/ttyACM*; do
    if [[ -e "$dev" ]]; then
        echo -e "  ${GREEN}✓ Found: $dev${NC}"
        RADIO_FOUND=true
    fi
done

if ! $RADIO_FOUND; then
    echo -e "  ${YELLOW}⚠ No radio device detected${NC}"
    echo -e "  ${YELLOW}  Connect a Meshtastic radio via USB${NC}"
fi

# ─────────────────────────────────────────────────────────────────
# Summary
# ─────────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}╔═══════════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║         MeshForge NOC Installation Complete!              ║${NC}"
echo -e "${GREEN}╚═══════════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "${CYAN}Installed Components:${NC}"
if $INSTALL_MESHTASTICD; then
    echo -e "  ${GREEN}✓${NC} meshtasticd (Meshtastic daemon)"
fi
if $INSTALL_RNS; then
    echo -e "  ${GREEN}✓${NC} Reticulum (RNS)"
fi
echo -e "  ${GREEN}✓${NC} MeshForge NOC"
echo ""
echo -e "${CYAN}NOC Mode:${NC} $NOC_MODE"
echo ""
echo -e "${CYAN}Commands:${NC}"
echo "  ${GREEN}sudo meshforge${NC}           - Launch interface wizard"
echo "  ${GREEN}sudo meshforge-noc --start${NC} - Start NOC services"
echo "  ${GREEN}sudo meshforge-noc --status${NC} - Check service status"
echo "  ${GREEN}sudo meshforge-noc --stop${NC}  - Stop NOC services"
echo ""
echo -e "${CYAN}Systemd Service:${NC}"
echo "  ${GREEN}sudo systemctl enable meshforge${NC}  - Enable on boot"
echo "  ${GREEN}sudo systemctl start meshforge${NC}   - Start now"
echo ""

# Offer to start services
if [[ -c /dev/tty ]]; then
    echo -e "${CYAN}Would you like to start MeshForge NOC now? [Y/n]${NC}"
    read -r response < /dev/tty
    if [[ ! "$response" =~ ^([nN][oO]|[nN])$ ]]; then
        echo ""
        echo -e "${GREEN}Starting MeshForge NOC...${NC}"
        /usr/local/bin/meshforge-noc --start
        echo ""
        echo -e "${GREEN}NOC is running. Launch interface with: sudo meshforge${NC}"
    fi
fi

echo ""
echo -e "${CYAN}Documentation:${NC} https://github.com/Nursedude/meshforge"
echo -e "${CYAN}Made with aloha for the mesh community${NC}"
echo ""
