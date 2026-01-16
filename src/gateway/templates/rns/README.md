# RNS Configuration Templates

User-editable templates for Reticulum Network Stack configuration.

## Quick Start

1. Choose a template based on your role:
   - **Server**: `hawaiinet_server.conf` - Hosts the network
   - **Client**: `hawaiinet_client.conf` - Connects to a server

2. Copy to your Reticulum config directory:
   ```bash
   cp hawaiinet_server.conf ~/.reticulum/config
   ```

3. Edit with your settings:
   ```bash
   nano ~/.reticulum/config
   ```

4. Start Reticulum:
   ```bash
   rnsd
   ```

## Templates

| Template | Use Case |
|----------|----------|
| `hawaiinet_server.conf` | Primary gateway with TCP server + RNode |
| `hawaiinet_client.conf` | Node connecting to HawaiiNet server |
| `basic_rnode.conf` | Simple RNode-only setup |
| `meshtastic_bridge.conf` | Bridge between RNS and Meshtastic |

## Interface Types

### TCPServerInterface
Allows other nodes to connect to this machine over TCP/IP.
```
[[My Server]]
    type = TCPServerInterface
    enabled = yes
    listen_ip = 0.0.0.0
    listen_port = 4242
```

### TCPClientInterface
Connects to a remote TCPServerInterface.
```
[[Remote Server]]
    type = TCPClientInterface
    enabled = yes
    target_host = 192.168.1.100
    target_port = 4242
```

### RNodeInterface
Connects to an RNode device for LoRa communication.
```
[[My RNode]]
    type = RNodeInterface
    interface_enabled = True
    port = /dev/ttyACM0
    frequency = 903625000
    txpower = 22
    bandwidth = 250000
    spreadingfactor = 7
    codingrate = 5
```

## US Frequency Slots (902-928 MHz)

| Slot | Frequency (MHz) | Notes |
|------|-----------------|-------|
| 0 | 903.875 | Default |
| 1 | 903.875 | Same as 0 |
| 2 | 906.125 | |
| 3 | 908.375 | |
| 4 | 910.625 | |
| 5 | 912.875 | |
| 6 | 915.125 | |
| 7 | 917.375 | |
| 8 | 919.625 | |
| 9 | 921.875 | |
| 10 | 924.125 | |
| 11 | 926.375 | |
| 12 | 903.625 | HawaiiNet |

## Modulation Presets

| Preset | SF | BW (kHz) | CR | Use Case |
|--------|----|---------|----|----------|
| LONG_FAST | 7 | 250 | 4/5 | Best range/speed balance |
| LONG_MODERATE | 8 | 125 | 4/5 | Better range, slower |
| LONG_SLOW | 11 | 125 | 4/8 | Maximum range |
| MEDIUM_FAST | 7 | 500 | 4/5 | Higher speed, less range |
| SHORT_TURBO | 6 | 500 | 4/5 | Maximum speed |

## Troubleshooting

### RNode not detected
```bash
# Check USB connection
ls -la /dev/ttyACM* /dev/ttyUSB*

# Check permissions
sudo usermod -a -G dialout $USER
# Then logout and login
```

### Can't connect to server
```bash
# Test TCP connection
nc -zv 192.168.86.38 4242

# Check firewall
sudo ufw allow 4242/tcp
```

### No LoRa traffic
- Verify both nodes use same frequency
- Check antenna connections
- Reduce TX power if very close (< 10m)

## See Also

- [Reticulum Manual](https://markqvist.github.io/Reticulum/manual/)
- [RNode Documentation](https://unsigned.io/rnode/)
- [MeshForge Gateway Guide](https://github.com/Nursedude/meshforge)
