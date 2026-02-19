# Session: Meshtastic_Interface.py Code Review & Debian pip Fixes

**Date:** 2026-02-19
**Branch:** `claude/fix-directory-permissions-7w8Xq`
**Commits:** `4c71797` through `704000c` (3 commits this session)

---

## Problem Statement

User reported the RNS repair wizard failing at step [3/5]:

```
[3/5] Checking rnsd Python dependencies...
  Installing meshtastic...
    meshtastic: FAILED
      hint: The package was installed by debian.
```

Cascade: meshtastic install fails → rnsd can't load `Meshtastic_Interface.py` → port 37428 never binds → NomadNet can't connect to shared instance → NomadNet tries loading interface directly → also fails → exit 255.

---

## Root Causes Found

### 1. Debian-managed pip conflict (pip install failure)

`_ensure_rnsd_dependencies()` used `--break-system-packages` but NOT `--ignore-installed`. On Debian/RPi OS where `python3-meshtastic` was installed via apt, pip refuses to overwrite. The pattern was already correct in `setup_wizard.py:558` but hadn't been propagated to the 3 TUI install paths.

### 2. Meshtastic_Interface.py plugin bugs (7 found)

The upstream plugin from `github.com/landandair/RNS_Over_Meshtastic` had bugs causing reliability issues. User confirmed this is their code to maintain.

---

## Changes Made

### Commit `4c71797` — Re-enable rnsd after service file regeneration

- `rns_menu_mixin.py`: Added `enable_service('rnsd')` call after `_validate_rnsd_service_file()` regenerates rnsd.service, preventing silent "disabled" state

### Commit `6c013cb` — Handle Debian-managed meshtastic package conflicts

Already implemented (prior session work on this branch):
- **Fix E**: `rns_diagnostics_mixin.py` — retry pip with `--ignore-installed` when stderr contains "installed by" or "externally-managed"
- **Fix F**: `rns_menu_mixin.py` crash recovery path — same retry pattern
- **Fix G**: `rns_menu_mixin.py` plugin installer path — same retry pattern
- **Fix H**: `nomadnet_client_mixin.py` — detect meshtastic module failure in NomadNet diagnosis, fallback to checking rnsd journal

### Commit `704000c` — Vendor Meshtastic_Interface.py with bug fixes

**Vendored to:** `templates/interfaces/Meshtastic_Interface.py`

| Bug | Severity | Fix |
|-----|----------|-----|
| `is PacketHandler` in write_loop | **Critical** — destination routing dead code, all packets broadcast | `isinstance(stored, PacketHandler)` |
| `dest_to_node_dict` LIFO eviction | **High** — stale entries survive, new entries evicted | LRU cache with `_dest_order` list |
| `split_data` off-by-one | **Medium** — empty trailing packet on exact-multiple payloads | Ceiling division |
| `connection_closed` unbounded reconnect | **High** — single failure kills pubsub thread | Exponential backoff, 5 retries |
| `get_next` on empty dict | **Medium** — `ValueError` crash | Guard clause |
| `assembly_dict` unbounded growth | **Medium** — memory leak per remote node | `MAX_ASSEMBLY_PER_NODE = 8` |
| `outgoing_packet_storage` never cleaned | **Medium** — memory leak for all sent packets | Post-send cleanup |

**Installer updated:** `_install_meshtastic_interface_plugin()` now prefers vendored copy from `templates/interfaces/`, falls back to git clone only if vendored file missing. No internet required for fresh installs.

---

## Files Modified

| File | Changes |
|------|---------|
| `src/launcher_tui/rns_menu_mixin.py` | `enable_service` import, plugin installer rewrite |
| `src/launcher_tui/rns_diagnostics_mixin.py` | pip retry with `--ignore-installed` (prior commit) |
| `src/launcher_tui/nomadnet_client_mixin.py` | meshtastic detection in NomadNet diagnosis (prior commit) |
| `templates/interfaces/Meshtastic_Interface.py` | **NEW** — vendored plugin with 7 bug fixes |

---

## Verification

- Linter: clean
- Tests: 1530 passed, 14 skipped
- All pushed to `claude/fix-directory-permissions-7w8Xq`

---

## Deployment on fleet-host-2

```bash
# Update the plugin on the live system:
sudo cp templates/interfaces/Meshtastic_Interface.py /etc/reticulum/interfaces/
sudo systemctl restart rnsd

# Or run Repair RNS wizard — it will now:
# 1. Detect meshtastic is missing
# 2. Try pip install with --break-system-packages
# 3. Retry with --ignore-installed if Debian conflict
# 4. Restart rnsd
```

---

## Remaining Work / Next Session

- [ ] **PR creation** for this branch (10+ commits across multiple sessions)
- [ ] **On-device test** on fleet-host-2 to verify the full cascade is resolved
- [ ] **Monitor** `Meshtastic_Interface.py` for connection stability after the reconnect fix
- [ ] Consider upstreaming bug fixes to `landandair/RNS_Over_Meshtastic` (optional — we now maintain our own copy)
- [ ] `persistent_issues.md` should be updated with Issue #24 resolution status
