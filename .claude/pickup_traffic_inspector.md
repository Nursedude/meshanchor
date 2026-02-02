# Pickup Point: Multi-Hop Path Visualization & Traffic Inspector

**Date**: 2026-02-01
**Feature**: Wireshark-Grade Traffic Visibility
**Branch**: claude/multi-hop-path-visualization-PZeyx
**Status**: Implementation Complete

---

## Summary

Implemented a comprehensive traffic inspection system inspired by Wireshark's architecture, providing deep packet inspection, path tracing, and traffic analysis for mesh networks.

## Components Created

### 1. Traffic Inspector (`src/monitoring/traffic_inspector.py`)

Core module providing:

- **MeshPacket**: Unified packet representation for both Meshtastic and RNS
- **PacketField & PacketTree**: Hierarchical packet detail display (like Wireshark's protocol tree)
- **Dissectors**: Protocol-specific parsers
  - `MeshtasticDissector`: Parses Meshtastic packets with full field extraction
  - `RNSDissector`: Parses RNS/Reticulum packets
- **DisplayFilter**: Wireshark-style filtering (`mesh.hops > 2`, `mesh.snr >= -5`)
- **TrafficCapture**: SQLite-backed packet storage with real-time callbacks
- **TrafficAnalyzer**: Statistics aggregation (by protocol, port, direction, time)

### 2. Path Visualizer (`src/monitoring/path_visualizer.py`)

Multi-hop path visualization:

- **PathSegment**: Single hop with metrics (SNR, RSSI, latency)
- **TracedPath**: Complete path from source to destination
- **PathVisualizer**: D3.js-based interactive HTML visualization
  - Force-directed network graph
  - Animated packet flow
  - Hop-by-hop details panel
  - Timeline view
  - Path statistics

### 3. TUI Integration (`src/launcher_tui/traffic_inspector_mixin.py`)

Menu integration providing:

- Live traffic view
- Packet list with filtering
- Packet detail inspection
- Path visualization (ASCII + HTML)
- Traffic statistics dashboard
- Export (JSON, CSV, HTML)
- Filter reference

### 4. Tests (`tests/test_traffic_inspector.py`)

Comprehensive test coverage for:

- PacketField operations and filtering
- PacketTree construction
- MeshPacket serialization
- Dissector protocol detection
- Display filter parsing
- Traffic capture storage
- Path visualization

## Key Features

### Wireshark-Style Display Filtering

```python
# Filter expressions
"mesh.hops > 2"
"mesh.from == \"!abc123\""
"mesh.snr >= -5 and mesh.portnum == 1"
"rns.hops <= 3"
```

Available fields: `mesh.from`, `mesh.to`, `mesh.hops`, `mesh.snr`, `mesh.rssi`, `mesh.portnum`, `mesh.port`, `rns.hops`, `rns.interface`, etc.

### Protocol Tree (Packet Detail)

```
[+] Frame
    Timestamp: 2026-02-01 12:00:00
    Direction: inbound
    Size: 256
[+] Meshtastic
    Source: !abc12345
    Destination: broadcast
  [+] Routing
      Hop Limit: 1
      Hop Start: 3
      Hops Taken: 2
      Via MQTT: False
  [+] Payload
      Port Number: 1
      Port Name: TEXT_MESSAGE
      Text: Hello mesh!
  [+] Radio Metrics
      SNR: 8.5 dB
      RSSI: -85 dBm
```

### Path Tracing

Tracks message path through relay nodes with:
- Per-hop signal quality (SNR, RSSI)
- Latency measurements
- Success/failure states
- Geographic positions (when available)

## Usage

### From Python

```python
from monitoring.traffic_inspector import TrafficInspector

inspector = TrafficInspector()

# Capture a packet (typically from meshtastic/RNS callbacks)
packet = inspector.capture(data, metadata)

# Get recent packets with filter
filtered = inspector.get_packets(filter="mesh.hops > 2")

# Get statistics
stats = inspector.get_stats()

# Trace message path
trace = inspector.trace_path(packet_id)
```

### From TUI

Navigate to: **Maps & Viz > Traffic Inspector**

Menu options:
1. View Live Traffic
2. Packet List
3. Apply Filter
4. Packet Details
5. Path Visualization
6. Traffic Statistics
7. Filter Reference
8. Export Data
9. Clear Capture

## Architecture Inspiration

Based on Wireshark's design principles:

1. **Dissector Framework**: Protocol-specific parsers that build structured data
2. **Protocol Tree**: N-way tree for hierarchical packet display
3. **Display Filters**: Field-based filtering with abbreviated names
4. **Statistics**: Aggregated traffic analysis

Also influenced by:
- [Datadog Network Path](https://www.datadoghq.com/blog/network-path/) - hop-by-hop visualization
- [meshtastic-network-visualization](https://github.com/filipsPL/meshtastic-network-visualization/) - mesh graph concepts

## Integration Points

The traffic inspector can receive packets from:
- meshtasticd via TCP interface (`NodeMonitor`)
- MQTT broker (`MQTTNodelessSubscriber`)
- RNS path table changes (`PathTableMonitor`)
- Gateway bridge message queue (`PersistentMessageQueue`)

## Files Changed/Created

```
src/monitoring/
├── traffic_inspector.py   # NEW: Core traffic inspection (~1,400 lines)
├── path_visualizer.py     # NEW: Path visualization (~1,160 lines)

src/launcher_tui/
├── main.py                # MODIFIED: Added TrafficInspectorMixin
├── traffic_inspector_mixin.py  # NEW: TUI menu integration (~450 lines)

tests/
├── test_traffic_inspector.py  # NEW: Comprehensive tests (~550 lines)
```

## Next Steps

1. **Real-time Integration**: Connect to meshtasticd callbacks for live capture
2. **MQTT Integration**: Capture MQTT-relayed traffic
3. **Path Reconstruction**: Use traceroute packets to build complete paths
4. **Persistence**: Long-term traffic history for trend analysis
5. **Alerts**: Configurable alerts for signal degradation or failures
