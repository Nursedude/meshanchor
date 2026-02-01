# Network Awareness Deep Dive - Preparation Notes

## Previous Session Summary (2026-01-30)

**Branch**: `claude/multiple-fixes-features-lTXaD`
**Commits**:
- `49338e0` feat: Add A-index to band condition assessment

**Completed**:
- A-index now fetched from NOAA and factored into low-band (160m/80m/40m) propagation assessment
- NOAA extraction from hamclock.py deferred (file under 1500 line limit, architecture sound)

---

## Deep Dive Goal: Network/RF Awareness

**Problem**: MeshForge struggles to see RNS and other topologies/nodes reliably.

**Objective**: Make MeshForge a true NOC that understands the network around it - maps, sensors, connectivity.

---

## Current State of Network Awareness

### What Exists

| Component | Location | What It Does |
|-----------|----------|--------------|
| **Node Tracker** | `gateway/node_tracker.py` | Unified RNS/Meshtastic tracking, `UnifiedNode` model |
| **RNS Discovery** | `gateway/node_tracker.py` | Announce handler + path_table polling |
| **Meshtastic Connection** | `utils/meshtastic_connection.py` | TCP singleton to meshtasticd |
| **MQTT Nodeless** | `monitoring/mqtt_subscriber.py` | Remote observation without hardware |
| **Coverage Maps** | `utils/coverage_map.py` | Folium maps with link quality |
| **Signal Trending** | `utils/signal_trending.py` | Per-node SNR/RSSI time-series |
| **Health Scoring** | `utils/health_score.py` | 0-100 network health |
| **Map Data Service** | `utils/map_data_service.py` | Unified GeoJSON + HTTP server |

### Known Gaps (To Investigate)

1. **RNS visibility** - Path table polling may miss transient nodes
2. **Topology mapping** - No graph representation of mesh connectivity
3. **Multi-hop visibility** - Only see direct peers, not full routing paths
4. **RF environment sensing** - No spectrum analysis or interference detection
5. **Protocol-level insight** - Need deeper packet inspection

---

## RNS Discovery Deep Dive

### Current Implementation (`node_tracker.py`)

```python
# Three data sources (priority order):
1. RNS.Transport.path_table       # Complete routing with hop counts
2. RNS.Identity.known_destinations  # Cached identities
3. RNS.Transport.destinations     # Local destinations (fallback)

# Announce handler registered for LXMF aspect
RNS.Transport.register_announce_handler(
    self._on_rns_announce,
    aspect_filter="lxmf.delivery"
)
```

### Limitation
- Only sees LXMF announces (messaging apps like Sideband/Nomad)
- Standard RNS services using other aspects are invisible
- Position data only present when Sideband GPS sharing enabled

### Potential Improvements
- Register announce handlers for additional aspects
- Monitor `RNS.Transport.active_links` for active connections
- Poll `RNS.Transport.path_table` more frequently
- Parse announce app_data for non-LXMF services

---

## Meshtastic Discovery

### Current Flow
```
meshtasticd (TCP:4403) ──► MeshtasticConnection ──► NodeTracker
       │
       └──► pubsub: meshtastic.receive ──► telemetry updates
```

### Data Available
- Node list with positions, telemetry, metrics
- Channel utilization, SNR, RSSI, hops_away
- Device metrics: battery, voltage, uptime

### Gap
- No direct radio access (goes through meshtasticd)
- Limited to what meshtasticd exposes via TCP

---

## Wireshark / Packet Analysis Angle

Reference: https://www.wireshark.org/docs/wsug_html/

### Potential Applications

1. **RNS Protocol Analysis**
   - Capture RNS traffic on interfaces
   - Decode announce packets, link establishment
   - Visualize routing table changes over time

2. **LoRa Packet Inspection**
   - If SDR available, capture raw LoRa frames
   - Analyze spreading factor, coding rate in use
   - Detect interference patterns

3. **Network Traffic Patterns**
   - Message flow visualization
   - Latency measurement between hops
   - Protocol efficiency analysis

### Implementation Options
- `pyshark` - Python Wireshark bindings
- `scapy` - Packet crafting/parsing
- Direct RNS API inspection (no capture needed)
- Custom dissectors for RNS/Meshtastic protocols

---

## Key Files for Deep Dive

### Primary
- `src/gateway/node_tracker.py` - Core discovery logic
- `src/gateway/rns_bridge.py` - RNS integration
- `src/utils/meshtastic_connection.py` - Meshtastic TCP

### Supporting
- `src/monitoring/mqtt_subscriber.py` - MQTT data source
- `src/utils/map_data_service.py` - Data aggregation
- `src/utils/coverage_map.py` - Visualization

### Research
- `.claude/research/reticulum_deep_dive.md` - RNS architecture notes
- Reticulum source: `RNS/Transport.py`, `RNS/Identity.py`

---

## Questions to Answer

1. **Why are RNS nodes not visible?**
   - Is rnsd running?
   - Are announce handlers firing?
   - Is path_table being polled?

2. **What aspects beyond LXMF exist?**
   - Need to discover what services are announcing

3. **Can we get topology, not just nodes?**
   - Build graph from hop counts
   - Infer mesh structure from path_table

4. **What RF data is accessible?**
   - SNR/RSSI per link
   - Channel utilization
   - Interference indicators

---

## Suggested Approach

### Phase 1: Diagnostics
- Add verbose logging to node_tracker.py
- Instrument announce handler with counters
- Log path_table changes in real-time

### Phase 2: Data Model
- Graph representation of network topology
- Edge weights from SNR/hop count
- Temporal tracking (link up/down events)

### Phase 3: Visualization
- Network graph view (not just map)
- Link quality heatmap
- Path tracing between nodes

### Phase 4: RF Awareness (if hardware available)
- SDR integration for spectrum view
- LoRa parameter detection
- Interference mapping

---

## Session Artifacts

- Branch: `claude/multiple-fixes-features-lTXaD`
- Previous session notes: `2026-01-30_alpha_branch_review.md`
- This prep document for network awareness deep dive
