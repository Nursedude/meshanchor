# MeshAnchor

<p align="center">
  <strong>MeshCore Network Operations Center</strong><br>
  <em>Anchor. Bridge. Monitor.</em>
</p>

<p align="center">
  <a href="https://github.com/Nursedude/meshanchor"><img src="https://img.shields.io/badge/version-0.3.0--beta-blue.svg" alt="Version"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-GPL--3.0-green.svg" alt="License"></a>
  <a href="https://python.org"><img src="https://img.shields.io/badge/python-3.10+-yellow.svg" alt="Python"></a>
  <a href="https://github.com/Nursedude/meshanchor"><img src="https://img.shields.io/badge/tests-passing-blue.svg" alt="Tests"></a>
</p>

<p align="center">
  <a href="https://wh6gxznursedude.substack.com">Development Blog</a> |
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
scripts/meshanchor-launcher.sh
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
> Added 2026-08-29: the **directed-DM reply leg** — an `@<contact>` reply from
> Meshtastic or RNS delivered as a MeshCore DM with a path-ACK, proven live
> end-to-end. That is a message *round-trip* across all three networks; it is
> **not** the same claim as sustained concurrent 3-way traffic, which remains
> unproven.
>
> **Not yet validated:** coverage maps with live GPS position data; sustained
> 3-way (MeshCore ↔ Meshtastic ↔ RNS) *concurrent* traffic (the tri-bridge
> tests are mock-only); and the `repliable_contacts` capture path — the live
> proof ran on a radio with firmware auto-add ON, so the capture mixin itself
> is exercised only by tests. If you have a MeshCore
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
| **Directed DM replies** | `@<contact> text` from Meshtastic or RNS lands as a MeshCore **DM**, not a channel broadcast — addressed by exact adv_name or pubkey prefix, with `[MC:reply]` notices and a `dm_replies_enabled` kill switch | MeshCore radio + a bridged network |
| **Reticulum (RNS)** | Encrypted transport, LXMF messaging, propagation, reliability tiers | Network or radio |
| **Live NOC maps** | Leaflet browser view, WebSocket updates, MeshCore map population + operator pins | Position data |
| **meshforge-maps** | Discovery, browser launch, bidirectional data fusion on `:8808` | [MeshForge Maps](https://github.com/Nursedude/meshforge-maps) |
| **MQTT monitoring** | Nodeless mesh observation, protobuf decode, traffic inspector | MQTT broker |
| **RF engineering** | Link budget, Fresnel zone, FSPL, coverage maps, NOAA space weather | — |
| **AI diagnostics** | Offline knowledge base; optional AI assist with your own Claude API key or subscription | — |
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
sudo git clone https://github.com/Nursedude/meshanchor.git /opt/meshanchor
cd /opt/meshanchor
sudo bash scripts/install_noc.sh       # guided install
scripts/meshanchor-launcher.sh  # the NOC
```

Runs on a Raspberry Pi with a MeshCore companion radio (RAK4631, Heltec V3,
T-Deck, or T-Echo). The reference box is a Pi 4B with a RAK Heltec V3 in Serial
Companion mode.

Clone into `/opt/meshanchor`: the installer and every service run from there,
so a checkout anywhere else is not the code that gets installed.

**A fresh gateway needs three settings** in `~/.config/meshanchor/gateway.json`
(MeshAnchor [#151](https://github.com/Nursedude/meshanchor/issues/151)):

- `"enabled": true` — off by default; while off, the gateway logs
  `Routing ingress policy: CLOSED` and bridges nothing.
- `"meshtastic": {"http_tls": false}` — only if your radio serves plain HTTP on
  a port other than 80; unset, port 80 is plain HTTP and every other port TLS.
- `"mqtt_bridge": {"region": ""}` — leave empty if your firmware publishes
  `msh/2/json/...` with no region; both topic shapes are subscribed either way.

**Full instructions — hardware, install, first run: [docs/install.md](docs/install.md)**

---

## What Works (v0.3.0-beta)

Each line carries its status from [docs/capabilities.md](docs/capabilities.md)
(**Field-Validated** = run on real hardware; **Inherited** = field-tested in
MeshForge, untested here; **Code-Ready** = implemented, awaiting hardware).

- **MeshCore integration** *(Field-Validated, MeshCore-only path, 2026-05-02)* —
  radio management, config, CLI passthrough
- **Directed DM replies** *(Field-Validated 2026-08-29)* — `@<contact>` from
  Meshtastic/RNS → MeshCore DM
- **LXMF broadcast bridge + subscriber reliability** *(Field-Validated
  2026-05-09 / 05-12)* — subscribe/fan-out, tier engine, Prometheus metrics
- **Fleet observability** *(Field-Validated)* — collector + watchdog, 24h
  clean-soak passed
- **Gateway bridge** *(Inherited + Code-Ready)* — MeshCore ⇄ Meshtastic / RNS
  via `CanonicalMessage`; an outside install bridged all six directions after
  the #151 fixes (with local patches)
- **Maps** *(Inherited + MeshCore population)* — MeshCore map population,
  operator pins, meshforge-maps fusion
- **RF engineering** *(Inherited)* — link budget, Fresnel, FSPL, space weather
- **AI diagnostics** *(Inherited)* — offline knowledge base; optional AI assist
  with your own Claude API key (in-TUI assistant) or subscription (Claude Code
  in the repo) — [how](docs/capabilities.md#ai-assist--optional-and-yours-to-configure)

The test suite (live count: `python3 -m pytest tests/ --co -q`) spans <!--STAT:testfiles-->353<!--/STAT--> test files.

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
| Development Blog | [wh6gxznursedude.substack.com](https://wh6gxznursedude.substack.com) | Project updates |

---

## License

GPL-3.0 — See [LICENSE](LICENSE), with an additional permission under GPL §7 for the
Reticulum (RNS) and LXMF libraries, which use the Reticulum License — see
[LICENSE-EXCEPTION.md](LICENSE-EXCEPTION.md) and [ATTRIBUTION.md](ATTRIBUTION.md).

---

<p align="center">
  <strong>MeshAnchor</strong><br>
  <em>Made with aloha for the mesh community</em><br>
  WH6GXZ | Hawaii
</p>
