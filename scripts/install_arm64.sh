#!/bin/bash
# Meshtasticd installation script for 64-bit Raspberry Pi OS (arm64)

set -e

VERSION_TYPE="${1:-stable}"

echo "========================================="
echo "Meshtasticd Installer for Debian arm64"
echo "Version: $VERSION_TYPE"
echo "========================================="

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Check if running as root
if [ "$EUID" -ne 0 ]; then
    echo -e "${RED}Error: This script must be run as root${NC}"
    exit 1
fi

# Install dependencies
echo -e "\n${GREEN}Installing dependencies...${NC}"
apt-get install -y \
    curl \
    gnupg \
    lsb-release \
    apt-transport-https \
    ca-certificates \
    build-essential \
    python3 \
    python3-pip \
    git

# Add Meshtastic repository key
echo -e "\n${GREEN}Adding Meshtastic repository...${NC}"
curl -fsSL https://apt.meshtastic.org/meshtastic.gpg | gpg --dearmor -o /usr/share/keyrings/meshtastic-archive-keyring.gpg

# Add repository for Debian arm64
echo "deb [arch=arm64 signed-by=/usr/share/keyrings/meshtastic-archive-keyring.gpg] https://apt.meshtastic.org debian-bookworm main" > /etc/apt/sources.list.d/meshtastic.list

# Update package lists
echo -e "\n${GREEN}Updating package lists...${NC}"
apt-get update

# Install meshtasticd
if [ "$VERSION_TYPE" = "beta" ]; then
    echo -e "\n${GREEN}Installing meshtasticd (beta)...${NC}"
    # For beta, we might need to specify a version or use a different repository
    # This is a placeholder - adjust based on actual beta installation method
    apt-get install -y meshtasticd
else
    echo -e "\n${GREEN}Installing meshtasticd (stable)...${NC}"
    apt-get install -y meshtasticd
fi

# Install Python meshtastic library
echo -e "\n${GREEN}Installing meshtastic Python library...${NC}"
pip3 install --upgrade meshtastic

# Enable SPI and I2C
echo -e "\n${GREEN}Enabling SPI and I2C...${NC}"
if ! grep -q "^dtparam=spi=on" /boot/firmware/config.txt; then
    echo "dtparam=spi=on" >> /boot/firmware/config.txt
fi

if ! grep -q "^dtparam=i2c_arm=on" /boot/firmware/config.txt; then
    echo "dtparam=i2c_arm=on" >> /boot/firmware/config.txt
fi

# Load SPI module
modprobe spi_bcm2835 || true

echo -e "\n${GREEN}Installation completed!${NC}"
echo -e "${YELLOW}Note: A reboot may be required for SPI/I2C changes to take effect${NC}"

exit 0
