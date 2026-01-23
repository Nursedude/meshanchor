# MeshForge

<p align="center">
  <img src="assets/shaka-simple.svg" alt="Shaka" width="64" height="64"/>
</p>

<p align="center">
  <strong>Mesh Network Operations Center</strong><br>
  <em>Meshtastic + Reticulum + AREDN — One Terminal</em>
</p>

<p align="center">
  <a href="https://github.com/Nursedude/meshforge"><img src="https://img.shields.io/badge/version-0.4.7--beta-blue.svg" alt="Version"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-GPL--3.0-green.svg" alt="License"></a>
  <a href="https://python.org"><img src="https://img.shields.io/badge/python-3.9+-yellow.svg" alt="Python"></a>
</p>

<p align="center">
  <a href="https://nursedude.substack.com">Development Blog</a> |
  <a href="https://github.com/Nursedude/meshforge/issues">Report Issues</a> |
  <a href="#contributing">Contribute</a>
</p>

---

## What Is This

MeshForge is a terminal-native NOC for mesh radio networks. It manages Meshtastic, Reticulum (RNS), and AREDN from a single SSH-accessible interface. Install it on a Raspberry Pi, plug in your radio, and you have a full network operations center.

**First open-source tool to unify Meshtastic, RNS, and AREDN mesh ecosystems.**

```
sudo python3 src/launcher_tui/main.py
```

```
MeshForge v0.4.7-beta

  Status Overview
  Radio (meshtastic CLI)
  Services (start/stop/restart)
  Logs (live follow, errors, analysis)
  Network & Ports
  RNS / Reticulum
  AREDN Mesh
  RF Tools & Calculator
  Configuration
  Hardware Detection
  System Tools
  Web Client URL
```

Every menu item launches real Linux tools — `journalctl`, `systemctl`, `meshtastic`, `rnstatus`, `ss`, `ip` — directly in your terminal. No wrappers. No abstraction layers. Ctrl+C to stop anything.

---

## Capabilities

### Radio Management
- **Install meshtasticd** from official repos (Debian/Ubuntu/Pi OS)
- **Configure LoRa** — region, presets, channels, TX power
- **SPI HAT + USB** — auto-detect and configure hardware
- **Web client** — access meshtasticd web UI at `https://localhost:9443`
- **Direct CLI** — `--info`, `--nodes`, `--sendtext`, `--set-owner`, `--reboot`

### Network Monitoring
- **Live logs** — `journalctl -fu meshtasticd` with Ctrl+C to exit
- **Node tracking** — MQTT subscriber, real-time telemetry
- **Port inspection** — `ss -tlnp`, connection states, listeners
- **Service health** — systemctl status for all mesh services
- **Coverage maps** — SNR-based link quality maps (Folium → browser)

### Multi-Mesh Bridge
- **Meshtastic ↔ RNS** — Gateway bridge on TCP port 4403
- **Reticulum config** — Auto-deploy working template, edit in nano
- **RNS tools** — `rnstatus`, `rnpath`, interface diagnostics
- **AREDN** — Node status, neighbor links, services, network scan

### RF Engineering
- **Link budget** calculator with receiver sensitivity
- **Fresnel zone** analysis and clearance requirements
- **Path loss** modeling (free space, terrain factors)
- **Site planning** — coverage radius, antenna height optimization
- **Space weather** — solar flux, K-index, band conditions (NOAA SWPC)

### System Operations
- **Network diagnostics** — ping, traceroute, DNS, route tables
- **Hardware detection** — SPI/I2C/USB device scanning, `lsusb`
- **Config overlays** — writes to `config.d/` (never overwrites `config.yaml`)
- **AI diagnostics** — offline troubleshooting with local knowledge base

---

## Roadmap: Network Analysis

The goal is Wireshark-grade visibility into mesh traffic:

| Capability | Status | Implementation |
|-----------|--------|----------------|
| **Packet decode** | Planned | Meshtastic protobuf decode, RNS frame analysis |
| **Traffic logging** | Planned | MQTT tap, message flow recording |
| **SDR spectrum** | Planned | RTL-SDR integration, 915MHz band scanning |
| **GPS tracking** | Planned | Node position history, track export (GPX/KML) |
| **Signal analysis** | Planned | SNR/RSSI trending, link quality over time |
| **Multi-hop trace** | Planned | Visualize routing paths across mesh |
| **Anomaly detection** | Planned | Unusual traffic patterns, rogue node alerts |

---

## Hardware

| Component | Recommended | Minimum |
|-----------|-------------|---------|
| **Computer** | Raspberry Pi 4/5 (4GB) | Pi 3B+ or Pi Zero 2W |
| **OS** | Raspberry Pi OS Bookworm (64-bit) | Debian 12+, Ubuntu 22.04+ |
| **Radio (SPI)** | Meshtoad, MeshAdv-Pi-Hat | Any SX1262/SX1276 SPI HAT |
| **Radio (USB)** | Heltec V3, T-Beam, RAK4631 | Any Meshtastic USB device |
| **SDR (optional)** | RTL-SDR Blog V4 | Any RTL2832U dongle |

**Minimal node**: Pi 4 ($55) + SPI HAT ($35) = **$90**

---

## Install

```bash
git clone https://github.com/Nursedude/meshforge.git
cd meshforge

# Full install (meshtasticd + MeshForge + dependencies)
sudo bash scripts/install_noc.sh

# Or launch directly (if meshtasticd already installed)
sudo python3 src/launcher_tui/main.py

# RF tools only (no sudo, no radio needed)
python3 src/standalone.py
```

### Port Map

| Port | Service | Protocol |
|------|---------|----------|
| **4403** | meshtasticd TCP API | Protobuf (RNS bridge connects here) |
| **9443** | meshtasticd Web UI | HTTPS (browser access) |

---

## Architecture

```
+---------------------------------------------+
|              MeshForge TUI                   |
|   Terminal-native NOC dispatcher             |
|   (whiptail menus → real CLI tools)          |
+-----+-----+-----+-----+-----+-----+--------+
      |     |     |     |     |     |
      v     v     v     v     v     v
  systemctl  meshtastic  journalctl  rnstatus  ss/ip  nano
      |          |           |          |        |      |
+-----+----+ +--+---+ +----+---+ +----+---+ +--+--+ +-+-+
|meshtasticd| | Radio| |  Logs  | |  rnsd  | | Net | |Cfg|
|  (LoRa)   | | HAT  | |syslog  | |  RNS   | |stack| |.d/|
+-----------+ +------+ +--------+ +--------+ +-----+ +---+
```

**Design principles:**
- TUI is a **dispatcher** — it selects what to run, not how to run it
- All output goes to your terminal directly (no dialog boxes for data)
- Every operation uses standard Linux tools (portable, debuggable)
- Config writes go to `/etc/meshtasticd/config.d/` overlays (safe)
- Services run independently — MeshForge connects, never embeds

---

## Configuration

### meshtasticd

MeshForge writes config overlays to `/etc/meshtasticd/config.d/`:

```
/etc/meshtasticd/
├── config.yaml                    # Package default (DO NOT EDIT)
└── config.d/
    ├── meshforge-lora-preset.yaml # LoRa region/preset
    ├── meshforge-radio.yaml       # Radio hardware (SPI/USB)
    ├── meshforge-channels.yaml    # Channel configuration
    └── meshforge-overrides.yaml   # Custom overrides
```

### Reticulum

The TUI auto-deploys a working RNS config from `templates/reticulum.conf`:
- AutoInterface (LAN discovery)
- Meshtastic Interface on `127.0.0.1:4403`
- RNode LoRa (optional, uncomment for dedicated RNS radio)

```bash
# RNS menu → Edit Reticulum Config
# Or manually:
cp templates/reticulum.conf ~/.reticulum/config
sudo systemctl restart rnsd
```

---

## Project Structure

```
src/
├── launcher_tui/          # Terminal UI (primary interface)
│   ├── main.py            # NOC dispatcher + menus
│   ├── backend.py         # whiptail/dialog abstraction
│   └── *_mixin.py         # Feature modules (RF, channels, system)
├── gateway/               # RNS-Meshtastic bridge
│   ├── rns_bridge.py      # Gateway transport
│   └── message_queue.py   # Persistent queue (SQLite)
├── monitoring/            # Network monitoring
│   └── mqtt_subscriber.py # Nodeless MQTT node tracking
├── utils/                 # Core utilities
│   ├── rf.py              # RF calculations (tested)
│   ├── aredn.py           # AREDN mesh client
│   ├── coverage_map.py    # Folium map generator
│   ├── diagnostic_engine.py # AI diagnostics
│   └── paths.py           # Sudo-safe path resolution
├── config/                # Config management
│   ├── yaml_editor.py     # Safe overlay writer
│   ├── radio.py           # Radio hardware config
│   └── channel_presets.py # Channel preset manager
├── standalone.py          # Zero-dependency RF tools
└── __version__.py         # Version tracking
templates/
└── reticulum.conf         # Working RNS config template
```

---

## Contributing

```bash
# Run tests
python3 -m pytest tests/ -v

# Run linter
python3 scripts/lint.py --all

# Syntax check
python3 -m py_compile src/launcher_tui/main.py
```

**Code rules:**
- `get_real_user_home()` not `Path.home()` — works under sudo (MF001)
- No `shell=True` in subprocess — no command injection (MF002)
- Explicit exception types — no bare `except:` (MF003)
- Timeouts on all subprocess calls (MF004)

See [CLAUDE.md](CLAUDE.md) for complete development guide.

---

## Resources

| Resource | Link |
|----------|------|
| Meshtastic Docs | [meshtastic.org/docs](https://meshtastic.org/docs/) |
| meshtasticd Install | [Linux Installation](https://meshtastic.org/docs/software/linux/installation/) |
| Reticulum Network | [reticulum.network](https://reticulum.network/) |
| AREDN Mesh | [arednmesh.org](https://www.arednmesh.org/) |
| MeshCore | [meshcore.co](https://meshcore.co/) |
| RTL-SDR | [rtl-sdr.com](https://www.rtl-sdr.com/) |

---

## License

GPL-3.0 — See [LICENSE](LICENSE)

---

<p align="center">
  <img src="assets/shaka-simple.svg" alt="Shaka" width="32" height="32"/><br>
  <strong>MeshForge</strong><br>
  <em>Made with aloha for the mesh community</em><br>
  WH6GXZ | Hawaii
</p>
