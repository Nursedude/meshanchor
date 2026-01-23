# MeshForge Sprint: Opus Power Window (Jan 23 - Feb 16, 2026)

> **Goal**: Make MeshForge everything it wants to be. Deliberate, quality-focused innovation.
> **Method**: Double-tap everything. When you think you're done, make it better.
> **QA**: fleet-host-1 (headless), fleet-host-2 (monitor/GTK testing) — user handles hardware testing.

---

## Active Development Path

```
TUI → dispatches real Linux tools
    → opens browser maps (node_map.html)
    → no framework in between
GTK → frozen (exists for fleet-host-2 monitor testing, not developed)
```

---

## Sprint Backlog

### Phase 1: Map Data Pipeline (IN PROGRESS)

- [x] Live Leaflet.js map engine with incremental updates
- [x] Node state animations (appear, pulse, alert)
- [x] SNR-based link topology lines
- [x] Coverage radius circles (toggle)
- [x] TUI "Live Network Map" menu option
- [x] **Map data service** — unified collector from all sources (meshtasticd, MQTT, tracker)
- [x] **Map HTTP server** — `/api/nodes/geojson` endpoint, serves live map at localhost:5000
- [x] **MQTT node tracking → map feed** — mqtt_subscriber persists GeoJSON cache every 30s
- [x] **Map data feed from meshtasticd** — TCP interface + CLI fallback with position parsing
- [x] **Node history SQLite** — store node positions/states over time for playback
- [x] **Auto-open map on TUI launch** — toggle in map menu, persisted setting
- [ ] **Map tile pre-cache for Hawaii** — ship with offline tiles for default region

### Phase 2: Gateway Bridge Hardening

- [x] **Reconnection logic audit** — integrated ReconnectStrategy with backoff + jitter
- [x] **Message queue overflow** — max_queue_size, priority-based shedding, auto-cleanup, stale recovery
- [x] **Bridge health metrics** — BridgeHealthMonitor: uptime, rates, errors, is_healthy
- [x] **Error categorization** — classify_error: transient/permanent/unknown patterns
- [x] **Integration test** — simulate Meshtastic→RNS→Meshtastic round trip (29 tests)
- [x] **LXMF delivery confirmation** — DeliveryTracker with callbacks, timeouts, confirmation rate

### Phase 3: RF Tools Enhancement

- [x] **Coverage prediction with terrain** — SRTM data download + LOS calculation
- [x] **Signal strength trending** — collect SNR/RSSI over time, identify patterns
- [x] **LoRa preset impact visualization** — show how preset choice affects coverage
- [x] **Multi-hop path loss** — calculate cumulative loss across relay chain
- [x] **Antenna pattern modeling** — basic dipole/yagi/omni patterns for site planning

### Phase 4: AI Diagnostics Expansion

- [x] **More diagnostic rules** — expanded from 17 to 58 symptom patterns (7 categories)
- [x] **Log parsing patterns** — extract common errors from journalctl/meshtasticd logs
- [x] **Health scoring** — overall network health 0-100 based on node metrics
- [x] **Predictive maintenance** — battery drain rate, node dropout patterns, reliability scoring
- [x] **Knowledge base expansion** — 35 entries + 6 guides (RNS, AREDN, RF, MQTT)

### Phase 5: TUI Polish

- [ ] **Menu reorganization** — group by workflow (Setup → Monitor → Diagnose → Tools)
- [ ] **First-run wizard** — detect fresh install, guide through radio setup
- [ ] **Status bar** — persistent bottom bar showing: nodes online, bridge status, last update
- [ ] **Quick actions** — single-key shortcuts for common operations
- [ ] **Export/report** — generate network status report (PDF or markdown)

### Phase 6: Field Operations

- [ ] **Offline-first data sync** — queue telemetry when internet is down, sync when back
- [ ] **GPS integration** — operator position on map, distance to nodes
- [ ] **Emergency mode** — simplified UI for EMCOMM operators (big buttons, clear status)
- [ ] **Channel scan** — detect active channels in range
- [ ] **Node inventory** — track all known nodes with hardware, firmware, location, owner

---

## Completed This Sprint

- [x] Full code review and healthcheck (1335 tests, 0 failures)
- [x] README revamp (mermaid diagrams, elevator speech, honest capabilities)
- [x] GTK frozen decision documented (TUI + browser-maps is the path)
- [x] Maps "Double Tap" vision document (.claude/research/maps_double_tap.md)
- [x] Live map engine (web/node_map.html) — incremental updates, animations, links
- [x] TUI live map integration (snapshot + server modes)
- [x] Gateway scope clarification (Meshtastic↔RNS bridge, AREDN monitoring)
- [x] Map data service (src/utils/map_data_service.py) — unified collector + HTTP server
- [x] MQTT subscriber → map cache persistence (auto-populates map data)
- [x] Meshtasticd TCP interface collection — direct node data with positions, online detection
- [x] Node history SQLite — trajectory, snapshots, stats, cleanup, API endpoints
- [x] Auto-open map on TUI launch — toggle setting, silent background start
- [x] Gateway bridge hardening — ReconnectStrategy integration, health monitor, error classification
- [x] Failed message persistence — re-queue to persistent queue on send failure
- [x] Message queue overflow protection — max size limits, priority shedding, stale recovery, auto-cleanup
- [x] Bridge integration test — full Mesh→RNS→Mesh round trip, routing, callbacks, edge cases
- [x] LXMF delivery confirmation — DeliveryTracker with pending/confirmed/failed/timeout states
- [x] Coverage prediction with terrain — SRTMProvider, LOSAnalyzer, Fresnel zones, diffraction, coverage grid
- [x] Signal strength trending — SignalTrend, per-node windowed stats, event detection, hourly patterns
- [x] MQTT subscriber hardening — input validation, payload limits, stale cleanup, reconnect jitter
- [x] Data pipeline integration test — MQTT → MapCollector → History round trip (22 tests)
- [x] Error handling audit — all critical paths verified, no bare except, no shell=True, all timeouts
- [x] LoRa preset impact visualization — sensitivity, range, airtime, throughput, coverage zones, comparison table
- [x] Multi-hop path loss — cumulative analysis, relay selection, preset comparison, path reports
- [x] Antenna pattern modeling — dipole, ground plane, Yagi, patch with gain patterns and coverage profiles
- [x] Network health scoring — unified 0-100 score with connectivity, performance, reliability, freshness
- [x] Log parsing patterns — 29 patterns for meshtasticd, rnsd, systemd, MeshForge with structured output
- [x] Diagnostic rules expansion — 17 → 58 rules (connectivity, hardware, protocol, performance, resource, config, security)
- [x] RF tools integration test — 20 cross-module tests (preset→multihop→antenna→health→trending→log_parser)
- [x] Predictive maintenance — battery drain forecasting, dropout patterns, periodicity detection, solar detection
- [x] Knowledge base expansion — 19→35 entries, 3→6 guides (RNS identity/transport/LXMF, AREDN overview/discovery/services, FSPL/antennas/propagation/ISM/terrain/solar/interference, MQTT)
- [x] 919 new tests across all modules (2151 total)

---

## Session Recovery Notes

If you lose context and start a new session:

1. Read this file first: `.claude/research/sprint_opus.md`
2. Read the project config: `CLAUDE.md`
3. Check git log: `git log --oneline -20`
4. Run healthcheck: `python3 -m pytest tests/ -x -q`
5. Pick up the next unchecked item in the Sprint Backlog above
6. Branch: `claude/code-review-docs-update-x1dtV`

---

## Architecture Decisions Log

| Date | Decision | Rationale |
|------|----------|-----------|
| 2026-01-23 | GTK frozen | Doubles dev time, WebKit root bug, TUI+browser covers 95% |
| 2026-01-23 | Leaflet.js over Folium for live maps | Incremental updates, JS bridge, no regen |
| 2026-01-23 | MeshCore = future research | Not integrated, Meshtastic↔RNS is today's bridge |
| 2026-01-23 | AREDN = monitoring only | Read-only node discovery, not a full bridge |

---

## Quality Checklist (Before Pushing)

- [ ] `python3 -m pytest tests/ -x -q` — all pass?
- [ ] `python3 scripts/lint.py --all` — MF001-MF004 clean?
- [ ] No `Path.home()` in new code
- [ ] No `shell=True` in subprocess
- [ ] All subprocess calls have timeouts
- [ ] No bare `except:` clauses
- [ ] Type hints on new functions
- [ ] Tested the happy path manually (or with unit test)
