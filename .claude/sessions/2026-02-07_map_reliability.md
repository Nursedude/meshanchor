# Session: Map Reliability & Visibility (2026-02-07)

## Branch
`claude/meshforge-reliability-tasks-0FNja`

## Research
- **RMAP.WORLD** (Reticulum Network Worldwide Map) - Leaflet + OSM, node type filtering, marker clustering, privacy-first, MQTT telemetry
- **GitHub Discussion #743** - RMAP v3 features, 306 nodes tracked, 9 node types, distance tools planned

## Changes Made

### 1. Coordinate Validation Centralization (`map_data_collector.py`)
- **Problem**: 4 duplicate coordinate checks using `abs(lat) < 0.001 and abs(lon) < 0.001` rejected equator/prime meridian nodes. No NaN/Infinity guards.
- **Fix**: New `_is_valid_coordinate()` static method handling:
  - NaN and Infinity rejection (prevents map rendering crash)
  - Out-of-range validation (-90..90 lat, -180..180 lon)
  - String-to-float coercion
  - Only rejects `(0.0, 0.0)` exactly (unset GPS default)
  - Accepts equator nodes (lat=0.0 with valid lon) and meridian nodes
- **Impact**: All 4 call sites updated to use centralized method

### 2. Empty Sequence Crash Guards
- **`path_visualizer.py`**: `get_path_stats()` returned `{}` when empty, and had unguarded `max()` / division-by-zero. Now returns consistent dict with all keys.
- **`topology_visualizer.py`**: Already had `default=0` on `max()` - verified good.

### 3. HTTP Error Logging (`map_http_handler.py`)
- **Problem**: `log_message()` was `pass` - complete silence. Can't debug server issues.
- **Fix**: Routes through Python logger (goes to file, not TUI stdout). 4xx/5xx logged as WARNING, other requests as DEBUG.

### 4. Frontend API Health (`node_map.html`)
- **Problem**: Failed API fetches silently fell through to demo data. No indication of stale data.
- **Fix**: `updateApiStatus()` function tracks connection state. Live indicator shows "Live" (green), "Stale (Xm)" (orange), or "Disconnected" (red).

### 5. Node Role Filtering (RMAP-inspired)
- **Problem**: Could only filter by network (Meshtastic/RNS/AREDN). No way to find specific node types.
- **Fix**: Dropdown filter for: Router, Router+Client, Client, Repeater, Tracker, Sensor, TAK, Hidden, RNS Node. Applied in `updateDisplay()`.

### 6. Clustering Verification
- MarkerCluster already properly implemented with network-specific groups, health coloring, toggle control. No changes needed.

## Test Results
- **Baseline**: 3261 passed, 18 skipped
- **After**: 3274 passed (+13 new), 18 skipped, 0 failures
- New tests: 10 coordinate validation, 2 path visualizer, 1 topology visualizer

## Files Changed
| File | Lines Changed | What |
|------|--------------|------|
| `src/utils/map_data_collector.py` | +50/-4 | Centralized coordinate validation |
| `src/monitoring/path_visualizer.py` | +20/-2 | Empty stats + max() guards |
| `src/utils/map_http_handler.py` | +15/-2 | Error logging via Python logger |
| `web/node_map.html` | +69/-1 | API health, role filter |
| `tests/test_map_data_service.py` | +77 | Coordinate validation tests |
| `tests/test_topology_visualizer.py` | +12 | Empty stats test |
| `tests/test_path_visualizer.py` | +55 (NEW) | Path visualizer tests |

## Remaining Reliability Items (for future sessions)
- [ ] Service Detection Redesign (Issue #20) - HIGH priority
- [ ] AREDN HTTP response validation (map_data_collector line 1043)
- [ ] RNS cache path consolidation (/tmp vs ~/.config)
- [ ] Connection pooling for map data collection
- [ ] WebSocket/SSE for real-time map updates (currently 30s polling)
