"""Prometheus metrics for the MeshAnchor map service — HTTP-side surface.

Sister-project port from MeshForge's `map_metrics.py` (Phase D-3). The
`prometheus_exporter` module already exposes node / service / RNS /
MQTT counters via hand-rolled exposition; this module adds the
**HTTP-side metrics** that benefit from `prometheus_client`'s
Histogram support (latency buckets are fiddly to hand-roll correctly).

The two surfaces are concatenated in `_serve_fleet_metrics` so a Prom
scraper sees both with one fetch of `/metrics`.

## Names are the contract

The metric NAMES below are the contract. Renaming breaks every saved
Grafana panel, every alertmanager rule, and every recording rule that
references them. Adding new metrics is fine; renaming or repurposing
existing ones is not. Treat this discipline the same way as a public
API: deprecate-and-remove rather than rename.

This is the discipline MeshForge has codified in its `map_metrics.py`
(Phase D-3) and that MeshAnchor is adopting as part of the
2026-05-09 reverse port.

## Optional dependency

`prometheus_client` is loaded via `safe_import`. When it isn't
installed, every helper here is a no-op and `render()` returns an
empty body — the existing hand-rolled `prometheus_exporter` still
serves at `/metrics`, so observability degrades gracefully rather
than failing 500. To get the HTTP-side metrics:

    pip install -r requirements/monitoring.txt

## Cardinality discipline

Labels are kept small. High-cardinality labels (per-node, per-channel,
per-IP) explode time-series counts and slow scrapes. The labels here
("method", "endpoint", "status_class") top out at a few dozen series
total. Routes with parameters (e.g. `/api/nodes/trajectory/<id>`) are
bucketed via `_endpoint_label` in the handler — never use the raw
path as a label value.
"""

from __future__ import annotations

import logging
from typing import Optional, Tuple

from utils.safe_import import safe_import

_prom, _HAS_PROM = safe_import("prometheus_client")

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Metric definitions — NAMES ARE THE CONTRACT. See module docstring.
# ---------------------------------------------------------------------------

if _HAS_PROM:
    from prometheus_client import (
        Counter,
        Gauge,
        Histogram,
        CollectorRegistry,
        CONTENT_TYPE_LATEST,
        generate_latest,
    )

    # Dedicated registry rather than prometheus_client's default global
    # one — keeps python_* / process_* collectors out of the scrape
    # body unless explicitly added later.
    REGISTRY = CollectorRegistry(auto_describe=True)

    # ----- Service health. NOTE: currently a CONSTANT-1 exporter-liveness marker
    # (equivalent to Prometheus's built-in `up`) — MA has no dynamic readiness/
    # warming signal to flip it to 0, so DO NOT alert on `== 0` (it never fires).
    # `set_service_up()` is the writer for a future readiness signal (matching
    # MeshForge's set_warming). (QA deferred low/perf, 2026-07-06.)
    SERVICE_UP = Gauge(
        "meshanchor_map_service_up",
        "Constant 1 while running (~= Prometheus up).",
        registry=REGISTRY,
    )

    # ----- HTTP-side metrics (drives request-rate / latency dashboards)
    HTTP_REQUESTS = Counter(
        "meshanchor_map_http_requests_total",
        "HTTP requests served by the map handler.",
        ["method", "endpoint", "status_class"],
        registry=REGISTRY,
    )
    HTTP_LATENCY = Histogram(
        "meshanchor_map_http_request_duration_seconds",
        "HTTP request latency (seconds).",
        ["endpoint"],
        # Buckets span very fast (sub-ms cache hits), normal API
        # responses, and slow paths (12.5MB meshcore.dev fetch on
        # cold-cache /metrics scrapes — see PR #112's 90s cache).
        buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5,
                 5.0, 10.0, 30.0),
        registry=REGISTRY,
    )

    # ----- /fleet/* in-flight load shedding (Issue #128)
    # Increments each time the heavy-fleet semaphore is at capacity
    # and a request is rejected with 429. Steady-state value 0 on a
    # healthy fleet; sustained non-zero deltas mean either the
    # dashboard is over-polling or some handler is degrading.
    FLEET_HEAVY_BUSY = Counter(
        "meshanchor_map_fleet_heavy_busy_total",
        "Times the /fleet/* in-flight semaphore was at cap and a "
        "request was 429'd. See utils/_map_fleet.heavy_gate.",
        registry=REGISTRY,
    )

    # Set service up at module import — flipped to 0 only if the
    # handler explicitly de-registers (no current code path; placeholder
    # for a future warming-state pattern matching MeshForge's).
    SERVICE_UP.set(1)

else:
    REGISTRY = None
    CONTENT_TYPE_LATEST = "text/plain; version=0.0.4; charset=utf-8"

    def generate_latest(_registry=None) -> bytes:  # type: ignore[no-redef]
        return b""


# ---------------------------------------------------------------------------
# Helpers — call from handler. All no-op gracefully without dep.
# ---------------------------------------------------------------------------


def is_available() -> bool:
    """True iff ``prometheus_client`` imported successfully."""
    return _HAS_PROM


def render() -> Tuple[bytes, str]:
    """Return ``(body, content_type)`` for the HTTP-side metrics surface.

    Empty body when ``prometheus_client`` isn't installed — the caller
    in ``_serve_fleet_metrics`` concatenates this with the existing
    hand-rolled exposition, so an empty contribution is fine.
    """
    if not _HAS_PROM:
        return b"", CONTENT_TYPE_LATEST
    return generate_latest(REGISTRY), CONTENT_TYPE_LATEST


def record_http(
    method: str, endpoint: str, status_code: int, duration_s: float,
) -> None:
    """Record one HTTP request outcome.

    ``endpoint`` MUST be a route template (``/api/status``,
    ``/api/nodes/geojson``), NOT the raw path with parameters —
    parameter expansion would explode cardinality (one series per
    node id). The handler's ``_endpoint_label`` static method does
    this normalization; callers in other code should follow the same
    pattern.
    """
    if not _HAS_PROM:
        return
    status_class = f"{status_code // 100}xx" if status_code else "0xx"
    HTTP_REQUESTS.labels(
        method=method, endpoint=endpoint, status_class=status_class,
    ).inc()
    HTTP_LATENCY.labels(endpoint=endpoint).observe(duration_s)


def set_service_up(up: bool) -> None:
    """Flip the service_up gauge. Provided for future warming-state
    use; not currently called by any path on MA."""
    if not _HAS_PROM:
        return
    SERVICE_UP.set(1 if up else 0)


def record_fleet_heavy_busy() -> None:
    """Increment the /fleet/* in-flight overflow counter (Issue #128).

    Called by `utils._map_fleet.heavy_gate` when the semaphore is at
    cap and a request is rejected with 429. No-op without
    prometheus_client.
    """
    if not _HAS_PROM:
        return
    FLEET_HEAVY_BUSY.inc()
