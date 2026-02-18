# Systemctl Refactoring - Session Tracking

**Created**: 2026-01-31
**Related Branch**: `claude/systemctl-refactor-tracking-UWPkt`
**Status**: ~~Phase 2 complete~~ ~~Phase 3 complete~~ **DONE — all systemctl service-control calls centralized**

## Session 1 Completed (Previous)

- [x] Added `ServiceState.FAILED` to service_check.py
- [x] Added `apply_config_and_restart()` helper function
- [x] Refactored service_menu_mixin.py (failed state detection)
- [x] Refactored diagnose.py (OpenWebRX check)
- [x] Refactored first_run_mixin.py (2 restart patterns)
- [x] Refactored meshtasticd_config_mixin.py (2 restart patterns)

## Session 2 Completed (This Session)

- [x] Added `daemon_reload()` helper to service_check.py
- [x] Added `enable_service()` helper to service_check.py (with optional start parameter)
- [x] Refactored spi_hats.py to use `enable_service()`
- [x] Refactored setup_wizard.py to use `enable_service()`
- [x] Refactored service_menu_mixin.py (2 more patterns):
  - daemon-reload + restart → `apply_config_and_restart()`
  - daemon-reload + enable + restart → `enable_service(start=True)`
- [x] Refactored main.py (1 pattern):
  - daemon-reload + restart → `apply_config_and_restart()`
- [x] Reviewed hardware_config.py sudo usage:
  - **Decision**: Leave as-is - module can run standalone, sudo is intentional

## Session 3 Completed (Documentation & Tests)

- [x] Fixed README version badge: `0.4.8-beta` → `0.4.8-alpha` (orange)
- [x] Updated CLAUDE.md:
  - Version reference to 0.4.8-alpha
  - Added `service_check.py` to architecture overview
  - New "Service Management" section with usage examples
  - Documented all helpers with code examples
- [x] Added comprehensive tests to `tests/test_service_check.py`:
  - `TestDaemonReload` - 4 tests
  - `TestEnableService` - 5 tests
  - `TestApplyConfigAndRestart` - 4 tests
  - `TestServiceHelpersIntegration` - 2 tests

## Session 4 Completed (Config Modules + sudo_write)

**Branch**: `claude/refactor-systemctl-calls-Kugxd`

### New Helpers Added to service_check.py
- [x] `start_service(name)` — start a systemd service with `_sudo_cmd()` elevation
- [x] `stop_service(name)` — stop a systemd service with `_sudo_cmd()` elevation
- [x] `_sudo_write(path, content)` — write to system paths via `sudo tee` when not root

### Files Refactored
- [x] **hardware_config.py** — 14 calls: all `['sudo', 'systemctl']` and `['sudo', 'raspi-config']`
      replaced with `_sudo_cmd()`, `apply_config_and_restart()`, `start_service()`, `stop_service()`
- [x] **config_file_manager.py** — 6 calls: switched from `safe_import` to direct import,
      replaced bare `['systemctl']` with `daemon_reload()`, `apply_config_and_restart()`,
      `_sudo_cmd()`, `_sudo_write()`; removed fallback paths
- [x] **spi_hats.py** — 4 calls + 2 file writes: switched to direct import, replaced bare
      `['systemctl']` and `['reboot']` with `_sudo_cmd()`; direct `/etc/` writes → `_sudo_write()`
- [x] **setup_wizard.py** — 6 calls + 1 file write: switched to direct import, replaced bare
      `['systemctl']` with `start_service()`, `enable_service()`; service file write → `_sudo_write()`

### Documentation
- [x] CLAUDE.md — added `start_service`/`stop_service`, `_sudo_write`, `_sudo_cmd` sections + sudoers template
- [x] domain_architecture.md — privilege elevation helpers table, Phase 2 checklist updated
- [x] NOPASSWD sudoers template added at `templates/sudoers.d/meshforge-nopasswd`

### Verification
- [x] All modified files pass `py_compile`
- [x] `scripts/lint.py --all` passes clean (MF001-MF004)
- [x] Zero bare `['systemctl']` or `['sudo', 'systemctl']` in target files
- [x] Zero direct `open('/etc/...', 'w')` writes in target files

## Documentation Updates (CLOSED)

1. [x] Update CLAUDE.md with service_check.py usage examples (Session 3 + Session 4)
2. [x] Docstring examples in service_check.py (already done)
3. [x] ~~Version notes for 0.4.8 if merging~~ Obsolete (now on 0.5.4-beta)

## Session 5 Completed (Final Sweep — Commands, Plugins, TUI Mixins)

**Branch**: `claude/refactor-systemctl-calls-jactg`

### New Helpers Added to service_check.py
- [x] `restart_service(name)` — simple restart without daemon-reload
- [x] `disable_service(name)` — disable a service at boot

### Files Refactored (16 files, -312/+217 lines)
- [x] **commands/rns.py** — 2 calls: `['sudo', 'systemctl']` → `start_service()`/`stop_service()`
- [x] **commands/service.py** — 5 calls: all operations → helpers (start/stop/restart/enable/disable)
- [x] **plugins/meshchat/service.py** — 2 calls: start/stop → helpers
- [x] **system_tools_mixin.py** — 1 interactive restart → `apply_config_and_restart()`
- [x] **service_menu_mixin.py** — 13 calls: dead `_HAS_*` fallback branches removed
- [x] **meshtasticd_config_mixin.py** — 2 fallback restart patterns simplified
- [x] **first_run_mixin.py** — 3 identical restart fallbacks collapsed
- [x] **rns_menu_mixin.py** — 2 calls: stop/start rnsd → helpers
- [x] **main.py** — 1 config-conflict restart fallback removed
- [x] **web_client_mixin.py** — 1 ImportError fallback removed
- [x] **rns_diagnostics_mixin.py** — 2 start rnsd calls
- [x] **ai_tools_mixin.py** — 2 start meshforge-map calls
- [x] **nomadnet_client_mixin.py** — 4 rnsd lifecycle calls
- [x] **quick_actions_mixin.py** — 2 quick restart calls
- [x] **updates_mixin.py** — 1 daemon-reload → `daemon_reload()`

### Verification
- [x] All 16 modified files pass `py_compile`
- [x] 1526 tests pass, 0 failures
- [x] Zero `['sudo', 'systemctl', ...]` calls outside service_check.py
- [x] Zero `_sudo_cmd(['systemctl', 'start|stop|restart|enable|disable|daemon-reload'])` outside service_check.py

## Remaining Items (Intentionally Left As-Is)

### Display/Diagnostic Commands
- `system_tools_mixin.py`: `list-units`, `--failed`, `list-timers`, `status`
  - Raw terminal output for user display, not service control

### System Power Operations
- `main.py`: `systemctl reboot`, `systemctl poweroff`
  - Not service management — different use case

### Read-Only systemctl Queries
- `commands/service.py`: `systemctl is-active`, `systemctl is-enabled`, `systemctl cat`, `systemctl status`
- `plugins/meshchat/service.py`: `systemctl is-active`, `systemctl is-enabled`, `systemctl show`
- `cli/diagnose.py`: `systemctl is-active`
  - Status queries only — no privilege elevation needed

### Shell Scripts
- `scripts/install_noc.sh`, `scripts/update.sh`, etc.
  - Shell scripts run with sudo by design — not Python code

## Complete Helper API (service_check.py)

| Helper | Added | Session |
|--------|-------|---------|
| `check_service()` | Pre-existing | — |
| `daemon_reload()` | Session 2 | S2 |
| `enable_service()` | Session 2 | S2 |
| `disable_service()` | Session 5 | S5 |
| `apply_config_and_restart()` | Session 1 | S1 |
| `start_service()` | Session 4 | S4 |
| `stop_service()` | Session 4 | S4 |
| `restart_service()` | Session 5 | S5 |
| `_sudo_cmd()` | Pre-existing | — |
| `_sudo_write()` | Session 4 | S4 |

## Status: COMPLETE

All systemctl service-control calls in the Python codebase now go through
`utils/service_check.py` as the SINGLE SOURCE OF TRUTH. No further refactoring needed.
