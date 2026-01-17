# MeshForge

<p align="center">
  <img src="assets/shaka-simple.svg" alt="Shaka" width="64" height="64"/>
</p>

<p align="center">
  <strong>The first Network Operations Center bridging Meshtastic and Reticulum mesh networks.</strong>
</p>

<p align="center">
  <a href="https://github.com/Nursedude/meshforge"><img src="https://img.shields.io/badge/version-0.4.6--beta-blue.svg" alt="Version"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-GPL--3.0-green.svg" alt="License"></a>
  <a href="https://python.org"><img src="https://img.shields.io/badge/python-3.9+-yellow.svg" alt="Python"></a>
  <a href="tests/"><img src="https://img.shields.io/badge/tests-1037%20passing-brightgreen.svg" alt="Tests"></a>
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

**One-liner install:**
```bash
curl -sSL https://raw.githubusercontent.com/Nursedude/meshforge/main/install.sh | sudo bash
```

**Or manual install:**
```bash
git clone https://github.com/Nursedude/meshforge.git
cd meshforge
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
| Nodeless MQTT monitoring | ✓ | ✓ |
| Coverage map generation | ✓ | ✓ |
| RF calculations (FSPL, Fresnel, link budget) | ✓ | ✓ |
| Service management (start/stop/logs) | ✓ | ✓ |
| Full radio configuration | ✓ | ✓ |
| AI diagnostics | Rule-based | Claude-powered |
| Knowledge base | ✓ | ✓ |
| Natural language queries | — | ✓ |

**PRO mode** requires an Anthropic API key (`ANTHROPIC_API_KEY` env var).

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
| **TUI** | `sudo python3 src/launcher_tui.py` | SSH / headless (recommended) |
| **GTK Desktop** | `sudo python3 src/main_gtk.py` | Full graphical interface |
| **Web UI** | `sudo python3 src/main_web.py` | Browser access |
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

**Raspberry Pi**: Pi 5, Pi 4, Pi 3, Zero 2 W

**USB LoRa Devices**
- ESP32-S3: MeshToad, MeshTadpole, Heltec V3
- nRF52840: RAK4631, MeshStick
- ESP32: T-Beam, T-Echo

**SPI HATs**: MeshAdv-Pi-Hat, Waveshare SX126x, Adafruit RFM9x

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

1. Run tests: `python3 -m pytest tests/ -v`
2. Use `get_real_user_home()` instead of `Path.home()` for user paths
3. Add tests for new features
4. Use the commands layer for new operations

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
