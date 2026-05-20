# MeshAnchor Persistent Issues & Resolution Patterns

> **Purpose**: Document recurring issues and their proper fixes to prevent regression.
> **Last audited**: 2026-03-13 — Trimmed to <40k chars; resolved issues archived.

---

## Archived / Fully Resolved Issues

The following are **RESOLVED** with automated prevention (linter + regression tests).
Full history in `persistent_issues_archive.md`.

| Issue | Summary | Prevention |
|-------|---------|------------|
| Health Check Reconciliation | C1-C5, H1 all fixed (2026-02-20) | — |
| Handler Registry Migration | 49 mixins → 60 handler files (2026-02-28) | — |
| #1 Path.home() | Use `get_real_user_home()` | Lint MF001 + regression test |
| #5 Duplicate Utilities | `safe_import` for external deps only | Direct imports for first-party |
| #7 Missing File References | Create scripts before referencing them | — |
| #8 Outdated Fallback Versions | Search hardcoded versions on bump | `grep -rn "0\.[0-9]\.[0-9]" src/` |
| #9 Broad Exception Swallowing | 28/30 fixed; 2 benign by design | `grep except.*:.*pass` |
| #10 Map Scrollbar Overlap | Thin dark-themed scrollbar CSS | — |
| #25, #26, #28 | rnsd ratchets, ReticulumPaths copies, API proxy | — |
| GTK Issues (#2, #11, #13–#15) | GTK4 removed in v0.5.x | — |

---

## Development Checklist

Before committing, verify:

- [ ] No `Path.home()` — use `get_real_user_home()`
- [ ] Actionable error messages, appropriate log levels
- [ ] Services verified with `check_service()` before use
- [ ] `subprocess` calls have `timeout=` (MF004)
- [ ] Utilities from central location, not duplicated
- [ ] `safe_import` for external deps only; direct imports for first-party

---

## Quick Reference: Import Patterns

```python
# Paths
from utils.paths import get_real_user_home, get_real_username, MeshAnchorPaths, ReticulumPaths

# Settings / Logging
from utils.common import SettingsManager, CONFIG_DIR
from utils.logging_config import get_logger

# Service checks
from utils.service_check import check_service, check_port, ServiceState

# Boundary observability — wrap every cross-process call (rnsd RPC,
# meshtasticd TCP, MeshCore TCP, MQTT, systemctl). See
# .claude/plans/boundary_observability_charter.md.
from utils.boundary_timing import timed_boundary, call_boundary, get_boundary_stats
with timed_boundary("rnsd.has_path", target=hash_short):
    has = RNS.Transport.has_path(dest_hash)
# or:
result = call_boundary("rnsd.handle_outbound",
                       router.handle_outbound, lxm,
                       target=hash_short)

# External deps (safe_import)
from utils.safe_import import safe_import
RNS, _HAS_RNS = safe_import('RNS')
_pub, _HAS_PUBSUB = safe_import('pubsub', 'pub')

# First-party — ALWAYS direct import
from utils.service_check import check_service
from utils.event_bus import emit_message
from gateway.rns_bridge import RNSMeshtasticBridge
```

**Test patching**: Patch `_HAS_*` flags directly, not `sys.modules`:
```python
@patch('gateway.rns_bridge._HAS_RNS', True)  # CORRECT
def test_rns(self): ...
```

---

## Issue #3: Services Not Started/Verified — MOSTLY RESOLVED

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

---

## Issue #4: Silent Debug-Level Logging

Use appropriate log levels — don't hide errors at DEBUG:
- **ERROR**: Something broke | **WARNING**: Unusual | **INFO**: User-visible ops | **DEBUG**: Dev internals

---

## Issue #6: Large Files — ALL UNDER THRESHOLD

Only `knowledge_content.py` (1,993 lines) exceeds 1,500 — acceptable as content file.
Monitor files approaching 1,400 lines. Split proactively at 1,000 lines when adding features.

Top files: `meshtastic_protobuf_client.py` (1,433), `service_check.py` (1,410),
`map_http_handler.py` (1,404), `prometheus_exporter.py` (1,399).

---

## Issue #12: RNS "Address Already in Use"

**Rule**: Never call `RNS.Reticulum()` without `configdir=` when rnsd is running.

MeshAnchor creates a client-only config in `/tmp/meshanchor_rns_client/` with
`share_instance = Yes` and no interface definitions, allowing connection to
rnsd without binding ports.

Location: `src/gateway/node_tracker.py` — `_init_rns_main_thread()`

---

## Issue #16: Gateway Message Routing Reliability

Delivery is **best-effort** — inherent to mesh networking. Message queue persists to SQLite for retry.
Always show "Sent (delivery not guaranteed)" or "Queued" status.

Files: `commands/messaging.py`, `gateway/rns_bridge.py`, `gateway/message_queue.py`

---

## Issue #17: Meshtastic Connection Contention (Single-Client TCP)

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

## Issue #18: Auto-Reconnect on Connection Drop

Gateway uses health monitoring + exponential backoff (1s → 2s → 4s → ... → 30s max)
in `rns_bridge.py`. All persistent connections should have health monitoring.
Release connection manager resources on disconnect.

---

## Issue #19: RNS Node Discovery from path_table

Use `RNS.Transport.path_table` (not just `destinations`) for complete routing info.
**path_table may be empty immediately after connect** — use delayed checks (5s) and
periodic re-checks (30s).

Location: `src/gateway/node_tracker.py`

---

## Issue #20: Service Detection & Status Display — ALL DONE

All 3 components resolved:

1. **Service Detection**: Simplified to systemctl-only for systemd services (SSOT)
2. **Status Display**: Separates "service state" from "detection capability" —
   never shows "FAILED" when service is running
3. **RX Messages**: `event_bus.py` → `websocket_server.py` → TUI live feed

### RNS Socket Detection
RNS uses abstract Unix domain sockets (`\0rns/{instance_name}`), not UDP port 37428.
Use `check_rns_shared_instance()` (3-tier: Unix socket → TCP → UDP fallback).

### Prevention
- UI must always distinguish "service state" from "detection capability"
- Use `check_rns_shared_instance()` for all rnsd checks (never raw UDP)

---

## Issue #21: Meshtastic CLI Preset Bug (Upstream)

**Not a MeshAnchor bug.** The Python meshtastic CLI doesn't always apply modem preset
changes correctly. Always verify in browser at `http://localhost:9443` after CLI changes.
Consider direct meshtasticd API calls instead of CLI.

---

## Issue #22: Never Overwrite meshtasticd's config.yaml

**Rule**: Check for existing valid config before touching it.

```
/etc/meshtasticd/
├── config.yaml     # PROVIDED BY meshtasticd — DO NOT OVERWRITE
├── available.d/    # HAT templates — PROVIDED BY meshtasticd — DO NOT CREATE
└── config.d/       # User's active HAT config — COPY from available.d/
```

Radio parameters (Bandwidth, SpreadFactor, TXpower) are set via
`meshtastic --set lora.modem_preset` and stored internally — **NEVER in yaml files**.

MeshAnchor's job: Help users SELECT HATs from meshtasticd's `available.d/`, COPY to
`config.d/`. Never overwrite `config.yaml` if it has a `Webserver:` section.

---

## Issue #23: Post-Install Verification

**Rule**: Never mark install "complete" until verification passes.

`scripts/verify_post_install.sh` checks: meshtasticd binary, config.yaml validity,
Webserver section, port 9443, radio detection, config.d/, rnsd, udev rules.
Also available via `meshanchor --verify-install`.

---

## Issue #24: Python Environment Mismatch (rnsd + meshtastic module)

rnsd's `Meshtastic_Interface.py` plugin requires the `meshtastic` Python module.
pipx isolation, different Python versions, or user vs system site-packages can
make the module invisible to rnsd.

**Fix**: `sudo pip3 install --break-system-packages --ignore-installed meshtastic`
or install to the same Python that rnsd uses:
`head -1 $(which rnsd)` then use that interpreter's pip.

**Diagnose**: `sudo python3 -c "import meshtastic; print(meshtastic.__version__)"`

---

## Issue #27: rnsd is OPTIONAL

MeshAnchor supports two independent transports:
- **MQTT** (mosquitto) — Meshtastic native. Used for preset bridging, monitoring.
- **RNS** (rnsd) — Reticulum. Used for LXMF messaging, cross-protocol bridging.

**Meshtastic preset bridging** (LF ↔ ST) needs only mosquitto — both radios MQTT
uplink/downlink to the same broker with same channel/PSK. No gateway code needed.

**Full NOC** (Meshtastic + RNS) uses both transports. They coexist independently.

---

## Issue #29: Regression Prevention System — ACTIVE

100+ hours of circular regressions led to this 4-layer prevention system.

### Layer 1: Lint Rules (`scripts/lint.py`)
| Rule | Catches |
|------|---------|
| MF007 | Direct `TCPInterface()` outside connection infrastructure |
| MF008 | Raw `systemctl` for service state (use `service_check`) |
| MF009 | `RNS.Reticulum()` without `configdir=` |
| MF010 | `time.sleep()` in daemon loops |

### Layer 2: Regression Guard Tests (`tests/test_regression_guards.py`)
- `TestTCPConnectionContract` — No new direct TCPInterface
- `TestFromradioContract` — TX uses `send_text_direct()`
- `TestServiceCheckContract` — Service state via `check_service()` only
- `TestPathHomeContract` — No `Path.home()` violations
- `TestNoShellTrue` — No `shell=True` in subprocess
- `TestKnownServicesConsistency` — KNOWN_SERVICES stays correct

### Layer 3: Pre-Commit Hook (`.githooks/pre-commit`)
Setup: `git config core.hooksPath .githooks`

### Working With This System

**New file needs meshtasticd TCP:**
```python
# Short-lived:
from utils.connection_manager import MeshtasticConnection
with MeshtasticConnection() as conn:
    if conn: nodes = conn.nodes

# Long-lived:
from utils.meshtastic_connection import MESHTASTIC_CONNECTION_LOCK, wait_for_cooldown
if MESHTASTIC_CONNECTION_LOCK.acquire(timeout=10):
    wait_for_cooldown()
    interface = TCPInterface(hostname='localhost')
```

**Adding legitimate TCPInterface creation:**
1. Add to `ALLOWLISTED` in `TestTCPConnectionContract`
2. Add to `lock_aware_files` in lint.py MF007
3. Acquire `MESHTASTIC_CONNECTION_LOCK` before creating

---

## Issue #30: NomadNet RPC ConnectionRefusedError (2026-03-11)

NomadNet crashes on startup when `get_interface_stats()` can't connect to rnsd's RPC socket.

**Root causes**: RNS version mismatch (pipx venv vs system rnsd), user mismatch
(root rnsd vs user NomadNet), rnsd still initializing, or stale state.

**Fix**: Pre-launch check in `_nomadnet_rns_checks.py` uses NomadNet's own Python
interpreter to test RPC (not system rnstatus). Detects version mismatches and
suggests `pipx upgrade nomadnet`. Auto-restarts rnsd if needed.

Post-failure diagnosis in `nomadnet.py:_diagnose_nomadnet_error` detects
`ConnectionRefusedError` / `Errno 111` patterns in NomadNet logfile.

---

## Issue #31: No Silent Persistent System Changes on Startup (2026-03-12)

**Rule**: NEVER make persistent system changes silently on startup.

MeshAnchor's `auto_lock_port()` was silently adding iptables REJECT rules on port 9443
every TUI launch, persisting after exit. This broke the Meshtastic web UI.

**Prohibited on startup**: iptables rules, cron jobs, udev rules, systemd unit mods,
config file overwrites (see also Issue #22).

MeshAnchor **observes and assists** — it does not take over infrastructure.
Explicit user actions (e.g., service_menu lock/unlock) are acceptable.

**Cleanup for affected users**: `sudo iptables -D INPUT -p tcp --dport 9443 ! -s 127.0.0.1 -j REJECT`

---

## Issue #32: NomadNet "Enabled but Disconnected" Interfaces (2026-03-13)

**Symptoms**: NomadNet shows interfaces as "enabled" but disconnected with no RX/TX.
MeshAnchor status says "rnsd: RUNNING (shared instance available)" when rnsd is actually dead.

**Root causes** (3 bugs):

1. **pgrep false positive**: `check_process_running('rnsd')` fallback used `pgrep -f 'python.*rnsd'`
   which matched any process mentioning "rnsd" (shell invocations, test runners, editors).

2. **Blind status display**: NomadNet status printed "(shared instance available)" without calling
   `check_rns_shared_instance()` — it assumed shared instance from process detection alone.

3. **No diagnostics when down**: Interface health checks (rnstatus, blocking interfaces) only
   ran when rnsd was detected as "running". When detection was wrong or rnsd was genuinely
   down, user got zero actionable diagnostic info.

**Fixes** (2026-03-13):

- `_port_detection.py`: Tightened pgrep regex, added `/proc/{pid}/cmdline` verification
  via `_verify_process_cmdline()` to eliminate self-matches. Same fix for `check_process_with_pid()`.
- `nomadnet.py`: Status display now calls `get_rns_shared_instance_info()` to verify shared
  instance. Shows three states: verified connected (with method), running but no shared instance,
  or not running (with systemd fix hint). Blocking interface diagnostics now shown even when
  rnsd is down.

**Prevention**:
- `check_process_running()` now verifies all pgrep hits via `/proc/cmdline`
- Status display always distinguishes process detection from shared instance availability
- `find_blocking_interfaces()` runs regardless of rnsd state for pre-startup diagnostics

---

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

## Issue #34: meshanchor-map handler thread leakage under slow-peer rollup (2026-05-16)

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

## Issue #35: Channel-0 Public leak — synth-ACK DM contact-not-found cascaded to MeshCore broadcast (2026-05-19)

**Status**: SHIPPED `ead12c7b` + test fixture follow-up `959e7fff`.
Live on meshanchor-server.

**Class**: A DM call site that omits `channel=` defaults the kwarg to
0 in the handler signature. The outbound queue's metadata fallback
(`int(meta.get('channel', 0) or 0)`) silently coerces the missing
value to 0. Contact resolution fails for a stale destination. The
handler logs "falling back to channel {channel} broadcast" and calls
`send_chan_msg(0, text)` — broadcasting the DM payload to MeshCore
slot 0 = Public. Three default-to-zero traps stacking turn a
misdirected DM into a public broadcast. Privacy bug independent of
which channel.

**Symptom**: Operator session on VolcanoAI 2026-05-19 — Meshtastic
broadcast round-trip via Meshtastic → MeshtasticBroadcastBridge →
LXMF → meshanchor-server's `meshtastic_reemit_bridge` → MeshCore
slot 1 ("meshanchor" channel) worked, but data also appeared on
MeshCore Public on both meshanchor-server's local device and the p1
portable. Operator hypothesis "somewhere in the CODE there's a
channel 0" was correct.

**Root cause** — three stacked `channel=0` traps:

1. `src/gateway/rns_bridge.py:1096-1098` — `_meshcore_handler.send_text(text, destination=origin_address)` with **no channel kwarg**.
2. `src/gateway/meshcore_handler.py:865, 869` — double-default `int(meta.get('channel', 0) or 0)` silently coerced None / missing → 0.
3. `src/gateway/meshcore_handler.py:925-934` — contact-not-found fallback called `send_chan_msg(channel, text)` with channel=0.

Trap 1 + Trap 2 stacking on Trap 3 = leak.

**Fix (Option B — recommended; principled)**:

`_send_message` contact-not-found branch:
- DROP the message instead of falling through to `send_chan_msg`.
- Increment `stats['meshcore_dm_dropped_contact_not_found']` so the
  drop rate is operator-observable.
- Log at WARNING with the destination.
- Return False (was True+leak).

`_process_outbound` metadata visibility:
- New `_resolve_channel(raw, source)` helper surfaces missing /
  None / unparsable channel metadata at DEBUG without changing the
  default value. Goal: make the call site detectable rather than
  mask Trap 1.

**Not touched (deliberate)**:
- `rns_bridge.py:1064` Meshtastic-origin synth-ACK still passes
  `channel=0` — Meshtastic primary channel = 0 by convention.
- MeshCore firmware slot-0/slot-1 shared-key display behaviour
  documented at the Meshtastic re-emit bridge work (2026-05-18) —
  firmware UI quirk, not a code defect.

**Tests** (4 new + 1 inverted in `tests/test_meshcore_handler.py`):
- `test_send_message_dm_drops_when_contact_missing` — replaces the
  inverted-contract `test_send_message_dm_falls_back_to_channel_when_contact_missing`
  that had pinned the WRONG behaviour. The old test asserted
  `send_chan_msg.assert_awaited_once_with(1, "ping")`; the new one
  asserts `assert_not_awaited()` and counter == 1.
- `test_send_message_dm_drop_counter_accumulates` — multiple drops
  increment the same counter so operators can see rate over time.
- `test_send_message_dm_no_leak_to_public_on_channel_zero_arg` —
  pins the precise pre-fix leak shape (no channel kwarg → default 0 →
  contact-not-found → must NOT call send_chan_msg).
- `test_resolve_channel_*` (4 tests) — visibility helper contract.

**Operator detection recipes**:

```bash
# Is the leak class currently firing?
ssh meshanchor-server "curl -s http://localhost:8081/api/stats \
  | python3 -c 'import json,sys; d=json.load(sys.stdin); \
      print(d.get(\"meshcore_dm_dropped_contact_not_found\", 0))'"
# 0 = no DM ACKs being misaddressed (or no traffic). Non-zero = visible.

# Cross-check with the daemon journal:
ssh meshanchor-server "journalctl -u meshanchor-daemon.service --since '1 hour ago' \
  --no-pager | grep -E 'MeshCore DM dropped — contact not found'"
```

**Closes**: the deferred plan at
`~/.claude/plans/investagate-why-volcanoai-9443-message-elegant-book.md`.

---

## Issue #36: CI failure log truncated by `| head -500` (2026-05-19)

**Status**: WORKAROUND DOCUMENTED. Same-session fixture fix
`959e7fff` cleared the symptom; the underlying `head -500`
truncation in `ci.yml` is still in place.

**Symptom**: Two consecutive commits (`d10421c5`, `ead12c7b`)
showed red Test Suite (3.10) / (3.11) with `##[error]Process
completed with exit code 1` and no visible failing test name in
the GitHub-rendered log. The displayed log stopped at ~10% test
progress, then jumped 90 seconds later straight to the exit-code
line. Without the actual test name, debugging looked like "did my
commit break CI?" when in fact the same red was pre-existing.

**Root cause**: `.github/workflows/ci.yml` Test Suite step:

```yaml
python -m pytest tests/ -v --tb=short --timeout=30 --timeout-method=thread \
  --ignore=tests/test_bridge_integration.py 2>&1 | tee /tmp/pytest.log | head -500
```

`| head -500` truncates the displayed stream to the first 500 lines
of pytest's verbose output — which at ~5,000 tests reaches ~10%.
When pytest later prints its failure summary and exits non-zero, the
display has already closed.

**Detection recipe**: download the uploaded `pytest.log` artifact
and tail it:

```bash
cd /opt/meshanchor
gh run download <run-id> -n pytest-log-py3.11 -D /tmp/mesh-ci-log
tail -50 /tmp/mesh-ci-log/pytest.log    # has the FAILED ... lines
```

The artifact captures the full `tee` output. The GitHub log just
truncates the display.

**Underlying issue actually fixed**: 14 failures in
`tests/test_message_queue_overflow.py` from Issue #67's
"no sender → drop enqueue" path. Fixture update in `f6b91527`
missed this file; `959e7fff` applies the same
`_register_default_senders` helper from `test_message_queue.py:40`
to the 5 fixtures in the file. Mechanically identical to the prior
fallout fix; just missed.

**Going-forward**: when CI shows red but the displayed log stops at
~10% progress with no test name, **always pull the artifact before
assuming your commit caused it**. The `head -500` line in ci.yml
should eventually be replaced with a tail-based display
(`tail -300 /tmp/pytest.log` runs after pytest completes and
captures the failure summary), but the artifact already has the
full log so the urgency is low.

**Cross-refs**: MeshForge memory
`project_meshanchor_mirror_completeness.md` — same "trust journal
evidence, not CI for sister-repo ports" lesson; this is the CI
equivalent.

---

## Issue #37: Cross-protocol bridge → MeshCore Public leak (2026-05-20)

**Class**: same privacy bug as Issue #35, different door. Issue #35
closed the DM contact-not-found fallback that broadcast misdirected
DMs on slot 0. The cross-protocol bridge path (`Meshtastic→MeshCore`,
`RNS→MeshCore`) had the same default-to-zero shape but went
unnoticed because the original audit focused on the synth-ACK call
sites added that week. Operator-observed leak surface: traffic from
a MeshCore *channel* (e.g. slot 1 "meshanchor") visibly landing on
slot 0 Public on both the local NOC radio and portable nodes.

**Status**: SHIPPED 2026-05-20.

**Root cause** — three stacked defaults, all in the bridge path:

1. `src/gateway/meshcore_bridge_mixin.py:146` — `_process_bridge_to_meshcore` called `self.send_to_meshcore(bridged_content)` with **no channel kwarg**.
2. `src/gateway/meshcore_bridge_mixin.py:37-38` — `send_to_meshcore` defaulted `channel: int = 0`. The asymmetric MC→Meshtastic direction at :82 correctly passes `channel=self.config.meshtastic.channel`; the reverse direction was never wired.
3. `src/gateway/rns_bridge.py:1777` — `_requeue_failed_message` persisted `{message, source_id, destination_id, metadata}` but no top-level `channel`. Replay via `meshcore_handler.queue_send` → `_process_outbound:872` read `msg.get('channel')` from the OUTER dict → None → `_resolve_channel` defaulted to 0.

**Why Issue #35's counter missed it**: `meshcore_dm_dropped_contact_not_found` only fires on the DM-fallback path. Findings #1–#3 are broadcast paths — different code, zero observability before this fix.

**Fix shape**:

- Added `MeshCoreConfig.bridge_target_channel: int = -1` (sentinel "unset"). Operator must set it explicitly (typically `1` for the "meshanchor" private slot) for cross-protocol cargo to reach MeshCore.
- `_process_bridge_to_meshcore` resolves the slot via new helper `_resolve_bridge_target_channel(msg)`:
  1. `msg.metadata['channel']` if present and >= 0
  2. `config.meshcore.bridge_target_channel` if >= 0
  3. -1 → DROP with `meshcore_bridge_default_channel_drop` counter increment
- `send_to_meshcore` signature changed: `channel: int = -1` (sentinel). Broadcasts without an explicit channel are REJECTED at the wrapper layer too — a future caller that forgets `channel=` cannot resurrect the leak.
- `_requeue_failed_message` lifts `metadata['channel']` into the top-level payload at enqueue time.
- `_process_outbound` dict-branch falls back to `metadata['channel']` if top-level `channel` is missing — belt-and-suspenders for any call site that forgets the lift.

**Behaviour change**: deployments currently relying on the silent
slot-0 broadcast (none should — every prior "delivery" to Public
was a leak) will see the new counter increment and a WARNING log
the first time the bridge tries to deliver. Operator action: set
`config.meshcore.bridge_target_channel = 1` (or whichever private
slot the operator's mesh uses) in `gateway.json`.

**Operator detection recipes**:

Counters surface on `/api/stats` (shipped 2026-05-20 commit `90933c86`).
Localhost-only — the daemon binds 127.0.0.1 plus an in-handler
`_check_localhost` gate (defense in depth). Both Issue #35 and #37
privacy counters appear under the `stats` sub-object:

```bash
# Both privacy counters in one ping:
ssh meshanchor-server "curl -s http://localhost:8081/api/stats \
  | python3 -c 'import json,sys; d=json.load(sys.stdin); s=d[\"stats\"]; \
      print(\"dm_dropped:\", s.get(\"meshcore_dm_dropped_contact_not_found\", 0)); \
      print(\"bridge_dropped:\", s.get(\"meshcore_bridge_default_channel_drop\", 0))'"
```

Journal cross-check (each drop emits a WARNING — counter + log fire
together):
```bash
# Successful bridge dispatch (with explicit channel):
ssh meshanchor-server "journalctl -u meshanchor-daemon.service --since '1 hour ago' \
  --no-pager | grep -E 'Bridge (Mesh|RNS)→MC ch[0-9]'"

# Drops (privacy-class refusal at the wrapper):
ssh meshanchor-server "journalctl -u meshanchor-daemon.service --since '1 hour ago' \
  --no-pager | grep -E 'no channel resolved|send_to_meshcore: broadcast with no channel'"
```

Live config + resolver smoke test (verifies the field loaded and the
helper returns the expected slot):
```bash
ssh meshanchor-server 'python3 -c "
import sys; sys.path.insert(0, \"/opt/meshanchor/src\")
from gateway.config import GatewayConfig
print(\"bridge_target_channel:\", GatewayConfig.load().meshcore.bridge_target_channel)
"'
```

A non-zero `bridge_dropped` rate means cross-protocol bridge cargo
is being refused — operator should set `bridge_target_channel` to
the intended private slot.

**Tests** (11 new in `tests/test_rns_bridge.py::TestBridgeToMeshcoreChannelLeak`
+ 3 in `TestRequeueFailedMessage::test_channel_lifted_*` + 2 in
`tests/test_meshcore_handler.py::TestSendPath::test_process_outbound_dict_*`):

- Channel resolution priority (metadata > config > drop)
- Unparsable metadata falls through correctly
- Explicit slot-0 in metadata preserved (operator opt-in to Public)
- `send_to_meshcore` broadcast-without-channel drops + counter
- DM path ignores channel sentinel
- End-to-end `_process_bridge_to_meshcore` with metadata, config, and unset paths
- Requeue payload carries channel at top-level
- `_process_outbound` dict path falls back to metadata.channel

All 33 targeted tests green; full pytest run 4547 passed (the one
test_status_bar failure is pre-existing test-pollution from suite
order, unrelated to this fix).
