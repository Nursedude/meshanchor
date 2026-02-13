# Session Notes: Phase 1 Health Loop + Auto-Fix Defang

**Date:** 2026-02-13
**Session:** claude/review-rns-nomadnet-issues-fMjX8
**Status:** Phase 1 COMPLETE — ready for Phase 2 (Circuit Breakers)

---

## What Was Done (4 commits)

### Commit 1: Critical Diagnosis Document
- Forensic review of all 8 RNS/NomadNet sessions (Jan 31 – Feb 13)
- Identified 5 recurring failure patterns
- Decision history scorecard (14 decisions rated good/bad)
- 5 actionable recommendations

### Commit 2: Wire Health Monitoring Loop
**Files changed:** `active_health_probe.py`, `status_bar.py`, `main.py`

Wired the existing (but disconnected) health infrastructure:

```
ActiveHealthProbe (30s background thread)
  ├── check meshtasticd (TCP port 4403)
  ├── check rnsd (UDP port 37428)
  └── check mosquitto (TCP port 1883)
      │
      ▼ on_state_change callback
EventBus.emit('service', ServiceEvent)
      │
      ├──▶ StatusBar._on_service_event() → update cache immediately
      └──▶ HealthScorer._on_service_event() → update scoring
```

- `get_health_probe()` singleton in `active_health_probe.py`
- `_emit_state_change()` bridges probe → EventBus
- StatusBar subscribes to EventBus on init, gets push updates
- main.py starts probe after startup checks, stops in finally

### Commit 3: Defang Auto-Fix
**File changed:** `rns_menu_mixin.py`

- `_run_rns_tool()` no longer calls auto-fix from error handlers
- When rnsd is down, shows dialog: "Start rnsd now?" (user chooses)
- `_auto_fix_rns_shared_instance()` → `_repair_rns_shared_instance()`
- New `_rns_repair_menu()` accessible from RNS > Repair RNS menu
- Repair wizard requires explicit consent before any state changes

### Commit 4: Wire HealthScorer Singleton
**Files changed:** `health_score.py`, `dashboard_mixin.py`, `report_generator.py`, `prometheus_exporter.py`, test fix

- `get_health_scorer()` singleton in `health_score.py`
- Auto-subscribes to EventBus service events
- Dashboard, reports, and Prometheus all use shared scorer
- Scorer now receives live service status from the health probe

---

## Test Results

**4021 passed, 19 skipped** — zero regressions across all 4 commits.

---

## What's Next: Phase 2 (Circuit Breakers)

### From the Reliability Plan:

**Goal:** One subsystem failing doesn't take others down.

1. **Gateway SubsystemState enum** — `HEALTHY`, `DEGRADED`, `DISCONNECTED`, `DISABLED`
2. **Independent lifecycle per side** — Meshtastic down? Queue messages, keep RNS running. RNS down? Queue messages, keep Meshtastic running.
3. **Message queue during degraded state** — SQLite message_queue.py already exists
4. **Status bar shows degraded state** — `bridge:DEGRADED(mesh→queue)`

**Key files to modify:**
- `src/gateway/rns_bridge.py` (main bridge — needs SubsystemState)
- `src/gateway/message_queue.py` (already exists, may need drain method)
- `src/launcher_tui/status_bar.py` (degraded state display)

### Phase 3 work identified but not started:
- Remove remaining auto-fix remnants from other mixins
- Build full repair wizard pattern with verify-after-fix
- Separate "Diagnose" and "Repair" throughout TUI

---

## Architecture After Phase 1

```
┌──────────────────────────────────────────────────────────────┐
│                 CONNECTED (Phase 1 done)                     │
├──────────────────────────────────────────────────────────────┤
│                                                               │
│  ActiveHealthProbe (30s loop) ──┐                            │
│    ├ meshtasticd (TCP 4403)     │                            │
│    ├ rnsd (UDP 37428)           │ on_state_change            │
│    └ mosquitto (TCP 1883)       │                            │
│                                  ▼                            │
│  EventBus ───────────────────────┐                           │
│    ├──▶ StatusBar (push updates) │                           │
│    └──▶ HealthScorer (live feed) │                           │
│                                   │                           │
│  StatusBar: mesh:* | rns:- | mqtt:* | nodes:42              │
│                                                               │
│  HealthScorer: 72/100 [fair]                                 │
│    Connectivity: 80  Performance: 50  Reliability: 85        │
│                                                               │
│  STILL DISCONNECTED:                                         │
│    shared_health_state.py ── SQLite persistence (Phase 5)    │
│    diagnostic_engine.py ── needs unification (Phase 4)       │
│                                                               │
└──────────────────────────────────────────────────────────────┘
```

---

## Key Design Decisions Made This Session

1. **Singleton pattern for probe and scorer** — all components share one instance
2. **Push-based status** — EventBus events, not polling (StatusBar keeps polling as fallback)
3. **meshtasticd check via TCP not systemd** — confirms daemon is accepting connections, not just "active"
4. **Auto-fix removed from error handlers entirely** — not gated, not conditional, removed
5. **Repair wizard requires full consent dialog** — explains what it does before doing it

---

## Files Modified (for next session's context)

| File | Change |
|------|--------|
| `src/utils/active_health_probe.py` | Singleton, EventBus callback, mosquitto check |
| `src/utils/event_bus.py` | No changes (already had everything needed) |
| `src/utils/health_score.py` | Singleton, EventBus subscription |
| `src/launcher_tui/status_bar.py` | EventBus subscription |
| `src/launcher_tui/main.py` | Start/stop health monitor |
| `src/launcher_tui/rns_menu_mixin.py` | Defanged auto-fix, repair wizard |
| `src/launcher_tui/dashboard_mixin.py` | Use singleton scorer |
| `src/utils/report_generator.py` | Use singleton scorer |
| `src/utils/prometheus_exporter.py` | Use singleton scorer |
| `tests/test_report_generator.py` | Fix test fixture for moved singleton |

---

*Session completed: 2026-02-13*
*Branch: claude/review-rns-nomadnet-issues-fMjX8*
*All tests green: 4021 passed*
