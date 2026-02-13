# Session: RNS Config Auto-Fix Regression Fixes
**Date:** 2026-02-13
**Branch:** `claude/fix-rnsd-python-error-8nyHL`

## Issues Fixed This Session

### 1. Meshtastic CLOSE-WAIT Zombie Self-Healing (Too Slow)
**File:** `src/gateway/meshtastic_handler.py`
- Self-healing zombie detection was waiting for all 10 reconnect attempts (~3 min)
- Changed to trigger after 3 failures (~7 seconds)
- `safe_close_interface()` already had force-close from prior commit

### 2. Auto-Fix Overwrites RNS Config with Template (ROOT CAUSE of config loss)
**File:** `src/launcher_tui/rns_menu_mixin.py` — `_auto_fix_rns_shared_instance()`
- When ANY RNS tool returned error containing "shared instance", "could not connect",
  "could not get", "authenticationerror", or "digest", the auto-fix would:
  1. Copy `templates/reticulum.conf` to `/etc/reticulum/config` (overwriting existing!)
  2. Restart rnsd
  3. All custom interfaces GONE (template has only AutoInterface enabled)
- **Fix:** Only deploy template if NO config exists anywhere. Never overwrite existing.

### 3. Bare RNS.Reticulum() in map_data_collector.py (Path.home() MF001)
**File:** `src/utils/map_data_collector.py:1180`
- Called `RNS.Reticulum()` without `configdir` — under sudo creates default config
  at `/root/.reticulum/` and may conflict with rnsd's interface bindings
- **Fix:** Use client-only temp config (like rns_bridge.py and node_tracker.py do)

### 4. NomadNet Exit Code 1 (Storage Permissions)
**Files:** `src/launcher_tui/rns_menu_mixin.py`, `src/launcher_tui/nomadnet_client_mixin.py`
- Auto-fix set `/etc/reticulum/storage/` to 0o755 (root-only writable)
- `ensure_system_dirs()` in paths.py correctly uses 0o777
- NomadNet launches as real user (via `sudo -u`) → can't write to storage → crash
- `_get_rns_config_for_user()` tested writability as root (always passes) instead
  of checking if the REAL USER can write
- **Fix:** Storage dirs set to 0o777; writability checks use mode bits for real user

### 5. Auto-Fix Restart Loop on Low Entropy (Pi)
**File:** `src/launcher_tui/rns_menu_mixin.py`
- Auto-fix fired on every RNS tool error → restarted rnsd → rnsd hangs on
  crypto init (low entropy on Pi) → tool still fails → auto-fix fires again
- **Fix:** Auto-fix only runs if rnsd is NOT active. If rnsd IS running but
  tools fail, show targeted diagnostics:
  - Auth token mismatch → clear `shared_instance_*` files
  - Low entropy → install `rng-tools`
  - Not listening → wait for init
  - Config drift → show diagnostic commands

## Still Open / Next Session

### Entropy on Pi
- User reported "watch for entropy" — Pi may need `rng-tools` or `haveged`
- The diagnostic code now detects low entropy and suggests the fix
- **ACTION:** User should run: `sudo apt install rng-tools && sudo systemctl enable --now rngd`

### NomadNet Exit Code 1 — May Need Further Investigation
- After pulling these fixes AND fixing permissions (`sudo chmod 777 /etc/reticulum/storage`),
  NomadNet should work. If it still fails, check:
  - `cat /home/<user>/.nomadnetwork/logfile` (last 20 lines)
  - `sudo journalctl -u rnsd -n 30` (rnsd logs)
  - Auth token stale? Clear: `sudo rm -f /etc/reticulum/storage/shared_instance_*`

### RNS Config Location Consistency
- MeshForge writes config via `ReticulumPaths.get_config_file()` (uses `get_real_user_home()`)
- rnsd (system service) reads from its own resolution order
- If `/etc/reticulum/config` exists, both converge. Otherwise, paths may diverge.
- The config drift detector exists (`_check_config_drift` in rns_menu_mixin.py)
  but is opt-in. Consider running it automatically on startup.

## Commits (4 total)
1. `fix: trigger zombie detection after 3 failures instead of 10`
2. `fix: stop auto-fix from overwriting RNS config with template`
3. `fix: NomadNet crash from /etc/reticulum storage permissions`
4. `fix: stop auto-fix from blindly restarting rnsd on every RNS error`
