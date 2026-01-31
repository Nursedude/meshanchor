# Session Notes: MeshForge-Owned Radio Control API

**Date**: 2026-01-31
**Branch**: `claude/setup-meshforge-noc-Vh1Pu`
**Focus**: NOC architecture - MeshForge owns all APIs

## Summary

Built the foundation for MeshForge to own the Meshtastic radio API directly, without depending on meshtasticd's web UI (port 9443).

## Changes Made

### 1. USB-Direct Mode Fix (`src/core/orchestrator.py`)
- Added `NOT_NEEDED` service state for services that don't need to run
- Handle `usb-direct` daemon type - skip meshtasticd from startup
- Remove rnsd dependency on meshtasticd in usb-direct mode

### 2. Connection Manager Enhancement (`src/utils/meshtastic_connection.py`)
- Added `ConnectionMode` enum: TCP, SERIAL, AUTO
- AUTO mode: prefers USB serial, falls back to TCP
- Auto-detection of USB devices (`/dev/ttyUSB*`, `/dev/ttyACM*`)
- `_create_serial_interface()` for direct USB connection
- MeshForge owns the radio without meshtasticd

### 3. Radio Control API (`src/utils/map_data_service.py`)
New endpoints on MapServer (port 5000):
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/radio/info` | GET | Device info |
| `/api/radio/nodes` | GET | Connected nodes |
| `/api/radio/channels` | GET | Channel config |
| `/api/radio/status` | GET | Connection status |
| `/api/radio/message` | POST | Send message |

### 4. Upgrade Script (`scripts/upgrade_to_native.sh`)
- Installs native meshtasticd from OBS repo
- Configures USB device
- Updates noc.yaml to native-usb mode

## Architecture Principle

**MeshForge = NOC = owns all APIs**

```
┌─────────────────────────────────────────┐
│           MeshForge (port 5000)         │
│  ┌─────────────────────────────────┐    │
│  │     MapServer + Radio API       │    │
│  └─────────────────────────────────┘    │
│              │                          │
│              ▼                          │
│  ┌─────────────────────────────────┐    │
│  │  MeshtasticConnectionManager    │    │
│  │  - AUTO/TCP/SERIAL modes        │    │
│  └─────────────────────────────────┘    │
│         │              │                │
│         ▼              ▼                │
│   ┌──────────┐   ┌──────────┐          │
│   │ USB Radio│   │meshtasticd│          │
│   │ (direct) │   │ (TCP)    │          │
│   └──────────┘   └──────────┘          │
└─────────────────────────────────────────┘
```

## Commits (in order)
1. `fix: Handle USB-direct mode in NOC orchestrator`
2. `docs: Add session notes for USB-direct mode fix`
3. `feat: Add upgrade script for native meshtasticd on USB radios`
4. `feat: Add MeshForge-owned radio control API`

## Next Session Tasks

1. **Integrate radio data into existing APIs**
   - Feed radio nodes into `/api/nodes/geojson`
   - Include radio status in `/api/status`

2. **WebSocket for real-time updates**
   - Replace polling with push
   - Node online/offline events
   - Message notifications

3. **Web dashboard enhancements**
   - Radio control panel in web UI
   - Send message form
   - Channel configuration view

4. **Test on real hardware**
   - Verify USB serial mode works
   - Test AUTO mode fallback logic

## Files Modified
- `src/core/orchestrator.py` - USB-direct mode handling
- `src/utils/meshtastic_connection.py` - Multi-mode connection
- `src/utils/map_data_service.py` - Radio control API
- `scripts/upgrade_to_native.sh` - New upgrade script

## User Environment
- Fresh NOC install
- USB radio (usb-direct mode)
- Services running but web client (9443) not available
- Need MeshForge to own the radio API on port 5000
