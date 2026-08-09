# Installing MeshAnchor

Hardware, install, and first run. The short version lives in the [README](../README.md); this is the full path.

## Quick Start

> **Using Meshtastic as your primary radio?** Use [MeshForge](https://github.com/Nursedude/meshforge) instead.

### Fresh Install

```bash
# One-liner (Pi / Debian / Ubuntu)
curl -sSL https://raw.githubusercontent.com/Nursedude/meshanchor/main/install.sh | sudo bash

# Or manually
git clone https://github.com/Nursedude/meshanchor.git /opt/meshanchor
cd /opt/meshanchor
sudo bash scripts/install_noc.sh    # Full NOC stack install
```

The NOC installer auto-detects your radio hardware (SPI HAT or USB), optionally installs
meshtasticd + Reticulum, and sets up systemd services. It will prompt you to select
your HAT if SPI is detected.

**One-liner options** (environment variables):
```bash
# Install with automatic system upgrade
curl -sSL https://raw.githubusercontent.com/Nursedude/meshanchor/main/install.sh | sudo UPGRADE_SYSTEM=yes bash

# Install without upgrade prompt
curl -sSL https://raw.githubusercontent.com/Nursedude/meshanchor/main/install.sh | sudo SKIP_UPGRADE=yes bash

# Use system pip instead of venv
curl -sSL https://raw.githubusercontent.com/Nursedude/meshanchor/main/install.sh | sudo BREAK_SYSTEM_PACKAGES=yes bash
```

**NOC installer options** (`scripts/install_noc.sh`):
```bash
sudo bash scripts/install_noc.sh --skip-meshtasticd   # Don't install meshtasticd
sudo bash scripts/install_noc.sh --skip-rns            # Don't install Reticulum
sudo bash scripts/install_noc.sh --client-only         # MeshAnchor only (no daemons)
sudo bash scripts/install_noc.sh --force-native        # Force SPI mode (native meshtasticd)
sudo bash scripts/install_noc.sh --force-python        # Force USB mode (Python CLI)
```

### Deployment Profiles

MeshAnchor supports 5 deployment profiles. Install only the dependencies you need.
Order below matches the in-TUI picker (MeshCore-first per the v0.1.0-alpha charter).

| Profile | Primary Use | MeshCore | Meshtastic | RNS | MQTT | Maps | Tactical |
|---------|------------|:--------:|:----------:|:---:|:----:|:----:|:--------:|
| `meshcore` (default) | MeshCore companion radio | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| `radio_maps` | MeshCore + coverage mapping | ✅ | ❌ | ❌ | ❌ | ✅ | ❌ |
| `monitor` | MQTT packet analysis (no radio) | ❌ | ❌ | ❌ | ✅ | ❌ | ❌ |
| `gateway` | MeshCore + Meshtastic/RNS bridge | ✅ | ✅ | ✅ | ✅ | ✅ | ❌ |
| `full` | Everything | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |

```bash
# Select profile at launch
python3 src/launcher.py --profile gateway

# Auto-detect (default): scans running services and installed packages
python3 src/launcher.py

# Profile is saved to ~/.config/meshanchor/deployment.json
```

For the full feature-flag matrix, per-profile install commands, decision tree, and
the rationale behind each default, see
[`../.claude/foundations/deployment_profiles.md`](../.claude/foundations/deployment_profiles.md).

### Already Have meshtasticd?

```bash
sudo python3 src/launcher_tui/main.py
```

### RF Tools Only (no sudo, no radio)

```bash
python3 src/standalone.py              # Interactive menu
python3 src/standalone.py rf           # RF calculator
python3 src/standalone.py sim          # Network simulator
python3 src/standalone.py --check      # Check dependencies
```

### TUI Menu Structure

The TUI uses a raspi-config style interface (whiptail/dialog) designed for SSH and
headless operation. Navigation is keyboard-driven with max 10 items per menu level:

```
Main Menu (MeshAnchor NOC)
├── 1. Dashboard             Service status, health, alerts, data path check
├── 2. MeshCore              Primary radio + Optional Gateways
│       ├── MeshCore submenu Status, detect, config, radio (LoRa/TX/channels),
│       │                    nodes, stats, chat, daemon control
│       ├── MeshCore CLI     meshcore-cli passthrough — common verbs, DM/channel,
│       │                    remote-admin cmd, interactive REPL (auto daemon handoff)
│       └── Optional Gateways → Meshtastic, RNS, AREDN, MQTT, Gateway Bridge
├── 3. RF & SDR              Link budget, site planner, frequency slots, SDR
├── 4. Maps & Viz            Live NOC map, coverage, topology, traffic inspector,
│                            meshforge-maps :8808 integration
├── 5. Configuration         Radio, channels, RNS config, services, backup
├── 6. System                Hardware detect, logs, network tools, shell, reboot
├── q. Quick Actions         Common shortcuts (2-tap access)
├── e. Emergency Mode        Field ops, weather/EAS alerts, SOS beacon
├── t. Tactical Ops          SITREP, zones, QR, ATAK (visible under FULL profile)
├── a. About                 Version, web client, help
└── x. Exit
```

### Upgrade / Reinstall / Uninstall

```bash
# Quick update (code + service files)
cd /opt/meshanchor && sudo bash scripts/update.sh

# Quick update to specific branch
sudo bash scripts/update.sh --branch main

# Clean reinstall (recommended for major version bumps)
sudo bash /opt/meshanchor/scripts/reinstall.sh

# Clean reinstall without confirmation prompt
sudo bash scripts/reinstall.sh --no-confirm

# Complete removal
sudo bash scripts/reinstall.sh --remove-only

# Manual git pull (developers)
cd /opt/meshanchor && sudo git pull origin main
```

**What is preserved during reinstall** (never touched):

| Preserved | Path | Why |
|-----------|------|-----|
| meshtasticd | apt package + `/etc/meshtasticd/config.yaml` | Separate package, your radio config |
| Radio hardware configs | `/etc/meshtasticd/config.d/` | Backed up and restored |
| Reticulum identity | `~/.reticulum/` | Your RNS address + keys |
| MeshAnchor user settings | `~/.config/meshanchor/` | Backed up and restored |
| MQTT broker | mosquitto service + config | Separate service |
| System packages | pip, apt installs | Not managed by MeshAnchor |

No need to re-image your Pi. Your radio stays configured.

### Post-Install Verification

```bash
# Automated check
sudo python3 src/launcher.py --verify-install

# Manual checks
python3 -c "from src.__version__ import __version__; print(__version__)"
python3 -m pytest tests/ -v --tb=short
sudo python3 src/launcher_tui/main.py
```

### Troubleshooting

| Issue | Solution |
|-------|----------|
| Python import errors | `sudo bash scripts/reinstall.sh` (clean reinstall) |
| `Local changes would be overwritten` | `git stash` before pull, or use clean reinstall |
| Service won't start | `journalctl -u meshanchor -n 50` |
| `meshcore` module not found | `pip install meshcore` (or `--break-system-packages` on Bookworm+) |
| `meshcore-cli not found` in TUI CLI menu | `/opt/meshanchor/venv/bin/pip install meshcore-cli` (or use TUI's *MeshCore CLI → Install / Locate*) |
| USB device path changes on reboot | Install udev rules: `sudo cp scripts/99-meshcore.rules /etc/udev/rules.d/ && sudo udevadm control --reload-rules` |
| Permission denied on serial port | Add user to dialout group: `sudo usermod -aG dialout $USER` then log out/in |
| `meshtastic` module not found | `pip install meshtastic --break-system-packages --ignore-installed` |
| Config file conflicts | Restore from `~/meshanchor-backup-*` or regenerate via TUI |
| Stale `.pyc` files | Clean reinstall handles this automatically |

---

## Hardware

**Minimum:** Raspberry Pi 3B+ + MeshCore companion radio
**Recommended:** Raspberry Pi 4/5 + MeshCore radio (~$35-90)

| Component | Options |
|-----------|---------|
| **Computer** | Raspberry Pi 4/5 (recommended), Pi 3B+, Pi Zero 2W, x86_64 Linux |
| **OS** | Raspberry Pi OS Bookworm 64-bit, Debian 12+, Ubuntu 22.04+ |
| **MeshCore Radio** | See companion radios table below |
| **Optional** | Meshtastic radio (gateway), RTL-SDR (spectrum), GPS module |

### MeshCore Companion Radios

MeshCore companion radios connect via USB serial and are managed by the meshcore_py library.
MeshAnchor auto-detects connected devices.

| Device | Connection | Notes |
|--------|-----------|-------|
| **RAK4631** (nRF52840) | USB Serial | Ultra-low power, GPS, UF2 flashing |
| **Heltec V3** (ESP32-S3) | USB Serial / WiFi TCP | Common, affordable, gateway capable |
| **Heltec V4** (ESP32-S3) | USB Serial | 28dBm TX power |
| **T-Deck** | USB Serial | Built-in keyboard + display |
| **T-Echo** | USB Serial | E-ink display, GPS |
| **Station G2** | USB Serial | Gateway capable, PoE option |

### Meshtastic Gateway Radios (Optional)

For the `gateway` or `full` deployment profile, you also need a Meshtastic radio.
MeshAnchor supports all radios that MeshForge supports — see the
[MeshForge hardware tables](https://github.com/Nursedude/meshforge#hardware) for
SPI HATs (MeshAdv, Waveshare, Ebyte, RAK) and USB radios (Heltec, T-Beam, MeshToad).

Pre-built gateway profiles are available in `src/gateway/profiles/`:

| Profile | Radio | Use Case |
|---------|-------|----------|
| `heltec_v3_gateway.yaml` | Heltec V3 | Standard USB gateway |
| `heltec_v4_gateway.yaml` | Heltec V4 | High-power USB gateway |
| `rak4631_gateway.yaml` | RAK4631 | Low-power gateway |
| `station_g2_gateway.yaml` | Station G2 | Infrastructure gateway |
| `rnode_gateway.yaml` | RNode | Dedicated RNS radio |

---

