#!/bin/bash
# MeshAnchor Desktop Integration Installer
#
# Installs:
# - Desktop launcher in applications menu
# - Icon in standard locations
# - Symlink in /opt/meshanchor
#
# Usage: sudo ./scripts/install-desktop.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

# Resolve actual path (follow symlinks)
PROJECT_DIR="$(cd "$PROJECT_DIR" && pwd -P)"

echo "==========================================="
echo "MeshAnchor Desktop Integration Installer"
echo "==========================================="
echo

# Check if running as root
if [ "$EUID" -ne 0 ]; then
    echo "Please run as root: sudo $0"
    exit 1
fi

# Determine the actual user (not root)
if [ -n "$SUDO_USER" ]; then
    REAL_USER="$SUDO_USER"
    REAL_HOME=$(getent passwd "$SUDO_USER" | cut -d: -f6)
else
    REAL_USER=$(whoami)
    REAL_HOME="$HOME"
fi

echo "Installing for user: $REAL_USER"
echo "Project directory: $PROJECT_DIR"
echo

# Create /opt/meshanchor symlink (only if not already there)
echo "Setting up /opt/meshanchor..."
if [ "$PROJECT_DIR" = "/opt/meshanchor" ]; then
    echo "  Already installed at /opt/meshanchor"
elif [ -L /opt/meshanchor ]; then
    # Check if symlink points to correct location
    CURRENT_TARGET="$(readlink -f /opt/meshanchor 2>/dev/null || echo '')"
    if [ "$CURRENT_TARGET" = "$PROJECT_DIR" ]; then
        echo "  Symlink already correct: /opt/meshanchor -> $PROJECT_DIR"
    else
        rm /opt/meshanchor
        ln -sf "$PROJECT_DIR" /opt/meshanchor
        echo "  Updated symlink: /opt/meshanchor -> $PROJECT_DIR"
    fi
elif [ -d /opt/meshanchor ]; then
    echo "Warning: /opt/meshanchor exists as directory, backing up..."
    mv /opt/meshanchor /opt/meshanchor.backup.$(date +%Y%m%d%H%M%S)
    ln -sf "$PROJECT_DIR" /opt/meshanchor
    echo "  /opt/meshanchor -> $PROJECT_DIR"
else
    ln -sf "$PROJECT_DIR" /opt/meshanchor
    echo "  /opt/meshanchor -> $PROJECT_DIR"
fi

# Install icon
echo "Installing icon..."
ICON_SIZES=(16 24 32 48 64 128 256)
for size in "${ICON_SIZES[@]}"; do
    ICON_DIR="/usr/share/icons/hicolor/${size}x${size}/apps"
    mkdir -p "$ICON_DIR"

    # Install SVG at all sizes (works on modern systems)
    if [ -f "$PROJECT_DIR/assets/meshanchor-icon.svg" ]; then
        cp "$PROJECT_DIR/assets/meshanchor-icon.svg" "$ICON_DIR/meshanchor.svg"
        # Also install with app_id name for desktop taskbar
        cp "$PROJECT_DIR/assets/meshanchor-icon.svg" "$ICON_DIR/org.meshanchor.app.svg"
    fi
done

# Install scalable SVG (required for desktop taskbar icon)
echo "Installing scalable icon for desktop taskbar..."
SCALABLE_DIR="/usr/share/icons/hicolor/scalable/apps"
mkdir -p "$SCALABLE_DIR"
if [ -f "$PROJECT_DIR/assets/meshanchor-icon.svg" ]; then
    cp "$PROJECT_DIR/assets/meshanchor-icon.svg" "$SCALABLE_DIR/meshanchor.svg"
    cp "$PROJECT_DIR/assets/meshanchor-icon.svg" "$SCALABLE_DIR/org.meshanchor.app.svg"
fi

# Also install to pixmaps
echo "Installing to pixmaps..."
mkdir -p /usr/share/pixmaps
if [ -f "$PROJECT_DIR/assets/meshanchor-icon.svg" ]; then
    cp "$PROJECT_DIR/assets/meshanchor-icon.svg" /usr/share/pixmaps/meshanchor.svg
    cp "$PROJECT_DIR/assets/meshanchor-icon.svg" /usr/share/pixmaps/org.meshanchor.app.svg
    # Create a PNG version for compatibility
    if command -v rsvg-convert &> /dev/null; then
        rsvg-convert -w 128 -h 128 "$PROJECT_DIR/assets/meshanchor-icon.svg" > /usr/share/pixmaps/meshanchor.png
        rsvg-convert -w 128 -h 128 "$PROJECT_DIR/assets/meshanchor-icon.svg" > /usr/share/pixmaps/org.meshanchor.app.png
    elif command -v convert &> /dev/null; then
        convert -background none "$PROJECT_DIR/assets/meshanchor-icon.svg" -resize 128x128 /usr/share/pixmaps/meshanchor.png
        convert -background none "$PROJECT_DIR/assets/meshanchor-icon.svg" -resize 128x128 /usr/share/pixmaps/org.meshanchor.app.png
    fi
fi

# Update desktop file to use installed icon
echo "Installing desktop file..."
DESKTOP_FILE="$PROJECT_DIR/org.meshanchor.app.desktop"
INSTALLED_DESKTOP="/usr/share/applications/org.meshanchor.app.desktop"

# Remove old meshanchor.desktop if it exists
rm -f /usr/share/applications/meshanchor.desktop 2>/dev/null || true

# Copy desktop file (Icon=org.meshanchor.app matches desktop app_id)
cp "$DESKTOP_FILE" "$INSTALLED_DESKTOP"
chmod 644 "$INSTALLED_DESKTOP"

# Also install to user's local applications (for menu)
USER_APPS_DIR="$REAL_HOME/.local/share/applications"
mkdir -p "$USER_APPS_DIR"
rm -f "$USER_APPS_DIR/meshanchor.desktop" 2>/dev/null || true
cp "$INSTALLED_DESKTOP" "$USER_APPS_DIR/"
chown "$REAL_USER:$REAL_USER" "$USER_APPS_DIR/org.meshanchor.app.desktop"

# Update desktop database
echo "Updating desktop database..."
if command -v update-desktop-database &> /dev/null; then
    update-desktop-database /usr/share/applications 2>/dev/null || true
    update-desktop-database "$USER_APPS_DIR" 2>/dev/null || true
fi

# Update icon cache
echo "Updating icon cache..."
if command -v gtk-update-icon-cache &> /dev/null; then
    gtk-update-icon-cache -f /usr/share/icons/hicolor 2>/dev/null || true
fi

# Install launcher scripts
echo "Installing launcher scripts..."
cp "$PROJECT_DIR/scripts/meshanchor-launcher.sh" /usr/local/bin/meshanchor
chmod 755 /usr/local/bin/meshanchor

# Install terminal launcher (sets proper window class for icons)
cp "$PROJECT_DIR/scripts/meshanchor-terminal.sh" /usr/local/bin/meshanchor-terminal
chmod 755 /usr/local/bin/meshanchor-terminal


# Install polkit policy (for pkexec authentication)
echo "Installing polkit policy..."
POLKIT_DIR="/usr/share/polkit-1/actions"
if [ -d "$POLKIT_DIR" ]; then
    cp "$PROJECT_DIR/assets/org.meshanchor.policy" "$POLKIT_DIR/"
    chmod 644 "$POLKIT_DIR/org.meshanchor.policy"
    echo "  Polkit policy installed - pkexec will prompt for password"
else
    echo "  Warning: Polkit not found, desktop launcher may not work"
fi

# Install SSH login message
echo "Installing SSH login message..."
if [ -d /etc/profile.d ]; then
    cp "$PROJECT_DIR/scripts/meshanchor-profile.sh" /etc/profile.d/meshanchor.sh
    chmod 644 /etc/profile.d/meshanchor.sh
    echo "  SSH users will see MeshAnchor prompt on login"
fi

echo
echo "==========================================="
echo "Installation complete!"
echo "==========================================="
echo
echo "MeshAnchor has been added to your applications menu."
echo "You can find it under:"
echo "  - Internet"
echo "  - System Tools"
echo
echo "Or search for 'MeshAnchor' in your application launcher."
echo
echo "To run from command line:"
echo "  meshanchor            # TUI launcher (default, works over SSH)"
echo "  meshanchor tui        # Same as default"
echo "  meshanchor maps       # Coverage map generator"
echo "  meshanchor prometheus # Metrics exporter"
echo
