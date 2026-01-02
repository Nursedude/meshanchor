# Claude Context - Meshtasticd Interactive Installer

**READ THIS FILE FIRST WHEN RESUMING A SESSION**

This file contains essential context for Claude to quickly understand and resume work on this project. It serves as persistent memory across sessions when context is limited.

---

## Project Overview

**Meshtasticd Interactive Installer** is a comprehensive configuration and management tool for meshtasticd (the Linux-native Meshtastic daemon) on Raspberry Pi and compatible systems.

### Three User Interfaces
1. **Rich CLI** (`src/main.py`) - Command-line interface with Rich formatting
2. **Textual TUI** (`src/main_tui.py`) - Full terminal UI for SSH/headless
3. **GTK4 GUI** (`src/main_gtk.py`) - Graphical interface with libadwaita

### Current State
- **Version:** 3.2.2
- **Branch:** `claude/review-meshtasticd-installer-52ENu`
- **Status:** Feature complete, ready for PR/merge

---

## Key Files to Know

| File | Purpose |
|------|---------|
| `src/main.py` | Main CLI entry - menu system, all features |
| `src/__version__.py` | Version string and changelog |
| `src/config/lora.py` | Channel configuration (PSK, MQTT, roles) |
| `src/config/radio_config.py` | Full radio config (Mesh, Position, Power, MQTT) |
| `src/gtk_ui/panels/radio_config.py` | GTK Radio Configuration panel |
| `src/config/hardware_config.py` | SPI/I2C/Serial/GPIO configuration |
| `src/tools/*.py` | Network, RF, MUDP tools |
| `src/gtk_ui/panels/service.py` | GTK service panel (uses sudo for systemctl) |
| `src/tui/app.py` | Textual TUI (widget IDs use underscores, not dots) |

---

## Common Issues & Fixes Applied

### 1. GTK Service Buttons Not Working
**Problem:** systemctl commands fail without sudo
**Solution:** All systemctl calls in `src/gtk_ui/panels/service.py` use `['sudo', 'systemctl', ...]`

### 2. Textual Widget ID Error (dots in filenames)
**Problem:** `BadIdentifier: 'display-waveshare-2.8.yaml'` - dots invalid in Textual IDs
**Solution:** `config.stem.replace(".", "_")` for IDs, retrieve filename from Label text

### 3. pip Install Failures on Raspberry Pi
**Problem:** `Cannot uninstall Pygments` - system package conflict
**Solution:** `pip install --break-system-packages --ignore-installed textual`

### 4. GTK content_stack AttributeError
**Problem:** Sidebar callback fires before content_stack exists
**Solution:** Create content_stack and add pages BEFORE creating sidebar in `_build_ui()`

### 5. Meshtastic CLI Not Found
**Problem:** CLI installed via pipx not in PATH
**Solution:** Check multiple paths: `/root/.local/bin`, `/home/pi/.local/bin`, `~/.local/bin`

---

## Meshtastic Technical Details

### Ports & Protocols
- **TCP:** 4403 (meshtasticd default)
- **UDP Multicast:** 224.0.0.69:4403 (MUDP)
- **CLI:** `meshtastic --host localhost`

### Raspberry Pi Paths
- Boot config: `/boot/firmware/config.txt`
- Config files: `/etc/meshtasticd/config.d/`
- Available configs: `/etc/meshtasticd/available.d/`
- SPI overlay: `dtoverlay=spi0-0cs`

### CLI Commands (via meshtastic)
```bash
meshtastic --info                    # Device info
meshtastic --set device.role ROUTER  # Set device role
meshtastic --set lora.region US      # Set region
meshtastic --ch-set name "MyChannel" --ch-index 0  # Channel config
```

---

## Menu Structure (src/main.py)

```
Main Menu Options:
1-4  = Status, Service, Install, Update
5-8  = Configure, Presets, Templates, Config Files
f    = Full Radio Config (Mesh, MQTT, Position, Telemetry)
c    = Meshtastic CLI
t,p  = Diagnostics, Site Planner
n,r,m,g = Network Tools, RF Tools, MUDP Tools, Tool Manager
9,h,w,d,u = Dependencies, Hardware, Hardware Config, Debug, Uninstall
q,?  = Exit, Help
```

---

## Code Patterns Used

### Rich CLI Menu Pattern
```python
while True:
    console.print("\n[bold cyan]Menu Title[/bold cyan]")
    console.print("  [bold]1[/bold]. Option one")
    console.print("  [bold]0[/bold]. Back")
    console.print("  [bold]m[/bold]. Main Menu")

    choice = Prompt.ask("Select", choices=["0", "1", "m"], default="0")
    if choice == "0":
        return
    elif choice == "m":
        self._return_to_main = True
        return
```

### GTK Background Thread Pattern
```python
def _service_action(self, action):
    def do_action():
        result = subprocess.run(['sudo', 'systemctl', action, 'meshtasticd'], ...)
        GLib.idle_add(self._action_complete, result)

    thread = threading.Thread(target=do_action, daemon=True)
    thread.start()
```

### Meshtastic CLI Wrapper Pattern
```python
def _run_cli(self, args, timeout=30):
    cli_path = self._find_meshtastic_cli()
    cmd = [cli_path, '--host', 'localhost'] + args
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return result
```

---

## User Preferences (from feedback)

1. **Navigation:** Always include "0. Back" and "m. Main Menu" in every menu
2. **Goodbye message:** "A Hui Hou! Happy meshing!"
3. **No abrupt exits:** Offer back options instead of "Aborted!"
4. **Progress indicators:** Show visual progress during installations
5. **Service management:** Live logs should update and be stoppable

---

## Testing Commands

```bash
# Run each interface
sudo python3 src/main.py
sudo python3 src/main_tui.py
sudo python3 src/main_gtk.py

# Test specific features
sudo python3 -c "from config.radio_config import RadioConfig; r = RadioConfig(); r.interactive_menu()"
sudo python3 -c "from config.hardware_config import HardwareConfigurator; h = HardwareConfigurator(); h.interactive_menu()"
sudo python3 -c "from tools.network_tools import NetworkTools; n = NetworkTools(); n.interactive_menu()"
```

---

## Git Workflow

```bash
# Feature branch
git checkout claude/review-meshtasticd-installer-52ENu

# Check status
git status && git log --oneline -5

# Commit convention
git commit -m "feat: Add new feature"
git commit -m "fix: Fix bug description"
git commit -m "docs: Update documentation"

# Push
git push -u origin claude/review-meshtasticd-installer-52ENu
```

---

## Files Modified Recently

| File | Changes |
|------|---------|
| `src/gtk_ui/panels/radio_config.py` | NEW - GTK Radio Configuration panel |
| `src/gtk_ui/app.py` | Fixed nodes/uptime, added radio_config page |
| `src/gtk_ui/panels/dashboard.py` | Fixed config count (.yaml + .yml) |
| `src/gtk_ui/panels/hardware.py` | Fixed enable buttons, improved detection |
| `src/config/radio_config.py` | Full radio configuration (CLI) |
| `src/config/hardware_config.py` | Hardware configuration (CLI) |
| `src/__version__.py` | Updated to 3.2.2 |
| `RESEARCH.md` | Added Web Client documentation |

---

## Next Steps (When Resuming)

1. Check `git status` for any uncommitted work
2. Review `SESSION_NOTES.md` for recent changes
3. Check if user has reported any new issues
4. Run tests to verify everything works
5. Create PR if not already done

---

## Repository Links

- **GitHub:** https://github.com/Nursedude/Meshtasticd_interactive_UI
- **MUDP Library:** https://github.com/pdxlocations/mudp
- **Meshtastic Docs:** https://meshtastic.org/docs/

---

*Last Updated: 2026-01-02 - v3.2.2*
