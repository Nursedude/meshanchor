# Session Notes: MeshForge Map Service Standalone

**Date**: 2026-01-31
**Branch**: `claude/radio-geojson-integration-eKnp7`
**Focus**: Make MeshForge web UI a standalone systemd service for reliability

## Summary

Upgraded the MeshForge web UI from an in-process TUI component to a standalone systemd service. This improves reliability - the map server now persists independently of the TUI lifecycle and starts automatically on boot.

## Problem

The previous architecture had the web UI (port 5000) running as a background thread inside the TUI process:
- Server died when TUI exited
- No auto-start on boot
- Connectivity issues when TUI crashed/restarted
- Not suitable for NOC operations requiring 24/7 uptime

## Solution

Created `meshforge-map` as a first-class systemd service:

### 1. Enhanced CLI Entry Point (`src/utils/map_data_service.py`)

Extended `main()` with service-friendly features:
- `--daemon` mode for systemd
- `--status` to check if running
- `--pid-file` for service management
- Signal handlers (SIGTERM, SIGINT) for graceful shutdown
- Port conflict detection

### 2. Systemd Service (`scripts/meshforge-map.service`)

```ini
[Unit]
Description=MeshForge Map Server - NOC Web Interface
After=network.target meshtasticd.service
Wants=meshtasticd.service

[Service]
Type=simple
ExecStart=/opt/meshforge/venv/bin/python -m utils.map_data_service --daemon --port 5000
Restart=on-failure
```

### 3. CLI Command (`/usr/local/bin/meshforge-map`)

Wrapper script for easy management:
```bash
meshforge-map start    # Start service
meshforge-map stop     # Stop service
meshforge-map status   # Check status
meshforge-map url      # Show access URL
meshforge-map enable   # Enable on boot
```

### 4. Installer Integration (`scripts/install_noc.sh`)

- Installs service file to `/etc/systemd/system/`
- Creates `/usr/local/bin/meshforge-map` command
- Enables and starts service on install
- Updated summary output to show new commands

### 5. TUI Integration (`src/launcher_tui/ai_tools_mixin.py`)

Modified `_start_map_server()` and `_maybe_auto_start_map()`:
- First tries systemd service (preferred)
- Falls back to in-process if systemd unavailable
- Shows service status in dialogs
- New helper methods: `_try_start_map_service()`, `_get_map_service_status()`

## Architecture

```
BEFORE:
┌─────────────────────────────────────┐
│  TUI Process                        │
│  ├── Whiptail dialogs               │
│  └── MapServer thread (port 5000)   │  ← Dies with TUI
└─────────────────────────────────────┘

AFTER:
┌─────────────────────────────────────┐
│  systemd                            │
│  ├── meshforge-map.service          │  ← Persistent, auto-start
│  │   └── python -m utils.map_data_service
│  │       └── HTTPServer :5000       │
│  │                                  │
│  └── (optional) meshforge.service   │
└─────────────────────────────────────┘
          │
          │ TUI detects existing service
          ▼
┌─────────────────────────────────────┐
│  TUI Process                        │
│  └── Uses existing service OR       │
│      falls back to in-process       │
└─────────────────────────────────────┘
```

## Files Modified

| File | Changes |
|------|---------|
| `src/utils/map_data_service.py` | Enhanced main() with daemon mode, status check, signal handling |
| `src/launcher_tui/ai_tools_mixin.py` | Added systemd service detection and fallback logic |
| `scripts/install_noc.sh` | Added meshforge-map service installation |
| `scripts/meshforge-map.service` | NEW - systemd unit file |

## Testing

- `python3 -m utils.map_data_service --help` - CLI works
- `python3 -m utils.map_data_service --status` - Status check works
- `python3 -m utils.map_data_service --collect-only` - Data collection works
- Syntax validation passed for all modified files

## Commands Added

```bash
meshforge-map start      # Start map server
meshforge-map stop       # Stop map server
meshforge-map restart    # Restart
meshforge-map status     # Check status
meshforge-map enable     # Enable on boot
meshforge-map disable    # Disable on boot
meshforge-map url        # Show access URL
meshforge-map -p 8080    # Run on custom port (interactive)
```

## Next Steps

1. **Test on real hardware** - Verify systemd service starts correctly
2. **Add to meshforge-noc orchestrator** - Service coordination
3. **Health checks** - Add /api/health endpoint for monitoring
4. **Nginx integration** - Optional reverse proxy for HTTPS
