# Mesh Networks Parity Port from MeshForge

> **Created**: 2026-05-04
> **Closed**: 2026-05-05 — charter complete, all bug-fix-shaped items shipped; structural-refactor and new-feature pickups explicitly deferred (see "Charter close-out" below).
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
| MN-1a | merged ✅ | #38 | eb305ff2 |
| MN-1b | merged ✅ | #39 | 6be4c207 |
| MN-2  | merged ✅ | #36 | c5150136 |
| MN-3  | merged ✅ | #35 | 4df6b063 |
| MN-4a | merged ✅ | #40 | 224f10d2 |
| MN-4b | **deferred indefinitely** | — | — |
| MN-5  | merged ✅ | #37 | b37bb330 |

## Charter close-out (2026-05-05)

**Charter is complete.** Every bug-fix-shaped item identified across the audit has shipped (six PRs, #35 → #40). The two remaining "phases" — MN-1b's full meshtasticd config editor stack and MN-4b's NomadNet 4-mixin refactor — are explicitly deferred indefinitely, not pending.

### Why MN-4b is deferred (not "later")

After audit, MN-4b would deliver five items, none of them bug-fix-shaped:

| Item | Cost | Why skip |
|------|------|----------|
| Structural split of `nomadnet.py` | refactor | File at 1,511 / cap 1,500 — barely over. Cleanup-for-cleanup's-sake on working code. |
| Per-identity interactive launches | ~400 LOC | Niche use case (most operators run one NomadNet instance). MN-5's `_lxmf_utils` already handles collisions if they spin up two. |
| Issue #45 legacy hardening | ~600 LOC | Phase 8.4 shipped tmux variant which `install_noc.sh` now uses. Legacy path symptom hasn't surfaced. The 2026-04-24 deferred-backports memory specifically said "Defer unless the same symptom surfaces on MeshAnchor." |
| Inline config toggles | ~470 LOC | UX nicety. `_edit_nomadnet_config` already lets users edit the file. |
| User-match + iface checks | ~236 LOC | Defensive depth, no fix. Existing `_nomadnet_rns_checks.py` covers basics. |

The cherry-pick pattern that worked for the rest of the charter doesn't have an analog here — there's no MN-4b bug fix to extract because Issue #46 (the wrapper-bypass bug) was the only fixable bug in MeshAnchor's nomadnet code, and MN-4a shipped it.

### When to revisit

Reopen MN-4b only if:
- A specific Issue #45 symptom appears on MeshAnchor (TUI fighting systemd Restart=always on the legacy `nomadnet-user.service`).
- An operator actually requests per-identity launches.
- `nomadnet.py` grows materially past 1,500 LOC such that the file becomes hard to navigate (right now it's at 1,511; a few hundred lines more makes the case).

In any of those cases, the right scope is a focused MeshAnchor-flavored extraction (e.g., split `_view_nomadnet_logs` + `_view_nomadnet_config` + `_edit_nomadnet_config` + `_configure_propagation_node` into a sibling file) — NOT a wholesale import of MeshForge's mixin structure, which would conflict with Phase 8.4's tmux-service-ops mixin and lose MeshAnchor-specific behavior.

### Lesson learned (charter-wide)

Across the six phases, the same pattern showed up four times:

1. **MN-1**: original plan said port six handlers (~3,138 LOC). Audit revealed five of them depend on 2,616 LOC of `core/*` infrastructure that doesn't exist in MeshAnchor. Reframed to **MN-1a** (channel_config alone, 629 LOC standalone) + **MN-1b** (Path A: Meshtastic Quick-Look — three surgical fixes to existing `radio_menu.py` instead of importing 5,095 LOC of MeshForge code).
2. **MN-4**: original plan said split `nomadnet.py` into 4 mixins. Audit revealed MeshForge's mixins aren't a pure refactor — they add ~1,800 LOC of new functionality (per-identity launches, Issue #45 hardening, Issue #46 guard, inline toggles, iface checks). Reframed to **MN-4a** (Issue #46 wrapper-bypass guard alone, ~30 LOC of behavior change) + **MN-4b** (deferred indefinitely).

The pattern: **when porting from a sister project, the original plan that says "drop in N files" frequently understates because it doesn't account for divergence the sister project accumulated since the fork.** The audit-first habit caught this both times. The "defer indefinitely with a specific reopen condition" habit prevents deferred items from drifting back as zombies.

The cherry-pick rule worked everywhere there was a real bug to extract:
- **MN-3**: extracted RNS Tools (genuinely missing affordance).
- **MN-2**: extracted gateway preflight + RX test (real GATEWAY-profile validation gap).
- **MN-5**: extracted the LXMF exclusivity bug fix (port-37428-LISTEN false-positive on every launch).
- **MN-1b**: extracted the dead-end menu fixes + web-UI shortcut.
- **MN-4a**: extracted the wrapper-bypass bug fix (silent fall-through on missing pipx venv).

Where there was no bug to extract — just structural refactor or new features — the right answer was to defer, not to ship work for the sake of completing the original plan.

---

## MN-1 scope revision (2026-05-05)

The original MN-1 plan scoped six MeshForge handler files (~3,138 LOC). On audit before porting, only **one** file (`channel_config.py`, 629 LOC) was self-contained. The other five — `meshtasticd_config.py`, `meshtasticd_lora.py`, `meshtasticd_mqtt.py`, `meshtasticd_nodedb.py`, `meshtasticd_radio.py` — share a `read_overlay`/`write_overlay`/`OVERLAY_PATH` utility set baked into `meshtasticd_config.py`, and that file plus the others import infrastructure modules MeshAnchor does not ship:

| Required module | LOC in MeshForge |
|-----------------|------------------|
| `core/meshtasticd_config.py` (`MeshtasticdConfig`, `RADIO_TEMPLATES`, `RadioType`) | 513 |
| `core/meshtastic_cli.py` (`get_cli()` factory) | 514 |
| `core/meshtasticd_templates.py` (hardware template DB) | 996 |
| `utils/meshtastic_http.py` (`get_http_client()`) | 593 |
| **Infrastructure subtotal** | **2,616** |

Plus the original 5 handler files (~2,479 LOC) for a true MN-1 cost of ~5,095 LOC, not the original ~3,138.

The infrastructure also tightly couples MeshAnchor to a Meshtastic-primary worldview (auto-creates `/etc/meshtasticd/{available.d,config.d}`, manages config templates, wraps the meshtastic CLI as the radio's primary control surface). Phase 7 + Phase 8 cross-cutting decisions ("MeshCore is primary; Meshtastic is the optional gateway") and Issue #31 ("no silent persistent system changes on startup") both push back on this. A full MN-1b port would warrant its own scope discussion.

**Split decision:**

- **MN-1a** ships now: `channel_config.py` only. Pure `commands.meshtastic` API consumer (`ensure_connection`, `set_channel_name`, `set_channel_psk`, `_run_command`, `get_channel_info`) — all of which exist in MeshAnchor today. Section `configuration`, gated `feature_flag="meshtastic"`. Slot already wired in `main.py:_configuration_menu` `_ORDERING` as a legacy placeholder waiting for the handler — registry replaces it automatically.
- **MN-1b** deferred pending separate scope decision: the meshtasticd config editor stack + its 2,600 LOC of infrastructure. Open question for the user: is the value of MeshAnchor managing `/etc/meshtasticd` config worth the import-time coupling, or should operators continue editing meshtasticd config via its own web UI on `:9443` (Issue #21 / Issue #22) and via direct CLI?

## Decisions log

- 2026-05-04: Plan created at user request after Phase 8.4 charter completion. Scoped to "Mesh Networks parity" only — Config Doctor + composable bridges + extensions remain deferred per prior memory.
- 2026-05-05 (MN-1a): MN-1 split into MN-1a (channel_config alone, ships now) and MN-1b (full meshtasticd config stack, scope decision pending — see "MN-1 scope revision" section above). Reason: post-audit infrastructure dependency was 2,616 LOC larger than the original plan estimated, plus tight coupling to a Meshtastic-primary worldview that contradicts Phase 7/8 cross-cutting decisions.
- 2026-05-05 (MN-1b): user agreed with the Path A reframing — ship a small Meshtastic Quick-Look instead of the full 5,095-LOC port. After auditing the existing `RadioMenuHandler` (which already covers `--info` view, region/TX-power/name setters, send/position/reboot, channel info, CLI install), only three changes were actually needed: (1) replace the dead-end `presets` action (delegated to a `meshtasticd_radio` sub-handler that doesn't exist in MeshAnchor) with an inline preset picker driven by `utils.lora_presets.MESHTASTIC_PRESETS`; (2) replace the dead-end `hw-config` action with a help msgbox documenting the manual HAT-selection process and pointing at the meshtasticd web UI; (3) add a `webui` action that offers `xdg-open http://localhost:9443` when `$DISPLAY` is set or prints an SSH-tunnel hint when headless. Net change: ~150 LOC in `radio_menu.py` + 18 tests, no infrastructure adds. Replaces the original Path A's standalone "quicklook" handler with three surgical fixes to existing code — same delivered value, smaller change, fixes pre-existing dead-ends as a side effect.
- 2026-05-05 (MN-4a): same audit pattern as MN-1b. The original MN-4 scope ("split nomadnet.py into 4 mixins") understated the cost: MeshForge's mixins aren't a pure refactor — they add ~1,800 LOC of new functionality (per-identity interactive launches, Issue #45 legacy hardening, Issue #46 wrapper-bypass guard, inline config toggles, user-match + Meshtastic-iface checks). MeshAnchor's `nomadnet.py` is at 1,511 LOC (just over CLAUDE.md's 1,500 cap) but Phase 8.4 already shipped the tmux-variant mixin with its own user-scope systemctl handling; the legacy `nomadnet-user.service` path that MeshForge's `_nomadnet_service_ops.py` hardens hasn't shown the same symptom on MeshAnchor. The one genuinely fixable bug in MeshAnchor's current code is the silent wrapper-bypass — `_get_wrapper_command` returns a fallback list when the pipx venv is missing, skipping the wrapper's rpc_key precondition and producing a hard-to-diagnose AuthenticationError 30 seconds into NomadNet's lifetime. MN-4a ships the bug fix (`_get_wrapper_command` returns `Optional[list]`, callers show `_show_canonical_installer_msg` and bail) plus a small UX bonus: thread the MN-5 `config_dir` kwarg through `_ensure_lxmf_exclusive` so the new per-config-dir collision check actually fires. The structural split + the rest of MeshForge's new functionality stays deferred as MN-4b pending separate scope decision.
- 2026-05-04: NomadNet 4-mixin refactor MUST preserve Phase 8.4 tmux mixin (`_nomadnet_tmux_service_ops.py`).
- 2026-05-04: Meshtasticd editors keep `menu_section="configuration"` (matches MeshForge), gated `feature_flag="meshtastic"` so they vanish in MESHCORE profile.
- 2026-05-04: RNS Tools is additive — inline rns_menu items stay; rns_tools adds a deeper submenu.
- 2026-05-04 (MN-5): MeshChatX deltas categorized into three buckets: pure-bug-fix (full `_lxmf_utils.py` rewrite — port-37428-LISTEN false-positive replaced with /proc + config-dir matching), pure-doc (handful of clarifying docstring/comment lines), and intentional-divergence (section/flag, paths-helper usage, brand strings, "Repair RNS alignment" submenu). Only the first two were landed; the divergent items stay per prior Phase 8.2 decisions. The "Repair RNS alignment" feature requires `rns_alignment.py` + `ReticulumPaths.get_shared_rpc_key()` — both are part of the still-deferred "Per-box rpc_key guard" backport.
