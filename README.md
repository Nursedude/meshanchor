# Meshtasticd Interactive Installer & Manager

An interactive installer, updater, and comprehensive configuration tool for meshtasticd on Raspberry Pi OS and compatible Linux systems.

**Version 3.0.0** | [Changelog](#version-history)

## What's New in v3.0.0

- **GTK4 Graphical Interface** - Modern desktop UI with libadwaita design
- **Textual TUI** - Full-featured terminal UI for SSH/headless access (Raspberry Pi Connect friendly)
- **Config File Manager** - Select YAML from `/etc/meshtasticd/available.d`, edit with nano
- **Service Management** - Start/stop/restart with live log viewing
- **Meshtastic CLI Integration** - Run CLI commands from the UI
- **Reboot Persistence** - Installer auto-restarts after system reboot
- **Three UI Options** - Choose GTK4, Textual TUI, or Rich CLI based on your setup

## UI Options

| Interface | Command | Best For |
|-----------|---------|----------|
| **GTK4 GUI** | `sudo python3 src/main_gtk.py` | Pi with display, Raspberry Pi Connect desktop |
| **Textual TUI** | `sudo python3 src/main_tui.py` | SSH, headless, Raspberry Pi Connect terminal |
| **Rich CLI** | `sudo python3 src/main.py` | Fallback, minimal environments |

## Features

### Installation & Management
- **Interactive Installation**: Guided setup for meshtasticd daemon
- **Version Management**: Install/update stable, beta, daily, or alpha builds
- **Official Repositories**: Uses OpenSUSE Build Service for latest builds
- **Virtual Environment**: Isolated Python dependencies (fixes PEP 668 errors)
- **OS Detection**: Automatic detection of 32-bit/64-bit Raspberry Pi OS and other Linux boards
- **Board Detection**: Reads exact model from device tree (Pi 2/3/4/5/Zero/Zero 2W/etc.)
- **Dependency Management**: Automatically fix deprecated dependencies
- **Error Handling**: Comprehensive debugging and troubleshooting tools
- **Automatic Update Notifications**: Get notified when updates are available

#### Available Build Channels
- **stable/beta** - Latest stable releases from `network:Meshtastic:beta` (recommended)
- **daily** - Cutting-edge daily builds from `network:Meshtastic:daily`
- **alpha** - Experimental alpha builds from `network:Meshtastic:alpha`

### Config File Manager (New in v3.0)
- **Browse available.d** - View all YAML configs from meshtasticd package
- **Activate configs** - Copy to config.d with one click
- **Edit with nano** - Direct editing in terminal (always returns to app)
- **Apply changes** - Automatic daemon-reload and service restart
- **Preview files** - See config content before activating

### Service Management
- **Start/Stop/Restart** - Control meshtasticd service
- **Live Logs** - View and follow journalctl output
- **Boot Control** - Enable/disable service on startup
- **Daemon Reload** - Reload systemd after config changes

### Quick Status Dashboard
Real-time monitoring at a glance:
- **Service Status**: Running/stopped state with uptime information
- **System Health**: CPU temperature, memory usage, disk space
- **Network Status**: IP address, internet connectivity
- **Configuration Status**: Active config file and template
- **Quick Actions**: Refresh, view logs, restart service, check updates

### Channel Presets
Pre-configured channel setups for common use cases:
- **Default Meshtastic** - Standard LongFast configuration
- **MtnMesh Community** - MediumFast with slot 20
- **Emergency/SAR** - Maximum range for emergency operations
- **Urban High-Density** - ShortFast for city networks
- **Private Group** - Custom encrypted channels
- **Long Range** - Maximum distance configuration
- **Repeater/Router** - Infrastructure node setup

### Hardware Support
- **Hardware Detection**: Auto-detect USB and SPI LoRa modules
- **MeshToad/MeshTadpole Support**: Specialized detection for MtnMesh devices
- **Power Warnings**: Alerts for high-power modules (900mA+ devices)

### Radio Configuration
- **Modem Presets**: All official Meshtastic presets (MediumFast, LongFast, ShortFast, etc.)
- **Channel Slot Configuration**: Interactive slot selection
- **Region Selection**: All supported regulatory regions
- **TX Power Configuration**: 0-30 dBm with device-specific recommendations

## Supported Platforms

- **Raspberry Pi OS** (32-bit armhf, 64-bit arm64)
- **Raspbian** Bookworm and newer
- **Debian-based** Linux distributions
- Works on: Direct console, SSH, Raspberry Pi Connect, Screen/tmux

## Supported Hardware

### Raspberry Pi Models
- Raspberry Pi 5, 4, 3, 2, Zero 2 W, Zero W, Zero, 400

### USB LoRa Modules
- MeshToad, MeshTadpole, MeshStick, CH340/CH341, CP2102, FT232

### SPI LoRa HATs
- MeshAdv-Mini, MeshAdv-Pi-Hat, Waveshare SX126X, Adafruit RFM9x

## Installation

### Quick Install (Recommended)

```bash
curl -sSL https://raw.githubusercontent.com/Nursedude/Meshtasticd_interactive_UI/main/install.sh | sudo bash
```

### UI-Specific Dependencies

**For GTK4 Graphical Interface:**
```bash
sudo apt install python3-gi python3-gi-cairo gir1.2-gtk-4.0 libadwaita-1-0 gir1.2-adw-1
```

**For Textual TUI:**
```bash
pip install textual
```

### Manual Installation

```bash
git clone https://github.com/Nursedude/Meshtasticd_interactive_UI.git
cd Meshtasticd_interactive_UI
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Choose your UI:
sudo ./venv/bin/python src/main_gtk.py   # GTK4 GUI
sudo ./venv/bin/python src/main_tui.py   # Textual TUI
sudo ./venv/bin/python src/main.py       # Rich CLI
```

## Usage

```bash
# GTK4 Graphical Interface (requires display)
sudo python3 src/main_gtk.py

# Textual TUI (works over SSH, Raspberry Pi Connect)
sudo python3 src/main_tui.py

# Rich CLI (original interface)
sudo python3 src/main.py
```

## Version History

### v3.0.0 (2025-12-30)
- **NEW: GTK4 graphical interface** - Modern libadwaita design
- **NEW: Textual TUI** - Terminal UI for SSH/headless access
- **Config File Manager** - Select YAML from available.d, edit with nano
- **Service Management panel** - Start/stop/restart with live logs
- **Meshtastic CLI panel** - Integrated CLI commands
- **Hardware Detection panel** - Detect SPI/I2C devices
- **Reboot Persistence** - Installer auto-restarts after reboot
- **Three UI options** - GTK4, Textual TUI, Rich CLI

### v2.3.0 - v2.0.0
- Config File Manager, Service Management, CLI integration
- Channel Presets, Configuration Templates, Dashboard
- PEP 668 fixes, emoji fallback, hardware detection

### v1.x
- Initial release with basic installation and configuration

## License

GPL-3.0

## Community Resources

- [Meshtastic Documentation](https://meshtastic.org/docs/)
- [MtnMesh Community](https://mtnme.sh/)
- [MeshAdv-Pi-Hat](https://github.com/chrismyers2000/MeshAdv-Pi-Hat)
