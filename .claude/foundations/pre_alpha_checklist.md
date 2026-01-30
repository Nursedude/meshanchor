# MeshForge Pre-Alpha Checklist

> **Purpose**: Track work required for alpha release
> **Created**: 2026-01-30
> **Status**: In Progress
> **Target**: Raspberry Pi 4/5
> **Timeline**: When ready
> **Required Services**: meshtasticd, rnsd
> **Optional Services**: MQTT, HamClock

---

## Session Workflow

Each session should:
1. Pick a category/task from this checklist
2. Mark task as `[~]` (in progress)
3. Complete and verify the work
4. Mark as `[x]` (done) and commit
5. Watch for session entropy - make notes and start fresh if needed

---

## Critical Bugs

### Fixed
- [x] **Export topology bug** - `main.py:692` created empty TopologyVisualizer (Session 2026-01-30)

### Pending
- [ ] **MF004 subprocess timeout** - `main.py:804` interactive shell without timeout (low priority - acceptable for interactive)

---

## TUI Feature Completeness

### Core Menus (Must Work)
| Menu | Status | Notes |
|------|--------|-------|
| Dashboard | [ ] | Verify node list, status display |
| Mesh Networks | [ ] | Meshtastic + RNS submenus |
| RF & SDR Tools | [ ] | RF calculator, site planner |
| Maps & Visualization | [ ] | Map server, topology viz, exports |
| Configuration | [ ] | Settings, channel config |
| System & Logs | [ ] | Service control, logs |

### Feature Verification Checklist
Each feature needs testing on target hardware:

#### Dashboard
- [ ] Shows connected node count
- [ ] Shows service status (meshtasticd, rnsd)
- [ ] Quick actions work
- [ ] Refresh updates data

#### Meshtastic
- [ ] Radio menu opens
- [ ] Node list displays
- [ ] Send message works (if device connected)
- [ ] Channel config loads/saves

#### RNS (Reticulum)
- [ ] RNS menu opens
- [ ] Interface list displays
- [ ] NomadNet integration works
- [ ] Path discovery works

#### Maps & Visualization
- [ ] Map server starts
- [ ] Browser opens map
- [ ] Topology view renders
- [ ] **Export works with real data** (fixed 2026-01-30)

#### Configuration
- [ ] Settings load correctly
- [ ] Settings save to correct location (not /root)
- [ ] First-run wizard triggers on fresh install

#### Services
- [ ] Service status detection accurate
- [ ] Start/stop services works (with sudo)
- [ ] Service logs viewable

---

## Missing Features (By Priority)

### High Priority - Should have for Alpha
| Feature | Module Exists | TUI Accessible | Session |
|---------|---------------|----------------|---------|
| Device Backup/Restore | `commands/device_backup.py` | [ ] | |
| Message History | `commands/messaging.py` | [ ] | |
| Network Health Score | `utils/network_health.py` | [ ] | |

### Medium Priority - Nice to have
| Feature | Module Exists | TUI Accessible | Session |
|---------|---------------|----------------|---------|
| HamClock Space Weather | `gtk_ui/panels/hamclock.py` | [ ] | |
| Firmware Update | `utils/firmware_flasher.py` | [ ] | |
| RNode Configuration | `commands/rnode.py` | [ ] | |
| Predictive Maintenance | `utils/predictive_maintenance.py` | [ ] | |

### Low Priority - Post-Alpha
| Feature | Module Exists | TUI Accessible | Session |
|---------|---------------|----------------|---------|
| Full Hardware Detection | `utils/hardware_*.py` | Partial | |
| AREDN Integration | `launcher_tui/aredn_mixin.py` | [ ] | |
| Advanced Diagnostics | `utils/diagnostic_engine.py` | Partial | |

---

## Installation & First Run

### Fresh Install Test
- [ ] Clone repo on fresh Pi
- [ ] Run install script
- [ ] TUI launches without errors
- [ ] First-run wizard appears
- [ ] Settings saved to ~/.config/meshforge/

### Dependencies Check
- [ ] whiptail/dialog available
- [ ] Python 3.9+ available
- [ ] Required packages install correctly

---

## Service Integration

### Required Services (Alpha)
| Service | Detection | Control | Verified | Notes |
|---------|-----------|---------|----------|-------|
| meshtasticd | systemctl | Start/Stop | [ ] | Single client limitation |
| rnsd | Port 37428 | Start/Stop | [ ] | RNS shared instance |

### Optional Services
| Service | Detection | Control | Verified | Notes |
|---------|-----------|---------|----------|-------|
| nomadnet | Port check | Start/Stop | [ ] | RNS messaging |
| MQTT | Port 1883 | External | [ ] | Monitoring |
| HamClock | Port 8080 | External | [ ] | Space weather |

---

## Known Limitations (Document for Users)

1. **WebKit disabled with sudo** - Browser fallback always used
2. **Meshtasticd single client** - Only one TCP connection at a time
3. **Root required** - Many features need sudo for hardware access
4. **Path.home() bug** - Fixed in code, verify no regressions

---

## Test Scenarios

### Scenario 1: Standalone RF Tools (No Hardware)
```
1. Launch TUI without any devices
2. Open RF & SDR Tools
3. Run RF calculator
4. Verify calculations display
```
Expected: Works offline, no errors

### Scenario 2: Meshtastic Connected
```
1. Connect USB Meshtastic device
2. Launch TUI with sudo
3. Open Mesh Networks > Meshtastic
4. View node list
5. Send test message
```
Expected: Shows nodes, message sends

### Scenario 3: Gateway Mode
```
1. Start meshtasticd and rnsd
2. Launch TUI
3. Open Dashboard
4. Verify both networks visible
5. Export topology
```
Expected: Shows unified topology, export contains data

---

## Acceptance Criteria for Alpha

### Must Pass
- [ ] TUI launches on Raspberry Pi 4/5
- [ ] All core menus accessible
- [ ] No crashes on basic navigation
- [ ] Export produces valid files with data
- [ ] Settings persist between sessions
- [ ] Service status accurate

### Should Pass
- [ ] First-run wizard helpful
- [ ] Error messages actionable
- [ ] Documentation accurate

---

## Session Log

| Date | Session ID | Work Done | Notes |
|------|------------|-----------|-------|
| 2026-01-30 | KMjRH | Fixed export bug, created checklist | |

---

## Notes for Next Session

- pytest not installed in environment - tests can't run
- Linter only found 1 warning (MF004 on interactive shell)
- 55+ utility modules exist; many not exposed in TUI
- Previous session work was on different branch (export-modules-docs-i4jRW)
