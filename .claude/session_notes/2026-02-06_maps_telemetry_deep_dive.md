# Maps & Telemetry Deep Dive - Session Notes

**Date**: 2026-02-06
**Branch**: `claude/maps-telemetry-deep-dive-ms1F8`
**Session**: Deep gap analysis and wiring fixes for maps, telemetry, sensors, topology, Grafana, Prometheus

## Gap Analysis Results

### What Was Missing

| Component | Gap | Severity |
|-----------|-----|----------|
| **Prometheus** | No environment sensor metrics (temp, humidity, pressure, gas, air quality) | High |
| **Prometheus** | Battery metric defined but never emitted | High |
| **Prometheus** | No MQTT subscriber stats exported | Medium |
| **Prometheus** | No topology graph stats exported | Medium |
| **Prometheus** | No health/wearable metrics (heart rate, SpO2) | Medium |
| **InfluxDB** | No environment sensor data written | High |
| **InfluxDB** | No MQTT stats or health metrics | Medium |
| **Map GeoJSON** | TCP nodes missing sensor data (temperature, humidity, pressure) | Medium |
| **Map GeoJSON** | MQTT collector only used cache file, not live subscriber | High |
| **JSON API** | `/api/json/nodes` missing RSSI, hardware, role, sensor fields | Medium |
| **JSON API** | `/api/json/metrics` missing MQTT stats | Low |
| **Grafana** | No environment sensor dashboard panels | High |
| **Grafana** | No air quality dashboard panels | Medium |
| **Grafana** | No MQTT stats panels | Medium |
| **Grafana** | No topology stats panels | Low |

### What Was Already Working

- SNR/RSSI per-node Prometheus metrics
- Service health monitoring (meshtasticd, rnsd, mosquitto)
- Gateway connection tracking
- TCP connection monitoring
- RNS sniffer metrics
- Wireshark-style packet capture/dissection
- D3.js topology visualization
- Topology snapshot storage
- Coverage map generation
- Live map (node_map.html) with multi-phase features
- Message queue statistics

## Changes Made

### 1. metrics_common.py - New Metric Definitions (+18 metrics)
- `meshforge_env_temperature_celsius` - BME280/680/BMP280 temperature
- `meshforge_env_humidity_percent` - Relative humidity
- `meshforge_env_pressure_hpa` - Barometric pressure
- `meshforge_env_gas_resistance_ohms` - BME680 VOC sensor
- `meshforge_air_quality_pm25` - PM2.5 particulate
- `meshforge_air_quality_pm10` - PM10 particulate
- `meshforge_air_quality_co2_ppm` - CO2 concentration
- `meshforge_air_quality_iaq` - Indoor Air Quality index
- `meshforge_health_heart_bpm` - Heart rate (Meshtastic 2.7+)
- `meshforge_health_spo2_percent` - Blood oxygen saturation
- `meshforge_mqtt_nodes_total` - MQTT discovered nodes
- `meshforge_mqtt_nodes_online` - MQTT online nodes
- `meshforge_mqtt_connected` - MQTT connection state
- `meshforge_mqtt_messages_received` - MQTT message counter
- `meshforge_mqtt_mesh_size` - 24h unique nodes
- `meshforge_topology_nodes` - Topology graph nodes
- `meshforge_topology_edges` - Topology graph edges
- `meshforge_topology_snapshots` - Stored snapshots

### 2. prometheus_exporter.py - Three New Collectors
- `_collect_environment_metrics()` - Reads from live MQTT subscriber for temp/humidity/pressure/gas/air quality/health
- `_collect_mqtt_metrics()` - MQTT connection state, node counts, mesh size
- `_collect_topology_metrics()` - Topology snapshot stats
- Fixed `_collect_node_metrics()` to emit `meshforge_node_battery_percent` (was defined but never output)
- Enhanced JSON API: `/api/json/nodes` now includes RSSI, hardware, role, sensor data
- Enhanced JSON API: `/api/json/metrics` now includes MQTT stats

### 3. influxdb_exporter.py - Sensor Data Export
- MQTT stats measurement (`meshforge_mqtt`)
- Per-node environment sensors (`meshforge_environment`)
- Per-node air quality (`meshforge_air_quality`)
- Per-node health metrics (`meshforge_health`)

### 4. map_data_collector.py - Enhanced Data Flow
- `_make_feature()` now accepts sensor fields (temperature, humidity, pressure, pm25, co2, iaq, channel_utilization, air_util_tx, rssi)
- `_parse_tcp_node()` now extracts `environmentMetrics` from meshtasticd API
- `_collect_mqtt()` now tries live subscriber singleton first (has real-time sensor data), falls back to cache file
- Sensor data flows through GeoJSON to map popups

### 5. Grafana Dashboard - New Panels
- **Environment Sensors** row: Temperature, Humidity, Barometric Pressure (timeseries)
- **Air Quality** row: PM2.5 with EPA thresholds, CO2 with health thresholds (timeseries)
- **MQTT Network** row: Connection status, total/online nodes, mesh size, topology nodes (stat panels)

### 6. Tests - 11 New Tests
- `TestEnvironmentMetricDefinitions` (5 tests) - Verify all new metrics defined correctly
- `TestEnvironmentCollector` (3 tests) - Mock MQTT subscriber with sensor data
- `TestMQTTCollector` (1 test) - Mock subscriber stats
- `TestTopologyCollector` (2 tests) - Mock snapshot store

## Test Results
- **All 11 new tests pass**
- **3039 passing** (full suite)
- **49 failing** (all pre-existing, none from this change)
- **Linter**: Clean (no MF001/MF002/MF003 violations)
- **auto_review**: 72 issues (all pre-existing, none in modified files)

## Data Flow Summary (After Fixes)

```
Meshtastic Nodes (radio/TCP)
    ├─ environmentMetrics → map_data_collector → GeoJSON → Map Popups
    ├─ deviceMetrics → map_data_collector → GeoJSON → Map Popups
    └─ snr/rssi/battery → MetricsHistory → Prometheus → Grafana

MQTT Subscriber (mqtt.meshtastic.org)
    ├─ temperature/humidity/pressure → Prometheus → Grafana (Environment Sensors)
    ├─ pm25/co2/iaq → Prometheus → Grafana (Air Quality)
    ├─ heart_bpm/spo2 → Prometheus → Grafana (Health)
    ├─ node_count/online/mesh_size → Prometheus → Grafana (MQTT Network)
    ├─ get_geojson() → map_data_collector → Map Popups
    └─ sensor data → InfluxDB (meshforge_environment, meshforge_air_quality)

Topology Snapshot Store
    └─ node_count/edge_count/snapshot_count → Prometheus → Grafana
```

## Entropy Check
- Session coherence: **HIGH** - systematic gap analysis and targeted fixes
- No drift from scope observed
- All changes directly address identified data flow gaps
