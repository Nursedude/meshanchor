# Session Notes: Alpha Reliability - Verify Install Integration

**Date**: 2026-01-31
**Branch**: `claude/alpha-reliability-planning-Y5Csz`
**Version**: `0.4.8-alpha`

---

## Summary

Added `--verify-install` flag to launcher.py that integrates with the existing comprehensive bash verification script. This closes the gap identified in the install reliability triage where the script existed but had no Python/CLI integration.

---

## Commit This Session

| File | Change |
|------|--------|
| `src/launcher.py` | Added `--verify-install` / `--verify` flag handling |

---

## What Was Done

### 1. Explored Existing Infrastructure

Found that MeshForge has solid verification infrastructure:

| Component | Location | Purpose |
|-----------|----------|---------|
| `verify_post_install.sh` | scripts/ | Comprehensive 6-section bash verification (430 lines) |
| `StartupChecker` | launcher_tui/startup_checks.py | Python environment state detection |
| `startup_health.py` | utils/ | Lightweight health summary |
| `service_check.py` | utils/ | Core service verification (authoritative) |

### 2. Added --verify-install to launcher.py

```bash
# Now works:
sudo python3 src/launcher.py --verify-install
sudo python3 src/launcher.py --verify-install --quiet
sudo python3 src/launcher.py --verify-install --json
```

**Implementation**:
- Primary: Calls existing `scripts/verify_post_install.sh`
- Fallback: Uses Python `StartupChecker` if bash script not found
- Exit codes: 0 (pass), 1 (critical fail), 2 (warnings)

### 3. Identified Fragmentation Issue

Found 15+ places with direct `pgrep`/`systemctl` calls instead of using centralized `service_check.py`:

```
src/launcher_tui/service_menu_mixin.py (2 places)
src/launcher_tui/rns_menu_mixin.py
src/launcher_tui/nomadnet_client_mixin.py (2 places)
src/commands/rns.py
src/utils/gateway_diagnostic.py (3 places)
src/utils/startup_health.py
src/utils/network_diagnostics.py
src/installer/meshtasticd.py
```

This causes the symptom from triage: "Service shows running in one UI, stopped in another"

---

## What Bash Script Checks

The existing `verify_post_install.sh` checks:

1. **MeshForge Installation**
   - /opt/meshforge directory
   - meshforge command in PATH
   - Python venv

2. **meshtasticd Installation**
   - Native binary or Python CLI
   - systemd service file

3. **meshtasticd Configuration** (CRITICAL)
   - config.yaml exists
   - Webserver section present (port 9443)
   - Lora section present
   - HAT templates available
   - Active HAT config (for SPI)

4. **Service Status**
   - meshtasticd running
   - Port 4403 listening
   - rnsd running

5. **Hardware Detection**
   - SPI devices
   - USB serial devices
   - udev rules

6. **Network Connectivity**
   - Web client (9443) responds
   - meshtasticd TCP (4403) accepts
   - Internet connectivity

---

## Next Steps for Alpha Reliability

### Priority 1: Unify Service Checking (HIGH)
Refactor all service checks to use `utils/service_check.py`:
- Create wrapper functions for common patterns
- Update all mixins and utilities
- This will fix "service shows different status" bug

### Priority 2: Test Core TUI Menus (MEDIUM)
Walk through each menu on target hardware:
- Dashboard shows nodes/status
- Meshtastic radio menu opens
- RNS interface list displays
- Map server starts
- Settings persist correctly

### Priority 3: Pre-Alpha Checklist (MEDIUM)
Complete items in `.claude/foundations/pre_alpha_checklist.md`:
- Missing features: Device Backup, Message History, Network Health Score
- Fresh install test scenarios
- Service integration verification

### Priority 4: Add A-index to Band Conditions (LOW)
From previous session - A-index is fetched but not used in `assess_band_conditions()`.

---

## Files Reference

| File | Purpose | Lines |
|------|---------|-------|
| `verify_post_install.sh` | Comprehensive bash verification | 430 |
| `startup_checks.py` | Python environment detection | 550 |
| `startup_health.py` | Lightweight health summary | ~200 |
| `service_check.py` | Core service verification | ~400 |

---

## Technical Notes

### Exit Code Contract
```
0 = All checks passed
1 = Critical failures (won't work)
2 = Warnings (may work but needs attention)
```

### Path Resolution
```python
# From launcher.py (in src/)
script_path = Path(__file__).parent.parent / 'scripts' / 'verify_post_install.sh'
```

---

## Session Entropy Notes

- Stopped before larger refactor (service unification)
- Each fix is small and isolated
- No breaking changes in this session
- Fragmentation fix should be separate session

---

*Session ID: claude/alpha-reliability-planning-Y5Csz*
