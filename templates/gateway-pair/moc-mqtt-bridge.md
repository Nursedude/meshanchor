# MeshForge MOC Deployment - MQTT-Bridged Topology

Two-node configuration bridging LongFast and ShortTurbo meshes via MQTT,
with RNS/NomadNet integration on MOC2.

## Architecture

```
  LongFast Mesh                                          ShortTurbo Mesh
  (wide-area)                                            (local high-speed)
       |                                                       |
   RF  |                                                   RF  |
       v                                                       v
 +-----------+          MQTT over LAN           +--------------+
 | MOC1      |  <============================> | MOC2         |
 | Pi5       |                                  | Pi + HAT     |
 |           |                                  |              |
 | Meshtoad  |    mosquitto :1883 (0.0.0.0)     | meshtasticd  |
 | SX1262    |         |                        | SX1262       |
 | LongFast  |         +-- MeshForge MQTT sub   | ShortTurbo   |
 |           |         +-- MQTT monitoring      |              |
 | Web :9443 |                                  | RNS/NomadNet |
 |           |                                  | MeshForge GW |
 +-----------+                                  | Web :9443    |
                                                | meshforge ch |
                                                |   <-> RNS    |
                                                +--------------+
```

## Message Flow

```
1. LongFast node transmits  -->  MOC1 Meshtoad receives via RF
2. meshtasticd on MOC1      -->  MQTT uplink to localhost mosquitto
3. mosquitto on MOC1        -->  MQTT publish to subscribed clients
4. MOC2 meshtasticd         -->  MQTT subscribe from MOC1 broker
5. MOC2 meshtasticd         -->  MQTT downlink to ShortTurbo RF
6. MOC2 MeshForge Gateway   -->  meshforge channel bridged to RNS
7. RNS/NomadNet/LXMF        -->  reachable from ShortTurbo mesh
```

## Hardware

| Node | Hardware | Radio | Preset | Role |
|------|----------|-------|--------|------|
| **MOC1** | Raspberry Pi 5 | Meshtoad (CH341 SPI, SX1262) | LONG_FAST | MQTT Broker |
| **MOC2** | Raspberry Pi | Pi HAT (SPI, SX1262) | SHORT_TURBO | RNS Gateway |

## MOC1 Setup (Broker Node)

### 1. Install meshtasticd

```bash
# Add Meshtastic repo (match your OS version)
# See: session_notes_meshtasticd_install.md for OS-specific repos
sudo apt install meshtasticd
```

### 2. Configure Meshtoad Hardware

```bash
# Copy Meshtoad SPI config
sudo cp templates/meshtoad.yaml /etc/meshtasticd/config.d/

# Ensure CH341 module loads
sudo modprobe ch341
echo "ch341" | sudo tee /etc/modules-load.d/ch341.conf
```

### 3. Install and Configure Mosquitto

```bash
# Install broker
sudo apt install mosquitto mosquitto-clients

# Use MeshForge TUI for guided setup:
sudo python3 src/launcher_tui/main.py
# Navigate: Mesh Networks > MQTT Broker Manager > Setup Private Broker
#   Channel: LongFast
#   Region: US
#   Username: meshforge
#   Password: (auto-generated or custom)

# Or configure manually:
sudo cp examples/configs/broker-private.conf /etc/mosquitto/conf.d/meshforge.conf
sudo mosquitto_passwd -c /etc/mosquitto/meshforge_passwd meshforge
sudo cp examples/configs/broker-private-acl.conf /etc/mosquitto/meshforge_acl

# Enable and start
sudo systemctl enable --now mosquitto
```

### 4. Configure Meshtastic MQTT Uplink

```bash
# Get MOC1's LAN IP (needed for MOC2 to connect)
ip -4 addr show | grep 'inet ' | grep -v 127.0.0.1

# Configure radio MQTT module
meshtastic --host localhost \
  --set mqtt.enabled true \
  --set mqtt.address localhost \
  --set mqtt.username meshforge \
  --set mqtt.password YOUR_PASSWORD \
  --set mqtt.encryption_enabled true \
  --set mqtt.json_enabled true \
  --set mqtt.tls_enabled false

# Enable uplink/downlink on primary channel
meshtastic --host localhost \
  --ch-set uplink_enabled true --ch-index 0 \
  --ch-set downlink_enabled true --ch-index 0
```

### 5. Start Services

```bash
sudo systemctl restart meshtasticd
sudo systemctl restart mosquitto

# Verify
curl -k https://localhost:9443       # Web UI
mosquitto_sub -h localhost -u meshforge -P YOUR_PASSWORD -t "msh/#" -v
```

## MOC2 Setup (RNS Gateway Node)

### 1. Configure MQTT to Point at MOC1

```bash
# Set MOC1's IP as the MQTT broker address
meshtastic --host localhost \
  --set mqtt.enabled true \
  --set mqtt.address MOC1_IP_ADDRESS \
  --set mqtt.username meshforge \
  --set mqtt.password YOUR_PASSWORD \
  --set mqtt.encryption_enabled true \
  --set mqtt.json_enabled true \
  --set mqtt.tls_enabled false

# Enable uplink/downlink
meshtastic --host localhost \
  --ch-set uplink_enabled true --ch-index 0 \
  --ch-set downlink_enabled true --ch-index 0
```

### 2. Configure MeshForge MQTT Subscriber

In MeshForge TUI:
- Navigate: MQTT Broker Manager > Add Custom Broker
- Host: MOC1_IP_ADDRESS
- Port: 1883
- Username: meshforge
- Password: YOUR_PASSWORD
- Channel: LongFast (or meshforge)

### 3. RNS/NomadNet Configuration

MOC2's `~/.reticulum/config` should have the MeshtasticInterface:

```ini
[interfaces]
  [[Meshtastic ShortTurbo]]
    type = MeshtasticInterface
    interface_enabled = True
    target_host = 127.0.0.1
    target_port = 4403
```

### 4. MeshForge Channel to RNS Bridge

Configure in MeshForge TUI:
- Navigate: Mesh Networks > Gateway Configuration
- Bridge Mode: message_bridge
- Meshtastic host: localhost:4403
- RNS: enabled
- Channel: meshforge (or primary)

## Verification

### Test MQTT Flow (MOC1)

```bash
# Subscribe to all mesh topics on MOC1
mosquitto_sub -h localhost -u meshforge -P YOUR_PASSWORD -t "msh/#" -v

# You should see messages from both LongFast (local) and ShortTurbo (MOC2)
```

### Test Cross-Mesh (MOC1 -> MOC2)

1. Send message from LongFast node
2. Verify it appears on MOC1 mosquitto (`mosquitto_sub`)
3. Verify MOC2 meshtasticd receives via MQTT downlink
4. Verify ShortTurbo nodes see the message

### Test RNS Bridge (MOC2)

```bash
# On MOC2
rnstatus                    # Check RNS interfaces
rnpath <destination_hash>   # Check RNS routing
```

## Firewall Notes

MOC1 must allow incoming connections from MOC2:

```bash
# Allow MQTT from LAN
sudo ufw allow 1883/tcp comment "MQTT broker"

# Allow web UI (optional, for remote config)
sudo ufw allow 9443/tcp comment "meshtasticd web UI"
```

## Troubleshooting

| Symptom | Check | Fix |
|---------|-------|-----|
| MOC2 can't connect to MQTT | `mosquitto_sub -h MOC1_IP` | Firewall, bind_address, credentials |
| No messages on MQTT | `meshtastic --info` on MOC1 | Check mqtt.enabled, uplink_enabled |
| One-way traffic only | Check downlink_enabled | Enable on both MOC1 and MOC2 |
| CH341 not detected | `lsmod \| grep ch341` | `sudo modprobe ch341` |
| meshtasticd SIGABRT | `journalctl -u meshtasticd` | Hardware not found, port conflict |
| RNS bridge timeout | `rnstatus` on MOC2 | Check MeshtasticInterface config |

## Security Notes

- Use custom PSK on the meshforge channel (not default AQ==)
- Use authentication on the MQTT broker (never allow_anonymous)
- Private broker does NOT enforce zero-hop (messages re-enter mesh)
- Consider TLS if MOC1 and MOC2 are on different network segments

## Related Files

- `templates/meshtoad.yaml` - Meshtoad hardware config
- `templates/meshforge-presets/moc1-broker.yaml` - MOC1 full config
- `templates/gateway-pair/node-a.yaml` - LongFast template
- `templates/gateway-pair/node-b.yaml` - ShortTurbo template
- `examples/configs/broker-private.conf` - Mosquitto config
- `src/utils/broker_profiles.py` - Broker profile management
- `src/launcher_tui/broker_mixin.py` - TUI broker UI

---
*Template version: 0.5.2-beta*
*Topology: MOC1 (LongFast/Broker) + MOC2 (ShortTurbo/RNS)*
*Author: WH6GXZ / Dude AI*
