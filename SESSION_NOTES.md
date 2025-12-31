# Meshtasticd Installer - Development Session Notes

## Session Date: 2025-12-30/31

### Branch: `claude/review-meshtasticd-installer-52ENu`

---

## Summary of Changes (v3.0.1)

### New Features Added
1. **Launcher Wizard** (`src/launcher.py`)
   - Detects environment (display, SSH, GTK4/Textual availability)
   - Recommends best interface based on environment
   - Offers to install missing dependencies
   - Launches selected UI (GTK4, TUI, or CLI)

### Bug Fixes
1. **GTK4 Log Following** (`src/gtk_ui/panels/service.py`)
   - Fixed `--since` parameter: was `-1h`, now `"1 hour ago"`
   - Added auto-scroll to bottom when logs update

2. **TUI Log Following** (`src/tui/app.py`)
   - Added `start_following()` and `stop_following()` methods
   - Added "Stop Follow" button that toggles with "Follow Logs"
   - Worker refreshes every 2 seconds while following

3. **Channel Configuration** (`src/config/lora.py`)
   - Complete rewrite of `configure_channels()`
   - Now has interactive menu with options 1-5
   - Back (0) and Main Menu (m) options
   - Can configure primary, add secondary, view summary, clear all

4. **pip Install for RPi** (`src/main_tui.py`, `src/main_gtk.py`)
   - Uses `--break-system-packages` flag
   - Offers sudo option for installation
   - Better error messages

5. **Meshtastic CLI Detection**
   - Checks multiple paths: `meshtastic`, `/root/.local/bin/meshtastic`,
     `/home/pi/.local/bin/meshtastic`, `~/.local/bin/meshtastic`
   - Offers to install via pipx if not found

### UI Improvements
1. **Main Menu Shortcuts** (`src/main.py`)
   - Changed exit from `0` to `q`
   - Help is `?`
   - More intuitive keyboard navigation

2. **install.sh Updates**
   - Now launches `launcher.py` by default
   - Creates both `meshtasticd-installer` (wizard) and `meshtasticd-cli` (direct CLI)
   - Shows v3.0.1 in banner

---

## Files Modified

| File | Changes |
|------|---------|
| `src/launcher.py` | **NEW** - Wizard interface selector |
| `src/main.py` | Exit shortcut changed to `q` |
| `src/main_gtk.py` | Added CLI detection with multiple paths |
| `src/main_tui.py` | Added CLI detection, pip `--break-system-packages` |
| `src/tui/app.py` | Log following with start/stop toggle |
| `src/gtk_ui/panels/service.py` | Fixed journalctl, auto-scroll |
| `src/config/lora.py` | Rewrote channel config with back options |
| `src/__version__.py` | Bumped to 3.0.1 with changelog |
| `install.sh` | Uses launcher wizard, creates both commands |
| `README.md` | Updated to v3.0.1 |

---

## Testing Checklist

### Launcher Wizard
- [ ] `sudo meshtasticd-installer` shows wizard
- [ ] Correctly detects display availability
- [ ] Correctly detects GTK4 availability
- [ ] Correctly detects Textual availability
- [ ] Offers to install missing dependencies
- [ ] Launches correct UI when selected

### GTK4 GUI
- [ ] Service panel loads
- [ ] "Fetch Logs" works
- [ ] "Follow" toggle works (updates every 2s)
- [ ] "Since" dropdown filters correctly
- [ ] Logs auto-scroll to bottom

### Textual TUI
- [ ] App launches without errors
- [ ] Service tab works
- [ ] "Follow Logs" / "Stop Follow" toggles
- [ ] Logs refresh while following

### Rich CLI
- [ ] Main menu displays correctly
- [ ] `q` exits
- [ ] `?` shows help
- [ ] Channel config has back options (0, m)
- [ ] All submenus have back navigation

### Meshtastic CLI Detection
- [ ] Detects if CLI is installed
- [ ] Offers to install if missing
- [ ] Works with pipx installation

---

## Known Issues / TODO

1. **Emojis** - May not display correctly on all terminals (low priority)
2. **More menu back options** - Some submenus may still need review

---

## Commands for Testing

```bash
# Switch to feature branch
git checkout claude/review-meshtasticd-installer-52ENu

# Run launcher wizard
sudo python3 src/launcher.py

# Run specific UIs directly
sudo python3 src/main_gtk.py    # GTK4
sudo python3 src/main_tui.py    # Textual TUI
sudo python3 src/main.py        # Rich CLI
```

---

## Git History

```
cf43bd4 v3.0.1: Add launcher wizard, fix logging, improve navigation
58ebf8d docs: Update README for v3.0.0 - GTK4 and Textual TUI
c9e8a8d v3.0.0: GTK4 Graphical UI + Textual TUI for Headless Access (#34)
```

---

## Next Steps (Wednesday)

1. Test all changes on actual Raspberry Pi
2. Review any issues found during testing
3. Merge to main if tests pass
4. Consider additional improvements based on testing notes
