# HamClock Decoupling — Session Notes

> Session: 2026-02-08 | Branch: claude/decouple-meshforge-hamclock-52CXI

## Context

The original HamClock developer (Elwood Downey, WB0OEW) is SK. The original
HamClock backend is scheduled to **sunset June 2026**. MeshForge needed to be
decoupled from HamClock as a dependency.

**OpenHamClock** (https://github.com/accius/openhamclock) is the community
replacement — MIT license, React/Node.js, Docker-friendly, port 3000.

## What Was Done

### Architecture Change: NOAA Primary, HamClock Optional

**Before:** HamClock was the primary data source, NOAA was the "fallback"
**After:** NOAA SWPC is the primary source (always works), HamClock/OpenHamClock are optional enhancements

### New File: `src/commands/propagation.py`

MeshForge-owned propagation command module. This is the new recommended
interface for all space weather and propagation data.

Features:
- `get_space_weather()` — NOAA SWPC primary, always works
- `get_band_conditions()` — Derived from NOAA SFI/Kp/A-index
- `get_propagation_summary()` — One-line summary
- `get_alerts()` — NOAA space weather alerts
- `get_enhanced_data()` — NOAA + optional HamClock/OpenHamClock
- `configure_source()` — Configure optional data sources
- `check_source()` — Test connectivity to any source
- `DataSource` enum: NOAA, OPENHAMCLOCK, HAMCLOCK

### Modified Files

1. **`src/commands/hamclock.py`**
   - Updated docstring: marked as optional data source plugin
   - Added OpenHamClock reference and sunset warning
   - Inverted auto-fallback: NOAA primary, HamClock enhances
   - All functions still work for backward compatibility

2. **`src/commands/__init__.py`**
   - Added `propagation` to imports and `__all__`
   - Updated usage docstring

3. **`src/launcher_tui/settings_menu_mixin.py`**
   - "HamClock Settings" → "Propagation Data Sources"
   - New submenu: NOAA (test), OpenHamClock (configure), HamClock Legacy
   - "Test All Sources" option
   - Sunset warning for legacy HamClock

4. **`src/launcher_tui/service_discovery_mixin.py`**
   - HamClock only shown in discovery if running
   - Labeled as "(optional)" in discovery and status
   - Core vs optional service separation in status overview

5. **`tests/test_propagation.py`** (new)
   - 31 tests covering all new functionality
   - Backward compatibility tests for hamclock module

### Not Changed (Already Independent)

- `src/utils/space_weather.py` — Already standalone NOAA client
- `src/launcher_tui/status_bar.py` — Already uses space_weather.py directly
- `src/utils/ports.py` — HAMCLOCK_PORT kept for service detection
- `src/utils/service_check.py` — hamclock still in KNOWN_SERVICES for optional detection

## Test Results

**3311 passed, 19 skipped, 0 failures**

## Completed (Session 2026-02-08b — branch: claude/persist-config-docs-Kpb7l)

### P1 — Done ✅
- [x] Persist propagation source config to disk via SettingsManager
  - `propagation.json` in `~/.config/meshforge/`
  - Auto-loads on module import, auto-saves on `configure_source()`
  - Graceful degradation if SettingsManager unavailable
- [x] Update CLAUDE.md: Added `commands/` to architecture, propagation docs section
- [x] Update `hamclock_complete.md`: Added OpenHamClock section, sunset warning, feature comparison

### P2 — Done ✅
- [x] Docker OpenHamClock management in service menu (start/stop/status/logs)
  - Auto-configures MeshForge propagation source on first Docker start
- [x] Direct DX cluster telnet: `get_dx_spots_telnet()` — connects to DX Spider nodes
- [x] VOACAP online: `get_voacap_online()` — public VOACAP P2P predictions
- [x] Ionosonde data: `get_ionosonde_data()` — real foF2/MUF from prop.kc2g.com
- [ ] PSKReporter integration via MQTT (like OpenHamClock) — future

### P3 — Done ✅
- [x] Deprecation warnings on `hamclock.get_space_weather_auto()`, `get_band_conditions_auto()`, `get_propagation_summary()`
- [x] CelesTrak TLE: `get_satellite_tle()` — standalone satellite tracking
- [ ] Remove HamClock from KNOWN_SERVICES if sunset confirmed — future

### Tests
- 46 tests in `test_propagation.py` (was 31) — all passing
- 155 tests across affected test files — all passing

## Data Source Comparison

| Feature | NOAA SWPC | OpenHamClock | HamClock (legacy) |
|---------|-----------|--------------|-------------------|
| SFI | Yes | Yes (proxied) | Yes |
| Kp/A-index | Yes | Yes (proxied) | Yes |
| X-ray flux | Yes | Yes (proxied) | Yes |
| Band conditions | Derived | Display only | Yes |
| VOACAP | No | ITU-R P.533 | Yes |
| DX spots | No | Yes (DX Spider) | Yes |
| Satellite | No | Yes (CelesTrak) | Yes |
| PSKReporter | No | Yes (MQTT) | No |
| POTA/SOTA | No | Yes | No |
| Availability | Always | Self-hosted | Sunsets June 2026 |
| MeshForge status | PRIMARY | Optional plugin | Optional/legacy |
