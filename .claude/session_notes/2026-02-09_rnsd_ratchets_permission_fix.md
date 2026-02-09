# Session Notes: rnsd Ratchets PermissionError Fix

**Date:** 2026-02-09
**Branch:** `claude/fix-rnsd-threading-error-stuMD`
**Session ID:** stuMD
**Follows:** `2026-02-05_gateway_bridge_rns_fix.md` (identified this as a separate issue)

---

## Problem

rnsd crashes with `PermissionError` in a background thread:
```
File "/usr/local/lib/python3.13/threading.py", line 994, in run
File "/usr/local/lib/python3.13/dist-packages/RNS/Identity.py", line 316, in persist_job
    os.makedirs(ratchetdir)
PermissionError: [Errno 13] Permission denied: '/etc/reticulum/storage/ratchets'
```

Additionally:
- `/etc/reticulum/identity` never created (rnsd can't write to config dir)
- TUI "Show local identity" ran bare `rnid` which always fails with "No identity provided"
- `service_check.py` said rnsd is NOT a systemd service, but install script creates a systemd unit
- `ensure_system_dirs()` was defined but **never called anywhere**

---

## Root Cause

RNS added a **key ratcheting feature** that stores ratchet keys in `/etc/reticulum/storage/ratchets/`. The `Identity.persist_job()` method runs in a background thread and calls `os.makedirs(ratchetdir)`. This directory was never created by MeshForge's installer, and MeshForge never self-healed at runtime.

**Three cascading failures:**
1. Install script didn't create `storage/ratchets/` (only `storage/`)
2. `ReticulumPaths.ensure_system_dirs()` existed but was never called
3. No diagnostic check detected the missing directory

---

## Fixes (10 files, 139 insertions)

### 1. Install Script (`scripts/install_noc.sh`)
- Added `mkdir -p /etc/reticulum/storage/ratchets` with `chmod 755`

### 2. Path Definitions (`src/utils/paths.py`)
- Added `ETC_RATCHETS = ETC_STORAGE / 'ratchets'`
- Updated `ensure_system_dirs()` to create ratchets directory

### 3. Self-Healing at Runtime
- **`src/gateway/rns_bridge.py`**: `_init_rns_main_thread()` calls `ensure_system_dirs()` before `RNS.Reticulum()` init (when running as root)
- **`src/launcher_tui/startup_checks.py`**: `check_all()` calls `ensure_system_dirs()` at TUI launch (when running as root)

### 4. Diagnostics (`src/core/diagnostics/checks/rns.py`)
- New `check_rns_storage_permissions()` check — detects missing/unwritable storage and ratchets dirs
- Registered in `__init__.py` and `engine.py`

### 5. Service Config (`src/utils/service_check.py`)
- Changed rnsd to `is_systemd: True` (matches the systemd unit that `install_noc.sh` creates)
- Updated fix_hint to `sudo systemctl start rnsd`

### 6. TUI Identity Display (`src/launcher_tui/rns_menu_mixin.py`)
- Fixed bare `rnid` → `rnid -i <identity_path> -p` (print-identity flag)
- Shows both rnsd identity and MeshForge gateway identity with proper `rnid` invocation
- Helpful message when identity file doesn't exist yet

### 7. Knowledge Base (`src/utils/knowledge_content.py`)
- Added `rnsd_ratchets_permission` troubleshooting guide with 3 steps

---

## Files Changed

| File | Change |
|------|--------|
| `scripts/install_noc.sh` | Create `storage/ratchets/` during install |
| `src/utils/paths.py` | `ETC_RATCHETS` + update `ensure_system_dirs()` |
| `src/utils/service_check.py` | rnsd → `is_systemd: True` |
| `src/gateway/rns_bridge.py` | Self-heal dirs before RNS init |
| `src/launcher_tui/startup_checks.py` | Self-heal dirs at TUI launch |
| `src/launcher_tui/rns_menu_mixin.py` | Fix `rnid` invocation |
| `src/core/diagnostics/checks/rns.py` | New `check_rns_storage_permissions()` |
| `src/core/diagnostics/checks/__init__.py` | Register new check |
| `src/core/diagnostics/engine.py` | Wire into RNS diagnostics pipeline |
| `src/utils/knowledge_content.py` | Troubleshooting guide |

---

## Test Results

- **Related tests:** 123/123 PASS (paths, diagnostics, knowledge base, rns_services, rns_config)
- **Syntax:** All 10 files compile clean
- **Commits:** 3 (ratchets dir fix → rnid fix → self-healing)

---

## Key Insight: "Defined But Never Called"

`ReticulumPaths.ensure_system_dirs()` was a perfectly written method that sat unused since it was created. The installer created dirs at install time, but any post-install change to RNS (like adding ratchet support) broke rnsd with no recovery path.

**Lesson:** Defensive directory creation should happen at runtime, not just at install time. Services evolve and add new directory requirements.

---

## Remaining Items

1. **rnsd enable at boot** — User's status showed "not enabled at boot". After re-running installer and restarting rnsd, they should run `sudo systemctl enable rnsd`
2. **rnsd identity creation** — After dirs are fixed and rnsd restarts cleanly, `/etc/reticulum/identity` should be auto-created by rnsd
3. **Auto-restart rnsd** — Could add logic to restart rnsd after self-healing dirs (deferred, needs user approval pattern)

---

## Session Health

**Entropy Level:** LOW — Systematic fix of a well-identified issue
**Blocking Issues:** None
**Connection to Previous Session:** This was item #3 in the 2026-02-05 session's "Next Steps"
