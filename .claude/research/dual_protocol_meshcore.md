# Dual-Protocol Mesh Node: Meshtastic <> MeshCore Bridge for MeshForge

**Date**: 2026-02-16
**Author**: Dude AI / WH6GXZ
**Status**: Research & Implementation Plan (Alpha)

---

## Context

MeshForge currently bridges **Meshtastic** and **Reticulum (RNS)** — the first open-source tool to unify these incompatible mesh ecosystems. A growing community demand exists for **MeshCore** support, a newer lightweight LoRa mesh protocol with a hybrid routing architecture. MeshCore's rapid adoption (companion radios, room servers, repeaters) and its fundamentally different design philosophy from Meshtastic make it a valuable third protocol for MeshForge's NOC vision.

This document researches what a dual-protocol Meshtastic <> MeshCore bridge would require, evaluates existing bridging projects, analyzes protocol incompatibilities, and provides a concrete implementation plan for MeshForge integration.

---

## 1. Protocol Comparison: Meshtastic vs MeshCore

### 1.1 Architectural Philosophy

| Aspect | Meshtastic | MeshCore |
|--------|-----------|----------|
| **Routing** | Managed flood (broadcast) + Next-Hop (DMs, v2.6+) | Flood-then-direct with path learning |
| **Who relays** | All nodes by default | Only dedicated repeaters |
| **Device roles** | Homogeneous (every node = client + repeater) | Heterogeneous: Companion, Repeater, Room Server |
| **Max hops** | 7 (default 3) | 64 |
| **Telemetry** | Push-based (frequent, chatty) | Pull-based (on-demand, quiet) |
| **Network model** | Ad-hoc peer-to-peer | Planned infrastructure with roles |
| **Best for** | Spontaneous mobile groups | Large-scale planned deployments |
| **Maturity** | Established, large community | Growing rapidly, smaller community |

### 1.2 RF Physical Layer Parameters

| Parameter | MeshCore (Default) | Meshtastic (LongFast) |
|-----------|-------------------|----------------------|
| **Frequency** | 910.525 MHz | 906.875 MHz |
| **Spreading Factor** | SF7 | SF11 |
| **Bandwidth** | 62.5 kHz | 250 kHz |
| **Sync Word** | 0x12 | 0x2B |
| **Coding Rate** | 4/5 | 4/5 |
| **Preamble** | 8 symbols | 16 symbols |
| **Max Payload** | 184 bytes | 237 bytes |
| **TX Power** | Up to 22 dBm (SX1262) | Up to 22 dBm |

**Key takeaway**: These protocols are **RF-incompatible** by design — different frequencies, spreading factors, sync words, and bandwidths. A device can only listen to one at a time. Bridging must happen at the **application layer** (software on a host with two radios) or via a **proxy device** that time-multiplexes between configurations.

### 1.3 Cryptographic Architecture

| Feature | Meshtastic | MeshCore |
|---------|-----------|----------|
| **Identity** | Ed25519 (PKI since v2.5) | Ed25519 (from inception) |
| **DM Encryption** | AES-256-CTR + PSK or PKI | AES-128 + HMAC (encrypt-then-MAC) via ECDH |
| **Channel Encryption** | AES-256-CTR with shared PSK | AES-256-CTR with channel PSK |
| **Addressing** | 32-bit node IDs (`!aabbccdd`) | 16-bit mesh addresses from pubkey hash |
| **Replay Protection** | Packet ID + sequence | Timestamp-based per-peer |
| **Key Exchange** | PKI or pre-shared | ECDH (X25519 from Ed25519) |
| **Anonymous Messaging** | No native support | `PAYLOAD_TYPE_ANON_REQ` with ephemeral keys |

**Note on MeshCore key derivation**: MeshCore uses a non-standard Ed25519 convention where the first 32 bytes of the private key are a pre-clamped scalar (skips `clamp(sha512(seed))`). Standard crypto libraries will derive the wrong public key if handed a raw MeshCore private key without accommodation.

### 1.4 Message Semantics

| Feature | Meshtastic | MeshCore |
|---------|-----------|----------|
| **Text Messages** | Protobuf-encoded, channel-based | Binary framed, max 160 bytes |
| **Broadcasts** | Channel flood to all nodes | Channel flood via repeaters only |
| **Direct Messages** | To node ID, encrypted | To pubkey, encrypt-then-MAC |
| **Telemetry** | Structured protobuf (battery, GPS, env) | Pull-based status requests |
| **Acknowledgments** | Optional per-message | Built-in with RTT tracking |
| **Store & Forward** | Plugin-based | Native Room Server / Post Office |

---

## 2. Existing Bridging Projects

### 2.1 MeshCore-Meshtastic-Proxy (Firmware-Level)
- **Repo**: https://github.com/wdunn001/MeshCore-Meshtastic-Proxy
- **Approach**: Embedded C++ firmware on RAK4631 or LoRa32u4II
- **Architecture**: Three-layer (Platform -> Radio -> Protocol) with canonical packet format
- **Key insight**: **Unidirectional only** — listens on one protocol, retransmits to other. Two devices needed for bidirectional bridging
- **Canonical format**: Universal intermediate `CanonicalPacket` struct reduces N-protocol conversion from N*(N-1) to 2*N
- **MQTT filtering**: Drops Meshtastic packets with `via_mqtt` flag (MeshCore is pure-radio)
- **Limitation**: Hardware-level, one-way, no application context

### 2.2 Akita Meshtastic-MeshCore Bridge (Application-Level)
- **Repo**: https://github.com/AkitaEngineering/Akita-Meshtastic-Meshcore-Bridge
- **Approach**: Python application bridging via serial (companion mode) or MQTT
- **Architecture**: Bidirectional relay with async/sync modes
- **Key features**:
  - Uses `meshcore_py` for async MeshCore integration
  - Uses `meshtastic` Python lib for Meshtastic integration
  - REST API for monitoring (`/api/health`, `/api/metrics`, `/api/status`)
  - Rate limiting, message validation/sanitization
  - Companion mode binary protocol support
  - MQTT transport option with TLS/SSL
- **License**: GPL-3.0
- **Relevance to MeshForge**: Closest existing model for what we'd build, but standalone (not integrated into a NOC)

### 2.3 meshcore-pi (Pure Python MeshCore Implementation)
- **Repo**: https://github.com/brianwiddas/meshcore-pi
- **Approach**: Full MeshCore protocol in Python for Raspberry Pi
- **Capabilities**: Companion radio, room server, repeater — all in one
- **Hardware**: SX1262 via SPI/GPIO (Waveshare LoRa HAT, HT-RA62) + ESP-NOW via WiFi
- **Relevance**: Reference for understanding MeshCore internals without needing firmware

### 2.4 meshcore_py (Official Python Bindings) — PRIMARY INTEGRATION POINT
- **Repo**: https://github.com/meshcore-dev/meshcore_py
- **PyPI**: `pip install meshcore` (Python 3.10+)
- **Architecture**: Fully async, event-driven
- **Connection types**: Serial, BLE, TCP
- **Key API**:
  - `MeshCore.create_serial("/dev/ttyUSB0", 115200)` — Connect
  - `meshcore.commands.get_contacts()` — Contact list
  - `meshcore.commands.send_msg(contact, "text")` — Send DM
  - `meshcore.subscribe(EventType.CONTACT_MSG_RECV, handler)` — Receive messages
  - `meshcore.start_auto_message_fetching()` — Continuous RX
- **Event types**: `CONTACT_MSG_RECV`, `CHANNEL_MSG_RECV`, `ADVERTISEMENT`, `ACK`, `NEW_CONTACT`, `ERROR`
- **Auto-reconnect**: Exponential backoff (1s, 2s, 4s, 8s max) with configurable retry limits
- **Known issue**: `CHANNEL_MSG_RECV` events sometimes don't fire (GitHub #1232, firmware v1.11.0)

---

## 3. MeshCore Companion Radio Protocol (Serial Binary)

The companion radio protocol is how external apps talk to MeshCore hardware:

### 3.1 Frame Format (USB)
```
Outbound (radio->app): '>' (0x3E) + 2-byte length (LE) + frame data
Inbound  (app->radio): '<' (0x3C) + 2-byte length (LE) + frame data
```

### 3.2 Handshake
1. App sends `CMD_DEVICE_QUERY` (22) with protocol version
2. Radio responds `RESP_CODE_DEVICE_INFO` (13) with firmware version + capabilities
3. App sends `CMD_APP_START` (1) with app version + name
4. Radio responds `RESP_CODE_SELF_INFO` (5) with node details

### 3.3 Key Commands
| Code | Command | Purpose |
|------|---------|---------|
| 2 | `CMD_SEND_TXT_MSG` | Direct message to contact |
| 3 | `CMD_SEND_CHANNEL_TXT_MSG` | Channel broadcast (flood) |
| 4 | `CMD_GET_CONTACTS` | Sync contact list |
| 6 | `CMD_SET_DEVICE_TIME` | Clock sync |
| 10 | `CMD_SYNC_NEXT_MESSAGE` | Retrieve queued messages |
| 11 | `CMD_SET_RADIO_PARAMS` | Configure frequency/BW/SF/CR |
| 14 | `CMD_SET_ADVERT_LATLON` | Update GPS coordinates |
| 27 | `CMD_SEND_STATUS_REQ` | Query repeater/sensor status |
| 36 | `CMD_SEND_TRACE_PATH` | Trace route with SNR |
| 39 | `CMD_SEND_TELEMETRY_REQ` | Request sensor data |
| 50 | `CMD_SEND_BINARY_REQ` | Send custom binary request |

### 3.4 Push Notifications (Async, Radio -> App)
| Code | Event | Description |
|------|-------|-------------|
| 0x80 | `PUSH_CODE_ADVERT` | New node advertisement |
| 0x82 | `PUSH_CODE_SEND_CONFIRMED` | ACK with RTT |
| 0x83 | `PUSH_CODE_MSG_WAITING` | Text message queued |
| 0x84 | `PUSH_CODE_RAW_DATA` | Custom binary payload |
| 0x87 | `PUSH_CODE_STATUS_RESPONSE` | Status query reply |
| 0x8C | `PUSH_CODE_BINARY_RESPONSE` | Binary response (matched by tag) |

### 3.5 Error Codes
| Code | Meaning |
|------|---------|
| 1 | Unsupported command |
| 2 | Not found |
| 3 | Table full |
| 4 | Bad state |
| 5 | File I/O error |
| 6 | Illegal argument |

---

## 4. MeshForge Architecture & Extension Points

### 4.1 Current Gateway Architecture
MeshForge's gateway (`src/gateway/`) already bridges Meshtastic <> RNS using:

- **MeshtasticHandler** (`meshtastic_handler.py`) — TCP connection to meshtasticd
- **MQTTBridgeHandler** (`mqtt_bridge_handler.py`) — Zero-interference MQTT approach (recommended)
- **RNSMeshtasticBridge** (`rns_bridge.py`) — Main orchestrator
- **MessageRouter** (`message_routing.py`) — AI classifier + regex routing rules
- **PersistentMessageQueue** (`message_queue.py`) — SQLite-backed reliable delivery
- **BridgeHealthMonitor** (`bridge_health.py`) — Cross-network health tracking
- **CircuitBreaker** (`circuit_breaker.py`) — Per-destination failure protection
- **UnifiedNodeTracker** (`node_tracker.py`) — Combined Meshtastic + RNS node tracking

### 4.2 Key Design Pattern: Dependency Injection
Both `MeshtasticHandler` and `MQTTBridgeHandler` accept identical constructor signatures:
```python
handler = Handler(
    config, node_tracker, health, stop_event,
    stats, stats_lock, message_queue,
    message_callback, status_callback, should_bridge
)
```
A `MeshCoreHandler` would implement this same interface — the architecture is already designed for protocol extensibility.

### 4.3 Existing Canonical Message (Partial)
The existing `meshcore_proxy_analysis.md` already recommended a `CanonicalMessage` format. The codebase uses `BridgedMessage` as a partial implementation with `source_network`, `source_id`, `content`, `is_broadcast`, and `origin` fields. This needs enhancement for true 3-protocol support.

### 4.4 Node Model Extension
`UnifiedNode` in `node_models.py` uses `source_network` prefix: `"meshtastic:!abc123"`, `"rns:abc123xyz"`. MeshCore nodes would follow: `"meshcore:<pubkey_prefix>"`.

---

## 5. Implementation Strategy

### 5.1 Approach: Application-Layer Bridge via meshcore_py

**Why application-layer** (not firmware-level):
1. MeshForge is a Linux NOC — it has access to USB-connected companion radios
2. `meshcore_py` provides a mature, async Python API with auto-reconnect
3. Application-layer allows message context, routing rules, logging, and persistence
4. No custom firmware needed — use stock MeshCore companion firmware
5. Consistent with how MeshForge already handles Meshtastic (via meshtasticd)

**Hardware requirement**: A MeshCore companion radio connected via USB serial (or TCP if using meshcore-pi/WiFi companion)

### 5.2 Three-Protocol Bridge Architecture

```
+----------------+    +----------------+    +----------------+
|  Meshtastic    |    |  MeshCore      |    |     RNS        |
|  (meshtasticd  |    | (companion     |    |   (rnsd)       |
|   TCP/MQTT)    |    |  USB/TCP)      |    |                |
+-------+--------+    +-------+--------+    +-------+--------+
        |                      |                     |
        v                      v                     v
+----------------+  +------------------+  +----------------+
| Meshtastic     |  |  MeshCore        |  | RNS/LXMF       |
| Handler        |  |  Handler         |  | Handler         |
| (existing)     |  |  (NEW)           |  | (existing)      |
+-------+--------+  +---------+--------+  +-------+--------+
        |                      |                    |
        +-----------+----------+--------------------+
                    |
           +--------v--------+
           | CanonicalMessage |  <-- Protocol-agnostic
           |   (enhanced)     |      intermediate format
           +---------+-------+
                     |
           +---------v-------+
           |  MessageRouter   |  <-- AI classifier +
           |  (enhanced)      |      regex rules
           +---------+-------+
                     |
        +------------+------------+
        |            |            |
        v            v            v
    To Mesh     To MeshCore    To RNS
```

### 5.3 Implementation Phases

#### Phase 1: Foundation — Canonical Message Format (Prerequisite)
Enhance `BridgedMessage` to become a true `CanonicalMessage` supporting N protocols with 2*N conversions instead of N*(N-1).

**Files to modify:**
- `src/gateway/rns_bridge.py` — Refactor to use CanonicalMessage
- `src/gateway/message_routing.py` — Update router for 3-way routing

**New file:**
- `src/gateway/canonical_message.py` — Protocol-agnostic message model

```python
@dataclass
class CanonicalMessage:
    id: str                          # UUID
    source_network: str              # "meshtastic" | "meshcore" | "rns"
    source_address: str              # Network-specific address
    destination_address: Optional[str]  # None = broadcast
    payload: bytes                   # Raw content
    text: Optional[str]              # Decoded text (if applicable)
    message_type: MessageType        # TEXT, TELEMETRY, POSITION, etc.
    is_broadcast: bool
    hop_limit: int
    via_internet: bool               # MQTT origin filtering
    origin: MessageOrigin            # RADIO, MQTT, API, BRIDGE
    timestamp: datetime
    metadata: dict                   # Protocol-specific extras

    @classmethod
    def from_meshtastic(cls, packet: dict) -> 'CanonicalMessage': ...

    @classmethod
    def from_meshcore(cls, event) -> 'CanonicalMessage': ...

    @classmethod
    def from_rns(cls, lxmf_message) -> 'CanonicalMessage': ...

    def to_meshtastic(self) -> dict: ...
    def to_meshcore_text(self) -> str: ...
    def to_rns_lxmf(self): ...
```

#### Phase 2: MeshCore Handler

**New file:** `src/gateway/meshcore_handler.py` (~600-800 LOC)

```python
class MeshCoreHandler:
    """MeshCore companion radio integration via meshcore_py.

    Follows the same dependency-injection pattern as MeshtasticHandler
    and MQTTBridgeHandler for seamless gateway integration.
    """

    def __init__(self, config, node_tracker, health, stop_event,
                 stats, stats_lock, message_queue,
                 message_callback, status_callback, should_bridge):
        self._meshcore = None  # meshcore_py.MeshCore instance
        self._subscriptions = []

    async def connect(self):
        """Connect to MeshCore companion radio."""
        from meshcore import MeshCore
        self._meshcore = await MeshCore.create_serial(
            self._config.meshcore.device_path,
            self._config.meshcore.baud_rate
        )
        # Subscribe to events
        self._subscriptions.append(
            self._meshcore.subscribe(EventType.CONTACT_MSG_RECV, self._on_message)
        )
        self._subscriptions.append(
            self._meshcore.subscribe(EventType.ADVERTISEMENT, self._on_advertisement)
        )
        await self._meshcore.start_auto_message_fetching()

    async def _on_message(self, event):
        """Handle incoming MeshCore DM."""
        msg = CanonicalMessage.from_meshcore(event)
        if self._should_bridge(msg):
            self._message_callback(msg)

    async def _on_advertisement(self, event):
        """Track MeshCore node discovery."""
        self._node_tracker.add_node(UnifiedNode(
            id=f"meshcore:{event.payload.pubkey_prefix}",
            source_network="meshcore",
            ...
        ))

    async def send_message(self, canonical_msg: CanonicalMessage):
        """Send message to MeshCore network."""
        contact = await self._resolve_contact(canonical_msg.destination_address)
        if contact:
            await self._meshcore.commands.send_msg(contact, canonical_msg.text)
        else:
            await self._meshcore.commands.send_channel_txt_msg(canonical_msg.text)

    def run_loop(self):
        """Main event loop (wraps async in thread)."""
        asyncio.run(self._async_run())
```

#### Phase 3: Configuration & Service Integration

**Modify:** `src/gateway/config.py`
```python
@dataclass
class MeshCoreConfig:
    enabled: bool = False
    device_path: str = "/dev/ttyUSB1"  # Separate from Meshtastic
    baud_rate: int = 115200
    connection_type: str = "serial"    # serial | tcp | ble
    tcp_host: str = ""
    tcp_port: int = 4000
    auto_fetch_messages: bool = True
    bridge_channels: bool = True       # Bridge channel messages
    bridge_dms: bool = True            # Bridge direct messages
```

**Note:** No `meshcored` daemon exists (unlike meshtasticd). MeshForge manages the connection directly via `meshcore_py`. Add a `check_meshcore_device()` helper to `src/utils/service_check.py` to verify USB serial availability.

#### Phase 4: Node Tracking & Telemetry

**Modify:** `src/gateway/node_models.py`
- MeshCore nodes: `"meshcore:<6-char-pubkey-prefix>"`
- Map MeshCore advertisements to `UnifiedNode`
- Parse MeshCore telemetry (pull-based via `CMD_SEND_TELEMETRY_REQ`) into unified `Telemetry` model

#### Phase 5: Routing Enhancement

**Modify:** `src/gateway/message_routing.py`
- Add routing directions: `meshcore_to_rns`, `meshcore_to_mesh`, `rns_to_meshcore`, `mesh_to_meshcore`
- Add `source_filter: "meshcore:.*"` patterns
- Internet-origin filtering: drop `via_internet=True` messages when routing to MeshCore (MeshCore is pure-radio)

#### Phase 6: Health & Monitoring

**Modify:** `src/gateway/bridge_health.py`
- Add `meshcore_state: SubsystemState`
- Three-way health: bridge HEALTHY only if all active protocols are healthy
- Track MeshCore connection state (connected, disconnected, reconnecting)

---

## 6. Technical Challenges & Mitigations

### 6.1 Async vs Sync Architecture
**Challenge**: `meshcore_py` is fully async (asyncio). MeshForge gateway uses threads.
**Mitigation**: Run MeshCore handler in its own thread with a dedicated asyncio event loop. Pattern: `asyncio.run_coroutine_threadsafe()` for cross-thread communication. This is well-established for mixing threaded and async code.

### 6.2 Message Size Mismatch
**Challenge**: MeshCore max 160 bytes vs Meshtastic 237 bytes vs RNS/LXMF ~500 bytes.
**Mitigation**: Truncate with indicator (`[...]`) when bridging to smaller-payload networks. Log full message in queue for audit. Make truncation configurable.

### 6.3 Address Translation
**Challenge**: Meshtastic uses `!aabbccdd` (32-bit), MeshCore uses pubkey prefixes, RNS uses destination hashes.
**Mitigation**: `CanonicalMessage` preserves original addresses. Routing uses network-prefixed IDs (`meshcore:a1b2c3`, `meshtastic:!aabb`, `rns:deadbeef`). Contact mapping table for DM routing across protocols.

### 6.4 No MeshCore Daemon (Unlike meshtasticd)
**Challenge**: No `meshcored` systemd service exists. MeshCore companion radios are connected directly via USB.
**Mitigation**: MeshForge manages the connection directly via `meshcore_py`. Add device detection (`/dev/ttyUSB*` scanning with vendor ID matching) and leverage meshcore_py's built-in auto-reconnect (exponential backoff).

### 6.5 Channel Message Event Bug
**Challenge**: `CHANNEL_MSG_RECV` events sometimes don't fire in meshcore_py (GitHub #1232, firmware v1.11.0 on nRF52840).
**Mitigation**: Implement polling fallback via `CMD_SYNC_NEXT_MESSAGE` (code 10) as backup. Monitor upstream fix status. Log when fallback is triggered.

### 6.6 Encryption Boundary
**Challenge**: Messages are decrypted on the bridge host — security boundary concern.
**Mitigation**: Same trust model as existing Meshtastic bridge. Bridge host is a trusted intermediary. Document security implications clearly. Messages are re-encrypted for destination network using that network's crypto.

### 6.7 Internet Origin Filtering
**Challenge**: MeshCore is a pure-radio mesh. Internet-originated Meshtastic messages (via MQTT) should not be bridged to MeshCore.
**Mitigation**: Parse `via_mqtt` flag from Meshtastic packets. Set `via_internet=True` on CanonicalMessage. Routing rules drop internet-origin messages destined for MeshCore. Make this configurable per deployment.

### 6.8 Python Version Requirement
**Challenge**: `meshcore_py` requires Python 3.10+. MeshForge currently targets 3.9+.
**Mitigation**: Use `safe_import` pattern — MeshCore support is optional. When `meshcore` import fails on Python 3.9, the feature is gracefully unavailable. Document 3.10+ requirement for MeshCore features.

---

## 7. Dependencies

| Package | Version | Purpose | Optional? |
|---------|---------|---------|-----------|
| `meshcore` | >=1.9 | MeshCore Python bindings | Yes (safe_import) |
| `meshtastic` | existing | Meshtastic integration | Yes (existing) |
| `RNS` | existing | Reticulum integration | Yes (existing) |
| `LXMF` | existing | LXMF messaging | Yes (existing) |

```python
# In meshcore_handler.py
from utils.safe_import import safe_import
meshcore_mod, _HAS_MESHCORE = safe_import('meshcore')
```

---

## 8. Hardware Requirements

For a dual-protocol (Meshtastic + MeshCore) bridge node:

| Component | Purpose | Example Hardware |
|-----------|---------|-----------------|
| Linux SBC | Bridge host | Raspberry Pi 4/5, uConsole |
| Meshtastic radio | Meshtastic network access | RAK WisBlock, T-Beam, Heltec V3 |
| MeshCore companion radio | MeshCore network access | RAK4631, T-Deck, Heltec V3 (MeshCore FW) |
| USB hub (if needed) | Multiple serial devices | Powered USB hub |

**Both radios must be on separate USB ports** — one running meshtasticd firmware, the other running MeshCore companion firmware. Same hardware model can be used for both, flashed with different firmware.

### Minimal Test Setup
```
Raspberry Pi 4
  |-- USB0: Heltec V3 (meshtasticd firmware)
  |-- USB1: Heltec V3 (MeshCore companion firmware)
  |-- WiFi/Ethernet: NOC access
```

---

## 9. Files to Create/Modify

### New Files
| File | LOC Est. | Purpose |
|------|----------|---------|
| `src/gateway/canonical_message.py` | 200-300 | Protocol-agnostic message model |
| `src/gateway/meshcore_handler.py` | 600-800 | MeshCore protocol adapter |
| `src/gateway/meshcore_config.py` | 150-200 | MeshCore configuration dataclass |
| `tests/test_meshcore_handler.py` | 400-500 | Unit tests |
| `tests/test_canonical_message.py` | 200-300 | Canonical message conversion tests |

### Modified Files
| File | Changes |
|------|---------|
| `src/gateway/config.py` | Add `MeshCoreConfig` section |
| `src/gateway/rns_bridge.py` | Use CanonicalMessage, register MeshCoreHandler |
| `src/gateway/message_routing.py` | 3-way routing directions |
| `src/gateway/node_models.py` | MeshCore node/telemetry parsing |
| `src/gateway/bridge_health.py` | MeshCore subsystem state |
| `src/utils/service_check.py` | MeshCore device detection helper |
| `src/launcher_tui/main.py` | MeshCore menu items (gateway submenu) |

---

## 10. Verification Plan

1. **Unit tests**: `pytest tests/test_meshcore_handler.py tests/test_canonical_message.py -v`
   - Mock `meshcore` with `safe_import` / `_HAS_MESHCORE` pattern
   - Test CanonicalMessage conversion (from/to all 3 protocols)
   - Test routing rules with MeshCore source/destination
   - Test health monitor with 3 subsystems
   - Test message truncation for size mismatch
   - Test internet-origin filtering

2. **Integration test** (requires hardware):
   - Connect MeshCore companion via USB
   - Verify `meshcore_py` connection and event subscription
   - Send test message from MeshCore -> verify arrival in Meshtastic
   - Send test message from Meshtastic -> verify arrival in MeshCore
   - Verify node tracking shows both MeshCore and Meshtastic nodes
   - Test auto-reconnect on USB disconnect/reconnect

3. **Lint & security**: `python3 scripts/lint.py --all`
   - No `shell=True` (MF002), no `Path.home()` (MF001), no bare `except:` (MF003)
   - All subprocess calls have timeouts (MF004)

---

## 11. Risk Assessment

| Risk | Probability | Impact | Mitigation |
|------|------------|--------|------------|
| meshcore_py API changes | Medium | Medium | Pin version, safe_import |
| CHANNEL_MSG_RECV bug | High | Medium | Polling fallback, monitor upstream |
| MeshCore ecosystem immaturity | Medium | Low | Feature is optional/alpha |
| Python 3.10+ requirement | Low | Low | safe_import makes it optional |
| Two USB radios conflict | Low | Medium | Device path config, udev rules |

---

## 12. References

### Protocol & Crypto
- [MeshCore Protocol Explained](https://www.localmesh.nl/en/meshcore-protocol-explained/)
- [MeshCore Encryption Details](https://www.localmesh.nl/en/meshcore-encryption-details/)
- [MeshCore Message Encryption (DeepWiki)](https://deepwiki.com/ripplebiz/MeshCore/9.2-message-encryption)
- [Hitchhiker's Guide to MeshCore Cryptography](http://mail.jacksbrain.com/2026/01/a-hitchhiker-s-guide-to-meshcore-cryptography/)
- [MeshCore Companion Radio Protocol](https://github.com/meshcore-dev/MeshCore/wiki/Companion-Radio-Protocol)

### Python Libraries & Tools
- [meshcore_py (GitHub)](https://github.com/meshcore-dev/meshcore_py)
- [meshcore (PyPI)](https://pypi.org/project/meshcore/)
- [meshcore-cli (PyPI)](https://pypi.org/project/meshcore-cli/)
- [MeshCore Python API Usage Guide](https://www.localmesh.nl/en/meshcore-python-api-usage/)

### Bridging Projects
- [Akita Meshtastic-MeshCore Bridge](https://github.com/AkitaEngineering/Akita-Meshtastic-Meshcore-Bridge)
- [MeshCore-Meshtastic-Proxy (firmware)](https://github.com/wdunn001/MeshCore-Meshtastic-Proxy)
- [meshcore-pi (Python implementation)](https://github.com/brianwiddas/meshcore-pi)

### Comparisons & Analysis
- [MeshCore vs Meshtastic (Austin Mesh)](https://www.austinmesh.org/learn/meshcore-vs-meshtastic/)
- [Meshtastic and MeshCore Pros & Cons](https://lucifernet.com/2025/08/29/meshtastic-and-meshcore-pros-cons/)
- [MeshCore vs Meshtastic (NodakMesh)](https://nodakmesh.org/blog/meshcore-vs-meshtastic-comparison/)
- Existing MeshForge analysis: `.claude/research/meshcore_proxy_analysis.md`

---

*Research conducted for MeshForge v0.5.4-beta — Alpha feature track*
*Made with aloha for the mesh community*
