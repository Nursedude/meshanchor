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
    help|--help|-h)
        show_help
        ;;
    *)
        launch_tui "$@"
        ;;
esac
