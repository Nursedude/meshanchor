# MeshForge

<p align="center">
  <img src="assets/shaka-simple.svg" alt="Shaka" width="64" height="64"/>
</p>

<p align="center">
  <strong>Network Operations Center for Mesh Networks</strong>
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

## What MeshForge Does

MeshForge manages Meshtastic mesh networks from a Raspberry Pi. It installs meshtasticd, configures your radio, monitors your nodes, and provides RF planning tools — all from a terminal interface that works over SSH.

The long-term goal is bridging Meshtastic and Reticulum (RNS) networks. That bridge is in development. Today, MeshForge is a capable NOC for Meshtastic.

## What Works Today (v0.4.7-beta)

| Feature | Status | Notes |
|---------|--------|-------|
| **meshtasticd Install** | Working | Automated install from [official repos](https://meshtastic.org/docs/software/linux/installation/) |
| **SPI HAT + USB Support** | Working | Configures hardware, enables SPI/I2C |
| **RF Calculator** | Working | Link budgets, Fresnel zones, path loss, site planning |
| **Coverage Maps** | Working | Generate SNR-based link quality maps (Folium) |
| **AI Diagnostics** | Working | Offline troubleshooting with knowledge base |
| **Node Monitoring** | Working | MQTT subscriber, real-time node tracking |
| **Service Management** | Working | Start/stop meshtasticd, check status, view logs |
| **Terminal UI** | Working | raspi-config style — SSH, serial, local |
| **GTK Desktop** | Working | Full graphical interface with maps and panels |

| Feature | Status | Notes |
|---------|--------|-------|
| **RNS Bridge** | In Progress | Transport layer code exists, needs end-to-end testing |
| **Gateway Routing** | In Progress | Meshtastic-to-RNS message passing, architecture done |

**"Working"** = You can install and use it without manual intervention.
**"In Progress"** = Code exists but isn't validated end-to-end yet.

## Hardware Requirements

| Component | Recommended | Minimum |
|-----------|-------------|---------|
| **Computer** | Raspberry Pi 4/5 (4GB) | Pi 3B+ or Pi Zero 2W (512MB) |
| **OS** | Raspberry Pi OS Bookworm (64-bit) | Debian 12+, Ubuntu 22.04+ |
| **Radio (SPI)** | Meshtoad, MeshAdv-Pi-Hat | Any SX1262/SX1276 SPI HAT |
| **Radio (USB)** | Heltec V3, T-Beam, RAK4631 | Any Meshtastic-compatible USB device |
| **Internet** | Required for install only | Not needed for mesh operation |

**Cost estimate**: Pi 4 (~$55) + SPI HAT (~$35) = ~$90 for a complete node.

## Quick Start

```bash
# Clone
git clone https://github.com/Nursedude/meshforge.git
cd meshforge

# Install meshtasticd + MeshForge (Pi/Debian)
sudo bash scripts/install_noc.sh

# Or launch directly (if meshtasticd already installed)
sudo python3 src/launcher_tui/main.py

# RF tools only (no sudo, no radio needed)
python3 src/standalone.py
```

The TUI will show a menu like:
```
MeshForge v0.4.7-beta - Select an option:

  GTK4 Desktop Interface
  AI Tools
  System Diagnostics
  Network Tools
  RF Tools
  Site Planner
  Start Gateway Bridge
  Node Monitor
  Meshtasticd Config
  Service Management
  Hardware Detection
  Settings
  About MeshForge
  Exit
```

## meshtasticd Installation

MeshForge installs meshtasticd from the [official Meshtastic repositories](https://meshtastic.org/docs/software/linux/installation/):

| Platform | Method |
|----------|--------|
| **Pi OS 64-bit / Debian 12+** | OpenSUSE Build Service (apt) |
| **Pi OS 32-bit (armhf)** | Raspbian OBS repository |
| **Ubuntu 22.04+** | Launchpad PPA (`ppa:meshtastic/beta`) |

Config file: `/etc/meshtasticd/config.yaml`

## Architecture

MeshForge manages the service stack and provides user interfaces:

```
+------------------+     +------------------+
|   GTK Desktop    |     |   Terminal UI    |
|   (panels/maps)  |     |  (raspi-config)  |
+--------+---------+     +--------+---------+
         |                         |
         +------------+------------+
                      |
         +------------+------------+
         |     MeshForge Core      |
         |  Service management     |
         |  RF tools, diagnostics  |
         |  Coverage mapping       |
         +------------+------------+
                      |
         +------------+------------+
         |                         |
+--------+---------+  +-----------+---------+
|   meshtasticd    |  |       rnsd          |
|   (LoRa radio)   |  |  (Reticulum stack)  |
+------------------+  +---------------------+
```

**Two interfaces:**
- **GTK4 Desktop** — Maps, charts, panels. Requires display.
- **Terminal UI** — whiptail/dialog menus. Works over SSH, serial, anywhere.

## Interface Options

| Interface | Command | Use Case |
|-----------|---------|----------|
| **Auto-detect** | `sudo python3 src/launcher.py` | Display? GTK. No display? TUI. |
| **Terminal UI** | `sudo python3 src/launcher_tui/main.py` | SSH, headless, serial |
| **GTK Desktop** | `sudo python3 src/main_gtk.py` | Full graphical |
| **Standalone** | `python3 src/standalone.py` | Zero-dep RF tools |

## Who Is This For?

| User | Use Case |
|------|----------|
| **HAM Operators** | Managing Meshtastic nodes, RF planning |
| **Emergency Comms** | ARES/RACES mesh network deployment |
| **Off-Grid Communities** | Running mesh networks without internet |
| **Tinkerers** | Learning LoRa, building mesh coverage |

## What's Next

1. **Gateway bridge validation** — End-to-end Meshtastic-to-RNS message passing
2. **Install reliability** — Verify first-run works on fresh Pi
3. **MeshCore research** — Emerging Reticulum-based protocol

## Contributing

```bash
# Run tests
python3 -m pytest tests/ -v

# Run linter
python3 scripts/lint.py --all
```

**Key rules:**
- `get_real_user_home()` not `Path.home()` (MF001)
- No `shell=True` in subprocess (MF002)
- Explicit exception types (MF003)
- Timeouts on subprocess calls (MF004)

See [CLAUDE.md](CLAUDE.md) for complete patterns.

**Get Involved:**
- [Development Blog](https://nursedude.substack.com)
- [Issues](https://github.com/Nursedude/meshforge/issues)
- [Discussions](https://github.com/Nursedude/meshforge/discussions)

## Resources

| Resource | Link |
|----------|------|
| Meshtastic | [meshtastic.org](https://meshtastic.org/docs/) |
| meshtasticd Install | [Linux Installation](https://meshtastic.org/docs/software/linux/installation/) |
| Reticulum | [reticulum.network](https://reticulum.network/) |
| MeshCore | [meshcore.co](https://meshcore.co/) |
| AREDN | [arednmesh.org](https://www.arednmesh.org/) |

## License

GPL-3.0 - See [LICENSE](LICENSE)

---

<p align="center">
  <img src="assets/shaka-simple.svg" alt="Shaka" width="32" height="32"/><br>
  <strong>MeshForge</strong><br>
  <em>Made with aloha for the mesh community</em><br>
  WH6GXZ | Hawaii
</p>
