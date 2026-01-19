# MeshForge

<p align="center">
  <img src="assets/shaka-simple.svg" alt="Shaka" width="64" height="64"/>
</p>

<p align="center">
  <strong>The first Network Operations Center bridging Meshtastic and Reticulum mesh networks.</strong>
</p>

<p align="center">
  <a href="https://github.com/Nursedude/meshforge"><img src="https://img.shields.io/badge/version-0.4.7--beta-blue.svg" alt="Version"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-GPL--3.0-green.svg" alt="License"></a>
  <a href="https://python.org"><img src="https://img.shields.io/badge/python-3.9+-yellow.svg" alt="Python"></a>
  <a href="tests/"><img src="https://img.shields.io/badge/tests-1297%20passing-brightgreen.svg" alt="Tests"></a>
</p>

---

## The Problem

**Meshtastic and Reticulum can't talk to each other.**

Both are excellent LoRa mesh networks, but they operate in complete isolation:
- Different protocols, different node databases, different ecosystems
- No way to route messages between them
- Managing both requires separate tools

```
Meshtastic (LoRa)  ---X---  Reticulum (RNS)
     BLOCKED - incompatible protocols
```

## The Solution

**MeshForge bridges them.**

```
Meshtastic  <------>  MeshForge Gateway  <------>  Reticulum
   (LoRa)                   |                       (RNS)
                            v
                    Unified Node View
                    + AI Diagnostics
```

---

## Quick Start

**NOC Stack Install (Recommended):**
```bash
git clone https://github.com/Nursedude/meshforge.git
cd meshforge
sudo bash scripts/install_noc.sh
```

This installs the complete stack:
- **meshtasticd** (native binary for SPI radios, or Python CLI for USB)
- **Reticulum (rnsd)** for RNS mesh support
- **MeshForge** orchestrator and interfaces

**Or minimal install:**
```bash
pip3 install rich textual flask --break-system-packages
sudo python3 src/launcher.py
```

The launcher auto-detects your environment and picks the best interface.

---

## Who Is This For?

- **HAM radio operators** building resilient off-grid networks
- **Emergency communications teams** needing mesh interoperability
- **Off-grid communities** connecting disparate mesh systems
- **Mesh networking enthusiasts** exploring protocol bridging

---

## Features

| Feature | Standalone | PRO |
|---------|:----------:|:---:|
| Gateway bridge (Meshtastic ↔ RNS) | ✓ | ✓ |
| Unified node tracking | ✓ | ✓ |
| NOC orchestrator (service management) | ✓ | ✓ |
| Native meshtasticd support (SPI HATs) | ✓ | ✓ |
| Nodeless MQTT monitoring | ✓ | ✓ |
| Coverage maps with SNR-based link quality | ✓ | ✓ |
| RF calculations (FSPL, Fresnel, link budget) | ✓ | ✓ |
| LoRa configuration wizard | ✓ | ✓ |
| AI diagnostics | Rule-based | Claude-powered |
| Knowledge base | ✓ | ✓ |
| Natural language queries | — | ✓ |

**PRO mode** requires an Anthropic API key (`ANTHROPIC_API_KEY` env var).

---

## NOC Stack

MeshForge is designed to BE your Meshtastic node - owning the full stack:

```
┌─────────────────────────────────────────────┐
│             MeshForge NOC                   │
│  ┌─────────┐  ┌─────────┐  ┌─────────────┐ │
│  │meshtasticd│  │  rnsd   │  │ Orchestrator│ │
│  │ (radio)  │  │ (RNS)   │  │  (manager)  │ │
│  └────┬─────┘  └────┬────┘  └──────┬──────┘ │
│       │             │              │        │
│       └─────────────┴──────────────┘        │
│                     │                       │
│         Health Monitoring + Auto-restart    │
└─────────────────────────────────────────────┘
```

**Supported Radio Hardware:**
- **SPI HATs**: Meshtoad, MeshAdv-Pi-Hat, RAK WisLink, Waveshare SX126x
- **USB Radios**: T-Beam, Heltec V3/V4, RAK4631, MeshStick

**Commands:**
```bash
sudo meshforge              # Launch interface wizard
sudo meshforge-noc --start  # Start NOC services
sudo meshforge-noc --status # Check service status
sudo meshforge-lora --interactive  # Configure LoRa settings
```

---

## Reliability (v0.4.7)

MeshForge is built for dependable mesh network operations:

**Single Source of Truth** - All service status checks (GTK, TUI, CLI) use one canonical implementation. No more conflicting status displays.

**Quality Gates** - Pre-commit hooks run security linting, critical tests, and type checking before every commit.

**Test Coverage** - 1297 tests including regression tests that prevent status drift across UIs.

**API Contracts** - Core functions document their guarantees, callers, and breaking change impacts.

---

## Intelligent Diagnostics

Ask MeshForge why your node is offline:

```
SYMPTOM: Connection refused to meshtasticd

LIKELY CAUSE: Service not running
CONFIDENCE: 85%

EVIDENCE:
  - Port 4403 not listening
  - systemctl shows inactive

SUGGESTIONS:
  1. sudo systemctl start meshtasticd
  2. Check /var/log/meshtasticd.log
  3. Verify USB device is connected
```

**Standalone mode**: Rule-based diagnostics + knowledge base (works offline)

**PRO mode**: Claude AI for natural language questions and complex analysis

---

## Choose Your Interface

| Interface | Command | Best For |
|-----------|---------|----------|
| **Auto** | `sudo python3 src/launcher.py` | Let MeshForge decide |
| **Rich CLI** | `sudo python3 src/launcher_tui/main.py` | SSH / headless (recommended) |
| **Web UI** | `sudo python3 src/main_web.py` | Browser access (port 8880) |
| **GTK Desktop** | `sudo python3 src/main_gtk.py` | Full graphical (needs display) |
| **Standalone** | `python3 src/standalone.py` | Zero dependencies |

All interfaces share the same AI features and gateway capabilities.

---

## Architecture

MeshForge connects to services — it doesn't embed them.

```
USER INTERFACES
  GTK4 Desktop | TUI (SSH) | Web | CLI | Standalone
                      |
               COMMANDS LAYER
  meshtastic.py | gateway.py | rns.py | service.py
                      |
                UTILS LAYER
  diagnostic_engine | knowledge_base | coverage_map | rf.py
                      |
             EXTERNAL SERVICES
  meshtasticd | rnsd | HamClock | MQTT broker
```

**Design Principles**
- Services run independently — MeshForge monitors and configures
- Viewer mode (no sudo) vs Admin mode (sudo required)
- Graceful degradation when dependencies are missing
- All operations go through `src/commands/` for consistency

---

## Supported Hardware

**Platforms**: Raspberry Pi 5/4/3/Zero 2 W, Debian/Ubuntu x86_64

**Native SPI Radios** (via meshtasticd):
- Meshtoad / MeshStick (CH341 USB-to-SPI + SX1262)
- MeshAdv-Pi-Hat (GPIO SPI)
- RAK WisLink HAT
- Waveshare SX126x HAT

**USB Serial Radios** (via Python CLI):
- Heltec V3/V4 (ESP32-S3)
- RAK4631 (nRF52840)
- T-Beam, T-Echo (ESP32)
- LilyGo T-Deck

Config templates in `/etc/meshtasticd/available.d/` - copy to `config.d/` to activate.

---

## Full Installation

```bash
# Raspberry Pi / Debian
sudo apt update
sudo apt install -y python3-pip python3-gi python3-gi-cairo \
    gir1.2-gtk-4.0 libadwaita-1-0 gir1.2-adw-1

pip3 install rich textual flask meshtastic folium --break-system-packages

# Enable SPI/I2C for HATs
sudo raspi-config nonint do_spi 0
sudo raspi-config nonint do_i2c 0

# Clone and run
git clone https://github.com/Nursedude/meshforge.git
cd meshforge
sudo python3 src/launcher_tui.py
```

**Desktop Integration**
```bash
sudo ./scripts/install-desktop.sh
meshforge vte  # Launch with proper taskbar icon
```

---

## Contributing

We welcome contributions! Before submitting:

1. Install git hooks: `cp scripts/hooks/pre-commit .git/hooks/ && chmod +x .git/hooks/pre-commit`
2. Run tests: `python3 -m pytest tests/ -v`
3. Use `get_real_user_home()` instead of `Path.home()` for user paths
4. Add tests for new features
5. Use the commands layer for new operations

Pre-commit hooks automatically run security linting and critical tests.

See `CLAUDE.md` for development guidelines.

---

## Resources

- [Meshtastic Documentation](https://meshtastic.org/docs/)
- [Reticulum Network](https://reticulum.network/)
- [AREDN Mesh](https://www.arednmesh.org/)

---

## License

GPL-3.0 - See [LICENSE](LICENSE)

---

<p align="center">
  <img src="assets/shaka-simple.svg" alt="Shaka" width="32" height="32"/><br>
  <strong>MeshForge</strong><br>
  Your Mesh Network Operations Center<br>
  <sub>Made with aloha for the mesh community | WH6GXZ</sub>
</p>
