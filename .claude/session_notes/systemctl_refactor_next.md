# Systemctl Refactoring - Session Tracking

**Created**: 2026-01-31
**Related Branch**: `claude/systemctl-refactor-tracking-UWPkt`
**Status**: ~~Phase 2 complete~~ **Phase 3 complete — all config modules and setup wizard refactored**

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

## Remaining Items (Leave As-Is)

### Display/Diagnostic Commands
- `system_tools_mixin.py`: `list-units`, `--failed`, `list-timers`
  - These are raw output for user display, not service control

### System Power Operations
- `main.py`: `reboot`, `poweroff`
  - Not service management - different use case

### Interactive Service Control (TUI Mixins)
- `service_menu_mixin.py`, `quick_actions_mixin.py`, `rns_menu_mixin.py`, etc.
  - Already use `_sudo_cmd()` wrapper or centralized helpers
  - Some use raw subprocess for direct user output (intentional)

### Shell Scripts
- `scripts/install_noc.sh`, `scripts/update.sh`, etc.
  - Shell scripts run with sudo by design — not Python code

## Files Modified (Session 4)

1. `src/utils/service_check.py` - Added start_service(), stop_service(), _sudo_write()
2. `src/config/hardware_config.py` - Replaced 14 hardcoded sudo/systemctl calls
3. `src/config/config_file_manager.py` - Direct import, service_check helpers, _sudo_write()
4. `src/config/spi_hats.py` - Direct import, _sudo_cmd(), _sudo_write()
5. `src/setup_wizard.py` - Direct import, start_service(), _sudo_write()
6. `templates/sudoers.d/meshforge-nopasswd` - New NOPASSWD sudoers template
7. `CLAUDE.md` - Service management documentation expanded
8. `.claude/foundations/domain_architecture.md` - Privilege helpers + Phase 2 checklist

## Branch Strategy Note

- Alpha is **89 commits ahead** of main
- Main should stay frozen until release
- All development on alpha (via feature branches)
- This branch merges to alpha

## Next Steps

1. [x] Commit and push changes (Session 4)
2. PR for review
3. Consider extending to `commands/rns.py` and `commands/service.py` (lower priority — these
   have hardcoded `['sudo', 'systemctl']` but work correctly since always run with sudo)
4. Consider extending to `plugins/meshchat/service.py` (same pattern)
