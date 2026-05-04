# TUI Rework Tracker — MeshCore as Primary Radio

> **Charter**: `/home/wh6gxz/.claude/plans/tui-needs-to-be-groovy-charm.md`
> **Memory pointer**: `project_meshcore_primary_rework.md`
> **Started**: 2026-05-03

This is the cross-session source of truth for the MeshCore-primary rework. When a new Claude session starts, **read this file first** to find the in-flight phase and resume.

---

## Phase Status

| # | Phase | Status | Branch / PR | Last touched |
|---|---|---|---|---|
| 1 | Map data flip — MeshCore as source | **in flight** | `claude/mc-phase1-map-data` | 2026-05-03 |
| 2 | TUI menu restructure (MeshCore primary, Optional Gateways submenu) | not started | — | — |
| 3 | Handler feature-flag audit (~40 Meshtastic handlers) | not started | — | — |
| 4 | MeshCore radio config gap (presets/channels/TX power) | not started | — | — |
| 5 | Startup health flip (meshtasticd → optional) | not started | — | — |
| 6 | meshforge-maps :8808 plugin scaffold | not started | — | — |
| 7 | Profile defaults + docs | not started | — | — |

---

## Phase 1 — Map Data Flip

**Goal**: `:5000/api/nodes/geojson` surfaces MeshCore nodes (via the position-less side panel since MeshCore advertisements don't carry GPS today). `_collect_meshtasticd()` is gated behind the `meshtastic` feature flag. Map renders with meshtasticd offline.

**Key contract findings (from this planning session)**:
- `meshcore_handler._on_advertisement()` (line 610) creates `UnifiedNode` with **no position** — meshcore_py advertisements don't carry GPS today. Position support is a future Phase 1.5 once meshcore_py exposes telemetry-with-position.
- `node_tracker.to_geojson()` (line 736) only returns nodes with valid positions, so MeshCore is invisible to the map today.
- `node_tracker.get_meshcore_nodes()` (line 459) already exists.
- `MapDataCollector._nodes_without_position` is already plumbed end-to-end to the `/api/nodes/geojson` `properties.nodes_without_position` field (served by `map_http_handler.py:560`).
- `MapDataCollector` has no profile / feature-flag awareness — `_collect_meshtasticd()` always runs (but probes TCP port and gracefully returns `[]` if meshtasticd is offline, so it's an efficiency issue, not a crash).
- `MapServer` instantiates `MapDataCollector()` bare in `map_data_service.py:182` — that's the wiring point for the feature flag.

**Implementation outline (this PR)**:
1. `node_tracker.py` — add `get_meshcore_nodes_for_map()` returning `(positioned_features, position_less_dicts)` tuple. Keep `get_meshcore_nodes()` unchanged.
2. `map_data_collector.py` — constructor accepts `meshtastic_enabled: bool = True`. Add `_collect_meshcore()` as the explicit primary source. Gate `_collect_meshtasticd()` behind the flag.
3. `map_data_service.py` — `MapServer` reads the active deployment profile, derives `meshtastic_enabled`, passes to collector.
4. Tests — `tests/test_map_data_collector.py` covering: MeshCore source surfaces position-less nodes; meshtasticd skipped when flag False; source ordering.

**Blockers / open questions**:
- (none — proceeding)

**Definition of Done**:
- [ ] Branch created (`claude/mc-phase1-map-data`)
- [ ] Tracker + memory artifacts written
- [ ] MeshCore source added to MapDataCollector
- [ ] meshtasticd poll gated behind feature flag
- [ ] `:5000/api/nodes/geojson` shows MeshCore nodes when meshtasticd offline
- [ ] Lint clean (`python3 scripts/lint.py --all`)
- [ ] Tests green (`python3 -m pytest tests/test_map_data_collector.py tests/test_node_tracker.py -v`)
- [ ] Regression guards green (`python3 -m pytest tests/test_regression_guards.py -v`)
- [ ] PR opened to main

---

## Where We Left Off (update each session)

**2026-05-03 (session start)**: Plan approved, branch created, tracker + memory artifacts being written. Next step: implement `node_tracker.get_meshcore_nodes_for_map()`.

---

## Decisions Log (cross-phase, durable)

| Date | Decision | Rationale |
|---|---|---|
| 2026-05-03 | Meshtastic handlers gated behind feature flag, not deleted | Preserves `gateway`/`full` profile capability. Reversible. |
| 2026-05-03 | Phase-by-phase PRs to `main` (no long-lived feature branch) | Each PR is small, internally complete, gated by Issue #29 regression suite. |
| 2026-05-03 | Map continues serving on `:5000` — only data source flips | No client-visible URL change; map UI stays compatible. |
| 2026-05-03 | `:8808` (meshforge-maps) is external — Phase 6 plugin scaffold | Not a MeshAnchor port today. |
| 2026-05-03 | MeshCore positions deferred to Phase 1.5 | meshcore_py advertisements don't carry GPS today; surface MeshCore via position-less side panel for now. |

---

## How to Resume This Work in a Fresh Session

1. Read this tracker (you're doing it).
2. Read the charter: `/home/wh6gxz/.claude/plans/tui-needs-to-be-groovy-charm.md`.
3. Check the in-flight phase row. If status is "in flight", continue from "Where We Left Off".
4. If a phase is marked complete and the next is "not started", start by extending this tracker with that phase's "Key contract findings" + "Implementation outline" sections (mirror Phase 1's structure).
5. Always update "Where We Left Off" at the end of the session — even if just one line.
