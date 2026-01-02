# Meshtasticd Installer - Development Session Notes

## Current Version: v3.2.2
## Session Date: 2026-01-02
## Branch: `claude/review-meshtasticd-installer-52ENu`

---

## QUICK RESUME - Start Here

When resuming this project, read this file and `CLAUDE_CONTEXT.md` first.

```bash
# 1. Switch to the feature branch
git checkout claude/review-meshtasticd-installer-52ENu

# 2. Check current status
git status && git log --oneline -5

# 3. Test the application
sudo python3 src/main.py        # Rich CLI
sudo python3 src/main_tui.py    # Textual TUI
sudo python3 src/main_gtk.py    # GTK4 GUI
```

---

## Latest Session Summary (2026-01-02)

### Completed This Session (v3.2.2)

1. **Radio Configuration Panel in GTK UI** (`src/gtk_ui/panels/radio_config.py`) - NEW
   - New sidebar navigation item "Radio Configuration"
   - Device & Mesh Settings: Role, rebroadcast mode
   - LoRa Settings: Region, modem preset, hop limit
   - Position Settings: GPS mode, broadcast interval, fixed position
   - Power Settings: TX power, power saving mode
   - MQTT Settings: Enable, server, auth, encryption, TLS
   - Telemetry Settings: Device and environment metrics intervals
   - Actions: Load Config, View Full Config, Factory Reset, Reboot Node

2. **Fixed GTK Status Bar** (`src/gtk_ui/app.py`)
   - FIX: Node count now updates from meshtastic CLI
   - FIX: Uptime now displays correctly when service running
   - NEW: `_get_node_count()` method queries `meshtastic --nodes`

3. **Fixed Dashboard Config Count** (`src/gtk_ui/panels/dashboard.py`)
   - FIX: Now counts both .yaml and .yml files
   - FIX: Better detection of meshtasticd installation

4. **Fixed Hardware Detection** (`src/gtk_ui/panels/hardware.py`)
   - FIX: Enable SPI/I2C buttons now use `sudo`
   - NEW: Detects active meshtasticd hardware via CLI
   - NEW: Shows active configs from config.d
   - IMPROVED: Better I2C device parsing

5. **Web Client Documentation** (`RESEARCH.md`)
   - NEW: Added Meshtastic Web Client section
   - HTTP, BLE, Serial connection methods
   - Browser compatibility notes
   - MUI and BaseUI (2.7+) documentation
   - References and links

### Previous Session (v3.2.1)

1. **Full Radio Configuration** (`src/config/radio_config.py`) - CLI version
2. **GTK Service Panel Fix** - Added sudo to systemctl
3. **Hardware Configuration** (`src/config/hardware_config.py`) - CLI version

---

## Version History

| Version | Date | Key Changes |
|---------|------|-------------|
| v3.2.1 | 2026-01-02 | Hardware Configuration, Full Radio Config, GTK service fix |
| v3.2.0 | 2026-01-01 | Network Tools, RF Tools, MUDP Tools, Tool Manager |
| v3.1.1 | 2026-01-01 | TUI widget ID fix, GTK content_stack fix, pip --ignore-installed |
| v3.1.0 | 2026-01-01 | System Diagnostics, Site Planner |
| v3.0.6 | 2025-12-31 | Meshtastic CLI detection (pipx) |
| v3.0.5 | 2025-12-31 | Emoji font detection |
| v3.0.4 | 2025-12-31 | Uninstaller, progress indicators, launcher preferences |
| v3.0.3 | 2025-12-31 | Edit channels, consistent navigation |
| v3.0.2 | 2025-12-31 | Channel config, CLI auto-install, PSK generation |
| v3.0.1 | 2025-12-30 | Launcher wizard, bug fixes |
| v3.0.0 | 2025-12-30 | GTK4 GUI, Textual TUI, Config File Manager |

---

## Project Architecture

```
src/
├── main.py                 # Rich CLI entry point
├── main_gtk.py             # GTK4 entry point
├── main_tui.py             # Textual TUI entry point
├── launcher.py             # UI selection wizard
├── __version__.py          # Version and changelog
├── dashboard.py            # Status dashboard
│
├── config/                 # Configuration modules
│   ├── lora.py             # LoRa/Channel configuration
│   ├── radio.py            # Basic radio settings
│   ├── radio_config.py     # Full radio config (NEW)
│   ├── hardware_config.py  # SPI/Serial/GPIO (NEW)
│   ├── modules.py          # Module configuration
│   ├── device.py           # Device configuration
│   ├── hardware.py         # Hardware detection
│   └── channel_presets.py  # Channel presets
│
├── tools/                  # System tools (v3.2.0)
│   ├── network_tools.py    # TCP/IP, ping, scanning
│   ├── rf_tools.py         # Link budget, LoRa analysis
│   ├── mudp_tools.py       # UDP, multicast, MUDP
│   └── tool_manager.py     # Tool install/update
│
├── diagnostics/            # Diagnostic tools (v3.1.0)
│   ├── system_diagnostics.py
│   └── site_planner.py
│
├── gtk_ui/                 # GTK4 interface
│   ├── app.py              # Main GTK4 application
│   └── panels/             # UI panels
│       ├── dashboard.py
│       ├── service.py      # Fixed with sudo
│       ├── config.py
│       ├── cli.py
│       ├── hardware.py
│       └── tools.py        # NEW in v3.2.0
│
├── tui/                    # Textual TUI
│   └── app.py              # Fixed widget IDs
│
├── installer/              # Installation modules
│   ├── meshtasticd.py
│   ├── dependencies.py
│   └── uninstaller.py      # v3.0.4
│
├── services/               # Service management
│   └── service_manager.py
│
├── cli/                    # Meshtastic CLI wrapper
│   └── meshtastic_cli.py
│
└── utils/                  # Utilities
    ├── system.py
    ├── emoji.py            # Font detection
    ├── cli.py              # CLI path finder
    ├── progress.py         # Progress indicators
    └── logger.py
```

---

## Main Menu Options

```
Main Menu:
1. Quick Status Dashboard
2. Service Management
3. Install meshtasticd
4. Update meshtasticd
5. Configure device
6. Channel Presets (Quick Setup)
7. Configuration Templates
8. Config File Manager (YAML + nano)
f. Full Radio Config (Mesh, MQTT, Position) [NEW]
c. Meshtastic CLI Commands
t. System Diagnostics
p. Site Planner
n. Network Tools
r. RF Tools
m. MUDP Tools
g. Tool Manager
9. Check dependencies
h. Hardware detection
w. Hardware Configuration (SPI, Serial, GPIO) [NEW]
d. Debug & troubleshooting
u. Uninstall
q. Exit
```

---

## Known Issues / Pending Work

1. **Device Configuration Wizard** - May need additional back options
2. **Additional TUI/GTK4 testing** - User testing in progress
3. **Mobile/tablet UI** - Not yet optimized

---

## Key Technical Details

### Meshtastic Connection
- Default: `localhost:4403` (TCP)
- CLI: `meshtastic --host localhost`
- MUDP: `224.0.0.69:4403` (multicast)

### Raspberry Pi Configuration
- Boot config: `/boot/firmware/config.txt`
- SPI overlay: `dtoverlay=spi0-0cs`
- Config files: `/etc/meshtasticd/config.d/`
- Available configs: `/etc/meshtasticd/available.d/`

### pip Installation (RPi)
```bash
# For Textual TUI
sudo pip install --break-system-packages --ignore-installed textual

# For GTK4 (system packages)
sudo apt install python3-gi python3-gi-cairo gir1.2-gtk-4.0 libadwaita-1-0 gir1.2-adw-1
```

---

## Git Commands

```bash
# Current branch
git checkout claude/review-meshtasticd-installer-52ENu

# View changes
git diff --stat origin/main..HEAD

# Commit format
git commit -m "feat: Description of feature"
git commit -m "fix: Description of fix"

# Push to branch
git push -u origin claude/review-meshtasticd-installer-52ENu
```

---

## Testing Checklist

- [ ] Rich CLI menu navigation
- [ ] Textual TUI all tabs work
- [ ] GTK4 service buttons work
- [ ] Config file manager (activate/edit)
- [ ] Channel configuration (add/edit)
- [ ] Hardware detection
- [ ] Service start/stop/restart
- [ ] Meshtastic CLI commands
- [ ] System diagnostics
- [ ] RF tools (link budget)
- [ ] MUDP tools (if mudp installed)

---

## Contact / Repository

- **GitHub:** https://github.com/Nursedude/Meshtasticd_interactive_UI
- **Branch:** claude/review-meshtasticd-installer-52ENu
- **License:** GPL-3.0
