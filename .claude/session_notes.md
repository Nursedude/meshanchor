# MeshForge Session Notes

**Last Updated**: 2026-02-12
**Version**: v0.5.4-beta
**Codebase**: 258 Python files, 291K lines, 4,009+ tests

---

## P2/P3 Remaining Gaps — Future Work Reference

> **Purpose**: Consolidated backlog of known gaps, ordered by priority.
> Cross-referenced from `persistent_issues.md`, `missing_features.md`, `technical_debt_plan.md`, and session history.

---

### P2: Feature Accessibility (modules exist, not wired to TUI)

| # | Module | What It Does | Suggested Menu | Effort |
|---|--------|-------------|----------------|--------|
| 1 | `analytics.py` | Predictive alerts, network forecast | Dashboard > Analytics | LOW |
| 2 | `webhooks.py` | Webhook endpoint management | Configuration > Webhooks | LOW |
| 3 | `active_health_probe.py` | NGINX-style service health checks | Dashboard > Health Probes | LOW |
| 4 | `messaging.py` | Message history viewer, search, export | Dashboard > Messages | LOW |
| 5 | `device_backup.py` | Backup/restore device configs | Configuration > Backup | LOW |
| 6 | `classifier.py` | Traffic classification | Mesh Networks > Traffic | LOW |
| 7 | `rnode.py` | RNode device detection + config | Hardware > RNode Setup | MEDIUM |
| 8 | `latency_monitor.py` | Background latency monitoring | Dashboard > Latency | MEDIUM |

**Pattern**: All modules have working APIs. Work = add menu entry + display wrapper in mixin.

---

### P2: Persistent Issues (open, not yet fixed)

| Issue | Summary | Root Cause | Effort |
|-------|---------|-----------|--------|
| **#20** | Service detection status flakiness | Multiple fallback methods (UDP/pgrep/systemctl) conflict | MEDIUM |
| **#21** | CLI preset settings not reliably applied | Upstream meshtastic CLI bug (not MeshForge) | N/A (document) |
| **#27** | rnsd optional — UI doesn't make this clear | Error messages assume rnsd required | LOW |

**Issue #20 redesign spec**: Simplify `service_check.py` to systemctl-only for systemd services. Stop using port checks and pgrep as fallbacks. See `persistent_issues.md` Issue #20 for full spec.

---

### P2: Testing Gaps

| Area | Estimated Coverage | Gap Description |
|------|--------------------|-----------------|
| AREDN integration | ~60% | Edge cases uncovered |
| Plugin system (MeshChat/MeshCore) | ~50% | Framework exists, minimal plugin tests |
| Web API error scenarios | ~70% | Happy path tested, error paths light |
| Multi-node scenarios | ~40% | Single-node tested; scaling untested |
| Performance/load testing | ~20% | No load/stress tests exist |

---

### P2: Architecture Gaps

| Area | Gap | Impact |
|------|-----|--------|
| Event bus system | No pub/sub for RX messages — UI polls instead | RX messages don't propagate to TUI in real-time |
| Offline-first mode | Gateway assumes real-time services | Queue exists but incomplete for store-and-forward |
| Service independence | Gateway assumes rnsd + meshtasticd both running | Should degrade gracefully for MQTT-only deployments |
| Web client serving | /mesh/ subpath abandoned (4 attempts failed) | Options A/B documented but not implemented |

**Event bus**: Spec in `persistent_issues.md` Issue #20 Phase 3. Would enable RX display, live alerts, and cross-component notifications.

---

### P3: Code Quality / Technical Debt

| Item | Scope | Effort | Reference |
|------|-------|--------|-----------|
| Import boilerplate (86 try/except blocks) | All panels | LOW | `technical_debt_plan.md` Phase 1.1 |
| Configuration centralization (36 SETTINGS_DEFAULTS) | All panels | LOW | `technical_debt_plan.md` Phase 1.2 |
| Subprocess wrapper abstraction | ~100 calls | MEDIUM | `technical_debt_plan.md` |
| Type hints coverage (~30%) | Gradual | LOW | mypy.ini exists |
| Plugin isolation (plugins in core codebase) | Structural | HIGH | No separate plugin runtime |

---

### P3: Feature Accessibility (lower priority)

| Module | What It Does | Notes |
|--------|-------------|-------|
| `prometheus_exporter.py` | Prometheus export config | Only useful with Grafana stack |
| `influxdb_exporter.py` | InfluxDB export config | Only useful with InfluxDB stack |
| `simulator.py` | Network simulation | Standalone by design |
| `firmware_flasher.py` | Firmware update/flash | Deliberately not exposed (risky) |
| `nanovna.py` | NanoVNA antenna analysis | Alpha, hardware-dependent |

---

### P3: Documentation Gaps

| Document | Status | Notes |
|----------|--------|-------|
| Deployment guides (Pi/uConsole) | Not started | Only `quick_start` exists |
| Network planning guide | Not started | When to use RNS vs MQTT, preset selection |
| Plugin development guide | Not started | How to write custom integrations |
| API reference | Not started | Could auto-generate from commands/ |
| Troubleshooting guide | Not started | Common issues and fixes (partial in persistent_issues.md) |
| Architecture diagrams | Not started | Visual overview of module interactions |

---

### P3: Research Documents — Incomplete

| Document | Completion | Notes |
|----------|-----------|-------|
| `maps_progress.md` | 50% | Offline tiles working; double-tap TODO |
| `maps_double_tap.md` | 30% | UI mockup exists; implementation pending |
| `nginx_reliability_patterns.md` | 80% | Patterns documented; metrics integration incomplete |
| `uconsole_portable_noc.md` | 60% | Hardware research done; OS testing incomplete |
| `firmware_viability.md` | 70% | Analysis complete; flashing not exposed |
| `meshforge_enhancement_todos.md` | 40% | Feature list; needs prioritization |

---

### Hardware Testing Backlog (requires physical deployment)

- [ ] Maps on actual Pi with radio connected
- [ ] Coverage map with real GPS nodes
- [ ] AREDN integration with actual AREDN hardware
- [ ] Headless/SSH browser detection path
- [ ] Cross-mesh message test: LongFast -> MQTT -> ShortTurbo
- [ ] RNS bridge test: ShortTurbo -> Gateway -> RNS/NomadNet
- [ ] rnsd permission fix verification on MOC2
- [ ] Grafana metrics with gateway on port 9090

---

## Project Health Snapshot (2026-02-12)

| Metric | Value | Status |
|--------|-------|--------|
| Version | v0.5.4-beta | MQTT bridge release |
| Python files | 258 | Well-modularized |
| Total lines | 291,258 | Healthy |
| Test count | 4,009+ | Comprehensive |
| Tests passing | 4,009 pass, 19 skip, 0 fail | Clean |
| Lint | Clean | MF001-MF004 all passing |
| Files >1,500 loc | 1 (knowledge_content.py, by design) | Healthy |
| Documentation | 51+ MD files | Extensive |
| Session notes | 43+ entries | Good tracking |
| Persistent issues | 28 tracked, 8 archived | Active maintenance |

---

## Session Log

### Session: Session Notes Documentation (2026-02-12)

**What**: Consolidated all P2/P3 gaps from across project documentation into a single reference in `session_notes.md`. Cross-referenced `persistent_issues.md`, `missing_features.md`, `technical_debt_plan.md`, `roadmap.md`, and all prior session notes.

**Entropy watch**: Documentation-only session. No code changes. Clean.

---

### Session: Feature Accessibility Audit & Fixes (2026-02-12)

**What**: Systematic audit of 110+ modules and 548+ methods. Wired 6 features to TUI menus.

**Changes** (commit c3e7431):

| Feature | Menu Location | Source Module |
|---------|--------------|---------------|
| Network Status Reports | Dashboard > Reports | `report_generator.py` |
| Health Score Dashboard | Dashboard > Health Score | `health_score.py` |
| RNS Config Drift Check | RNS > Config Drift Check | `config_drift.py` |
| Antenna Comparison | RF & SDR > Antenna Analysis | `antenna_patterns.py` |
| Enhanced Signal Trends | Node Health > Signal Trends | `signal_trending.py` |
| Enhanced Battery Forecast | Node Health > Battery Forecast | `predictive_maintenance.py` |

**Files modified (5):** `main.py`, `dashboard_mixin.py`, `rns_menu_mixin.py`, `rf_tools_mixin.py`, `node_health_mixin.py`

**Tests**: 4,009 passed, 19 skipped, 0 failures. Lint clean.

---

### Session: MQTT Bridge Architecture (2026-02-11)

**What**: Gateway bridge rewritten from TCP:4403 to MQTT transport. Zero interference with web client.

**Key changes** (v0.5.4-beta):
- NEW: `MQTTBridgeHandler` — subscribes to meshtasticd MQTT, sends via CLI
- MQTT bridge is now default mode (web client on :9443 works uninterrupted)
- DEPRECATED: `meshtastic_api_proxy.py` (was source of web client interference)
- NEW: Deployment templates (mosquitto.conf, rnsd-user.service, setup script)
- NEW: MQTT bridge settings menu in TUI

---

### Session: API Proxy fromradio Fix (2026-02-10)

**What**: `MeshtasticApiProxy` was draining ALL fromradio packets from port 9443. Fixed: proxy defaults to OFF.

**See**: `.claude/session_notes/2026-02-10_api_proxy_fromradio_fix.md`

---

### Session: /mesh/ Architecture Rethink (2026-02-10)

**Status**: SUPERSEDED — meshtasticd owns its web client.

4 attempts at /mesh/ subpath serving all failed (HTML rewriting, CSS injection, base tag, regex stripping). Root cause: Vite builds hardcode paths in JS bundles. Clean options documented (serve at root or separate ports). See full analysis in previous session notes archive.

---

### Session: Phantom Nodes + Web UI Fixes (2026-02-10)

**What**: Two-layer phantom node defense (server-side protobuf filtering + client-side JS error protection). Right panel CSS overflow fix. Radio message feedback improvement.

---

### Session: MOC Broker Templates (2026-02-09)

**What**: Configured broker templates for MOC1 (Pi5 + Meshtoad + LongFast) with MQTT-bridged topology to MOC2 (Pi HAT + ShortTurbo + RNS/NomadNet).

**See**: `.claude/session_notes_moc_broker.md`

---

### Session: Feature Accessibility Audit (2026-02-08)

**What**: Verified all features accessible via TUI. 30 mixin files, zero dead menu entries. Added 97 tests for `rns_transport.py`. v0.5.2-beta and v0.5.3-beta releases.

---

## Quick Reference: Next Session Pickup

1. **P2 quick wins**: Wire `analytics.py`, `webhooks.py`, `messaging.py` to TUI (LOW effort each)
2. **P2 reliability**: Fix service detection flakiness (Issue #20 — simplify to systemctl-only)
3. **P2 architecture**: Event bus for RX message propagation
4. **P3 debt**: Import boilerplate consolidation (`safe_import()` pattern)
5. **Hardware**: Test all backlog items on MOC1/MOC2
