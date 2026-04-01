# MeshAnchor Grafana Dashboards

Pre-built Grafana dashboards for monitoring MeshAnchor mesh networks.

## Available Dashboards

### Prometheus Dashboards

| Dashboard | Description | UID |
|-----------|-------------|-----|
| **MeshAnchor Overview** | System health, service status, message queues | `meshanchor-overview` |
| **MeshAnchor Node Metrics** | Per-node SNR, RSSI, battery, status table | `meshanchor-nodes` |
| **MeshAnchor Gateway** | Gateway connections, message flow, errors | `meshanchor-gateway` |

### InfluxDB Dashboard

| Dashboard | Description | UID |
|-----------|-------------|-----|
| **MeshAnchor InfluxDB** | Node trends, signal quality, message activity | `meshanchor-influxdb` |

### Grafana Infinity Plugin Dashboard

| Dashboard | Description | UID |
|-----------|-------------|-----|
| **MeshAnchor Infinity** | JSON API integration (no Prometheus required) | `meshanchor-infinity` |

## Quick Start

### 1. Enable MeshAnchor Metrics Server

```python
from utils.metrics_export import start_metrics_server

# Start on default port 9090
server = start_metrics_server()

# Or specify a custom port
server = start_metrics_server(port=9091)
```

Or use the textfile exporter for node_exporter:

```python
from utils.metrics_export import setup_textfile_exporter

# Writes to /var/lib/node_exporter/textfile_collector/meshanchor.prom
setup_textfile_exporter()
```

### 2a. Configure Prometheus (for Prometheus dashboards)

Add to your `prometheus.yml`:

```yaml
scrape_configs:
  - job_name: 'meshanchor'
    static_configs:
      - targets: ['localhost:9090']
    scrape_interval: 15s
```

### 2b. Configure InfluxDB (for InfluxDB dashboard)

```python
from utils.metrics_export import start_influxdb_exporter

# InfluxDB 2.x
exporter = start_influxdb_exporter(
    url="http://localhost:8086",
    token="your-token",
    org="meshanchor",
    bucket="metrics",
    interval=15
)

# InfluxDB 1.x
exporter = start_influxdb_exporter(
    url="http://localhost:8086",
    database="meshanchor",
    interval=15
)
```

### 2c. Configure Infinity Plugin (for JSON API dashboard)

1. Install the Infinity data source plugin in Grafana
2. Add a new Infinity data source with URL: `http://localhost:9090`
3. No authentication required

The JSON API endpoints are:
- `/api/json/metrics` - System metrics
- `/api/json/nodes` - Node list
- `/api/json/status` - Service status

### 3. Import Dashboards to Grafana

1. Open Grafana (default: http://localhost:3000)
2. Go to **Dashboards** > **Import**
3. Upload the JSON file or paste its contents
4. Select your Prometheus data source
5. Click **Import**

## Dashboard Details

### MeshAnchor Overview

Main dashboard showing:
- Overall health score (0-100%)
- System uptime
- Tracked node count
- Message queue depth
- Dead letter count
- Service health table
- Health score trends over time

### MeshAnchor Node Metrics

Node-specific metrics:
- Average SNR, RSSI, battery levels
- Per-node signal quality over time
- Node status table with color-coded values
- Battery monitoring trends
- Variable selector for filtering nodes

### MeshAnchor Gateway

Gateway bridge monitoring:
- Meshtastic/RNS connection status
- Reconnect and error counts
- Message throughput (incoming/outgoing)
- Queue depth visualization
- Retry and dead letter tracking
- Error breakdown by type

## Metrics Reference

| Metric | Type | Description |
|--------|------|-------------|
| `meshanchor_health_score` | gauge | Health scores by category (0-100) |
| `meshanchor_service_healthy` | gauge | Service health (1=up, 0=down) |
| `meshanchor_service_uptime_percent` | gauge | Service uptime percentage |
| `meshanchor_nodes_total` | gauge | Total tracked nodes |
| `meshanchor_node_snr` | gauge | Node SNR in dB |
| `meshanchor_node_rssi` | gauge | Node RSSI in dBm |
| `meshanchor_node_battery_percent` | gauge | Node battery level |
| `meshanchor_messages_total` | counter | Message counts by direction/status |
| `meshanchor_message_queue_depth` | gauge | Current queue sizes |
| `meshanchor_gateway_connections` | gauge | Active gateway connections |
| `meshanchor_gateway_errors_total` | counter | Gateway errors by type |
| `meshanchor_env_temperature_celsius` | gauge | Node temperature (Celsius) |
| `meshanchor_env_humidity_percent` | gauge | Node humidity (%) |
| `meshanchor_env_pressure_hpa` | gauge | Barometric pressure (hPa) |
| `meshanchor_env_gas_resistance_ohms` | gauge | VOC gas resistance (BME680) |
| `meshanchor_air_quality_pm25` | gauge | PM2.5 particulate (ug/m3) |
| `meshanchor_air_quality_co2_ppm` | gauge | CO2 concentration (ppm) |
| `meshanchor_air_quality_iaq` | gauge | Indoor Air Quality index |
| `meshanchor_health_heart_bpm` | gauge | Heart rate (BPM) |
| `meshanchor_health_spo2_percent` | gauge | Blood oxygen saturation (%) |
| `meshanchor_mqtt_connected` | gauge | MQTT subscriber connected |
| `meshanchor_mqtt_nodes_total` | gauge | Total MQTT-discovered nodes |
| `meshanchor_mqtt_nodes_online` | gauge | Online MQTT nodes |
| `meshanchor_mqtt_mesh_size` | gauge | 24h unique nodes via MQTT |
| `meshanchor_topology_nodes` | gauge | Topology graph node count |
| `meshanchor_topology_edges` | gauge | Topology graph edge count |

## Alerting Examples

Add these to your Prometheus alerting rules:

```yaml
groups:
  - name: meshanchor
    rules:
      - alert: MeshAnchorHealthLow
        expr: meshanchor_health_score{category="overall"} < 50
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "MeshAnchor health score is low"

      - alert: MeshAnchorServiceDown
        expr: meshanchor_service_healthy == 0
        for: 2m
        labels:
          severity: critical
        annotations:
          summary: "MeshAnchor service {{ $labels.service }} is down"

      - alert: MeshAnchorDeadLetters
        expr: meshanchor_dead_letter_count > 10
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "Dead letter queue growing"
```

## Requirements

- Grafana 9.0+
- Prometheus 2.0+
- MeshAnchor with metrics server running

## Customization

These dashboards use template variables and can be customized:

1. Change time ranges in Grafana
2. Add additional panels
3. Modify thresholds (red/yellow/green zones)
4. Add annotations for deployments/events
