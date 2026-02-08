# HamClock Integration - Complete Reference

> Consolidated documentation for MeshForge HamClock integration
> Updated: 2026-02-08

## Status Update (2026-02-08)

**Original HamClock backend sunsets June 2026** (author Elwood Downey WB0OEW is SK).

MeshForge now uses **NOAA SWPC as the primary data source** via `commands.propagation`.
HamClock and OpenHamClock are optional enhancements — see `src/commands/propagation.py`.

**OpenHamClock** (https://github.com/accius/openhamclock) is the community replacement:
- MIT license, React/Node.js, Docker-friendly
- REST API on port 3000
- Adds: PSKReporter MQTT, POTA/SOTA, ionosonde data from prop.kc2g.com
- CelesTrak TLE for satellite tracking

## Overview

HamClock provides space weather, propagation data, and satellite tracking for amateur radio operators. MeshForge integrates with HamClock via its REST API.

### Ports

| Port | Purpose |
|------|---------|
| **8080** (or 8082) | REST API - commands and queries |
| **8081** | Live web view (live.html) |

### URLs

- **Live View**: `http://<pi-ip>:8081/live.html`
- **REST API**: `http://<pi-ip>:8080/` or `http://<pi-ip>:8082/`

---

## Installation

### Option 1: hamclock-web Package (Recommended for Pi)

```bash
# Add pa28 repository
wget -qO- https://pa28.github.io/pa28-pkg/pa28-pkg.gpg.key | sudo apt-key add -
echo "deb https://pa28.github.io/pa28-pkg ./" | sudo tee /etc/apt/sources.list.d/pa28.list
sudo apt update
sudo apt install hamclock-web
sudo systemctl enable --now hamclock
```

### Option 2: Build from Source (arm64)

The pre-built packages require `libbcm_host.so` which is unavailable on 64-bit Pi OS:

```bash
# Install dependencies
sudo apt install -y build-essential libx11-dev

# Download and build web-only version
cd /tmp
wget https://www.clearskyinstitute.com/ham/HamClock/ESPHamClock.zip
unzip ESPHamClock.zip
cd ESPHamClock
make -j4 hamclock-web-1600x960
sudo cp hamclock-web-1600x960 /usr/local/bin/
```

### Systemd Service

```ini
[Unit]
Description=HamClock Web Server
After=network.target

[Service]
Type=simple
ExecStart=/usr/local/bin/hamclock-web-1600x960
Restart=on-failure
User=<your-username>
Environment=HOME=/home/<your-username>

[Install]
WantedBy=multi-user.target
```

**Important:** HamClock needs write access to `~/.hamclock/` for config storage.

---

## REST API Reference

### Query Endpoints (GET)

#### System Information

| Endpoint | Description | Returns |
|----------|-------------|---------|
| `get_sys.txt` | System info and health check | Version, uptime, DE/DX info |
| `get_de.txt` | Home (DE) location | Callsign, grid, lat/lon, time |
| `get_dx.txt` | Target (DX) location | Callsign, grid, lat/lon, time |
| `get_config.txt` | Current configuration | Various settings |

#### Space Weather

| Endpoint | Description | Returns |
|----------|-------------|---------|
| `get_spacewx.txt` | Space weather conditions | SFI, Kp, A-index, X-ray flux |
| `get_voacap.txt` | VOACAP propagation data | Band predictions |
| `get_bc.txt` | Band conditions | Current HF conditions |

#### Satellites & DX

| Endpoint | Description | Returns |
|----------|-------------|---------|
| `get_satellite.txt` | Current satellite info | Name, position, next pass |
| `get_satlist.txt` | Available satellites | List of tracked sats |
| `get_dxspots.txt` | Recent DX spots | Callsign, freq, time |

### Command Endpoints

| Command | Parameters | Description |
|---------|------------|-------------|
| `set_newdx` | `?call=XX0XX` or `?grid=AA00` | Set DX target |
| `set_newde` | `?call=XX0XX` or `?grid=AA00` | Set DE location |
| `set_title` | `?msg=text` | Set title message |
| `set_satname` | `?name=ISS` | Select satellite |

### Response Format

Most endpoints return key=value pairs:

```
Version=4.21
Uptime=12345
DE_call=WH6GXZ
DE_grid=BL10
SFI=156
Kp=2
```

### cURL Examples

```bash
# Get system info
curl http://hamclock.local:8080/get_sys.txt

# Get space weather
curl http://hamclock.local:8080/get_spacewx.txt

# Set DX to grid square
curl 'http://hamclock.local:8080/set_newdx?grid=JO62'

# Get VOACAP propagation
curl http://hamclock.local:8080/get_voacap.txt
```

---

## MeshForge Integration

### Panel Location
`src/gtk_ui/panels/hamclock.py`

### Current Features
- URL/IP configuration for remote HamClock
- API port (8080) and Live port (8081) settings
- REST API fetching (`/get_sys.txt`, `/get_voacap.txt`, etc.)
- Service status checking via systemctl
- WebKit embed (when not running as root)
- Browser fallback for root users
- Auto-refresh capability
- VOACAP propagation display

### Connection Flow

```
1. User enters HamClock URL
2. Click "Connect" → check_connection() hits get_sys.txt
3. On success: fetch space weather data
4. If WebKit available: load live.html in embedded view
5. Auto-refresh updates data periodically
```

### Key Methods

| Method | Purpose |
|--------|---------|
| `_on_connect()` | Save settings, verify connection |
| `_fetch_space_weather()` | Get SFI, Kp, propagation |
| `_on_fetch_voacap()` | Get VOACAP band predictions |
| `_open_url_in_browser()` | Fallback for root/no-WebKit |

---

## Troubleshooting

### Permission denied on ~/.hamclock
```bash
sudo chown -R $USER:$USER ~/.hamclock
chmod -R 755 ~/.hamclock
```

### "basic_string: construction from null" error
Config file corrupted. Remove and let HamClock regenerate:
```bash
rm -f ~/.hamclock/eeprom
```

### libbcm_host.so missing
Use web-only build (see Installation) or:
```bash
sudo apt install libraspberrypi0  # may not be available on arm64
```

### Connection Errors

| Error | Cause | Fix |
|-------|-------|-----|
| `Connection refused` | HamClock not running | Start service |
| `Name not known` | DNS failed | Use IP address |
| `Timeout` | Network/firewall | Check connectivity |

---

## Band Conditions Reference

### Kp Index

| Kp | Condition | HF Impact |
|----|-----------|-----------|
| 0-2 | Quiet | Good propagation |
| 3-4 | Unsettled | Moderate |
| 5-6 | Active | Degraded |
| 7-9 | Storm | Poor/blackout |

SFI (Solar Flux Index) above 100 generally indicates good HF conditions.

---

## OpenHamClock (Community Replacement)

### Overview

OpenHamClock (https://github.com/accius/openhamclock) is the community fork/replacement
for HamClock, designed to survive the June 2026 backend sunset.

- **License**: MIT
- **Stack**: React + Node.js
- **Port**: 3000 (default)
- **Deployment**: Docker recommended

### Docker Installation

```bash
# Clone and run
git clone https://github.com/accius/openhamclock.git
cd openhamclock
docker compose up -d
```

### API Endpoints

| Endpoint | Description |
|----------|-------------|
| `/api/dxcluster/spots` | DX cluster spots (JSON) |
| `/api/spaceweather` | Space weather (proxied from NOAA) |
| `/api/satellites` | Satellite tracking (CelesTrak TLE) |
| `/api/propagation` | ITU-R P.533 predictions |

### MeshForge Integration

```python
from commands import propagation
from commands.propagation import DataSource

# Configure (persists to ~/.config/meshforge/propagation.json)
propagation.configure_source(DataSource.OPENHAMCLOCK, host="localhost", port=3000)

# Check connectivity
result = propagation.check_source(DataSource.OPENHAMCLOCK)

# Get enhanced data (NOAA + OpenHamClock)
result = propagation.get_enhanced_data()
```

### Feature Comparison vs HamClock

| Feature | OpenHamClock | HamClock (legacy) |
|---------|--------------|-------------------|
| DX Spots | Yes (DX Spider) | Yes |
| VOACAP | ITU-R P.533 | Yes |
| Satellites | CelesTrak TLE | Yes |
| PSKReporter | Yes (MQTT) | No |
| POTA/SOTA | Yes | No |
| Ionosonde | Yes (prop.kc2g.com) | No |
| Self-hosted | Docker | Build from source |
| License | MIT | Proprietary |

## References

- [HamClock Official](https://www.clearskyinstitute.com/ham/HamClock/) *(sunsets June 2026)*
- [HamClock User Guide (PDF)](https://www.clearskyinstitute.com/ham/HamClock/HamClockKey.pdf)
- [pa28/hamclock-systemd](https://github.com/pa28/hamclock-systemd)
- [OpenHamClock](https://github.com/accius/openhamclock) *(community replacement)*
- [NOAA SWPC](https://services.swpc.noaa.gov/) *(primary data source)*

---
*Consolidated from hamclock.md, hamclock_api.md, hamclock_integration.md*
*Updated 2026-02-08: Added OpenHamClock, NOAA primary architecture*
