# MeshForge Architecture Overview

**Version**: 0.5.4-beta
**Updated**: 2026-02-22
**Interface**: TUI (Terminal UI — sole interface)

---

## Executive Summary

MeshForge is a mesh network operations center and development ecosystem spanning 5 repositories. The core NOC runs on a Raspberry Pi via a TUI interface, bridging Meshtastic (LoRa), Reticulum (encrypted transport), and MeshCore (alpha) mesh networks.

```
Python Files:   274+
Total Lines:    153,000+
Test Files:     50
Tests:          1,743 (100% pass rate)
Security Rules: 4 enforced (MF001-MF004), 0 violations
```

---

## Architecture Diagram

```
┌──────────────────────────────────────────────────────────────────┐
│                     MESHFORGE NOC (v0.5.4-beta)                   │
├──────────────────────────────────────────────────────────────────┤
│                                                                    │
│  ┌──────────────────────────────────────────────────────┐        │
│  │              TUI (whiptail/dialog)                     │        │
│  │         46 mixin dispatch loops, _safe_call           │        │
│  │    Viewer Mode (no sudo) | Admin Mode (sudo)          │        │
│  └───────────────────────┬──────────────────────────────┘        │
│                          │                                        │
│            ┌─────────────┼─────────────┐                         │
│            │             │             │                          │
│            ▼             ▼             ▼                          │
│  ┌──────────────┐ ┌──────────┐ ┌──────────────┐                │
│  │   Gateway    │ │ RF Tools │ │  Monitoring  │                │
│  │   Bridge     │ │ Coverage │ │  Diagnostics │                │
│  │   (MQTT)     │ │ Maps     │ │  AI/KB       │                │
│  └──────┬───────┘ └──────────┘ └──────────────┘                │
│         │                                                         │
│  ┌──────┴─────────────────────────────────────────────┐         │
│  │              Service Layer (systemd)                  │         │
│  │  meshtasticd ─── mosquitto ─── rnsd ─── MeshCore     │         │
│  └──────────────────────────────────────────────────────┘         │
│                                                                    │
└──────────────────────────────────────────────────────────────────┘

External Services (independent, not embedded):
  Meshtastic LoRa ◄──► MQTT (mosquitto:1883) ◄──► Gateway Bridge
  Reticulum/RNS   ◄──► LXMF transport        ◄──► Gateway Bridge
  MeshCore (alpha) ◄──► meshcore_py           ◄──► MeshCore Handler
  AREDN            ◄──► Service discovery      ◄──► Code-ready
```

---

## Module Architecture

```
src/
├── launcher_tui/              # Terminal UI — PRIMARY INTERFACE
│   ├── main.py                # NOC dispatcher (1,475 lines, 46 mixins)
│   ├── meshcore_mixin.py      # MeshCore TUI menu (alpha branch)
│   ├── rns_config_mixin.py    # RNS config editor
│   └── rns_diagnostics_mixin.py # RNS diagnostics
│
├── gateway/                   # Protocol bridging
│   ├── rns_bridge.py          # Main Meshtastic↔RNS bridge (1,570 lines)
│   ├── gateway_cli.py         # Headless CLI helpers
│   ├── message_queue.py       # Persistent SQLite queue
│   ├── node_tracker.py        # Unified node discovery (975 lines)
│   ├── meshcore_handler.py    # MeshCore protocol handler (alpha, 796 lines)
│   ├── canonical_message.py   # Multi-protocol message format (alpha)
│   ├── meshcore_bridge_mixin.py # MeshCore bridge mixin (alpha)
│   └── message_routing.py     # 3-way routing classifier (alpha)
│
├── commands/                  # Command modules
│   ├── propagation.py         # Space weather (NOAA primary)
│   ├── hamclock.py            # HamClock client (legacy/optional)
│   └── base.py                # CommandResult base class
│
├── monitoring/                # Network monitoring
│   └── mqtt_subscriber.py     # Nodeless MQTT monitoring
│
├── plugins/                   # Protocol plugins
│   └── meshcore.py            # MeshCore plugin (alpha)
│
├── utils/                     # Shared utilities
│   ├── rf.py                  # RF calculations (well-tested)
│   ├── rf_fast.pyx            # Cython optimization
│   ├── common.py              # SettingsManager
│   ├── service_check.py       # Service management (1,415 lines, SSOT)
│   ├── diagnostic_engine.py   # Rule-based diagnostics
│   ├── knowledge_base.py      # 20+ topic knowledge base
│   ├── coverage_map.py        # Folium map generator
│   ├── claude_assistant.py    # AI assistant
│   └── auto_review.py         # Self-audit system
│
├── standalone.py              # Zero-dependency RF tools
└── __version__.py             # Version and changelog
```

---

## Data Flow (v0.5.4+)

```
Meshtastic Radio
    ↓ (protobuf)
meshtasticd
    ↓ (MQTT publish)
mosquitto (localhost:1883, topic: msh/#)
    ↓ (MQTT subscribe)
MeshForge Gateway Bridge
    ├─→ Message Queue (SQLite, persistent)
    ├─→ Node Tracker (unified Meshtastic + RNS)
    ├─→ RNS Bridge (LXMF transport → rnsd → RNS network)
    ├─→ Coverage Maps (Folium, SNR-based coloring)
    ├─→ Traffic Inspector (packet capture, protocol tree)
    └─→ Prometheus Metrics (port 9090)
```

---

## Key Design Decisions

### Service Independence
MeshForge connects to services — it never embeds them. meshtasticd, rnsd, mosquitto, and MeshCore all run independently under systemd.

### Privilege Separation
- **Viewer Mode** (no sudo): Monitoring, RF calculations, API data, diagnostics
- **Admin Mode** (sudo): Service control, /etc/ config, hardware management

### MQTT Bridge Architecture (v0.5.4)
Replaced direct TCP:4403 connection with MQTT transport. The gateway subscribes to meshtasticd's MQTT topics and sends outbound via CLI. This eliminates the port-locking issue that prevented the web client from running simultaneously.

### Plugin Discovery
Satellite repos (meshforge-maps, meshing_around_meshforge) use `manifest.json` for auto-detection. The NOC discovers plugins at startup but never requires them.

---

## API Surface

42+ REST endpoints across four services:

| Service | Port | Endpoints | Purpose |
|---------|------|-----------|---------|
| Map Server | 5000 | 14 | Node maps, topology, GeoJSON |
| Prometheus | 9090 | 5 | Metrics scraping |
| Config API | 8081 | 5 | Remote configuration |
| Protobuf | 9443 | Multiple | Device/module config via meshtasticd |

---

## Security Model

Four enforced rules (linter + auto-review):

| Rule | Constraint |
|------|-----------|
| MF001 | No `Path.home()` — use `get_real_user_home()` |
| MF002 | No `shell=True` in subprocess |
| MF003 | No bare `except:` — specify exception type |
| MF004 | Always include `timeout=` on subprocess calls |

---

## Ecosystem (5 Repositories)

| Repo | Version | Role |
|------|---------|------|
| meshforge (NOC) | 0.5.4-beta | Core gateway, TUI, RF tools, diagnostics |
| meshforge-maps | 0.7.0-beta | Live visualization, topology, alerting |
| meshing_around_meshforge | 0.5.0-beta | Bot alerting (12 alert types) |
| RNS-Management-Tool | 0.3.2-beta | Cross-platform RNS installer |
| RNS-Meshtastic-Gateway-Tool | Alpha | Original bridge (migrating into NOC) |

---

*Previous version of this document (v0.4.3-beta, January 2026) included GTK4 and Web UI diagrams. GTK4 was removed in v0.5.x. TUI is the sole interface as of v0.5.0-beta.*
