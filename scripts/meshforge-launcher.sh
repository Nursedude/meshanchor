#!/bin/bash
# MeshForge Launcher Script
# Launches the TUI (raspi-config style) interface

MESHFORGE_DIR="/opt/meshforge"

# Function to launch TUI with sudo
launch_tui() {
    local script="$MESHFORGE_DIR/src/launcher_tui/main.py"

    if [ "$EUID" -eq 0 ]; then
        exec python3 "$script" "$@"
    else
        exec sudo python3 "$script" "$@"
    fi
}

# Show usage help
show_help() {
    echo "MeshForge - Mesh Network Operations Center"
    echo ""
    echo "Usage: meshforge [command]"
    echo ""
    echo "Commands:"
    echo "  (none)    Launch TUI menu (default)"
    echo "  tui       Same as default"
    echo "  help      Show this help message"
    echo ""
    echo "The launcher uses whiptail/dialog for a"
    echo "raspi-config style interface that works over SSH."
    echo ""
    echo "Examples:"
    echo "  meshforge          # Launch TUI menu"
    echo "  sudo meshforge     # Launch with privileges"
}

# Determine which interface to launch
case "$1" in
    tui)
        shift
        launch_tui "$@"
        ;;
    help|--help|-h)
        show_help
        ;;
    *)
        launch_tui "$@"
        ;;
esac
