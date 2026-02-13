# Session: RNS Config Regression — Root Cause Analysis & Fix
**Date:** 2026-02-13
**Branch:** `claude/fix-rns-config-regression-P7ikY`
**Prior session:** `2026-02-13_rns_config_autofix_regression.md`

## Context

Continuation of RNS config auto-fix regression work. Prior session made 4 fixes
but the underlying systemic issue persisted. This session traced the full
progression from clean install to broken state and identified 4 design gaps
that combine to create the recurring config/permission/auth failure.

## Root Cause Analysis

The recurring failure is caused by **4 gaps** that combine:

### Gap 1: Auth token clearing is incomplete
`_auto_fix_rns_shared_instance()` cleared tokens from `/etc/reticulum/storage`
and `/root/.reticulum/storage` but NOT from `~/.reticulum/storage` (real user).
After rnsd restart with new tokens, NomadNet (if it falls back to ~/.reticulum)
has stale tokens → AuthenticationError.

### Gap 2: Config path is not explicit
NomadNet relied on RNS's default resolution (returning None from
`_get_rns_config_for_user()`). Different user contexts may resolve to different
paths. When `~/.reticulum/config` exists, NomadNet (running as real user) may
use it instead of `/etc/reticulum/config` → config drift → auth mismatch.

### Gap 3: `chmod -R 755` destroys world-writable
The "fix permissions" option in `_check_rns_for_nomadnet()` ran
`chmod -R 755 /etc/reticulum/`, removing S_IWOTH from storage. Next NomadNet
launch detects storage isn't writable → falls back to ~/.reticulum → drift.

### Gap 4: Storage file permissions not fixed before NomadNet launch
`_fix_storage_file_permissions()` only ran during gateway bridge startup and
auto-fix, NOT before NomadNet launch. Files created by rnsd (root, 0o644)
are read-only to the real user.

## Issues Fixed This Session

### 1. Entropy Diagnostic Wrong Service Name
**File:** `src/launcher_tui/rns_menu_mixin.py`
- Suggested `rng-tools`/`rngd` instead of `rng-tools-debian` for Debian/Pi OS
- **Fix:** Probes systemd for actual service name before suggesting commands

### 2. Auth Token Clearing Now Covers All Locations
**File:** `src/launcher_tui/rns_menu_mixin.py` — `_auto_fix_rns_shared_instance()`
- Now clears `shared_instance_*` from ALL 4 locations:
  - `/etc/reticulum/storage`
  - `/root/.reticulum/storage`
  - `~/.reticulum/storage` (real user)
  - `~/.config/reticulum/storage` (real user XDG)

### 3. NomadNet Always Uses Explicit Config Path
**File:** `src/launcher_tui/nomadnet_client_mixin.py` — `_get_rns_config_for_user()`
- Never returns None — always returns explicit path
- When `/etc/reticulum/config` exists → always uses it (auto-fixes permissions)
- No fallback to `~/.reticulum` which caused config drift
- NomadNet launched with `--rnsconfig /etc/reticulum` (same as rnsd)

### 4. Removed Destructive chmod -R 755
**File:** `src/launcher_tui/nomadnet_client_mixin.py` — `_check_rns_for_nomadnet()`
- Old code: `chmod -R 755 /etc/reticulum/` removed world-writable from storage
- New code: `storage_dir.chmod(0o777)` + `_fix_storage_file_permissions()`
- Never offers to fall back to `~/.reticulum` — always fixes system config
- No more "continue with user config" option that created config drift

## Design Principle Established

**NomadNet and rnsd MUST always use the SAME config directory.**
- System config at `/etc/reticulum/config` is the convergence point
- When it exists, ALWAYS pass `--rnsconfig /etc/reticulum` to NomadNet
- Fix permissions rather than falling back to a different config dir
- Clear auth tokens from ALL known locations when restarting rnsd

## Commits
1. `fix: detect correct entropy service name for Debian/Pi OS`
2. `fix: eliminate RNS config drift between rnsd and NomadNet`

## Tests
- 4021 passed, 19 skipped (271s) — all clean

## Session Status
- Clean — focused and systematic
- Deep root cause analysis completed
- 4 design gaps identified and fixed
