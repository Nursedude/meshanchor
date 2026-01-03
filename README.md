# MeshForge

```
╔╦╗╔═╗╔═╗╦ ╦╔═╗╔═╗╦═╗╔═╗╔═╗
║║║║╣ ╚═╗╠═╣╠╣ ║ ║╠╦╝║ ╦║╣
╩ ╩╚═╝╚═╝╩ ╩╚  ╚═╝╩╚═╚═╝╚═╝
LoRa Mesh Network Development & Operations Suite
```

**Build. Test. Deploy. Monitor.**

A professional-grade toolkit for developing, testing, and managing Meshtastic/LoRa mesh networks on Raspberry Pi and Linux systems.

**Version 4.0.0** | [Changelog](#version-history)

---

## What is MeshForge?

MeshForge is a comprehensive suite of tools for:

- **Installing & Managing** meshtasticd daemon on Linux/Raspberry Pi
- **Configuring** LoRa radios, channels, and mesh network settings
- **Testing** radio links, frequency planning, and network topology
- **Monitoring** nodes, messages, and system health
- **Developing** mesh network applications and configurations

Originally created to simplify meshtasticd installation, MeshForge has evolved into a full network operations center for LoRa mesh networks.

---

## Quick Start

```bash
# Clone the repository
git clone https://github.com/YOUR_USERNAME/meshforge.git
cd meshforge

# Install dependencies
sudo apt install python3-gi python3-gi-cairo gir1.2-gtk-4.0 libadwaita-1-0 gir1.2-adw-1

# Launch MeshForge
sudo python3 src/main_gtk.py      # GTK Desktop UI
sudo python3 src/main_web.py      # Web UI (http://localhost:8880)
sudo python3 src/main_tui.py      # Terminal UI (SSH-friendly)
```

---

## Interfaces

| Interface | Command | Best For |
|-----------|---------|----------|
| **Web UI** | `sudo python3 src/main_web.py` | Remote access via browser |
| **GTK Desktop** | `sudo python3 src/main_gtk.py` | Pi with display, VNC |
| **Terminal TUI** | `sudo python3 src/main_tui.py` | SSH, headless systems |
| **CLI** | `sudo python3 src/main.py` | Scripting, minimal environments |

---

## Features

### FORGE - Build & Configure
- Interactive meshtasticd installation (stable/beta/daily/alpha channels)
- Hardware auto-detection (USB LoRa devices, SPI HATs, I2C displays)
- Configuration templates for common hardware setups
- YAML config file manager with nano integration

### TEST - Validate & Analyze
- Frequency Slot Calculator (coming soon)
- Link Budget Calculator with FSPL and Fresnel zones
- Network topology mapping
- RF tools and range estimation

### DEPLOY - Install & Activate
- One-click config activation from templates
- Service management (start/stop/restart/logs)
- Boot persistence configuration
- Auto-update notifications

### MONITOR - Track & Observe
- Real-time dashboard (CPU, memory, temperature)
- Node list with hardware info
- Message sending (broadcast and direct)
- System health monitoring

---

## Supported Hardware

### Raspberry Pi Models
- Pi 5, 4, 3, 2, Zero 2 W, Zero W, 400

### USB LoRa Devices
- MeshToad, MeshTadpole, MeshStick
- CH340/CH341, CP2102, ESP32-S3, nRF52840

### SPI LoRa HATs
- MeshAdv-Mini, MeshAdv-Pi-Hat
- Waveshare SX126X, Adafruit RFM9x

### I2C Displays
- SSD1306 OLED, SH1106, BME280, GPS modules

---

## Web UI

Access MeshForge from any browser on your network:

```bash
# Start web server (default port 8880)
sudo python3 src/main_web.py

# With password protection
sudo python3 src/main_web.py --password mysecret

# Custom port
sudo python3 src/main_web.py --port 9000

# Check status / stop
sudo python3 src/main_web.py --status
sudo python3 src/main_web.py --stop
```

Open `http://your-pi-ip:8880` in your browser.

---

## GTK Desktop UI

Modern libadwaita interface with tabbed navigation:

```bash
# Normal mode
sudo python3 src/main_gtk.py

# Background/daemon mode
sudo python3 src/main_gtk.py --daemon

# Keyboard shortcuts
# F11 - Toggle fullscreen
# Escape - Exit fullscreen
# Ctrl+Q - Quit
```

---

## Project Structure

```
meshforge/
├── src/
│   ├── main_gtk.py          # GTK Desktop entry point
│   ├── main_web.py          # Web UI entry point
│   ├── main_tui.py          # Terminal TUI entry point
│   ├── main.py              # CLI entry point
│   ├── __version__.py       # Version info
│   ├── gtk_ui/
│   │   ├── app.py           # MeshForge GTK application
│   │   └── panels/          # UI panels (dashboard, config, radio, etc.)
│   ├── installer/           # meshtasticd installation logic
│   ├── config/              # Configuration management
│   ├── tools/               # Network, RF, and MUDP tools
│   ├── services/            # Systemd service management
│   └── utils/               # Utilities and helpers
├── templates/               # Config templates
├── .claude/                 # Development session notes
└── README.md
```

---

## Version History

### v4.0.0 (2026-01-03) - MeshForge
- **REBRAND**: Project renamed from "Meshtasticd Interactive Installer" to **MeshForge**
- **NEW**: Professional suite branding - "LoRa Mesh Network Development & Operations Suite"
- **NEW**: Enhanced Radio Config parsing with robust data extraction
- **NEW**: Hardware detection for USB LoRa devices (CH340, CP2102, ESP32, nRF52840)
- **NEW**: Serial port detection for GPS modules
- **NEW**: Desktop launcher support for Raspberry Pi
- **IMPROVED**: Session notes for development continuity
- **FOUNDATION**: Preparing for future node flashing capability

### v3.2.7 (2026-01-02)
- Web UI with Nodes and Messages tabs
- Clean shutdown with signal handlers
- D-Bus registration fix for GTK

### v3.2.x (2026-01-02)
- System monitor, daemon control
- Radio configuration panel
- Node monitoring module

### v3.0.0 - v3.1.x (2025-12-30)
- GTK4 and Textual TUI interfaces
- Config file manager
- System diagnostics and site planner

### v2.x (2025-12-29 - 2025-12-30)
- Service management, CLI integration
- Channel presets and templates

### v1.x (2025-11-15)
- Initial release with basic installation

---

## Roadmap

### v4.1 - Self-Updating
- Component version detection
- One-click updates for meshtasticd, CLI, MeshForge
- Backup and rollback system

### v4.2 - Desktop Integration
- `.desktop` launcher for Raspberry Pi
- System tray icon
- Autostart on boot

### v4.3 - Node Flashing
- Flash Meshtastic firmware to connected devices
- OTA updates for mesh nodes

---

## Contributing

See `.claude/session_notes.md` for development patterns and architecture notes.

---

## License

GPL-3.0

---

## Resources

- [Meshtastic Documentation](https://meshtastic.org/docs/)
- [Meshtastic GitHub](https://github.com/meshtastic)
- [MtnMesh Community](https://mtnme.sh/)
