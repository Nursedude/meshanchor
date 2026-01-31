# Systemctl Refactoring - Next Session Tasks

**Created**: 2026-01-31
**Related PR**: `claude/refactor-systemctl-calls-ryIH0`
**Status**: Ready for next session

## Completed (This Session)

- [x] Added `ServiceState.FAILED` to service_check.py
- [x] Added `apply_config_and_restart()` helper function
- [x] Refactored service_menu_mixin.py (failed state detection)
- [x] Refactored diagnose.py (OpenWebRX check)
- [x] Refactored first_run_mixin.py (2 restart patterns)
- [x] Refactored meshtasticd_config_mixin.py (2 restart patterns)

## Remaining Systemctl Calls (Lower Priority)

### Display/Diagnostic Commands (Leave as-is)
- `system_tools_mixin.py`: `list-units`, `--failed`, `list-timers`
  - These are raw output for user display, not service control

### Service Setup Patterns (Consider `enable_service()` helper)
- `spi_hats.py`: `daemon-reload` + `enable` for resume service
- `setup_wizard.py`: `daemon-reload` + `enable rnsd`

### Sudo-prefixed Calls
- `hardware_config.py`: `sudo systemctl restart meshtasticd`
  - Review if sudo is needed (TUI runs as root already?)

### System Power Operations (Different use case)
- `main.py`: `reboot`, `poweroff`
  - Not service management - leave as-is

## Suggested Helper Functions

```python
# Potential additions to service_check.py:

def enable_service(service_name: str) -> Tuple[bool, str]:
    """Enable service at boot."""
    ...

def daemon_reload(timeout: int = 30) -> Tuple[bool, str]:
    """Just reload systemd daemon (for enable operations)."""
    ...
```

## Documentation Updates

1. Update CLAUDE.md with service_check.py usage examples
2. Consider adding docstring examples to service_check.py (already done)
3. Version notes for 0.4.8 if merging

## Reliability Concerns to Review

1. Error message consistency across TUI dialogs
2. Timeout values (currently 30s default - appropriate?)
3. Fallback behavior when service_check module unavailable
