#!/bin/bash
# MeshAnchor SSH Login Message
# Installed to /etc/profile.d/meshanchor.sh

# Only show for interactive shells
[ -z "$PS1" ] && return

# Check if MeshAnchor is installed
if [ -x /usr/local/bin/meshanchor ] || [ -f /opt/meshanchor/src/launcher_tui/main.py ]; then
    # Show message on SSH login
    if [ -n "$SSH_TTY" ] || [ -n "$SSH_CLIENT" ]; then
        echo ""
        echo "  ┌─────────────────────────────────────┐"
        echo "  │  MeshAnchor NOC is installed         │"
        echo "  │  Type 'meshanchor' to launch         │"
        echo "  └─────────────────────────────────────┘"
        echo ""
    fi
fi
