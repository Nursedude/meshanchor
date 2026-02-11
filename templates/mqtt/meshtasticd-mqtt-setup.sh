#!/bin/bash
# MeshForge - Configure meshtasticd MQTT for gateway bridge
# =========================================================
#
# This script configures meshtasticd to publish mesh traffic to a local
# MQTT broker. MeshForge's gateway subscribes to MQTT instead of using
# the TCP connection, leaving the web client unaffected.
#
# Prerequisites:
#   - meshtasticd running and accessible
#   - mosquitto installed and running (sudo apt install mosquitto)
#   - meshtastic CLI installed (pip install meshtastic)
#
# Usage:
#   chmod +x meshtasticd-mqtt-setup.sh
#   ./meshtasticd-mqtt-setup.sh
#
# After running, verify with:
#   mosquitto_sub -h localhost -t 'msh/#' -v

set -e

HOST="${1:-localhost}"
echo "Configuring meshtasticd MQTT on host: ${HOST}"

# Check prerequisites
if ! command -v meshtastic &>/dev/null; then
    echo "ERROR: meshtastic CLI not found. Install with: pip install meshtastic"
    exit 1
fi

if ! systemctl is-active --quiet mosquitto 2>/dev/null; then
    echo "WARNING: mosquitto not running. Install with: sudo apt install mosquitto"
    echo "         Then: sudo systemctl start mosquitto"
fi

echo ""
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

echo ""
echo "Step 6: Enable downlink on primary channel (MQTT -> mesh)"
meshtastic --host "${HOST}" --ch-index 0 --ch-set downlink_enabled true

echo ""
echo "Done! meshtasticd will now publish mesh traffic to MQTT."
echo ""
echo "Verify with:"
echo "  mosquitto_sub -h localhost -t 'msh/#' -v"
echo ""
echo "Web client still works at: https://${HOST}:9443"
