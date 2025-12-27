#!/bin/bash
# Setup GPIO/SPI permissions for meshtasticd

set -e

echo "========================================="
echo "Setting up GPIO/SPI permissions"
echo "========================================="

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Check if running as root
if [ "$EUID" -ne 0 ]; then
    echo "Error: This script must be run as root"
    exit 1
fi

# Add meshtastic user to required groups
echo -e "${GREEN}Adding user permissions...${NC}"

# Create meshtastic user if it doesn't exist
if ! id "meshtastic" &>/dev/null; then
    useradd -r -s /bin/false meshtastic || true
fi

# Add to required groups
usermod -a -G gpio,spi,i2c,dialout meshtastic || true

# Set up udev rules for SPI devices
echo -e "\n${GREEN}Setting up udev rules...${NC}"

cat > /etc/udev/rules.d/99-meshtastic.rules << 'EOF'
# SPI devices
SUBSYSTEM=="spidev", GROUP="spi", MODE="0660"

# I2C devices
SUBSYSTEM=="i2c-dev", GROUP="i2c", MODE="0660"

# GPIO devices
SUBSYSTEM=="gpio", GROUP="gpio", MODE="0660"

# USB serial devices (for USB LoRa modules)
SUBSYSTEM=="tty", ATTRS{idVendor}=="1a86", ATTRS{idProduct}=="7523", GROUP="dialout", MODE="0660"
EOF

# Reload udev rules
echo -e "\n${GREEN}Reloading udev rules...${NC}"
udevadm control --reload-rules
udevadm trigger

# Ensure gpio/spi groups exist
echo -e "\n${GREEN}Creating required groups...${NC}"
groupadd -f gpio
groupadd -f spi
groupadd -f i2c

echo -e "\n${GREEN}Permissions setup completed!${NC}"
echo -e "${YELLOW}Note: You may need to log out and back in for group changes to take effect${NC}"

exit 0
