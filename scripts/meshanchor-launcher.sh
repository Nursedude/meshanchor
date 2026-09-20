#!/bin/bash
# MeshAnchor Launcher Script
# Launches MeshAnchor tools and services

MESHANCHOR_DIR="/opt/meshanchor"

# Root's bytecode must not land in the repo — see the file for the fleet-wide
# census that forced this. Sourced, never copied (scripts/lib convention).
# shellcheck source=lib/pycache_prefix.sh
. "$MESHANCHOR_DIR/scripts/lib/pycache_prefix.sh"

# THE interpreter this install actually uses. `.no-venv` is the installer's
# own marker; without it the venv is authoritative, and using system python3
# there would silently miss every dependency installed into the venv.
ma_python() {
    if [ -f "$MESHANCHOR_DIR/.no-venv" ] || [ ! -x "$MESHANCHOR_DIR/venv/bin/python" ]; then
        echo "python3"
    else
        echo "$MESHANCHOR_DIR/venv/bin/python"
    fi
}

# Function to launch the NOC with sudo.
#
# PORTED from MeshForge 2026-09-20 (MF 4e726548 + efdc6615 + b29e26ae). Routes
# through src/launcher.py, NOT straight at launcher_tui/main.py: launcher.py
# does profile detection, the startup health check and the setup wizard, then
# `os.execv`s into launcher_tui/main.py with sys.executable — so the TUI still
# appears and the environment, PYTHONPYCACHEPREFIX included, is inherited
# across the exec (VERIFIED live on MF 2026-09-20: the running main.py's
# /proc environ carried the prefix).
# ⚠️ launcher.py's DEFAULT is its interface menu plus NOC service startup;
# it goes straight to the TUI only with `--tui` or a saved auto_launch
# preference. `meshanchor tui` / `meshanchor-tui` pass `--tui` (see below).
#
# Before this port the installed /usr/local/bin/meshanchor was a generated
# copy that ALREADY ran launcher.py via the venv, while this script went
# straight to main.py with system python3 — two different programs under one
# name. This shape is the one the installed command already had.
launch_tui() {
    cd "$MESHANCHOR_DIR" || exit 1
    local py; py="$(ma_python)"

    if [ "$EUID" -eq 0 ]; then
        exec env PYTHONPYCACHEPREFIX="$MA_ROOT_PYCACHE" "$py" src/launcher.py "$@"
    else
        exec sudo PYTHONPYCACHEPREFIX="$MA_ROOT_PYCACHE" "$py" src/launcher.py "$@"
    fi
}

# Function to launch coverage map generator
launch_maps() {
    cd "$MESHANCHOR_DIR"
    exec python3 -c "
from src.utils.coverage_map import CoverageMapGenerator
import sys

gen = CoverageMapGenerator()
output = sys.argv[1] if len(sys.argv) > 1 else 'coverage_map.html'
gen.generate(output)
print(f'Map generated: {output}')
" "$@"
}

# Function to launch prometheus metrics server
launch_prometheus() {
    local port="${1:-9090}"
    cd "$MESHANCHOR_DIR"

    if [ "$EUID" -eq 0 ]; then
        exec env PYTHONPYCACHEPREFIX="$MA_ROOT_PYCACHE" python3 -c "
from src.utils.metrics_export import start_metrics_server
import signal
import sys

port = int(sys.argv[1]) if len(sys.argv) > 1 else 9090
print(f'Starting Prometheus metrics server on port {port}...')
print(f'Scrape endpoint: http://localhost:{port}/metrics')
print('Press Ctrl+C to stop')

server = start_metrics_server(port=port)
signal.signal(signal.SIGINT, lambda s, f: sys.exit(0))
signal.pause()
" "$port"
    else
        exec sudo PYTHONPYCACHEPREFIX="$MA_ROOT_PYCACHE" python3 -c "
from src.utils.metrics_export import start_metrics_server
import signal
import sys

port = int(sys.argv[1]) if len(sys.argv) > 1 else 9090
print(f'Starting Prometheus metrics server on port {port}...')
print(f'Scrape endpoint: http://localhost:{port}/metrics')
print('Press Ctrl+C to stop')

server = start_metrics_server(port=port)
signal.signal(signal.SIGINT, lambda s, f: sys.exit(0))
signal.pause()
" "$port"
    fi
}

# ---------------------------------------------------------------------------
# The five OTHER installed commands (2026-09-20). Until now install_noc.sh
# wrote /usr/local/bin/meshanchor-noc/-lora/-status/-web/-map as generated copies:
# frozen at install time, and the -noc one ran a privileged venv python with
# NO pycache prefix on every box it was typed on (root bytecode in the repo).
# They are now symlinks to this script, dispatched on basename below, so a
# pull moves them and scripts/guard_drill.py Layer D covers each name. The
# bodies are the installer's heredocs, moved here verbatim except for the
# interpreter (ma_python, honouring .no-venv) and the prefix on privileged runs.
# ---------------------------------------------------------------------------

# meshanchor-noc: the NOC orchestrator (privileged).
launch_noc() {
    cd "$MESHANCHOR_DIR/src" || exit 1
    local py; py="$(ma_python)"
    if [ "$EUID" -eq 0 ]; then
        exec env PYTHONPYCACHEPREFIX="$MA_ROOT_PYCACHE" "$py" -m core.orchestrator "$@"
    else
        exec sudo PYTHONPYCACHEPREFIX="$MA_ROOT_PYCACHE" "$py" -m core.orchestrator "$@"
    fi
}

# meshanchor-lora: LoRa configuration helper (a privileged shell script, no python).
launch_lora() {
    if [ "$EUID" -eq 0 ]; then
        exec "$MESHANCHOR_DIR/scripts/configure_lora.sh" "$@"
    else
        exec sudo "$MESHANCHOR_DIR/scripts/configure_lora.sh" "$@"
    fi
}

# meshanchor-status: terminal-native one-shot status. UNPRIVILEGED by design, so
# the interpreter is `$upy`, not `$py` — TestPrivilegedPycachePrefix reads
# `$py` as "a privileged launch" and would demand the prefix here. The root
# branch (a typed `sudo meshanchor-status`) still carries it.
launch_status() {
    cd "$MESHANCHOR_DIR" || exit 1
    local upy; upy="$(ma_python)"
    if [ "$EUID" -eq 0 ]; then
        exec env PYTHONPYCACHEPREFIX="$MA_ROOT_PYCACHE" "$upy" src/cli/status.py "$@"
    else
        exec "$upy" src/cli/status.py "$@"
    fi
}

# meshanchor-web: open or display the meshtasticd web client URL (pure bash).
launch_web() {
    local LOCAL_IP URL
    LOCAL_IP=$(hostname -I 2>/dev/null | awk '{print $1}')
    [ -z "$LOCAL_IP" ] && LOCAL_IP="localhost"
    URL="https://${LOCAL_IP}:9443"

    # Check if meshtasticd web server is responding
    if timeout 2 bash -c "echo >/dev/tcp/${LOCAL_IP}/9443" 2>/dev/null; then
        echo "Meshtastic Web Client: ${URL}"
        echo ""
        echo "  Full radio configuration in your browser:"
        echo "    • Region, Preset, TX Power (Config → LoRa)"
        echo "    • Channels and PSK keys   (Config → Channels)"
        echo "    • Node name and position   (Config → Device)"
        echo "    • Messaging and map view"
        echo ""
        # Try to open browser (works on desktop, no-op on headless)
        if command -v xdg-open &>/dev/null && [ -n "$DISPLAY" ]; then
            xdg-open "$URL" 2>/dev/null &
            echo "  Opening browser..."
        else
            echo "  Open this URL in any browser on your network:"
            echo "  ${URL}"
        fi
    else
        echo "ERROR: meshtasticd web server not responding on port 9443"
        echo ""
        echo "  Check: sudo systemctl status meshtasticd"
        echo "  Start: sudo systemctl start meshtasticd"
        echo ""
        echo "  The web client is served by meshtasticd when running."
        echo "  Config: /etc/meshtasticd/config.yaml (Webserver section)"
    fi
}

# meshanchor-map (the INSTALLED name) = the MAP SERVER control on port 5000. NOT
# the `map`/`maps` subcommand above, which generates a coverage map: the
# installed name predates the subcommand, so basename dispatch routes it to
# `mapserver` and the two never meet.
launch_mapserver() {
    local upy; upy="$(ma_python)"
    local LOCAL_IP
    case "${1:-}" in
        start)
            echo "Starting MeshAnchor Map Server..."
            sudo systemctl start meshanchor-map
            ;;
        stop)
            echo "Stopping MeshAnchor Map Server..."
            sudo systemctl stop meshanchor-map
            ;;
        restart)
            echo "Restarting MeshAnchor Map Server..."
            sudo systemctl restart meshanchor-map
            ;;
        status)
            systemctl status meshanchor-map --no-pager
            cd "$MESHANCHOR_DIR/src" && "$upy" -m utils.map_data_service --status
            ;;
        enable)
            echo "Enabling MeshAnchor Map Server on boot..."
            sudo systemctl enable meshanchor-map
            ;;
        disable)
            echo "Disabling MeshAnchor Map Server on boot..."
            sudo systemctl disable meshanchor-map
            ;;
        url)
            LOCAL_IP=$(hostname -I 2>/dev/null | awk '{print $1}')
            [ -z "$LOCAL_IP" ] && LOCAL_IP="localhost"
            echo "MeshAnchor Map: http://${LOCAL_IP}:5000/"
            ;;
        *)
            # Default: run interactively (for debugging)
            cd "$MESHANCHOR_DIR/src" || exit 1
            if [ "$EUID" -eq 0 ]; then
                exec env PYTHONPYCACHEPREFIX="$MA_ROOT_PYCACHE" "$upy" -m utils.map_data_service "$@"
            else
                exec "$upy" -m utils.map_data_service "$@"
            fi
            ;;
    esac
}

# Show usage help
show_help() {
    echo "MeshAnchor - Mesh Network Operations Center"
    echo ""
    echo "Usage: meshanchor [command] [options]"
    echo ""
    echo "Commands:"
    echo "  (none)         Launch the NOC launcher (menu, or auto-launch the TUI"
    echo "                 if you saved that preference); starts NOC services"
    echo "  tui            Launch the TUI directly — skips the launcher menu AND"
    echo "                 NOC service startup (same as the meshanchor-tui command)"
    echo "  maps [file]    Generate coverage map (default: coverage_map.html)"
    echo "  prometheus [p] Start Prometheus metrics server (default port: 9090)"
    echo "  noc [args]     NOC orchestrator (same as meshanchor-noc, e.g. --status)"
    echo "  lora [args]    LoRa configuration helper (same as meshanchor-lora)"
    echo "  status [args]  One-shot terminal status (same as meshanchor-status)"
    echo "  web            Show/open the meshtasticd web client URL (meshanchor-web)"
    echo "  mapserver [op] Map server control: start|stop|restart|status|url|"
    echo "                 enable|disable (same as the meshanchor-map command)"
    echo "  help           Show this help message"
    echo ""
    echo "The TUI uses whiptail/dialog for a raspi-config style"
    echo "interface that works over SSH."
    echo ""
    echo "Examples:"
    echo "  meshanchor                  # Launch TUI menu"
    echo "  meshanchor maps output.html # Generate coverage map"
    echo "  meshanchor prometheus 8080  # Start metrics on port 8080"
}

# `meshanchor-tui` is a symlink to this script (install.sh). launcher.py's
# default is its interface MENU plus NOC service startup — the two things the
# alias exists to skip (MF learned this the hard way, efdc6615). `--tui` is
# launcher.py's own flag for "the TUI, now, no services", so the alias means
# `tui`. Drilled by scripts/guard_drill.py Layer D.
case "$(basename "$0")" in
    meshanchor-tui) set -- tui "$@" ;;
    meshanchor-noc) set -- noc "$@" ;;
    meshanchor-lora) set -- lora "$@" ;;
    meshanchor-status) set -- status "$@" ;;
    meshanchor-web) set -- web "$@" ;;
    meshanchor-map) set -- mapserver "$@" ;;   # the map SERVER, not the coverage map
esac

# Determine which interface to launch
case "$1" in
    tui)
        shift
        launch_tui --tui "$@"
        ;;
    maps|map)
        shift
        launch_maps "$@"
        ;;
    prometheus|metrics)
        shift
        launch_prometheus "$@"
        ;;
    noc)
        shift
        launch_noc "$@"
        ;;
    lora)
        shift
        launch_lora "$@"
        ;;
    status)
        shift
        launch_status "$@"
        ;;
    web)
        shift
        launch_web "$@"
        ;;
    mapserver)
        shift
        launch_mapserver "$@"
        ;;
    help|--help|-h)
        show_help
        ;;
    *)
        launch_tui "$@"
        ;;
esac
