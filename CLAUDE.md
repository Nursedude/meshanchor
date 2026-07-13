# MeshAnchor - Claude Code Configuration

> **Dude AI**: Network Engineer, Physicist, Programmer, Project Manager
> **Architect**: WH6GXZ (Nursedude) — HAM General, Infrastructure Engineering, RN BSN

<!-- Auto-loaded by Claude Code -->
@.claude/rules/security.md
@.claude/rules/calibrated_claims.md
@.claude/rules/model_advisor.md
@.claude/foundations/persistent_issues.md

---

## CRITICAL — Read Before Any Code Change

**Service Interaction Rules** (inherited from MeshForge Issue #29):
- **NEVER** call `RNS.Reticulum()` without `configdir=` — causes EADDRINUSE when rnsd is running
- **NEVER** use raw `systemctl is-active` — use `check_service()` from `service_check.py`
- **NEVER** use `Path.home()` directly — use `utils.paths.get_real_user_home()` (MF001)
- **NEVER** use `safe_import` for first-party modules — external deps only
- **NEVER** call `sqlite3.connect()` directly — use `connect_tuned()` from `utils.db_helpers` (MF013). Every new SQLite DB also needs a `DBSpec` entry in `utils.db_inventory`. Run `python3 scripts/db_audit.py` to verify.
- **NEVER** use `shell=True`, bare `except:`, or skip input validation / subprocess timeouts
- **ALWAYS** use `_stop_event.wait()` instead of `time.sleep()` in daemon loops
- **ALWAYS** split files exceeding 1,500 lines — lint-enforced since 2026-07-13 (MF025, frozen baseline for the 2 pre-existing offenders; baseline only shrinks)
- **NOTE**: Meshtastic TCP rules (TCPInterface, fromradio) apply only to optional gateway code

**Calibrated claims** (the discipline for what *I* say; ported from MeshForge's
calibration spine): never emit a bare "100% / verified / all green / it works /
done" without a quoted external result. Tag every completion claim **VERIFIED**
(a check ran this turn — quote its exit code), **BELIEVED** (written, not run —
say so), or **UNKNOWN** (couldn't check — unobservable ≠ healthy). "Worked once"
is not "reliable". Re-derive the count at the end; never patch a running tally.
Checks of record: `python3 -m pytest tests/ -q` + `python3 scripts/lint.py --all`
(capture the real exit code; never `| tail`). The `claim_gate` Stop hook injects
this as one reflective beat — not a cage.

> Full security rules: `.claude/rules/security.md`
> Calibrated-claims discipline: `.claude/rules/calibrated_claims.md`
> Known issues & fixes: `.claude/foundations/persistent_issues.md`

---

## Quick Context

MeshAnchor is a **MeshCore-primary Network Operations Center (NOC)** — the sister project to [MeshForge](https://github.com/Nursedude/meshforge). Where MeshForge treats Meshtastic as the home radio, MeshAnchor makes **MeshCore primary** with Meshtastic and RNS as optional gateways.

Forked from MeshForge main on 2026-04-01. Shares the same TUI framework, gateway bridge architecture, and RF tools.

---

## Branch Strategy

| Branch | Version | Status |
|--------|---------|--------|
| `main` | `0.1.0-alpha` | MeshCore-primary NOC. Not yet field-tested. |

- Single branch for now. Feature branches use `claude/` prefix → PR to main.
- **Sister project**: [MeshForge](https://github.com/Nursedude/meshforge) (Meshtastic-primary)
- **Shared contract**: `CanonicalMessage` in `src/gateway/canonical_message.py` must stay compatible with MeshForge's version.
- **RNS-reliability parity**: MeshForge is the **lead repo** for the RNS-reliability arc; MeshAnchor mirrors it (the fleet runs one rnsd per box as a shared instance, so both apps must run the identical RNS substrate). **RNS-reliability changes land in MeshForge first, then port here.** Run `python3 /opt/meshforge/scripts/parity_check.py` to see drift. Byte-identical: `src/utils/rns_init.py` (the guarded RNS-init chokepoint — every `RNS.Reticulum()` routes through `open_reticulum()`; enforced by lint MF019 + `TestRNSReticulumChokepoint`), `src/gateway/canonical_message.py`, the `requirements/rns.txt` `MF-FORK-PIN` block (rns/lxmf pinned to the MeshForge-owned fork; verify with `scripts/rns_version_check.py`). Shape (MeshAnchor idiom): `rns_status_parser.py` `timed_out`, lint MF009+MF019, and the two RNS-wedge probes as `check_rns_rpc_responsive` + `check_rns_interface_down_peer_reachable` in `src/utils/active_health_probe.py` (HealthResult idiom, registered in the 30s gateway probe gated behind rnsd), NOT MeshForge's `watchdog_probes.py` shape.

---

## Development Principles

```
1. Make it work         ← First priority
2. Make it reliable     ← Security, testing
3. Make it maintainable ← Clean code, docs
4. Make it fast         ← Only when proven necessary
```

---

## Key Commands

```bash
# Launch
sudo python3 src/launcher_tui/main.py   # Primary interface (TUI)
python3 src/standalone.py               # Zero-dependency RF tools
# GTK4 desktop REMOVED — TUI is the only interface

# Verify changes
python3 -m pytest tests/ -v
python3 -c "from src.__version__ import __version__; print(__version__)"

# Regression prevention (Issue #29)
python3 scripts/lint.py --all
python3 -m pytest tests/test_regression_guards.py -v
git config core.hooksPath .githooks
```

---

## Architecture Overview

**TUI Pattern**: Handler Registry (Protocol + BaseHandler + TUIContext). Each menu action is a self-contained handler in `handlers/` dispatched by `handler_registry.py`.

```
src/
├── launcher_tui/      # PRIMARY INTERFACE (TUI)
│   ├── main.py        # NOC launcher + handler registration
│   ├── handler_protocol.py  # CommandHandler Protocol + TUIContext + BaseHandler
│   ├── handler_registry.py  # register/lookup/dispatch
│   ├── backend.py           # whiptail/dialog abstraction
│   └── handlers/            # 64 registered command handlers
├── commands/          # propagation.py, hamclock.py, base.py
├── gateway/           # RNS-Meshtastic bridge
│   ├── rns_bridge.py, gateway_cli.py, meshcore_handler.py
│   ├── canonical_message.py   # Multi-protocol message format
│   └── message_routing.py, message_queue.py (SQLite)
├── monitoring/        # mqtt_subscriber, node_monitor, traffic_inspector, packet_dissectors
├── plugins/           # meshcore.py plugin wrapper
├── utils/             # rf.py, common.py, service_check.py, coverage_map.py, claude_assistant.py
├── standalone.py      # Zero-dependency RF tools
└── __version__.py     # Version + changelog
```

> Full architecture: `.claude/foundations/domain_architecture.md`

---

## Exploration Entry Points

| Question | Start here |
|----------|-----------|
| Service behavior | `src/utils/service_check.py` |
| Protocol routing | `src/gateway/message_routing.py` |
| TUI handler dispatch | `src/launcher_tui/handler_registry.py` |
| RF math | `src/utils/rf.py` |
| AI assistant | `src/utils/claude_assistant.py` |
| Coverage maps | `src/utils/coverage_map.py` |

---

## Deployment Profiles

Profiles: `radio_maps` | `monitor` | `meshcore` | `gateway` | `full`

```bash
python3 src/launcher.py --profile gateway   # Select profile
python3 src/launcher.py                      # Auto-detect (default)
# Profile saved to ~/.config/meshanchor/deployment.json
```

> Full profile definitions + install commands: `.claude/foundations/deployment_profiles.md`

---

## Service Management

`utils/service_check.py` — **SINGLE SOURCE OF TRUTH** for systemd operations.

Key imports: `check_service`, `apply_config_and_restart`, `enable_service`, `start_service`, `stop_service`, `_sudo_cmd`, `_sudo_write`

**Privilege Separation**:
- **Viewer Mode** (default, no sudo): Monitoring, RF calcs, API data
- **Admin Mode** (sudo): Service control, `/etc/` config, hardware

---

## Key Modules

| Module | API |
|--------|-----|
| `utils/diagnostic_engine.py` | `diagnose(symptom, category, severity)` |
| `utils/knowledge_base.py` | `get_knowledge_base().query("topic")` |
| `utils/claude_assistant.py` | AI assistant (Standalone + PRO tiers) |
| `utils/coverage_map.py` | Folium coverage map generator |
| `commands/propagation.py` | Space weather (NOAA primary) |

---

## Auto-Review

```python
cd src && python3 -c "
from utils.auto_review import ReviewOrchestrator
r = ReviewOrchestrator()
report = r.run_full_review()
print(f'Issues: {report.total_issues}')
"
```

---

## Commit Style

```
feat: Add new feature       fix: Bug fix
docs: Documentation         refactor: Code restructure
test: Add tests             security: Security fix
```

---

## Research Docs (`.claude/` — 84 files)

| File | Contents |
|------|----------|
| `foundations/meshanchor_ecosystem.md` | All 5 repos, boundaries, APIs (canonical) |
| `foundations/domain_architecture.md` | Core vs Plugin model |
| `foundations/ai_principles.md` | Human-centered design philosophy |
| `foundations/persistent_issues.md` | **Known issues & fixes** |
| `plans/branch_convergence_guide.md` | main ↔ alpha merge strategy |
| `plans/qa_field_testing_plan.md` | Gateway, maps, MeshCore field-test protocol |
| `INDEX.md` | Full doc index with quick lookups |
| `research/README.md` | 22 technical deep dives (RNS, AREDN, RF, etc.) |

---

*Made with aloha for the mesh community*
