# Meshtasticd Installer - Development Session Notes

## Session Date: 2025-12-31 (v3.0.3)

### Branch: `claude/review-meshtasticd-installer-52ENu`
### PR: https://github.com/Nursedude/Meshtasticd_interactive_UI/pull/37

---

## PERPETUAL MEMORY - Pick Up Here

### ✅ COMPLETED This Session (v3.0.3)

1. **Edit Existing Channels** (`src/config/lora.py`)
   - New menu option "Edit Existing Channel"
   - Pre-fills current values when editing
   - Shows [current] markers on role options
   - "Keep current PSK" option when editing

2. **Consistent Menu Navigation**
   - All menus now use `m` for Main Menu
   - All menus have `0` for Back
   - Region selection updated with back/menu options
   - Channel config role/PSK selections have proper navigation

3. **Improved Emoji Detection** (`src/utils/emoji.py`)
   - Better SSH terminal detection
   - Checks locale (LANG, LC_ALL, LC_CTYPE) for UTF-8
   - Modern terminals (256color, xterm) get emojis
   - Still respects ENABLE_EMOJI/DISABLE_EMOJI env vars

4. **Launcher Saves UI Preference** (`src/launcher.py`)
   - Saves to ~/.config/meshtasticd-installer/preferences.json
   - Auto-launches saved preference (with dependency check)
   - Press 's' to save preference, 'c' to clear
   - Use `--wizard` flag to force wizard and reset
   - Shows [saved] marker on saved preference in menu
   - Updated version display to v3.0.3

### ✅ COMPLETED Previously (v3.0.2)

1. **Modem Presets Updated** - SHORT_TURBO added, Fastest→Slowest order
2. **Channel Configuration Saves** - meshtastic CLI integration
3. **Auto-Install Meshtastic CLI** - via pipx with PATH auto-add
4. **PSK Key Generation** - 256-bit, 128-bit, custom, none options
5. **MQTT Settings** - uplink/downlink per channel
6. **Position Precision** - location sharing accuracy settings
7. **Live Log Exit Fixed** - Popen with proper terminate()

### ⏳ STILL PENDING

1. ~~**UI Selection Not Working**~~ - ✅ FIXED: Launcher now saves preference
2. **Add Uninstaller Option** - Create uninstall functionality
3. **Progress Indicators** - Show progress during installs/updates
4. **Device Configuration Wizard** - May need more back options

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
