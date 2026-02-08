# MeshForge Session Notes

**Last Updated**: 2026-02-08
**Current Branch**: `claude/analyze-maps-integration-O7C5q`
**Version**: v0.5.2-beta
**Tests**: 3360 passing, 19 skipped, 0 failures

## Session Focus: Maps Integration Analysis & Reliability

### What Was Done

#### 1. Full Maps System Audit (~9,000 lines across 7 files)

Systematically analyzed every maps-related component:

| File | Lines | Assessment |
|------|-------|------------|
| `map_data_collector.py` | 1,507 | Solid — 7 data sources, all try/except guarded |
| `coverage_map.py` | 1,145 | Solid — Folium → Leaflet.js fallback |
| `map_http_handler.py` | 973 | Solid — CORS, path traversal protection, error handling |
| `map_data_service.py` | 521 | Solid — daemon threads, graceful shutdown |
| `ai_tools_mixin.py` (map fns) | ~800 | **Fixed** — see below |
| `main.py` (maps menu) | ~75 | **Fixed** — see below |
| `node_map.html` | 4,750 | Solid — 4-level data loading cascade |

**Data Source Chain (MapDataCollector):**
1. UnifiedNodeTracker (RNS + Meshtastic merged)
2. meshtasticd (HTTP API → TCP → CLI fallback)
3. Direct USB radio (serial, when meshtasticd not running)
4. MQTT subscriber (live → cached GeoJSON fallback)
5. Node tracker cache files (node_cache.json)
6. AREDN mesh network (local node → neighbors)
7. RNS direct query (rnsd path table + NomadNet peers)
8. Last-known disk cache (24h max age)

**Frontend Resilience (node_map.html):**
1. `window.meshforgeData` (injected by TUI snapshot)
2. `fetch('/api/nodes/geojson')` (live API)
3. URL query parameter `?data=`
4. Demo data (development/preview)
- Auto-refresh every 30 seconds (API mode only)
- WebSocket for real-time message stream

#### 2. Issues Found & Fixed

##### Fix 1: `_open_live_map` — dispatch + loop pattern
- **Was**: Raw if/elif (browser/server/autostart), no `_safe_call`, single-shot
- **Now**: `_safe_call` dispatch dict, wrapped in `while True` loop
- **Benefit**: Menu re-displays after toggling auto-start; crash protection

##### Fix 2: `_export_data_menu` — `_safe_call` dispatch
- **Was**: Raw `if choice in [...]` delegation (line 916)
- **Now**: Proper dispatch dict with `_safe_call` wrapping
- **Benefit**: Exception in any export format won't crash TUI

##### Fix 3: `_open_in_browser` — headless detection
- **Was**: Silently failed on SSH/headless sessions (no $DISPLAY)
- **Now**: Calls `_is_headless()` before attempting browser, shows URL dialog
- **Benefit**: Users on SSH see the URL and can copy it to their local browser

##### Fix 4: `_toggle_auto_map` — recursion removal
- **Was**: Called `self._open_live_map()` recursively after toggle
- **Now**: Returns to caller; `_open_live_map` loop handles re-display
- **Benefit**: Cleaner control flow, no recursive stack growth

#### 3. Test Results
- 63 core map tests — all pass
- 88 map-related tests (broader keyword search) — all pass
- 52 coverage-related tests — all pass
- Full suite: 3360 pass, 0 fail, 19 skip (unchanged from baseline)

### Maps Features — User Access Paths

| Feature | Path | Status |
|---------|------|--------|
| Live NOC Map (snapshot) | Maps & Viz → Live NOC Map → Browser | Working |
| Live NOC Map (server) | Maps & Viz → Live NOC Map → Server | Working |
| Auto-start map on launch | Maps & Viz → Live NOC Map → Auto-open | Working |
| Coverage Map (all sources) | Maps & Viz → Coverage Map → All sources | Working |
| Coverage Map (single source) | Maps & Viz → Coverage Map → meshtasticd/MQTT | Working |
| Network Topology (D3.js) | Maps & Viz → Network Topology | Working |
| Export GeoJSON/CSV/GraphML/D3 | Maps & Viz → Export Data | Working |
| Coverage prediction API | GET /api/coverage/lat/lon/alt | Working |
| Line-of-sight API | GET /api/los/lat1/lon1/lat2/lon2 | Working |
| Historical snapshot | GET /api/nodes/snapshot | Working |
| Node trajectory | GET /api/nodes/trajectory/id | Working |
| Radio control API | GET/POST /api/radio/* | Working |
| WebSocket real-time | ws://localhost:5001/ | Working |
| Offline tile caching | TileCacheManager | Available |
| Heatmap generation | CoverageMapGenerator.generate_heatmap() | Available |
| AI Diagnostics | Maps & Viz → AI Diagnostics | Working |

### Remaining Work (Next Session Priorities)

#### Still-Unprotected Sub-Menus (~20 loops in deeper nesting)
- `service_menu_mixin.py` — 4 internal dispatch loops (complex, interacts with systemd)
- `system_tools_mixin.py` — 9 internal dispatch loops (biggest risk)
- `ai_tools_mixin.py` — 2 sub-menus (`_intelligent_diagnostics`, `_knowledge_base_query`)
- `settings_menu_mixin.py` — 2 sub-menus
- `metrics_mixin.py` — 2 sub-menus
- `meshtasticd_config_mixin.py` — 1 sub-menu
- `channel_config_mixin.py` — 1 sub-menu
- `rf_awareness_mixin.py` — 1 sub-menu
- `traffic_inspector_mixin.py` — 1 sub-menu

#### Feature Gaps (Lower Priority)
- Auto-Review System — not accessible from TUI (command-line only)
- Device Persistence — no view/reset UI (internal only)
- Heatmap — code exists but no TUI menu entry
- Tile caching — code exists but no TUI menu entry for pre-caching
- Map settings — no TUI menu to configure cache ages, thresholds, AREDN IPs

#### Hardware Testing
- Maps on actual Pi with radio connected
- Coverage map with real GPS nodes
- AREDN integration with actual AREDN hardware
- Headless/SSH browser detection path

### File Sizes (All Under 1,500 lines)
- launcher_tui/main.py: ~1,375 lines
- ai_tools_mixin.py: ~945 lines
- All other modified files: well under threshold

### Commits
- `51d3762` — fix: Maps integration reliability — dispatch protection, headless detection, recursion fix

### Architecture Notes for Future Sessions

**Maps data flow:**
```
TUI Menu → MapDataCollector.collect() → 7 sources (all try/except)
    → Aggregated GeoJSON → CoverageMapGenerator or MapServer
    → HTML output or HTTP API → Web Browser (node_map.html)
```

**Key design decisions:**
- MapDataCollector merges by node ID (dedup), prefers newer data
- meshtasticd HTTP API is preferred over TCP (doesn't need lock)
- Direct USB radio only attempted when TCP returns nothing
- AREDN validates with actual HTTP API response, not just socket test
- Frontend demo data ensures map always renders even with no data
