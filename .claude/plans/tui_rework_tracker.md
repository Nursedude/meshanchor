# TUI Rework Tracker — MeshCore as Primary Radio

> **Charter**: `/home/wh6gxz/.claude/plans/tui-needs-to-be-groovy-charm.md`
> **Memory pointer**: `project_meshcore_primary_rework.md`
> **Started**: 2026-05-03

This is the cross-session source of truth for the MeshCore-primary rework. When a new Claude session starts, **read this file first** to find the in-flight phase and resume.

---

## Phase Status

> **Lifecycle**: a phase has three states only — `merged ✅`, `in flight`, `not started`. When a phase merges, the **next phase's prep PR** flips its row to `merged ✅` (one-line edit, no standalone bump PR). The `implementation — PR pending` intermediate state is gone — it caused mid-PR tracker conflicts and forced a separate roundtrip per phase. Source of truth for "did this merge" is `git log` / `gh pr view`; this table just gives a scannable index.

| # | Phase | Status | Pointer |
|---|---|---|---|
| 1 | Map data flip — MeshCore as source | merged ✅ | [PR #13](https://github.com/Nursedude/meshanchor/pull/13) |
| 2 | TUI menu restructure (MeshCore primary, Optional Gateways submenu) | merged ✅ | [PR #16](https://github.com/Nursedude/meshanchor/pull/16) |
| 3 | Handler feature-flag audit (opt-in flagging, 12 handlers / 17 rows) | merged ✅ | [PR #18](https://github.com/Nursedude/meshanchor/pull/18) |
| 4 | MeshCore radio config gap (presets/channels/TX power) | in flight | `claude/mc-phase4-meshcore-config` |
| 5 | Startup health flip (meshtasticd → optional) | not started | — |
| 6 | meshforge-maps :8808 plugin scaffold | not started | — |
| 7 | Profile defaults + docs | not started | — |

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

## Phase 4 — MeshCore Radio Config Gap

**Goal**: Surface MeshCore's LoRa radio parameters (preset, channel slots, TX power, frequency band) inside the existing MeshCore TUI submenu so MeshCore-primary users can configure the radio without leaving MeshAnchor for the external `meshcore_set_channel.py` script or the Node-Connect web UI.

**Key contract findings (from 2026-05-03 prep exploration)**:

- **Today's MeshCore submenu** (`src/launcher_tui/handlers/meshcore.py:44`, `_meshcore_menu`) exposes 8 items: `status`, `detect`, `config`, `enable`, `nodes`, `stats`, `chat`, `daemon`. **Zero are radio parameters.**
- **The `config` item is connection-only** (`_meshcore_configure`): serial path, baud rate, TCP host/port, plus the bridge-channels / bridge-DMs toggles. Not LoRa preset, not channel slot, not TX power, not frequency.
- **Where MeshCore radio config goes today**: external `meshcore_set_channel.py` script + Node-Connect web UI. Confirmed by the comment at `src/gateway/meshcore_handler.py:869` ("slots set up via meshcore_set_channel.py / Node-Connect"). MeshAnchor users currently leave the TUI to configure the radio.
- **meshcore_py command surface used by MeshAnchor today** (greppable in `src/gateway/meshcore_handler.py`): `commands.get_contacts`, `commands.send_msg`, `commands.send_chan_msg`, `commands.get_channel_messages`, `commands.get_messages`, plus lifecycle (`subscribe`, `start`, `start_auto_message_fetching`, `stop`/`disconnect`/`close`). **No radio-config commands are used today** — this is the API surface gap.
- **Daemon HTTP API surface** (`src/utils/config_api.py`): `/chat/send`, `/chat/messages?since=<id>`, `/chat/channels`, plus generic `/config/*` (key/value config server, validators in place). **No `/radio` or `/preset` endpoint exists.** Whatever Phase 4 adds will be greenfield routes on the daemon.
- **Serial-port locking constraint**: per memory + `meshcore_handler.py`, the MeshCore daemon owns p4's serial port. The TUI runs in a separate process and cannot open the port directly — any radio-config write has to flow through the daemon (HTTP API or shared-state IPC), not via a parallel meshcore_py connection from the TUI.
- **File size headroom**: `meshcore.py` (TUI handler) is 840 lines, `meshcore_handler.py` (daemon) is 1230 lines. Both under the 1500-line cap, but if Phase 4 adds three new TUI methods (preset / channel / TX power) plus matching daemon endpoints, the daemon side will likely cross 1400 — schedule a split if it does (per `persistent_issues.md` Issue #6).

**Open questions (resolved 2026-05-03 during Phase 4a)**:

1. **Does meshcore_py expose radio-config commands?** ✅ YES. Confirmed against upstream `src/meshcore/commands/device.py` + `src/meshcore/reader.py`. Reads: `commands.send_appstart()` → `EventType.SELF_INFO` (payload contains `radio_freq` MHz, `radio_bw` kHz, `radio_sf`, `radio_cr`, `tx_power`, `max_tx_power`, `name`); `commands.send_device_query()` → `EventType.DEVICE_INFO` (payload contains `fw ver`, `max_channels`, `model`, `fw_build`); `commands.get_channel(idx: int)` → `EventType.CHANNEL_INFO` (payload contains `channel_idx`, `channel_name`, `channel_secret`, `channel_hash`). Writes (deferred to 4b): `commands.set_radio(freq, bw, sf, cr)`, `commands.set_tx_power(val)`, `commands.set_channel(idx, name, secret)`. All async; same `commands.*` surface the daemon already uses for `send_msg` / `get_contacts`.
2. **Where do the new daemon endpoints live?** Picked option (a): added a single `GET /radio[?refresh=1]` route to `ConfigAPIHandler` in `src/utils/config_api.py` (1412 → 1455 lines, well under cap). Kept the surface coherent with `/chat/*`. Single endpoint instead of three (`/radio/preset` + `/radio/channels` + `/radio/tx_power`) — they all read from the same cached snapshot, and one round-trip is faster + simpler than three for the TUI display block.
3. **Read-only vs write?** ✅ Phase 4a is read-only as recommended. Writes ship in 4b.
4. **Is "frequency" exposed as a separate config or rolled into preset?** ✅ Settled: there is **no preset on the wire**. MeshCore stores raw `(freq_MHz, bw_kHz, sf, cr)`. `set_radio` takes the four numbers; "preset" is only a UI mapping. Phase 4a displays all four numerically and adds a small `_radio_preset_name()` lookup table for common tuples (EU 869, US 915, etc.) — returns `None` when no match, so the four numbers are always the source of truth.

**Implementation outline** (assumes Q1 = "yes, meshcore_py exposes the commands", Q3 = "Phase 4a is read-only first"):

1. **Branch**: `claude/mc-phase4-meshcore-config` (this branch — currently in prep mode).
2. **Prep PR (this PR)**: tracker updates only — Phase Status table simplified + Phase 4 section written (this section). No code.
3. **Implementation PR (Phase 4a — read-only)**:
   - `src/utils/config_api.py` — add `GET /radio/preset`, `GET /radio/channels`, `GET /radio/tx_power`. Each endpoint reads from a shared MeshCore daemon state object (same one chat reads from) and returns JSON.
   - `src/gateway/meshcore_handler.py` — extend the existing daemon to query meshcore_py for radio params on connect + on a periodic refresh, store in a `RadioState` dataclass, expose to the HTTP layer.
   - `src/launcher_tui/handlers/meshcore.py` — add a single new menu item `radio` ("Radio Config        Preset, channels, TX power (read-only)") between `config` and `enable`. New method `_meshcore_radio_status()` calls the three GET endpoints and prints a status block.
   - Tests: `tests/test_phase4a_radio_readonly.py` — fixture mocks the three GET endpoints, asserts the TUI handler prints the expected fields when daemon responds, and shows "MeshCore daemon not running" when the connection fails.
4. **Implementation PR (Phase 4b — writes, separate PR after 4a merges)**:
   - Add `PUT /radio/preset`, `PUT /radio/channels/<slot>`, `PUT /radio/tx_power` with input validation (preset enum, slot 0-31, TX power per region cap).
   - Three new TUI methods: `_meshcore_set_preset`, `_meshcore_set_channel_slot`, `_meshcore_set_tx_power`. Each shows current value, new value preview, double-confirm dialog, then PUT.
   - Tests assert input validation rejects out-of-range / wrong-region values.
5. **Verification (post-4a)**: launch TUI under MESHCORE profile, navigate MeshCore → Radio Config, confirm display matches what `meshcore_set_channel.py --show` reports against the same radio.

**Critical files (cross-reference)**:

- `src/launcher_tui/handlers/meshcore.py:44` — `_meshcore_menu` (insertion point for the new "radio" entry)
- `src/launcher_tui/handlers/meshcore.py:84` — `_meshcore_status_line` (status hint may want a preset summary added in 4a)
- `src/gateway/meshcore_handler.py:869` — comment confirming current external-tool config path (worth removing in 4b once writes work)
- `src/utils/config_api.py:1012` — chat endpoint dispatch (model for /radio dispatch)
- `src/utils/config_api.py:147` — `ConfigValidator` (reusable for /radio PUT input validation in 4b)

**Blockers / open questions**:

- Q1, Q2, Q3, Q4 above. Q1 is the load-bearing one — answer it first, then proceed. Q3 is the scope-control lever — strong recommendation is read-only first.

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

**2026-05-03 (Phase 2 implementation — PR pending)**:
- Auto-mode session continued the tracker. The three open questions were resolved as:
  1. **2-full** (charter intent — "Optional Gateways submenu").
  2. **Cross-cutting handlers stay in `mesh_networks` section** (no per-handler `menu_section` churn). The `mesh_networks` section is internally re-purposed to back the "Optional Gateways" submenu, so failover / load_balancer / mesh_alerts / classifier / automation / service_menu / messaging / mqtt / broker / nomadnet / aredn / favorites / amateur_radio all stay where they are. Only `meshcore.py` migrated.
  3. **Stay at 6 primary**: top-level slot #2 was repurposed in place — "Mesh Networks" → "MeshCore". The old contents are now a sub-submenu reachable via an "Optional Gateways →" entry inside the new MeshCore primary submenu. Net change to top-level item count: 0.
- **Files changed** (4):
  - `src/launcher_tui/handlers/meshcore.py` — `menu_section` flipped from `"mesh_networks"` → `"meshcore"`.
  - `src/launcher_tui/main.py` — slot #2 label + dispatch flipped to MeshCore; `_mesh_networks_menu()` renamed `_optional_gateways_menu()` (title "Optional Gateways", `meshcore` removed from `_ORDERING`); new `_meshcore_primary_menu()` builds from the `meshcore` section + adds an `optional_gateways` legacy item that calls into the renamed submenu.
  - `src/launcher_tui/handlers/dashboard.py` — `_REMEDIATION_HINTS` breadcrumbs updated from `"Mesh Networks > ..."` to `"MeshCore > Optional Gateways > ..."` for `rnsd`, `mqtt`, `bridge`, `identity` keys.
  - `tests/test_phase2_menu_restructure.py` (NEW, 7 tests) — guards: meshcore handler section, slot-2 label/dispatch, both submenu method names, optional_gateways linkage, and that meshcore is no longer auto-added to the Optional Gateways legacy block.
- **Gates green**: `python3 scripts/lint.py --all` exit 0; combined run of `test_phase2_menu_restructure.py + test_handler_registry.py + test_meshcore_handler.py + test_all_handlers_protocol.py + test_regression_guards.py` = **481 passed**, 0 failed.
- **Internal section name kept as `mesh_networks`**: chose not to rename the section key on disk because every existing `mesh_networks` handler would have to flip too, and the user-visible label is what actually matters. Documented in the new `_optional_gateways_menu` docstring.
- **Next session resume point**: PR open against `main`. Once it merges, mark Phase 2 ✅, then start Phase 3 (handler feature-flag audit — there are ~40 Meshtastic-leaning handlers in `mesh_networks`/`rns` whose menu_items currently surface unconditionally even when the relevant feature is disabled). Branch convention: `claude/mc-phase3-handler-flag-audit`. First step there is to enumerate every Meshtastic-leaning handler and decide which `feature_flag=` value should gate each `menu_items()` row.

**2026-05-03 (Phase 2 MERGED)**:
- PR #16 merged into main as merge commit `e0d4d326`. Single feature commit landed: `edd76042` (the implementation + tracker entry + smoke tests).
- Branch `claude/mc-phase2-menu-restructure` deleted both locally and on origin; main is clean and up to date.
- **Phase 3 readiness check** (relevant for the next session): no clearance needed. The handler-flag audit only reads `menu_items()` rows on the handlers and writes back `feature_flag=` values — no schema or section migrations are pending, no other branch is in flight against the same files, and the existing feature-flag plumbing (`_feature_enabled` + `feature_flags` dict on TUIContext + per-row `flag` argument in `BaseHandler.menu_items()`) is already wired end-to-end. The only open design question for Phase 3 is policy, not infrastructure: **opt-in or opt-out** when a handler row has no obvious flag (default to safe — keep visible — and only gate rows that genuinely require Meshtastic / RNS / Gateway).
- **Next session resume point**: branch `claude/mc-phase3-handler-flag-audit`. Step 1 = enumerate all `mesh_networks` + `rns` section handlers' `menu_items()` rows and tag each with the appropriate flag (`meshtastic`, `rns`, `gateway`, or `None` = always-visible). Step 2 = write the changes per-handler in one PR, paired with smoke tests asserting that under a MESHCORE-only profile the Optional Gateways submenu has zero `mesh_networks`-tagged Meshtastic items. Step 3 = run lint + the existing test suites.

**2026-05-03 (Phase 3 implementation — PR pending)**:
- User picked policy **(a) opt-in flagging only**: gate clear meshtastic / rns / gateway / mqtt rows; leave cross-radio rows (HAM, AREDN, Favorites, Service Menu) always-visible.
- **Handler audit matrix applied** — 12 handlers, 17 rows newly flagged:
  - `meshtastic`: `automation`, `classifier` (`traffic` tag).
  - `mqtt`: `broker` (`broker-menu` tag).
  - `gateway`: `dual_radio_failover`, `load_balancer`, `mesh_alerts`, `messaging` (semantically only routes Meshtastic + RNS today, so the `gateway` flag is the right gate even though the row is named "messaging").
  - `rns`: every row in `rns_config` (4) + `rns_diagnostics` (3) + `rns_interfaces` + `rns_monitor` + `rns_sniffer` (10 rows total in the rns section).
  - Already-correct handlers untouched: `radio_menu` (meshtastic), `mqtt`, `nomadnet` (rns), `rns_menu` (rns), `gateway` (gateway).
  - Always-visible (unflagged) confirmed: `amateur_radio` (`ham`), `aredn`, `favorites`, `service_menu` (`services`).
- **Files changed** (13): 12 handlers + new `tests/test_phase3_handler_flag_audit.py` (30 tests). Each handler change was a single-line `None` → flag-string flip on the third tuple slot.
- **Smoke-test design**: parametrized matrix asserts each `(tag, expected_flag)` pair within sections `mesh_networks`+`rns` (scoped to avoid the legitimate `traffic` tag collision with `traffic_inspector` in `maps_viz`); profile-level integration test builds a full HandlerRegistry under MESHCORE flags and asserts (1) zero leaked gated tags, (2) all four cross-radio tags still visible, (3) the entire `rns` section collapses to empty under MESHCORE, (4) FULL profile shows everything again.
- **Gates green**: `lint --all` exit 0; combined run of `test_phase3_handler_flag_audit.py + test_phase2_menu_restructure.py + test_handler_registry.py + test_meshcore_handler.py + test_all_handlers_protocol.py + test_regression_guards.py + test_handlers_dual_radio_failover.py + test_handlers_service_menu.py + test_handlers_gateway.py + test_nomadnet_handler.py` = **648 passed**.
- **Branched off main pre-PR-#17**, so the local tracker file may show a small Phase 2 row delta vs. what's on origin/main once #17 merges. Trivial conflict (this Phase 3 entry is appended below the Phase 2 entries, which is exactly where #17 added its own Phase 2 MERGED entry — so it's the same insertion site, easy resolve at merge time).
- **Next session resume point**: wait for the Phase 3 PR to merge, then **start Phase 4** (MeshCore radio config gap — presets/channels/TX power UI for MeshCore). Begin by adding a Phase 4 "Key contract findings" + "Implementation outline" section. Branch convention: `claude/mc-phase4-meshcore-config`. The Phase 4 work is a feature-add to the `meshcore.py` handler's submenu, not another menu restructure.

**2026-05-03 (Phase 4a implementation — PR pending)**:
- Branch `claude/mc-phase4a-radio-readonly` off main. Phase 4 row in the Status table stays `in flight` per the new lifecycle — Phase 5's prep PR will flip it to `merged ✅` once 4a + 4b ship.
- **Q1 answered**: meshcore_py exposes everything we need (full table in the Phase 4 section above). Path picked: TUI → daemon HTTP → meshcore_py reads. No daemon-side wire-protocol code needed.
- **Files changed** (4):
  - `src/gateway/meshcore_handler.py` (+187 lines, 1230 → 1420 — 80-line headroom under the 1500 cap; **flag for split before Phase 4b adds writes**): module-level `_empty_radio_state()` + `_coerce_int/_coerce_float` helpers; instance-level `self._radio_state` + lock initialised in `__init__`; new `_refresh_radio_state()` async coroutine that calls `commands.send_appstart()`/`send_device_query()`/`get_channel(i)` for each slot up to `max_channels`, with simulator + missing-meshcore_py fallbacks; `_set_radio_error()` stamps an error without losing the prior snapshot; public `get_radio_state(refresh=False)` accessor. Hooked into `_connect()` after subscriptions succeed (best-effort; failure logged but doesn't fail the connect).
  - `src/utils/config_api.py` (+43 lines, 1412 → 1455): `do_GET()` routes `/radio` paths early; new `_handle_radio_get()` mirrors the chat-endpoint pattern — pulls the active handler, honors `?refresh=1` / `?refresh=true`, returns `{"radio": <state>}` on success, 503 if no active handler, 500 on handler exception.
  - `src/launcher_tui/handlers/meshcore.py` (+164 lines, 840 → 1004): new `radio` menu item between `config` and `enable`; new `_meshcore_radio_status()` method renders Identity / LoRa Parameters / TX Power / Channels / Last refreshed blocks, with friendly error paths for daemon-unreachable / 503 / never-refreshed states; helpers `_radio_fetch_state()` (urllib client to GET /radio?refresh=1, 10s timeout), `_fmt_freq()` / `_fmt_bw()` formatters, `_radio_preset_name()` lookup table (4 common tuples mapped to friendly names; returns None when no match). Renamed the `# Chat` section header to `# Daemon HTTP API` since both `_meshcore_chat` and `_meshcore_radio_status` share the `:8081` base; `CHAT_API_BASE` constant kept as-is to avoid a cross-file rename in this PR.
  - `tests/test_phase4a_radio_readonly.py` (NEW, 25 tests): three test classes mirror the three layers (cache / HTTP / TUI) — empty-state shape, simulator population, error-stamping (preserves prior snapshot), 503-when-no-handler, refresh query-param threading, HTTPError handling, preset table coverage, formatter edge cases.
- **Gates green**: `python3 scripts/lint.py --all` exit 0; combined run of `test_phase4a_radio_readonly.py + test_meshcore_handler.py + test_meshcore_channel_metrics.py + test_config_api_chat.py + test_tui_meshcore_chat.py + test_all_handlers_protocol.py + test_phase{1,2,3}_*.py + test_regression_guards.py` = **588 passed**, 0 failed.
- **Constraint flag for next session**: `meshcore_handler.py` is now 1420 lines. Phase 4b adds three setter wrappers + three PUT endpoints + three TUI methods + validation. **Plan a split** before Phase 4b coding — likely extract the radio-state code (RadioState dataclass, refresh, get_radio_state, set_radio_*) into `src/gateway/meshcore_radio_config.py` and have MeshCoreHandler hold a reference. ~120 lines moves out, restoring headroom and keeping the daemon module focused on connection/messaging concerns.
- **Next session resume point**: review/merge this PR, then start Phase 4b (writes). First step of 4b = read this tracker entry's "constraint flag" + extract `meshcore_radio_config.py` before adding setters. Branch convention: `claude/mc-phase4b-radio-writes`. Validation needs a region-aware TX-power cap table (e.g. EU868 = 14 dBm EIRP, US915 = 30 dBm) — pull from a published source, not hand-curated, and link in the PR.

**2026-05-03 (Phase 3 MERGED + Phase 4 prep — PR pending)**:
- PR #18 merged into main as merge commit `09b97cfa` after force-push rebase resolved the trivial tracker conflict from PR #17 landing first. Branch `claude/mc-phase3-handler-flag-audit` deleted both locally and on origin.
- **Bottleneck addressed**: user explicitly flagged that the per-phase post-merge tracker bumps (PR #14, PR #17) were creating administrative overhead and PR collisions. This prep PR fixes the root cause:
  - **Phase Status table simplified**: dropped the "Last touched" column; collapsed status states to three only (`merged ✅` / `in flight` / `not started`); added a lifecycle note above the table explaining that the next phase's prep PR flips the prior row to `merged`. The `implementation — PR pending` intermediate state is gone — it was the duplicate-state-on-disk pattern that caused mid-PR tracker conflicts.
  - **No more standalone tracker-bump PRs.** Each phase = one implementation PR. The status flip happens organically during the next phase's prep.
- **Phase 4 prep section added** with Goal + Key contract findings + four open questions + a Phase-4a-then-Phase-4b implementation outline. Critical finding: **MeshCore's radio config UI does not exist in MeshAnchor today** — the existing MeshCore submenu has 8 items, all connection / lifecycle / messaging, zero LoRa parameters. Users currently leave the TUI for `meshcore_set_channel.py` or Node-Connect web UI.
- **Load-bearing open question for Phase 4**: does meshcore_py expose `commands.set_channel` / `set_preset` / `set_tx_power`? Need to install meshcore_py locally (or read upstream source) before writing the Phase 4a implementation PR. If yes → simple TUI → daemon HTTP shim → meshcore_py path. If no → daemon needs to reimplement the wire-level config protocol.
- **Strong recommendation for Phase 4 scope**: split into Phase 4a (read-only display of current preset / channels / TX power) and Phase 4b (writes with input validation + double-confirm). Lower regression surface, easier review, lets us discover the meshcore_py API gap before committing to a write path.
- **Files changed in this prep PR** (1): `.claude/plans/tui_rework_tracker.md` only.
- **Next session resume point**: review/merge this prep PR, then start Phase 4a implementation. Step 1 of Phase 4a = answer Q1 (install meshcore_py, inspect `commands.*` for radio-config methods). Steps 2-5 are written out in the Phase 4 section above. If Q1's answer is "no", revise the Phase 4a outline before coding — the daemon-side wire-protocol path is materially more work than the meshcore_py-shim path.

| Date | Decision | Rationale |
|---|---|---|
| 2026-05-03 | Meshtastic handlers gated behind feature flag, not deleted | Preserves `gateway`/`full` profile capability. Reversible. |
| 2026-05-03 | Phase-by-phase PRs to `main` (no long-lived feature branch) | Each PR is small, internally complete, gated by Issue #29 regression suite. |
| 2026-05-03 | Map continues serving on `:5000` — only data source flips | No client-visible URL change; map UI stays compatible. |
| 2026-05-03 | `:8808` (meshforge-maps) is external — Phase 6 plugin scaffold | Not a MeshAnchor port today. |
| 2026-05-03 | MeshCore positions deferred to Phase 1.5 | meshcore_py advertisements don't carry GPS today; surface MeshCore via position-less side panel for now. |
| 2026-05-03 | Phase 2 picked Option 2-full (MeshCore primary at slot #2; Optional Gateways nested) | Charter explicitly calls for "Optional Gateways submenu". 2-light was a stepping stone; 2-full delivers the demoted/promoted structure the charter wants. |
| 2026-05-03 | Phase 2 keeps internal section key `mesh_networks` (only label changes to "Optional Gateways") | Avoids touching ~15 handlers' `menu_section` attribute when only one (`meshcore`) actually needed to move. Reversible. |
| 2026-05-03 | Phase 2 stays at 6 primary slots (no menu growth) | Slot #2 repurposed in place; no item added or dropped from the top-level menu. Keeps within the soft UX cap. |
| 2026-05-03 | Phase 3 chose opt-in flagging (Option a) | User explicit choice. Lower risk + lower line count than opt-out. The user-visible win (MESHCORE profile drops Meshtastic/RNS/Gateway rows) comes from gating the obvious 17 rows; chasing every cross-cutting one would over-gate handlers like Favorites and Service Menu that are useful regardless of profile. |
| 2026-05-03 | Phase 3 gates `messaging` behind `gateway` flag despite no exact-fit flag | The current `messaging` menu only offers Meshtastic and RNS as transports. Under MESHCORE all three of those are False, so the menu is half-broken there. `gateway` is the only flag whose truth value matches "is there a non-MeshCore radio to route through". Reversible if a finer-grained flag is added later. |
| 2026-05-03 | Phase Status table simplified; no more standalone tracker-bump PRs | User explicit feedback: per-phase post-merge bumps (PR #14, PR #17) created admin overhead and tracker conflicts (PR #18 hit `CONFLICTING / DIRTY` because of this). Root cause was duplicate state on disk (`implementation — PR pending`) that needed a separate PR to flip post-merge. New rule: status states are `merged ✅` / `in flight` / `not started` only, and the next phase's prep PR flips the prior row. `git log` / `gh pr view` is authoritative for "did this merge". |
| 2026-05-03 | Phase 4 split into 4a (read-only) and 4b (writes) | Lower regression surface. Phase 4a discovers the meshcore_py command surface for radio config (open question Q1) before committing to a write path. Misconfigured radio writes can brick a radio for a region — Phase 4b will need explicit confirmations and validation that 4a doesn't, so they're naturally separate PRs. |

---

## How to Resume This Work in a Fresh Session

1. Read this tracker (you're doing it).
2. Read the charter: `/home/wh6gxz/.claude/plans/tui-needs-to-be-groovy-charm.md`.
3. Check the in-flight phase row. If status is "in flight", continue from "Where We Left Off".
4. If a phase is marked complete and the next is "not started", start by extending this tracker with that phase's "Key contract findings" + "Implementation outline" sections (mirror Phase 1's structure).
5. Always update "Where We Left Off" at the end of the session — even if just one line.
