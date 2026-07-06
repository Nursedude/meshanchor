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
| 2026-07-06 | maps HTTP hardening + honest_status watchdog leg — `914de08b..b31a7daf` (src/utils/map_http_handler.py, src/utils/_map_fleet.py, scripts/honest_status.sh) | **PORT of MeshForge 2026-07-05 maps-domain QA audit** (adapted to MA's structure, NOT blind-copied) + a diagnosis pass on MA's honest_status. NOT an independent adversarial review of MA's whole map. | `a8a70627` (CORS tail-anchor + LAN-trust gate on /fleet/logs + /fleet/run-test), `b31a7daf` (honest_status watchdog leg → /fleet/blackouts) | PORTED: the shared `origin.startswith` CORS subdomain-bypass (→ tail-anchored `_origin_allowed`) + the ungated /fleet/logs (journal leak) + /fleet/run-test (open-LAN) → LAN-trust `_reject_if_untrusted`. **Deliberately NOT loosened**: `/api/radio/message` + PUT toradio stay `_is_localhost` (loopback-only) — MA is STRICTER than MF here (MF gates radio-TX to LAN-trust; the two repos intentionally differ), test-pinned. **DoS/clamp findings PORTED 2026-07-06 (this commit)**: limit clamp (`?limit=abc` uncaught-500 / `?limit=-1` unbounded), snapshot-window clamp (≤3600), open-redirect Host-validation (`/mesh`), weather-cache lock + single-flight, path-traversal `relative_to` (was `startswith`), destination 32-bit clamp — all 6 defects existed in MA identical to MF pre-fix; +11 tests (`test_map_dos_clamp_port`), MA map/http suite 894 passed. **honest_status fix**: the watchdog leg read a phantom `/var/lib/meshanchor/watchdog.json` (reader with no writer, honest_failure #4 — MF port artifact); rewired to the live `/fleet/blackouts` with a dead-watchdog UNKNOWN guard. +8 tests (`test_map_cors_trust_port`). MA suite 5461 passed; honest_status 6/6 green. Deployed + live-verified on meshanchor-server (CORS bypass blocked / legit reflected / LAN /fleet/logs 200). |

## Known NEVER-reviewed (as of 2026-07-06)

- The bulk of MeshAnchor's map/gateway/collector code predates any adversarial
  review — field soak + the test suite are its evidence base. The 2026-07-06
  row above covers ONLY the ported hardening findings, not a full map audit.
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
