# Development

Working on MeshAnchor: tests, gates, and the contribution path.

## Development

### Branch Strategy

| Branch | Version | Focus |
|--------|---------|-------|
| `main` | see `src/__version__.py` | MeshCore-primary NOC (the SSOT is the version file; a copy here is unguarded and goes stale) |

**Sister project:** [MeshForge](https://github.com/Nursedude/meshforge) is the
Meshtastic-primary NOC — extracted from the same codebase on 2026-04-01.

Feature branches use `claude/` prefix, merged via PR to main. Dependabot
dependency PRs auto-merge once CI is green.

**Shared contract:** `CanonicalMessage` in `src/gateway/canonical_message.py` must
stay compatible with MeshForge's version. Changes to the message format should be
coordinated across both projects.

---

## Code Health

### Test Coverage

**~5,900 tests** across <!--STAT:testfiles-->215<!--/STAT--> test files. Top suites by depth
(per-file counts are a 2026-07 snapshot — run `python3 -m pytest tests/<file> --co -q` for the live number):

| Test File | Tests | Covers |
|-----------|-------|--------|
| `test_all_handlers_protocol.py` | 449 | Protocol conformance across all 85 TUI handlers |
| `test_rns_bridge.py` | 250 | Core bridge: routing, circuit breaker, callbacks |
| `test_rf.py` | 107 | RF calculations: haversine, FSPL, Fresnel, link budget |
| `test_message_queue.py` | 104 | SQLite queue, retry policy, dead letter |
| `test_rns_transport.py` | 97 | Packet fragmentation, reassembly, transport stats |
| `test_lxmf_broadcast_bridge.py` | 88 | LXMF broadcast bridge: subscribe, fan-out, dedup |
| `test_gateway_config.py` | 80 | Gateway config + validators |
| `test_status_bar.py` | 70 | TUI status bar rendering |
| `test_fleet_aggregator.py` | 69 | Fleet observability aggregation |
| `test_node_tracker.py` | 68 | Unified node tracking |
| `test_mqtt_robustness.py` | 68 | MQTT reconnection, broker failover |
| `test_service_check.py` | 67 | Service management single source of truth |
| `test_phase4b_radio_writes.py` | 63 | MeshCore radio writes, region-aware validation |
| `test_rns_status_parser.py` | 62 | rnstatus parsing (incl. `timed_out` wedge detection) |
| `test_commands.py` | 61 | CLI command handlers |
| `test_meshcore_handler.py` | 58 | MeshCore connection, messaging, node tracking |

*All tests use mocked external services. Field validation with real hardware is a separate track — and the one that needs your help.*

```bash
python3 -m pytest tests/ -v            # Run all tests
python3 -m pytest tests/ -v -x         # Stop on first failure
python3 -m pytest tests/test_meshcore_handler.py -v  # MeshCore tests only
```

### Auto-Review & Lint

Security linter (`scripts/lint.py`) enforces 17 rules:

| Rule | Description |
|------|-------------|
| MF001 | `Path.home()` -> use `get_real_user_home()` for sudo safety |
| MF002 | No `shell=True` in subprocess calls |
| MF003 | No bare `except:` — specify exception types |
| MF004 | All subprocess calls need `timeout` parameter |
| MF006 | No `safe_import` for first-party modules — use direct imports |
| MF007 | No direct `TCPInterface()` — use connection manager |
| MF008 | No raw `systemctl` for service state — use `service_check` |
| MF009 | `RNS.Reticulum()` must include `configdir=` parameter |
| MF010 | No `time.sleep()` in daemon loops — use `_stop_event.wait()` |
| MF011 | RNS repair logic must live in `_rns_repair.py` / diagnostics |
| MF012 | Context-loaded docs (e.g. `persistent_issues.md`) must stay under 40k chars |
| MF013 | Bare `sqlite3.connect()` outside `db_helpers.py` — use `connect_tuned()` |
| MF014 | No operator-specific values (hostnames, personal emails, `/home/<user>/` paths) |
| MF016 | `@patch('src.utils.paths.…')` in tests no-ops — production imports via `utils.paths` |
| MA017 | Hardened systemd units: `ReadWritePaths=` must cover the MeshAnchor write buckets |
| MF019 | `RNS.Reticulum()` only via the `open_reticulum()` chokepoint in `utils/rns_init.py` |
| MF020 | `apply_config_and_restart()` `(bool, msg)` result must not be discarded in TUI handlers |

```bash
python3 scripts/lint.py --all          # Run all lint rules
git config core.hooksPath .githooks    # Enable pre-commit hooks
```

### Reliability Patterns

- **Circuit breaker** — fault isolation on gateway connections
- **Exponential backoff** — reconnect (1s -> 2s -> 4s -> ... -> 30s max) with jitter
- **Graceful degradation** — `safe_import` for optional dependencies, features disable not crash
- **Handler isolation** — registry dispatch with per-handler exception boundaries
- **Persistent queue** — SQLite message queue survives restarts
- **Shared connection manager** — prevents TCP:4403 client contention
- **Pre-flight validation** — device probes before connection, service checks before operations
- **Stale node purge** — 24h TTL on offline nodes, prevents ghost entries in node tracker
- **Localhost-only control** — all mutating HTTP endpoints restricted to loopback
- **Permission hardening** — narrow Bash subcommand patterns with explicit deny list (CVE-2026-21852)

---

## Contributing

> **The #1 way to help right now: test MeshAnchor with a real MeshCore radio and report what happens.**

### How to Help (Priority Order)

1. **Test with real MeshCore hardware** — connect a companion radio, try the TUI,
   attempt gateway bridging, report results. This is the single most impactful
   contribution you can make.

2. **Report issues** — field test results (even "it worked!" is valuable), bugs,
   unexpected behavior, missing documentation. Use
   [GitHub Issues](https://github.com/Nursedude/meshanchor/issues).

3. **Code contributions** — feature branches use `claude/` prefix, merged via PR to main.

### Development Commands

```bash
python3 -m pytest tests/ -v           # Run tests
python3 scripts/lint.py --all         # Security linter
git config core.hooksPath .githooks   # Enable pre-commit hooks
```

**Code rules:** No `shell=True`, no bare `except:`, use `get_real_user_home()` not
`Path.home()`, use `_stop_event.wait()` not `time.sleep()` in daemon loops,
use `connect_tuned()` not bare `sqlite3.connect()`, split files over 1,500 lines.

**Commit style:** `feat:`, `fix:`, `docs:`, `refactor:`, `test:`, `security:`

See [CLAUDE.md](../CLAUDE.md) for the complete development guide.

---

