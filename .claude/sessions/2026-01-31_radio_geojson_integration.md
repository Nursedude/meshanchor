# Session Notes: Radio GeoJSON Integration

**Date**: 2026-01-31
**Branch**: `claude/fix-usb-direct-mode-WrFRa`
**Focus**: Integrate direct radio nodes into GeoJSON API and web UI

## Summary

Extended the previous session's work to make direct radio data available through the standard `/api/nodes/geojson` endpoint and added a radio control panel to the web UI.

## Changes Made

### 1. Direct Radio Collection (`src/utils/map_data_service.py`)

Added `_collect_direct_radio()` method in MapDataCollector:
- Uses MeshtasticConnectionManager in SERIAL mode
- Auto-detects USB devices (`/dev/ttyUSB*`, `/dev/ttyACM*`)
- Converts radio nodes to GeoJSON features
- Only runs when meshtasticd TCP is unavailable

**Collection flow**:
```
collect()
├── Source 1: meshtasticd TCP (localhost:4403)
├── Source 1.5: Direct USB radio (when TCP fails) ← NEW
├── Source 2: MQTT subscriber
├── Source 3: Node tracker cache
├── Source 4: AREDN mesh
└── Source 5: Last-known cache
```

### 2. Radio Status in `/api/status` (`src/utils/map_data_service.py`)

Added `_get_radio_status_summary()` to the status endpoint:
- Shows TCP availability (port 4403)
- Shows USB device availability
- Lists USB devices found
- Determines connection mode (tcp/serial/none)

**Response format**:
```json
{
  "status": "running",
  "radio": {
    "connected": true,
    "mode": "serial",
    "tcp_available": false,
    "usb_available": true,
    "usb_devices": ["/dev/ttyUSB0"]
  }
}
```

### 3. Radio Control Panel (`web/node_map.html`)

Added "Radio Control" section in the NOC control panel:
- Connection status indicator (green/red)
- Connection mode display (TCP/SERIAL)
- Radio info (node name, hardware) when connected
- Send message form with destination field
- Auto-refresh every 30 seconds

**JavaScript functions**:
- `refreshRadioStatus()` - Fetches `/api/radio/status` and `/api/radio/info`
- `sendRadioMessage()` - POSTs to `/api/radio/message`

## Commits

1. `f05e58e` - `feat: Integrate direct radio nodes into geojson API`
2. `c1e7799` - `feat: Add radio control panel to web UI`

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    Web Browser                               │
│  ┌─────────────────────────────────────────────────────┐    │
│  │             node_map.html                            │    │
│  │  ┌─────────────────────────────────────────────────┐ │    │
│  │  │  Radio Control Panel                            │ │    │
│  │  │  - Status: Connected (SERIAL)                   │ │    │
│  │  │  - Node: My Radio (!abc123)                     │ │    │
│  │  │  - [Message] [Send]                             │ │    │
│  │  └─────────────────────────────────────────────────┘ │    │
│  └─────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                  MeshForge (port 5000)                       │
│  ┌─────────────────────────────────────────────────────┐    │
│  │  MapDataCollector.collect()                         │    │
│  │  - _collect_meshtasticd() → TCP:4403               │    │
│  │  - _collect_direct_radio() → USB Serial  ← NEW     │    │
│  │  - _collect_mqtt()                                  │    │
│  │  - _collect_node_tracker()                          │    │
│  └─────────────────────────────────────────────────────┘    │
│                              │                               │
│                              ▼                               │
│  ┌─────────────────────────────────────────────────────┐    │
│  │  MeshtasticConnectionManager                        │    │
│  │  Mode: AUTO → SERIAL (if USB detected)             │    │
│  └─────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
                    ┌──────────────────┐
                    │   USB Radio      │
                    │  /dev/ttyUSB0    │
                    └──────────────────┘
```

## Deferred Tasks

### WebSocket Support
- Requires external dependencies (websockets/aiohttp)
- Not available in current environment
- Current 30s polling is acceptable for NOC use case
- Consider SSE (Server-Sent Events) as simpler alternative

## Testing Notes

- No pytest available in environment
- Syntax verified with `python3 -m py_compile`
- Import verification: `from utils.map_data_service import MapDataCollector`
- HTML validated with Python html.parser

## Next Session Priorities

1. **Test on real USB hardware** - Verify serial mode works
2. **WebSocket/SSE** - Requires adding dependencies to project
3. **Message history** - Store sent/received messages in UI
4. **Channel display** - Show channel config in radio panel

## Files Modified

| File | Lines Changed | Purpose |
|------|---------------|---------|
| `src/utils/map_data_service.py` | +144 | Direct radio collection, status endpoint |
| `web/node_map.html` | +130 | Radio control panel UI |
