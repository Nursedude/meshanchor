# MeshForge MQTT Integration Guide

> **Version:** 1.0.0
> **Last Updated:** 2026-02-04
> **For:** meshing_around_meshforge repository
> **Author:** WH6GXZ (Nursedude)

---

## Overview

This document describes MQTT integration between **meshing_around_meshforge** and **MeshForge NOC**, running on a private encrypted channel.

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                    MeshForge Ecosystem                          │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ┌──────────────────┐           ┌──────────────────┐           │
│  │  MeshForge NOC   │           │ meshing_around   │           │
│  │  (Main App)      │◄─────────►│ _meshforge       │           │
│  │                  │   MQTT    │                  │           │
│  │  - Gateway       │   Sync    │  - MQTT Client   │           │
│  │  - Node Tracker  │           │  - Alert System  │           │
│  │  - TUI Interface │           │  - TUI/Web UI    │           │
│  └──────────────────┘           └──────────────────┘           │
│           │                              │                      │
│           ▼                              ▼                      │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │              Private MQTT Channel                        │   │
│  │         Channel: "meshforge" (256-bit PSK)              │   │
│  │         Topic: msh/{region}/meshforge/#                 │   │
│  └─────────────────────────────────────────────────────────┘   │
│                              │                                  │
│                              ▼                                  │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │              Meshtastic Mesh Network                     │   │
│  │              (LoRa Radio Infrastructure)                 │   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## Private Channel Configuration

### Channel Specifications

| Setting | Value |
|---------|-------|
| Channel Name | `meshforge` |
| Channel Index | User-configurable (slot 1-7) |
| Encryption | AES-256-CTR |
| PSK Length | 256-bit (32 bytes) |
| MQTT Uplink | Enabled |
| MQTT Downlink | Enabled |

### Generating a 256-bit PSK

```bash
# Generate cryptographically secure 256-bit key
python3 -c "import secrets; import base64; print(base64.b64encode(secrets.token_bytes(32)).decode())"
```

### Meshtastic Device Configuration

```bash
# Configure meshforge channel (adjust --ch-index as needed)
meshtastic --ch-index 1 --ch-set name meshforge
meshtastic --ch-index 1 --ch-set psk base64:YOUR_256BIT_KEY_HERE
meshtastic --ch-index 1 --ch-set uplink_enabled true
meshtastic --ch-index 1 --ch-set downlink_enabled true
```

See [Meshtastic Channel Documentation](https://meshtastic.org/docs/configuration/radio/channels/) for details.

---

## MQTT Configuration

### User-Configurable Settings

All MQTT settings are user-configurable. No hardcoded values.

### Configuration File Locations

```
~/.config/meshing-around-clients/config.ini    # User (recommended)
./mesh_client.ini                               # Local
/etc/meshing-around-clients/config.ini          # System-wide
```

### MQTT Section

```ini
[mqtt]
# Broker - MUST be configured by user
# Public: mqtt.meshtastic.org
# Private: mqtt.meshforge-hi.local (Hawaii example)
broker = mqtt.meshtastic.org
port = 1883
use_tls = false

# Authentication
username = meshdev
password = large4cats

# Topic configuration
# Regions: US, EU_868, EU_433, AU_915, CN, JP, KR, TW, etc.
topic_root = msh/US

# Channel name - MUST match Meshtastic channel exactly
channel = meshforge

# 256-bit PSK (base64 encoded)
# Generate: python3 -c "import secrets,base64; print(base64.b64encode(secrets.token_bytes(32)).decode())"
encryption_key = YOUR_256BIT_PSK_BASE64

# Virtual node ID (format: !hex8chars)
node_id = !meshforg

# Connection settings
qos = 1
reconnect_delay = 5
max_reconnect_attempts = 10
```

### Environment Variable Overrides

```bash
export MESHFORGE_MQTT_PASSWORD="your_password"
export MESHFORGE_MQTT_PSK="your_256bit_key_base64"
```

---

## Hawaii Template (WH6GXZ)

Reference configuration for Hawaii deployment. **Customize for your region.**

```ini
# Hawaii Region - WH6GXZ
# Private broker with public fallback

[mqtt]
# Primary: Local private broker
broker = mqtt.meshforge-hi.local
port = 1883
use_tls = false

# Fallback to public if private unavailable
# broker = mqtt.meshtastic.org

# Hawaii-specific
topic_root = msh/US
channel = meshforge
node_id = !wh6gxzmf

# Auth (configure for your broker)
username =
password =

# PSK - shared with Hawaii mesh group
# encryption_key = <shared-psk>

qos = 1
reconnect_delay = 5
max_reconnect_attempts = 10

[general]
bot_name = MeshForge-HI
admin_nodes = !wh6gxz01,!wh6gxz02

[alerts]
# Oahu coordinates
proximity_enabled = true
proximity_lat = 21.4389
proximity_lon = -158.0001
proximity_radius_km = 50

# Hawaii weather zones
weather_enabled = true
weather_zones = HIZ001,HIZ002,HIZ003

# Volcano monitoring (Hawaii-specific)
volcano_enabled = true
volcano_codes = HVO

[interface]
type = mqtt
```

---

## Python Integration

```python
from meshing_around_clients.core.mqtt_client import MQTTClient, MQTTConfig

# Configure
config = MQTTConfig(
    broker="mqtt.meshforge-hi.local",
    port=1883,
    topic_root="msh/US",
    channel="meshforge",
    encryption_key="YOUR_256BIT_PSK_BASE64",
    node_id="!meshforg"
)

# Connect
client = MQTTClient(config)
client.on_message = lambda msg: print(f"Received: {msg}")
client.on_node_update = lambda node: print(f"Node: {node.short_name}")
client.connect()

# Send
client.send_text("Hello from MeshForge!", channel="meshforge")
```

---

## Deployment Modes

### Mode 1: MQTT Only (Nodeless)

```ini
[interface]
type = mqtt

[mqtt]
broker = mqtt.meshforge-hi.local
channel = meshforge
```

### Mode 2: Hybrid (Local Node + MQTT)

```ini
[interface]
type = serial
port = /dev/ttyUSB0

[mqtt]
enabled = true
channel = meshforge
```

---

## Security

### PSK Management

- Never commit PSKs to version control
- Use environment variables for sensitive data
- Rotate keys periodically (quarterly recommended)
- Unique PSKs per deployment/region

### TLS (Production)

```ini
[mqtt]
use_tls = true
port = 8883
```

---

## Troubleshooting

### Test MQTT Connectivity

```bash
mosquitto_sub -h mqtt.meshtastic.org -t "msh/US/meshforge/#" -u meshdev -P large4cats -v
```

### Verify PSK Format

```bash
python3 -c "import base64; k=base64.b64decode('YOUR_KEY'); print(f'Key length: {len(k)} bytes')"
# Should output: Key length: 32 bytes
```

### Common Issues

| Issue | Cause | Fix |
|-------|-------|-----|
| No messages | Channel mismatch | Verify channel name matches exactly |
| Decryption failed | PSK mismatch | Ensure all nodes share same PSK |
| Connection refused | Broker down | Check broker status, try fallback |

---

## Support

- **GitHub:** [Nursedude/meshing_around_meshforge](https://github.com/Nursedude/meshing_around_meshforge)
- **MeshForge NOC:** [Nursedude/meshforge](https://github.com/Nursedude/meshforge)
- **Callsign:** WH6GXZ

---

*Made with aloha for the mesh community* - 73 de WH6GXZ
