# Session: Map Integration Deep Research (2026-02-07, Session 2)

## Branch
`claude/meshforge-reliability-tasks-0FNja`

## Research Sources
- **[meshtastic-map](https://github.com/liamcottle/meshtastic-map)** (Liam Cottle)
  - MQTT NEIGHBORINFO_APP → visual neighbor links
  - TRACEROUTE_APP → path visualization
  - LWT (Last Will Testament) → 30s online/offline detection
  - Hop count + relay node tracking (Meshtastic 2.6+)
  - Telemetry charts (battery, voltage, signal)
  - Stack: Node.js + Express + MySQL/Prisma + MQTT
  - Live at meshtastic.liamcottle.net

- **[AREDN World Map](https://www.arednmesh.org/content/aredn-world-map)**
  - Band/channel filtering
  - Link quality color-coded visualization (RF/DTD/TUN)
  - 3D terrain view, measurement tool
  - [Topologr](https://github.com/captainwasabi/topologrjs) - dedicated topology explorer

- **[NewMeshMap](https://github.com/kn6plv/NewMeshMap)** (kn6plv)
  - Client-side JavaScript, static files
  - MeshInfo generates data files via network walk
  - walk.js: real-time mesh traversal

## Changes Made

### 1. AREDN Node Validation (`map_data_collector.py`)
- **Problem**: `_get_aredn_node_ip()` only tested socket on port 8080 - any HTTP service would pass
- **Fix**: Added HTTP response check to `/a/sysinfo` with JSON validation. Checks for AREDN-specific fields (`node`, `sysinfo`, `meshrf`) before accepting
- **Impact**: Prevents false AREDN detection from non-AREDN services on port 8080

### 2. HTTP Feature Format Normalization (`map_data_collector.py`)
- **Problem**: HTTP API path used `online` instead of `is_online`, missing `network`, `is_local`, `is_gateway`, `hops_away`
- **Fix**: Normalized to match standard GeoJSON feature format used by all other sources
- **Impact**: Consistent properties regardless of data source (HTTP, TCP, MQTT, AREDN, RNS)

### 3. Enhanced Node Popups (`node_map.html`)
**Meshtastic-map inspired:**
- Hop count display with relay node ID
- Channel utilization + congestion warning

**AREDN world map inspired:**
- Link type display (RF=blue, DTD=green, TUN=purple)
- Link quality percentage with quality color coding

**Environment sensors:**
- Temperature, humidity display when available
- All fields null-safe (only shown when data present)

### 4. Visual Staleness Indicator
- Offline nodes render at 40% opacity with 60% grayscale filter
- CSS class `.node-marker-stale` applied when `is_online === false`
- Inspired by meshtastic-map's LWT-based online/offline detection

## Test Results
- **Previous**: 3274 passed
- **After**: 3280 passed (+6 new), 18 skipped, 0 failures
- New tests: 3 AREDN validation, 3 HTTP feature format

## Commits
1. `9ff50b3` - fix: Map reliability - coordinate validation, empty sequence guards, API health (Session 1)
2. `1d12aef` - feat: Routing rules, compatibility shims, and session notes
3. `bddb2d7` - feat: Map integration improvements from meshtastic-map + AREDN research

## Key Architecture Insights

### meshtastic-map Data Pipeline
```
MQTT (mqtt.meshtastic.org) → ServiceEnvelope decode → DB (MySQL/Prisma) → Express API → Frontend
Packet types: NODEINFO, POSITION, NEIGHBORINFO, TELEMETRY, TRACEROUTE, MAP_REPORT
LWT topic: /stat/!{node_id} → online/offline with ~30s delay
```

### AREDN Data Pipeline
```
AREDN Mesh → HTTP API (:8080/a/sysinfo) → Node walk → Static JSON → Web frontend
Link types: RF (radio), DTD (device-to-device LAN), TUN (tunnel/VPN)
Quality: LQM (Link Quality Manager) 0-100% per link
```

### MeshForge Unified Pipeline (what makes it unique)
```
Meshtastic (HTTP/TCP/CLI) ─┐
MQTT (live subscriber)     ─┤
AREDN (HTTP /a/sysinfo)   ─┼→ MapDataCollector → FeatureCollection → HTTP API → Leaflet
RNS (path table)           ─┤
Node cache (disk)          ─┤
Last-known cache           ─┘
```

## Remaining Items (for future sessions)
- [ ] Service Detection Redesign (Issue #20) - HIGH
- [ ] NeighborInfo-based link drawing (real topology, not proximity)
- [ ] Traceroute visualization (TRACEROUTE_APP data)
- [ ] Connection pooling for map data collection
- [ ] WebSocket/SSE for real-time map updates (30s polling → push)
- [ ] AREDN band/channel filtering (AREDN world map pattern)
- [ ] 3D terrain view integration
- [ ] Measurement tool (bearing/distance between nodes)
