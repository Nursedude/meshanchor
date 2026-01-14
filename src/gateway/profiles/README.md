# MeshForge Gateway Profiles

Pre-configured profiles for optimizing Meshtastic devices as MeshForge gateways.

## Available Profiles

| Profile | Device | TX Power | Features |
|---------|--------|----------|----------|
| `rak4631_gateway` | RAK4631 (nRF52840) | 22dBm | GPS, Low power |
| `heltec_v3_gateway` | Heltec V3 (ESP32-S3) | 20dBm | WiFi |
| `heltec_v4_gateway` | Heltec V4 (ESP32-S3) | 27dBm | WiFi, High power |
| `station_g2_gateway` | Station G2 | 22dBm | Ethernet, PoE |

## Quick Start

### 1. Apply a Profile

```bash
# Via serial port
meshtastic --port /dev/ttyUSB0 --configure rak4631_gateway.yaml

# Via meshtasticd (TCP)
meshtastic --host localhost --configure heltec_v4_gateway.yaml
```

### 2. Via Python

```python
from gateway.profiles import ProfileManager

manager = ProfileManager()

# List profiles
for profile in manager.list_profiles():
    print(f"{profile.name}: {profile.device} ({profile.tx_power}dBm)")

# Apply profile
result = manager.apply_profile('heltec_v4_gateway', port='/dev/ttyUSB0')
print(result['message'])
```

## Gateway Optimizations

All profiles include these gateway optimizations:

### Always Enabled
- **Router role**: Forward messages for other nodes
- **Store & Forward**: Buffer messages for offline nodes
- **Neighbor info**: Track nearby nodes
- **Serial API**: For meshtasticd connection
- **Maximum hop limit**: Wide network coverage

### Always Disabled
- **Bluetooth**: Not needed for headless gateway
- **Display**: Power saving, headless operation
- **Power saving**: Always listening for messages
- **Canned messages**: Not needed for gateway
- **Range test**: Disable unless actively testing

## Customization

### Change LoRa Region

Edit the profile and change the region:

```yaml
lora:
  region: EU_868  # Options: US, EU_868, AU_915, etc.
```

### Set Fixed Position

For fixed gateways, set your coordinates:

```yaml
position:
  fixed_position: true
  fixed_lat: 37.7749    # Your latitude
  fixed_lng: -122.4194  # Your longitude
  fixed_alt: 10         # Altitude in meters
```

### Configure WiFi (ESP32 devices)

```yaml
network:
  wifi_enabled: true
  wifi_ssid: "YourNetworkName"
  wifi_psk: "YourPassword"
```

### Enable MQTT Bridge

```yaml
modules:
  mqtt:
    enabled: true
    address: "mqtt.example.com"
    username: "meshforge"
    password: "secret"
    json_enabled: true
```

## Profile Selection Guide

| Use Case | Recommended Profile |
|----------|-------------------|
| Solar-powered remote node | `rak4631_gateway` |
| WiFi-connected indoor gateway | `heltec_v3_gateway` |
| Maximum range outdoor gateway | `heltec_v4_gateway` |
| Fixed infrastructure/base station | `station_g2_gateway` |

## Troubleshooting

### Profile Won't Apply

1. Check device connection:
   ```bash
   meshtastic --info
   ```

2. Verify serial port:
   ```bash
   ls -la /dev/ttyUSB* /dev/ttyACM*
   ```

3. Check meshtasticd is running:
   ```bash
   systemctl status meshtasticd
   ```

### Device Not Detected

1. Install meshtastic CLI:
   ```bash
   pip install meshtastic
   ```

2. Add user to dialout group:
   ```bash
   sudo usermod -a -G dialout $USER
   # Logout and login again
   ```

### Wrong Region Error

If you see "Invalid region" errors, the device firmware may not support your region. Check supported regions:

```bash
meshtastic --info | grep region
```

## Creating Custom Profiles

Copy an existing profile and modify:

```bash
cp heltec_v4_gateway.yaml my_custom_gateway.yaml
# Edit my_custom_gateway.yaml
meshtastic --configure my_custom_gateway.yaml
```

---

*Made with aloha for the mesh community*
