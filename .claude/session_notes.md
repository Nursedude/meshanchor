# MeshForge Session Notes

**Last Updated**: 2026-02-09
**Current Branch**: `claude/configure-meshforge-broker-h1VTf`
**Version**: v0.5.2-beta
**Tests**: 3926 passed, 1 failed (pre-existing rnsd sandbox), 19 skipped
**Linter**: 0 issues (clean)

## Session Focus: MOC1 Broker Configuration (2026-02-09)

Configured MeshForge broker templates for MOC1 (Pi5 + Meshtoad + LongFast) with
MQTT-bridged topology to MOC2 (Pi HAT + ShortTurbo + RNS/NomadNet).

See: `.claude/session_notes_moc_broker.md` for full details.

### Changes This Session
- **NEW** `templates/meshtoad.yaml` — Meshtoad CH341 SPI hardware template
- **NEW** `templates/meshforge-presets/moc1-broker.yaml` — MOC1 full preset
- **NEW** `templates/gateway-pair/moc-mqtt-bridge.md` — MQTT-bridged deployment guide
- **UPDATED** `templates/gateway-pair/README.md` — MQTT topology reference
- **UPDATED** `meshtasticd_config_mixin.py` — Broker-aware MQTT default

## Previous Session: Feature Accessibility Audit (2026-02-08)

### Full TUI Feature Audit (2026-02-08)

Verified ALL features are accessible to the user via TUI. No dead code, no orphaned features.

#### TUI Menu Structure (10 top-level + sub-menus)

| Menu | Key Features | Status |
|------|-------------|--------|
| Dashboard | Service status, node health, alerts, EAS | OK |
| Mesh Networks | Meshtastic, RNS, Gateway, AREDN, MQTT, **Favorites** | OK |
| RF & SDR | Link budget, site planner, freq slots, SDR | OK |
| Maps & Viz | NOC map, coverage, **heatmap**, **offline tiles**, topology | OK |
| Configuration | Radio, channels, RNS, services, backup, updates, **PSKReporter** | OK |
| System | Hardware, logs, network, diagnostics, **code review** | OK |
| Quick Actions | Shortcuts to common ops | OK |
| Emergency Mode | Broadcast, SOS, **EAS alerts** | OK |
| About | Version, web client, help | OK |

#### Previously Reported Feature Gaps — ALL RESOLVED

| Feature | Prior Status | Current Status |
|---------|-------------|----------------|
| Auto-Review System | "command-line only" | System > Code Review |
| Heatmap | "no TUI entry" | Maps & Viz > Heatmap |
| Tile caching | "no TUI entry" | Maps & Viz > Offline Tiles |
| EAS Alerts | new feature | Emergency Mode > Weather/EAS Alerts |
| PSKReporter MQTT | new feature | Config > Settings > Propagation Sources |
| Favorites | new feature | Mesh Networks > Favorites |

### Linter Fix

Fixed false positive MF001 in `scripts/lint.py` — linter now skips `Path.home()` references inside string literals (changelog entries). Previously flagged `__version__.py` line 50.

### Mixin Coverage

30 mixin files provide TUI functionality. All dispatch entries reference implemented methods. Zero broken/dead menu entries found.

### Test Results
- Core tests (RF, safe_call, message_queue): 99 pass, 0 fail
- Full suite: 3397+ (may timeout in sandbox — runs fine on Pi)
- Linter tests: 13 pass

### Commits This Session
- (pending) fix: Linter MF001 false positive on string literals

### Test Coverage Update (2026-02-08)

Three files previously listed as "zero tests" already had comprehensive suites:
- `meshtastic_protobuf_client.py` — 1,027 lines of tests in `test_meshtastic_protobuf.py`
- `meshtastic_handler.py` — 929 lines of tests in `test_meshtastic_handler.py`
- `packet_dissectors.py` — 1,029 lines of tests in `test_packet_dissectors.py`

New test file added this session:
- `rns_transport.py` — **97 tests** in `test_rns_transport.py` (Fragment, PendingPacket, TransportStats, fragmentation, callbacks, receive handler, connection, start/stop, RNS adapter, factory, end-to-end pipeline)

**All 244 tests pass** (147 existing + 97 new) for these 4 modules.

### Remaining Work (Next Session Priorities)

#### Reliability Improvements (from persistent_issues.md)
1. **Gateway bridge mode fix** — `mesh_bridge` → `message_bridge` for single-radio setups (Issue #16)
2. **Grafana metrics** — needs gateway running for metrics server on port 9090
3. **MQTT service check** — verify `mosquitto` service status detection works (Issue #20 Phase 1)
4. **Service detection simplification** — trust systemctl only for systemd services (Issue #20 Phase 1, LOW effort / HIGH impact)
5. **Status display separation** — separate "service running" from "preset detected" in UI (Issue #20 Phase 2)
6. **RX message event bus** — messages received by gateway not displayed in UI (Issue #20 Phase 3)
7. **Post-install verification** — ensure `verify_post_install.sh` runs automatically (Issue #23)
8. **Python environment mismatch** — rnsd can't find meshtastic module when installed via pipx (Issue #24)

#### Additional Test Coverage Targets
- `rns_bridge.py` (1,614 lines) — core gateway bridge logic
- `node_tracker.py` (930 lines) — unified node tracking
- `message_queue.py` — persistent SQLite queue
- `reconnect.py` — reconnection strategy with backoff

#### Hardware Testing
- Maps on actual Pi with radio connected
- Coverage map with real GPS nodes
- AREDN integration with actual AREDN hardware
- Headless/SSH browser detection path

### File Sizes (All Under 1,500 lines)
- launcher_tui/main.py: ~1,433 lines
- service_menu_mixin.py: ~1,358 lines
- All other files: well under threshold
