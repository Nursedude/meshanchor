# MeshForge Gateway Pair Template

Two-node configuration for bridging between different Meshtastic modem presets.

## Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                                                                 │
│   Wide-Area Network          MeshForge           Local Network  │
│   (LongFast)                 Gateway             (Short Turbo)  │
│                                                                 │
│   [Remote Nodes] ─── RF ───► [Node A] ◄──► [Node B] ◄─── RF ─── [Local Nodes]
│                               (USB)           (HAT)             │
│                                  │              │               │
│                                  └──── RNS ─────┘               │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

## Hardware Requirements

| Node | Radio Type | Modem Preset | Purpose |
|------|------------|--------------|---------|
| **Node A** | USB (T-Beam, Heltec) | LONG_FAST | Wide-area reception |
| **Node B** | HAT (Waveshare, RAK) | SHORT_TURBO | Local high-speed |

Both nodes run on the same Raspberry Pi with MeshForge.

## Setup Steps

### 1. Generate Shared PSK

All gateway nodes must share the same 256-bit PSK:

```bash
# Generate a new 256-bit key
meshtastic --ch-set psk random --ch-index 0

# View the key (save this!)
meshtastic --ch-get psk --ch-index 0

# Or use an existing key
meshtastic --ch-set psk base64:YOUR_BASE64_KEY_HERE --ch-index 0
```

### 2. Configure Node A (LongFast)

```bash
# Copy template
sudo cp node-a.yaml /etc/meshtasticd/config.d/gateway-node-a.yaml

# Set the PSK (replace with your key)
meshtastic --host localhost --ch-set psk base64:YOUR_KEY --ch-index 0

# Set channel name
meshtastic --host localhost --ch-set name meshforge --ch-index 0

# Verify
meshtastic --host localhost --info
```

### 3. Configure Node B (Short Turbo)

```bash
# Copy template
sudo cp node-b.yaml /etc/meshtasticd/config.d/gateway-node-b.yaml

# Set the same PSK
meshtastic --host localhost:4404 --ch-set psk base64:YOUR_KEY --ch-index 0

# Set channel name
meshtastic --host localhost:4404 --ch-set name meshforge --ch-index 0

# Verify
meshtastic --host localhost:4404 --info
```

### 4. Configure RNS Bridge

Edit `~/.reticulum/config`:

```ini
[interfaces]

  [[Meshtastic LongFast]]
    type = MeshtasticInterface
    interface_enabled = True
    target_host = 127.0.0.1
    target_port = 4403

  [[Meshtastic Short Turbo]]
    type = MeshtasticInterface
    interface_enabled = True
    target_host = 127.0.0.1
    target_port = 4404
```

### 5. Start Services

```bash
# Start meshtasticd (handles both radios)
sudo systemctl restart meshtasticd

# Start RNS daemon
rnsd

# Verify connectivity
rnstatus
```

## Verification

### Check Node A (LongFast)
```bash
meshtastic --host localhost:4403 --nodes
```

### Check Node B (Short Turbo)
```bash
meshtastic --host localhost:4404 --nodes
```

### Check RNS Bridge
```bash
rnstatus
rnpath <destination_hash>
```

## Troubleshooting

| Symptom | Check | Fix |
|---------|-------|-----|
| No nodes seen | `meshtastic --info` | Verify radio connected, check Region |
| Auth failure | PSK mismatch | Ensure same PSK on all nodes |
| RNS timeout | `rnstatus` | Check interface config, restart rnsd |
| One-way traffic | Modem preset | Both nodes must be on same preset per link |

## Test Procedure

1. **Send from LongFast network** → Should appear on Node A → Bridge to Node B → Appear on Short Turbo network

2. **Send from Short Turbo network** → Should appear on Node B → Bridge to Node A → Appear on LongFast network

3. **Check message_queue.db** for persistence:
   ```bash
   sqlite3 ~/.local/share/meshforge/message_queue.db "SELECT * FROM messages ORDER BY created_at DESC LIMIT 10;"
   ```

## Related Files

- `src/gateway/rns_bridge.py` - Main bridge logic
- `src/gateway/message_queue.py` - SQLite persistence
- `~/.reticulum/config` - RNS interface configuration

---

*Template version: 0.5.0-beta*
*Tested: [date] by [callsign]*
