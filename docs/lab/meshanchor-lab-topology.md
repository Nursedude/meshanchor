# MeshAnchor Lab Topology — Q&A Test Network

> **Architect**: WH6GXZ | **Date**: 2026-04-07
> **Purpose**: Reference deployment for MeshCore Q&A testing

---

## Network Map

```
                        +-----------------------+
                        |    MeshAnchor P1      |
                        |    (Client)           |
                        |                       |
                        |  iOS App via BLE      |
                        +-----------+-----------+
                                    |
                                    | RF (MeshCore)
                                    |
                        +-----------+-----------+
                        |    MeshAnchor R1      |
                        |    (Repeater)         |
                        |                       |
                        |  Extends RF range     |
                        |  Remote / field unit  |
                        +-----------+-----------+
                                    |
                                    | RF (MeshCore)
                                    |
+-------------------+   +-----------+-----------+
|   Raspberry Pi 4  +---+    MeshAnchor RS1     |
|   MeshAnchor NOC  |USB|    (Room Server)      |
|                   +---+                       |
|  - TUI interface  |   |  Heltec E213          |
|  - Node tracking  |   |  USB serial to Pi     |
|  - RF monitoring  |   +-----------------------+
|  - Message logs   |
+-------------------+
```

---

## Node Registry

| Node ID | Name | Role | Hardware | Connection | Status |
|---------|------|------|----------|------------|--------|
| P1 | MeshAnchor P1 | Client | MeshCore radio | BLE to iOS app | Planned |
| RS1 | MeshAnchor RS1 | Room Server | Heltec E213 | USB serial to Pi 4 | Planned |
| R1 | MeshAnchor R1 | Repeater | MeshCore radio | RF only (remote) | Planned |

---

## Infrastructure

| Component | Hardware | OS | Role |
|-----------|----------|----|------|
| NOC | Raspberry Pi 4 | Raspberry Pi OS (Linux) | MeshAnchor host, node management |
| Room Server Radio | Heltec E213 | MeshCore firmware | Mesh gateway, USB-attached to Pi |
| Client Radio | MeshCore device | MeshCore firmware | Mobile endpoint, BLE to iOS |
| Repeater | MeshCore device | MeshCore firmware | RF relay, extends mesh coverage |

---

## Data Flow

```
[iOS App]                    User sends Q&A message
    |
    | BLE
    v
[P1 - Client]               MeshCore encrypts + transmits
    |
    | RF (MeshCore protocol)
    v
[R1 - Repeater]             Relays packet (store-and-forward)
    |
    | RF (MeshCore protocol)
    v
[RS1 - Room Server]         Heltec E213 receives
    |
    | USB Serial
    v
[Pi 4 - MeshAnchor NOC]     Logs, monitors, displays in TUI
```

---

## USB Serial Notes

- Heltec E213 exposes `/dev/ttyUSB0` or `/dev/ttyACM0` on Pi
- Confirmed working on Win11 (USB quirks noted — not relevant for Pi deployment)
- MeshAnchor MeshCore handler reads serial for node tracking and message monitoring

---

## Lab Setup Checklist

- [ ] Flash Heltec E213 with MeshCore room server firmware
- [ ] Connect E213 to Pi 4 via USB, verify serial device appears
- [ ] Install MeshAnchor on Pi 4
- [ ] Configure MeshAnchor to use serial port for MeshCore
- [ ] Flash client radio with MeshCore client firmware
- [ ] Pair client radio with iOS app via BLE
- [ ] Flash repeater with MeshCore repeater firmware
- [ ] Verify P1 -> R1 -> RS1 message path
- [ ] Confirm MeshAnchor TUI shows all three nodes
- [ ] Run Q&A test session
