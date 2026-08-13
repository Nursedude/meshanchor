# What MeshAnchor Does

The full capability inventory, including what is proven in the field and what still needs independent testing.

## What Works

<!-- Version-tagged heading lives in the README, where the
     version guard can see it; a copy here would go stale unguarded. -->

### Status Definitions

| Status | Meaning |
|--------|---------|
| **Mock-Tested** | Passes unit tests against mocks. No field validation with real hardware. |
| **Inherited** | Carried from MeshForge where it was field-tested. Untested in MeshAnchor context. |
| **Code-Ready** | Implemented and compiles. Needs real hardware/services to validate. |

### MeshCore Integration (Code-Ready, MeshCore-only path Field-Validated 2026-05-02)

| Feature | Tests | Notes |
|---------|-------|-------|
| Companion radio detection | -- | Serial USB scan + udev persistent naming (`/dev/ttyMeshCore`) |
| Pre-flight device validation | -- | Serial probe before connection, permission + existence checks |
| meshcore_py connection | 58 | Async event loop, reconnect, message handling. **Field-validated** with RAK Heltec V3 in Serial Companion mode (2026-05-02). |
| CanonicalMessage bridging | 46 | Protocol-agnostic message format, N-protocol routing |
| 3-way routing classifier | 32 | MeshCore + Meshtastic + RNS tri-bridge tests (mock-only) |
| MeshCore TUI menu | -- | Status, detect, config, nodes, stats, chat, daemon control |
| Chat HTTP API (`/chat/*` on :8081) | 19 | Daemon-side ring buffer + send/receive endpoints. Bidirectional Public + private-channel messaging field-validated 2026-05-02. |
| Daemon control in TUI | 5 | start / stop / restart / journal / live tail through `service_check` SSOT |

### Gateway Bridge (Inherited + Code-Ready)

| Feature | Tests | Notes |
|---------|-------|-------|
| Meshtastic MQTT bridge | 250 | Zero-interference gateway (v0.5.4 architecture) |
| RNS/LXMF gateway | 97 | rnsd shared instance client |
| LXMF broadcast bridge plug-in | -- | **Field-validated 2026-05-09**: same-host + cross-federation subscribe/fan-out. Fleet floor: RNS ≥ 1.1.9. |
| LXMF subscriber reliability | 187 | **Field-live 2026-05-12**: state machine, tier engine, auto-transitions, tier-aware backoff, Stale Subscribers TUI prune, Prometheus metrics on `/metrics`, structured JSON state-transition logs, `/health` digest, Stack Health probe |
| Message queue (SQLite) | 104 | Persistent queue, retry policy, circuit breaker |
| Reconnect engine | 45 | Exponential backoff (1s -> 30s max), jitter, slow start |
| MQTT robustness | 68 | Reconnection, message loss recovery, broker failover |

### Fleet Observability (Field-Validated 2026-05-09 → 2026-05-11)

| Feature | Tests | Notes |
|---------|-------|-------|
| Fleet collector + watchdog | -- | Local cron-style systemd units writing heartbeat / blackout state. 24h clean-soak passed 2026-05-11 (NRestarts=0, history matches expected events). |
| Blackout kinds | -- | `no_data`, `http_dead`, `frozen`, `daemon_dead` — 4-kind silence detection with 2-cycle hysteresis. End-to-end smoke: 43s detect, 13s recover. |
| `/fleet/slo` + `/fleet/federation` + `/fleet/activity` | -- | Daemon HTTP endpoints; required/optional service split (1+ required up = degraded, all required down = error, optional doesn't gate). |
| Cross-fleet web view (`/fleet`) | -- | `web/fleet.html` BIARC demo surface with sparklines, decoupled polling, active/stale federation styling. |
| Prometheus exposition | -- | `/metrics` + `/healthz` on daemon and map processes. Map's `/fleet/*` polls auto-instrumented as `meshanchor_map_http_requests_total`. |

### RF & Maps (Inherited + MeshCore population shipped 2026-05-07)

| Feature | Tests | Notes |
|---------|-------|-------|
| Link budget calculator | 107 | FSPL, Fresnel zone, earth bulge, signal classification |
| Coverage maps (Folium) | -- | Static HTML, SNR-based link coloring, offline tiles |
| Live NOC map (Leaflet) | -- | Force-clustering across all networks (>1k features), gzipped JSON+HTML (28MB→3.4MB on meshanchor-server), reticulum→rns alias |
| `map.meshcore.dev` fetcher | -- | Direct fetch of ~42k positioned MeshCore nodes (MessagePack) into the live NOC map |
| Operator pin placement (TUI) | -- | "MeshCore Map Pins" handler for local pubkeys; daemon `PUT /radio/coords` writes radio identity |
| Space weather (NOAA) | -- | Solar flux, K-index, band conditions |
| Site planner | -- | Range estimation with terrain |
| Cython fast path | -- | Optional 5-10x RF calculation speedup |

### TUI Interface (Inherited)

| Feature | Tests | Notes |
|---------|-------|-------|
| Handler registry | 70 | <!--STAT:handlers-->85<!--/STAT--> handler files, Protocol + BaseHandler pattern |
| whiptail/dialog backend | -- | raspi-config style, SSH-friendly |
| Deployment profile selector | 76 | 5 profiles, MeshCore-first ordering, auto-detect, full matrix pinned |
| Startup health checks | 38 | Profile-aware classification (required / optional / not_applicable) |
| Fleet monitor panel | -- | TUI handler reads daemon `/fleet/slo` with required-only semantics ("ready · 2/2 required") |
| Identity & Position submenu | -- | `set_radio_name` / `set_radio_coords` / send advert; pushes to daemon HTTP `/radio/{name,coords,advert}` |
| `meshcore-cli` passthrough | -- | TUI surface drops operator into [meshcore-cli](https://github.com/meshcore-dev/meshcore-cli) with daemon hand-off; bridge resumes on exit |

### Monitoring & Telemetry (Inherited)

| Feature | Tests | Notes |
|---------|-------|-------|
| MQTT subscriber | 68 | Nodeless node tracking, protobuf decode |
| Traffic inspector | -- | Packet capture, protocol tree, display filters |
| RNS packet sniffer | -- | Wireshark-grade capture, announce tracking |
| Prometheus exporter | -- | 50+ metric families, Grafana-compatible |
| Node tracker | 68 | Unified node inventory, 15m offline threshold, 24h stale purge |

### Known Limitations

| Feature | Limitation | Workaround / Status |
|---|---|---|
| **Fleet Monitor (multi-host)** | Handler-thread pile-up on Pi-class hardware under sustained dashboard polling. Single-tab steady-state works; multiple dashboard tabs / sustained over-polling can briefly trigger 429 (server_busy) responses. | In-flight semaphore caps concurrent `/fleet/*` handlers ([#128](https://github.com/Nursedude/meshanchor/issues/128)) + dashboard auto-retries on 429 honoring `Retry-After`. Daily `meshanchor-map-restart.timer` is in place as a workaround belt-and-suspenders until the supporting fixes ([#126](https://github.com/Nursedude/meshanchor/issues/126), [#127](https://github.com/Nursedude/meshanchor/issues/127)) land. **Single-box deployment is the most reliable mode today.** Full failure-mode log in [#131](https://github.com/Nursedude/meshanchor/issues/131). |
| **`non_self_peers` filter** | Filters by peer name only, so a `fleet.json` self entry under a non-matching name slips through and the rollup HTTP-polls its own listener every cycle. | [#130](https://github.com/Nursedude/meshanchor/issues/130) tracks the fix; until then, don't include a self entry in `~/.config/meshanchor/fleet.json` (or name it exactly your hostname). |
| **Cascade detector** | Not yet ported from MeshForge — pre-failure shapes (rnsd RPC wedge, stale tracer fires) won't alarm before the cross-fleet rollup shows their downstream effects. | [#129](https://github.com/Nursedude/meshanchor/issues/129) tracks the port. |

### Testing Reality Check

MeshAnchor has **~5,900 automated tests** (run `python3 -m pytest tests/ --co -q`
for the live count) across <!--STAT:testfiles-->217<!--/STAT--> test files. However, automated tests
validate code paths with mocks — they do not replace field testing. Every feature
listed above needs validation with **real radios and real mesh traffic** before it can
be considered reliable.

**What has been validated with real hardware on `meshanchor-server` (Pi 4B + RAK Heltec V3, continuous deployment since 2026-05-02):**
- MeshCore companion radio connection (Serial Companion firmware via USB)
- Bidirectional channel messaging on Public (slot 0) and private channels (`meshanchor`
  on slot 1/2) — RX from a paired BLE Companion + iOS, TX from the daemon's chat API
  through the TUI, both directions confirmed
- **MeshCore↔RNS LXMF broadcast bridge (2026-05-09)** — same-host subscribe + fan-out,
  then cross-host fan-out across the MeshForge Hawaii federation; "Got to test Claude"
  message round-tripped from a federation peer to `meshanchor-server` and broadcast
  to subscribers
- **LXMF subscriber reliability stack (2026-05-12)** — state machine, tier engine,
  Prometheus metrics on daemon `/metrics` (4 metric families), `/health` digest,
  Stack Health probe; live sample shows 3 healthy/external subscribers
- **Fleet observability (2026-05-11)** — collector + watchdog ran a 24-hour clean
  soak with `NRestarts=0` on both units, exactly the expected closed blackout history,
  and all required services available
- **MeshCore map population (2026-05-07)** — first appearance of MeshCore data on the
  meshanchor-server live map (`map.meshcore.dev` fetcher + operator pin placement)
- **`meshcore-cli` TUI passthrough (2026-05-07)** — daemon hand-off + restart wrapper
  validated against meshcore-cli v1.5.7 on meshanchor-server
- Daemon stability under restart cycles (no watchdog churn, NRestarts=0 over the
  most recent 24h window)
- Chat HTTP API + TUI Chat menu (since-id polling, channel + DM send paths)
- TUI daemon control (status / start / stop / restart / journal / live tail)
- RNS announce reception (gateway sees external MeshForge gateway announces)

**What has not yet been tested with real hardware in MeshAnchor:**
- Coverage maps with real GPS position data
- 3-way routing (MeshCore ↔ Meshtastic ↔ RNS) with concurrent traffic on a single host
- Independent confirmation on hardware other than `meshanchor-server` (this is the gap
  external testers can close — see [Contributing](development.md#contributing))

**Reliability ratio — single-box vs fleet monitor:**

The fleet rollup / federation monitoring path has the **least field time** in
the project and the most documented recurrence patterns ([#34](https://github.com/Nursedude/meshanchor/blob/main/.claude/foundations/persistent_issues.md), [#128](https://github.com/Nursedude/meshanchor/issues/128), [#130](https://github.com/Nursedude/meshanchor/issues/130), [#131](https://github.com/Nursedude/meshanchor/issues/131)).
Single-box install (one host, one TUI, the local map at `:5000`) is
significantly more reliable than the multi-host fleet monitor view. Operators
who need steady-state observability should start with single-box, confirm the
core flows, then layer in the cross-host dashboard once they've understood the
restart cadence + known limitations above.

**What was field-tested in MeshForge** (inherited, likely works): TUI, meshtasticd config,
RF tools, RNS/rnsd integration, NomadNet, service management, standalone tools.

---

## AI Intelligence

MeshAnchor includes two tiers of AI-powered network diagnostics:

### Standalone Mode (No Internet Required)
- 20+ topic knowledge base covering mesh networking fundamentals
- Rule-based diagnostic engine with pattern matching
- Structured troubleshooting guides for common issues
- Confidence scoring on diagnoses
- Works completely offline — ideal for field deployment

### PRO Mode (Claude API)
- Natural language troubleshooting ("Why is my MeshCore node offline?")
- Log file analysis with suggested actions
- Context-aware responses (knows your network topology)
- Predictive issue detection
- Falls back to Standalone when API unavailable

---

