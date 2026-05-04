# Mesh Networks Parity Port from MeshForge

> **Created**: 2026-05-04
> **Goal**: Bring Optional Gateways (Meshtastic / RNS / AREDN) and RNS-client lineup to MeshForge parity by **porting existing handlers**, not rewriting.
> **Branch convention**: `claude/mn-parityN-<topic>` → PR per phase to `main`.
> **Origin**: User has surfaced this gap multiple times; previous "deferred backports" memory deferred several items, but the user is now explicitly asking. This plan picks them up.

---

## Why now

The MeshCore-primary TUI rework (charters 1–7 + 8) finished on 2026-05-04 (PRs #15–#34). Both charters left the **Optional Gateways** submenu (formerly "Mesh Networks") functionally thinner than MeshForge's equivalent. The code already exists in `/opt/meshforge/src/launcher_tui/handlers/` — this plan ports it, with surgical adjustments for MeshAnchor's MeshCore-primary divergence.

The user's stated principle: **don't recreate, port**.

---

## Scope

### IN — five phases

| Phase | Topic | Section | LOC | Files |
|-------|-------|---------|-----|-------|
| MN-1  | Meshtasticd config editors | configuration | ~3,138 | `meshtasticd_{config,lora,mqtt,nodedb,radio}.py` + `channel_config.py` |
| MN-2  | Gateway field-test handlers | mesh_networks | ~1,051 | `gateway_preflight.py` + `_gateway_preflight_template.py` + `test_gateway_rx.py` |
| MN-3  | RNS Tools handler | rns | 191 | `rns_tools.py` |
| MN-4  | NomadNet 4-mixin refactor | mesh_networks | ~split | `_nomadnet_{config_ops,iface_checks,io_ops,service_ops,submenus}.py` + slim `nomadnet.py` |
| MN-5  | MeshChatX + LXMF utility deltas | rns | ~480 diff | `meshchatx.py` (82) + `_meshchatx_service_ops.py` (228) + `_lxmf_utils.py` (173) |

### OUT — separate charter, do NOT pull in

- **Config Doctor** (`config_doctor.py` + `_config_doctor_checks.py`, 1,328 LOC, 43 tests). Cross-cutting audit, not Mesh-Networks-scoped.
- **Composable bridges** (HIGH effort, MeshCore-primary divergence; would require re-architecting the meshcore_bridge default in `gateway/config.py:453`).
- **Extensions framework / Meshing Around** (`extensions.py`).
- **Per-box rpc_key guard** (config-time concern, not menu-scoped).
- **AI Tools coverage / diagnostics / mfmaps / tilecache extras** (`_ai_tools_*.py` × 4).
- **Issue #49 perf prereqs** (gzip / chunked render — defer until perf shows).
- **Loop-detection token** (only when meshforge-maps starts sourcing MeshAnchor).

---

## Phase MN-1 — Meshtasticd Config Editors

### Files to copy verbatim from MeshForge HEAD

```
/opt/meshforge/src/launcher_tui/handlers/meshtasticd_config.py    (734 LOC)
/opt/meshforge/src/launcher_tui/handlers/meshtasticd_lora.py      (484 LOC)
/opt/meshforge/src/launcher_tui/handlers/meshtasticd_mqtt.py      (366 LOC)
/opt/meshforge/src/launcher_tui/handlers/meshtasticd_nodedb.py    (375 LOC)
/opt/meshforge/src/launcher_tui/handlers/meshtasticd_radio.py     (550 LOC)
/opt/meshforge/src/launcher_tui/handlers/channel_config.py        (629 LOC)
```

### Adjustments after copy

1. **Section gating**: All six declare `menu_section = "configuration"` in MeshForge. **Add** `feature_flag = "meshtastic"` (verify each file — at least `meshtasticd_config.py` already has it). Without this they'll appear in MeshAnchor's Configuration menu under MESHCORE profile, which contradicts Phase 5/7.
2. **Issue #22 audit**: meshtasticd ships `config.yaml` and the editors must not overwrite it. Grep ported files for `config.yaml` writes — only HAT selection (copy from `available.d/` to `config.d/`) is permitted. If a file writes the root config.yaml, refuse + add a regression test.
3. **Connection contract**: any `TCPInterface()` use must go through the connection manager (Issue #17). Grep ported files for `TCPInterface(` — if found, route via `MeshtasticConnection` context manager and add to `ALLOWLISTED` in `TestTCPConnectionContract` only if absolutely required.
4. **Brand strings**: `s/MeshForge/MeshAnchor/g` on user-facing strings only (not on data paths — `~/.config/meshforge/` is intentional cross-app shared config in some places; spot-check rather than blanket-replace).

### Tests to port

- `tests/test_handlers_meshtasticd_lora.py` exists in MeshForge — port verbatim.
- For the other four meshtasticd_* files, MeshForge has no dedicated tests. Add smoke tests: handler instantiates, `menu_items()` returns expected tags, dispatch routes don't raise on `--dry-run` paths.
- Run `tests/test_phase8_3_section_invariants.py` after port to confirm nothing leaks across sections.

### Risks

- Editors call `meshtastic --set lora.modem_preset` (CLI). On MeshAnchor under MESHCORE profile, `meshtastic` CLI may not be installed. The `feature_flag` guard prevents access; verify the handlers degrade gracefully if invoked anyway (e.g., from a script).

---

## Phase MN-2 — Gateway Field-Test Handlers

### Files to copy verbatim

```
/opt/meshforge/src/launcher_tui/handlers/gateway_preflight.py            (348 LOC)
/opt/meshforge/src/launcher_tui/handlers/_gateway_preflight_template.py  (377 LOC)
/opt/meshforge/src/launcher_tui/handlers/test_gateway_rx.py              (326 LOC)
```

Both declare `menu_section = "mesh_networks"`, `feature_flag = "gateway"`. Drop into MeshAnchor under the existing Optional Gateways menu.

### Composable-bridges divergence

MeshForge's preflight assumes `bridge_mode` enum across {meshcore, meshtastic, rns, mqtt}. MeshAnchor's `gateway/config.py:453` defaults to `bridge_mode = "meshcore_bridge"`. **Required surgical adjustments:**

1. Read `gateway_preflight.py` for any `bridge_mode in (...)` checks — replace with MeshAnchor-equivalent values.
2. Confirm `_gateway_preflight_template.py` doesn't hard-code MeshForge defaults. If it does, parameterize via `GatewayConfig`.
3. RX test (`test_gateway_rx.py`) sends a probe message. On MeshAnchor it should default to **MeshCore** (not Meshtastic) as the primary radio. Probably needs a `--via meshcore|meshtastic|rns` flag.

### Tests

- `tests/test_gateway_preflight.py` exists in MeshForge — port verbatim, then update for MeshCore-primary defaults.

---

## Phase MN-3 — RNS Tools Handler

### File to copy verbatim

```
/opt/meshforge/src/launcher_tui/handlers/rns_tools.py  (191 LOC)
```

Declares `menu_section = "rns"`. Adds a **Tools** entry to the RNS submenu with: rnstatus / rnpath -t / hash lookup / gateway hash / probe with round-trip.

### Decision: keep both inline + tools

MeshAnchor's `rns_menu.py` already inlines status / paths / probe / identity / nodes. The MeshForge `rns_tools.py` provides a richer, dedicated submenu. **Keep both** — inline items remain (one-click access from RNS submenu top), `rns_tools` adds a deeper "Tools" subpage. Minimal-diff approach.

### Risk

Adding `rns_tools` will register two entries with overlapping function (e.g., both have "probe"). Phase MN-3 must verify the RNS submenu's `_RNS_ORDERING` list still produces sensible results; if not, add an "advanced" prefix to `rns_tools` rows or relabel.

---

## Phase MN-4 — NomadNet 4-Mixin Refactor

### Files to add

```
/opt/meshforge/src/launcher_tui/handlers/_nomadnet_config_ops.py
/opt/meshforge/src/launcher_tui/handlers/_nomadnet_iface_checks.py
/opt/meshforge/src/launcher_tui/handlers/_nomadnet_io_ops.py
/opt/meshforge/src/launcher_tui/handlers/_nomadnet_service_ops.py
/opt/meshforge/src/launcher_tui/handlers/_nomadnet_submenus.py
```

And **replace** `nomadnet.py` with the slimmed MeshForge version that composes these mixins.

### Critical: preserve Phase 8.4 tmux mixin

MeshAnchor shipped `_nomadnet_tmux_service_ops.py` in Phase 8.4 (PR #34). MeshForge's 4-mixin refactor does NOT include this file. **MN-4 must merge tmux mixin into the new MRO**, not overwrite. The slimmed `nomadnet.py` MRO becomes:

```python
class NomadNetHandler(
    BaseHandler,
    _NomadNetConfigOps,
    _NomadNetIfaceChecks,
    _NomadNetIoOps,
    _NomadNetServiceOps,
    _NomadNetSubmenus,
    _NomadNetTmuxServiceOps,  # MeshAnchor-only addition (Phase 8.4)
):
    ...
```

### MeshForge Issue #45 fix to inherit

The 4-mixin refactor introduces user-scope `systemctl --user` and refuses `pkill` when systemd is managing the unit. This fixes the "TUI fighting systemd Restart=always" footgun. Phase 8.4's tmux mixin should already be compatible (it's user-scope), but confirm.

### Tests

- `tests/test_nomadnet_handler.py` exists in MeshForge — port.
- Add coverage for the MRO interaction with `_nomadnet_tmux_service_ops.py` (Phase 8.4 tests still pass).

---

## Phase MN-5 — MeshChatX + LXMF Utility Deltas

### Diffs to land

| File | Diff lines |
|------|------------|
| `meshchatx.py` | 82 |
| `_meshchatx_service_ops.py` | 228 |
| `_lxmf_utils.py` | 173 |

Inspect each diff first — these are likely upstream MeshChatX bug fixes / stability work that landed in MeshForge after Phase 8.2's port (PR #32, 2026-05-04).

### Procedure

1. `diff /opt/meshanchor/src/launcher_tui/handlers/meshchatx.py /opt/meshforge/src/launcher_tui/handlers/meshchatx.py` — categorize each hunk: brand-rename / bugfix / feature / divergence-on-purpose.
2. Apply bug-fix and stability hunks; skip brand renames where MeshAnchor's intentional divergence is correct (e.g., `~/.config/meshanchor/` paths).
3. Same for `_meshchatx_service_ops.py` and `_lxmf_utils.py`.

### Tests

Port matching test deltas from `tests/test_meshchatx*.py` + `tests/test_lxmf_*.py`.

---

## Cross-cutting Constraints (apply to every phase)

1. **Section invariants**: `tests/test_phase8_3_section_invariants.py` is the wall — meshcore-section files cannot import meshtastic/rns code, rns-section files cannot call MeshCore APIs, etc. Run after every PR.
2. **Test isolation lessons** (Phase 5/5.5/6.1/8.4):
   - Pin `_active_profile` in any test that calls `create_gateway_health_probe` / `run_health_check`.
   - Patch `_unit_dest` / `_wrapper_dest` / `_env_dest` directly — `HOME` monkeypatching does NOT isolate (`get_real_user_home()` consults `LOGNAME` first).
   - Pass `meshforge_maps_enabled=False` to any `MapDataCollector(...)` constructed in tests.
3. **Pre-push** (per memory `feedback_test_coverage_when_modifying_module.md`): run the module's own canonical test file, not just adjacent suites. PR #22 CI failure was the canary.
4. **Pre-commit hook**: lint + regression guards run automatically. If a hook fails, fix the underlying issue and create a NEW commit — never `--amend` post-failure.
5. **CI invocation**: `pytest tests/ --ignore=tests/test_bridge_integration.py`. Two pre-existing flakes are OK.
6. **MF013**: any new SQLite path needs a `DBSpec` entry in `utils.db_inventory`. Run `python3 scripts/db_audit.py` after the port.
7. **Regression rules** (CLAUDE.md): no `Path.home()`, no `shell=True`, no bare `except`, no `time.sleep` in daemon loops, no `RNS.Reticulum()` without `configdir=`, no raw `systemctl` for state decisions. The lint rules MF001/MF002/MF003/MF004/MF007/MF008/MF009/MF010 enforce these; expect noise on the first push.

---

## Phasing decision

**Recommend MN-3 (RNS Tools) → MN-2 (Gateway field-test) → MN-1 (Meshtasticd editors) → MN-5 (MeshChatX deltas) → MN-4 (NomadNet refactor).**

Rationale:
- MN-3 is the smallest (191 LOC, single file, clear scope) — proves the porting workflow.
- MN-2 is the next-most-isolated and adds the highest field-validation value (preflight + RX test for the user's GATEWAY profile).
- MN-1 is the bulk LOC but six independent files — can be sub-phased MN-1a/b if needed.
- MN-5 is small but riskier (merges into already-shipped Phase 8.2 code).
- MN-4 last because it's a refactor on a file that Phase 8.4 just touched (most disruptive).

Each phase = one PR. No long-lived branches. Tracker file (this file) updated at the end of each phase to flip status.

---

## Status table

| Phase | Status | PR | Merge commit |
|-------|--------|-----|--------------|
| MN-1  | not started | — | — |
| MN-2  | not started | — | — |
| MN-3  | in flight (PR open) | — | — |
| MN-4  | not started | — | — |
| MN-5  | not started | — | — |

---

## Decisions log

- 2026-05-04: Plan created at user request after Phase 8.4 charter completion. Scoped to "Mesh Networks parity" only — Config Doctor + composable bridges + extensions remain deferred per prior memory.
- 2026-05-04: NomadNet 4-mixin refactor MUST preserve Phase 8.4 tmux mixin (`_nomadnet_tmux_service_ops.py`).
- 2026-05-04: Meshtasticd editors keep `menu_section="configuration"` (matches MeshForge), gated `feature_flag="meshtastic"` so they vanish in MESHCORE profile.
- 2026-05-04: RNS Tools is additive — inline rns_menu items stay; rns_tools adds a deeper submenu.
