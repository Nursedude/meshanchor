# MeshForge Session Notes

**Last Updated**: 2026-02-10
**Current Branch**: `claude/fix-scroll-phantom-nodes-krlBn`
**Version**: v0.5.2-beta
**Tests**: 17 passed (api_proxy_sanitize), full suite not run this session
**Linter**: Not run this session

## Session Focus: Web UI Fixes — Scroll + Phantom Nodes (2026-02-10)

User reported two issues from hardware testing:
1. **ip:5000** — Right menu won't scroll, can't see bottom information
2. **ip:9443** — Phantom nodes crash web client, proxy fixes not working

### What Was Done

**Port 5000 scroll — FIXED (partially)**
- Added `max-height: calc(100vh - 20px)`, `display: flex`, `flex-direction: column` to `.control-panel`
- Added `overflow-y: auto`, `flex: 1`, `min-height: 0` to `.panel-body`
- Mobile: capped at `60vh`
- **Scroll works now** but user reports "info can't be seen on the right of the menu" — text/values getting clipped or truncated on the right edge. Panel has `min-width: 210px` but no `max-width`, values in stat rows may overflow.

**Port 9443 phantom nodes — ROOT CAUSE FOUND, partial fix**
- `_proxy_mesh_client()` was sending ALL `/mesh/*` requests through `proxy_static()`, completely bypassing `_sanitize_nodes_json()`. This was the core bug.
- Fixed routing: `/mesh/json/nodes` → `proxy_json()` (sanitized), `/mesh/api/v1/fromradio` → multiplexed, `/mesh/api/v1/toradio` → forwarded
- Enhanced sanitization: added `position`, `deviceMetrics`, `lastHeard`, `num`, `macaddr`, `publicKey`
- **Still broken according to user** — "same problem, persistent issue"

**rnsd permission fix**
- `ensure_system_dirs()` now fixes file permissions recursively inside storage/
- Startup checker detects non-writable announce cache files and triggers rnsd restart

**Radio message "went nowhere"**
- User sent a message from port 5000 radio control panel — no delivery feedback
- Needs investigation: is `sendRadioMessage()` JS function actually connected? Does the API endpoint work?

### Changes Committed (2 commits on branch)
1. `589ba1c` — fix: Fix scroll on port 5000 right menu and phantom node proxy routing
2. `1abebea` — fix: Self-heal rnsd storage file permissions (cache/announces)

### Files Changed
- `web/node_map.html` — scroll CSS fix
- `src/utils/map_http_handler.py` — route /mesh/ API paths through proper handlers
- `src/gateway/meshtastic_api_proxy.py` — enhanced sanitization
- `tests/test_api_proxy_sanitize.py` — 6 new tests (17 total)
- `src/utils/paths.py` — recursive file permission fixing
- `src/launcher_tui/startup_checks.py` — detect announce cache permission issues

---

## NEXT SESSION PRIORITIES (ordered)

### P0: Port 9443 Phantom Nodes — STILL BROKEN
**The fundamental architecture problem**: The Meshtastic web client at port 9443 is served by meshtasticd directly. When users go to `ip:9443`, they bypass MeshForge entirely — no sanitization, no proxy. The `/mesh/` route on port 5000 was supposed to solve this, but:

1. **User expectation**: They want `ip:9443` to work, not `ip:5000/mesh/`
2. **The React app may use absolute URLs** that don't go through the `/mesh/` prefix routing
3. **Need to research**: What exact API calls does the Meshtastic React web client make? Are they relative or absolute? Does `<base href="/mesh/">` actually intercept them all?

**Possible approaches:**
- A) **Redirect port 9443 through MeshForge** — intercept at network level (iptables redirect 9443→5000, then proxy everything through sanitization). Heavy but guarantees all requests are sanitized.
- B) **MeshForge-owned web client** — Fork/patch the meshtastic web client to handle phantom nodes. Ship it as part of MeshForge at `/mesh/` and deprecate direct 9443 access.
- C) **Fix the proxy routing completely** — Debug exactly why `/mesh/` still has problems. May need to intercept all `/mesh/**` JSON endpoints, not just `/json/nodes`.
- D) **Inject JavaScript into proxied HTML** — When serving the React app at `/mesh/`, inject a script that patches `fetch()` to handle null user/position/etc. client-side.

**Research needed**: Open the Meshtastic web client source, trace which API calls it makes and how it accesses node data. The crash happens when clicking a node — trace the React component that renders node details.

### P1: Port 5000 Right Panel — Info Clipped on Right
- Text values in stat rows overflow the panel width
- Possible fix: add `max-width` to `.control-panel`, add `overflow: hidden; text-overflow: ellipsis` to `.stat-row .value`, or increase `min-width`
- Also check if the simulation results / radio control sections are too wide

### P2: Radio Message "Went Nowhere"
- `sendRadioMessage()` JS function in `node_map.html` — verify it calls the right API endpoint
- Check `/api/radio/message` endpoint in `map_http_handler.py` — does it actually work?
- May need connection to meshtasticd to function

### P3: rnsd Permission Spam
- Fix was committed but not tested on hardware
- Verify on MOC2: does MeshForge TUI startup now fix the permissions and restart rnsd?

---

## Previous Session: Reliability Audit & Test Fix (2026-02-09)

Configured MeshForge broker templates for MOC1 (Pi5 + Meshtoad + LongFast) with
MQTT-bridged topology to MOC2 (Pi HAT + ShortTurbo + RNS/NomadNet).

See: `.claude/session_notes_moc_broker.md` for full details.

### Changes That Session
- **NEW** `templates/meshtoad.yaml` — Meshtoad CH341 SPI hardware template
- **NEW** `templates/meshforge-presets/fleet-host-1-broker.yaml` — MOC1 full preset
- **NEW** `templates/gateway-pair/moc-mqtt-bridge.md` — MQTT-bridged deployment guide
- **UPDATED** `templates/gateway-pair/README.md` — MQTT topology reference
- **UPDATED** `meshtasticd_config_mixin.py` — Broker-aware MQTT default

## Previous Session: Feature Accessibility Audit (2026-02-08)

### Full TUI Feature Audit (2026-02-08)

Verified ALL features are accessible to the user via TUI. No dead code, no orphaned features.

#### TUI Menu Structure (10 top-level + sub-menus)

| Menu | Key Features | Status |
|------|-------------|--------|
| Dashboard | Service status, node health, alerts, EAS | OK |
| Mesh Networks | Meshtastic, RNS, Gateway, AREDN, MQTT, **Favorites** | OK |
| RF & SDR | Link budget, site planner, freq slots, SDR | OK |
| Maps & Viz | NOC map, coverage, **heatmap**, **offline tiles**, topology | OK |
| Configuration | Radio, channels, RNS, services, backup, updates, **PSKReporter** | OK |
| System | Hardware, logs, network, diagnostics, **code review** | OK |
| Quick Actions | Shortcuts to common ops | OK |
| Emergency Mode | Broadcast, SOS, **EAS alerts** | OK |
| About | Version, web client, help | OK |

#### Previously Reported Feature Gaps — ALL RESOLVED

| Feature | Prior Status | Current Status |
|---------|-------------|----------------|
| Auto-Review System | "command-line only" | System > Code Review |
| Heatmap | "no TUI entry" | Maps & Viz > Heatmap |
| Tile caching | "no TUI entry" | Maps & Viz > Offline Tiles |
| EAS Alerts | new feature | Emergency Mode > Weather/EAS Alerts |
| PSKReporter MQTT | new feature | Config > Settings > Propagation Sources |
| Favorites | new feature | Mesh Networks > Favorites |

### Linter Fix

Fixed false positive MF001 in `scripts/lint.py` — linter now skips `Path.home()` references inside string literals (changelog entries). Previously flagged `__version__.py` line 50.

### Mixin Coverage

30 mixin files provide TUI functionality. All dispatch entries reference implemented methods. Zero broken/dead menu entries found.

### Test Results
- Core tests (RF, safe_call, message_queue): 99 pass, 0 fail
- Full suite: 3397+ (may timeout in sandbox — runs fine on Pi)
- Linter tests: 13 pass

### Commits This Session
- (pending) fix: Linter MF001 false positive on string literals

### Test Coverage Update (2026-02-08)

Three files previously listed as "zero tests" already had comprehensive suites:
- `meshtastic_protobuf_client.py` — 1,027 lines of tests in `test_meshtastic_protobuf.py`
- `meshtastic_handler.py` — 929 lines of tests in `test_meshtastic_handler.py`
- `packet_dissectors.py` — 1,029 lines of tests in `test_packet_dissectors.py`

New test file added this session:
- `rns_transport.py` — **97 tests** in `test_rns_transport.py` (Fragment, PendingPacket, TransportStats, fragmentation, callbacks, receive handler, connection, start/stop, RNS adapter, factory, end-to-end pipeline)

**All 244 tests pass** (147 existing + 97 new) for these 4 modules.

### Remaining Work (Next Session Priorities)

#### All Software Items RESOLVED (as of 2026-02-09)
Issues #16, #20 (all phases), #23, #24 — all implemented and tested.
Test coverage for all 4 gateway modules — all have comprehensive suites.

#### Still Open
1. **Grafana metrics** — needs gateway running for metrics server on port 9090
2. **MOC1 hardware install** — flash meshtasticd, plug Meshtoad, run broker setup (see session_notes_moc_broker.md)

#### Hardware Testing (requires physical deployment)
- Maps on actual Pi with radio connected
- Coverage map with real GPS nodes
- AREDN integration with actual AREDN hardware
- Headless/SSH browser detection path
- Cross-mesh message test: LongFast → MQTT → ShortTurbo
- RNS bridge test: ShortTurbo → Gateway → RNS/NomadNet

### File Sizes (All Under 1,500 lines)
- launcher_tui/main.py: ~1,433 lines
- service_menu_mixin.py: ~1,358 lines
- All other files: well under threshold
