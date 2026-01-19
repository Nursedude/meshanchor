# MeshForge Native Meshtastic Integration

> **Research Date**: 2026-01-19
> **Status**: PROPOSAL
> **Priority**: HIGH - Solves Issues #17, #18, #20

---

## Executive Summary

**Problem**: MeshForge connects to meshtasticd via TCP, but meshtasticd only supports ONE client at a time. This causes connection contention (Issue #17), reconnection failures (Issue #18), and status detection problems (Issue #20).

**Solution**: MeshForge becomes its own meshtastic daemon - connect DIRECTLY to hardware via Serial/BLE using the meshtastic Python library, eliminating the TCP bottleneck entirely.

---

## Current Architecture (Broken)

```
┌─────────────┐     TCP:4403     ┌─────────────┐      Serial/USB
│  MeshForge  │◄────────────────►│ meshtasticd │◄──────────────────► Radio
│  (client)   │                  │  (daemon)   │
└─────────────┘                  └─────────────┘
      ▲                                ▲
      │                                │
      └───── SINGLE CONNECTION ────────┘
            (causes Issues #17-20)
```

**Problems**:
1. meshtasticd accepts only ONE TCP connection
2. Multiple MeshForge components compete for that connection
3. External tools (Meshtastic Web UI on :9443) also compete
4. Connection drops require complex reconnection logic
5. Status detection is unreliable

---

## Proposed Architecture (Native)

```
┌──────────────────────────────────────┐
│           MeshForge                  │
│  ┌─────────────────────────────┐     │      Serial/USB/BLE
│  │  Native Meshtastic Handler  │◄────┼──────────────────────► Radio
│  │  (SerialInterface)          │     │
│  └─────────────────────────────┘     │
│            ▲                         │
│            │ pub/sub events          │
│  ┌─────────┴─────────┐               │
│  │  Gateway Panel    │               │
│  │  Node Tracker     │               │
│  │  Radio Config     │               │
│  │  Messaging        │               │
│  └───────────────────┘               │
└──────────────────────────────────────┘
```

**Benefits**:
1. NO external daemon dependency
2. NO TCP contention - direct hardware access
3. Single connection owner with internal pub/sub
4. Simplified startup - MeshForge works without meshtasticd
5. Full control over reconnection and health monitoring

---

## Meshtastic Python Library Architecture

### Connection Interfaces

| Interface | Transport | Use Case |
|-----------|-----------|----------|
| `SerialInterface` | USB Serial | Direct USB connection (most reliable) |
| `TCPInterface` | TCP:4403 | Connect to meshtasticd (current approach) |
| `BLEInterface` | Bluetooth | Wireless connection via BLE |

### Class Hierarchy

```
MeshInterface (base)
    └── StreamInterface (abstract)
            ├── SerialInterface
            └── TCPInterface
    └── BLEInterface
```

### Key Methods

```python
from meshtastic.serial_interface import SerialInterface

# Auto-discover and connect
interface = SerialInterface()  # Finds first device

# Or specify device
interface = SerialInterface(devPath="/dev/ttyUSB0")

# Access node database
my_node = interface.myNodeInfo
all_nodes = interface.nodes

# Send message
interface.sendText("Hello mesh!", destinationId=0xFFFFFFFF)

# Subscribe to events
pub.subscribe(on_receive, "meshtastic.receive.text")
pub.subscribe(on_connection, "meshtastic.connection.established")

# Close
interface.close()
```

### Pub/Sub Topics

| Topic | Event |
|-------|-------|
| `meshtastic.connection.established` | Connected and DB downloaded |
| `meshtastic.connection.lost` | Connection dropped |
| `meshtastic.receive.text` | Text message received |
| `meshtastic.receive.position` | Position update received |
| `meshtastic.receive.telemetry` | Telemetry data received |
| `meshtastic.receive.data` | Any data packet |

---

## Implementation Plan

### Phase 1: Native Meshtastic Handler

Create `src/core/meshtastic_handler.py`:

```python
"""
Native Meshtastic Handler - Direct hardware communication.
Replaces TCPInterface connection to meshtasticd.
"""
import threading
from typing import Optional, Callable
from pubsub import pub

class NativeMeshtasticHandler:
    """
    Direct connection to Meshtastic hardware.
    No meshtasticd required.
    """

    def __init__(self, device_path: str = None, ble_address: str = None):
        self._interface = None
        self._lock = threading.Lock()
        self._subscribers = []
        self._device_path = device_path
        self._ble_address = ble_address

    def connect(self) -> bool:
        """Connect to device via Serial or BLE."""
        with self._lock:
            if self._interface:
                return True  # Already connected

            try:
                if self._ble_address:
                    from meshtastic.ble_interface import BLEInterface
                    self._interface = BLEInterface(address=self._ble_address)
                else:
                    from meshtastic.serial_interface import SerialInterface
                    self._interface = SerialInterface(devPath=self._device_path)

                # Subscribe to events
                pub.subscribe(self._on_receive, "meshtastic.receive.text")
                pub.subscribe(self._on_position, "meshtastic.receive.position")
                pub.subscribe(self._on_telemetry, "meshtastic.receive.telemetry")

                return True
            except Exception as e:
                logger.error(f"Connection failed: {e}")
                return False

    @property
    def nodes(self) -> dict:
        """Get all known nodes."""
        if self._interface:
            return self._interface.nodes or {}
        return {}

    @property
    def my_node(self) -> Optional[dict]:
        """Get local node info."""
        if self._interface:
            return self._interface.myInfo
        return None

    def send_text(self, text: str, destination: int = 0xFFFFFFFF) -> bool:
        """Send text message."""
        if not self._interface:
            return False
        self._interface.sendText(text, destinationId=destination)
        return True

    def subscribe(self, event: str, callback: Callable):
        """Subscribe to mesh events."""
        self._subscribers.append((event, callback))
        # Internal event routing
```

### Phase 2: Integration Points

| Component | Current | New |
|-----------|---------|-----|
| `connection_manager.py` | TCPInterface to meshtasticd | NativeMeshtasticHandler |
| `node_tracker.py` | Poll via TCP | Subscribe to events |
| `mesh_bridge.py` | TCP connection | Direct serial |
| `radio_config_simple.py` | CLI subprocess | Direct interface methods |

### Phase 3: Fallback Mode

Keep TCPInterface as fallback for:
- When meshtasticd is already running (user choice)
- Remote radio on different machine
- Docker environments where USB passthrough isn't configured

```python
def get_meshtastic_interface():
    """Get best available interface."""
    # Try direct serial first
    if has_serial_device():
        return NativeMeshtasticHandler()

    # Fall back to meshtasticd if available
    if is_meshtasticd_running():
        return TCPInterface()

    return None
```

---

## "Double Tap" Feature

Note: The user asked about "double tap" - this is a **physical device feature**, not a connection protocol:

- Requires accelerometer on device
- Double-tap gesture treated as button press
- Configured via Device Settings
- Useful for devices without physical buttons (RAK19003)
- Known reliability issues on some hardware

Not directly relevant to this architecture change.

---

## Migration Strategy

### Step 1: Add Native Handler (Non-Breaking)
- Create NativeMeshtasticHandler alongside existing code
- Add settings toggle: "Use direct connection"
- Default: OFF (use meshtasticd)

### Step 2: Test & Validate
- Test with various hardware (T-Beam, RAK, Heltec)
- Test Serial and BLE connections
- Verify pub/sub event flow
- Compare with meshtasticd behavior

### Step 3: Make Default
- Once validated, make native mode default
- meshtasticd becomes optional/fallback
- Document migration for users

### Step 4: Deprecate meshtasticd Dependency
- Remove meshtasticd requirement from install docs
- Keep TCPInterface for advanced use cases

---

## Issues Resolved

| Issue | How Resolved |
|-------|--------------|
| #17 Connection Contention | No TCP, no contention |
| #18 Auto-Reconnect | Single owner, simple reconnect |
| #20 Status Detection | Direct interface state, no proxies |
| Startup Reliability | No external daemon dependency |

---

## Open Questions

1. **USB Permissions**: Need udev rules for non-root access to /dev/ttyUSB*
2. **BLE Permissions**: May need polkit or user groups for BLE
3. **Concurrent Access**: What if user also runs meshtastic CLI?
4. **Remote Radios**: How to handle radio on different host?

---

## References

- [Meshtastic Client API](https://meshtastic.org/docs/development/device/client-api/)
- [Meshtastic Python Library](https://python.meshtastic.org/)
- [Serial Interface](https://deepwiki.com/meshtastic/python/3.1-serial-interface)
- [Web Client Architecture](https://deepwiki.com/meshtastic/web/4.1-connection-protocols)

---

*Made with aloha for the mesh community*
