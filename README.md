# MeshAnchor

<p align="center">
  <strong>MeshCore Network Operations Center</strong><br>
  <em>Anchor. Bridge. Monitor.</em>
</p>

<p align="center">
  <a href="https://github.com/Nursedude/meshanchor"><img src="https://img.shields.io/badge/version-0.2.0--beta-blue.svg" alt="Version"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-GPL--3.0-green.svg" alt="License"></a>
  <a href="https://python.org"><img src="https://img.shields.io/badge/python-3.10+-yellow.svg" alt="Python"></a>
  <a href="https://github.com/Nursedude/meshanchor"><img src="https://img.shields.io/badge/tests-passing-blue.svg" alt="Tests"></a>
</p>

<p align="center">
  <a href="https://nursedude.substack.com">Development Blog</a> |
  <a href="https://github.com/Nursedude/meshanchor/issues">Report Issues</a> |
  <a href="docs/development.md">Contribute</a> |
  <a href="https://github.com/Nursedude/meshforge">Sister Project: MeshForge</a>
</p>

---

## What is MeshAnchor?

**MeshAnchor takes a MeshCore radio and gives it somewhere to go.**

MeshCore brings 64-hop routing (vs Meshtastic's 7) and a flood-free mesh
topology — but no operational tooling. MeshAnchor is that tooling: manage the
radio, watch the network, put it on a map, and **bridge it out to Reticulum, to
Meshtastic, or to whatever else your site runs.**

It runs on **one box**. No cloud, no subscriptions, no account.

```bash
sudo python3 src/launcher_tui/main.py
```

**Built for:** MeshCore developers, RF engineers, network operators, amateur
radio operators, and emergency comms planners.

> **BETA — field-deployed since 2026-05-02 and accumulating mileage. More testers wanted.**
>
> The canonical NOC (Pi 4B + RAK Heltec V3 in Serial Companion mode) has run as
> a continuous deployment since 2026-05-02 and is the source of truth for field
> validation. Proven: bidirectional MeshCore channel messaging, MeshCore↔RNS
> LXMF broadcast bridge (same-host and cross-federation), the LXMF reliability
> tier engine, the fleet observability stack, MeshCore map population, and
> `meshcore-cli` passthrough with daemon hand-off.
>
> **Not yet validated:** coverage maps with live GPS position data, and full
> 3-way (MeshCore ↔ Meshtastic ↔ RNS) concurrent traffic. If you have a MeshCore
> companion radio (RAK4631, Heltec V3, T-Deck, T-Echo), independent field
> testing on different hardware is the single most valuable contribution right
> now — see [Contributing](docs/development.md).

---

## Composable by design

MeshAnchor is not one monolithic install. **MeshCore is the home radio; every
other protocol is an optional gateway you switch on if your site needs it.**
Run the radio alone, or bridge it to two other meshes — the combination is
yours.

| Component | What it does | Needs |
|-----------|--------------|-------|
| **MeshCore radio** | Companion-radio management via meshcore_py, pre-flight device validation, persistent udev naming, in-TUI LoRa/channel/TX-power config with region-aware validation | MeshCore radio |
| **MeshCore CLI passthrough** | Drops you into [meshcore-cli](https://github.com/meshcore-dev/meshcore-cli) — DMs, channels, remote-admin `cmd`, REPL — handing off radio ownership cleanly so the bridge resumes on exit | MeshCore radio |
| **Gateway bridge** | Bidirectional MeshCore ⇄ Meshtastic / RNS routing via `CanonicalMessage` | Target network |
| **Reticulum (RNS)** | Encrypted transport, LXMF messaging, propagation, reliability tiers | Network or radio |
| **Live NOC maps** | Leaflet browser view, WebSocket updates, MeshCore map population + operator pins | Position data |
| **meshforge-maps** | Discovery, browser launch, bidirectional data fusion on `:8808` | [MeshForge Maps](https://github.com/Nursedude/meshforge-maps) |
| **MQTT monitoring** | Nodeless mesh observation, protobuf decode, traffic inspector | MQTT broker |
| **RF engineering** | Link budget, Fresnel zone, FSPL, coverage maps, NOAA space weather | — |
| **AI diagnostics** | Offline knowledge base, optional Claude PRO tier | — |
| **Prometheus / Grafana** | 50+ metrics, 5 pre-built dashboards | — |

Optional add-on, installed from the TUI when you want it: **NomadNet**, a
terminal LXMF client for Reticulum users.

### Sister project

**[MeshForge](https://github.com/Nursedude/meshforge)** is the same NOC with the
radios inverted — Meshtastic primary, MeshCore optional. Same TUI framework,
same gateway-bridge pattern, same RF tools, same reliability spine. Pick the one
whose home radio matches yours; they interoperate.

---

## Quick start

```bash
git clone https://github.com/Nursedude/meshanchor.git
cd meshanchor
sudo bash scripts/install_noc.sh       # guided install
sudo python3 src/launcher_tui/main.py  # the NOC
```

Runs on a Raspberry Pi with a MeshCore companion radio (RAK4631, Heltec V3,
T-Deck, or T-Echo). The reference box is a Pi 4B with a RAK Heltec V3 in Serial
Companion mode.

**Full instructions — hardware, install, first run: [docs/install.md](docs/install.md)**

---

## What Works (v0.2.0-beta)

- **MeshCore integration** — radio management, config, CLI passthrough
- **Gateway bridge** — MeshCore ⇄ Meshtastic / RNS via `CanonicalMessage`
- **LXMF reliability tiers** — subscriber engine with Prometheus metrics
- **Fleet observability** — collector + watchdog, 24h clean-soak passed
- **Maps** — MeshCore map population, operator pins, meshforge-maps fusion
- **RF engineering** — link budget, Fresnel, FSPL, space weather
- **AI diagnostics** — offline knowledge base, optional Claude tier

~5,900 tests across <!--STAT:testfiles-->217<!--/STAT--> test files (run
`python3 -m pytest tests/ --co -q` for the live count).

**Full inventory, with proven vs. unvalidated called out:
[docs/capabilities.md](docs/capabilities.md)**

---

## Documentation

| Doc | What's in it |
|-----|--------------|
| [Install](docs/install.md) | Hardware, install, first run |
| [Capabilities](docs/capabilities.md) | Full feature inventory, AI tiers |
| [Architecture](docs/architecture.md) | How the pieces fit; where the code lives |
| [Configuration](docs/configuration.md) | Every knob and its file |
| [Development](docs/development.md) | Tests, gates, contributing |
| [Project history](docs/project-history.md) | Origins, ecosystem, research foundation |

---

## Resources

| Resource | Link | Relation |
|----------|------|----------|
| MeshCore | [meshcore.co](https://meshcore.co/) | Primary radio network |
| meshcore-cli | [github.com/meshcore-dev/meshcore-cli](https://github.com/meshcore-dev/meshcore-cli) | CLI passthrough target |
| Reticulum Network | [reticulum.network](https://reticulum.network/) | Bridge target (encrypted transport) |
| Meshtastic Docs | [meshtastic.org/docs](https://meshtastic.org/docs/) | Optional gateway |
| MeshForge | [github.com/Nursedude/meshforge](https://github.com/Nursedude/meshforge) | Sister project, Meshtastic-primary |
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
