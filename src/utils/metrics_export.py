"""
Prometheus and InfluxDB Metrics Export for MeshForge.

This module provides backward compatibility by re-exporting from
the split modules:
- utils.metrics_common - Metric definitions and helpers
- utils.prometheus_exporter - Prometheus format export
- utils.influxdb_exporter - InfluxDB export

Usage:
    from utils.metrics_export import PrometheusExporter, start_metrics_server

    # Option 1: Generate metrics string
    exporter = PrometheusExporter()
    metrics_text = exporter.export()
    print(metrics_text)

    # Option 2: Start HTTP server (for Prometheus scraping)
    server = start_metrics_server(port=9090)
    # Prometheus can now scrape http://localhost:9090/metrics

    # Option 3: Write to file for pushgateway or file-based collection
    exporter.write_to_file("/var/lib/meshforge/metrics.prom")

    # InfluxDB export
    from utils.metrics_export import InfluxDBExporter, start_influxdb_exporter
    exporter = InfluxDBExporter(url="http://localhost:8086", database="meshforge")
    exporter.write_metrics()

Reference:
    Prometheus exposition format:
    https://prometheus.io/docs/instrumenting/exposition_formats/

    InfluxDB Line Protocol:
    https://docs.influxdata.com/influxdb/latest/reference/syntax/line-protocol/
"""

# Re-export from metrics_common
from utils.metrics_common import (
    COUNTER,
    GAUGE,
    HISTOGRAM,
    SUMMARY,
    MetricDefinition,
    METRICS,
    escape_label_value,
    format_labels,
    format_metric_line,
    # Also export with underscore prefix for backward compatibility
    _escape_label_value,
    _format_labels,
    _format_metric_line,
)

# Re-export from prometheus_exporter
from utils.prometheus_exporter import (
    PrometheusExporter,
    MetricsHTTPHandler,
    MetricsServer,
    start_metrics_server,
    setup_textfile_exporter,
)

# Re-export from influxdb_exporter
from utils.influxdb_exporter import (
    InfluxDBExporter,
    start_influxdb_exporter,
)

# Define __all__ for explicit public API
__all__ = [
    # Type constants
    "COUNTER",
    "GAUGE",
    "HISTOGRAM",
    "SUMMARY",
    # Common definitions
    "MetricDefinition",
    "METRICS",
    # Helper functions
    "escape_label_value",
    "format_labels",
    "format_metric_line",
    "_escape_label_value",
    "_format_labels",
    "_format_metric_line",
    # Prometheus exports
    "PrometheusExporter",
    "MetricsHTTPHandler",
    "MetricsServer",
    "start_metrics_server",
    "setup_textfile_exporter",
    # InfluxDB exports
    "InfluxDBExporter",
    "start_influxdb_exporter",
]
