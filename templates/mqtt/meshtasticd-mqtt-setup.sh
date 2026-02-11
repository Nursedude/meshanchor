#!/bin/bash
# MeshForge - Configure meshtasticd MQTT
# =======================================
#
# Two modes:
#   --monitor    Monitor only (uplink, no downlink). Read-only, safe.
#   --bridge     Bidirectional bridge (uplink + downlink). For gateway.
#
# Prerequisites:
#   - meshtasticd running and accessible
#   - mosquitto installed and running (sudo apt install mosquitto)
#   - meshtastic CLI installed (pip install meshtastic)
#
# Usage:
#   ./meshtasticd-mqtt-setup.sh --monitor          # Safe, read-only
#   ./meshtasticd-mqtt-setup.sh --bridge            # Bidirectional gateway
#   ./meshtasticd-mqtt-setup.sh --bridge myhost     # Custom host
#
# After running, verify with:
#   mosquitto_sub -h localhost -t 'msh/#' -v

set -e

# Parse args
MODE="monitor"
HOST="localhost"

for arg in "$@"; do
    case "$arg" in
        --monitor) MODE="monitor" ;;
        --bridge)  MODE="bridge" ;;
        --help|-h)
            echo "Usage: $0 [--monitor|--bridge] [host]"
            echo ""
            echo "  --monitor  Uplink only (mesh -> MQTT). Read-only, safe."
            echo "  --bridge   Uplink + downlink (bidirectional). For gateway."
            echo "  host       meshtasticd host (default: localhost)"
            exit 0
            ;;
        *)
            # Treat as host if not a flag
            if [[ ! "$arg" == --* ]]; then
                HOST="$arg"
            fi
            ;;
    esac
done

echo "Mode: ${MODE}"
echo "Host: ${HOST}"
echo ""

# Check prerequisites
if ! command -v meshtastic &>/dev/null; then
    echo "ERROR: meshtastic CLI not found. Install with: pip install meshtastic"
    exit 1
fi

if ! systemctl is-active --quiet mosquitto 2>/dev/null; then
    echo "WARNING: mosquitto not running. Install with: sudo apt install mosquitto"
    echo "         Then: sudo systemctl start mosquitto"
fi

echo "Step 1: Enable MQTT module"
meshtastic --host "${HOST}" --set mqtt.enabled true

echo ""
echo "Step 2: Set MQTT broker to localhost"
meshtastic --host "${HOST}" --set mqtt.address 127.0.0.1

echo ""
echo "Step 3: Enable JSON output (human-readable, recommended)"
meshtastic --host "${HOST}" --set mqtt.json_enabled true

echo ""
echo "Step 4: Disable encryption to MQTT (local broker, not needed)"
meshtastic --host "${HOST}" --set mqtt.encryption_enabled false

echo ""
echo "Step 5: Enable uplink on primary channel (mesh -> MQTT)"
meshtastic --host "${HOST}" --ch-index 0 --ch-set uplink_enabled true

if [ "$MODE" = "bridge" ]; then
    echo ""
    echo "Step 6: Enable downlink on primary channel (MQTT -> mesh)"
    meshtastic --host "${HOST}" --ch-index 0 --ch-set downlink_enabled true
else
    echo ""
    echo "Step 6: Disable downlink (monitor-only, no injection into mesh)"
    meshtastic --host "${HOST}" --ch-index 0 --ch-set downlink_enabled false
fi

echo ""
echo "Done! Mode: ${MODE}"
echo ""
if [ "$MODE" = "bridge" ]; then
    echo "  Bidirectional: mesh traffic published to MQTT,"
    echo "  MQTT messages injected into mesh via downlink."
else
    echo "  Monitor only: mesh traffic published to MQTT."
    echo "  No messages will be injected into the mesh."
fi
echo ""
echo "Verify with:"
echo "  mosquitto_sub -h localhost -t 'msh/#' -v"
echo ""
echo "Web client still works at: https://${HOST}:9443"
