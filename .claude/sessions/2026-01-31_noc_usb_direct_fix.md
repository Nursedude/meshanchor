# Session Notes: NOC USB-Direct Mode Fix

**Date**: 2026-01-31
**Branch**: `claude/setup-meshforge-noc-Vh1Pu`
**Issue**: Fresh install NOC startup failure for USB radios

## Problem

On fresh install with USB radio configured in `usb-direct` mode:
```
meshtasticd started but not healthy, retrying check...
meshtasticd failed health check
Required service meshtasticd failed to start
```

Status showed:
```
✗ meshtasticd: failed
    meshtasticd running but not responding
```

## Root Cause

The NOC orchestrator was checking port 4403 for all daemon types, but:
- USB radios in `usb-direct` mode don't have a meshtasticd daemon running
- The radio firmware handles mesh networking internally
- Users interact via `meshtastic --port /dev/ttyUSB0` CLI directly
- There's no daemon to respond on port 4403

## Solution

Updated `src/core/orchestrator.py`:

1. **Added `NOT_NEEDED` state** - New service state for services that don't need to run
2. **Handle `usb-direct` daemon type**:
   - Mark meshtasticd as `required=False`
   - Remove from `STARTUP_ORDER`
   - Remove meshtasticd dependency from rnsd
   - Display helpful message about using CLI directly
3. **Made `STARTUP_ORDER` an instance attribute** - Allows per-instance modification
4. **Added `check_binary` to service configs** - Better install detection

## Expected Behavior After Fix

```
Mode: local | Radio: usb | Daemon: usb-direct

— meshtasticd: not_needed
    USB-direct mode: no daemon needed (use meshtastic CLI directly)
✓ rnsd: running
```

## Files Changed

- `src/core/orchestrator.py` - USB-direct mode handling

## Testing

Verified:
- Config loads correctly with `usb-direct` daemon type
- Startup order excludes meshtasticd
- Status shows `not_needed` instead of `failed`
- rnsd starts without meshtasticd dependency

## Notes for User

After this fix, on USB-direct systems:
1. NOC startup should succeed (rnsd starts without meshtasticd)
2. Use `meshtastic --port /dev/ttyUSB0 --info` for radio interaction
3. meshtasticd service is a placeholder only
