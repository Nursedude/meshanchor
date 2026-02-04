"""
Common metric definitions and utilities for MeshForge exporters.

This module contains shared components used by both Prometheus and InfluxDB
exporters:
- MetricDefinition dataclass
- METRICS dictionary with all metric definitions
- Helper functions for label formatting

Usage:
    from utils.metrics_common import METRICS, MetricDefinition, format_labels
"""

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List

logger = logging.getLogger(__name__)

# Import centralized path utility for sudo compatibility
try:
    from utils.paths import get_real_user_home
except ImportError:
    def get_real_user_home() -> Path:
        """Fallback for when utils.paths is not in Python path."""
        sudo_user = os.environ.get('SUDO_USER', '')
        if sudo_user and sudo_user != 'root' and '/' not in sudo_user and '..' not in sudo_user:
            return Path(f'/home/{sudo_user}')
        logname = os.environ.get('LOGNAME', '')
        if logname and logname != 'root' and '/' not in logname and '..' not in logname:
            return Path(f'/home/{logname}')
        return Path('/root')


# Metric type constants (Prometheus types)
COUNTER = "counter"
GAUGE = "gauge"
HISTOGRAM = "histogram"
SUMMARY = "summary"


@dataclass
class MetricDefinition:
    """Definition of a Prometheus metric."""
    name: str
    metric_type: str
    help_text: str
    labels: List[str]


# MeshForge metric definitions
METRICS: Dict[str, MetricDefinition] = {
    # Health metrics
    "meshforge_service_healthy": MetricDefinition(
        name="meshforge_service_healthy",
        metric_type=GAUGE,
        help_text="Whether a service is healthy (1) or not (0)",
        labels=["service"],
    ),
    "meshforge_service_uptime_percent": MetricDefinition(
        name="meshforge_service_uptime_percent",
        metric_type=GAUGE,
        help_text="Service uptime percentage (0-100)",
        labels=["service"],
    ),
    "meshforge_service_latency_ms": MetricDefinition(
        name="meshforge_service_latency_ms",
        metric_type=GAUGE,
        help_text="Service health check latency in milliseconds",
        labels=["service"],
    ),
    "meshforge_service_consecutive_fails": MetricDefinition(
        name="meshforge_service_consecutive_fails",
        metric_type=GAUGE,
        help_text="Number of consecutive health check failures",
        labels=["service"],
    ),
    "meshforge_health_score": MetricDefinition(
        name="meshforge_health_score",
        metric_type=GAUGE,
        help_text="Overall network health score (0-100)",
        labels=["category"],
    ),

    # Message metrics
    "meshforge_messages_total": MetricDefinition(
        name="meshforge_messages_total",
        metric_type=COUNTER,
        help_text="Total messages processed",
        labels=["direction", "status"],
    ),
    "meshforge_message_queue_depth": MetricDefinition(
        name="meshforge_message_queue_depth",
        metric_type=GAUGE,
        help_text="Current message queue depth",
        labels=["status"],
    ),
    "meshforge_message_retries_total": MetricDefinition(
        name="meshforge_message_retries_total",
        metric_type=COUNTER,
        help_text="Total message retry attempts",
        labels=[],
    ),
    "meshforge_dead_letter_count": MetricDefinition(
        name="meshforge_dead_letter_count",
        metric_type=GAUGE,
        help_text="Messages in dead letter queue",
        labels=[],
    ),

    # Node metrics
    "meshforge_node_snr": MetricDefinition(
        name="meshforge_node_snr",
        metric_type=GAUGE,
        help_text="Node signal-to-noise ratio in dB",
        labels=["node_id"],
    ),
    "meshforge_node_rssi": MetricDefinition(
        name="meshforge_node_rssi",
        metric_type=GAUGE,
        help_text="Node received signal strength in dBm",
        labels=["node_id"],
    ),
    "meshforge_node_last_seen_seconds": MetricDefinition(
        name="meshforge_node_last_seen_seconds",
        metric_type=GAUGE,
        help_text="Seconds since node was last seen",
        labels=["node_id"],
    ),
    "meshforge_node_battery_percent": MetricDefinition(
        name="meshforge_node_battery_percent",
        metric_type=GAUGE,
        help_text="Node battery level percentage",
        labels=["node_id"],
    ),
    "meshforge_nodes_total": MetricDefinition(
        name="meshforge_nodes_total",
        metric_type=GAUGE,
        help_text="Total number of tracked nodes",
        labels=["state"],
    ),

    # Gateway metrics
    "meshforge_gateway_connections": MetricDefinition(
        name="meshforge_gateway_connections",
        metric_type=GAUGE,
        help_text="Number of active gateway connections",
        labels=["network"],
    ),
    "meshforge_gateway_reconnects_total": MetricDefinition(
        name="meshforge_gateway_reconnects_total",
        metric_type=COUNTER,
        help_text="Total reconnection attempts",
        labels=["network"],
    ),
    "meshforge_gateway_errors_total": MetricDefinition(
        name="meshforge_gateway_errors_total",
        metric_type=COUNTER,
        help_text="Total gateway errors",
        labels=["network", "error_type"],
    ),

    # System metrics
    "meshforge_info": MetricDefinition(
        name="meshforge_info",
        metric_type=GAUGE,
        help_text="MeshForge version and build info",
        labels=["version"],
    ),
    "meshforge_uptime_seconds": MetricDefinition(
        name="meshforge_uptime_seconds",
        metric_type=GAUGE,
        help_text="MeshForge process uptime in seconds",
        labels=[],
    ),
    "meshforge_last_scrape_timestamp": MetricDefinition(
        name="meshforge_last_scrape_timestamp",
        metric_type=GAUGE,
        help_text="Unix timestamp of last metrics collection",
        labels=[],
    ),

    # TCP connection metrics
    "meshforge_tcp_connections": MetricDefinition(
        name="meshforge_tcp_connections",
        metric_type=GAUGE,
        help_text="Number of TCP connections by state",
        labels=["state", "port"],
    ),
    "meshforge_tcp_meshtasticd_connections": MetricDefinition(
        name="meshforge_tcp_meshtasticd_connections",
        metric_type=GAUGE,
        help_text="Active connections to meshtasticd (port 4403)",
        labels=["remote_addr"],
    ),
    "meshforge_tcp_connection_rtt_ms": MetricDefinition(
        name="meshforge_tcp_connection_rtt_ms",
        metric_type=GAUGE,
        help_text="TCP connection round-trip time in milliseconds",
        labels=["remote_addr", "remote_port"],
    ),
    "meshforge_tcp_connections_total": MetricDefinition(
        name="meshforge_tcp_connections_total",
        metric_type=COUNTER,
        help_text="Total TCP connections seen since start",
        labels=[],
    ),
    "meshforge_network_devices_discovered": MetricDefinition(
        name="meshforge_network_devices_discovered",
        metric_type=GAUGE,
        help_text="Number of network devices discovered",
        labels=["type"],  # meshtasticd, web, other
    ),

    # RNS Sniffer metrics
    "meshforge_rns_packets_captured": MetricDefinition(
        name="meshforge_rns_packets_captured",
        metric_type=COUNTER,
        help_text="Total RNS packets captured by sniffer",
        labels=["packet_type"],
    ),
    "meshforge_rns_announces_seen": MetricDefinition(
        name="meshforge_rns_announces_seen",
        metric_type=COUNTER,
        help_text="Total RNS announces observed",
        labels=[],
    ),
    "meshforge_rns_paths_discovered": MetricDefinition(
        name="meshforge_rns_paths_discovered",
        metric_type=GAUGE,
        help_text="Number of RNS paths in path table",
        labels=[],
    ),
    "meshforge_rns_links_active": MetricDefinition(
        name="meshforge_rns_links_active",
        metric_type=GAUGE,
        help_text="Number of active RNS links",
        labels=[],
    ),
    "meshforge_rns_links_total": MetricDefinition(
        name="meshforge_rns_links_total",
        metric_type=COUNTER,
        help_text="Total RNS links established",
        labels=[],
    ),
    "meshforge_rns_sniffer_running": MetricDefinition(
        name="meshforge_rns_sniffer_running",
        metric_type=GAUGE,
        help_text="Whether RNS sniffer is capturing (1) or not (0)",
        labels=[],
    ),
    "meshforge_rns_bytes_captured": MetricDefinition(
        name="meshforge_rns_bytes_captured",
        metric_type=COUNTER,
        help_text="Total bytes captured by RNS sniffer",
        labels=[],
    ),
    "meshforge_rns_path_hops": MetricDefinition(
        name="meshforge_rns_path_hops",
        metric_type=GAUGE,
        help_text="Hop count for known RNS path",
        labels=["destination"],
    ),
}


def escape_label_value(value: str) -> str:
    """Escape special characters in Prometheus label values.

    Per Prometheus exposition format, label values must escape:
    - Backslash (\\) -> \\\\
    - Double quote (") -> \\"
    - Newline (\\n) -> \\n
    """
    # Order matters: escape backslash first to avoid double-escaping
    value = value.replace('\\', '\\\\')
    value = value.replace('"', '\\"')
    value = value.replace('\n', '\\n')
    return value


def format_labels(labels: Dict[str, str]) -> str:
    """Format labels for Prometheus exposition format."""
    if not labels:
        return ""
    pairs = [f'{k}="{escape_label_value(str(v))}"' for k, v in sorted(labels.items())]
    return "{" + ",".join(pairs) + "}"


def format_metric_line(name: str, value: float, labels: Dict[str, str] = None) -> str:
    """Format a single metric line."""
    label_str = format_labels(labels or {})
    return f"{name}{label_str} {value}"


# Also export with underscore prefix for backward compatibility
_escape_label_value = escape_label_value
_format_labels = format_labels
_format_metric_line = format_metric_line
