# Review provenance ledger — which commits have been through a review pass

> **Why this file exists**: mirrors MeshForge's `.claude/audits/review_provenance.md`
> (born 2026-07-05). Scoping "what's unreviewed" cost a full exploration pass
> because review provenance lived only in commit bodies. One row per review
> pass, updated when a pass closes. A commit range NOT in this table has had
> **no** adversarial review — that absence is the signal this ledger makes
> legible. Convention: record the RANGE + scope paths + fix commits + where the
> residual/refuted notes live. Never delete rows; supersede them.
>
> **MeshAnchor-specific note**: MeshAnchor was extracted from MeshForge
> (2026-04-01) and shares much of its structure. When MeshForge (the lead repo)
> reviews a shared-shape subsystem, the relevant findings are PORTED here rather
> than re-reviewed from scratch — those rows say "PORT of MF <date>" in the
> Mechanism column and only cover the ported findings, NOT a full independent
> audit of the MeshAnchor file.

| Date | Scope (range + paths) | Mechanism | Fix commits | Residuals / refuted notes |
|------|----------------------|-----------|-------------|---------------------------|
| 2026-07-24 | `src/utils/fleet_truth.py` (byte-locked twin) + `src/mini_dudeai/rollup.py` — the claw-cell + posture-pane half of MeshForge's dude-claw / fleet-monitor pass | PORT of MF 2026-07-24 (`/code-review` adversarial, 8 findings, all confirmed by hand); MA-side verification = `tests/test_fleet_truth.py` 44 passed exit 0, lint 0, parity in sync | `3790ebd7` (MF twin `41f34b4b`) | Ported findings: `_claw_cell.claw_count` counted ABSENT claws ("2 claws healthy" for one claw + a missing primary tick); all-absent now benign `absent=True`; new `claw_present` so a NOC panel cannot invent a claw from an unreachable box; rollup read only the primary tick so a dead second claw was invisible, and a no-mini box's claw was never collected though the renderer always drew one. NOT ported (MF-only surfaces): `web/fleet.html` claw panel, `web/node_map.html` claw cards, `map_federation`, `promote_seed_rules`, `battery_soak`, and the `utils/claw_battery` follow-on — MA has no claw panel or claw probes. Full inventory in MF's row of the same date. |
| 2026-07-06 | maps HTTP hardening + honest_status watchdog leg — `914de08b..b31a7daf` (src/utils/map_http_handler.py, src/utils/_map_fleet.py, scripts/honest_status.sh) | **PORT of MeshForge 2026-07-05 maps-domain QA audit** (adapted to MA's structure, NOT blind-copied) + a diagnosis pass on MA's honest_status. NOT an independent adversarial review of MA's whole map. | `a8a70627` (CORS tail-anchor + LAN-trust gate on /fleet/logs + /fleet/run-test), `b31a7daf` (honest_status watchdog leg → /fleet/blackouts) | PORTED: the shared `origin.startswith` CORS subdomain-bypass (→ tail-anchored `_origin_allowed`) + the ungated /fleet/logs (journal leak) + /fleet/run-test (open-LAN) → LAN-trust `_reject_if_untrusted`. **Deliberately NOT loosened**: `/api/radio/message` + PUT toradio stay `_is_localhost` (loopback-only) — MA is STRICTER than MF here (MF gates radio-TX to LAN-trust; the two repos intentionally differ), test-pinned. **DoS/clamp findings PORTED 2026-07-06 (this commit)**: limit clamp (`?limit=abc` uncaught-500 / `?limit=-1` unbounded), snapshot-window clamp (≤3600), open-redirect Host-validation (`/mesh`), weather-cache lock + single-flight, path-traversal `relative_to` (was `startswith`), destination 32-bit clamp — all 6 defects existed in MA identical to MF pre-fix; +11 tests (`test_map_dos_clamp_port`), MA map/http suite 894 passed. **honest_status fix**: the watchdog leg read a phantom `/var/lib/meshanchor/watchdog.json` (reader with no writer, honest_failure #4 — MF port artifact); rewired to the live `/fleet/blackouts` with a dead-watchdog UNKNOWN guard. +8 tests (`test_map_cors_trust_port`). MA suite 5461 passed; honest_status 6/6 green. Deployed + live-verified on meshanchor-server (CORS bypass blocked / legit reflected / LAN /fleet/logs 200). |
| 2026-07-06 | MA collector + fleet-observability + prometheus layer — FIRST INDEPENDENT adversarial pass (not a port). `dd716d36~1..2243a65e` (src/utils/{map_data_collector,_map_collector_meshtastic,_map_collector_rns,_map_collector_meshcore_public,node_history,prometheus_exporter,map_metrics,_map_fleet}, src/monitoring/{fleet_collector,fleet_aggregator,fleet_history,fleet_watchdog}) | 2 xhigh code-reviewer agents (collectors+node_history; fleet+prometheus) — MF-findings PORT-CHECK + fresh MA-specific review → 24 findings; **self-re-review of the fix commits** (feedback_review_your_own_fixes) → 1 fix-introduced issue caught+fixed (⚠️ mid-review I misjudged agent liveness by output-file mtime and wrongly killed one agent — re-launched; lesson: mtime ≠ liveness) | `dd716d36` (collectors), `b00a1bc9` (fleet/prometheus), `2243a65e` (re-review: B-9 None-return needed a 2nd consumer guarded in _serve_fleet_tests_list) | COLLECTOR: 8/9 shared MF findings PRESENT+fixed (CLI numeric-id → _canonical_meshtastic_id; future-clamp + _coerce_epoch at _is_node_online SSOT; record_observations coord float-coerce; source-dedup `(f.get('properties') or {})` guard; RNS last_heard-stamp + coord-validation + one-axis-phantom; meshtasticd lock-contention→skip-CLI; atomic _save_cache) + MA-specific: meshcore_public UNBOUNDED resp.read() OOM (48MB cap) + settings _safe_int. FLEET/PROMETHEUS: heartbeat-write-lost-on-one-bad-peer-field (false fleet-silent alarm); watchdog CLOSING valid daemon_dead blackout on empty table (→ _daemon_dead_reason evaluated both paths); unbounded peer resp.read() (128MB cap); _list_timers_scope []-masks-failed-probe → None; MF008 raw systemctl → check_service; prometheus POST-body cap; write_to_file fixed-tmp → mkstemp; negative-age not counted active; heartbeat ts guard+clamp. **REFUTED** by the reviewers: prometheus label-VALUE escaping IS handled (metrics_common), WAL+busy_timeout IS set (connect_tuned) — so raw-injection + basic write-contention are not live. +25 tests; full MA suite 5484 passed; lint 0. **DEFERRED (low/perf, in ledger not yet a task)**: prometheus per-node env/health label cardinality cap (B-F6), per-scrape collector-object reuse (B-F7), SERVICE_UP dead gauge (B-F11), PromQL substring-filter silent-no-data (B-F13); collector SQL-vs-Python origin-priority drift (F10), get_nodes_without_position unlocked read (F11-collector), meshcore id-space consistency (F12, conditional on node.id format). |
| 2026-07-19 | fleet-truth honesty invariants, MA side — `491fff34` port surface (src/utils/fleet_truth.py byte-identical builder, src/utils/fleet_truth_collector.py MA shim, _map_fleet._serve_fleet_truth) | Frontier (Fable 5) adversarial pass run in the LEAD repo: self-pass on the invariant core + 6 refutation-framed verifier agents + fresh-eyes renderer/collector attacker; MA receives the reviewed builder as a byte-identical mirror + the collector fixes adapted to MA idiom. Full findings table + residuals live in MeshForge's review_provenance row of the same date (lead-repo convention). | `0afc8add` | MA-specific CONFIRMED+fixed: bounded response read (8 MB / 10s wall — drip-peer can't hang the fan-out lock), http.client.HTTPException caught (garbage HTTP → dark box, not worker-error), monotonic TTL (NTP backstep can't freeze the doc as fresh), IP-shaped fleet.json peer-NAME masking (MF014/15), fanout.membership fleet-of-one transparency. Builder fixes inherited via byte-mirror (stale-signals-not-active, stale-mini-escalations, _ci_cell observed-states-only, services core-taint, box_state roll-up). **RESIDUAL → RESOLVED same-day**: `fleet_config.non_self_peers` bare-hostname drop + IP-self double-count fixed by positive-identity self-detection (resolve → local-bind ownership test, memoized; identity-unknown → KEEP, fail-visible — never a silently dropped row); +5 tests incl. the real bind-probe primitive. The 0-total-coverage residual also closed same-day by the MA status enrichment (`1df8178f`: blackout kinds = MA's enum). |

## Known NEVER-reviewed (as of 2026-07-06)

- The map HTTP surface (row 1) and the collector + fleet-observability +
  prometheus layer (row 2) have now had an adversarial pass. What remains
  NEVER-reviewed: the gateway / RNS-bridge / TUI / automation-engine /
  meshcore-connection paths — field soak + the test suite are their evidence
  base. Any commit outside the two ranges above is unreviewed.
- (RESOLVED 2026-07-06) The MeshForge maps-QA DoS/clamp findings
  (limit/window/open-redirect/path-traversal/weather-lock/dest-clamp) are now
  PORTED — see the 2026-07-06 row. What remains genuinely unreviewed is the rest
  of MA's map surface beyond the specific MF-ported findings (no independent
  adversarial pass over MA's collectors / federation / prometheus equivalents).

## Conventions for future passes

1. When a review pass closes, add its row HERE in the same commit as the fixes.
2. Hand every new pass the residual/refuted pointers of overlapping prior rows.
3. A fix commit is unreviewed code: re-review it before push, and record the
   re-review in the row.
4. A PORT from MeshForge covers only the ported findings — say so, so the rest
   of the MA file stays legibly "never-reviewed."

## Frontier worklist — queued upshift ranges (added 2026-07-16 with the upshift-witness gate port)

> The model advisor's upshift path lands HERE: when a session judges work
> review-shaped but isn't running on a frontier-class model, it appends a row
> instead of faking the pass (enforced by `scripts/review_provenance_check.py`
> in the pre-push hook — twin of MeshForge's, ported 2026-07-16 after its
> first live firing there). Next frontier session: pick from the top. Remove
> a row only by running the pass (its results become a row in the table above).
>
> Note: the 2026-07-13→16 Opus-interregnum range of THIS repo
> (`417d57ad..a1f32f93`) was already reviewed by the MeshForge-side pass
> (MF ledger row 2026-07-16) — MA scope was in that row, twin-first.

| Pri | Scope | Why it's frontier-shaped |
|-----|-------|--------------------------|
| — | _(add new upshift rows here as sessions surface review-shaped work)_ | |
