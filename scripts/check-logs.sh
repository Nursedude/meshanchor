#!/bin/bash
# MeshAnchor Log Checker
# Usage: ./scripts/check-logs.sh

echo "========================================"
echo "MeshAnchor Diagnostic Log Check"
echo "========================================"
echo

# Check GTK log
echo "=== GTK Log (last 30 lines) ==="
if [ -f /tmp/meshanchor-gtk.log ]; then
    tail -30 /tmp/meshanchor-gtk.log
    echo
    echo "Errors/Warnings:"
    grep -i "error\|critical\|warning\|exception\|traceback" /tmp/meshanchor-gtk.log | tail -20
else
    echo "No GTK log found at /tmp/meshanchor-gtk.log"
fi
echo

# Check TUI log
echo "=== TUI Log (last 20 lines) ==="
if [ -f /tmp/meshanchor-tui.log ]; then
    tail -20 /tmp/meshanchor-tui.log
else
    echo "No TUI log found"
fi
echo

# Check user logs
USER_LOG_DIR="${HOME}/.config/meshanchor/logs"
echo "=== User Logs in ${USER_LOG_DIR} ==="
if [ -d "$USER_LOG_DIR" ]; then
    ls -la "$USER_LOG_DIR" | tail -10
    LATEST=$(ls -t "$USER_LOG_DIR"/meshanchor_*.log 2>/dev/null | head -1)
    if [ -n "$LATEST" ]; then
        echo
        echo "Latest log: $LATEST"
        echo "--- Last 20 lines ---"
        tail -20 "$LATEST"
    fi
else
    echo "No user log directory found"
    echo "Creating it now..."
    mkdir -p "$USER_LOG_DIR"
fi
echo

# Check journald for meshanchor
echo "=== System Journal (meshanchor) ==="
if journalctl --disk-usage &>/dev/null; then
    journalctl -b --no-pager -n 30 2>/dev/null | grep -i meshanchor || echo "No meshanchor entries in journal"
else
    echo "Journald not persistent - run: sudo mkdir -p /var/log/journal"
fi
echo

# Check for common issues
echo "=== Quick Health Check ==="
echo -n "meshtasticd running: "
systemctl is-active meshtasticd 2>/dev/null || echo "not active"

echo -n "Port 4403 open: "
ss -tln | grep -q ":4403 " && echo "yes" || echo "no"

echo -n "GTK log size: "
du -h /tmp/meshanchor-gtk.log 2>/dev/null || echo "N/A"

echo
echo "========================================"
echo "To watch logs live: tail -f /tmp/meshanchor-gtk.log"
echo "========================================"
