# Project History and Ecosystem

Where MeshAnchor came from, how it relates to MeshForge, and the research it rests on.

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

Full research library: [`.claude/research/`](../.claude/research/README.md)

---

