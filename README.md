# MeshAnchor

<p align="center">
  <strong>MeshCore Network Operations Center</strong><br>
  <em>Anchor. Bridge. Monitor.</em>
</p>

<p align="center">
  <a href="https://github.com/Nursedude/meshanchor"><img src="https://img.shields.io/badge/version-0.1.0--alpha-orange.svg" alt="Version"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-GPL--3.0-green.svg" alt="License"></a>
  <a href="https://python.org"><img src="https://img.shields.io/badge/python-3.10+-yellow.svg" alt="Python"></a>
  <a href="https://github.com/Nursedude/meshanchor"><img src="https://img.shields.io/badge/tests-passing-blue.svg" alt="Tests"></a>
</p>

<p align="center">
  <a href="https://nursedude.substack.com">Development Blog</a> |
  <a href="https://github.com/Nursedude/meshanchor/issues">Report Issues</a> |
  <a href="#contributing">Contribute</a> |
  <a href="https://github.com/Nursedude/meshforge">Sister Project: MeshForge</a>
</p>

---

## What is MeshAnchor?

**MeshAnchor is a MeshCore-primary Network Operations Center** — the sister project to [MeshForge](https://github.com/Nursedude/meshforge).

Where MeshForge treats Meshtastic as the "home" radio, MeshAnchor flips the architecture: **MeshCore is primary, Meshtastic and RNS are optional gateways.** Same TUI framework. Same gateway bridge pattern. Same RF tools. Different home radio.

> **ALPHA SOFTWARE — Field-deployed and accumulating mileage. More testers wanted.**
>
> MeshAnchor has **~5,900 tests passing** against mocks (run `python3 -m pytest tests/ --co -q` for the live count). The canonical NOC
> (`meshanchor-server`, Pi 4B + RAK Heltec V3 in Serial Companion mode) has
> been running as a continuous deployment since 2026-05-02 and is currently
> the source of truth for field validation. As of 2026-05-13 it carries:
> bidirectional MeshCore channel messaging, MeshCore↔RNS LXMF broadcast
> bridge (same-host + cross-federation fan-out validated 2026-05-09),
> LXMF subscriber reliability tier engine with Prometheus metrics, a fleet
> observability stack (collector + watchdog + 24h clean-soak passed
> 2026-05-11), MeshCore map population from `map.meshcore.dev` plus operator
> pin placement, and a `meshcore-cli` TUI passthrough with daemon hand-off.
> Coverage maps with live GPS position data and full 3-way (MeshCore ↔
> Meshtastic ↔ RNS) concurrent traffic remain unvalidated. If you have a
> MeshCore companion radio (RAK4631, Heltec V3, T-Deck, T-Echo), your
> independent field testing on different hardware is the single most
> valuable contribution right now. See [Contributing](#contributing).

Plug in a MeshCore companion radio, run the installer, and you get:

- **MeshCore integration** — direct companion radio management via meshcore_py, pre-flight device validation, persistent udev naming, in-TUI radio config (LoRa params, channels, TX power) with region-aware validation
- **MeshCore CLI passthrough** — TUI surface that drops the operator into [meshcore-cli](https://github.com/meshcore-dev/meshcore-cli) (common verbs, send DM/channel, remote-admin `cmd` bridge to [docs.meshcore.io/cli_commands/](https://docs.meshcore.io/cli_commands/), interactive REPL) with the daemon's radio ownership handed off cleanly so the bridge resumes when the CLI exits
- **Gateway bridge** — bidirectional MeshCore to Meshtastic/RNS message routing via CanonicalMessage
- **RF engineering tools** — link budget, Fresnel zone, FSPL, coverage maps, space weather (NOAA)
- **TUI interface** — <!--STAT:handlers-->85<!--/STAT--> handler files, MeshCore-primary menu with Optional Gateways submenu, raspi-config style (whiptail/dialog), SSH-friendly
- **Live NOC maps** — Leaflet.js browser view with WebSocket updates
- **meshforge-maps integration** — discovery, browser launch, configurable endpoint, bidirectional data fusion, and double-confirmed lifecycle control for the sister-project mapping service on `:8808`
- **MQTT monitoring** — nodeless mesh observation, protobuf decode, traffic inspector
- **AI diagnostics** — offline knowledge base + optional Claude PRO mode
- **Prometheus/Grafana** — 50+ metrics, 5 pre-built dashboards

Optional add-ons (install from TUI when you need them):
- **NomadNet** — terminal-based LXMF client (Reticulum users)

### The Vision

MeshCore brings capabilities that Meshtastic can't — 64-hop routing (vs 7), flood-free mesh topology, and a protocol designed from the ground up for resilient long-range communication. But MeshCore lacks the operational tooling that network operators need: maps, monitoring, diagnostics, gateway bridging.

**MeshAnchor fills that gap.**

One interface to manage your MeshCore network. One gateway to bridge messages to Meshtastic and Reticulum nodes. One toolkit for RF planning, diagnostics, and field operations. All running on a Raspberry Pi that you can SSH into from anywhere.

This is the first open-source NOC that treats MeshCore as the primary radio, with Meshtastic and RNS as optional gateways rather than the other way around. No cloud dependencies. No subscriptions. Just a box that makes mesh networks work together.

```bash
sudo python3 src/launcher_tui/main.py
```

**Built for:** MeshCore developers, RF engineers, network operators, amateur radio operators, and emergency comms planners who want professional-grade network visibility.

---

## Quick Start

> **Using Meshtastic as your primary radio?** Use [MeshForge](https://github.com/Nursedude/meshforge) instead.

### Fresh Install

```bash
# One-liner (Pi / Debian / Ubuntu)
curl -sSL https://raw.githubusercontent.com/Nursedude/meshanchor/main/install.sh | sudo bash

# Or manually
git clone https://github.com/Nursedude/meshanchor.git /opt/meshanchor
cd /opt/meshanchor
sudo bash scripts/install_noc.sh    # Full NOC stack install
```

The NOC installer auto-detects your radio hardware (SPI HAT or USB), optionally installs
meshtasticd + Reticulum, and sets up systemd services. It will prompt you to select
your HAT if SPI is detected.

**One-liner options** (environment variables):
```bash
# Install with automatic system upgrade
curl -sSL https://raw.githubusercontent.com/Nursedude/meshanchor/main/install.sh | sudo UPGRADE_SYSTEM=yes bash

# Install without upgrade prompt
curl -sSL https://raw.githubusercontent.com/Nursedude/meshanchor/main/install.sh | sudo SKIP_UPGRADE=yes bash

# Use system pip instead of venv
curl -sSL https://raw.githubusercontent.com/Nursedude/meshanchor/main/install.sh | sudo BREAK_SYSTEM_PACKAGES=yes bash
```

**NOC installer options** (`scripts/install_noc.sh`):
```bash
sudo bash scripts/install_noc.sh --skip-meshtasticd   # Don't install meshtasticd
sudo bash scripts/install_noc.sh --skip-rns            # Don't install Reticulum
sudo bash scripts/install_noc.sh --client-only         # MeshAnchor only (no daemons)
sudo bash scripts/install_noc.sh --force-native        # Force SPI mode (native meshtasticd)
sudo bash scripts/install_noc.sh --force-python        # Force USB mode (Python CLI)
```

### Deployment Profiles

MeshAnchor supports 5 deployment profiles. Install only the dependencies you need.
Order below matches the in-TUI picker (MeshCore-first per the v0.1.0-alpha charter).

| Profile | Primary Use | MeshCore | Meshtastic | RNS | MQTT | Maps | Tactical |
|---------|------------|:--------:|:----------:|:---:|:----:|:----:|:--------:|
| `meshcore` (default) | MeshCore companion radio | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| `radio_maps` | MeshCore + coverage mapping | ✅ | ❌ | ❌ | ❌ | ✅ | ❌ |
| `monitor` | MQTT packet analysis (no radio) | ❌ | ❌ | ❌ | ✅ | ❌ | ❌ |
| `gateway` | MeshCore + Meshtastic/RNS bridge | ✅ | ✅ | ✅ | ✅ | ✅ | ❌ |
| `full` | Everything | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |

```bash
# Select profile at launch
python3 src/launcher.py --profile gateway

# Auto-detect (default): scans running services and installed packages
python3 src/launcher.py

# Profile is saved to ~/.config/meshanchor/deployment.json
```

For the full feature-flag matrix, per-profile install commands, decision tree, and
the rationale behind each default, see
[`.claude/foundations/deployment_profiles.md`](.claude/foundations/deployment_profiles.md).

### Already Have meshtasticd?

```bash
sudo python3 src/launcher_tui/main.py
```

### RF Tools Only (no sudo, no radio)

```bash
python3 src/standalone.py              # Interactive menu
python3 src/standalone.py rf           # RF calculator
python3 src/standalone.py sim          # Network simulator
python3 src/standalone.py --check      # Check dependencies
```

### TUI Menu Structure

The TUI uses a raspi-config style interface (whiptail/dialog) designed for SSH and
headless operation. Navigation is keyboard-driven with max 10 items per menu level:

```
Main Menu (MeshAnchor NOC)
├── 1. Dashboard             Service status, health, alerts, data path check
├── 2. MeshCore              Primary radio + Optional Gateways
│       ├── MeshCore submenu Status, detect, config, radio (LoRa/TX/channels),
│       │                    nodes, stats, chat, daemon control
│       ├── MeshCore CLI     meshcore-cli passthrough — common verbs, DM/channel,
│       │                    remote-admin cmd, interactive REPL (auto daemon handoff)
│       └── Optional Gateways → Meshtastic, RNS, AREDN, MQTT, Gateway Bridge
├── 3. RF & SDR              Link budget, site planner, frequency slots, SDR
├── 4. Maps & Viz            Live NOC map, coverage, topology, traffic inspector,
│                            meshforge-maps :8808 integration
├── 5. Configuration         Radio, channels, RNS config, services, backup
├── 6. System                Hardware detect, logs, network tools, shell, reboot
├── q. Quick Actions         Common shortcuts (2-tap access)
├── e. Emergency Mode        Field ops, weather/EAS alerts, SOS beacon
├── t. Tactical Ops          SITREP, zones, QR, ATAK (visible under FULL profile)
├── a. About                 Version, web client, help
└── x. Exit
```

### Upgrade / Reinstall / Uninstall

```bash
# Quick update (code + service files)
cd /opt/meshanchor && sudo bash scripts/update.sh

# Quick update to specific branch
sudo bash scripts/update.sh --branch main

# Clean reinstall (recommended for major version bumps)
sudo bash /opt/meshanchor/scripts/reinstall.sh

# Clean reinstall without confirmation prompt
sudo bash scripts/reinstall.sh --no-confirm

# Complete removal
sudo bash scripts/reinstall.sh --remove-only

# Manual git pull (developers)
cd /opt/meshanchor && sudo git pull origin main
```

**What is preserved during reinstall** (never touched):

| Preserved | Path | Why |
|-----------|------|-----|
| meshtasticd | apt package + `/etc/meshtasticd/config.yaml` | Separate package, your radio config |
| Radio hardware configs | `/etc/meshtasticd/config.d/` | Backed up and restored |
| Reticulum identity | `~/.reticulum/` | Your RNS address + keys |
| MeshAnchor user settings | `~/.config/meshanchor/` | Backed up and restored |
| MQTT broker | mosquitto service + config | Separate service |
| System packages | pip, apt installs | Not managed by MeshAnchor |

No need to re-image your Pi. Your radio stays configured.

### Post-Install Verification

```bash
# Automated check
sudo python3 src/launcher.py --verify-install

# Manual checks
python3 -c "from src.__version__ import __version__; print(__version__)"
python3 -m pytest tests/ -v --tb=short
sudo python3 src/launcher_tui/main.py
```

### Troubleshooting

| Issue | Solution |
|-------|----------|
| Python import errors | `sudo bash scripts/reinstall.sh` (clean reinstall) |
| `Local changes would be overwritten` | `git stash` before pull, or use clean reinstall |
| Service won't start | `journalctl -u meshanchor -n 50` |
| `meshcore` module not found | `pip install meshcore` (or `--break-system-packages` on Bookworm+) |
| `meshcore-cli not found` in TUI CLI menu | `/opt/meshanchor/venv/bin/pip install meshcore-cli` (or use TUI's *MeshCore CLI → Install / Locate*) |
| USB device path changes on reboot | Install udev rules: `sudo cp scripts/99-meshcore.rules /etc/udev/rules.d/ && sudo udevadm control --reload-rules` |
| Permission denied on serial port | Add user to dialout group: `sudo usermod -aG dialout $USER` then log out/in |
| `meshtastic` module not found | `pip install meshtastic --break-system-packages --ignore-installed` |
| Config file conflicts | Restore from `~/meshanchor-backup-*` or regenerate via TUI |
| Stale `.pyc` files | Clean reinstall handles this automatically |

---

## What Works (v0.1.0-alpha)

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
for the live count) across <!--STAT:testfiles-->200<!--/STAT--> test files. However, automated tests
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
  external testers can close — see [Contributing](#contributing))

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

## Architecture

```mermaid
graph TB
    subgraph User Interfaces
        TUI[Terminal UI<br>SSH-friendly, raspi-config style]
        BROWSER[Browser Maps<br>Live Leaflet.js NOC view]
        CLI[Standalone CLI<br>Zero-dependency RF tools]
    end

    subgraph MeshAnchor Core
        MESHCORE[MeshCore Handler<br>meshcore_py companion radio]
        CANONICAL[CanonicalMessage<br>Protocol-agnostic bridge]
        GATEWAY[Gateway Bridge<br>MQTT transport + SQLite queue]
        MONITOR[MQTT Subscriber<br>Nodeless node tracking]
        RF[RF Engine<br>Link budget, Fresnel, path loss]
        DIAG[Diagnostics<br>Rule engine + knowledge base]
        AI[AI Assistant<br>Standalone + PRO modes]
    end

    subgraph Optional Gateways
        MESHTASTICD[meshtasticd<br>LoRa radio daemon]
        RNSD[rnsd<br>Reticulum transport]
        MQTT[MQTT Broker<br>Node telemetry]
    end

    subgraph Hardware
        MESHCORE_RADIO[MeshCore Radio<br>RAK4631, Heltec V3, T-Deck]
        SPI[SPI HAT<br>Meshtoad, MeshAdv]
        USB[USB Radio<br>Heltec, T-Beam, RAK]
    end

    TUI --> MESHCORE
    TUI --> BROWSER
    CLI --> RF

    MESHCORE --> MESHCORE_RADIO
    MESHCORE --> CANONICAL
    CANONICAL --> GATEWAY
    GATEWAY --> MQTT
    GATEWAY --> RNSD
    MONITOR --> MQTT
    MQTT --> MESHTASTICD
    RF --> TUI
    DIAG --> AI

    MESHTASTICD --> SPI
    MESHTASTICD --> USB

    style MESHCORE fill:#1a5c1a,color:#fff
    style CANONICAL fill:#1a3a5c,color:#fff
    style TUI fill:#2d5016,color:#fff
    style BROWSER fill:#2d5016,color:#fff
    style CLI fill:#2d5016,color:#fff
    style GATEWAY fill:#1a3a5c,color:#fff
    style AI fill:#5c1a3a,color:#fff
    style MESHCORE_RADIO fill:#5c4a1a,color:#fff
```

### Data Flow: MeshCore to Meshtastic/RNS

```mermaid
sequenceDiagram
    participant MC as MeshCore Radio
    participant MH as MeshCore Handler
    participant CM as CanonicalMessage
    participant MR as Message Router
    participant MT as Meshtastic (MQTT)
    participant RN as RNS (LXMF)

    MC->>MH: meshcore_py event
    MH->>CM: Convert to CanonicalMessage
    CM->>MR: Classify + route
    MR->>MT: meshtastic CLI (transient)
    MR->>RN: LXMF via rnsd

    RN->>MR: RNS reply
    MR->>CM: Convert to CanonicalMessage
    CM->>MH: Route to MeshCore
    MH->>MC: meshcore_py send
```

### Key Differences from MeshForge

| | MeshAnchor | MeshForge |
|---|---|---|
| Primary radio | MeshCore | Meshtastic |
| Default profile | `meshcore` | `radio_maps` |
| Bridge direction | MeshCore -> Meshtastic/RNS | Meshtastic -> MeshCore/RNS |
| meshtasticd required? | No (optional gateway) | Yes (primary) |
| meshcore package | Primary dependency | Optional |
| Python version | 3.10+ | 3.9+ |
| Field-tested | Continuous deployment on `meshanchor-server` since 2026-05-02; 24h clean fleet soak 2026-05-11; LXMF bridge cross-federation 2026-05-09 | Yes (beta) |

### Design Principles

- **TUI is a dispatcher** — selects what to run, not how to run it
- **Services run independently** — MeshAnchor connects, never embeds
- **Standard Linux tools** — `systemctl`, `journalctl`, `meshtastic`, `rnstatus`
- **Config overlays** — writes to `config.d/`, never overwrites defaults
- **Graceful degradation** — missing dependencies disable features, don't crash
- **Defense-in-depth** — handler registry dispatches with exception isolation per handler

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

## Hardware

**Minimum:** Raspberry Pi 3B+ + MeshCore companion radio
**Recommended:** Raspberry Pi 4/5 + MeshCore radio (~$35-90)

| Component | Options |
|-----------|---------|
| **Computer** | Raspberry Pi 4/5 (recommended), Pi 3B+, Pi Zero 2W, x86_64 Linux |
| **OS** | Raspberry Pi OS Bookworm 64-bit, Debian 12+, Ubuntu 22.04+ |
| **MeshCore Radio** | See companion radios table below |
| **Optional** | Meshtastic radio (gateway), RTL-SDR (spectrum), GPS module |

### MeshCore Companion Radios

MeshCore companion radios connect via USB serial and are managed by the meshcore_py library.
MeshAnchor auto-detects connected devices.

| Device | Connection | Notes |
|--------|-----------|-------|
| **RAK4631** (nRF52840) | USB Serial | Ultra-low power, GPS, UF2 flashing |
| **Heltec V3** (ESP32-S3) | USB Serial / WiFi TCP | Common, affordable, gateway capable |
| **Heltec V4** (ESP32-S3) | USB Serial | 28dBm TX power |
| **T-Deck** | USB Serial | Built-in keyboard + display |
| **T-Echo** | USB Serial | E-ink display, GPS |
| **Station G2** | USB Serial | Gateway capable, PoE option |

### Meshtastic Gateway Radios (Optional)

For the `gateway` or `full` deployment profile, you also need a Meshtastic radio.
MeshAnchor supports all radios that MeshForge supports — see the
[MeshForge hardware tables](https://github.com/Nursedude/meshforge#hardware) for
SPI HATs (MeshAdv, Waveshare, Ebyte, RAK) and USB radios (Heltec, T-Beam, MeshToad).

Pre-built gateway profiles are available in `src/gateway/profiles/`:

| Profile | Radio | Use Case |
|---------|-------|----------|
| `heltec_v3_gateway.yaml` | Heltec V3 | Standard USB gateway |
| `heltec_v4_gateway.yaml` | Heltec V4 | High-power USB gateway |
| `rak4631_gateway.yaml` | RAK4631 | Low-power gateway |
| `station_g2_gateway.yaml` | Station G2 | Infrastructure gateway |
| `rnode_gateway.yaml` | RNode | Dedicated RNS radio |

---

## Project History

MeshAnchor shares 2,848 commits of history with MeshForge. Here's how we got here:

### Origins (December 2025)

The project started as **"Meshtasticd Interactive UI"** — a simple interactive installer
and manager for meshtasticd on Raspberry Pi. Initial commit: 2025-12-27.

### MeshForge Era (January - March 2026)

The installer rapidly evolved into a full Network Operations Center:

| Version | Date | Milestone |
|---------|------|-----------|
| v0.4.0-beta | 2026-01-10 | Initial public release — GTK4 desktop + Meshtastic-RNS gateway |
| v0.4.1-beta | 2026-01-12 | TUI interface added (raspi-config style) |
| v0.4.2-beta | 2026-01-13 | RF tools — FSPL, Fresnel zone, link budget |
| v0.4.6-beta | 2026-01-17 | AI diagnostics, coverage maps, CI/CD |
| v0.4.7-beta | 2026-01-17 | Regression prevention system, predictive analytics |
| v0.5.0-beta | 2026-02-01 | Beta milestone — TUI stable across 6+ fresh installs |
| v0.5.1-beta | 2026-02-06 | Telemetry pipeline, RNS sniffer, Path.home() security fixes |
| v0.5.3-beta | 2026-02-08 | 350+ unit tests for core gateway bridge |
| v0.5.4-beta | 2026-02-11 | MQTT bridge architecture (zero interference with web client) |
| v0.5.5-beta | 2026-03-09 | MeshChat removed, mesh alert engine, focus narrowed |

During this period, MeshCore support was explored on an alpha branch with a 796-line
`meshcore_handler.py`, a `CanonicalMessage` protocol format for N-protocol bridging,
and 1,839 tests for tri-bridge routing.

### The Split (April 1, 2026)

MeshAnchor was **extracted from MeshForge main** at commit `7e4fa02`. The extraction:

- Rebranded 580+ references from MeshForge to MeshAnchor
- Renamed all service/config files (`meshforge.service` -> `meshanchor.service`)
- Created `RadioMode` abstraction (`src/core/radio_mode.py`)
- **Inverted the architecture**: MeshCore enabled by default, Meshtastic optional
- Inverted deployment profiles: `meshcore` is the default (was `radio_maps`)
- Removed 16 Meshtastic-specific source files and 8 Meshtastic-specific test files
- Gated remaining Meshtastic imports as optional gateway support
- Updated CI for Python 3.10+, meshcore package
- Version reset to **0.1.0-alpha**

### Today

Two sister projects with the same DNA, different home radios:

```
MeshForge (v0.6.2-beta)           MeshAnchor (v0.1.0-alpha)
  Primary: Meshtastic               Primary: MeshCore
  Gateway to: MeshCore/RNS          Gateway to: Meshtastic/RNS
  Field-tested: Yes                  Field-tested: One box — needs YOU
```

---

## Ecosystem

MeshAnchor is part of a 5-repository ecosystem:

| Repository | Purpose | Version |
|------------|---------|---------|
| **[meshanchor](https://github.com/Nursedude/meshanchor)** (this repo) | MeshCore-primary NOC — gateway, TUI, RF tools, diagnostics | 0.1.0-alpha |
| **[meshforge](https://github.com/Nursedude/meshforge)** | Meshtastic-primary NOC — same architecture, different home radio | 0.6.2-beta |
| **meshanchor-maps** | Visualization plugin — Leaflet/D3.js topology, health scoring | 0.7.0 |
| **meshing_around_meshanchor** | Bot alerting — 12 alert types, complements meshing-around bot | 0.5.0 |
| **[RNS-Management-Tool](https://github.com/Nursedude/RNS-Management-Tool)** | Cross-platform RNS ecosystem installer | 0.3.2 |

### Shared Components

Both MeshForge and MeshAnchor share these core components. Changes to the shared
contract must stay compatible across both projects:

- **CanonicalMessage** (`src/gateway/canonical_message.py`) — the protocol-agnostic
  message format that bridges all three radio protocols
- **TUI handler architecture** — Protocol + BaseHandler + HandlerRegistry dispatch
- **RF tools** (`src/utils/rf.py`) — link budget, Fresnel, FSPL, coverage maps
- **Gateway bridge pattern** — adapter -> CanonicalMessage -> message router
- **RNS substrate** (`src/utils/rns_init.py` + the `requirements/rns.txt` fork pin) —
  RNS and LXMF are installed from MeshForge-owned hard forks
  ([Nursedude/reticulum](https://github.com/Nursedude/reticulum),
  [Nursedude/lxmf](https://github.com/Nursedude/lxmf)), pinned by tag + SHA
  (`MF-FORK-PIN` lines). MeshForge is the lead repo for this substrate; its
  `scripts/parity_check.py` keeps the shared files byte-identical across both projects

### Which Should You Run?

- **MeshForge** — if Meshtastic is your primary radio
- **MeshAnchor** — if MeshCore is your primary radio
- **Both** — if you operate both radio types on separate Pis

---

## Project Structure

```
src/
├── launcher_tui/          # PRIMARY INTERFACE (TUI)
│   ├── main.py            # NOC launcher + handler registration
│   ├── handler_protocol.py  # CommandHandler Protocol + TUIContext + BaseHandler
│   ├── handler_registry.py  # register/lookup/dispatch
│   ├── backend.py           # whiptail/dialog abstraction
│   ├── startup_checks.py   # Environment checks + conflict resolution
│   ├── status_bar.py       # Service status bar
│   └── handlers/            # 85 registered command handlers
├── daemon.py              # MeshCore radio daemon (chat API + /radio endpoints on :8081)
├── gateway/               # Multi-protocol bridge engine
│   ├── meshcore_handler.py   # MeshCore companion radio (meshcore_py) — PRIMARY
│   ├── rns_bridge.py        # RNS/LXMF gateway (optional)
│   ├── mqtt_bridge_handler.py # Meshtastic MQTT bridge (optional)
│   ├── canonical_message.py  # Protocol-agnostic message format
│   ├── message_routing.py   # 3-way routing classifier
│   ├── message_queue.py     # SQLite persistent queue
│   ├── circuit_breaker.py   # Fault tolerance
│   └── profiles/            # 5 gateway radio profiles (Heltec, RAK, RNode, Station G2)
├── monitoring/            # Network monitoring
│   ├── mqtt_subscriber.py   # Nodeless MQTT node tracking
│   ├── traffic_inspector.py # Packet capture + protocol analysis
│   ├── rns_sniffer.py      # RNS packet capture + announce tracking
│   └── path_visualizer.py  # Multi-hop path tracing
├── commands/              # Command modules (propagation, hamclock, service mgmt)
├── utils/                 # Core utilities
│   ├── rf.py              # RF calculations (haversine, FSPL, Fresnel, link budget)
│   ├── coverage_map.py    # Folium map generator + offline tile cache
│   ├── service_check.py   # Service management (single source of truth)
│   ├── diagnostic_engine.py # Rule-based diagnostics
│   ├── claude_assistant.py  # AI assistant (Standalone + PRO)
│   ├── prometheus_exporter.py # Metrics pipeline
│   ├── active_health_probe.py # Health probes (RNS wedge, delivery stall, drift checks)
│   ├── rns_init.py        # Guarded RNS chokepoint (shared with MeshForge, fork-pinned)
│   └── paths.py           # Sudo-safe path resolution
├── tactical/              # Tactical operations, QR transport, compliance
├── core/                  # RadioMode, orchestrator, plugin system
├── standalone.py          # Zero-dependency RF tools
└── __version__.py         # Version: 0.1.0-alpha

scripts/
├── install_noc.sh         # Full NOC stack installer
├── update.sh              # In-place code update
├── reinstall.sh           # Clean reinstall (preserves config)
├── lint.py                # Security linter (17 rules: MF001-MF014, MF016, MA017, MF019-MF020)
├── meshanchor-launcher.sh # Shell wrapper
└── verify_post_install.sh # Post-install health check

dashboards/                # 5 Grafana monitoring dashboards
├── meshanchor-overview.json  # Health, services, queues
├── meshanchor-nodes.json     # Per-node RF metrics
├── meshanchor-gateway.json   # Gateway bridge status
├── meshanchor-infinity.json  # Long-term trends
└── meshanchor-influxdb.json  # InfluxDB integration

templates/                 # Config templates (meshtasticd, reticulum, MQTT, systemd)
config_templates/          # RNS gateway configuration templates
tests/                     # <!--STAT:testfiles-->200<!--/STAT--> test files, ~5,900 tests
docs/                      # REST API, metrics, usage guide, visual guide
examples/                  # Example configurations
web/                       # Node map, LOS visualization (browser)
```

---

## Configuration

### meshtasticd (Optional — Gateway Profile Only)

MeshAnchor writes hardware config overlays (never overwrites defaults):

```
/etc/meshtasticd/
├── config.yaml                    # Package default (DO NOT EDIT)
└── config.d/
    ├── lora-*.yaml                # Hardware config (SPI pins, module)
    └── meshanchor-overrides.yaml  # Custom overrides
```

LoRa modem presets and frequency slots are applied via the meshtastic
CLI (`--set lora.modem_preset`, `--set lora.channel_num`), not config.d.

### Reticulum

Auto-deploys a working config from `templates/reticulum.conf`:
- AutoInterface (LAN discovery)
- Meshtastic Interface on `127.0.0.1:4403` (if gateway profile)
- RNode LoRa (optional, for dedicated RNS radio)

Gateway-specific templates in `config_templates/`:
- `rns_meshtastic_gateway.conf` — full gateway with Meshtastic interface
- `rns_minimal.conf` — minimal config for MeshCore-only operation

### Ports

| Port | Service | Owner | Notes |
|------|---------|-------|-------|
| 4403 | meshtasticd TCP API | meshtasticd | Optional (gateway only), single client limit |
| 9443 | meshtasticd Web UI | meshtasticd | Optional (gateway only) |
| 1883 | MQTT broker | mosquitto | Optional, multi-consumer |
| 5000 | Map Server | **MeshAnchor** | Live NOC map + REST API |
| 5001 | WebSocket | **MeshAnchor** | Real-time message broadcast |
| 8081 | Config API | **MeshAnchor** | RESTful config management |
| 9090 | Prometheus metrics | **MeshAnchor** | Grafana-compatible JSON API |

---

## Code Health

### Test Coverage

**~5,900 tests** across <!--STAT:testfiles-->200<!--/STAT--> test files. Top suites by depth
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

See [CLAUDE.md](CLAUDE.md) for the complete development guide.

---

## Development

### Branch Strategy

| Branch | Version | Focus |
|--------|---------|-------|
| `main` | `0.1.0-alpha` | MeshCore-primary NOC, alpha testing |

**Sister project:** [MeshForge](https://github.com/Nursedude/meshforge) is the
Meshtastic-primary NOC — extracted from the same codebase on 2026-04-01.

Feature branches use `claude/` prefix, merged via PR to main. Dependabot
dependency PRs auto-merge once CI is green.

**Shared contract:** `CanonicalMessage` in `src/gateway/canonical_message.py` must
stay compatible with MeshForge's version. Changes to the message format should be
coordinated across both projects.

---

## Research & Technical Foundation

MeshAnchor development is backed by 22+ technical research documents covering
protocol analysis, integration architecture, and RF engineering. These inform
every major design decision in the codebase.

### Multi-Protocol Bridging
- MeshCore <> Meshtastic dual-protocol bridge architecture (3-way routing design)
- MeshCore reliability patterns: canonical packet format, MQTT origin filtering, lenient parsing
- Gateway scenario analysis: multi-protocol deployment topologies and trade-offs

### RF & Physical Layer
- LoRa PHY deep-dive: CSS modulation, spreading factors, SNR limits, link budget calculations
- Official Semtech LoRa reference data for engineering-grade RF planning

### Protocol Documentation
- Complete Reticulum/RNS protocol documentation, configuration guides, and integration patterns
- AREDN mesh network integration research

### Tactical Operations
- XTOC/XCOM integration: X1 compact packet protocol, structured message templates
- ATAK ecosystem research: CoT XML, PLI, GeoChat, KML/CoT export

### Architecture & Infrastructure
- MQTT zero-interference bridging design (foundation of the gateway architecture)
- NGINX reliability patterns applied to mesh networking APIs

Full research library: [`.claude/research/`](.claude/research/README.md)

---

## Resources

| Resource | Link | Relation |
|----------|------|----------|
| MeshCore | [meshcore.co](https://meshcore.co/) | Primary radio protocol |
| Meshtastic | [meshtastic.org/docs](https://meshtastic.org/docs/) | Optional gateway radio |
| Reticulum | [reticulum.network](https://reticulum.network/) | Optional gateway (encrypted transport) |
| MeshForge | [github.com/Nursedude/meshforge](https://github.com/Nursedude/meshforge) | Sister project (Meshtastic-primary) |
| Development Blog | [nursedude.substack.com](https://nursedude.substack.com) | Project updates |

---

## License

GPL-3.0 — See [LICENSE](LICENSE)

---

<p align="center">
  <strong>MeshAnchor</strong><br>
  <em>Made with aloha for the mesh community</em><br>
  WH6GXZ | Hawaii
</p>
