# Session: Map Server Stability & RF Calculator Restoration
**Date**: 2026-01-30
**Duration**: ~20 hours (across multiple sessions)
**Branch**: `claude/setup-mesh-map-server-9h1g1`

## Summary

Major stability improvements to Live NOC Map and restoration of RF Calculator functionality from the frozen GTK4 codebase into the web-based TUI workflow.

## Commits This Session

1. `ec1da78` - fix: Suppress stdout/stderr during map server init to prevent TUI crash
2. `34f6f6b` - fix: Show total/mapped/no-GPS node counts and silence HTTP logging
3. `49d1186` - feat: Add Hawaii location presets to RF LOS calculator
4. `b25b31f` - feat: Add Leaflet map to RF LOS calculator
5. `1f89d0d` - fix: Allow page scrolling in RF Calculator for smaller screens
6. `3bd1c2c` - fix: Add no-cache headers to map server to prevent browser caching

## Issues Resolved

### TUI Crash on Map Server Launch
**Root cause**: Multiple sources of stdout/stderr output corrupting whiptail display
- HTTP request logging (`127.0.0.1 - GET /api/nodes/geojson`)
- Server initialization messages
- SQLite "readonly database" errors from node history

**Fix**:
- Wrapped server init in `redirect_stdout`/`redirect_stderr` context managers
- Raised logging level to `CRITICAL+1` during init
- Silenced HTTP `log_message()` completely (returns `pass`)

### Database Permission Issues
**Root cause**: Mixed ownership in `~/.local/share/meshforge/` - directory owned by root, files by user
**Fix**: `sudo chown -R $(logname):$(logname) ~/.local/share/meshforge/`

### Browser Caching Old Files
**Root cause**: SimpleHTTPRequestHandler doesn't send cache-control headers
**Fix**: Added `Cache-Control: no-cache, no-store, must-revalidate` headers for all HTML files

## Features Added

### RF Calculator Restoration
Restored functionality from frozen GTK4 to web-based tool:
- **Hawaii location presets**: Big Island (Hilo, Volcano, Kona, Mauna Kea, Mauna Loa, Waimea, Pahoa, Ocean View), Oahu (Honolulu, Pearl Harbor, North Shore, Diamond Head), Other islands + mainland
- **Leaflet map**: Dark theme (CartoDB tiles), Point A/B markers, RF path line colored by LOS status
- **Elevation profile**: Chart.js visualization with Fresnel zone clearance
- **Two-column layout**: Map + chart side-by-side

### Node Count Clarity
- Web UI now shows: "Total Seen / Mapped / No GPS"
- Explains why node counts differ from meshtastic browser (only GPS nodes can be mapped)

## Files Modified

- `src/launcher_tui/ai_tools_mixin.py` - TUI output suppression
- `src/utils/map_data_service.py` - HTTP logging silence, cache headers, static HTML serving
- `web/node_map.html` - Node count display, RF Calculator button
- `web/los_visualization.html` - Complete rewrite with presets, Leaflet, elevation profile

## Next Steps

### Immediate
- Clean install testing on HAT and USB nodes
- Monitor stability over extended runtime

### Friday
- Gateway bridging: longfast <> meshforge <> short turbo
- This is core value prop - connecting incompatible mesh channels

### Future Map Development
1. **Trajectory playback** - Node history DB is recording, add UI for playback
2. **Link lines** - Draw SNR-colored lines between nodes that hear each other
3. **Offline tiles** - Pre-cache map tiles for field ops without internet

## Notes

- RF LOS Calculator described as "one of the cool things of meshforge"
- Hawaii terrain makes link planning particularly valuable (volcanic slopes, valleys)
- Both local and remote nodes now stable after permission fixes
