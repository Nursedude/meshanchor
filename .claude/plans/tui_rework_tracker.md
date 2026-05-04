# TUI Rework Tracker — MeshCore as Primary Radio

> **Charter**: `/home/wh6gxz/.claude/plans/tui-needs-to-be-groovy-charm.md`
> **Memory pointer**: `project_meshcore_primary_rework.md`
> **Started**: 2026-05-03

This is the cross-session source of truth for the MeshCore-primary rework. When a new Claude session starts, **read this file first** to find the in-flight phase and resume.

---

## Phase Status

| # | Phase | Status | Branch / PR | Last touched |
|---|---|---|---|---|
| 1 | Map data flip — MeshCore as source | **MERGED** ✅ | [PR #13](https://github.com/Nursedude/meshanchor/pull/13) (merge 0b91289c) | 2026-05-03 |
| 2 | TUI menu restructure (MeshCore primary, Optional Gateways submenu) | **prepped — design awaits decision** | (Phase 2 section below) | 2026-05-03 |
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

## Phase 2 — TUI Menu Restructure

**Goal**: MeshCore is presented as the primary radio in the TUI's menu hierarchy. Meshtastic + RNS + Gateway live under an "Optional Gateways" branch. Nothing is removed; only menu placement and ordering change.

**Key contract findings (from 2026-05-03 prep exploration)**:

- **Top-level menu** is hardcoded in `src/launcher_tui/main.py:519` (`_run_main_menu`). Six primary entries:
  1. Dashboard
  2. **Mesh Networks** ← all radios live here today, including MeshCore as a peer
  3. RF & SDR
  4. Maps & Viz (feature-flagged on `maps`)
  5. Configuration
  6. System
  Plus quick-access: Tactical (flagged), Quick Actions, Emergency, About, Exit.
  Dispatch table at line 619 (`_handle_main_choice`); dispatch goes through `self._registry.dispatch("main", choice)` first, then a hardcoded fallback dict.
- **`_mesh_networks_menu()` at `main.py:667`** already has `_feature_enabled()` gating per radio. Current ordering is `["meshtastic", "meshcore", "rns", "gateway", "aredn", "messaging", "traffic", "mqtt", "favorites", "ham", "services", "nomadnet"]` — **Meshtastic is listed first**.
- **`menu_section` field** on each handler determines which section it appears in. Today the values are: `main`, `dashboard`, `mesh_networks`, `rns`, `rf_sdr`, `system`, `configuration`, `maps_viz`, `about`. Almost every Meshtastic-leaning handler uses `mesh_networks`; only RNS-specific handlers use `rns`.
- **Cross-cutting handlers** sit in `mesh_networks` today but logically belong with gateway functionality: `dual_radio_failover`, `load_balancer`, `mesh_alerts`, `automation`, `classifier`, `service_menu`, `service_discovery`, `messaging`, `mqtt`, `broker`, `gateway`, `nomadnet`. Their placement is the open question (see below).

**Design options (decision required before coding)**:

| Option | Scope | Files touched | Visible result | Risk |
|---|---|---|---|---|
| **2-light** | Reorder MeshCore first within `_mesh_networks_menu`. Rename "Mesh Networks" → "Radios". Adjust `_ORDERING`. | 1 file (`main.py`), ~30 lines | MeshCore appears first in the existing submenu; Meshtastic/RNS still peer items beneath it | Low — cosmetic |
| **2-full** | Promote MeshCore to its own top-level menu entry. Move Meshtastic/RNS/Gateway handlers under a new "Optional Gateways" submenu. Restructure cross-cutting handlers. | `main.py` + every Meshtastic-flagged handler's `menu_section` (~15 files) + new orchestrator method `_optional_gateways_menu` | MeshCore is its own #2 top-level item; Meshtastic/RNS nested one level deeper | Medium — cross-cutting handler placement is judgment-laden |

**Open questions (must resolve before coding)**:

1. **Top-level vs nested**: option 2-light or 2-full? The original charter implies 2-full ("Optional Gateways submenu"). 2-light is a reversible stepping stone.
2. **Cross-cutting handler placement** (only matters for 2-full): where do `dual_radio_failover`, `load_balancer`, `mesh_alerts` live? They make no sense without a gateway. Three sub-options:
   - (a) Move them under "Optional Gateways" alongside Meshtastic/RNS
   - (b) Keep them in a "Radios" top-level entry (MeshCore + cross-cutting nuts and bolts)
   - (c) Move to a new "Bridging" top-level — explicit, but adds a 7th primary menu item
3. **Top-level menu cap**: current main menu is at 6 primary + 4 quick-access = 10 items, which is the soft UX cap. Adding MeshCore as #2 brings it to 11 — needs to either replace something or compress (e.g. fold "Mesh Networks" → "Optional Gateways" so the count stays at 6).

**Implementation outline (whichever option wins)**:

1. **Branch**: `claude/mc-phase2-menu-restructure` (off main)
2. **Tracker prep PR** (this PR): records findings + open questions + option matrix.
3. **Decision PR or comment**: user picks option + answers open questions.
4. **Implementation PR**:
   - Edit `_run_main_menu()` and `_handle_main_choice()` for top-level changes
   - Edit affected handlers' `menu_section` class attributes
   - Add `_optional_gateways_menu()` orchestrator if going 2-full
   - Update `_get_menu_status_hint()` if status indicators reference Meshtastic-specific signals
5. **Tests**:
   - Add a TUI smoke test that walks the menu tree and asserts MeshCore is reachable as a primary item
   - Existing handler tests should still pass — the dispatch contract doesn't change
6. **Verification**: launch with each profile (MESHCORE, RADIO_MAPS, GATEWAY, FULL) and confirm correct menu structure for each.

**Critical files (cross-reference)**:

- `src/launcher_tui/main.py:519` — `_run_main_menu` (top-level)
- `src/launcher_tui/main.py:608` — `_handle_main_choice` (dispatch)
- `src/launcher_tui/main.py:667` — `_mesh_networks_menu` (current radio submenu — would become "Optional Gateways")
- `src/launcher_tui/main.py:107` — `_build_section_menu` (per-section menu builder)
- `src/launcher_tui/handler_registry.py:42` — `register` (handlers grouped by `menu_section`)
- All 64 handlers in `src/launcher_tui/handlers/` — their `menu_section` class attribute determines placement

---

## Where We Left Off (update each session)

**2026-05-03 (session start)**: Plan approved, branch created, tracker + memory artifacts being written. Next step: implement `node_tracker.get_meshcore_nodes_for_map()`.

**2026-05-03 (Phase 1 implementation complete, PR open)**:
- Branch `claude/mc-phase1-map-data` pushed; PR #13 open against `main`.
- 4 files changed (+434/-29): `map_data_collector.py`, `map_data_service.py`, new `tests/test_map_data_collector.py` (8 tests), this tracker.
- All gates green: lint clean, 17 regression guards passing, 85 node_tracker tests passing, 8 new tests passing, related meshcore/tactical_map tests passing.
- Decided MeshCore positions are deferred to Phase 1.5 — `_on_advertisement()` line 610 in `meshcore_handler.py` doesn't extract GPS because meshcore_py advertisements don't expose it. Position-less side panel covers MeshCore nodes for now.
- **Next session resume point**: wait for PR #13 review/merge, then start Phase 2 (TUI menu restructure — MeshCore primary, Optional Gateways submenu). Add Phase 2 "Key contract findings" + "Implementation outline" sections mirroring Phase 1's structure before coding.

**2026-05-03 (Phase 1 MERGED)**:
- PR #13 merged into main as merge commit 0b91289c.
- Three commits landed: `60175708` (feat — main implementation), `7562c77c` (tracker session-state update), `72ff06fc` (CI fix: pr_overdue_check fetches base via FETCH_HEAD).
- **CI fix propagated**: same `--prune` bug existed in MeshForge's mirror workflow. Fixed in [MeshForge PR #1154](https://github.com/Nursedude/meshforge/pull/1154) — the workflow being fixed ran on the fix-PR itself and passed (6s), so the fix is meta-verified. Bug scope confirmed limited to MeshAnchor + MeshForge (only 2 of 9 `/opt/` repos mirror this workflow).
- **Next session resume point**: start Phase 2. Begin by adding a Phase 2 "Key contract findings" section to this tracker (mirror Phase 1's structure). Phase 2 = TUI menu restructure: top-level menu reordered so MeshCore is primary, Meshtastic + RNS handlers grouped under an "Optional Gateways" submenu. Touches `handler_registry.py` aggregation + per-handler `menu_section`. No handler removed. Branch convention: `claude/mc-phase2-menu-restructure`.

**2026-05-03 (Phase 2 prep — design awaits decision)**:
- Tracker now has full Phase 2 section (above) with Key contract findings, design options matrix (2-light vs 2-full), open questions, and implementation outline.
- **Three open questions blocking implementation**: (1) option 2-light vs 2-full, (2) cross-cutting handler placement under 2-full, (3) top-level menu cap — would adding MeshCore as #2 push us over the 10-item soft UX cap, or do we replace "Mesh Networks" with "Optional Gateways" to stay at 6 primary?
- **Important contract finding**: `_mesh_networks_menu` (`main.py:667`) already gates radios by `_feature_enabled()` — the foundation is in place. Current `_ORDERING` literally lists `["meshtastic", "meshcore", ...]` putting Meshtastic first.
- **Next session resume point**: read the Phase 2 section + answer the three open questions (probably as a brief AskUserQuestion at session start), then implement on `claude/mc-phase2-menu-restructure`. Branch is reserved for the implementation PR.

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
