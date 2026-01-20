# MeshForge

<p align="center">
  <img src="assets/shaka-simple.svg" alt="Shaka" width="64" height="64"/>
</p>

<p align="center">
  <strong>The First Open-Source NOC Bridging Meshtastic and Reticulum Mesh Networks</strong>
</p>

<p align="center">
  <a href="https://github.com/Nursedude/meshforge"><img src="https://img.shields.io/badge/version-0.4.7--beta-blue.svg" alt="Version"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-GPL--3.0-green.svg" alt="License"></a>
  <a href="https://python.org"><img src="https://img.shields.io/badge/python-3.9+-yellow.svg" alt="Python"></a>
  <a href="tests/"><img src="https://img.shields.io/badge/tests-1297%20passing-brightgreen.svg" alt="Tests"></a>
</p>

<p align="center">
  <a href="https://nursedude.substack.com">📡 Follow on Substack</a> •
  <a href="https://github.com/Nursedude/meshforge/issues">Report Issues</a> •
  <a href="#contributing">Contribute</a>
</p>

---

## Why MeshForge?

**Two great mesh networks. Zero interoperability. Until now.**

Meshtastic and Reticulum are the leading open-source LoRa mesh networks, but they can't communicate with each other. Different protocols, different ecosystems, completely isolated.

```
┌──────────────────┐           ┌──────────────────┐
│    Meshtastic    │     X     │    Reticulum     │
│   (LoRa Mesh)    │ BLOCKED   │      (RNS)       │
└──────────────────┘           └──────────────────┘
         Incompatible protocols - no bridge exists
```

**MeshForge changes this.**

```
┌──────────────────┐    ┌───────────────────┐    ┌──────────────────┐
│    Meshtastic    │◄──►│  MeshForge NOC    │◄──►│    Reticulum     │
│   (LoRa Mesh)    │    │  Gateway Bridge   │    │      (RNS)       │
└──────────────────┘    └─────────┬─────────┘    └──────────────────┘
                                  │
                        ┌─────────┴─────────┐
                        │  Unified Node DB  │
                        │  AI Diagnostics   │
                        │  Coverage Maps    │
                        └───────────────────┘
```

**First and only tool to unify these mesh ecosystems.**

---

## Features at a Glance

| Capability | Description |
|------------|-------------|
| 🌉 **Gateway Bridge** | Route messages between Meshtastic and RNS networks |
| 🗺️ **Unified Node View** | See all nodes from both networks in one place |
| 🤖 **AI Diagnostics** | Natural language troubleshooting (Claude-powered) |
| 📊 **Coverage Maps** | SNR-based link quality visualization |
| 🔧 **NOC Orchestrator** | Service management with health monitoring |
| 📡 **Native SPI Support** | Direct radio control via meshtasticd |
| 🌐 **Multi-Interface** | GTK, TUI, Web, CLI - use what fits your setup |

---

## Quick Start

```bash
# Clone the repo
git clone https://github.com/Nursedude/meshforge.git
cd meshforge

# Full NOC stack install (recommended)
sudo bash scripts/install_noc.sh

# Or minimal install
pip3 install rich textual flask --break-system-packages
sudo python3 src/launcher.py
```

**Startup shows instant health check:**
```
MeshForge v0.4.7-beta

Services:
  ✓ meshtasticd: running (port 4403)
  ✓ Hardware: Meshtoad SX1262 detected
  ⚠ rnsd: not running (optional)

Network:
  ✓ Nodes visible: 3

Ready! [Continue] [Configure] [Troubleshoot]
```

---

## NOC Stack Architecture

MeshForge owns the full stack - it IS your Meshtastic node:

```
╔═══════════════════════════════════════════════════════╗
║                   MeshForge NOC                       ║
╠═══════════════════════════════════════════════════════╣
║  ┌─────────────┐  ┌─────────────┐  ┌───────────────┐  ║
║  │ meshtasticd │  │    rnsd     │  │ Orchestrator  │  ║
║  │   (radio)   │  │    (RNS)    │  │   (manager)   │  ║
║  └──────┬──────┘  └──────┬──────┘  └───────┬───────┘  ║
║         │                │                 │          ║
║         └────────────────┼─────────────────┘          ║
║                          │                            ║
║         ┌────────────────┴────────────────┐           ║
║         │  Health Monitor + Auto-restart  │           ║
║         └─────────────────────────────────┘           ║
╚═══════════════════════════════════════════════════════╝
```

**Supported Hardware:**

| Type | Devices |
|------|---------|
| **SPI HATs** | Meshtoad, MeshAdv-Pi-Hat, RAK WisLink, Waveshare SX126x |
| **USB Radios** | T-Beam, Heltec V3/V4, RAK4631, MeshStick, T-Deck |
| **Platforms** | Raspberry Pi 5/4/3/Zero 2 W, Debian/Ubuntu x86_64 |

---

## Who Is This For?

- **HAM Radio Operators** - Building resilient off-grid communication networks
- **Emergency Services** - ARES/RACES teams needing mesh interoperability
- **Off-Grid Communities** - Connecting disparate mesh systems
- **Network Engineers** - Exploring LoRa protocol bridging
- **Researchers** - Studying mesh network behavior and optimization

---

## Choose Your Interface

| Interface | Command | Best For |
|-----------|---------|----------|
| **Auto** | `sudo python3 src/launcher.py` | Let MeshForge decide |
| **Rich TUI** | `sudo python3 src/launcher_tui/main.py` | SSH / headless |
| **Web UI** | `sudo python3 src/main_web.py` | Browser (port 8880) |
| **GTK Desktop** | `sudo python3 src/main_gtk.py` | Full graphical |
| **Standalone** | `python3 src/standalone.py` | Zero dependencies |

All interfaces share the same AI features and gateway capabilities.

---

## AI-Powered Diagnostics

Ask MeshForge why your node is offline:

```
SYMPTOM: Connection refused to meshtasticd

ANALYSIS:
  ✗ Port 4403 not responding
  ✗ systemctl shows inactive

LIKELY CAUSE: Service not running (85% confidence)

SUGGESTED FIXES:
  1. sudo systemctl start meshtasticd
  2. Check logs: journalctl -u meshtasticd -n 50
  3. Verify USB/SPI device connected
```

| Mode | Capability |
|------|------------|
| **Standalone** | Rule-based diagnostics + knowledge base (works offline) |
| **PRO** | Claude AI for natural language queries (`ANTHROPIC_API_KEY`) |

---

## Reliability Built In

**v0.4.7 Quality Gates:**

| Feature | Description |
|---------|-------------|
| **Single Source of Truth** | All UIs use one canonical service check |
| **Pre-commit Hooks** | Security lint + critical tests before every commit |
| **1297 Tests** | Regression tests prevent status drift across interfaces |
| **API Contracts** | Core functions document guarantees and breaking changes |

---

## Full Installation

```bash
# Dependencies (Raspberry Pi / Debian)
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
sudo python3 src/launcher.py
```

**Desktop Integration:**
```bash
sudo ./scripts/install-desktop.sh
meshforge vte  # Launch with proper taskbar icon
```

---

## Contributing

We're building something new - the first tool to bridge Meshtastic and Reticulum. Contributions welcome!

**Before submitting:**
1. Install hooks: `cp scripts/hooks/pre-commit .git/hooks/ && chmod +x .git/hooks/pre-commit`
2. Run tests: `python3 -m pytest tests/ -v`
3. Use `get_real_user_home()` instead of `Path.home()`
4. Route operations through `src/commands/`

See `CLAUDE.md` for development patterns and architecture.

**Get Involved:**
- 📡 [Follow on Substack](https://nursedude.substack.com) for updates
- 🐛 [Report issues](https://github.com/Nursedude/meshforge/issues)
- 💡 [Discussions](https://github.com/Nursedude/meshforge/discussions)

---

## Resources

| Resource | Link |
|----------|------|
| Meshtastic Docs | [meshtastic.org](https://meshtastic.org/docs/) |
| Reticulum Network | [reticulum.network](https://reticulum.network/) |
| AREDN Mesh | [arednmesh.org](https://www.arednmesh.org/) |

---

## License

GPL-3.0 - See [LICENSE](LICENSE)

---

<p align="center">
  <img src="assets/shaka-simple.svg" alt="Shaka" width="32" height="32"/><br>
  <strong>MeshForge</strong><br>
  Your Mesh Network Operations Center<br>
  <sub>Made with aloha for the mesh community</sub><br>
  <sub>WH6GXZ • Hawaii</sub>
</p>
