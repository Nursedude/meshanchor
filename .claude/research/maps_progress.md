# Maps Development Progress

> **Branch**: `alpha`
> **Started**: 2026-01-29
> **Last Updated**: 2026-01-29

## Discovery: Phase 1 Already Implemented!

Upon code review, Phase 1 of the live map engine is **already complete**:

| Component | File | Status |
|-----------|------|--------|
| Leaflet.js live map | `web/node_map.html` | ✅ Complete |
| Map data server | `src/utils/map_data_service.py` | ✅ Complete |
| Node state animations | CSS in node_map.html | ✅ Complete |
| TUI integration | `src/launcher_tui/ai_tools_mixin.py` | ✅ Complete |
| Data collection | `MapDataCollector` class | ✅ Complete |

### Existing Features (web/node_map.html)
- Leaflet.js with dark theme
- Pulse animations for new nodes
- Online/offline status visualization
- Network type badges (Meshtastic, RNS, AREDN)
- Coverage circles
- Link lines between nodes
- Filter controls (network type, online-only)
- Auto-refresh polling

### Existing Server (map_data_service.py)
- HTTP server with JSON API
- `/api/nodes` endpoint returns GeoJSON
- Data collection from meshtasticd, MQTT, RNS
- Node history database
- Link calculation

---

## Current Phase: 2 — RF-Aware Visualization

### Task Status

| Task | Status | Notes |
|------|--------|-------|
| 5. Terrain-aware coverage | ✅ Done | `terrain.py` already exists with SRTM + LOS |
| 5b. API integration | ✅ Done | `/api/coverage/` and `/api/los/` endpoints added |
| 7. Coverage prediction overlay | ✅ Done | Click node → "Show Coverage" button |
| 6. Link quality animation | 🔴 Not started | Colors exist, no pulse animation |
| 8. Signal heatmap from real data | 🔴 Not started | Needs data collection over time |

### Files to Create (Phase 2)

```
src/utils/terrain.py         # SRTM elevation data, LOS calculation
src/utils/node_history.py    # SQLite time-series (may already exist)
```

### Files to Modify

- `web/node_map.html` — Add coverage prediction toggle
- `src/utils/coverage_map.py` — Add terrain-aware coverage
- `src/utils/rf.py` — May need additional path loss models

---

## Session Log

### Session 1 (2026-01-29)
- [x] Synced alpha with main
- [x] Updated README (removed GTK references)
- [x] Discovered Phase 1 already implemented
- [x] Updated roadmap for TUI-only architecture
- [x] Phase 2: Added `/api/coverage/` and `/api/los/` endpoints
- [x] Phase 2: Added terrain coverage overlay to map
- [x] Phase 2: Node click → terrain analysis button

**Completed:**
- Task 5: Terrain-aware coverage (API + map integration)
- Task 7: Coverage prediction overlay

**Next Steps (new session):**
1. Task 6: Link quality animation (pulse on active links)
2. Task 8: Signal heatmap from real measurements

---

## Quick Resume

To continue map development in a new session:
```
"Continue map dev on alpha"
```

I'll read this file and pick up where we left off.

---

## Existing Infrastructure Reference

```
web/
└── node_map.html           # Leaflet.js live map (44KB, full-featured)

src/utils/
├── map_data_service.py     # MapServer + MapDataCollector (31KB)
├── coverage_map.py         # Folium static map generator
├── node_history.py         # SQLite node history (if exists)
└── rf.py                   # RF calculations (path loss, Fresnel)

src/launcher_tui/
└── ai_tools_mixin.py       # TUI menu integration
```
