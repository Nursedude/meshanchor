# TUI Menu Redesign - Research & Implementation Plan

> Strategic redesign of MeshForge TUI for intuitive, no-dependency operation with best-of-breed UI patterns.

**Date**: 2026-01-30
**Branch**: `claude/network-topology-enhancement-BRXEF`
**Status**: Research Complete, Ready for Implementation

---

## 1. Research Summary

### 1.1 UI/UX Best Practices (Industry Sources)

#### Apple Human Interface Guidelines
- **Clarity**: Interface should be legible and easy to understand
- **Hierarchy**: Organize visual elements to prioritize important info/actions
- **Menu Organization**: Order reflects natural hierarchy of objects
  - Higher-level, universal items → left/top
  - More specific entities → right/bottom
- **Depth Limit**: Main functions accessible in ≤2 navigations from home
- **Consistency**: Users shouldn't have to re-learn patterns

#### Raspberry Pi raspi-config Patterns
- **Prefixed Items**: `S1 Wireless LAN`, `D1 Resolution` - numbered for quick selection
- **Consistent Navigation**: `--cancel-button Back --ok-button Select`
- **Flat Where Possible**: Minimize submenu depth
- **Action Verbs**: "Enable", "Configure", "View" - clear intent
- **Status Indicators**: Show current state inline where feasible

#### Modern TUI Best Practices (2025-2026)
- **Command Palette**: Quick access via Ctrl+X or `/` (for power users)
- **Status Bar**: Persistent info at top/bottom (MeshForge already has this)
- **Progress Feedback**: Essential for long-running operations
- **Frame Views**: Container views with title/border for grouping
- **Resource Efficiency**: TUIs excel on low-power devices (Pi target)
- **Keyboard First**: Every action reachable via keyboard

### 1.2 Current MeshForge TUI Analysis

#### Current Main Menu (19 items)
```
status      - Status Overview
quick       - Quick Actions (shortcuts)
logs        - Logs (live follow, errors, analysis)
network     - Network & Ports
radio       - Radio (meshtastic CLI)
services    - Services (start/stop/restart)
emcomm      - EMERGENCY MODE (field ops)
rns         - RNS / Reticulum
aredn       - AREDN Mesh
metrics     - Historical Metrics & Trends
rf          - RF Tools & Calculator
sdr         - RF Awareness (SDR Monitoring)
maps        - Maps & Coverage
config      - Configuration
hardware    - Hardware Detection
system      - System Tools (full Linux CLI)
web         - Web Client URL
about       - About
quit        - Exit
```

#### Problems Identified

1. **Too Many Top-Level Items** (19)
   - Cognitive overload
   - Violates "7±2" rule for menu items
   - Hard to scan quickly

2. **No Clear Categories**
   - Comments exist (`# Monitor`, `# Operate`) but not visible to user
   - Related items scattered (radio, config, channels in different places)

3. **Inconsistent Naming**
   - Some use nouns: "Status Overview", "Configuration"
   - Some use verbs: "Exit"
   - Some have parenthetical help, some don't

4. **Deep Nesting in Some Areas**
   - RNS menu has 15 items with further submenus
   - Config menu leads to multiple levels

5. **No Startup Environment Check**
   - Assumes services exist
   - No port conflict detection
   - No first-run guidance

---

## 2. Proposed Menu Hierarchy

### Design Principles Applied
1. **Max 7-9 items** per menu level
2. **Verb-first naming** for actions, Noun for views
3. **Grouped by user task**, not technical domain
4. **Status visible** at launch without navigation
5. **2-tap max** for common operations

### New Main Menu Structure

```
┌─────────────────────────────────────────────────────────┐
│           MeshForge NOC v0.4.8-alpha                    │
├─────────────────────────────────────────────────────────┤
│  Services: meshtasticd ● rnsd ○ (conflicts: none)       │
├─────────────────────────────────────────────────────────┤
│                                                         │
│  1. Dashboard          View status, health, alerts      │
│  2. Mesh Networks      Meshtastic, RNS, AREDN           │
│  3. RF & SDR           Calculators, SDR monitoring      │
│  4. Maps & Viz         Coverage maps, topology          │
│  5. Configuration      Radio, services, MeshForge       │
│  6. System             Hardware, logs, Linux tools      │
│  ─────────────────────────────────────────────────────  │
│  Q. Quick Actions      Common shortcuts                 │
│  E. Emergency Mode     Field operations                 │
│  ─────────────────────────────────────────────────────  │
│  A. About              Version, help, web client        │
│  X. Exit                                                │
│                                                         │
└─────────────────────────────────────────────────────────┘
```

### Submenu: Dashboard (1)
```
1. Service Status        All services with health
2. Network Status        Ports, interfaces, conflicts
3. Node Count           Meshtastic + RNS nodes
4. Historical Trends    Metrics over time
5. Back
```

### Submenu: Mesh Networks (2)
```
1. Meshtastic           Radio config, channels, CLI
2. RNS / Reticulum      rnsd, paths, gateway bridge
3. AREDN                AREDN mesh integration
4. Back
```

### Submenu: Meshtastic (2→1)
```
1. Radio Info           Current device info
2. Channels             View/edit channels
3. LoRa Presets         Modem configuration
4. Position             GPS settings
5. CLI Commands         Direct meshtastic CLI
6. Back
```

### Submenu: RNS (2→2)
```
1. Status               rnstatus, rnpath
2. Topology             Network graph view
3. Link Quality         Quality analysis
4. Gateway Bridge       Start/stop bridge
5. NomadNet             Client access
6. Interfaces           Manage RNS interfaces
7. Configuration        View/edit config
8. Back
```

### Submenu: RF & SDR (3)
```
1. Link Budget          FSPL, Fresnel, range
2. Site Planner         Coverage estimation
3. Frequency Slots      Channel calculator
4. SDR Monitor          RF awareness (Airspy)
5. Back
```

### Submenu: Maps & Viz (4)
```
1. Coverage Map         Generate coverage map
2. Network Topology     D3.js graph view
3. Node Map             All nodes on map
4. Export Data          GeoJSON, CSV, GraphML
5. Back
```

### Submenu: Configuration (5)
```
1. Radio Config         meshtasticd settings
2. Service Config       systemd services
3. MeshForge Settings   App preferences
4. Setup Wizard         First-run wizard
5. Back
```

### Submenu: System (6)
```
1. Hardware             Detect SPI/I2C/USB
2. Logs                 View/follow logs
3. Network Tools        Ping, ports, interfaces
4. Linux Shell          Drop to bash
5. Reboot/Shutdown      Safe system control
6. Back
```

---

## 3. No-Dependencies Startup

### Current Problem
MeshForge currently:
- Tries to connect to services immediately
- Shows errors if meshtasticd/rnsd not running
- No clear guidance on what's missing

### Proposed Solution

#### Phase 1: Environment Detection (on startup)
```python
def _detect_environment(self):
    """Detect services, ports, and conflicts at startup."""
    return {
        'services': {
            'meshtasticd': self._check_service_state('meshtasticd'),
            'rnsd': self._check_service_state('rnsd'),
        },
        'ports': {
            4403: self._check_port_owner(4403),  # meshtasticd TCP
            37428: self._check_port_owner(37428),  # rnsd UDP
        },
        'conflicts': self._detect_port_conflicts(),
        'hardware': {
            'spi': self._check_spi_available(),
            'usb_serial': self._find_usb_serial_devices(),
        },
        'first_run': not self._config_exists(),
    }
```

#### Phase 2: Status Display in Header
```
┌─────────────────────────────────────────────────────────┐
│ MeshForge NOC v0.4.8-alpha           [No root - limited]│
├─────────────────────────────────────────────────────────┤
│ Services:  meshtasticd ● running   rnsd ○ stopped       │
│ Hardware:  SPI ○  USB ●(/dev/ttyACM0)                   │
│ Alerts:    Port 4403 conflict (NomadNet using it)       │
├─────────────────────────────────────────────────────────┤
```

#### Phase 3: Conflict Resolution
When conflict detected:
```
┌─────────────────────────────────────────────────────────┐
│                  Port Conflict Detected                 │
├─────────────────────────────────────────────────────────┤
│ Port 4403 is in use by: NomadNet (PID 12345)            │
│                                                         │
│ Options:                                                │
│ 1. Stop NomadNet and continue                           │
│ 2. Use different port for MeshForge                     │
│ 3. Continue anyway (may cause errors)                   │
│ 4. Exit and resolve manually                            │
└─────────────────────────────────────────────────────────┘
```

---

## 4. First-Run Wizard

### Trigger Conditions
- No `~/.config/meshforge/settings.json` exists
- User explicitly selects "Setup Wizard"
- `--wizard` flag passed

### Wizard Flow

```
Step 1: Connection Type
┌─────────────────────────────────────────────────────────┐
│              MeshForge First-Run Setup                  │
├─────────────────────────────────────────────────────────┤
│ How is your Meshtastic device connected?                │
│                                                         │
│ 1. SPI HAT (MeshAdv-Mini, Waveshare, etc.)             │
│ 2. USB Serial (T-Beam, Heltec, etc.)                   │
│ 3. Network (remote meshtasticd)                        │
│ 4. None yet (configure later)                          │
└─────────────────────────────────────────────────────────┘

Step 2a: SPI HAT Selection (if SPI chosen)
┌─────────────────────────────────────────────────────────┐
│                  Select Your Hardware                   │
├─────────────────────────────────────────────────────────┤
│ Which SPI device are you using?                         │
│                                                         │
│ 1. MeshAdv-Mini (recommended for Pi)                   │
│ 2. Waveshare SX1262 HAT                                │
│ 3. Ebyte E22 Module                                    │
│ 4. Custom / Other                                       │
└─────────────────────────────────────────────────────────┘

Step 2b: USB Detection (if USB chosen)
┌─────────────────────────────────────────────────────────┐
│                 USB Device Detection                    │
├─────────────────────────────────────────────────────────┤
│ Found USB serial devices:                               │
│                                                         │
│ 1. /dev/ttyACM0 - Meshtastic T-Beam                    │
│ 2. /dev/ttyUSB0 - Unknown CP2102                       │
│                                                         │
│ Select device or 0 to scan again:                       │
└─────────────────────────────────────────────────────────┘

Step 3: Region Selection
┌─────────────────────────────────────────────────────────┐
│                  Region Configuration                   │
├─────────────────────────────────────────────────────────┤
│ Select your regulatory region:                          │
│                                                         │
│ 1. US (915 MHz)                                        │
│ 2. EU_868 (868 MHz)                                    │
│ 3. ANZ (915/928 MHz)                                   │
│ ... (more regions)                                      │
└─────────────────────────────────────────────────────────┘

Step 4: Service Setup
┌─────────────────────────────────────────────────────────┐
│                  Service Configuration                  │
├─────────────────────────────────────────────────────────┤
│ Enable services to start automatically?                 │
│                                                         │
│ [x] meshtasticd - Start at boot                        │
│ [ ] rnsd - Start at boot (optional)                    │
│                                                         │
│ Note: You can change this later in Configuration.       │
└─────────────────────────────────────────────────────────┘

Step 5: Summary
┌─────────────────────────────────────────────────────────┐
│                    Setup Complete                       │
├─────────────────────────────────────────────────────────┤
│ Configuration saved:                                    │
│                                                         │
│ • Device: MeshAdv-Mini (SPI)                           │
│ • Region: US                                           │
│ • meshtasticd: enabled at boot                         │
│                                                         │
│ Config written to:                                      │
│   /etc/meshtasticd/config.d/meshforge.yaml             │
│                                                         │
│ Press Enter to start MeshForge...                       │
└─────────────────────────────────────────────────────────┘
```

---

## 5. Implementation Task List

### Phase 1: Foundation (Required First)
1. [ ] **Create `startup_checks.py` module**
   - Environment detection
   - Port conflict detection
   - Service state checking
   - Hardware detection

2. [ ] **Update status bar to show live status**
   - Services (running/stopped/failed)
   - Conflicts if any
   - Root/non-root indicator

3. [ ] **Create port conflict resolver**
   - Identify process using port
   - Offer stop/change/continue options
   - Log resolution actions

### Phase 2: First-Run Wizard
4. [ ] **Enhance `first_run_mixin.py`**
   - SPI vs USB vs Network selection
   - Hardware-specific config templates
   - Region selection
   - Service enable/disable

5. [ ] **Create hardware config templates**
   - MeshAdv-Mini SPI config
   - USB serial config
   - Network client config

### Phase 3: Menu Restructure
6. [ ] **Refactor `_run_main_menu()` in main.py**
   - Reduce to 8-10 items max
   - Add numbered shortcuts
   - Implement new hierarchy

7. [ ] **Create new submenu structure**
   - Dashboard submenu
   - Mesh Networks submenu (combining radio, rns, aredn)
   - RF & SDR submenu
   - Maps & Viz submenu
   - Consolidated config submenu

8. [ ] **Update navigation**
   - Consistent Back behavior
   - Breadcrumb in title (optional)
   - Quick jump with number keys

### Phase 4: Testing & Polish
9. [ ] **Test on target hardware**
   - Raspberry Pi over SSH
   - Local terminal
   - Various terminal sizes

10. [ ] **Update documentation**
    - Menu reference
    - First-run guide
    - Troubleshooting

---

## 6. Files to Modify

| File | Changes |
|------|---------|
| `launcher_tui/main.py` | Main menu restructure, startup checks |
| `launcher_tui/first_run_mixin.py` | Enhanced wizard |
| `launcher_tui/status_bar.py` | Live status display |
| NEW: `launcher_tui/startup_checks.py` | Environment detection |
| NEW: `launcher_tui/conflict_resolver.py` | Port conflict handling |
| NEW: `utils/hardware_templates.py` | Config templates for known hardware |

---

## 7. Success Criteria

- [ ] Main menu has ≤10 items
- [ ] Any common action reachable in ≤2 navigations
- [ ] Startup shows service/port status immediately
- [ ] Port conflicts detected and resolution offered
- [ ] First-run wizard guides SPI/USB setup
- [ ] Works on Pi Zero over SSH (low bandwidth)
- [ ] All tests pass

---

## References

- [Apple Human Interface Guidelines - Menus](https://developer.apple.com/design/human-interface-guidelines/menus)
- [raspi-config source](https://github.com/RPi-Distro/raspi-config/blob/master/raspi-config)
- [CLI UX Best Practices - Evil Martians](https://evilmartians.com/chronicles/cli-ux-best-practices-3-patterns-for-improving-progress-displays)
- [awesome-tuis](https://github.com/rothgar/awesome-tuis)
- [Whiptail Linux Magazine](https://www.linux-magazine.com/Issues/2023/270/Whiptail)

---

*Research complete. Ready for implementation.*
