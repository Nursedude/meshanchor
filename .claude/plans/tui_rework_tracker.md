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
| 4 | MeshCore radio config gap (presets/channels/TX power) | merged ✅ | 4a [PR #20](https://github.com/Nursedude/meshanchor/pull/20); 4b [PR #21](https://github.com/Nursedude/meshanchor/pull/21) (merge `fa41c5ce`) |
| 5 | Startup health flip (meshtasticd → optional) | merged ✅ | [PR #22](https://github.com/Nursedude/meshanchor/pull/22) (merge `55acf1f3`) |
| 6 | meshforge-maps :8808 plugin scaffold | merged ✅ | [PR #23](https://github.com/Nursedude/meshanchor/pull/23) (merge `b10074b6`) |
| 6.3 | meshforge-maps endpoint config (host/port/timeout) | in flight | `claude/mc-phase6.3-maps-config` |
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

## Phase 5 — Startup Health Flip (meshtasticd → optional)

**Goal**: A MESHCORE-only deployment shouldn't light up red just because `meshtasticd`/`rnsd` aren't running — they're Optional Gateways under that profile, not required core services. Make the startup banner profile-aware so MeshAnchor reports "Ready" when the components the active profile actually needs are healthy.

**Key contract findings (2026-05-04 audit)**:

- **`startup_health.py` already accepts `profile=None`** at line 343 — and `launcher.py:165` already calls it with the active profile. The plumbing is in place. Two bugs prevent it from working:
  1. Line 78-84: `is_ready` hardcodes "meshtasticd must be running" — independent of profile. So even when `overall_status='ready'`, `is_ready` still returns False under MESHCORE.
  2. Lines 369-378: services NOT in the profile's required/optional list are marked `optional=True` — but `optional_ok = all(s.running for s in optional services)` then DEMOTES status to "degraded" when those services aren't running. There's no way to say "this service is irrelevant for this profile, ignore it for the overall_status calculation."
- **`MESHCORE` profile has empty service lists**: `required_services=[]`, `optional_services=[]` (line 102-119 of `deployment_profiles.py`). MeshCore daemon runs in-process; there's no systemd service to check. So under MESHCORE, all three audited services (meshtasticd, rnsd, mosquitto) fall into the "not in either list" bucket — which is exactly the bug above.
- **Auto-detection works**: `load_or_detect_profile()` at line 349 returns the saved profile or auto-detects via running-services heuristic, defaulting to MESHCORE.
- **No `/health` HTTP endpoint exists on the daemon**. Prometheus exporter has one at a different port. Phase 5 adds `GET /health` to the `:8081` daemon, mirroring the `/radio` and `/chat/*` pattern from Phase 4.
- **Defer-list (legitimate gateway-side requirements when those features are active)**: `health_score.py:719` hardcodes meshtasticd/rnsd as critical for the alert engine; `active_health_probe.py:582-596` registers gateway probes without consulting profile; `service_menu.py:168-171` bridge preflight requires meshtasticd. All three only run under GATEWAY/FULL profiles where those services *are* required, so they don't fire under MESHCORE today. Document in the PR; defer to a follow-up.

**Implementation outline (this PR)**:

1. **Branch**: `claude/mc-phase5-health-flip`. **Done.**
2. **Pre-Phase-5 cleanup (opening commit)**: extract `_handle_radio_get` + `_handle_radio_put` into `src/utils/radio_api.py`. `config_api.py` was 1526 (over the 1500 cap); becomes 1440 + new `radio_api.py` (121 lines). Class methods on `ConfigAPIHandler` are thin delegators so test fixtures that drive `h._handle_radio_*` directly stay untouched. **Done in commit `8e44cb3a`.**
3. **Phase 5 implementation (feature commit)**:
   - **`startup_health.py`** — add `not_applicable: bool = False` to `ServiceHealth`. `run_health_check(profile)` now classifies each service as required / optional / not_applicable. `overall_status` is computed from `relevant = [s for s in summary.services if not s.not_applicable]`. `is_ready` becomes `overall_status in ("ready", "degraded")`. `print_health_summary` and `get_compact_status` render not_applicable services dim. `get_health_dict` exposes `profile_name`, per-service `not_applicable` + `fix_hint`.
   - **`utils/health_api.py`** (new, 57 lines) — `handle_get(handler)` resolves the active profile via `load_or_detect_profile()` (best-effort; falls back to `profile=None` on error) and returns `{"health": <dict>}`. 503 on import failure, 500 on `run_health_check` exception. Service degradation is encoded in the body, not the HTTP status.
   - **`config_api.py`** — wire `/health` route into `do_GET` next to `/chat/*` and `/radio`. New thin delegator `_handle_health_get`. Routed BEFORE the api null-check so `/health` works before `ConfigurationAPI` is initialized.
4. **Tests** (`tests/test_phase5_startup_health.py`, 18 tests across 4 classes):
   - `TestProfileAwareClassification` (6) — MESHCORE no services → ready; MESHCORE w/ stray meshtasticd running still ready (n/a flag preserved); FULL missing required rnsd → error; FULL missing optional meshtasticd → degraded; GATEWAY all-optional → degraded but is_ready=True; no profile → legacy behaviour (meshtasticd hardcoded required).
   - `TestIsReadyProperty` (4) — ready/degraded → True; error/unknown → False.
   - `TestHealthDictShape` (2) — dict carries `profile_name` and per-service `not_applicable` + `fix_hint`.
   - `TestHealthEndpoint` (5) — 200 envelope; 500 on run_health_check exception; profile-resolution failure falls back to `profile=None` (still serves a snapshot); `do_GET` dispatches `/health` and `/health?...`.
   - `TestHealthRoutingIsolation` (1) — `/health` works when `self.api is None` (the config-store API isn't initialized yet).

**Critical files (cross-reference)**:

- `src/utils/startup_health.py:78-84` — `is_ready` flip (was hardcoded meshtasticd)
- `src/utils/startup_health.py:343-407` — `run_health_check` not_applicable classification
- `src/utils/startup_health.py:487-512` — `get_health_dict` shape
- `src/utils/health_api.py` — new module, mirrors `radio_api.py` pattern
- `src/utils/config_api.py:1006-1030` — `do_GET` route registration
- `src/utils/deployment_profiles.py:100-195` — profile definitions (read but not touched)

**Definition of Done**:
- [x] `config_api.py` under 1500-line cap (currently 1451)
- [x] MESHCORE profile + no services running → `overall_status='ready'`, `is_ready=True`
- [x] FULL profile + missing required rnsd → `overall_status='error'`
- [x] No-profile path preserves legacy "meshtasticd is required" behaviour
- [x] `GET /health` returns `{"health": {...}}` with `profile_name`, services list, `is_ready`
- [x] `/health` works when `ConfigurationAPI` isn't initialized
- [x] Lint clean
- [x] All Phase 4a/4b/chat/regression suites still pass
- [ ] PR opened to main

**Deferred to a Phase 5.5 follow-up**:
- `health_score.py:719` hardcoded `critical=(name in ('meshtasticd','rnsd'))` — alert-engine refactor needed
- `active_health_probe.py:582-596` — `create_gateway_health_probe()` should consult the active profile, not just `noc.yaml`
- `service_menu.py:168-171` — bridge preflight should soften meshtasticd check when profile doesn't require it (rnsd remains required for bridge)

---

## Phase 6 — Meshforge-Maps :8808 Plugin Scaffold

**Goal**: MeshAnchor can *discover, surface, and link* the sister project [meshforge-maps](https://github.com/Nursedude/meshforge-maps) which runs its own HTTP server on :8808 (Leaflet UI + REST API). MeshAnchor does NOT take ownership of meshforge-maps' lifecycle — meshforge-maps owns its own systemd unit. Phase 6 is the minimum scaffold: a discovery client, a TUI menu entry, browser-launch. Deeper integration (data ingestion, embedded panels, lifecycle control) is left for follow-up phases.

**Key contract findings (2026-05-04)**:

- **meshforge-maps service surface**: HTTP on :8808 (configurable via `http_port` in its own `settings.json`). Endpoints we care about for discovery: `/api/status` (version + uptime), `/api/health` (composite 0–100 score), `/api/sources` (enabled data sources), `/api/config`. Companion WebSocket on :8809 for real-time push (out of scope for the scaffold).
- **No existing :8808 references in MeshAnchor**: `grep -rn ":8808\|8808" src/` is empty. Phase 6 introduces the convention. Ports already in use: `:5000` (MeshAnchor's own NOC map via `map_data_service.py`), `:9443` (meshtasticd web), `:8081` (config + chat + radio + health daemon API). `:8808` for meshforge-maps slots in cleanly.
- **Existing maps_viz section**: TUI handlers `ai_tools.py` and `topology.py` already register `menu_section = "maps_viz"`. The whole section is gated by the `maps` feature flag at the top-level menu (in `_run_main_menu()`), so per-row flags inside `maps_viz` aren't needed — adding the new handler with `menu_section="maps_viz"` and `feature_flag=None` per row matches the existing convention.
- **Plugin loader vs handler dispatch**: `src/utils/plugins.py` defines `BasePlugin` / `IntegrationPlugin` etc. for protocol-level integrations (see `meshing_around.py`). But TUI menus go through the *handler registry* (`src/launcher_tui/handler_registry.py`), NOT the plugin manager. The scaffold doesn't need a `BasePlugin` subclass — a TUI handler is sufficient for "show status, open browser." A `BasePlugin` would be appropriate later if we add lifecycle (start/stop/restart) or message hooks.
- **File-size cliff (defer)**: `src/utils/map_data_collector.py` is at **1529 lines**, just above the 1500 cap (was 1529 since well before Phase 6). Phase 6 doesn't touch it — the new code is a separate module — so a split is *not* a Phase 6 prerequisite. Flag for a future cleanup phase.

**Implementation outline (this PR)**:

1. **Branch**: `claude/mc-phase6-maps-plugin` off main (post PR #22).
2. **Tracker prep (this commit)**: flip Phase 5 row to `merged ✅`, add this Phase 6 section.
3. **`utils/meshforge_maps_client.py`** (new, 178 lines): `MeshforgeMapsClient(host=localhost, port=8808, timeout=3.0)` with a single `probe() -> MapsServiceStatus` method. Hits `/api/status` first; if that fails, returns `available=False` with a populated `error` string. Layers in `/api/health` and `/api/sources` best-effort. Never raises — connection refused, timeout, 404, malformed JSON all collapse into the structured result. Includes `_extract_source_names` helper tolerant of two payload shapes the upstream API has used (list of strings vs list of dicts with `enabled` flag).
4. **`launcher_tui/handlers/meshforge_maps.py`** (new, 137 lines): `MeshforgeMapsHandler` registered in `maps_viz` section. Two menu items: `mf_status` (probe + render `_format_status` block) and `mf_open` (`webbrowser.open(client.web_url)`). `_format_status` is a pure function — easy to unit-test. Available case shows URL / version / health / sources / uptime; unavailable case shows the error and a fix hint pointing at the install URL + `systemctl start meshforge-maps`.
5. **Registration**: append `MeshforgeMapsHandler` to `get_all_handlers()` in `handlers/__init__.py` (Batch 16).
6. **Tests** (`tests/test_phase6_meshforge_maps.py`, 34 tests across 5 classes):
   - `TestMeshforgeMapsClient` (12) — URL builder, defaults, probe-on-URLError / TimeoutError / OSError, full payload, partial endpoints, non-200, non-JSON, configured-timeout threading, uptime coercion (string → float), unparseable uptime → None.
   - `TestExtractSourceNames` (7) — list-of-strings, list-of-dicts (enabled true/false/missing), missing key, non-list, mixed shapes.
   - `TestMeshforgeMapsHandler` (6) — handler metadata, menu_items shape, dispatch for both actions, unknown-action no-op, `webbrowser.open` called with correct URL.
   - `TestFormatStatus` (4) — unavailable + install hint, unavailable with no error string, available with full data, available with partial data (no `None` strings).
   - `TestFormatUptime` (4) — seconds / minutes / hours / days.
   - `TestHandlerRegistration` (1) — `MeshforgeMapsHandler` is in `get_all_handlers()`.

**Critical files (cross-reference)**:

- `src/utils/meshforge_maps_client.py` — new, discovery client
- `src/launcher_tui/handlers/meshforge_maps.py` — new, TUI handler
- `src/launcher_tui/handlers/__init__.py:189-192` — Batch 16 registration
- `tests/test_phase6_meshforge_maps.py` — new, 34 tests

**Definition of Done**:
- [x] Branch `claude/mc-phase6-maps-plugin` created
- [x] Discovery client never raises on offline / timeout / non-JSON
- [x] TUI handler renders both available + unavailable cases cleanly
- [x] Handler registered in `get_all_handlers()`
- [x] 34 Phase 6 tests pass
- [x] Full suite pass (3034 passed; the 2 pre-existing dev-box flakes that also fail on main aren't Phase 6 regressions)
- [x] Lint clean
- [ ] PR opened to main

**Deferred to follow-up phases** (Phase 6 scaffold intentionally minimal):
- **Phase 6.1 — bidirectional handshake**: meshforge-maps can read MeshAnchor's `/api/nodes/geojson`; consider whether MeshAnchor should return the favor for cross-source data fusion.
- **Phase 6.2 — lifecycle control**: convert the handler into an `IntegrationPlugin` so MeshAnchor can `start` / `stop` / `restart` meshforge-maps' systemd unit (with the explicit-action guardrails from Issue #31 — no silent persistent system changes).
- **Phase 6.3 — config schema**: read `host` / `port` from MeshAnchor's settings rather than hardcoding `localhost:8808`. Useful when meshforge-maps runs on a different host on the LAN.
- **map_data_collector split**: 1529 → under 1500 by extracting per-source collectors. Independent of Phase 6 work.

---

## Phase 6.3 — Meshforge-Maps Endpoint Config

**Goal**: Take the Phase 6 hardcoded `localhost:8808` and turn it into a per-deployment config (`host` / `port` / `timeout`) backed by `SettingsManager("meshforge_maps")`. Useful when meshforge-maps runs on a different host on the LAN — common for users who want their NOC TUI on a Pi while the maps server runs on a beefier mini-PC. Defaults match Phase 6 exactly so existing localhost installs keep working without writing a settings file.

**Key contract findings (2026-05-04)**:

- **`SettingsManager` is the existing well-trodden path**: `propagation.py:47`, `mesh_alerts`, `automation`, `gateway`, `logging`, etc. all use `SettingsManager("<name>", defaults={...})` from `utils.common`. Same pattern fits cleanly here — `SettingsManager("meshforge_maps", defaults={"host": "localhost", "port": 8808, "timeout": 3.0})`. Saves to `~/.config/meshanchor/meshforge_maps.json`.
- **Validation lives in the new module, not `MeshforgeMapsClient`**: Phase 6's client already accepts `host`, `port`, `timeout` constructor args — no changes needed there. Validation belongs in the config layer because the client is *probe-and-collapse-on-error* by design (it'll happily try to reach `bad host!` and just return `available=False`). Catching invalid input at the TUI prompt is the right UX moment to show a fix hint.
- **Two independent failure modes**: (a) bad on-disk values shouldn't lock the user out of the TUI — the load path falls back to defaults per-field with a logged warning; (b) bad TUI input from the user should *not* persist — the save path raises `MapsConfigError` and the handler shows a msgbox so the user can correct it. Mirrors the same split that `service_check` uses (return-value-encodes-failure on the read path, raise-on-the-write-path).
- **`TUIContext.validate_hostname` / `validate_port` exist** (`handler_protocol.py:88,97`) but they're booleans-only — they don't carry an error message for the msgbox. The new `MapsConfigError` instances *do* carry a fix hint string, so the dialog flow gets useful "Port must be in range 1-65535" text instead of a generic "Invalid Port" with no context.
- **Frozen dataclass for `MapsConfig`**: handler code holds the config briefly across an `_configure_endpoint` loop iteration. Frozen avoids accidental in-place mutation; rebuild is cheap (just a `load_maps_config()` round-trip after a save). Matches the pattern used by Phase 4b's `RegionBand` namedtuple.
- **Phase 6 test that asserted exactly two menu items has to update**: `test_phase6_meshforge_maps.py::test_menu_items` hard-coded `keys == ["mf_status", "mf_open"]`. Phase 6.3 adds `"mf_endpoint"`, so the assertion flips to "first two are unchanged + endpoint is present". Per saved feedback (PR #22 CI canary), running the module's *own* canonical test before pushing is what catches this — adjacent suite coverage misses it.
- **Test isolation**: every test in `test_phase6_3_maps_config.py` uses an `isolated_config_dir` fixture that monkeypatches `utils.common.CONFIG_DIR` to `tmp_path`. SettingsManager's `__init__` reads CONFIG_DIR at construction time, so the patch propagates without further wiring. Prevents the user's real `~/.config/meshanchor/meshforge_maps.json` from leaking into the test outcome.
- **No file-size cliff this phase**: `meshforge_maps_config.py` is the new ~210-line module; `meshforge_maps.py` grows from 137 → 220 (still well under 1500); `test_phase6_3_maps_config.py` is the new 480-line test. `map_data_collector.py` still at 1529 — Phase 6.3 doesn't touch it.

**Implementation outline (this PR)**:

1. **Branch**: `claude/mc-phase6.3-maps-config` off main (post PR #23).
2. **Tracker prep (this commit)**: flip Phase 6 row to `merged ✅`, add this Phase 6.3 section.
3. **`utils/meshforge_maps_config.py`** (new, ~210 lines): `MapsConfig` frozen dataclass (host, port, timeout) + `MapsConfigError` exception + `load_maps_config()` / `save_maps_config()` / `reset_maps_config()` API. Validation helpers `_validate_host` / `_validate_port` / `_validate_timeout` raise `MapsConfigError` with human-readable hints; `_safe_*` coercion helpers swallow invalid on-disk values back to defaults with a logged warning. `MapsConfig.build_client()` returns a fully-configured `MeshforgeMapsClient`.
4. **`launcher_tui/handlers/meshforge_maps.py`**: add a third menu row `mf_endpoint` ("Maps Endpoint — Configure host/port"). `_client()` flips from hardcoded constants to `load_maps_config().build_client()`. New `_configure_endpoint()` method opens a sub-menu (Host / Port / Timeout / Reset / Back) and `_prompt_host` / `_prompt_port` / `_prompt_timeout` validators show a msgbox on `MapsConfigError` instead of writing junk.
5. **No registration change**: Batch 16 in `handlers/__init__.py` already imports `MeshforgeMapsHandler`. Adding rows to `menu_items()` doesn't require a re-register.
6. **Tests** (`tests/test_phase6_3_maps_config.py`, 49 tests across 7 classes):
   - `TestDefaults` (2) — module defaults equal Phase 6 hardcoded values; load returns defaults when no settings file.
   - `TestSaveAndLoad` (5) — full save round-trip, partial update, no-arg noop, on-disk JSON shape, reset.
   - `TestLoadFromCorruptOrInvalid` (4) — corrupt JSON / bad port / bad host / bad timeout each fall back to defaults per-field.
   - `TestValidation` (13) — empty/invalid-char/leading-dash/overlong host; zero/negative/oversize/non-int port; zero/negative/oversize timeout; IPv4 + IPv6 acceptance; failed save doesn't corrupt prior good config.
   - `TestMapsConfig` (4) — dataclass validate(), build_client(), frozenness.
   - `TestHandlerUsesSettings` (3) — `_client()` defaults / picks up override / rebuilds each call (no caching).
   - `TestMenuItems` (4) — endpoint row present, no per-row flag, Phase 6 keys preserved, dispatch wires to `_configure_endpoint`.
   - `TestConfigureEndpointDialog` (11) — back exits cleanly; host/port/timeout each persist; invalid host/port/timeout each show a msgbox and *don't* persist; reset confirm/abort; blank input keeps current; status format renders overridden URL.
   - `TestPhase6BackwardCompat` (2) — `_open_browser` opens `localhost:8808` by default, opens overridden URL after save.

**Critical files (cross-reference)**:

- `src/utils/meshforge_maps_config.py` — new, persisted endpoint config + validation
- `src/launcher_tui/handlers/meshforge_maps.py:43-55` — Phase 6.3 menu row + dispatch
- `src/launcher_tui/handlers/meshforge_maps.py:64-69` — `_client()` reads from settings
- `src/launcher_tui/handlers/meshforge_maps.py:107-180` — `_configure_endpoint` + `_prompt_*`
- `tests/test_phase6_3_maps_config.py` — new, 49 tests
- `tests/test_phase6_meshforge_maps.py:284-292` — Phase 6 menu test relaxed for the third row

**Definition of Done**:
- [x] Branch `claude/mc-phase6.3-maps-config` created
- [x] `MapsConfig` + `load_maps_config` / `save_maps_config` / `reset_maps_config` shipped
- [x] Validation rejects bad input on the write path; falls back to defaults on the read path
- [x] Handler `_client()` reads from settings; `mf_endpoint` menu row + sub-menu shipped
- [x] 49 Phase 6.3 tests pass
- [x] Existing Phase 6 + adjacent suites still pass (513 passed combined)
- [x] Full suite passes (3083 passed; the 2 dev-box flakes documented in Phase 5/6 entries are pre-existing and not Phase 6.3 regressions)
- [x] Lint clean
- [ ] PR opened to main

**Deferred to follow-up phases** (still on the menu after 6.3):
- **Phase 6.1 — bidirectional handshake** (data fusion).
- **Phase 6.2 — lifecycle control** (start/stop/restart of meshforge-maps' systemd unit, with Issue #31 guardrails).
- **Phase 5.5 — health code cleanup** (`health_score` / `active_health_probe` / `service_menu` deferrals).
- **map_data_collector split** (1529 → under 1500).

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

**2026-05-03 (Phase 4a MERGED + Phase 4b implementation — PR pending)**:
- PR #20 merged into main as merge commit `2cd7772d`. Phase 4a = read-only `/radio` endpoint + TUI display; cold-start checklist for 4b moved to `project_meshcore_primary_rework.md`.
- **Branch**: `claude/mc-phase4b-radio-writes` off main. Phase 4 row in the Status table stays `in flight` until Phase 5's prep flips it (4 = 4a + 4b, per the Status table lifecycle note).
- **Commit 1 — split** (`d6666cf6`, no behavior change): extracted radio-state code from `meshcore_handler.py` (1420 → 1280 lines, 140 lines of headroom recovered) into new `src/gateway/meshcore_radio_config.py` (211 lines). `MeshCoreHandler` now holds `self._radio = MeshCoreRadioConfig(self)`; `_refresh_radio_state` / `_set_radio_error` / `get_radio_state` are thin delegates. `_empty_radio_state` / `_coerce_*` re-exported from `meshcore_handler` so the Phase 4a test idiom (`from gateway.meshcore_handler import _empty_radio_state` + `patch("gateway.meshcore_handler._HAS_MESHCORE", False)`) keeps working — verified: 25/25 phase 4a tests + 39/39 meshcore_handler/channel_metrics tests + 17/17 regression guards green.
  - **Cycle break**: `meshcore_radio_config` lazy-imports `meshcore_handler` inside `refresh()` / `_await_ok()` so the `_HAS_MESHCORE` patch stays as the single source of truth instead of duplicating `safe_import('meshcore')`. Simulator detection uses duck-type (`type(meshcore).__name__ == "MeshCoreSimulator"`) for the same reason.
- **Commit 2 — writes**: added 4 layers in one commit (per checklist):
  1. `meshcore_radio_config.py` (211 → 502 lines): `RadioWriteError`, `RegionBand` namedtuple + 4-row `REGION_BANDS` table sourced from ETSI EN 300 220 (EU433 = 10 dBm, EU868 = 14 dBm), KCC (KR920 = 14 dBm), and FCC Part 15.247(b)(3) (US915 = 30 dBm). `region_for_freq()` picks the narrowest band on overlap so KR920 (1.4 MHz) wins over US915 (26 MHz) at 921 MHz. `validate_lora_params()` enforces SX1262 PLL range + supported BW set + SF 5-12 + CR 5-8. `validate_tx_power()` takes the lower of region cap and radio max. `derive_channel_secret()` implements meshcore_py's `sha256(name)[:16]` rule; `parse_channel_secret()` accepts hex (with optional whitespace) or auto-derives for `#`-prefixed names. Setter wrappers: `set_lora()` / `set_tx_power()` / `set_channel()` — each validates → calls meshcore_py command → awaits OK Event → refreshes the cache (via `await self.refresh()`) so the next read reflects the write.
  2. `meshcore_handler.py` (1280 → 1312 lines): synchronous setter delegates `set_radio_lora()` / `set_radio_tx_power()` / `set_radio_channel()` plus the `_run_radio_write()` bridge that runs the coroutine inline (no loop) or schedules on `self._loop` (running daemon). Bounded by 10s timeout.
  3. `config_api.py` (1455 → 1526 lines): `do_PUT()` routes `/radio/*` to new `_handle_radio_put()` *before* the api-null check so radio writes don't depend on the config-store API being initialized. Routes: `PUT /radio/lora` (body `{freq, bw, sf, cr}`), `PUT /radio/tx_power` (body `{value}`), `PUT /radio/channel/<idx>` (body `{name, secret?}`). `RadioWriteError` → 400, other exceptions → 500, unknown `/radio/*` → 404, non-object body → 400. Localhost gate inherited from `do_PUT` via `_check_localhost()`.
  4. `launcher_tui/handlers/meshcore.py` (1004 → 1371 lines): existing "Radio Config" item now opens a sub-submenu (`_meshcore_radio_menu`) with `View / Set LoRa Params / Set TX Power / Set Channel Slot / Back`. Each setter prompts for new value(s) (current shown as default), surfaces a region-cap warning derived from the cached freq, and double-confirms via two `dialog.yesno(default_no=True)` calls before issuing the PUT. No auto-write on Enter — wrong frequency or excessive TX power can violate licence terms or brick a radio for a region. New `_radio_put()` HTTP client mirrors `_radio_fetch_state` shape (`{ok, status, error}` / `{ok, radio}`).
- **New tests** (`tests/test_phase4b_radio_writes.py`, 63 tests across 8 classes): region table + LoRa validation + TX-power validation + channel secret derivation/parsing + channel name validation + setter wrappers (with `_HAS_MESHCORE`/`_meshcore_mod` patched + AsyncMock'd commands) + HTTP PUT dispatch (12 cases including 503/400/404/500 + body parsing + the `do_PUT` routing seam) + TUI double-confirm (each setter aborts on first NO, aborts on second NO, only writes after both YES) + region warning helpers.
- **Gates green**: `python3 scripts/lint.py --all` exit 0; combined run of `test_phase4b_radio_writes.py + test_phase4a_radio_readonly.py + test_meshcore_handler.py + test_config_api_chat.py + test_all_handlers_protocol.py + test_regression_guards.py` = **542 passed**, 0 failed; adjacent `test_meshcore_channel_metrics.py + test_tui_meshcore_chat.py + test_phase1_handlers.py + test_phase2_menu_restructure.py + test_phase3_handler_flag_audit.py` = 109 passed.
- **File size watch**: `meshcore_handler.py` is back at 1312 lines (post-split + setter delegates), `meshcore_radio_config.py` at 502, `config_api.py` at 1526 (above 1500 cap — flag for next session), `launcher_tui/handlers/meshcore.py` at 1371 (within cap, getting close).
- **Next session resume point**: review/merge the 4b PR. Once merged, **Phase 5** (startup health flip — meshtasticd dependency made optional) starts. Phase 5's prep PR flips this Phase 4 row to `merged ✅` per the no-standalone-bumps lifecycle. Branch convention: `claude/mc-phase5-health-flip`. **Pre-Phase-5 cleanup candidate**: `config_api.py` at 1526 lines is over the 1500-line cap — a small split (move chat handlers to `chat_api.py` or move radio handlers to `radio_api.py`) would restore headroom before Phase 5 adds a startup health endpoint.

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

**2026-05-04 (Phase 4b MERGED + Phase 5 implementation — PR pending)**:
- PR #21 merged into main as merge commit `fa41c5ce` at 2026-05-04T05:03Z. Phase 4b shipped radio writes (LoRa / TX power / channels) + region-aware validation. Phase Status table now shows Phase 4 as `merged ✅` per the no-standalone-bumps lifecycle.
- **Branch**: `claude/mc-phase5-health-flip` off main.
- **Commit 1 — radio_api split** (`8e44cb3a`, no behaviour change): `config_api.py` was at 1526 (over the 1500 cap from PR #21). Extract `_handle_radio_get` + `_handle_radio_put` into new `src/utils/radio_api.py` (121 lines). `ConfigAPIHandler._handle_radio_*` methods become thin delegators so `tests/test_phase4{a,b}_radio_*.py`'s direct method-driving fixtures stay untouched. config_api.py: 1526 → 1440. 98 radio + chat tests + 17 regression guards green.
- **Commit 2 — health flip** (`c1ae4498`): three-layer change.
  1. `startup_health.py` (580 → 614): `ServiceHealth.not_applicable` field added; `run_health_check(profile)` classifies each service required/optional/not_applicable; `overall_status` ignores not_applicable services; `is_ready` flips from "is meshtasticd running?" to `overall_status in ("ready", "degraded")`. `print_health_summary` and `get_compact_status` render N/A services dim. `get_health_dict` exposes `profile_name` + per-service `not_applicable` + `fix_hint`.
  2. `utils/health_api.py` (new, 57 lines): `handle_get(handler)` — auto-resolves the active profile via `load_or_detect_profile()` (best-effort; falls back to `profile=None` on error) and returns `{"health": <dict>}`. 503 on import failure, 500 on `run_health_check` exception.
  3. `config_api.py` (1440 → 1451): wire `GET /health` route into `do_GET`, route ABOVE the api null-check so it works before `ConfigurationAPI` is initialized. New thin delegator `_handle_health_get`.
- **Tests**: `tests/test_phase5_startup_health.py` (NEW, 18 tests across 4 classes — see Phase 5 section above for the full breakdown). 647 tests passed across phase4a/4b/chat/regression/phases 1-3/all-handlers/meshcore_handler suites.
- **File-size watch**: `config_api.py` 1451 (under cap, +11 from health route + delegator), `startup_health.py` 614, new `health_api.py` 57, `radio_api.py` 121.
- **Backward compat preserved**: passing `profile=None` to `run_health_check` keeps the legacy "meshtasticd treated as required" behaviour. Confirmed by `test_no_profile_falls_back_to_legacy_behaviour`. Existing callers that haven't migrated to passing a profile still see the old semantics.
- **Deferred to Phase 5.5** (legitimate when those features are active under GATEWAY/FULL): `health_score.py:719` hardcoded critical=meshtasticd/rnsd; `active_health_probe.py:582-596` doesn't consult profile; `service_menu.py:168-171` bridge preflight requires meshtasticd. None of these fire under MESHCORE today, so deferring doesn't reintroduce the user-visible bug.
- **Next session resume point**: review/merge the Phase 5 PR. Once merged, **Phase 6** (meshforge-maps :8808 plugin scaffold) starts. Phase 6's prep PR flips this Phase 5 row to `merged ✅` per the no-standalone-bumps lifecycle. Branch convention: `claude/mc-phase6-maps-plugin`. Phase 5.5 cleanup (health_score / active_health_probe / service_menu deferrals) is independently mergeable and can slot in either before or after Phase 6 — they aren't gating each other.

**2026-05-04 (Phase 5 MERGED + Phase 6 implementation — PR pending)**:
- PR #22 merged into main as merge commit `55acf1f3` at 2026-05-04T06:05Z. Phase 5 shipped the `not_applicable` ServiceHealth state, `is_ready` derives from `overall_status`, profile-aware `run_health_check`, and the new `GET /health` endpoint. CI surfaced one regression mid-PR (legacy `test_is_ready_when_meshtasticd_running` in `tests/test_startup_health.py:38`); fixed in commit `f577dfdf` by adding a legacy fallback in `is_ready` for hand-built HealthSummaries with `overall_status='unknown'`. Lesson saved to memory: run the module's own canonical test file before pushing, not just adjacent suites.
- **Branch**: `claude/mc-phase6-maps-plugin` off main. Phase 5 row flipped to `merged ✅` per the no-standalone-bumps lifecycle.
- **Audit findings**: meshforge-maps owns its own HTTP server on :8808 with `/api/status`, `/api/health`, `/api/sources`, `/api/config`. No existing :8808 references in MeshAnchor — Phase 6 introduces the convention. The `maps_viz` section is already gated by the `maps` feature flag at the top-level menu, so the new handler inherits that gating without per-row `feature_flag=` values. Plugin loader (`src/utils/plugins.py` `BasePlugin`) is for protocol-level integrations; TUI menus go through the handler registry, so a `BasePlugin` subclass is NOT needed for the scaffold (just a `BaseHandler`). `map_data_collector.py` is at 1529 lines (over the 1500 cap from before Phase 6 — flag for a future cleanup phase, not a Phase 6 prerequisite since the new code lives in separate modules).
- **Files changed** (4):
  - `src/utils/meshforge_maps_client.py` (NEW, 178): `MeshforgeMapsClient` + `MapsServiceStatus` dataclass + `_extract_source_names` helper. Single-shot `probe()` that never raises — URLError / TimeoutError / OSError / non-200 / non-JSON all collapse into `available=False` with a populated `error` string. `/api/health` and `/api/sources` are best-effort (partial responses still produce a usable status). Coercion helpers tolerate string-or-int field types from upstream.
  - `src/launcher_tui/handlers/meshforge_maps.py` (NEW, 137): `MeshforgeMapsHandler` registered in `maps_viz`. Two menu rows: "Meshforge Maps Status" (probe + render) and "Open Maps Browser" (webbrowser.open). Pure-function `_format_status` and `_format_uptime` for easy unit testing. Unavailable case shows the install URL + `systemctl start meshforge-maps` hint.
  - `src/launcher_tui/handlers/__init__.py` (+3): Batch 16 registration of `MeshforgeMapsHandler`.
  - `tests/test_phase6_meshforge_maps.py` (NEW, 34 tests across 5 classes — see Phase 6 section above for the full breakdown).
- **Gates green**: `python3 scripts/lint.py --all` exit 0; full suite `pytest tests/ --ignore=tests/test_bridge_integration.py` = 3034 passed (was 2998 pre-Phase-6, +34 Phase 6 + 2 collection delta). Two failures on this dev box (test_gateway_integration, test_status_bar) are pre-existing flakes that also fail on main without Phase 6 — confirmed via `git stash` + re-run. CI runs in a clean env and won't see them.
- **File-size watch**: nothing new approaches the 1500 cap. `map_data_collector.py` at 1529 stays as a flagged-but-not-touched file — a future cleanup phase will split it. Phase 6's two new files are 178 + 137 lines, comfortable.
- **Next session resume point**: review/merge the Phase 6 PR. Once merged, options for the next phase: (a) Phase 6.1 — bidirectional handshake / data fusion with meshforge-maps; (b) Phase 6.2 — lifecycle control (start/stop/restart of meshforge-maps' systemd unit, observing Issue #31 no-silent-changes rule); (c) Phase 6.3 — config schema for non-localhost meshforge-maps deployments; (d) Phase 5.5 — the deferred health code cleanup (health_score / active_health_probe / service_menu); (e) `map_data_collector` split. Pick whichever the user prioritizes.

**2026-05-04 (Phase 6 MERGED + Phase 6.3 implementation — PR pending)**:
- PR #23 merged into main as merge commit `b10074b6`. Phase 6 scaffold (discovery client + TUI handler for meshforge-maps :8808) shipped. Phase Status table now shows Phase 6 as `merged ✅` per the no-standalone-bumps lifecycle.
- **Branch**: `claude/mc-phase6.3-maps-config` off main. User picked Phase 6.3 (endpoint config schema) over the other follow-up options.
- **Files changed** (4):
  - `src/utils/meshforge_maps_config.py` (NEW, 211 lines): `MapsConfig` frozen dataclass + `MapsConfigError` + `load_maps_config()` / `save_maps_config()` / `reset_maps_config()`. `_validate_*` raise on the write path; `_safe_*` swallow on the read path back to defaults. `MapsConfig.build_client()` returns a configured `MeshforgeMapsClient`.
  - `src/launcher_tui/handlers/meshforge_maps.py` (137 → 220): new `mf_endpoint` menu row + `_configure_endpoint` sub-menu with Host / Port / Timeout / Reset / Back. `_prompt_host` / `_prompt_port` / `_prompt_timeout` validate via `save_maps_config` and surface `MapsConfigError` in a msgbox. `_client()` reads from `load_maps_config()` instead of using `DEFAULT_HOST/PORT` constants.
  - `tests/test_phase6_3_maps_config.py` (NEW, 484 lines, 49 tests across 7 classes — see Phase 6.3 section above for the full breakdown).
  - `tests/test_phase6_meshforge_maps.py` (1 line): `test_menu_items` relaxed to assert `keys[:2] == ["mf_status", "mf_open"]` + `"mf_endpoint" in keys`. Per the saved feedback from Phase 5: run the module's own canonical test before pushing — full-suite caught it before commit, would have CI'd otherwise.
- **Gates green**: `python3 scripts/lint.py --all` exit 0; combined Phase 6 + Phase 6.3 + handler registry + all-handlers protocol + regression guards = 513 passed; full suite `pytest tests/ --ignore=tests/test_bridge_integration.py` = **3083 passed** (was 3034 pre-Phase-6.3, +49 new). Same two pre-existing dev-box flakes (`test_gateway_integration::test_bridge_starts_in_degraded_mode`, `test_status_bar::test_no_node_count_by_default`) are documented in the Phase 5/6 entries — confirmed not Phase 6.3 regressions.
- **File-size watch**: nothing in this PR approaches the 1500-line cap. New `meshforge_maps_config.py` is 211; `meshforge_maps.py` grew 137 → 220; `test_phase6_3_maps_config.py` is 484. `map_data_collector.py` still at 1529 — Phase 6.3 doesn't touch it.
- **Next session resume point**: review/merge this Phase 6.3 PR. Once merged, the remaining follow-ups are (a) Phase 6.1 — bidirectional handshake / data fusion; (b) Phase 6.2 — lifecycle control (start/stop/restart of meshforge-maps' systemd unit, with Issue #31 guardrails); (c) Phase 5.5 — health code cleanup (health_score / active_health_probe / service_menu deferrals); (d) `map_data_collector` split (1529 → under 1500). Phase 7 (profile defaults + docs) is the natural next "main-line" phase if no follow-up is prioritized.

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
| 2026-05-04 | Phase 5 introduces a `not_applicable` ServiceHealth state instead of overloading `optional` | The original `run_health_check(profile)` already had two states (`optional=True/False`) but a service that's irrelevant for the active profile is fundamentally different from one that's relevant-but-optional. Conflating them caused MESHCORE-only deployments to render "degraded" because mosquitto/meshtasticd/rnsd weren't in MESHCORE's required or optional lists, but were still gating `optional_ok=all(running)`. A third state cleanly separates "report it but don't gate health" from "report it and warn if missing". |
| 2026-05-04 | Phase 5 keeps backward compat for `run_health_check(profile=None)` | Legacy callers (anything that hasn't migrated to passing a profile) still see the old "meshtasticd is required" semantics. Avoids forcing a flag day across the codebase — callers can migrate one at a time. Verified by `test_no_profile_falls_back_to_legacy_behaviour`. |
| 2026-05-04 | Phase 5 defers `health_score.py` / `active_health_probe.py` / `service_menu.py` to a Phase 5.5 follow-up | Those three call sites only fire under GATEWAY/FULL profiles where the relevant services *are* required, so they don't reintroduce the user-visible MESHCORE-red bug. Keeping the PR scoped to the user-facing startup banner + the new `/health` endpoint means smaller diff, easier review, and the deferred cleanup can land independently. |
| 2026-05-04 | `/health` route handler lives in a separate `utils/health_api.py` module | Mirrors the `radio_api.py` pattern from PR #21's prep split. Keeps `config_api.py` under the 1500-line cap and isolates the deployment-profile-aware logic from the generic config-store API surface. Same routing-before-api-null-check pattern means `/health` works during daemon startup before `ConfigurationAPI` is wired up. |
| 2026-05-04 | Phase 6 scaffold uses a `BaseHandler`, not a `BasePlugin` subclass | TUI menus dispatch through the handler registry (`launcher_tui/handler_registry.py`), not the plugin manager (`utils/plugins.py`). `BasePlugin` is the right shape when a plugin needs lifecycle hooks (start/stop, message hooks). For "show status, open browser," a handler is sufficient and matches the existing `maps_viz` peers (`ai_tools.py`, `topology.py`). Promote to `IntegrationPlugin` later if Phase 6.2 (lifecycle control) lands. |
| 2026-05-04 | Phase 6 client never raises; failures collapse into `MapsServiceStatus(available=False, error=...)` | Cleaner than try/except gymnastics at every call site. The TUI handler renders the `error` string as a fix hint without needing to know which underlying exception fired. Mirrors the pattern from `service_check.py` where reachability is encoded in the return value, not the control flow. |
| 2026-05-04 | Phase 6 handler doesn't take a per-row `feature_flag=` value | The whole `maps_viz` section is gated by the `maps` feature flag at the top level (`_run_main_menu()`). Per-row gating inside that section would be redundant. Matches the convention used by `ai_tools.py` and `topology.py`. If a profile ever needs MeshAnchor maps but NOT meshforge-maps, introduce a finer-grained `meshforge_maps` flag — but until then, section-level gating is the right scope. |
| 2026-05-04 | Phase 6 doesn't split `map_data_collector.py` (1529 lines, over cap) | The file's been over the 1500 cap since well before Phase 6 — this is not a Phase 6 regression and Phase 6's new code lives in separate modules, so no mechanical reason to bundle the split into this PR. A future cleanup phase will extract per-source collectors. Keeping Phase 6 scoped tight makes review easier and lets the split land independently when it's the right priority. |
| 2026-05-04 | Phase 6.3 splits validation across two paths (raise on write, swallow on read) | Two failure modes need different UX. Bad TUI input should surface a fix hint via msgbox so the user can correct it — that's `MapsConfigError`. Bad on-disk values shouldn't lock the user out of the TUI at all — the load path falls back to defaults per-field with a logged warning. Mirrors the `service_check.py` split (return-value-encodes-failure on read; raise-on-write). |
| 2026-05-04 | Phase 6.3 introduces `MapsConfigError` instead of reusing `TUIContext.validate_hostname` / `validate_port` | The TUIContext validators are booleans-only — they don't carry an error message for the msgbox. `MapsConfigError` instances carry a human-readable hint ("port must be in range 1-65535") that the dialog can render directly. Reusing the booleans would force the handler to re-stringify generic "Invalid X" messages without the actual constraint, which is exactly the "actionable error message" rule from `persistent_issues.md`. |
| 2026-05-04 | Phase 6.3 defaults match Phase 6 hardcoded values exactly (`localhost:8808`, timeout 3.0) | Non-breaking is the load-bearing guarantee — every existing localhost deployment must keep working without writing a settings file. Tests assert the equality directly so a future drift can't sneak through. |
| 2026-05-04 | Phase 6.3 hard-caps probe timeout at 60 seconds | A TUI menu blocking on a 60-second probe already feels broken; anything bigger is almost certainly user typo. The cap is configurable via the source if a future use case demands it, but the default validation is opinionated. |
| 2026-05-04 | Phase 6.3 `MapsConfig` is frozen | Handler holds the config briefly across the `_configure_endpoint` loop. Frozen avoids accidental in-place mutation; rebuild is cheap. Matches Phase 4b's `RegionBand` namedtuple. |
| 2026-05-04 | Phase 6.3 `_client()` rebuilds on every call (no caching) | Settings can change mid-session via the new "Configure Endpoint" menu. A cached client would render stale URLs in `_show_status` after a save. The rebuild is a single SettingsManager load — cheap. |
| 2026-05-04 | Phase 6.3 updates the Phase 6 menu-items test rather than working around it | `test_phase6_meshforge_maps.py::test_menu_items` was the canonical test for the module and asserted exactly two rows. Phase 6.3 adds a third — the right answer is to update the canonical test, not to add the row in a way that hides from it. Per the saved feedback memory: pre-push, run the module's own test file. |

---

## How to Resume This Work in a Fresh Session

1. Read this tracker (you're doing it).
2. Read the charter: `/home/wh6gxz/.claude/plans/tui-needs-to-be-groovy-charm.md`.
3. Check the in-flight phase row. If status is "in flight", continue from "Where We Left Off".
4. If a phase is marked complete and the next is "not started", start by extending this tracker with that phase's "Key contract findings" + "Implementation outline" sections (mirror Phase 1's structure).
5. Always update "Where We Left Off" at the end of the session — even if just one line.
