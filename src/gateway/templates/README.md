# MeshAnchor Gateway Configuration Templates

Pre-configured gateway templates for common bridging scenarios.

## Available Templates

### 1. `meshtastic_rns_bridge.json` - Meshtastic <> RNS Message Bridge

Bridges messages between Meshtastic LoRa mesh and Reticulum (RNS/LXMF) networks.

**Use case**: Connect Meshtastic users to RNS-based applications like NomadNet.

**Requirements**:
- meshtasticd running (default port 4403)
- rnsd running
- Meshtastic radio connected

### 2. (removed) `rns_over_meshtastic.json`

RNS over Meshtastic was removed on 2026-10-01: the transport never delivered packets to RNS. For RNS over LoRa, add an `RNodeInterface` to rnsd's config.

### 3. `meshtastic_preset_bridge.json` - LONG_FAST <> SHORT_TURBO Bridge

Bridges two Meshtastic networks with different LoRa presets.

**Use case**: Connect a wide-coverage rural mesh (LONG_FAST) with a high-speed local mesh (SHORT_TURBO).

**Requirements**:
- Two Meshtastic radios (one per preset)
- Two meshtasticd instances on different ports:
  ```bash
  # Terminal 1 - LONG_FAST radio
  meshtasticd -h localhost -d /dev/ttyUSB0 -p 4403

  # Terminal 2 - SHORT_TURBO radio
  meshtasticd -h localhost -d /dev/ttyUSB1 -p 4404
  ```

## Installation

1. Choose a template and copy it to your config directory:

```bash
cp meshtastic_rns_bridge.json ~/.config/meshanchor/gateway.json
```

2. Edit the configuration:

```bash
nano ~/.config/meshanchor/gateway.json
```

3. Adjust settings for your environment:
   - Host/port for meshtasticd
   - Channel numbers
   - Routing rules
   - Logging preferences

4. Test the configuration:

```bash
# Via MeshAnchor CLI
python3 -m src.commands.gateway test
```

5. Start the gateway:

```bash
# Via MeshAnchor CLI
python3 -m src.commands.gateway start
```

## Configuration Reference

### Bridge Modes

| Mode | Description |
|------|-------------|
| `message_bridge` | Translate messages between RNS and Meshtastic |
| `rns_transport` | Removed 2026-10-01. Not shipped; `bridge_mode: rns_transport` is refused at startup, an old section is ignored and dropped on the next save |
| `mesh_bridge` | Bridge two Meshtastic presets |

### Common Settings

```json
{
  "enabled": true,           // Enable the gateway
  "auto_start": false,       // Start on MeshAnchor launch
  "bridge_mode": "...",      // See modes above
  "log_level": "INFO",       // DEBUG, INFO, WARNING, ERROR
  "log_messages": true       // Log bridged message content
}
```

### Routing Rules (message_bridge mode)

```json
{
  "routing_rules": [
    {
      "name": "rule_name",
      "enabled": true,
      "direction": "bidirectional",  // "mesh_to_rns", "rns_to_mesh"
      "source_filter": "regex",      // Filter by source ID
      "dest_filter": "regex",        // Filter by destination ID
      "message_filter": "regex",     // Filter by message content
      "priority": 10                 // Higher = evaluated first
    }
  ]
}
```

## Monitoring

### Check Status

```bash
# Via journalctl (recommended for RPi)
journalctl -t meshanchor -f | grep gateway

# Via MeshAnchor CLI
python3 -m src.commands.gateway status
```

## Troubleshooting

### Gateway Won't Start

1. Check services:
```bash
systemctl status meshtasticd
systemctl status rnsd
```

2. Check ports:
```bash
ss -tlnp | grep -E "4403|4404"
```

### Messages Not Bridging

1. Check routing direction matches message flow
2. Verify regex patterns in filters
3. Enable DEBUG logging for details

### Preset Bridge Loops

If seeing duplicate messages:
1. Increase `dedup_window_sec` (default 60)
2. Add `exclude_filter` pattern for bridged prefixes

---
*Made with aloha for the mesh community*
