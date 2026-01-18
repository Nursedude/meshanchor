# MeshForge UI Design Decisions

> **Status:** ACTIVE - This document guides all UI development
> **Date:** 2026-01-18
> **Authors:** WH6GXZ (Nursedude) + Dude AI

## Core Principle

**The UI must make sense.** Users should always know:
- Where they are
- How to go back
- What happens next

No Ctrl+C to escape. Every menu has a cancel/back option.

---

## UI Strategy

### Primary Interfaces by Environment

| Environment | Primary UI | Why |
|-------------|-----------|-----|
| **Headless (SSH/Pi)** | Launcher TUI (dialog) | Reliable, works as root, raspi-config familiar |
| **Desktop** | GTK Desktop | Maps, visual monitoring |
| **Advanced/Scripting** | CLI commands | `meshtastic`, `rnsd`, direct control |

### UI Status

| UI | Status | Action |
|----|--------|--------|
| **Launcher TUI** | C - Core | Primary CLI interface, enhance navigation |
| **GTK Desktop** | N - Maintenance | Keep working, stop adding features |
| **Textual TUI** | N - Nice | Lower priority than Launcher TUI |
| **Rich CLI configs** | N - Advanced | Keep for power users, add questionary for escape handling |
| **Web UI** | X - Cut | Don't invest time here |

---

## First-Run Experience

### After Install Completes

```
╔══════════════════════════════════════════════════════════════╗
║                    MeshForge Installed                       ║
╠══════════════════════════════════════════════════════════════╣
║                                                              ║
║  Installation complete!                                      ║
║                                                              ║
║  What would you like to do?                                  ║
║                                                              ║
║    1. Run Setup Wizard (recommended for new installs)        ║
║    2. Launch MeshForge                                       ║
║    3. Exit (run 'meshforge' later)                           ║
║                                                              ║
╚══════════════════════════════════════════════════════════════╝
```

### Setup Wizard Flow

```
1. Environment Detection
   └── Headless? → Launcher TUI
   └── Desktop?  → Offer GTK or TUI choice

2. Hardware Detection
   └── Detect SPI devices
   └── Identify HAT type (if possible)
   └── Offer hardware config selection

3. Node Identity
   └── Set Owner Name (long name)
   └── Set Short Name (4 char)

4. Region Selection
   └── Select regulatory region (US, EU, etc.)

5. Radio Preset
   └── Choose modem preset (LONG_FAST default)
   └── Explain tradeoffs

6. Channel Setup
   └── Primary channel name
   └── PSK (generate or use default)

7. Service Start
   └── Start meshtasticd?
   └── Enable on boot?

8. Complete
   └── Summary of configuration
   └── "Run 'meshforge' to access full interface"
```

---

## Menu Navigation Standard

### Every Menu Must Have

1. **Clear title** - Where am I?
2. **Back/Cancel option** - Always visible, always works
3. **Keyboard shortcuts** - q=quit, b=back, Enter=select

### Dialog Menu Template

```
┌─────────── Menu Title ───────────┐
│                                  │
│  Description of what this does   │
│                                  │
│  ○ Option 1                      │
│  ○ Option 2                      │
│  ○ Option 3                      │
│  ────────────────────────────    │
│  ○ Back                          │
│                                  │
│     < Cancel >    < OK >         │
└──────────────────────────────────┘
```

### Input Prompt Template

For Y/N questions, always accept: y, n, c (cancel), q (quit), b (back)

```
Configure custom channel slot? [y/n/c] (n):
  y = yes, n = no, c = cancel/back
```

---

## Feature Scope

### Core (Must Work Perfectly)

| Feature | Component | Status |
|---------|-----------|--------|
| Meshtastic ↔ RNS Bridge | `gateway/rns_bridge.py` | Working |
| Node Tracking | `gateway/node_tracker.py` | Working |
| SPI HAT Detection | `config/spi_hats.py` | Needs work |
| Radio Presets | `config/lora.py` | Working |
| Channel Config | `launcher_tui/channel_config_mixin.py` | Working |
| Owner Name | `commands/meshtastic.py` | Added |
| Region Selection | `config/lora.py` | Working |
| Node List/Telemetry | Multiple panels | Working |
| MQTT Dashboard | `gtk_ui/panels/mqtt_dashboard.py` | Working |
| Diagnostics Panel | `utils/diagnostic_engine.py` | Working |
| Coverage Maps | `utils/coverage_map.py` | Working |
| RF Calculator | `utils/rf.py` | Working, tested |
| Launcher TUI | `launcher_tui/` | Primary CLI |

### Nice-to-Have (Can Be Rough)

| Feature | Notes |
|---------|-------|
| Message Queue | SQLite queue exists, works |
| Webhooks | Implemented, low maintenance |
| Textual TUI | Works but lower priority |
| GTK Desktop | Maintenance only |

### Nice-to-Have (Revised)

| Feature | Notes |
|---------|-------|
| AREDN Panel | **KEEP** - User is part of AREDN network |
| AI Assistant | **KEEP** - Differentiator, future of mesh NOC |
| Message Queue | SQLite queue exists, works |
| Webhooks | Implemented, low maintenance |
| Textual TUI | Works but lower priority than Launcher TUI |

### Cut/Deferred

| Feature | Reason |
|---------|--------|
| Web UI | Not used, adds complexity |
| HamClock Integration | Nice but not core NOC (defer) |

---

## Maps Strategy

Maps are **Core** - essential for NOC visualization. Must be DYNAMIC, showing all networks.

### Unified Map Vision
```
┌─────────────────────────────────────────────────────────────┐
│  🗺️ MeshForge Network Map                                   │
│                                                             │
│  Legend:                                                    │
│    ◉ Meshtastic Node (from meshtasticd)                    │
│    ◆ RNS Destination (from rnsd)                           │
│    ■ AREDN Node (from AREDN API)                           │
│    ── RF Link                                               │
│    -- Tunnel/IP Link                                        │
│                                                             │
│  Data Sources:                                              │
│    - meshtasticd (localhost:4403)                          │
│    - rnsd (UDP 37428)                                       │
│    - AREDN sysinfo API (*.local.mesh)                      │
└─────────────────────────────────────────────────────────────┘
```

### Current Implementation
- Folium generates HTML with Leaflet.js
- WebKitGTK embeds in GTK (has root sandbox issues)
- Fallback: Open in external browser

### Decision
1. **Primary:** Generate map HTML, open in system browser (`xdg-open`)
2. **GTK:** Try WebKit, fall back to browser if fails
3. **Headless:** Generate HTML file, user opens on another device

### Map Features (Priority Order)
1. Node positions with icons (per-network type)
2. Link lines between nodes (RF, tunnel, IP)
3. AREDN integration (query *.local.mesh nodes)
4. Coverage circles (optional, can be toggled)
5. Real-time updates (poll every 30s)

### AREDN Map Integration
Reference: https://worldmap.arednmesh.org/
- AREDN nodes report location to AREDN servers
- MeshForge can query local AREDN nodes via API
- See `.claude/research/aredn_integration.md` for API details

---

## Implementation Priorities

### Phase 1: First-Run Experience
1. Create setup wizard in Launcher TUI
2. Add post-install prompt to installer
3. Environment detection (headless vs desktop)

### Phase 2: Navigation Fixes
1. Add questionary to Rich CLI configs (escape handling)
2. Audit all menus for Back/Cancel options
3. Standardize keyboard shortcuts

### Phase 3: Core Reliability
1. Ensure all Core features work 100%
2. Add tests for critical paths
3. Fix any SPI HAT detection gaps

### Phase 4: Cleanup
1. Remove/hide Cut features from UI
2. Mark Nice-to-have clearly in menus
3. Update documentation

---

## Technical Decisions

### Dialog Backend (Launcher TUI)
- Use `dialog` or `whiptail` (whichever available)
- Consistent look across systems
- Works over SSH, works as root

### Questionary (Rich CLI)
- Replace `Confirm.ask()` with questionary menus
- Arrow key navigation
- Escape to cancel
- Only where Rich CLI is still needed

### GTK Threading
- All UI updates via `GLib.idle_add()`
- Background work in threads with `daemon=True`
- See `.claude/rules/gtk_threading.md`

### Service Detection
- Single source of truth: `utils/service_check.py`
- Systemd services: trust `systemctl` only
- Non-systemd (rnsd): port + process check
- See Issue #17 redesign

---

## Open Questions

1. **WebKit alternative?** - Need to evaluate options for embedded browser
2. **Map caching?** - Should we cache map tiles for offline use?
3. **Mobile/tablet?** - Any consideration for touch-friendly UI?

---

## References

- `.claude/foundations/persistent_issues.md` - Known bugs and fixes
- `.claude/foundations/domain_architecture.md` - Core vs Plugin model
- `.claude/rules/gtk_threading.md` - GTK threading rules
- `.claude/rules/security.md` - Security rules (Path.home, shell=True, etc.)
