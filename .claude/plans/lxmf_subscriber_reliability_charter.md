# LXMF Subscriber Reliability Charter

> **Goal**: Make the LXMF broadcast bridge reliable at fleet scale. Replace today's
> per-client probe model with announce-based discovery, add a subscriber state
> machine + tiers, and harden fan-out against dead-subscriber retry storms. When
> shipped, this charter makes the smaller per-client issues (NomadNet not
> subscribed, MeshChatX hash undiscoverable, federation peers untracked, dead
> subscribers burning CPU) moot.
> **Author**: WH6GXZ + Claude Code
> **Created**: 2026-05-12
> **Status**: Drafted. No sessions started.
> **Related**: `project_lxmf_subscriber_management_roadmap.md` (memory),
> `project_lxmf_broadcast_bridge.md` (memory), `project_rnsd_wedging_hypothesis.md` (memory).

---

## Why now

The "Subscribe Local Client" TUI (shipped 2026-05-12) closed the immediate gap —
operator can one-tap subscribe NomadNet from the TUI. But the deeper architecture
is brittle:

1. **Per-client probe model doesn't scale.** Every new LXMF client (NomadNet,
   MeshChatX, Sideband, future apps) reinvents identity storage and needs a new
   probe function. We can't always assume upstream cooperation.
2. **No subscriber state.** A subscriber that's offline / dead / unreachable is
   indistinguishable from a healthy one. Every fan-out attempts delivery to dead
   subscribers and burns `Transport.request_path` + `Identity.recall` on each
   broadcast.
3. **Federation makes the count grow.** Today the bridge has 2 subscribers on
   `meshanchor-server`. At fleet scale (5+ NOCs, each with operators running
   2–3 LXMF clients) we're 30+ subscribers per bridge with no visibility into
   liveness.
4. **No GC.** Memory rule says never auto-prune (a silent prune is worse than a
   dead row). But the operator has no surface to *see* dead rows and prune
   manually.
5. **Suspected wedge contributor.** `project_rnsd_wedging_hypothesis.md` notes
   the bridge's per-RPC timing instrumentation (PR #50) didn't reproduce the
   wedge in isolation — but the bridge is still the prime suspect under live
   daemon load. Per-subscriber retry storms on dead hashes is a plausible
   driver.

Operator framing 2026-05-12: *"this will make other issue [moot] — improve
reliability."* This charter is that improvement.

---

## The four shifts (operator-facing summary)

1. **Subscriber state machine + tiers** — every subscriber carries `tier` (local
   / federation / external) and `state` (healthy / degraded / stale / dead).
   State transitions on every fan-out attempt.
2. **Announce-based discovery** — bridge listens for `lxmf.delivery` announces
   on the local shared rnsd; locally-originated announces auto-subscribe as
   `tier=local`. The `_lxmf_clients_discovery` probes stay as fallback / manual
   override.
3. **Reliability hardening** — bounded fan-out concurrency, exponential backoff
   on path-request retries, mark-stale after K consecutive failures, operator-
   confirmed prune surface.
4. **Observability** — Prometheus metrics with tier/state labels, structured
   log events on state transitions, /healthz includes fan-out queue depth, TUI
   panel renders state breakdown.

Sessions 2–4 build on the state machine + tiers from Session 1.

---

## Non-goals

- **Auto-prune.** Operators surface dead subscribers; never delete without an
  explicit operator action. Memory rule.
- **Cross-protocol propagation back into MeshCore.** Bridge stays RX-only fan-out
  (today's contract). Loop prevention is harder than this charter's scope.
- **Upstream LXMF protocol changes.** Roadmap option B (standardize
  `~/.config/lxmf/identities.d/`) is multi-quarter and outside our control.
  Mention only as a "graceful future" if upstream lands it.
- **MeshChatX-specific hash discovery.** Today's best-effort probe stays;
  Session 2's announce listener should pick up MeshChatX's announces just like
  NomadNet's, so the probe becomes redundant.

---

## Session 1 — Subscriber state machine + tiers (delta on today's work)

**Goal**: Layer `tier` + `state` per subscriber on top of the SubscriberStore +
fan-out path that shipped 2026-05-12. The bridge already records success per
subscriber (`mark_delivered` + `last_delivery`); this session adds the failure
side and the categorisation. **No new behavior** — automatic state transitions
to `stale`/`dead` are deferred to S3, auto-discovery to S2.

### What's already on disk (2026-05-12)
- `Subscriber` dataclass with `lxmf_hash`, `added_at`, `last_delivery`
  (`src/gateway/lxmf_broadcast_bridge.py:100-105`).
- `SubscriberStore.add` / `.remove` / `.list_all` / `.mark_delivered`
  (`:108-191`).
- Per-call success path → `mark_delivered` (`:571`).
- Three failure paths in `_send_to_subscriber` that already log + bump a
  global `stats["errors"]` counter — exact slots where `mark_failed` lands:
    - `bytes.fromhex(sub.lxmf_hash)` ValueError (`:530-532`).
    - `Identity.recall(...) is None` (`:556-558`).
    - generic `except Exception` (`:575-579`).
- `get_status()` already returns per-subscriber row dicts (`:590-617`).
- HTTP `/lxmf-broadcast/status` + TUI renderer ship per-subscriber columns.

### S1 delta (the actual change)

- **DB columns added** to `subscribers` table:
  - `tier TEXT NOT NULL DEFAULT 'external'` — values: `local`, `federation`,
    `external`. Existing 2 subscribers on `meshanchor-server` migrate to
    `'external'` via the column default.
  - `state TEXT NOT NULL DEFAULT 'healthy'` — values: `healthy`, `degraded`,
    `stale`, `dead`. S1 only writes `healthy` (on success) — `degraded`/etc
    arrive with S3's transition logic.
  - `consecutive_failures INTEGER NOT NULL DEFAULT 0`.
  - `last_failure_at TEXT NULL`.

  Drop `last_success_at` from the original draft — the existing `last_delivery`
  column already serves that role. Defer `path_last_seen` to S3 (backoff math).

- **`SubscriberStore` additions**:
  - Extend `Subscriber` dataclass with `tier`, `state`, `consecutive_failures`,
    `last_failure_at`.
  - `add(hash, tier='external')` — same idempotency contract; tier fixed at
    first add (S2 may upsert-promote `external` → `local` for auto-discovered
    rows).
  - `mark_failed(hash, reason)` — sets `last_failure_at=now`, increments
    `consecutive_failures`. (Does NOT transition state — that's S3.)
  - `mark_delivered(hash)` extended to also set `consecutive_failures=0` and
    `state='healthy'`. Backwards compat: same call signature.
  - `transition_state(hash, new_state)` — explicit state setter with one
    INFO log line. Unused in S1 (no automatic transitions yet) but lets S3
    layer on without touching this file again.
  - `list_by_state` / `list_by_tier` — small read helpers for the status
    renderer + future S3 queries.

- **Wire `mark_failed` at the three existing `except` paths** in
  `_send_to_subscriber`. No new failure paths invented.

- **Status JSON**: per-subscriber row gains `tier`, `state`,
  `consecutive_failures`, `last_failure_at`.

- **TUI Bridge Status renderer**: line per subscriber expands from
  `<hash>  added=…  last=…` to `<hash>  tier=…  state=…  fails=N  added=…  last_ok=…  last_fail=…`.

- **DBSpec entry** in `src/utils/db_inventory.py` updated with the new column
  list — schema doc stays accurate.

### Key files
- `src/gateway/lxmf_broadcast_bridge.py` — schema migration in
  `SubscriberStore._init_schema`, `Subscriber` dataclass, `add` / `mark_failed`
  / `mark_delivered` / `transition_state` / `list_by_*`, `_send_to_subscriber`
  failure wiring, `get_status` row shape.
- `src/utils/db_inventory.py` — DBSpec for `lxmf_broadcast_subs.db`.
- `src/launcher_tui/handlers/lxmf_broadcast.py` — extended renderer line.
- `tests/test_lxmf_broadcast_bridge.py` — `mark_failed` + tier-aware `add` +
  schema-migration tests (open old DB, confirm new columns appear with
  defaults).
- `tests/test_handlers_lxmf_broadcast.py` — renderer shows new fields.

### Migration safety

The DB lives at `~/.config/meshanchor/lxmf_broadcast_subs.db` on
`meshanchor-server` with 2 live subscriber rows. Migration runs in
`_init_schema` via `ALTER TABLE ... ADD COLUMN ... DEFAULT ...` — SQLite
supports this for NULL/literal defaults and the existing rows pick up the
defaults atomically. Test: copy the live DB to a tmp path, run schema init
against it, assert all 2 rows now have `tier='external'`, `state='healthy'`.

### Verification

- `python3 -m pytest tests/test_lxmf_broadcast_bridge.py tests/test_lxmf_broadcast_api.py tests/test_handlers_lxmf_broadcast.py -v` → green.
- `python3 scripts/db_audit.py` clean.
- `python3 scripts/lint.py --all` clean.
- Drop-in deploy to `meshanchor-server`; restart daemon. Existing 2
  subscribers migrate to `tier=external`, `state=healthy`. NomadNet (just
  subscribed) shows `tier=external` initially — S2 will promote to `local`.
- TUI Bridge Status renders the new columns.
- Send a MeshCore broadcast from a peer; verify `last_delivery` updates for
  healthy subscribers and `consecutive_failures` stays 0.
- Synthetically subscribe a junk hash (`0000…0000`); verify
  `consecutive_failures` increments on each broadcast.

### Out of scope for S1
- Automatic state transitions to `degraded` / `stale` / `dead` — S3.
- Auto-subscribe via announces — S2.
- Backoff on failed subscribers — S3 (today every broadcast still attempts
  every subscriber; that's the existing behavior, just visible now).

---

## Session 2 — Announce-based discovery (makes per-client probes moot)

**Goal**: Bridge listens for LXMF delivery-identity announces on the shared
rnsd. Locally-originated announces auto-subscribe the announcing identity as
`tier=local`. The probe-per-client model becomes redundant — any new local
LXMF client that announces is picked up automatically.

### Research / risk

- **Is "local-origin" detectable?** RNS announces carry interface metadata.
  Local-origin = announce arrived on the shared-instance Unix socket, not over
  a radio interface. Need to confirm RNS 1.2.x exposes this on the announce
  callback. Spike before committing the session.
- **Loopback risk.** The bridge announces its own identity. Its own announce
  arriving on the loopback must NOT subscribe the bridge to itself. Filter by
  hash equality against `bridge._destination_hash`.

### Scope

- `LXMFBroadcastBridge._on_announce(destination_hash, app_data, interface)`
  callback registered via `RNS.Transport.register_announce_handler(aspect_filter='lxmf.delivery')`.
- Local-origin detection: `interface.is_local` (or equivalent — confirm in
  spike).
- Filter: ignore announces for our own destination hash; ignore announces from
  hashes already in the subscriber DB (state-preserving).
- On local-origin new announce: `_subs.add(hash, tier='local')` + log INFO
  line + emit structured event.
- New config flag `LXMFBroadcastConfig.auto_subscribe_local` (default **True**
  for new installs; existing deployments keep their explicit-subscribe behavior
  via a one-shot migration check).
- Operator-visible "auto-subscribe is on/off" line in Bridge Status.
- Probes in `_lxmf_clients_discovery.py` stay as fallback for clients that
  don't announce (or operator wants to subscribe before client launches).

### Key files

- `src/gateway/lxmf_broadcast_bridge.py` — announce hook, local-origin filter.
- `src/gateway/config.py` — `LXMFBroadcastConfig.auto_subscribe_local`.
- `src/launcher_tui/handlers/lxmf_broadcast.py` — render the auto-subscribe state.
- `tests/test_lxmf_broadcast_bridge.py` — announce callback unit tests with
  mocked RNS Transport (local vs radio interface, own-hash filter, already-
  subscribed no-op).
- New regression guard: bridge MUST register exactly one announce handler at
  start and clean it up at stop.

### Verification

- Unit: simulated announces from a fake interface (`is_local=True/False`)
  exercise both paths.
- Field on `meshanchor-server`: cold-start NomadNet, verify the bridge auto-
  subscribes it within 60s of the LXMF identity announce. Confirm tier=local.
- Field on `meshanchor-server`: send announces from a remote NOC; confirm they
  are NOT auto-subscribed (interface != local).
- Regression: own-hash announce loop doesn't self-subscribe.

### Out of scope for S2

- Demoting/promoting existing `tier=external` subscribers to `tier=local`
  retroactively (one-shot migration if needed; not behavior).
- Federation-aware tier=federation auto-add (deferred or possibly never — the
  federation handshake is its own protocol).

---

## Session 3 — Reliability hardening

**Goal**: Bound fan-out cost. Dead subscribers stop burning per-broadcast
retries. State transitions to `stale`/`dead` happen automatically; operators
get a "Stale Subscribers" surface to prune.

### Scope

- **Per-subscriber backoff**: on `consecutive_failures > 0`, skip fan-out
  attempts until backoff window elapses. Backoff = `min(2^failures * 30s, 1h)`.
- **State transitions**:
  - `healthy` → `degraded` after 3 consecutive failures.
  - `degraded` → `stale` after 24h with no success.
  - `stale` → `dead` after 7d with no success.
  - Successful delivery resets to `healthy` from any state.
- **Bounded fan-out**: `concurrent_fanouts` config (default 8). Above the cap,
  queue and drain on completion. Prevents the "100 subscribers all retry
  request_path simultaneously" pathology.
- **Tier-aware retry budget**: `local` gets aggressive retry (default 1s
  backoff floor, 10min cap); `external` gets lazy retry (30s/1h);
  `federation` in between (5s/30min). Operator overrides via config.
- **TUI "Stale Subscribers"** menu action under LXMF Broadcast Bridge:
  list `state in (stale, dead)` rows; operator can prune individual rows
  (NEVER bulk). Each prune is logged.
- **HTTP `DELETE /lxmf-broadcast/subscribers/<hash>`** — localhost-only,
  drives the prune action.

### Key files

- `src/gateway/lxmf_broadcast_bridge.py` — backoff, state transitions, fan-out
  queue.
- `src/utils/lxmf_broadcast_api.py` — DELETE handler.
- `src/utils/config_api.py` — wire DELETE dispatch.
- `src/launcher_tui/handlers/lxmf_broadcast.py` — Stale Subscribers menu action.
- `src/gateway/config.py` — new tunables under `LXMFBroadcastConfig`.
- Tests for state machine transitions + backoff math.

### Verification

- Unit: fake "subscriber path always fails" simulates the 3-failure → degraded
  → 24h → stale → 7d → dead trajectory.
- Field: subscribe a fake hash to a known-dead destination; observe state
  transitions in TUI over time (skip ahead by tweaking timestamps in DB for
  test).
- Field: live MeshCore broadcast with 2 healthy + 1 dead subscriber — confirm
  dead subscriber stops getting `request_path` attempts after backoff kicks
  in. Verify with `journalctl` per-RPC timing.

### Out of scope for S3

- Cross-host coordination of dead-subscriber lists (federation gossip).
- Automatic prune (operator-only).

---

## Session 4 — Observability

**Goal**: Prometheus metrics with labels. Structured events for state
transitions. /healthz includes bridge fanout depth. TUI dashboard panel.

### Scope

- **Prometheus metrics** (extend `src/utils/metrics_exporter.py` and
  `health_api.py`):
  - `meshanchor_lxmf_broadcast_subscribers{tier, state}` — gauge.
  - `meshanchor_lxmf_broadcast_fanouts_total{tier, outcome}` — counter
    (outcome ∈ success/path_missing/identity_recall_null/outbound_error).
  - `meshanchor_lxmf_broadcast_fanout_duration_seconds` — histogram.
  - `meshanchor_lxmf_broadcast_state_transitions_total{from, to}` — counter.
  - `meshanchor_lxmf_broadcast_fanout_queue_depth` — gauge (from S3).
- **/healthz**: include `lxmf_broadcast.fanout_queue_depth`, `lxmf_broadcast.dead_subscribers`.
- **Structured log events** on every state transition: JSON one-liner with
  hash (first 8 chars only — privacy), from_state, to_state, reason.
- **TUI dashboard panel**: under Stack Health, show subscribers-by-state
  histogram + last-hour fan-out outcome breakdown.
- **MeshForge port surface**: when MF Phase D-4 alertmanager rules land
  (memory `project_meshforge_audit_2026_05_09.md`), the metric names above
  match the existing `meshanchor_map_http_*` naming — so alertmanager
  rules can apply uniformly.

### Key files

- `src/utils/metrics_exporter.py` (or wherever Prom registration lives).
- `src/utils/health_api.py` — new health fields.
- `src/gateway/lxmf_broadcast_bridge.py` — emit structured events.
- `src/launcher_tui/handlers/fleet_health.py` — new panel.
- Tests for metric registration + label cardinality.

### Verification

- `curl http://127.0.0.1:8081/metrics | grep meshanchor_lxmf_broadcast` shows
  the new metrics on a fresh deploy.
- `curl http://127.0.0.1:8081/healthz` includes the new fields.
- TUI Stack Health renders the panel; no crash when bridge is inactive.

### Out of scope for S4

- Grafana dashboard JSON (operator-owned, can come later).
- Alertmanager rules (depends on MF Phase D-4 landing).

---

## Cross-session verification

- After S2 ships: cold-start meshanchor-server → restart NomadNet → confirm
  NomadNet auto-subscribes within 60s without operator intervention. This is
  the load-bearing "moot" demonstration.
- After S3 ships: kill rnsd on a peer, wait 7 days, confirm that peer's
  subscriber row transitions to `dead` and stops receiving fan-out attempts.
  Confirm bridge CPU drops vs S2-only baseline.
- After S4 ships: an external Prometheus poller sees state transitions land
  as counter increments; alertmanager can fire on
  `rate(meshanchor_lxmf_broadcast_fanouts_total{outcome!="success"}[5m]) > 0.1`.

---

## Memory entry to write when shipped

Replace `project_lxmf_subscriber_management_roadmap.md` with a `project_lxmf_subscriber_reliability_charter.md`
entry once S1 ships. Format: ship state per session, what code references for
"how does state get to X", reopen conditions if a regression surfaces.

---

## Open questions for the operator

1. **Default for `auto_subscribe_local` (S2)** — operator-confirmed default
   True so new installs Just Work, but the field-deployed `meshanchor-server`
   keeps explicit-subscribe? Or flip both to auto?
2. **`tier=federation` discovery** — happy to defer entirely. The federation
   handshake is its own protocol; treating known federation peers as `tier=federation`
   when they DM `subscribe` is doable but requires a peer-list config.
3. **Schedule** — S1 in one session feels right. S2 has a research spike for
   "is interface.is_local exposed?" that may turn into a 2-session split. S3
   + S4 are mostly mechanical. Ballpark: 4–5 sessions total.
