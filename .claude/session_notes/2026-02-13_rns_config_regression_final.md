# Session: RNS Config Regression — Final Hardening
**Date:** 2026-02-13
**Branch:** `claude/rns-config-regression-bVB9N`
**Prior sessions:** `2026-02-13_rns_config_autofix_regression.md`, `2026-02-13_rns_config_regression_cont.md`

## Context

Third session in the RNS config regression series. Prior sessions identified
5 design gaps and fixed the core issues. This session addresses the remaining
TODO items: systemd service correctness, NomadNet pre-flight checks, diagnostics
visibility, and user-consented auto-remediation.

## Changes Made (4 fixes)

### 1. StartLimitIntervalSec moved to [Unit] section (systemd correctness)
**Files:** `templates/systemd/rnsd-user.service`, `scripts/meshforge-map.service`,
`scripts/update.sh`, `scripts/install_noc.sh`

- `StartLimitIntervalSec` and `StartLimitBurst` belong in `[Unit]`, not `[Service]`
- systemd warns about this placement — moved in all 4 files that define service units
- No functional change on most systemd versions (it tolerates the wrong section)
  but future versions may enforce this

### 2. NomadNet pre-flight: verify rnsd is actually listening
**File:** `src/launcher_tui/nomadnet_client_mixin.py`

- Added port 37428 TCP check in `_check_rns_for_nomadnet()` after confirming rnsd process exists
- If rnsd is running but port not bound:
  - Calls `_find_blocking_interfaces()` to diagnose why
  - Shows blocking interface details with fix hints
  - Asks user whether to continue anyway
- Prevents the silent failure where NomadNet launches → can't connect → crashes

### 3. Interface Dependencies added to RNS Diagnostics
**File:** `src/launcher_tui/rns_menu_mixin.py`

- Added `[5/5] Checking interface dependencies...` section to `_rns_diagnostics()`
- Shows blocking interfaces with fix hints
- Checks shared instance port 37428 listening status
- Issues and warnings accumulate across all 5 diagnostic checks
- Updated section numbering from [X/4] to [X/5]

### 4. Auto-disable blocking interfaces with user consent
**File:** `src/launcher_tui/rns_menu_mixin.py`

- New method: `_disable_interfaces_in_config(interface_names)`
  - Parses RNS config, changes `enabled = yes` to `enabled = no` for named interfaces
  - Uses regex with `re.escape()` for safe interface name matching
  - Only writes if changes were actually made
- Integrated into `_auto_fix_rns_shared_instance()`:
  - When blocking interfaces detected, shows dialog asking user permission
  - Lists which interfaces will be disabled
  - Notes user can re-enable from RNS menu later
  - If user declines, proceeds anyway (rnsd may hang)

## Tests
- 4021 passed, 19 skipped — all clean

## Design Decisions

- **Port check uses raw socket, not ss/netstat**: Avoids subprocess overhead and
  parsing issues. 1-second timeout prevents blocking.
- **_disable_interfaces_in_config is a separate method**: Reusable for future
  use cases (e.g., diagnostics menu offering to disable individual interfaces).
- **User consent required**: Never auto-modify the RNS config without asking.
  The dialog makes clear what will change and how to undo it.

## Session Status
- All changes committed, tested, and pushed
- Session notes complete
