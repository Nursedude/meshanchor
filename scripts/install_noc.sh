#!/bin/bash
#
# MeshForge NOC Stack Installer
#
# Installs the complete NOC stack:
#   - meshtasticd (Meshtastic daemon) - auto-detects USB or SPI radio
#   - Reticulum/RNS (rnsd)
#   - MeshForge (orchestrates everything)
#
# Supports:
#   - USB Serial radios (T-Beam, Heltec, RAK USB) → Python CLI
#   - Native SPI radios (Meshtoad, RAK HAT) → Native meshtasticd binary
#
# Usage:
#   sudo bash scripts/install_noc.sh
#
# Options:
#   --skip-meshtasticd    Don't install meshtasticd
#   --skip-rns            Don't install Reticulum
#   --client-only         Only install MeshForge as client (no daemons)
#   --force-native        Force native meshtasticd (for SPI radios)
#   --force-python        Force Python meshtastic CLI (for USB radios)
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
MESHTASTICD_CONFIG_DIR="/etc/meshtasticd"
FORCE_NATIVE=false
FORCE_PYTHON=false

# Architecture detection
ARCH=$(dpkg --print-architecture 2>/dev/null || uname -m)
case $ARCH in
    aarch64|arm64) ARCH="arm64" ;;
    armv7l|armhf) ARCH="armhf" ;;
    x86_64|amd64) ARCH="amd64" ;;
esac

# OpenSUSE Build Service repo for native meshtasticd
# Supports: Debian_12, Debian_13, Debian_Testing, Raspbian_12, Ubuntu_24.04, etc.
OBS_BASE_URL="https://download.opensuse.org/repositories/network:/Meshtastic:/beta"

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
        --force-native)
            FORCE_NATIVE=true
            shift
            ;;
        --force-python)
            FORCE_PYTHON=true
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
# Radio Type Detection Functions
# ─────────────────────────────────────────────────────────────────

detect_radio_type() {
    # Returns: "spi", "usb", or "none"

    # Check for CH341 (Meshtoad USB-to-SPI adapter)
    if dmesg 2>/dev/null | grep -qi "ch341.*spi\|ch341-spi"; then
        echo "spi"
        return
    fi

    # Check for known SPI HAT configurations
    if [[ -e /dev/spidev0.0 ]] || [[ -e /dev/spidev0.1 ]]; then
        # Check if there's a SPI radio config or HAT overlay
        if grep -q "meshtastic\|sx126\|sx127\|lora" /boot/config.txt 2>/dev/null; then
            echo "spi"
            return
        fi
    fi

    # Check for USB serial devices
    if ls /dev/ttyUSB* /dev/ttyACM* 2>/dev/null | head -1 >/dev/null; then
        echo "usb"
        return
    fi

    echo "none"
}

get_usb_device() {
    # Find the first USB serial device
    for dev in /dev/ttyUSB* /dev/ttyACM*; do
        if [[ -e "$dev" ]]; then
            echo "$dev"
            return
        fi
    done
    echo ""
}

load_ch341_driver() {
    # Load CH341 kernel module if needed
    if ! lsmod | grep -q ch341; then
        echo -e "  ${CYAN}Loading CH341 driver...${NC}"
        modprobe ch341 2>/dev/null || true
        sleep 1
    fi
}

detect_os_repo() {
    # Detect OS and return the correct OpenSUSE Build Service repo name
    # Returns: Debian_12, Debian_13, Raspbian_12, Ubuntu_24.04, etc.

    if [[ ! -f /etc/os-release ]]; then
        echo "Debian_12"  # Fallback
        return
    fi

    # shellcheck source=/dev/null
    source /etc/os-release

    local os_name="${ID}"
    local version="${VERSION_ID:-}"
    local version_codename="${VERSION_CODENAME:-}"

    case "$os_name" in
        debian)
            case "$version" in
                12) echo "Debian_12" ;;
                13) echo "Debian_13" ;;
                *)
                    # Use codename for sid/testing
                    case "$version_codename" in
                        bookworm) echo "Debian_12" ;;
                        trixie)   echo "Debian_13" ;;
                        sid)      echo "Debian_Unstable" ;;
                        *)        echo "Debian_Testing" ;;
                    esac
                    ;;
            esac
            ;;
        raspbian)
            case "$version" in
                12) echo "Raspbian_12" ;;
                11) echo "Raspbian_11" ;;
                *)  echo "Raspbian_12" ;;  # Fallback to latest supported
            esac
            ;;
        ubuntu)
            case "$version" in
                24.04|24.10) echo "xUbuntu_24.04" ;;
                22.04)       echo "xUbuntu_22.04" ;;
                20.04)       echo "xUbuntu_20.04" ;;
                *)           echo "xUbuntu_24.04" ;;  # Fallback to latest
            esac
            ;;
        *)
            # Fallback: try Debian version if it's a Debian derivative
            if [[ -n "$version" ]]; then
                echo "Debian_${version%%.*}"
            else
                echo "Debian_12"
            fi
            ;;
    esac
}

add_meshtastic_repo() {
    # Add OpenSUSE Build Service meshtastic repo for the detected OS
    local os_repo
    os_repo=$(detect_os_repo)

    echo -e "  ${CYAN}Adding Meshtastic repo for ${BOLD}${os_repo}${NC}"

    local repo_url="${OBS_BASE_URL}/${os_repo}/"
    local key_url="${OBS_BASE_URL}/${os_repo}/Release.key"

    # Add repo
    echo "deb ${repo_url} /" > /etc/apt/sources.list.d/meshtastic.list

    # Add GPG key
    curl -fsSL "$key_url" | gpg --dearmor > /etc/apt/trusted.gpg.d/meshtastic.gpg 2>/dev/null

    # Update apt cache
    if ! apt-get update -qq 2>/dev/null; then
        echo -e "  ${YELLOW}Warning: apt update had errors (repo may be unavailable)${NC}"
        return 1
    fi

    return 0
}

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
    echo -e "  ${BOLD}q)${NC} Quit installer"
    echo ""

    if [[ -c /dev/tty ]]; then
        read -p "  Select mode [1/2/3/q] (default: 1): " -n 1 -r mode_choice < /dev/tty
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
            q|Q|0)
                echo -e "  ${YELLOW}→ Installation cancelled${NC}"
                exit 0
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

# Show detected OS
OS_REPO=$(detect_os_repo)
if [[ -f /etc/os-release ]]; then
    # shellcheck source=/dev/null
    source /etc/os-release
    echo -e "  ${CYAN}Detected: ${BOLD}${PRETTY_NAME:-$ID}${NC} → repo: ${BOLD}${OS_REPO}${NC}"
fi

apt-get update -qq
apt-get install -y -qq \
    python3 python3-pip python3-venv \
    git wget curl gnupg \
    libusb-1.0-0 \
    &>/dev/null

echo -e "  ${GREEN}✓ System dependencies installed${NC}"

# ─────────────────────────────────────────────────────────────────
# Detect PEP 668 (externally-managed-environment)
# ─────────────────────────────────────────────────────────────────
PIP_ARGS=""
# Check for EXTERNALLY-MANAGED file (Debian Bookworm, RPi OS)
if ls /usr/lib/python3*/EXTERNALLY-MANAGED 1>/dev/null 2>&1; then
    echo -e "${YELLOW}  Detected: Externally managed Python (PEP 668)${NC}"
    PIP_ARGS="--break-system-packages"
fi

# ─────────────────────────────────────────────────────────────────
# Install meshtasticd (auto-detect USB vs SPI)
# ─────────────────────────────────────────────────────────────────
if $INSTALL_MESHTASTICD; then
    echo -e "${CYAN}[3/8] Installing meshtasticd...${NC}"

    # Create udev rules first (needed for detection)
    if [[ ! -f /etc/udev/rules.d/99-meshtastic.rules ]]; then
        echo "  Creating udev rules for radio devices..."
        cat > /etc/udev/rules.d/99-meshtastic.rules << 'UDEV_RULES'
# Meshtastic USB devices
# T-Beam, Heltec, RAK, etc.

# Silicon Labs CP210x
SUBSYSTEM=="tty", ATTRS{idVendor}=="10c4", ATTRS{idProduct}=="ea60", MODE="0666", GROUP="dialout"

# CH340/CH341 (USB serial AND USB-to-SPI for Meshtoad)
SUBSYSTEM=="tty", ATTRS{idVendor}=="1a86", ATTRS{idProduct}=="7523", MODE="0666", GROUP="dialout"
SUBSYSTEM=="usb", ATTRS{idVendor}=="1a86", ATTRS{idProduct}=="5512", MODE="0666", GROUP="dialout"

# FTDI
SUBSYSTEM=="tty", ATTRS{idVendor}=="0403", ATTRS{idProduct}=="6001", MODE="0666", GROUP="dialout"

# ESP32 native USB
SUBSYSTEM=="tty", ATTRS{idVendor}=="303a", ATTRS{idProduct}=="1001", MODE="0666", GROUP="dialout"

# SPI device permissions (for native meshtasticd)
SUBSYSTEM=="spidev", MODE="0666", GROUP="spi"
UDEV_RULES

        udevadm control --reload-rules
        udevadm trigger
        sleep 1
    fi

    # Load CH341 driver if present (Meshtoad)
    load_ch341_driver

    # Determine radio type
    if $FORCE_NATIVE; then
        RADIO_TYPE="spi"
    elif $FORCE_PYTHON; then
        RADIO_TYPE="usb"
    else
        RADIO_TYPE=$(detect_radio_type)
    fi

    echo -e "  ${CYAN}Detected radio type: ${BOLD}${RADIO_TYPE}${NC}"

    # Create meshtasticd config directory structure
    echo "  Creating /etc/meshtasticd/ structure..."
    mkdir -p "$MESHTASTICD_CONFIG_DIR"/{available.d,config.d,ssl}
    chmod 700 "$MESHTASTICD_CONFIG_DIR/ssl"

    # Create config templates
    cat > "$MESHTASTICD_CONFIG_DIR/available.d/meshtoad-spi.yaml" << 'MESHTOAD_CONFIG'
# Meshtoad / MeshStick SPI Radio Configuration
# Uses CH341 USB-to-SPI adapter with SX1262
# Reference: https://github.com/markbirss/MESHSTICK

Lora:
  Module: sx1262
  spidev: ch341
  CS: 0
  IRQ: 6
  Reset: 2
  Busy: 4
  DIO2_AS_RF_SWITCH: true
  DIO3_TCXO_VOLTAGE: true

Logging:
  LogLevel: info

Webserver:
  Port: 9443
MESHTOAD_CONFIG

    cat > "$MESHTASTICD_CONFIG_DIR/available.d/rak-hat-spi.yaml" << 'RAK_CONFIG'
# RAK WisLink SPI HAT Configuration
# Direct GPIO connection on Raspberry Pi

Lora:
  Module: sx1262
  CS: 0
  IRQ: 22
  Busy: 23
  Reset: 24

Logging:
  LogLevel: info

Webserver:
  Port: 9443
RAK_CONFIG

    cat > "$MESHTASTICD_CONFIG_DIR/available.d/waveshare-spi.yaml" << 'WAVESHARE_CONFIG'
# Waveshare SX1262 LoRa HAT Configuration
# For Raspberry Pi (adjust gpiochip for Pi 5)

Lora:
  Module: sx1262
  DIO2_AS_RF_SWITCH: true
  CS: 21
  IRQ: 16
  Busy: 20
  Reset: 18
  # Uncomment for Raspberry Pi 5:
  # gpiochip: 4

Logging:
  LogLevel: info

Webserver:
  Port: 9443
WAVESHARE_CONFIG

    cat > "$MESHTASTICD_CONFIG_DIR/available.d/usb-serial.yaml" << 'USB_CONFIG'
# USB Serial Radio Configuration
# For radios connected via USB (T-Beam, Heltec, RAK USB)
# This config is for reference - USB radios use Python CLI

Serial:
  Device: auto

Logging:
  LogLevel: info
USB_CONFIG

    # Install appropriate daemon based on radio type
    case "$RADIO_TYPE" in
        spi)
            echo -e "  ${CYAN}Installing native meshtasticd for SPI radio...${NC}"

            NATIVE_INSTALLED=false

            # Check if already installed
            if command -v meshtasticd &>/dev/null; then
                INSTALLED_VERSION=$(meshtasticd --version 2>/dev/null || echo "unknown")
                echo -e "  ${GREEN}✓ Native meshtasticd already installed (${INSTALLED_VERSION})${NC}"
                NATIVE_INSTALLED=true
            else
                # Add OpenSUSE Build Service repo and install via apt
                if add_meshtastic_repo; then
                    echo "  Installing meshtasticd via apt..."
                    if apt-get install -y meshtasticd 2>&1 | tail -5; then
                        # Verify it actually installed
                        if command -v meshtasticd &>/dev/null; then
                            INSTALLED_VERSION=$(meshtasticd --version 2>/dev/null || echo "unknown")
                            echo -e "  ${GREEN}✓ Native meshtasticd installed (${INSTALLED_VERSION})${NC}"
                            NATIVE_INSTALLED=true
                        else
                            echo -e "  ${RED}Package installed but binary not found${NC}"
                        fi
                    else
                        echo -e "  ${RED}Failed to install meshtasticd via apt${NC}"
                    fi
                else
                    echo -e "  ${RED}Failed to add Meshtastic repo${NC}"
                fi

                # If native install failed, fall back to Python CLI
                if ! $NATIVE_INSTALLED; then
                    echo -e "  ${YELLOW}Native meshtasticd required for SPI radios${NC}"
                    echo -e "  ${YELLOW}Install from: https://meshtastic.org/docs/software/linux-native/${NC}"
                    pip3 install $PIP_ARGS --ignore-installed -q meshtastic

                    # Create placeholder service explaining the requirement
                    cat > /etc/systemd/system/meshtasticd.service << 'SPI_NEEDS_NATIVE'
[Unit]
Description=Meshtastic (Native daemon required for SPI)
Documentation=https://meshtastic.org/docs/software/linux-native/

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/bin/echo "SPI radios require native meshtasticd. Install from meshtastic.org then run: meshforge"

[Install]
WantedBy=multi-user.target
SPI_NEEDS_NATIVE
                    DAEMON_TYPE="spi-pending"
                    RADIO_TYPE="spi"  # Mark as SPI mode needing native daemon
                fi
            fi

            # Only create native configs if native binary is installed
            if $NATIVE_INSTALLED; then
                # Find actual binary path
                MESHTASTICD_BIN=$(command -v meshtasticd)
                echo -e "  ${GREEN}✓ Binary at: ${MESHTASTICD_BIN}${NC}"

                # Enable Meshtoad config by default (copy is more reliable than symlink)
                cp "$MESHTASTICD_CONFIG_DIR/available.d/meshtoad-spi.yaml" "$MESHTASTICD_CONFIG_DIR/config.d/"

                # Create main config.yaml (minimal - auto-loads from config.d/)
                cat > "$MESHTASTICD_CONFIG_DIR/config.yaml" << 'MAIN_CONFIG'
### MeshForge NOC - Meshtasticd Configuration
### Device configs are loaded from /etc/meshtasticd/config.d/
### Copy configs from available.d/ to config.d/ to activate
---
Lora:
  Module: auto

Logging:
  LogLevel: info

Webserver:
  Port: 9443
  RootPath: /usr/share/meshtasticd/web

General:
  MaxNodes: 400
  MaxMessageQueue: 100
  ConfigDirectory: /etc/meshtasticd/config.d/
  AvailableDirectory: /etc/meshtasticd/available.d/
MAIN_CONFIG

                # Create/update systemd service for native meshtasticd
                # Use the actual binary path we found
                cat > /etc/systemd/system/meshtasticd.service << NATIVE_SERVICE
[Unit]
Description=Meshtastic Daemon (Native SPI)
Documentation=https://meshtastic.org
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/etc/meshtasticd
ExecStart=${MESHTASTICD_BIN} -c /etc/meshtasticd/config.yaml
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
NATIVE_SERVICE

                DAEMON_TYPE="native"
            fi
            ;;

        usb)
            echo -e "  ${CYAN}Installing for USB serial radio...${NC}"

            # Install meshtastic Python package for CLI tools
            pip3 install $PIP_ARGS --ignore-installed -q meshtastic

            USB_DEV=$(get_usb_device)
            echo -e "  ${GREEN}✓ Python meshtastic CLI installed${NC}"
            if [[ -n "$USB_DEV" ]]; then
                echo -e "  ${GREEN}  USB device: $USB_DEV${NC}"
            fi

            # Enable USB config (copy is more reliable than symlink)
            cp "$MESHTASTICD_CONFIG_DIR/available.d/usb-serial.yaml" "$MESHTASTICD_CONFIG_DIR/config.d/"

            # Check if native meshtasticd is available (can work with USB serial too)
            if command -v meshtasticd &> /dev/null; then
                MESHTASTICD_BIN=$(command -v meshtasticd)
                echo -e "  ${GREEN}✓ Native meshtasticd available: ${MESHTASTICD_BIN}${NC}"

                # Create USB serial config for native daemon
                if [[ -n "$USB_DEV" ]]; then
                    cat > "$MESHTASTICD_CONFIG_DIR/config.d/usb-device.yaml" << USB_CONFIG
# USB Serial Radio Configuration (auto-generated)
Lora:
  SerialPath: ${USB_DEV}

Logging:
  LogLevel: info

Webserver:
  Port: 9443
  RootPath: /usr/share/meshtasticd/web

General:
  MaxNodes: 200
USB_CONFIG
                fi

                # Create service using native meshtasticd
                cat > /etc/systemd/system/meshtasticd.service << NATIVE_USB_SERVICE
[Unit]
Description=Meshtastic Daemon (USB Serial)
Documentation=https://meshtastic.org
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/etc/meshtasticd
ExecStart=${MESHTASTICD_BIN} -c /etc/meshtasticd/config.yaml
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
NATIVE_USB_SERVICE

                DAEMON_TYPE="native-usb"
            else
                # No native daemon - USB radios work directly without a service
                # The firmware handles mesh networking; CLI connects on demand
                echo -e "  ${YELLOW}Note: USB radios don't require a daemon service${NC}"
                echo -e "  ${YELLOW}  Use CLI: meshtastic --port ${USB_DEV:-/dev/ttyUSB0} --info${NC}"

                # Create a placeholder service that explains the situation
                cat > /etc/systemd/system/meshtasticd.service << 'USB_PLACEHOLDER'
[Unit]
Description=Meshtastic USB Radio (No Daemon Needed)
Documentation=https://meshtastic.org

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/bin/echo "USB radios work directly - use: meshtastic --port /dev/ttyUSB0 --info"

[Install]
WantedBy=multi-user.target
USB_PLACEHOLDER

                DAEMON_TYPE="usb-direct"
            fi
            ;;

        none)
            echo -e "  ${YELLOW}⚠ No radio detected${NC}"
            echo -e "  ${YELLOW}  Installing Python meshtastic CLI tools${NC}"

            pip3 install $PIP_ARGS --ignore-installed -q meshtastic

            # Check if native meshtasticd is available
            if command -v meshtasticd &> /dev/null; then
                MESHTASTICD_BIN=$(command -v meshtasticd)
                echo -e "  ${GREEN}✓ Native meshtasticd available: ${MESHTASTICD_BIN}${NC}"
                echo -e "  ${YELLOW}  Configure hardware in /etc/meshtasticd/config.d/${NC}"

                # Create service using native meshtasticd
                cat > /etc/systemd/system/meshtasticd.service << NATIVE_GENERIC
[Unit]
Description=Meshtastic Daemon
Documentation=https://meshtastic.org
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/etc/meshtasticd
ExecStart=${MESHTASTICD_BIN} -c /etc/meshtasticd/config.yaml
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
NATIVE_GENERIC

                DAEMON_TYPE="native"
            else
                # Create a placeholder service
                echo -e "  ${YELLOW}  Connect USB radio or configure SPI HAT${NC}"

                cat > /etc/systemd/system/meshtasticd.service << 'NO_RADIO_SERVICE'
[Unit]
Description=Meshtastic (No Radio Configured)
Documentation=https://meshtastic.org

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/bin/echo "No radio detected. Connect USB radio or configure SPI HAT, then run: meshforge"

[Install]
WantedBy=multi-user.target
NO_RADIO_SERVICE

                DAEMON_TYPE="placeholder"
            fi
            ;;
    esac

    systemctl daemon-reload

    echo -e "  ${GREEN}✓ meshtasticd installed (${DAEMON_TYPE})${NC}"
    echo -e "  ${GREEN}✓ Config directory: $MESHTASTICD_CONFIG_DIR${NC}"
else
    echo -e "${CYAN}[3/8] Skipping meshtasticd...${NC}"
    echo -e "  ${YELLOW}⊘ Skipped${NC}"
fi

# ─────────────────────────────────────────────────────────────────
# Install Reticulum (RNS)
# ─────────────────────────────────────────────────────────────────
if $INSTALL_RNS; then
    echo -e "${CYAN}[4/8] Installing Reticulum (RNS)...${NC}"

    pip3 install $PIP_ARGS --ignore-installed -q rns

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

# Set defaults if not set earlier
DAEMON_TYPE=${DAEMON_TYPE:-"python"}
RADIO_TYPE=${RADIO_TYPE:-"unknown"}
USB_DEV=${USB_DEV:-$(get_usb_device)}

# Create config directory
CONFIG_DIR="/etc/meshforge"
mkdir -p "$CONFIG_DIR"

# Create comprehensive NOC config
cat > "$CONFIG_DIR/noc.yaml" << NOC_CONFIG
# MeshForge NOC Configuration
# Generated by install_noc.sh on $(date)
# Architecture: $ARCH

noc:
  mode: "$NOC_MODE"  # local | client | remote-only
  version: "1.0.0"

  # Radio configuration
  radio:
    type: "$RADIO_TYPE"        # spi | usb | none
    daemon: "$DAEMON_TYPE"     # native | python
    device: "$USB_DEV"         # USB device path (if applicable)
    config_dir: "$MESHTASTICD_CONFIG_DIR"

  # Service management
  services:
    meshtasticd:
      managed: $INSTALL_MESHTASTICD
      auto_start: $INSTALL_MESHTASTICD
      daemon_type: "$DAEMON_TYPE"
      port: 4403

    rnsd:
      managed: $INSTALL_RNS
      auto_start: $INSTALL_RNS

  # Startup behavior
  startup:
    auto_start_services: true
    health_check_interval: 30
    restart_on_failure: true
    max_restart_attempts: 3

  # Paths
  paths:
    install_dir: "$INSTALL_DIR"
    venv_dir: "$VENV_DIR"
    meshtasticd_config: "$MESHTASTICD_CONFIG_DIR"
    meshforge_config: "$CONFIG_DIR"
NOC_CONFIG

echo -e "  ${GREEN}✓ NOC mode: $NOC_MODE${NC}"
echo -e "  ${GREEN}✓ Radio type: $RADIO_TYPE${NC}"
echo -e "  ${GREEN}✓ Daemon type: $DAEMON_TYPE${NC}"

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
cd /opt/meshforge/src
exec sudo /opt/meshforge/venv/bin/python -m core.orchestrator "$@"
NOC_CMD
chmod +x /usr/local/bin/meshforge-noc

# LoRa configuration helper
cat > /usr/local/bin/meshforge-lora << 'LORA_CMD'
#!/bin/bash
exec sudo /opt/meshforge/scripts/configure_lora.sh "$@"
LORA_CMD
chmod +x /usr/local/bin/meshforge-lora

# Update systemd service to use orchestrator
cat > /etc/systemd/system/meshforge.service << 'MESHFORGE_SERVICE'
[Unit]
Description=MeshForge Mesh Network Operations Center
Documentation=https://github.com/Nursedude/meshforge
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/meshforge/src
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
    if [[ "$DAEMON_TYPE" == "native" ]]; then
        echo -e "  ${GREEN}✓${NC} meshtasticd (native binary for SPI)"
    else
        echo -e "  ${GREEN}✓${NC} meshtastic (Python CLI for USB)"
    fi
fi
if $INSTALL_RNS; then
    echo -e "  ${GREEN}✓${NC} Reticulum (RNS)"
fi
echo -e "  ${GREEN}✓${NC} MeshForge NOC"
echo ""
echo -e "${CYAN}Configuration:${NC}"
echo -e "  NOC Mode:    ${BOLD}$NOC_MODE${NC}"
echo -e "  Radio Type:  ${BOLD}$RADIO_TYPE${NC}"
echo -e "  Daemon:      ${BOLD}$DAEMON_TYPE${NC}"
if [[ -n "$USB_DEV" ]]; then
    echo -e "  USB Device:  ${BOLD}$USB_DEV${NC}"
fi
echo ""
echo -e "${CYAN}Config Files:${NC}"
echo "  /etc/meshforge/noc.yaml           - MeshForge NOC config"
echo "  /etc/meshtasticd/config.yaml      - Meshtasticd config"
echo "  /etc/meshtasticd/available.d/     - Radio templates"
echo "  /etc/meshtasticd/config.d/        - Active configs"
echo ""
echo -e "${CYAN}Commands:${NC}"
echo -e "  ${GREEN}sudo meshforge${NC}             - Launch interface wizard"
echo -e "  ${GREEN}sudo meshforge-noc --start${NC}  - Start NOC services"
echo -e "  ${GREEN}sudo meshforge-noc --status${NC} - Check service status"
echo -e "  ${GREEN}sudo meshforge-noc --stop${NC}   - Stop NOC services"
echo ""
echo -e "${CYAN}Systemd Services:${NC}"
echo -e "  ${GREEN}sudo systemctl enable meshforge${NC}   - Enable on boot"
echo -e "  ${GREEN}sudo systemctl start meshforge${NC}    - Start now"
echo -e "  ${GREEN}sudo systemctl status meshtasticd${NC} - Check meshtasticd"
echo ""

# Post-install note for network configuration
if [[ "$DAEMON_TYPE" == "native" ]]; then
    # Get IP address for web UI URL
    LOCAL_IP=$(hostname -I 2>/dev/null | awk '{print $1}' || echo "localhost")

    echo -e "${YELLOW}╔═══════════════════════════════════════════════════════════╗${NC}"
    echo -e "${YELLOW}║  IMPORTANT: Configure LoRa settings to join a network!    ║${NC}"
    echo -e "${YELLOW}╚═══════════════════════════════════════════════════════════╝${NC}"
    echo ""
    echo -e "  Your radio needs these settings to match your mesh network:"
    echo -e "    - ${BOLD}Region${NC}     (US, EU_868, AU_915, etc.)"
    echo -e "    - ${BOLD}Channel${NC}    (frequency slot - MUST match network)"
    echo -e "    - ${BOLD}Preset${NC}     (LONG_FAST, SHORT_FAST, etc.)"
    echo -e "    - ${BOLD}TX Power${NC}   (depends on region/radio)"
    echo ""
    echo -e "  ${CYAN}Option 1: Web UI (Recommended)${NC}"
    echo -e "  ${GREEN}https://${LOCAL_IP}:9443${NC}"
    echo -e "  Navigate to: Config → LoRa"
    echo ""
    echo -e "  ${CYAN}Option 2: Interactive CLI wizard${NC}"
    echo -e "  ${GREEN}sudo meshforge-lora --interactive${NC}"
    echo ""
    echo -e "  ${CYAN}Option 3: Quick profile${NC}"
    echo -e "  ${GREEN}sudo meshforge-lora --profile us_default${NC}"
    echo ""
fi

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
