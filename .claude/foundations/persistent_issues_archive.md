# MeshAnchor Persistent Issues — Archive

> **Purpose**: Historical record of resolved issues.
> These were moved from `persistent_issues.md` to reduce file size.
> Last updated: 2026-03-13
>
> **Note**: GTK-specific issues (#2, #11, #13, #14, #15) were removed during
> the 2026-02-21 cleanup. GTK4 was removed in v0.5.x; TUI is the only interface.

---

## Issue #25: rnsd PermissionError on /etc/reticulum/storage/ratchets

### Symptom
rnsd crashes in a background thread with:
```
PermissionError: [Errno 13] Permission denied: '/etc/reticulum/storage/ratchets'
```
Additionally, `/etc/reticulum/identity` is never created, and the TUI "Show local identity" shows "No identity provided, cannot continue."

### Root Cause
RNS added **key ratcheting** support which requires a `ratchets/` subdirectory under storage. `Identity.persist_job()` runs in a background thread and calls `os.makedirs(ratchetdir)`. The install script didn't create this directory, and `ReticulumPaths.ensure_system_dirs()` was defined but never called at runtime.

### Fix (v0.5.x, 2026-02-09)
**Self-healing at runtime** — MeshAnchor now creates the directories automatically:
1. `startup_checks.check_all()` calls `ensure_system_dirs()` at TUI launch
2. `rns_bridge._init_rns_main_thread()` calls it before RNS init
3. `install_noc.sh` creates `storage/ratchets/` during install
4. `check_rns_storage_permissions()` diagnostic detects the issue
5. After fixing dirs, MeshAnchor auto-restarts rnsd via `apply_config_and_restart()`

### Files
- `src/utils/paths.py` — `ETC_RATCHETS`, `ensure_system_dirs()`
- `src/gateway/rns_bridge.py` — Self-heal in `_init_rns_main_thread()`
- `src/launcher_tui/startup_checks.py` — Self-heal in `check_all()`
- `src/core/diagnostics/checks/rns.py` — `check_rns_storage_permissions()`
- `scripts/install_noc.sh` — Pre-create dirs
- `src/launcher_tui/rns_menu_mixin.py` — Fixed `rnid` invocation

### Status: RESOLVED


---

## Issue #26: ReticulumPaths Fallback Copies Cause Config Divergence

### Symptom
`.reticulum` interface configuration is "lost" between sessions. RNS config changes made in the TUI have no effect. rnsd uses a different config file than what MeshAnchor reads/writes.

### Root Cause
**Four separate copies** of `ReticulumPaths` existed in the codebase:
1. `src/utils/paths.py` — **Canonical** (correct: checks `/etc/reticulum`, XDG, `~/.reticulum`)
2. `src/launcher_tui/main.py` — Fallback (missing `get_interfaces_dir`, `ensure_system_dirs`)
3. `src/launcher_tui/rns_menu_mixin.py` — Fallback (missing `ensure_system_dirs`)
4. `src/core/diagnostics/checks/rns.py` — Fallback (**WRONG: skipped `/etc/reticulum` and XDG entirely**)
5. `src/gateway/rns_bridge.py` — Fallback (missing `get_interfaces_dir`, `ensure_system_dirs`)

### Fix (v0.5.x, 2026-02-09)
**Eliminated all fallback copies.** Every file now imports directly:
```python
# NO try/except, NO fallback class
from utils.paths import ReticulumPaths
```

### Prevention
- **NEVER** duplicate `ReticulumPaths`. Always import from `utils/paths.py`.
- `utils/paths.py` is the SINGLE SOURCE OF TRUTH for all path resolution.

### Status: RESOLVED


---

## Issue #28: API Proxy Steals fromradio Packets from Native Web Client

**Date Identified**: 2026-02-10
**Severity**: Critical (breaks meshtasticd web client at :9443)

### Symptom
When MeshAnchor is running, the Meshtastic web client at `ip:9443` shows
no data. The gateway bridge works fine (RX green), NomadNet talks to other
RNS nodes normally. Only the native web client is broken.

### Root Cause
`MeshtasticApiProxy` was **enabled by default**. It continuously polls
`GET /api/v1/fromradio` from meshtasticd's HTTP API on port 9443.
This endpoint is **queue-based** — each GET pops the next protobuf packet.
MeshAnchor drained the queue before the native web client could read it.

### Fix Applied
1. **Default `enable_api_proxy` to `False`** in `MapServer.__init__`
2. **Added `--enable-api-proxy` CLI flag** for explicit opt-in
3. **`/mesh/` redirects to native `:9443`** when proxy is disabled

### Prevention
Never enable the API proxy by default. The gateway (TCP:4403) and
web client (HTTP:9443) are separate channels and should coexist.

### Status: RESOLVED


---

## Health Check Reconciliation (2026-02-20) — Moved from persistent_issues.md

The code review health check (2026-01-24) identified 5 critical (C1-C5) and 1 high (H1)
issues. All resolved:

| ID | Issue | Status | Evidence |
|----|-------|--------|----------|
| C1 | LXMF Source None after partial RNS init | **MITIGATED** | Guard at `rns_bridge.py:579-580` |
| C2 | reconnect.py raises None on early interruption | **FIXED** | `reconnect.py:176-178` |
| C3 | Unbounded node tracking dicts (memory leak) | **FIXED** | MAX_NODES caps + eviction |
| C4 | Stats dict race conditions (24 racy increments) | **FIXED** | threading.Lock added |
| C5 | Atomic write uses deterministic temp path | **FIXED** | `tempfile.mkstemp()` |
| H1 | Non-interruptible shutdown in daemon loops | **FIXED** | `_stop_event.wait()` everywhere |


---

## Issue #1: Path.home() Returns /root with sudo — RESOLVED (2026-02-20)

Zero `Path.home()` violations remain. Use `get_real_user_home()` from `utils/paths.py`.
Fixed last 3 violations in `mqtt_bridge_handler.py`, `cli.py`, `rns_config.py`.
Linter (`scripts/lint.py`) checks MF001. Regression test in `test_regression_guards.py`.

---

## Issue #5: Duplicate Utility Functions — RESOLVED (2026-02-20)

All 20 `safe_import` fallback copies consolidated to direct imports (-220 lines).
Rule: `safe_import` is for EXTERNAL deps only. First-party modules always use direct imports.
Follow-up: `startup_checks.py` converted from `safe_import('utils.service_check')` to direct import.

---

## Issue #6: Large Files — Extraction History (2026-03-02)

8 files split in Session 2 (2026-03-02):
- meshtasticd_config.py: 1,497 → 516 (meshtasticd_templates.py)
- rns.py: 1,505 → 1,306 (rns_templates.py)
- prometheus_exporter.py: 1,523 → 1,399 (metrics_server.py)
- map_http_handler.py: 1,557 → 1,404 (_map_meshtastic_proxy.py)
- map_data_collector.py: 1,568 → 1,320 (_map_collector_rns.py)
- service_check.py: 1,573 → 1,410 (_service_iptables.py)
- rns_bridge.py: 1,599 → 1,349 (_rns_bridge_connection.py)
- nomadnet.py: 1,610 → 1,315 (_nomadnet_rns_checks.py)

Previous extractions (2026-02-06):
- traffic_inspector.py: 2,194 → 442, main.py: 1,799 → 1,489
- node_tracker.py: 1,808 → 989, metrics_export.py: 1,762 → 96
- engine.py: 1,767 → 709, rns_menu_mixin.py: 1,524 → 1,210

---

## Issue #7: Missing File References — RESOLVED

Create scripts before referencing them in menu options. Use commands layer when possible.

---

## Issue #8: Outdated Fallback Versions — RESOLVED

Search for hardcoded version strings when bumping: `grep -rn "0\.[0-9]\.[0-9]" src/*.py`

---

## Issue #9: Broad Exception Swallowing — MOSTLY RESOLVED (2026-02-20)

28/30 fixed across 7 files (tcp_monitor, system_diagnostics, setup_wizard, hardware_config,
rns_sniffer, site_planner). 2 benign by design (packet_dissectors, pskreporter_subscriber).

---

## Issue #10: Map Control Panel Scrollbar Overlap — FIXED (2026-02-25)

Added thin dark-themed scrollbar CSS to `web/node_map.html`.

---

## Handler Registry Migration — COMPLETE (2026-02-28)

49-mixin inheritance chain replaced with handler registry pattern.
See `handler_protocol.py` (Protocol + BaseHandler + TUIContext) and
`handler_registry.py` (register/lookup/dispatch). 60 handler files in
`launcher_tui/handlers/`. `main.py` dropped from 1,947 to 1,148 lines.
