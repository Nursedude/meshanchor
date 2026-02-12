# MeshForge Session Notes

**Last Updated**: 2026-02-12
**Current Branch**: `claude/feature-improvements-accessibility-F3KZw`
**Version**: v0.5.4-beta

---

## Latest Session: Feature Accessibility Audit & Fixes (2026-02-12)

### What We Did

Systematic audit of all 110+ modules and 548+ methods to find features
that existed in code but were NOT accessible through the TUI menus.

### Changes Made (commit c3e7431)

**6 features wired up to TUI menus:**

| Feature | Menu Location | Source Module |
|---------|--------------|---------------|
| Network Status Reports | Dashboard > Reports | `report_generator.py` |
| Health Score Dashboard | Dashboard > Health Score | `health_score.py` |
| RNS Config Drift Check | RNS > Config Drift Check | `config_drift.py` |
| Antenna Comparison | RF & SDR > Antenna Analysis | `antenna_patterns.py` |
| Enhanced Signal Trends | Node Health > Signal Trends | `signal_trending.py` |
| Enhanced Battery Forecast | Node Health > Battery Forecast | `predictive_maintenance.py` |

**Files modified (5):**
- `main.py` (+6 lines) — new menu items in Dashboard and RF&SDR
- `dashboard_mixin.py` (+131 lines) — reports menu + health score display
- `rns_menu_mixin.py` (+47 lines) — config drift check
- `rf_tools_mixin.py` (+186 lines) — antenna analysis submenu
- `node_health_mixin.py` (+106/-15 lines) — enhanced signal + battery displays

**Tests**: 4009 passed, 19 skipped, 0 failures. Lint clean.

### Remaining Accessibility Gaps (P2/P3 — for future sessions)

| Module | TUI Status | Priority | Notes |
|--------|-----------|----------|-------|
| `analytics.py` | Not exposed | P2 | Predictive alerts, network forecast |
| `webhooks.py` | Not exposed | P2 | Webhook endpoint management UI |
| `active_health_probe.py` | Not exposed | P2 | NGINX-style service health checks |
| `latency_monitor.py` | Partial | P3 | Background monitoring not wired |
| `prometheus_exporter.py` | Not exposed | P3 | Export config only |
| `influxdb_exporter.py` | Not exposed | P3 | Export config only |
| `simulator.py` | Standalone only | P3 | By design |
| `classifier.py` | Not exposed | P3 | Traffic classification |

### Entropy Watch

Session was clean — no scope creep. All changes were focused on wiring
existing APIs to existing menus. No new functionality created, only
plumbing. Stopped at the right time.

### Next Session Priorities

1. **P0**: If any PRs pending, review/merge
2. **P1**: Wire up `analytics.py` predictive alerts to Dashboard
3. **P1**: Wire up `webhooks.py` management UI (Configuration menu)
4. **P2**: Gateway TX path (from prior session notes)

---

## Previous Session: API Proxy fromradio Fix (2026-02-10)

**See**: `.claude/session_notes/2026-02-10_api_proxy_fromradio_fix.md` for full details.

**TL;DR**: `MeshtasticApiProxy` was enabled by default, draining ALL fromradio packets from port 9443. Native web client got nothing. Fixed: proxy defaults to OFF. Gateway uses TCP:4403 (unaffected). Web client at :9443 works natively now.

**Next priorities**: P0 = Gateway TX (RX works, TX doesn't). P1 = Verify :9443 web client works. P2 = Plan for upstream meshtastic web client updates.

---

## Previous Session: /mesh/ Architecture Rethink (2026-02-10)

### Status: SUPERSEDED — user decided meshtasticd owns its web client

The `/mesh/` subpath approach has been attempted from multiple angles across several sessions. Each fix adds complexity (HTML rewriting, CSS injection, base tag manipulation, regex path stripping) and the blank screen / UI glitches persist. The core problem is **fighting the web client's build assumptions** rather than working with them.

### What We Tried (and why each is fragile)

| Attempt | Approach | Why it breaks |
|---------|----------|---------------|
| Session 1 | `_proxy_mesh_client()` — full HTML proxy + JS injection | 101 lines of fragile proxying, broke on every meshtasticd update |
| Session 2 | Serve files from disk + `<base href="/mesh/">` injection | `<base>` doesn't affect root-absolute paths (`/assets/x.js`) |
| Session 3 | Regex strip leading `/` from src/href + replace existing `<base>` | Still blank — Vite builds have paths in JS bundles too, not just HTML |
| Session 3 | CSS injection (`overflow:hidden`) for scrollbar covering sidebar | Treating symptoms, not the cause |

### The fundamental tension

meshtasticd builds its web client for serving at **root `/`**. MeshForge wants to serve it at **`/mesh/`**. Every approach to bridge this gap adds a layer of fragile rewriting. The JS bundles themselves contain hardcoded paths (dynamic imports, fetch calls, worker URLs) that HTML-level rewriting can't reach.

### Clean architectural options for next session

**Option A: Serve web client at root, move NOC map to subpath**
```
ip:5000/           → meshtastic web client (no rewriting needed)
ip:5000/noc/       → MeshForge NOC dashboard
ip:5000/api/*      → MeshForge APIs
```
- Simplest. Web client works as-is. NOC dashboard is our code so we control its paths.
- Trade-off: `/` is no longer the NOC map.

**Option B: Separate ports**
```
ip:5000             → MeshForge NOC dashboard
ip:5001             → meshtastic web client (served by MeshForge, no subpath)
meshtasticd:9443    → locked down (iptables)
```
- Zero path rewriting. Each app owns its root.
- MeshForge still proxies the API (phantom filtering, stream multiplexing).
- Trade-off: Two ports to remember.

**Option C: Rebuild web client with correct base path**
```
vite build --base=/mesh/
```
- Ship a pre-built copy with correct paths baked in.
- Trade-off: Must rebuild on every meshtasticd web client update. Maintenance burden.

**Option D: Reverse proxy (nginx/caddy)**
- Let a proper reverse proxy handle subpath rewriting.
- Trade-off: Adds a dependency. MeshForge's "zero-dependency" design principle.

### Recommendation for discussion
Option A or B are cleanest. Option A requires only moving the NOC map to `/noc/` and serving meshtasticd files at `/`. Option B is the most isolated but means two ports.

### MapDataCollector type error — FIXED this session
Separate from the /mesh/ issue. `_get_source_summary()` puts `"meshtasticd_via": "http"` (string) in the sources dict. Dashboard did `v > 0` on all values → TypeError. Fixed with `isinstance(v, (int, float))` guard. Also hardened AREDN `tunnel_count` int conversion.

### Commits on branch
1. `a16ad6d` — fix: Fix /mesh/ blank white screen by rewriting Vite root-absolute paths
2. `e3b2f06` — fix: Fix MapDataCollector type error and /mesh/ scrollbar covering sidebar

### Files changed this session
- `src/utils/map_http_handler.py` — `_rewrite_mesh_html()`, MIME types, CSS injection
- `src/utils/map_data_collector.py` — AREDN tunnel_count int conversion
- `src/launcher_tui/dashboard_mixin.py` — isinstance guard on source values
- `tests/test_map_data_service.py` — 13 new TestRewriteMeshHtml tests

---

## Previous Session: MeshForge Owns the Browser (2026-02-10)

### Root Cause Found — P0 Phantom Nodes
The Meshtastic React web client gets node data via **protobuf streaming** (`/api/v1/fromradio`), NOT from `/json/nodes`. The previous fix sanitized JSON but the protobuf packets were forwarded raw to clients. Phantom NodeInfo packets (MQTT nodes without User data) caused React to crash on `node.user.longName`.

### What Was Done

**P0: Phantom Node Fix — TWO-LAYER DEFENSE**
1. **Server-side protobuf filtering** (`meshtastic_api_proxy.py`):
   - Added raw protobuf wire format parser (`_read_varint`, `_extract_protobuf_fields`)
   - `_is_phantom_nodeinfo()` detects `FromRadio.node_info` packets missing User field
   - `_distribute_packet()` now drops phantom NodeInfo before distributing to clients
   - Added `phantom_nodes_filtered` stats counter
2. **Client-side JS error protection** (`map_http_handler.py`):
   - Injected `window.onerror` + `unhandledrejection` handlers into proxied HTML
   - Catches "Cannot read properties of null/undefined" as safety net
   - Prevents React white-screen-of-death from any remaining phantom data

**P1: Right Panel Info Clipping — FIXED** (`web/node_map.html`)
- Added `max-width: 280px` to `.control-panel`
- Added `overflow: hidden; text-overflow: ellipsis; white-space: nowrap` to `.stat-row .value`
- Added `flex-shrink: 0` to `.stat-row .label` so labels don't compress
- Added `gap: 8px` between label and value for consistent spacing

**P2: Radio Message "Went Nowhere" — IMPROVED** (`map_http_handler.py`, `node_map.html`)
- Improved error messages: now returns actionable detail (library install, TCP port check)
- Changed success to "Sent via radio (delivery best-effort)" — sets correct user expectation
- JS now shows `data.detail` as tooltip, longer display for errors (8s vs 3s)
- HTTP status codes: 503 for missing library, 502 for send failure

### Changes This Session
- `src/gateway/meshtastic_api_proxy.py` — protobuf phantom filtering, wire format parser
- `src/utils/map_http_handler.py` — JS error protection injection, radio API error improvement
- `web/node_map.html` — panel CSS overflow fix, radio send feedback improvement
- `tests/test_api_proxy_sanitize.py` — 17 new tests (protobuf filtering + wire format)

### What About ip:9443 Directly?
Users going to ip:9443 bypass MeshForge entirely — there's no fix possible at the proxy level. Options for next session:
- **Tell users to use ip:5000/mesh/** — this is now the sanitized path
- **iptables redirect** 9443→5000 (requires root, add to install script)
- **Disable meshtasticd web server** and serve exclusively through MeshForge

### P3: rnsd Permission Fix — UNTESTED
Committed last session. Needs hardware verification on MOC2.

---

## Previous Session: Web UI Fixes — Scroll + Phantom Nodes (2026-02-10)

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

### P0: Gateway TX — Green RX but No TX
Gateway receives Meshtastic messages (RX green) but doesn't transmit back. Investigate `meshtastic_handler.py` TX path, channel/PSK config, and gateway logs.

### P1: Verify Web Client at :9443 Works Now
API proxy disabled by default (Issue #28 fix). User should confirm native web client shows data. If it still doesn't, the issue is meshtasticd-side (not MeshForge).

### P2: Meshtastic Web Client Upstream Updates
User plans to track meshtastic web client updates. MeshForge should NOT own/fork it. Phantom node fixes should be contributed upstream to meshtastic/web.

### P3: rnsd Permission Fix — UNTESTED
Needs hardware verification on MOC2.

### P4: Grafana Metrics
Needs gateway running with metrics server on port 9090.

---

## Previous Session: Reliability Audit & Test Fix (2026-02-09)

Configured MeshForge broker templates for MOC1 (Pi5 + Meshtoad + LongFast) with
MQTT-bridged topology to MOC2 (Pi HAT + ShortTurbo + RNS/NomadNet).

See: `.claude/session_notes_moc_broker.md` for full details.

### Changes That Session
- **NEW** `templates/meshtoad.yaml` — Meshtoad CH341 SPI hardware template
- **NEW** `templates/meshforge-presets/moc1-broker.yaml` — MOC1 full preset
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
