"""Prometheus HTTP request handler for the metrics exporter.

Extracted from prometheus_exporter.py 2026-07-14 for MF025 size compliance.
``MetricsHTTPHandler`` serves the Prometheus exposition endpoint off a
``PrometheusExporter`` instance (bound by ``metrics_server`` via a subclass);
``prometheus_exporter`` re-exports this class. The Grafana JSON API, /api/v1/*
shims, POST mirror and wildcard CORS were removed 2026-11-14 (operator; MF
twin) — their only callers were the deleted dashboards.

``from __future__ import annotations`` keeps the ``Optional[PrometheusExporter]``
annotation a string, so no runtime import of the exporter (circular) is needed.
"""
from __future__ import annotations

import http.server
import logging
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from utils.prometheus_exporter import PrometheusExporter

logger = logging.getLogger(__name__)


class MetricsHTTPHandler(http.server.BaseHTTPRequestHandler):
    """HTTP handler for the Prometheus metrics endpoint."""

    exporter: Optional[PrometheusExporter] = None

    def do_GET(self):
        """Handle GET request. Prometheus-only since 2026-11-14: the Grafana
        shims (/api/json/{metrics,nodes,status}, /api/v1/*, the POST mirror,
        wildcard CORS) were removed with the dashboards (operator; MF twin)."""
        if self.path == "/metrics":
            self._serve_metrics()
        elif self.path == "/health" or self.path == "/healthz":
            self._serve_health()
        elif self.path == "/":
            self._serve_index()
        else:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"Not Found")

    def _serve_index(self):
        """Serve index page with available endpoints."""
        content = """MeshAnchor Metrics Server

Endpoints:
  /metrics  - Prometheus format (for a Prometheus scraper)
  /health   - Health check
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

    def _serve_health(self):
        """Serve health check endpoint."""
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"OK")

    def log_message(self, format, *args):
        """Suppress default logging."""
        pass

