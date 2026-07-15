"""Prometheus/Grafana HTTP request handler for the metrics exporter.

Extracted from prometheus_exporter.py 2026-07-14 for MF025 size compliance
(pure code motion). ``MetricsHTTPHandler`` serves the Prometheus exposition
endpoint and the Grafana JSON API off a ``PrometheusExporter`` instance (bound
by ``metrics_server`` via a subclass). ``prometheus_exporter`` re-exports this
class, so ``from utils.prometheus_exporter import MetricsHTTPHandler`` still
works for every consumer.

``from __future__ import annotations`` keeps the ``Optional[PrometheusExporter]``
class annotation a string, so no runtime import of the exporter is needed here
(the TYPE_CHECKING guard covers type-checkers) — avoiding a circular import,
since prometheus_exporter imports THIS module at load for the re-export.
``_MAX_QUERY_BODY`` and ``_collect_node_geojson`` are imported from
prometheus_exporter (defined there before the re-export line, so circular-safe).
"""
from __future__ import annotations

import http.server
import json
import logging
import time
from datetime import datetime
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

# All defined in prometheus_exporter BEFORE the re-export line, so importing them
# here is circular-safe. None are test-patched via prometheus_exporter.* (the
# handler's health/status/query methods carried these module globals over).
from utils.prometheus_exporter import (
    _HAS_MQTT_SUBSCRIBER,
    _HAS_SERVICE_CHECK,
    _HAS_VERSION,
    _MAX_QUERY_BODY,
    _collect_node_geojson,
    _meshanchor_version,
    check_service,
    get_local_subscriber,
)

if TYPE_CHECKING:
    from utils.prometheus_exporter import PrometheusExporter

logger = logging.getLogger(__name__)


class MetricsHTTPHandler(http.server.BaseHTTPRequestHandler):
    """HTTP handler for Prometheus metrics endpoint and Grafana JSON API."""

    exporter: Optional[PrometheusExporter] = None

    def do_GET(self):
        """Handle GET request."""
        # CORS headers for Grafana
        if self.path == "/metrics":
            self._serve_metrics()
        elif self.path == "/health" or self.path == "/healthz":
            self._serve_health()
        # Grafana JSON API endpoints
        elif self.path == "/api/json/metrics":
            self._serve_json_metrics()
        elif self.path == "/api/json/nodes":
            self._serve_json_nodes()
        elif self.path == "/api/json/status":
            self._serve_json_status()
        # Prometheus API endpoints (for Grafana Prometheus data source)
        elif self.path.startswith("/api/v1/query_range"):
            self._serve_prometheus_query_range()
        elif self.path.startswith("/api/v1/query"):
            self._serve_prometheus_query()
        elif self.path.startswith("/api/v1/labels"):
            self._serve_prometheus_labels()
        elif self.path.startswith("/api/v1/label"):
            self._serve_prometheus_label_values()
        elif self.path == "/":
            self._serve_index()
        else:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"Not Found")

    def _add_cors_headers(self):
        """Add CORS headers for Grafana."""
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Accept, Authorization")

    def do_OPTIONS(self):
        """Handle OPTIONS request (CORS preflight)."""
        self.send_response(200)
        self._add_cors_headers()
        self.end_headers()

    def do_POST(self):
        """Handle POST request (for Grafana Prometheus data source)."""
        # Grafana Prometheus data source sends POST requests
        # Route to same handlers as GET for our endpoints
        if self.path == "/metrics":
            self._serve_metrics()
        elif self.path == "/api/json/metrics":
            self._serve_json_metrics()
        elif self.path == "/api/json/nodes":
            self._serve_json_nodes()
        elif self.path == "/api/json/status":
            self._serve_json_status()
        # Prometheus API compatibility endpoints
        elif self.path.startswith("/api/v1/query_range"):
            self._serve_prometheus_query_range()
        elif self.path.startswith("/api/v1/query"):
            self._serve_prometheus_query()
        elif self.path.startswith("/api/v1/labels"):
            self._serve_prometheus_labels()
        elif self.path.startswith("/api/v1/label"):
            self._serve_prometheus_label_values()
        else:
            self.send_response(404)
            self._add_cors_headers()
            self.end_headers()
            self.wfile.write(b"Not Found")

    def _serve_prometheus_query(self):
        """Serve Prometheus query API for Grafana compatibility."""
        import json
        import re
        import urllib.parse

        # Parse query from URL or body
        query = ""
        if "?" in self.path:
            params = urllib.parse.parse_qs(self.path.split("?", 1)[1])
            query = params.get("query", [""])[0]
        else:
            # Read POST body
            try:
                content_length = int(self.headers.get('Content-Length', 0))
            except (ValueError, TypeError):
                content_length = 0
            if 0 < content_length <= _MAX_QUERY_BODY:
                body = self.rfile.read(content_length).decode('utf-8')
                params = urllib.parse.parse_qs(body)
                query = params.get("query", [""])[0]

        # Return metrics in Prometheus API format
        result = {
            "status": "success",
            "data": {
                "resultType": "vector",
                "result": []
            }
        }

        try:
            # Parse all metrics from exporter
            if self.exporter:
                metrics_text = self.exporter.export()
                now = time.time()

                # Parse Prometheus format lines: metric_name{labels} value
                # or metric_name value
                metric_pattern = re.compile(
                    r'^([a-zA-Z_][a-zA-Z0-9_]*)(\{[^}]*\})?\s+([0-9.eE+-]+|NaN|Inf|-Inf)$'
                )

                for line in metrics_text.split('\n'):
                    line = line.strip()
                    if not line or line.startswith('#'):
                        continue

                    match = metric_pattern.match(line)
                    if match:
                        metric_name = match.group(1)
                        labels_str = match.group(2) or ""
                        value = match.group(3)

                        # Filter by query if specified
                        if query and query not in metric_name:
                            continue

                        # Parse labels
                        labels = {"__name__": metric_name, "job": "meshanchor"}
                        if labels_str:
                            # Parse {key="value",key2="value2"}
                            label_pattern = re.compile(r'([a-zA-Z_][a-zA-Z0-9_]*)="([^"]*)"')
                            for label_match in label_pattern.finditer(labels_str):
                                labels[label_match.group(1)] = label_match.group(2)

                        result["data"]["result"].append({
                            "metric": labels,
                            "value": [now, value]
                        })

            # Always include 'up' metric
            if not query or "up" in query:
                result["data"]["result"].append({
                    "metric": {"__name__": "up", "job": "meshanchor"},
                    "value": [time.time(), "1"]
                })

        except Exception as e:
            logger.debug(f"Query error: {e}")

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self._add_cors_headers()
        self.end_headers()
        self.wfile.write(json.dumps(result).encode('utf-8'))

    def _serve_prometheus_labels(self):
        """Serve Prometheus labels API for Grafana compatibility."""
        import json

        result = {
            "status": "success",
            "data": ["__name__", "job", "service", "node_id", "state", "network"]
        }

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self._add_cors_headers()
        self.end_headers()
        self.wfile.write(json.dumps(result).encode('utf-8'))

    def _serve_prometheus_query_range(self):
        """Serve Prometheus query_range API for Grafana time-series panels."""
        import json
        import re
        import urllib.parse

        # Parse query from URL or body
        query = ""
        if "?" in self.path:
            params = urllib.parse.parse_qs(self.path.split("?", 1)[1])
            query = params.get("query", [""])[0]
        else:
            try:
                content_length = int(self.headers.get('Content-Length', 0))
            except (ValueError, TypeError):
                content_length = 0
            if 0 < content_length <= _MAX_QUERY_BODY:
                body = self.rfile.read(content_length).decode('utf-8')
                params = urllib.parse.parse_qs(body)
                query = params.get("query", [""])[0]

        # Return metrics in Prometheus API matrix format
        result = {
            "status": "success",
            "data": {
                "resultType": "matrix",
                "result": []
            }
        }

        try:
            if self.exporter:
                metrics_text = self.exporter.export()
                now = time.time()

                metric_pattern = re.compile(
                    r'^([a-zA-Z_][a-zA-Z0-9_]*)(\{[^}]*\})?\s+([0-9.eE+-]+|NaN|Inf|-Inf)$'
                )

                for line in metrics_text.split('\n'):
                    line = line.strip()
                    if not line or line.startswith('#'):
                        continue

                    match = metric_pattern.match(line)
                    if match:
                        metric_name = match.group(1)
                        labels_str = match.group(2) or ""
                        value = match.group(3)

                        if query and query not in metric_name:
                            continue

                        labels = {"__name__": metric_name, "job": "meshanchor"}
                        if labels_str:
                            label_pattern = re.compile(r'([a-zA-Z_][a-zA-Z0-9_]*)="([^"]*)"')
                            for label_match in label_pattern.finditer(labels_str):
                                labels[label_match.group(1)] = label_match.group(2)

                        # For query_range, return values array (time series)
                        # We only have current value, so return single point
                        result["data"]["result"].append({
                            "metric": labels,
                            "values": [[now, value]]
                        })

        except Exception as e:
            logger.debug(f"Query range error: {e}")

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self._add_cors_headers()
        self.end_headers()
        self.wfile.write(json.dumps(result).encode('utf-8'))

    def _serve_prometheus_label_values(self):
        """Serve Prometheus label values API for Grafana variable queries."""
        import json
        import re

        # Extract label name from path: /api/v1/label/<label>/values
        label_match = re.search(r'/api/v1/label/([^/]+)/values', self.path)
        label_name = label_match.group(1) if label_match else ""

        values = set()

        try:
            if self.exporter and label_name:
                metrics_text = self.exporter.export()

                if label_name == "__name__":
                    # Return metric names
                    metric_pattern = re.compile(r'^([a-zA-Z_][a-zA-Z0-9_]*)')
                    for line in metrics_text.split('\n'):
                        line = line.strip()
                        if not line or line.startswith('#'):
                            continue
                        match = metric_pattern.match(line)
                        if match:
                            values.add(match.group(1))
                else:
                    # Return label values
                    label_pattern = re.compile(rf'{label_name}="([^"]*)"')
                    for line in metrics_text.split('\n'):
                        for match in label_pattern.finditer(line):
                            values.add(match.group(1))

        except Exception as e:
            logger.debug(f"Label values error: {e}")

        result = {
            "status": "success",
            "data": sorted(list(values))
        }

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self._add_cors_headers()
        self.end_headers()
        self.wfile.write(json.dumps(result).encode('utf-8'))

    def _serve_index(self):
        """Serve index page with available endpoints."""
        content = """MeshAnchor Metrics Server

Endpoints:
  /metrics          - Prometheus format (for Prometheus scraper)
  /health           - Health check
  /api/json/metrics - JSON metrics (for Grafana Infinity plugin)
  /api/json/nodes   - Node data JSON
  /api/json/status  - System status JSON
  /api/v1/query     - Prometheus API (for Grafana Prometheus data source)
  /api/v1/labels    - Prometheus labels API

Grafana Setup (Option 1 - Prometheus data source):
  1. Add data source: Type = Prometheus
  2. URL = http://localhost:9090
  3. Query: meshanchor_uptime_seconds, etc.

Grafana Setup (Option 2 - Infinity plugin):
  1. Install 'Infinity' data source plugin
  2. Add data source: URL = http://localhost:9090
  3. Query: /api/json/metrics or /api/json/nodes
"""
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(content.encode('utf-8'))

    def _serve_metrics(self):
        """Serve Prometheus metrics."""
        if self.exporter is None:
            self.send_response(503)
            self.end_headers()
            self.wfile.write(b"Exporter not initialized")
            return

        try:
            content = self.exporter.export()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
            self.end_headers()
            self.wfile.write(content.encode('utf-8'))

        except Exception as e:
            logger.error(f"Error serving metrics: {e}")
            self.send_response(500)
            self.end_headers()
            self.wfile.write(str(e).encode('utf-8'))

    def _serve_json_metrics(self):
        """Serve metrics as JSON for Grafana Infinity plugin."""
        import json

        try:
            metrics = {}

            # Get node counts from MapDataCollector (uses shared cache)
            geojson = _collect_node_geojson()
            if geojson:
                props = geojson.get('properties', {})
                metrics['nodes_total'] = props.get('total_nodes', 0)
                metrics['nodes_with_gps'] = props.get('nodes_with_position', 0)
                metrics['sources'] = props.get('sources', {})
            else:
                metrics['nodes_total'] = 0
                metrics['nodes_with_gps'] = 0

            # Get service status
            if _HAS_SERVICE_CHECK:
                try:
                    mesh_status = check_service("meshtasticd")
                    rns_status = check_service("rnsd")
                    metrics['meshtasticd_running'] = 1 if mesh_status.available else 0
                    metrics['rnsd_running'] = 1 if rns_status.available else 0
                except Exception:
                    metrics['meshtasticd_running'] = 0
                    metrics['rnsd_running'] = 0
            else:
                metrics['meshtasticd_running'] = 0
                metrics['rnsd_running'] = 0

            # MQTT stats
            if _HAS_MQTT_SUBSCRIBER:
                try:
                    subscriber = get_local_subscriber()
                    mqtt_stats = subscriber.get_stats()
                    metrics['mqtt_connected'] = 1 if subscriber.is_connected() else 0
                    metrics['mqtt_nodes'] = mqtt_stats.get('node_count', 0)
                    metrics['mqtt_online'] = mqtt_stats.get('online_count', 0)
                    metrics['mqtt_mesh_size_24h'] = mqtt_stats.get('mesh_size_24h', 0)
                    metrics['mqtt_nodes_with_env'] = mqtt_stats.get('nodes_with_env_metrics', 0)
                    metrics['mqtt_nodes_with_aq'] = mqtt_stats.get('nodes_with_aq_metrics', 0)
                    metrics['mesh_health_status'] = mqtt_stats.get('mesh_health_status', 'unknown')
                except Exception:
                    metrics['mqtt_connected'] = 0
            else:
                metrics['mqtt_connected'] = 0

            # Uptime
            if self.exporter:
                metrics['uptime_seconds'] = time.time() - self.exporter.start_time

            metrics['timestamp'] = datetime.now().isoformat()

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self._add_cors_headers()
            self.end_headers()
            self.wfile.write(json.dumps(metrics, indent=2).encode('utf-8'))

        except Exception as e:
            logger.error(f"Error serving JSON metrics: {e}")
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": str(e)}).encode('utf-8'))

    def _serve_json_nodes(self):
        """Serve node data as JSON for Grafana."""
        import json

        try:
            nodes = []

            geojson = _collect_node_geojson()
            if geojson:
                try:
                    for feature in geojson.get('features', []):
                        props = feature.get('properties', {})
                        coords = feature.get('geometry', {}).get('coordinates', [0, 0])
                        node_data = {
                            'id': props.get('id', ''),
                            'name': props.get('name', ''),
                            'lat': coords[1] if len(coords) > 1 else 0,
                            'lon': coords[0] if len(coords) > 0 else 0,
                            'snr': props.get('snr'),
                            'rssi': props.get('rssi'),
                            'battery': props.get('battery'),
                            'last_heard': props.get('last_heard'),
                            'online': props.get('online', False),
                            'hardware': props.get('hardware', ''),
                            'role': props.get('role', ''),
                            # Environment sensors
                            'temperature': props.get('temperature'),
                            'humidity': props.get('humidity'),
                            'pressure': props.get('pressure'),
                            # Air quality
                            'pm25': props.get('pm25'),
                            'co2': props.get('co2'),
                            'iaq': props.get('iaq'),
                        }
                        nodes.append(node_data)
                except Exception as e:
                    logger.debug(f"Node collection error: {e}")

            result = {
                'timestamp': datetime.now().isoformat(),
                'count': len(nodes),
                'nodes': nodes
            }

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self._add_cors_headers()
            self.end_headers()
            self.wfile.write(json.dumps(result, indent=2).encode('utf-8'))

        except Exception as e:
            logger.error(f"Error serving JSON nodes: {e}")
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": str(e)}).encode('utf-8'))

    def _serve_json_status(self):
        """Serve system status as JSON."""
        import json

        version = _meshanchor_version if _HAS_VERSION else "unknown"

        status = {
            'version': version,
            'timestamp': datetime.now().isoformat(),
            'services': {},
        }

        # Check services via the SSOT service layer, not raw `systemctl` (MF008):
        # check_service() carries sudo/degraded-daemon semantics + the fleet's
        # ServiceState vocabulary, so /api/json/status agrees with /metrics and
        # the fleet views. (QA audit 2026-07-06.)
        for svc in ['meshtasticd', 'rnsd', 'mosquitto', 'grafana-server']:
            if _HAS_SERVICE_CHECK:
                try:
                    status['services'][svc] = check_service(svc).state.value
                except Exception:
                    status['services'][svc] = 'unknown'
            else:
                status['services'][svc] = 'unknown'

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self._add_cors_headers()
        self.end_headers()
        self.wfile.write(json.dumps(status, indent=2).encode('utf-8'))

    def _serve_health(self):
        """Serve health check endpoint."""
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"OK")

    def log_message(self, format, *args):
        """Suppress default logging."""
        pass

