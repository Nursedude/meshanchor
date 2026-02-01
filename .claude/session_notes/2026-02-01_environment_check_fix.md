# Session Notes: Environment Check Alert Fix

**Date**: 2026-02-01
**Branch**: `claude/environment-privilege-check-bpphK`

## Issue

The startup environment check was showing a noisy alert:
```
Service rnsd running but not enabled at boot
```

This appeared even though rnsd was running correctly. The user knew the service was working - they could see it reported as "running" in multiple places in the UI.

## Root Cause

In `src/launcher_tui/startup_checks.py`, the `get_alerts()` method (line 179) was generating alerts for services that were:
- Running (`ServiceRunState.RUNNING`)
- But not enabled at boot (`not info.enabled_at_boot`)

This is not actually an error condition - it's a user preference. Many users intentionally run services manually or start them via the TUI rather than enabling them at boot.

## Fix

Removed the "running but not enabled at boot" alert from `get_alerts()`:

**Before** (line 192-193):
```python
elif info.state == ServiceRunState.RUNNING and not info.enabled_at_boot:
    alerts.append(f"Service {name} running but not enabled at boot")
```

**After**:
```python
# Note: We intentionally don't alert on "running but not enabled at boot"
# since service is working - boot-enable is a user preference, not an issue
```

## What Still Alerts

The environment check still alerts for genuine issues:
- Port conflicts (e.g., port 4403 used by unexpected process)
- Failed services (`ServiceRunState.FAILED`)

## Verification

- Syntax check: PASS
- Linter: PASS (only pre-existing MF004 warning in main.py)
- Module import: PASS

## Files Changed

- `src/launcher_tui/startup_checks.py` - Removed noisy boot-enable alert

## Session Health

Session remained focused on single issue. No entropy detected.
