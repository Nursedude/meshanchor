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

## What Remains (Future Sessions)

### P1 — Should Do
- [ ] Persist propagation source configuration to disk (SettingsManager)
- [ ] Update CLAUDE.md architecture section and usage examples
- [ ] Update `.claude/research/hamclock_complete.md` with OpenHamClock info

### P2 — Nice to Have
- [ ] Add OpenHamClock as Docker service option in service manager
- [ ] Direct DX cluster telnet support (bypass HamClock for DX spots)
- [ ] VOACAP online service integration (independent of HamClock)
- [ ] Add ionosonde data from prop.kc2g.com (like OpenHamClock does)
- [ ] PSKReporter integration via MQTT (like OpenHamClock)

### P3 — Consider
- [ ] Deprecation warnings for code calling hamclock module directly
- [ ] Remove HamClock from KNOWN_SERVICES if sunset confirmed
- [ ] CelesTrak TLE integration for standalone satellite tracking

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
