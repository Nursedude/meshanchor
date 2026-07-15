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

## Issue #33: MeshCore Connection Contract (2026-05-05)

**Rule**: MeshCore radio is opened only via `utils.meshcore_connection`.

MeshCore has no daemon (unlike Meshtastic's meshtasticd) — the first process
to open `/dev/ttyMeshCore` (or the BLE/TCP equivalent) wins exclusive
ownership. A second `MeshCore.create_serial(...)` or raw `serial.Serial(...)`
races the gateway handler and silently breaks the running session.

### The contract

- **Long-running owner** (gateway bridge) wraps its connect bring-up in
  `acquire_for_connect(owner=...)` (acquires `MESHCORE_CONNECTION_LOCK`,
  honors any existing persistent owner) and calls
  `register_persistent(meshcore, loop, ...)` BEFORE the lock context exits.
- **Short-lived consumers** (TUI probes, CLI helpers) use
  `MeshCoreConnection()` as a context manager — returns `None` if the
  persistent owner is active, holds the lock for the duration otherwise.
- **Sync callers wanting to talk to the live radio** call
  `get_connection_manager().run_in_radio_loop(coro, timeout=...)` — schedules
  the coroutine on the persistent owner's asyncio loop and blocks for the
  result. No second connection ever opened.
- **Probes** (`validate_meshcore_device`) skip the raw open entirely while a
  persistent owner is registered — return synthetic OK with an
  `error="persistent owner '...' active"` note. This stops `Detect Devices`
  flows from racing the bridge.

### Prevention

- **Lint MF014**: `MeshCore.create_serial`/`create_tcp` and raw
  `serial.Serial(...)` on MeshCore-class devices outside
  `meshcore_connection.py` (or `meshcore_handler.py`, the persistent owner)
  fail the lint.
- **Regression guard**:
  `tests/test_regression_guards.py::TestMeshCoreConnectionContract` —
  ratchets direct opens at 0 and verifies the handler still calls
  `acquire_for_connect` + `register_persistent` + `unregister_persistent`.

### Why this matters

Sessions 2-4 of the MeshCore integration depend on multiple consumers being
able to share the link safely:

- **Session 2** (`meshcore-radio` supervisor service): Lifecycle separation
  between the radio and the bridge daemon. The supervisor is the persistent
  owner, the bridge becomes a `run_in_radio_loop` consumer.
- **Session 3** (config-ownership): Region/preset/firmware push needs
  short-lived exclusive access (`MeshCoreConnection`) without fighting the
  bridge.
- **Session 4** (TUI radio control): Status panel reads share via
  `get_meshcore()`; reset / preset switch / firmware update use
  `MeshCoreConnection` for exclusive operations.

### Session 2 extension: supervisor IPC contract (2026-05-05)

When the operator enables `meshcore-radio.service`, the supervisor process
becomes the persistent owner via `register_persistent(owner="meshcore-radio")`.
Cross-process consumers (bridge daemon, TUI, future CLI) reach the radio via
the Unix socket protocol in `src/supervisor/protocol.py`:

- **Socket**: `/run/meshcore-radio/meshcore-radio.sock` (separate runtime
  directory from `meshanchor-daemon`'s `/run/meshanchor` so the two units
  don't fight for ownership).
- **Wire format**: NDJSON. Hello frame on accept declares protocol version
  and current radio state. Methods: `status`, `get_radio_info`,
  `get_contacts`, `get_channels`, `send_message`, `ping`. Events broadcast
  to every connected client: `contact_message`, `channel_message`,
  `advertisement`, `ack`, `connection_state`.
- **Client SDK**: `utils.meshcore_supervisor_client.MeshCoreSupervisorClient`
  (sync facade with a background reader thread) and
  `is_supervisor_running(socket_path)` for liveness probes.
- **Schema lock**: `tests/test_meshcore_supervisor_protocol.py` ratchets the
  method and event-kind sets so additions go through code review.

The contract is one-way for now: the bridge handler does NOT yet consume the
supervisor — that's a follow-up PR. Until then, only one process should hold
the radio at a time. The lint MF014 + persistent-owner check in
`acquire_for_connect` prevent accidental dual ownership.


---


## Issue #3: Services Not Started/Verified — MOSTLY RESOLVED (archived 2026-05-24)

**Rule**: Always call `check_service()` before connecting to services.

- **Advisory** (daemons): Warn + continue — service may run outside systemd
- **Blocking** (TUI actions): Show error + fix hint, don't proceed

**Note**: Gateway checks are ADVISORY. Blocking checks caused "waiting for delivery"
regression when mosquitto wasn't detectable via systemctl.

**Remaining** (acceptable): `system_tools_mixin.py` and `service_menu_mixin.py` use
`systemctl status` for display only, not state decisions.

| Service | Port | systemd name |
|---------|------|--------------|
| meshtasticd | 4403 | meshtasticd |
| rnsd | None | rnsd |
| hamclock | 8080 | hamclock |
| mosquitto | 1883 | mosquitto |

## Issue #6: Large Files — ALL UNDER THRESHOLD (archived 2026-05-24)

Only `knowledge_content.py` (1,993 lines) exceeds 1,500 — acceptable as content file.
Monitor files approaching 1,400 lines. Split proactively at 1,000 lines when adding features.

Top files: `meshtastic_protobuf_client.py` (1,433), `service_check.py` (1,410),
`map_http_handler.py` (1,404), `prometheus_exporter.py` (1,399).

## Issue #21: Meshtastic CLI Preset Bug (Upstream) (archived 2026-05-24)

**Not a MeshAnchor bug.** The Python meshtastic CLI doesn't always apply modem preset
changes correctly. Always verify in browser at `http://localhost:9443` after CLI changes.
Consider direct meshtasticd API calls instead of CLI.

## Issue #23: Post-Install Verification (archived 2026-05-24)

**Rule**: Never mark install "complete" until verification passes.

`scripts/verify_post_install.sh` checks: meshtasticd binary, config.yaml validity,
Webserver section, port 9443, radio detection, config.d/, rnsd, udev rules.
Also available via `meshanchor --verify-install`.

## Issue #24: Python Environment Mismatch (rnsd + meshtastic module) (archived 2026-05-24)

rnsd's `Meshtastic_Interface.py` plugin requires the `meshtastic` Python module.
pipx isolation, different Python versions, or user vs system site-packages can
make the module invisible to rnsd.

**Fix**: `sudo pip3 install --break-system-packages --ignore-installed meshtastic`
or install to the same Python that rnsd uses:
`head -1 $(which rnsd)` then use that interpreter's pip.

**Diagnose**: `sudo python3 -c "import meshtastic; print(meshtastic.__version__)"`

## Issue #18: Auto-Reconnect on Connection Drop (archived 2026-05-24)

Gateway uses health monitoring + exponential backoff (1s → 2s → 4s → ... → 30s max)
in `rns_bridge.py`. All persistent connections should have health monitoring.
Release connection manager resources on disconnect.

## Issue #19: RNS Node Discovery from path_table (archived 2026-05-24)

Use `RNS.Transport.path_table` (not just `destinations`) for complete routing info.
**path_table may be empty immediately after connect** — use delayed checks (5s) and
periodic re-checks (30s).

Location: `src/gateway/node_tracker.py`

## Issue #27: rnsd is OPTIONAL (archived 2026-05-24)

MeshAnchor supports two independent transports:
- **MQTT** (mosquitto) — Meshtastic native. Used for preset bridging, monitoring.
- **RNS** (rnsd) — Reticulum. Used for LXMF messaging, cross-protocol bridging.

**Meshtastic preset bridging** (LF ↔ ST) needs only mosquitto — both radios MQTT
uplink/downlink to the same broker with same channel/PSK. No gateway code needed.

**Full NOC** (Meshtastic + RNS) uses both transports. They coexist independently.

---

## Issue #17: Meshtastic Connection Contention (Single-Client TCP)

> Demoted from persistent_issues.md 2026-06-15 (MF012 headroom); table row + Lint
> MF007 carry the live essence. Fully resolved + guarded.

**meshtasticd only supports ONE TCP client at a time.** Multiple components creating
independent connections causes thrashing every 1-2 seconds.

### Fix: Shared Connection Manager
All components share ONE persistent connection via `get_connection_manager()`.
Short-lived reads use `MeshtasticConnection` context manager.
Long-lived connections acquire `MESHTASTIC_CONNECTION_LOCK`.

### HTTP fromradio Contention Fix
The `/api/v1/fromradio` endpoint is also single-consumer. `send_text_direct()` POSTs
directly to `/api/v1/toradio` without ever reading fromradio. All TX paths use this.

### Prevention
- **NEVER** create `TCPInterface()` directly — use connection manager
- **NEVER** read `/api/v1/fromradio` in TX paths — use `send_text_direct()`
- Reserve session-based `connect()` + `start_polling()` for config reads only

---

## Issue #34: meshanchor-map handler thread leakage under slow-peer rollup (2026-05-16) — DEMOTED from persistent_issues.md 2026-07-15 (MF012 headroom; table row kept there)


**Observed**: meshanchor-server's `meshanchor-map.service` ran for ~4 h
(09:31 → 13:30 HST) and degraded into a state where:
- Main thread saturated at ~110 % CPU continuously.
- **526 sleeping handler threads** accumulated (against the design
  one-thread-per-request shape of ThreadingHTTPServer + `daemon_threads=True`).
- `/healthz` stayed fast (200 in ~2 ms; zero-work fast path) while
  `/api/status`, `/fleet/health`, and `/fleet/slo` all timed out (5-15 s,
  HTTP 000 / no response). RSS 760 MB.
- BrokenPipeError visible in the journal — clients disconnect mid-response,
  but the server's handler thread keeps running.

**Trigger**: same wall-clock window as the moc1 rnsd-RPC wedge recurrence
(MeshForge `project_rnsd_rpc_listener_wedge` #3, 2026-05-16). The
fleet-rollup endpoint polls every peer in `~/.config/meshanchor/fleet.json`
serially with `PEER_HTTP_TIMEOUT_S = 3.0` (`src/monitoring/fleet_rollup.py`).
With ~7 peers in fleet.json and one peer wedged, each `/fleet/rollup` call
holds a handler thread for 21 s worst-case. Dashboard polls at ~5 s; the
arrival rate exceeds the drain rate, threads pile up indefinitely.

**Why daemon_threads=True wasn't enough**: that flag only governs
shutdown — daemon threads die when the server stops. While running,
handlers stay alive until `do_GET` returns. A handler blocked in
`urllib.request.urlopen()` against a wedged peer pins the thread for
the full timeout window. Multiple endpoints (`/fleet/health`,
`/fleet/slo`, `/fleet/rollup`) all share this pattern.

**Symptom shape on the rollup itself**: per the operator's `/fleet`
dashboard, peer rows show `peer fetch: timeout: timed out` — which is
the *handler's* outbound urllib timeout against the wedged peer, NOT a
connection-refused or DNS failure. Distinguishing this shape from the
moc1 case (where moc1 itself was the wedged peer) matters: the moc1
case is "this host is the cause," the MA-server case is "this host is
downstream of the cause but its handlers are stuck upstream of
recovery."

**Immediate recovery** (verified 2026-05-16 13:30 HST):
```bash
ssh meshanchor-server 'sudo systemctl restart meshanchor-map.service'
```
Post-restart: 11 threads, `/fleet/slo` returns in 870 ms.

**Open follow-ups** (tracked as GitHub issues 2026-05-16):
1. [#126](https://github.com/Nursedude/meshanchor/issues/126) — concurrent
   peer fetches in `collect_fleet_rollup`. Worst-case latency goes from
   `N × timeout` to `timeout`.
2. [#127](https://github.com/Nursedude/meshanchor/issues/127) — tighten
   `PEER_HTTP_TIMEOUT_S` from 3.0 s to 1.5 s. Healthy peers respond in
   sub-200 ms; a wedged peer never recovers within the 1.5-3 s window.
3. ✅ [#128](https://github.com/Nursedude/meshanchor/issues/128)
   **SHIPPED 2026-05-16 (commits `8fa73309` + `3787d977`)** —
   in-flight semaphore on `/fleet/{rollup,slo,health,activity}`.
   Default cap 8 (raised from initial 4 after the first deploy
   produced visible 429 errors on natural dashboard load — the fast
   tick polls 2 gated endpoints every 5 s, the slow tick polls 1
   gated endpoint every 15 s, so 3 gated handlers can be in flight
   simultaneously every 15 s when ticks align; cap=4 left no
   headroom). Excess requests return 429 + `Retry-After: 2`.
   Dashboard `fetchJson` treats 429 as a `TRANSIENT_BUSY` sentinel
   and skips render-and-error-card update (`web/fleet.html`
   commit `3787d977`) so transient cap-hits no longer surface as
   user-facing errors. Synthetic 10-parallel `/fleet/rollup` burst:
   8 × 200 + 2 × 429. 4-minute 4-parallel-per-second soak: thread
   count stable at 12-13 (previously climbed past 200 in minutes);
   `/fleet/slo` 200 in 300 ms under load.
   `meshanchor_map_fleet_heavy_busy_total` exposes the 429 counter
   for over-polling alerts. **This is the gating fix.** Daily restart
   timer (commit `c9cb1a5a`) can be retired once multi-day soak
   confirms steady-state stability.
4. [#129](https://github.com/Nursedude/meshanchor/issues/129) — port
   MeshForge's `cascade_detector` to MA. Surfaces the pre-failure shape
   one cadence after the threshold so MA-server alarms *before* its
   rollup-handler threads start piling up. Audit `MapServer.start()` +
   `start_background()` BOTH — MF shipped 79f5d7b with the call only
   in `start_background()` (commit 368e591 caught it).
5. [#130](https://github.com/Nursedude/meshanchor/issues/130) — fix
   `non_self_peers()` to filter self by host+port, not just by name.
   Discovered 2026-05-16 (post-restart recurrence of #34): MA-server's
   fleet.json carried a `"name": "meshanchor-server-self"` self entry
   that the filter missed, so every `/fleet/rollup` HTTP-polled its
   own listener — doubling the thread arrival rate and recreating the
   pile-up in ~8 min after each restart. Local mitigation: dropped the
   self entry from `~/.config/meshanchor/fleet.json` on MA-server
   (post-mitigation: /fleet/rollup 4s steady, 13 threads, blackout
   banner cleared).
6. [#131](https://github.com/Nursedude/meshanchor/issues/131) —
   pile-up recurs even with healthy peers. Second post-restart
   recurrence 2026-05-16 ~14:25 HST: 228 threads in 36 min with all 5
   fleet peers responding in 91-260 ms (no wedged peer, no
   self-loopback). Sustained dashboard polling alone is enough on
   Pi-class hardware to saturate the GIL and starve handler threads.
   Strengthens [#128](https://github.com/Nursedude/meshanchor/issues/128)
   (in-flight semaphore) as the *gating* fix — #126 and #127 reduce
   per-rollup latency but neither caps concurrent in-flight handlers.
   Restart cadence today: 13:30 -> 13:50 (re-piled, fixed self-loop) ->
   14:25 (re-piled w/ healthy peers). **Recurrence is expected until
   #128 lands**; rely on `meshforge-map-restart.timer`-equivalent
   weekly + ad-hoc operator restart in the meantime.

**Cross-refs**: MF `project_rnsd_rpc_listener_wedge.md` (the upstream
cause class — recurrent on moc1).

---

