# Session Notes: 2026-01-31 NomadNet Install Fix

## Branch: `claude/fix-fresh-install-issues-YNtgD`

## Status: READY FOR FRESH INSTALL TEST

User is re-imaging Pi to test on clean environment.

## Issue

NomadNet was not working on fresh installs. Users saw:
```
module 'nomadnet.ui' has no attribute 'COLORMODE_16'
```

## Root Cause Analysis

1. **pipx not installed** - `install_noc.sh` didn't install pipx, so NomadNet couldn't be installed via menu

2. **COLORMODE_16 bug** - NomadNet 0.9.8 line 838 references `nomadnet.ui.COLORMODE_16` (wrong path). This bug only triggers when `colormode` is MISSING from config.

3. **MeshForge was creating minimal configs** - The `_setup_nomadnet_shared_instance()` function created a stripped-down config template missing `colormode = 256`. This exposed the upstream bug.

## The Real Fix

**Don't touch NomadNet configs. Let NomadNet use its own defaults.**

NomadNet creates a complete default config on first run that includes `colormode = 256`. Our "smart" minimal template was breaking it.

## Commits on Branch

| Commit | Description |
|--------|-------------|
| `35c93f1` | fix: Install pipx only, NomadNet stays menu-driven |
| `68bef2f` | fix: Add colormode to NomadNet config template (superseded) |
| `4b14706` | **THE FIX**: Stop creating/modifying NomadNet configs - use defaults |
| `783474f` | docs: Add debugging lesson article |

## Key Changes

### scripts/install_noc.sh
- Added pipx installation during RNS setup
- NomadNet remains menu-driven (user installs via TUI)

### src/launcher_tui/nomadnet_client_mixin.py
- Removed 130 lines of config creation/modification code
- `_setup_nomadnet_shared_instance()` now just prints messages
- `_validate_nomadnet_config()` just checks if config exists, doesn't modify

## Lesson Learned

> "Stop wiping out the original config file. We spent hours debugging something that if the original config file was there would not have happened."

**Trust upstream defaults. Don't create minimal templates.**

## Substack Article

Written to `.claude/articles/2026-01-31_config_lesson.md`

## Next Steps for Fresh Install Test

1. Re-image Pi with clean Raspberry Pi OS
2. Run `install_noc.sh`
3. Launch TUI, go to RNS > NomadNet > Install
4. Verify NomadNet starts without COLORMODE_16 error
5. Confirm NomadNet creates its own complete config

## Branch Cleanup Done

- Deleted old local branch `claude/fix-meshtastic-interface-zbX5V`
- Current branch is clean and pushed
