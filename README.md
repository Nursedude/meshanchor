# MeshAnchor

<p align="center">
  <strong>MeshCore Network Operations Center</strong><br>
  <em>Anchor. Bridge. Monitor.</em>
</p>

<p align="center">
  <a href="https://github.com/Nursedude/meshanchor"><img src="https://img.shields.io/badge/version-0.1.0--alpha-orange.svg" alt="Version"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-GPL--3.0-green.svg" alt="License"></a>
  <a href="https://python.org"><img src="https://img.shields.io/badge/python-3.10+-yellow.svg" alt="Python"></a>
</p>

<p align="center">
  <a href="https://nursedude.substack.com">Development Blog</a> |
  <a href="https://github.com/Nursedude/meshanchor/issues">Report Issues</a> |
  <a href="https://github.com/Nursedude/meshforge">Sister Project: MeshForge</a>
</p>

---

## What is MeshAnchor?

**MeshAnchor is a MeshCore-primary Network Operations Center** — the sister project to [MeshForge](https://github.com/Nursedude/meshforge).

Where MeshForge treats Meshtastic as the "home" radio, MeshAnchor flips the architecture: **MeshCore is primary, Meshtastic is an optional gateway.**

Same TUI framework. Same gateway bridge architecture. Same RF tools. Different home radio.

> **Status: Alpha** — MeshCore gateway integration has not been field-tested yet. Use at your own risk.

---

## Architecture

```
                    MeshAnchor NOC
                    ┌─────────────────────┐
MeshCore Radio ◄──►│  MeshCore Handler   │
  (Primary)        │  (meshcore_py)      │
                    │                     │
                    │  CanonicalMessage   │◄──► RF Tools, Maps, Monitoring
                    │  (protocol bridge)  │
                    │                     │
Meshtastic   ◄────►│  Meshtastic Handler │
  (Gateway)        │  (optional)         │
                    │                     │
RNS/LXMF    ◄────►│  RNS Bridge         │
  (Gateway)        │  (optional)         │
                    └─────────────────────┘
```

### Key Differences from MeshForge

| | MeshAnchor | MeshForge |
|---|---|---|
| Primary radio | MeshCore | Meshtastic |
| Default profile | `meshcore` | `radio_maps` |
| Bridge direction | MeshCore -> Meshtastic/RNS | Meshtastic -> MeshCore/RNS |
| meshtasticd required? | No (optional gateway) | Yes (primary) |
| meshcore package | Primary dependency | Optional |

---

## Quick Start

```bash
# Clone
git clone https://github.com/Nursedude/meshanchor.git /opt/meshanchor
cd /opt/meshanchor

# Install MeshCore library
pip install meshcore

# Launch TUI
sudo python3 src/launcher_tui/main.py
```

### Requirements

- Python 3.10+
- Linux (Raspberry Pi recommended)
- MeshCore companion radio (RAK4631, Heltec V3, T-Deck, T-Echo, etc.)
- `pip install meshcore` for radio communication

### Optional (for gateway bridging)

- `meshtasticd` — for Meshtastic gateway bridge
- `rnsd` — for Reticulum/LXMF gateway bridge
- `mosquitto` — for MQTT monitoring

---

## Deployment Profiles

```bash
python3 src/launcher.py --profile meshcore    # Default: MeshCore only
python3 src/launcher.py --profile gateway     # MeshCore + Meshtastic/RNS bridge
python3 src/launcher.py --profile full        # Everything
python3 src/launcher.py                       # Auto-detect
```

---

## Relationship to MeshForge

MeshAnchor was forked from [MeshForge](https://github.com/Nursedude/meshforge) on 2026-04-01. Both share:

- **CanonicalMessage** protocol format (the bridge between all three radio protocols)
- **TUI handler architecture** (Protocol + BaseHandler + registry dispatch)
- **RF tools** (link budget, coverage maps, propagation)
- **Gateway bridge pattern** (adapter -> canonical message -> router)

They differ in which radio is "home." If you're building around Meshtastic, use MeshForge. If you're building around MeshCore, use MeshAnchor.

---

## License

GPL-3.0 — see [LICENSE](LICENSE)

---

*Made with aloha for the mesh community* -- WH6GXZ
