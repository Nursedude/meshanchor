# Boundary Observability Charter

> **Goal**: Every integration boundary in MeshAnchor (and MeshForge) produces a forensic when it stalls. Diagnose the next wedge in 30 seconds, not 30 minutes.
> **Author**: WH6GXZ + Claude Code
> **Created**: 2026-05-06
> **Status**: Draft — awaiting approval before Phase A
> **Related**: `.claude/plans/strategic_improvements.md` (axis #1 of the four-axis plan), `.claude/foundations/persistent_issues.md` (#17 TCP contention, rnsd wedge thread)

---

## Why now

Months of dev got the bridges working. The bugs we keep chasing aren't *in* the apps — they're at the **integration boundaries**: RF ↔ TCP ↔ bridge ↔ upstream daemon. The recent rnsd wedge investigation cost ~10 hours of bisection that timing logs would have cut to minutes. The PR #50 instrumentation in `lxmf_broadcast_bridge._send_to_subscriber` proves the pattern works; this charter scales it.

**Reliability/security/configurability all depend on this floor.** You cannot refactor confidently, ship fault injection tests, or write a `meshanchor doctor` without first knowing *which* boundary is slow when something breaks.

---

## Scope

### In scope

Every call where MeshAnchor talks to something *outside its own Python process*:

- rnsd shared-instance RPCs (Unix socket)
- meshtasticd TCP / HTTP (`/api/v1/toradio`, `/api/v1/fromradio`)
- MeshCore TCP companion API
- MQTT publish / subscribe
- HTTP servers we own (map :5001, chat :8081)
- systemctl shell-outs in `service_check.py`
- SQLite operations on hot paths (message_queue, subscriber store)

### Out of scope (deferred)

- Distributed tracing (OpenTelemetry / Jaeger) — overkill for this fleet size
- Prometheus push / external metrics infra — local logs first
- Auto-recovery / circuit breakers — that's a follow-up axis
- Internal in-process function timing — only daemon/socket/RPC boundaries
- HTTP fetches to public APIs (NOAA, HamClock) — already isolated, low blast radius

---

## Shared helper: `utils/boundary_timing.py`

Single source of truth. Pull the inline `_timed_rpc` from `lxmf_broadcast_bridge.py` to `utils/`, expose two forms:

```python
# Context-manager form — best when you want to wrap a block
with timed_boundary("rnsd.has_path", target=hash_short, threshold_s=2.0):
    has = RNS.Transport.has_path(dest_hash)

# Call-wrapper form — best for single function call
result = call_boundary(
    "rnsd.handle_outbound",
    self._router.handle_outbound, lxm,
    target=hash_short, threshold_s=2.0,
)
```

Behavior:
- Sub-threshold calls log at DEBUG (`rpc[label] ok 0.034s`)
- Slow calls log at WARNING (`rpc[label] slow: 4.231s`)
- Exceptions log at WARNING with elapsed time, then re-raise
- Optional `target=` adds a hash/id prefix to the label for fan-out correlation
- Default threshold 2.0s, overridable per call site (e.g., 30s for known long-tail boundaries)

Counters (in-memory, exposed via `get_boundary_stats()`):
- count, slow_count, error_count
- p50, p95, p99 of last N samples (ring buffer, N=1000 default)

No external deps. No async. Drop-in for any synchronous boundary call.

---

## Boundary inventory

### A. RNS / rnsd shared-instance RPCs

| Site | Operation | Already wrapped? |
|---|---|---|
| `gateway/lxmf_broadcast_bridge.py:534` | `has_path` / `request_path` / `Identity.recall` / `handle_outbound` | ✅ PR #50 (inline) |
| `gateway/rns_bridge.py:720-727` | `has_path` / `request_path` / `Identity.recall` / `handle_outbound` (`send_to_rns`) | ❌ |
| `gateway/rns_bridge.py:808-826` | same set (`_queue_send_rns` retry path) | ❌ |
| `gateway/network_topology.py:232` | `RNS.Transport.path_table` read every 10s | ❌ |
| `gateway/node_tracker.py:175` | `RNS.Reticulum(configdir=)` attach | ❌ |
| `utils/service_check.py:check_rns_shared_instance` | Unix-socket probe | ❌ |
| `commands/rns.py:927` | `request_path` (manual TUI) | ❌ |
| `monitoring/rns_sniffer.py:747` | `request_path` (manual probe) | ❌ |

### B. meshtasticd TCP / HTTP

| Site | Operation | Already wrapped? |
|---|---|---|
| `utils/connection_manager.py` | `MeshtasticConnection.__enter__` / `__exit__` | ❌ |
| `utils/meshtastic_connection.py` | `MESHTASTIC_CONNECTION_LOCK.acquire`, `wait_for_cooldown` | ❌ |
| `gateway/meshtastic_handler.py` | `send_text_direct` (POST `/api/v1/toradio`) | ❌ |
| `gateway/meshtastic_protobuf_client.py` | `connect`, `start_polling`, `_read_fromradio` | ❌ |

### C. MeshCore TCP

| Site | Operation | Already wrapped? |
|---|---|---|
| `plugins/meshcore.py` | `connect`, poll loop reads | ❌ |
| `gateway/meshcore_handler.py` | `send_text`, `send_chan_msg` | ❌ |

### D. MQTT (mosquitto)

| Site | Operation | Already wrapped? |
|---|---|---|
| `monitoring/mqtt_subscriber.py` | `connect`, `subscribe`, message receive | ❌ |

### E. Systemd / shell-outs

| Site | Operation | Already wrapped? |
|---|---|---|
| `utils/service_check.py` | `systemctl is-active` / `is-enabled` / restart family | ❌ (but already has timeouts) |

### F. SQLite hot paths

| Site | Operation | Already wrapped? |
|---|---|---|
| `gateway/message_queue.py` | enqueue / dequeue / mark_delivered | ❌ |
| `gateway/lxmf_broadcast_bridge.py` (SubscriberStore) | add / remove / list_all | ❌ |

---

## Phased rollout

Each phase is a separate PR. Each is independently shippable, additive, and reversible.

### Phase A — Extract the helper (1 PR, ~150 LOC)

- Create `utils/boundary_timing.py` with `timed_boundary` context manager, `call_boundary` function, and `get_boundary_stats()`
- Migrate `lxmf_broadcast_bridge._timed_rpc` to use the shared helper
- Add `tests/test_boundary_timing.py` — happy path, slow path, exception path, threshold logic, counter accuracy
- Update CLAUDE.md "Quick Reference" with the import pattern
- **Exit criteria**: existing PR #50 behavior unchanged, helper has unit tests, no other call sites yet

### Phase B — RNS boundaries (1 PR, ~100 LOC)

- Wrap all sites in inventory section A
- Use uniform 2.0s threshold except `node_tracker._init_rns_main_thread` (give RNS attach 10s, it's slow on cold start)
- **Exit criteria**: every RNS RPC produces a timing log; broadcast-bridge tests still pass; no perf regression

### Phase C — meshtasticd boundaries (1 PR, ~80 LOC)

- Wrap connection_manager / meshtastic_connection / meshtastic_handler / protobuf_client
- Special case: `MESHTASTIC_CONNECTION_LOCK.acquire(timeout=10)` already times itself; just emit the elapsed value as a boundary metric
- **Exit criteria**: every meshtasticd TCP/HTTP call produces a timing log; Issue #17 contention regressions still pass

### Phase D — MeshCore + MQTT + systemd (1 PR, ~100 LOC)

- Wrap remaining sites in inventory C, D, E
- **Exit criteria**: every external-process call has a timing wrapper

### Phase E — Status surface (1 PR, ~150 LOC)

- Add `get_boundary_stats()` to `gateway_cli.py status`
- Add a "Boundaries" section to the TUI status bar (show worst-case p95 across all boundaries)
- Emit a structured log line every 60s with per-boundary p50/p95/slow_count for grep-ability
- **Exit criteria**: operator can see which boundary is degrading without tailing daemon logs

### Phase F — MeshForge port (1 PR there, ~50 LOC)

- Backport `utils/boundary_timing.py` (file is generic, no MeshAnchor-specific code)
- Wrap MeshForge's RNS / meshtasticd / MQTT sites mirror-image to MeshAnchor
- **Exit criteria**: both sister projects use the same helper and emit comparable forensics

### Phase G (optional, deferred) — SQLite boundary

- Wrap `connect_tuned()` operations on hot paths
- Skip if Phases A–F prove SQLite isn't a wedge contributor

---

## Success criteria

When this charter is done:

1. **Forensic floor**: any wedge produces a timing log within seconds of the next slow call. No more "rnsd hung, why?" without evidence.
2. **No behavior change** on the happy path. Sub-threshold calls log at DEBUG so production logs aren't noisier.
3. **No measurable perf regression** — the helper adds ~1µs per call (`time.monotonic()` + dict lookup).
4. **Status surface** shows degraded boundaries before they become wedges.
5. **MeshForge parity** — sister project benefits from the same floor.
6. **Test floor**: `tests/test_boundary_timing.py` proves the helper itself is correct, and existing integration tests (when they land in axis #2) can assert "no boundary exceeded threshold during run."

---

## Risks and what NOT to do

- **Don't add try/except around every wrapped call.** The helper times; exceptions propagate. Wrapping changes error behavior and creates dual error paths.
- **Don't introduce new dependencies** (Prometheus client, OpenTelemetry SDK). Logger + in-memory counters only.
- **Don't instrument internal functions.** Only daemon/socket/RPC boundaries. Wrapping internal helpers adds noise without diagnostic value.
- **Don't tune thresholds preemptively.** Default 2.0s. Override only when a boundary has a documented long-tail (RNS attach 10s, NOAA fetch 30s, etc.). Tuning by guess hides real slowness.
- **Don't ship a phase without unit tests for new wraps.** A regression where the helper itself starts swallowing exceptions or missing slow calls would silently degrade the entire forensic floor.
- **Don't change call signatures.** Use context manager / call wrapper; don't refactor the underlying code while wrapping it.

---

## Estimated scope

| Phase | LOC | Time | Risk |
|---|---|---|---|
| A — helper + migrate | ~150 | 0.5 day | Low |
| B — RNS | ~100 | 0.5 day | Low |
| C — meshtasticd | ~80 | 0.5 day | Low |
| D — MeshCore/MQTT/systemd | ~100 | 0.5 day | Low |
| E — status surface | ~150 | 1 day | Medium (TUI changes) |
| F — MeshForge port | ~50 + ~330 | 0.5 day | Low |
| **Total** | **~960 LOC** | **~3.5 days** | **Low overall** |

Each phase is a self-contained PR. Stop after any phase if priorities shift; what shipped still works and still helps.

---

## Decision points

1. **Approve charter, start Phase A?** Or revise scope first.
2. **Threshold default 2.0s?** Or aggressive (1.0s) for richer signal vs. quiet-by-default (5.0s).
3. **Status surface in Phase E or defer?** TUI changes have higher review cost — defer is cheaper.
4. **MeshForge port in lockstep or after MeshAnchor stabilizes?** Lockstep means both projects benefit faster but doubles review work.

---

## Out of scope, captured for follow-up

- **Axis #2 (failure-mode test suite)** — the timing infrastructure here makes integration tests trivial to assert against. After Phase B/C lands, "no boundary exceeded threshold during this run" becomes a one-line test fixture.
- **Axis #3 (bridge architecture refactor)** — composable bridges would shrink the inventory here by collapsing duplicate RNS calls in `rns_bridge.py` and `lxmf_broadcast_bridge.py`. Don't do it before this charter; do it after to validate the refactor doesn't regress timings.
- **Axis #4 (operator surface unification)** — `meshanchor doctor` becomes far easier when boundary stats are already exposed.
