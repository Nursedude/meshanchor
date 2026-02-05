# Session Notes: NomadNet SUDO_USER Fix

**Date:** 2026-02-05
**Branch:** `claude/review-session-notes-2NhZ2`
**Commits:** 050e494, bc76d7d

---

## Problem Identified

User reported NomadNet failing with RPC authentication error on fresh install:

```
NomadNet exited with error code 1

Diagnosis:
  - RPC authentication failed between NomadNet and rnsd
  - rnsd runs as 'wh6gxz', you are 'root'
```

**Root cause:** Fresh install flow tells users to run `sudo meshforge`, but SUDO_USER may not be set (especially after `su -` or direct root login). This causes:
- rnsd runs as regular user
- NomadNet runs as root (because SUDO_USER is empty, privilege drop doesn't happen)
- Different users = different RNS identities = RPC auth failure

---

## Fixes Implemented

### 1. NomadNet Launch Detection (`nomadnet_client_mixin.py`)

Added Case 2 in `_check_rns_for_nomadnet()`:

```python
elif we_are_root and rnsd_user and rnsd_user != 'root' and not sudo_user:
    # Case 2: We're root but SUDO_USER not set, rnsd runs as user
    choice = self.dialog.menu(
        "User Mismatch Detected",
        f"rnsd is running as '{rnsd_user}', but SUDO_USER is not set...",
        [
            ("run_as_user", f"Run NomadNet as '{rnsd_user}' (recommended)"),
            ("stop", "Stop rnsd (NomadNet will use its own RNS)"),
            ("cancel", "Cancel"),
        ],
    )
```

If user chooses "run_as_user", temporarily sets `os.environ['SUDO_USER'] = rnsd_user`.

### 2. Startup Warning (`main.py`)

Added `_check_root_without_sudo_user()` method called early in `run()`:
- Detects root context without SUDO_USER
- Checks if rnsd is running as different user
- Shows warning dialog with recommended fixes

---

## Research: cp210x USB Error

User also reported:
```
cp210x ttyUSB0: failed set request 0x12 status: -110
```

**Finding:** Known upstream issue, not MeshForge-related.
- `-110` = ETIMEDOUT
- `0x12` = SET_BAUDRATE or SET_LINE_CTL
- Causes: USB power issues, timing problems, device hiccup

**Workaround:**
```bash
sudo modprobe -r cp210x && sudo modprobe cp210x
# Or unplug/replug device
```

---

## Test Suite Analysis

Ran pytest, found pre-existing failures (not from our changes):

| Category | Example Tests | Issue |
|----------|---------------|-------|
| State leakage | `test_device_persistence` | Singleton loads from real config, not temp |
| Mock setup | `test_emergency_mode` | Mock responses don't match impl |
| Timing | `test_metrics_export` | Server connection timeouts |

**Our changes verified:**
```bash
python3 -c "from src.launcher_tui.main import MeshForgeLauncher"  # OK
python3 -c "from src.launcher_tui.nomadnet_client_mixin import NomadNetClientMixin"  # OK
```

---

## Files Changed

1. `src/launcher_tui/nomadnet_client_mixin.py` - Added Case 2 user mismatch detection
2. `src/launcher_tui/main.py` - Added startup warning for root without SUDO_USER

---

## Next Steps

1. **Merge PR** - Branch ready at `claude/review-session-notes-2NhZ2`
2. **Test on hardware** - Fresh install verification needed
3. **Consider** - Changing install guidance from `sudo meshforge` to just `meshforge`
4. **Test infrastructure** - Pre-existing test failures need fixture improvements (separate task)

---

## Session Health

**Entropy Level:** LOW - Systematic approach, clear fixes
**Blocking Issues:** None
**PR Ready:** Yes (manual creation needed, `gh` CLI not available)
