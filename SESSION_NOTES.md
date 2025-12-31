# Meshtasticd Installer - Development Session Notes

## Session Date: 2025-12-30/31 (Updated)

### Branch: `claude/review-meshtasticd-installer-52ENu`
### PR: https://github.com/Nursedude/Meshtasticd_interactive_UI/pull/37

---

## PERPETUAL MEMORY - Pick Up Here

### ✅ COMPLETED This Session

1. **Modem Presets Updated** (`src/config/lora.py`)
   - Added SHORT_TURBO with legal warning (500kHz)
   - Reordered: Fastest → Slowest (official Meshtastic order)
   - Order: SHORT_TURBO → SHORT_FAST → SHORT_SLOW → MEDIUM_FAST → MEDIUM_SLOW → LONG_FAST → LONG_MODERATE → LONG_SLOW → VERY_LONG_SLOW
   - Added back options (0, m) to preset selection

2. **Channel Configuration Rewritten** (`src/config/lora.py`)
   - Full interactive menu with back navigation
   - Options: Primary, Secondary, View, Clear, Save
   - Back (0) and Main Menu (m) options

3. **Goodbye Message Changed**
   - Now says "A Hui Hou! Happy meshing!" (was "Goodbye!")

4. **Launcher Wizard** (`src/launcher.py`)
   - Environment detection (display, GTK4, Textual)
   - Interface selection with recommendations

5. **Log Following Fixed**
   - GTK4: Fixed journalctl --since format, auto-scroll
   - TUI: Added start/stop toggle

6. **README.md Merge Conflict Resolved**
   - Combined v3.0.1 and v3.0.0 changes from both branches
   - Removed MeshAdv-Mini 400MHz variant from templates list
   - Updated project structure with launcher.py
   - Kept all installation options (Web, Docker, Manual)

7. **Channel Configuration Now Saves!** (`src/config/lora.py`)
   - Reads existing channels from device via `meshtastic --info`
   - "Apply & Save to Device" runs `meshtastic --ch-set` commands
   - "Refresh from Device" re-reads current configuration
   - No longer requires primary channel if one already exists
   - Added tip about using meshtastic CLI or http://client.meshtastic.org

8. **Live Log Exit Fixed** (`src/services/service_manager.py`)
   - Clearer exit instructions (Ctrl+C/Ctrl+Z)
   - Uses Popen instead of run() for better interrupt handling
   - Properly terminates process on exit

### ⏳ STILL PENDING (For Wednesday)

1. **Add back options to ALL menus** - Many submenus still missing
   - Region selection (line-by-line, no back)
   - Device configuration wizard
   - Template manager
   - Check all Prompt.ask() calls

2. ~~**Remove MeshAdv-Mini 400MHz variant**~~ - ✅ DONE

3. ~~**Service Management Live Logs**~~ - ✅ FIXED (clearer exit, Popen)

4. **UI Selection Not Working** - "Same look every time" - investigate launcher

5. **Add Uninstaller Option** - Create uninstall functionality

6. **Progress Indicators** - Show progress during installs/updates

---

## User's Exact Feedback (Verbatim)

```
- always have a back option and back to main option in a menu
- verify UI interface is working as expected
- pip install --break-system-packages textual for RPI
- provide sudo as an option when you have pip install textual
- check and verify if the meshtastic cli is installed
- emojis not working (less priority)
- error checking and version control, test and push to repo

PR #36 issues:
- Presets: SHORT_TURBO, SHORT_FAST, SHORT_SLOW, MEDIUM_FAST, MEDIUM_SLOW,
  LONG_FAST (Default), LONG_MODERATE, LONG_SLOW, VERY_LONG_SLOW
- Channel Configuration should be fully configurable
- offer a back out quit instead of Aborted!
- remove MeshAdv-Mini 400MHz variant
- back button/main menu in every window
- show progress of installs/updates
- Region selection needs back option
- goodbye should say "A Hui Hou! Happy meshing!"
- Service Management live logs not updating, can't quit
- UI selection not working (same look every time)
- have an uninstaller option
```

---

## Files Modified This Session

| File | Changes |
|------|---------|
| `src/launcher.py` | NEW - Wizard interface selector |
| `src/main.py` | Exit=q, goodbye="A Hui Hou!" |
| `src/main_gtk.py` | CLI detection |
| `src/main_tui.py` | CLI detection, pip --break-system-packages |
| `src/tui/app.py` | Log following toggle |
| `src/gtk_ui/panels/service.py` | Fixed journalctl, auto-scroll |
| `src/config/lora.py` | **MAJOR**: Presets reordered, SHORT_TURBO added, channel config rewrite |
| `src/__version__.py` | v3.0.1 |
| `install.sh` | Launcher wizard default |
| `README.md` | v3.0.1 |

---

## Code Locations for Pending Work

### Back Options Needed
- `src/config/device.py` - Device configuration wizard
- `src/config/lora.py:configure_region()` - Region selection
- `src/installer/meshtasticd.py` - Install process
- Search: `Prompt.ask` without choices including "0" or "m"

### MeshAdv-Mini 400MHz
- Search for "400MHz" or "MeshAdv-Mini 400" in templates/

### Live Logs Fix
- `src/services/service_manager.py` - Rich CLI service menu
- `src/gtk_ui/panels/service.py` - GTK4 logs (partially fixed)
- `src/tui/app.py` - TUI logs (partially fixed)

### Uninstaller
- Create `src/installer/uninstaller.py`
- Add option to main menu

---

## Testing Commands

```bash
# Switch to feature branch
git checkout claude/review-meshtasticd-installer-52ENu

# Test launcher wizard
sudo python3 src/launcher.py

# Test specific UIs
sudo python3 src/main_gtk.py    # GTK4
sudo python3 src/main_tui.py    # Textual TUI
sudo python3 src/main.py        # Rich CLI

# Test modem preset selection
# In Rich CLI: 6 → Channel Presets → should show new order

# Test channel config
# In Rich CLI: 5 → Configure device → should have back options
```

---

## Git Status

```bash
# Current branch
claude/review-meshtasticd-installer-52ENu

# Last commits (as of 2025-12-31)
c849a73 fix: Channel config now detects existing channels and saves to device
6e13f8f docs: Update session notes - PR #37 pushed and ready
a03358f docs: Update session notes with merge conflict resolution
908f4a1 fix: Resolve merge conflict in README.md
740bdf3 v3.0.2: Fix modem presets, add SHORT_TURBO, update goodbye message

# PR Status: ✅ PUSHED & READY FOR MERGE - Channel config + live log fixes
```

---

## Version History

- **v3.0.1** (2025-12-30) - Launcher wizard, bug fixes, navigation improvements
- **v3.0.0** (2025-12-30) - GTK4 GUI, Textual TUI, Config File Manager
- **v2.3.0** - Config File Manager
- **v2.2.0** - Service management, meshtastic CLI

---

## Contact / Collaboration

- GitHub: https://github.com/Nursedude/Meshtasticd_interactive_UI
- Branch: claude/review-meshtasticd-installer-52ENu
- PR #37: ✅ Pushed & ready for merge (conflicts resolved)

---

## Resume Instructions

When resuming:
1. `git checkout claude/review-meshtasticd-installer-52ENu`
2. `git status` to see any uncommitted work
3. Review "STILL PENDING" section above
4. Check user's testing notes
5. Continue with pending items
