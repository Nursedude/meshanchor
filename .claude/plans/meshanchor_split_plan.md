# Branch Analysis & Repository Split Plan

> **Context**: WH6GXZ is reconsidering the "MeshCore stays in MeshAnchor" decision. Instead of merging alpha back into main, the proposal is to fork alpha into a **new standalone app/repo** where MeshCore is the primary radio and Meshtastic becomes a gateway plugin — the mirror image of main's architecture.
>
> **Decisions Made**:
> - **Name**: MeshAnchor (`Nursedude/meshanchor`)
> - **Shared code**: Fork and diverge — cherry-pick critical fixes as needed
> - **Timing**: Field test alpha first, then create MeshAnchor repo once 3-way routing is validated on real hardware

---

## 1. Current State of Each Branch

### Main (v0.5.4-beta) — Meshtastic-Primary NOC
- **Field-tested**: TUI, radio config, RNS, NomadNet, MQTT bridge
- **2,607 tests** across 71 files
- **MeshCore**: Optional handler via `safe_import()`, not primary
- **Recent work**: Meshtastic 2.7.x upgrade, schema validation, 3,457 lines dead code cleanup, security audit
- **Structure**: Flat `src/utils/`, `src/gateway/`, `src/launcher_tui/`

### Alpha (v0.6.0-alpha) — MeshCore-Elevated, Refactored
- **139 commits ahead**, 18 behind main (branched at PR #1000)
- **NOT field-tested**
- **RadioMode abstraction**: `MESHTASTIC | MESHCORE | DUAL` enum with persistence
- **Structural refactoring**: `src/core/rf/` (13 modules), `src/core/services/` (10 modules), `src/core/diagnostics/` (12 modules), `src/mapping/` (12 modules)
- **Plugin system**: Standard `IntegrationPlugin` with auto-discovery + event bus
- **Viewer mode**: TUI works without sudo by default
- **3-way routing**: Meshtastic ↔ MeshCore ↔ RNS in `message_routing.py`
- **316 files changed**, +61,015 / -40,141 lines vs main

---

## 2. The Gateway Inversion — Why This Split Works

The key insight is that alpha's `RadioMode` abstraction already models this split perfectly:

| | **Main (MeshAnchor)** | **New App (alpha fork)** |
|---|---|---|
| Primary radio | Meshtastic (meshtasticd) | MeshCore (meshcore_py) |
| RadioMode default | `MESHTASTIC` | `MESHCORE` |
| Secondary/gateway | MeshCore as optional handler | Meshtastic as gateway plugin |
| Bridge direction | meshanchor → meshcore (outbound) | newapp → meshtastic (outbound) |
| RNS integration | Core (gateway bridge) | Core (gateway bridge) |

**The architecture is symmetric**: Both apps are NOCs that bridge mesh networks. They just differ in which radio is "home" and which is "foreign."

```
┌──────────────┐         ┌──────────────┐         ┌──────────────┐
│  Meshtastic  │◄───────►│  MeshAnchor   │◄───────►│     RNS      │
│   (Primary)  │         │   (main)     │         │  (Primary)   │
└──────────────┘         └──────┬───────┘         └──────────────┘
                                │
                          gateway/plugin
                                │
                         ┌──────▼───────┐
                         │  New App     │
                         │  (alpha)     │
                         └──────┬───────┘
                                │
                    ┌───────────┴───────────┐
                    ▼                       ▼
             ┌──────────────┐       ┌──────────────┐
             │  MeshCore    │       │     RNS      │
             │  (Primary)   │       │  (Primary)   │
             └──────────────┘       └──────────────┘
```

---

## 3. Shared Code Strategy

### The 70% Problem
Both apps need: RF tools, service management, diagnostic engine, paths, validation, knowledge base, TUI framework, gateway infrastructure (~3,750+ LOC of utils alone).

### Selected: Fork-and-Diverge

**Why fork works here:**
- Alpha already has 139 commits of refactoring — it's already structurally different
- The shared utils are **stable infrastructure** (rf.py, paths.py, service_check.py) — they don't change often
- Both apps will diverge in focus: main toward Meshtastic ecosystem, MeshAnchor toward MeshCore ecosystem
- If a critical bugfix is needed in shared code, cherry-pick between repos (rare)

**Future option**: If the projects stabilize and shared code drift becomes painful, extract `meshanchor-common` as a package later. Don't over-engineer now.

---

## 4. What Alpha Already Has for Standalone

Alpha is remarkably well-positioned to become a standalone app:

| Feature | Status | Notes |
|---|---|---|
| RadioMode abstraction | Done | Just flip default to `MESHCORE` |
| Plugin system | Done | Meshtastic becomes an `IntegrationPlugin` |
| Modular src/core/ structure | Done | Clean separation from main's flat layout |
| Viewer mode (no sudo) | Done | Production-ready privilege model |
| 3-way routing | Done | CanonicalMessage handles all protocols |
| Deployment profiles | Done | `meshcore` profile already exists |
| MeshCore TUI handlers | Done | Config, status, diagnostics |
| MeshCore gateway handler | Done | Async with reconnection, dual-path |

---

## 5. Changes Needed for Alpha → Standalone App

### Must Do
1. **Flip RadioMode default**: `DEFAULT_MODE = RadioMode.MESHCORE` in `src/core/radio_mode.py`
2. **Rebrand**: Package name, version file, TUI title, log prefixes
3. **Adjust deployment profiles**: Make `meshcore` the default profile, rename `meshtasticd` profiles
4. **Cherry-pick main's 18 missing commits**: Especially Meshtastic 2.7.x upgrade, timeout module, security fixes
5. **New repo setup**: LICENSE, README, CI/CD, PyPI config
6. **Meshtastic as optional**: Gate meshtasticd features behind `safe_import()` (reverse of current pattern)

### Nice to Have
- Shared branding assets (logo family)
- Cross-repo issue linking
- Coordinated release schedule

---

## 6. What Main Keeps / Changes

### Stays the Same
- Meshtastic as primary radio
- All current features (MQTT, RNS bridge, RF tools, monitoring, TUI)
- MeshCore as optional handler (current behavior)
- Deployment profiles, service management

### Changes (Eventually)
- Once new app is stable, MeshCore handler on main can optionally connect **through** the new app as a gateway (meshcore ↔ new-app ↔ meshanchor ↔ meshtastic)
- Or keep direct MeshCore handler for simple setups
- Document the two-app ecosystem

---

## 7. Naming Suggestions for the Sister App

### Forge Family (sibling metaphor)
| Name | Rationale |
|---|---|
| **CoreForge** | Direct: MeshCore + Forge family. Clear sister relationship |
| **MeshCore Forge** | Explicit: it's MeshAnchor but for MeshCore |
| **RadioForge** | Broader: any radio protocol, MeshCore primary |

### Craft/Build Family
| Name | Rationale |
|---|---|
| **MeshCraft** | Craft = forge sibling. Implies hands-on building |
| **CoreCraft** | MeshCore + craft. Distinctive |
| **NetCraft** | Network + craft (note: NetCraft is a known company — avoid) |

### Hawaiian / Aloha Theme
| Name | Rationale |
|---|---|
| **MeshMana** | Mana = spiritual power in Hawaiian. "Power of the mesh" |
| **CoreMana** | MeshCore + mana |
| **MeshKai** | Kai = ocean/sea in Hawaiian. Waves = radio waves |
| **MeshOhana** | Ohana = family. "The mesh family" (sister project) |

### Technical / Radio Theme
| Name | Rationale |
|---|---|
| **MeshPulse** | Pulse = heartbeat of the mesh. MeshCore's always-on nature |
| **CoreLink** | Direct: MeshCore + link. What it does — links networks |
| **MeshAnchor** | Anchor = the stable core radio. Nautical/Hawaiian fit |
| **MeshNexus** | Nexus = connection point. The bridge between meshes |

### Selected: MeshAnchor
**MeshAnchor** — The MeshCore radio is the "anchor" of the network. Hawaiian/nautical connection. The anchor metaphor works on multiple levels:
- MeshCore is the stable, always-on anchor radio
- Nautical theme fits Hawaiian callsign WH6GXZ
- "Anchor" implies reliability — the thing you trust when conditions get rough
- Clear sister branding: MeshAnchor (builds the mesh) + MeshAnchor (holds it steady)

---

## 8. Risks and Mitigations

| Risk | Severity | Mitigation |
|---|---|---|
| **Shared code drift** | Medium | Cherry-pick critical fixes quarterly. Extract `meshanchor-common` if drift exceeds 20% |
| **CanonicalMessage divergence** | High | Keep protocol conversion methods identical. Shared test vectors |
| **User confusion** | Medium | Clear README/docs explaining when to use which app |
| **Maintenance burden (2 repos)** | Medium | Both share TUI framework, handler pattern — skills transfer. One developer (WH6GXZ) owns both |
| **MeshCore library instability** | Low | Already gated behind safe_import, simulator available |
| **Alpha not field-tested** | High | Field test MeshCore gateway BEFORE creating new repo. Don't ship untested code to a new repo |

---

## 9. Practical Next Steps

### Phase 1: Field Test Alpha (Before Split) — DO THIS FIRST
- [ ] Deploy alpha to test hardware with real MeshCore radios
- [ ] Validate 3-way routing (MeshCore ↔ MeshAnchor ↔ Meshtastic)
- [ ] Cherry-pick main's 18 commits onto alpha (Meshtastic 2.7.x, security, timeouts)
- [ ] Run full test suite on merged alpha
- [ ] Document any architectural issues found during field testing

**Why test before split**: If field testing reveals routing or protocol issues, it's far easier to fix on a branch than to restructure a newly-branded repo. The split itself is mechanical — don't do it until the code is proven.

### Phase 2: Create MeshAnchor Repository
- [ ] Create GitHub repo: `Nursedude/meshanchor`
- [ ] Copy alpha branch as initial commit (clean history via `git archive` or squash)
- [ ] Update branding: `src/__version__.py` → MeshAnchor, TUI title, log prefixes, README
- [ ] Flip `DEFAULT_MODE = RadioMode.MESHCORE` in `src/core/radio_mode.py`
- [ ] Adjust deployment profiles: `meshcore` becomes default profile
- [ ] Gate Meshtastic as optional (reverse current safe_import pattern)
- [ ] Set up CI/CD (pytest, lint, pre-commit hooks)
- [ ] Add CLAUDE.md for MeshAnchor with updated architecture docs

### Phase 3: Update MeshAnchor Main
- [ ] Document the two-app ecosystem in MeshAnchor README
- [ ] Keep MeshCore handler on main as-is (optional gateway)
- [ ] Archive alpha/meshcore-bridge branch (`git tag alpha-archived`)
- [ ] Update CLAUDE.md branch strategy to reflect MeshAnchor as separate repo

### Phase 4: Ecosystem Integration
- [ ] Define gateway protocol between MeshAnchor ↔ MeshAnchor
- [ ] Shared test vectors for CanonicalMessage (identical protocol conversion)
- [ ] Coordinated release documentation
- [ ] Cross-linking in both READMEs ("Made with aloha — part of the MeshAnchor family")

---

## 10. Deliverable

This is an analysis document. On approval:
1. Commit this analysis to `claude/analyze-branches-new-app-YM4Ht` and push
2. Save a copy to `.claude/plans/meshanchor_split_plan.md` for long-term reference
3. Update `.claude/plans/branch_convergence_guide.md` to note the new direction (MeshAnchor split instead of convergence)
