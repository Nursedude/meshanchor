"""Prometheus metrics for the LXMF broadcast bridge (S4 of the reliability
charter).

Lives in the daemon process where the bridge runs. Mirrors the
`utils.map_metrics` pattern: dedicated `CollectorRegistry`,
`prometheus_client` via `safe_import` so the module no-ops gracefully
when the dep isn't installed, and `render()` returns `(body, content_type)`
for the HTTP handler to serve.

## Names are the contract

Metric names below are the contract — see `map_metrics.py` for the full
discipline. Adding new metrics is fine; renaming or repurposing existing
ones breaks Grafana panels and alertmanager rules. Deprecate-and-remove
rather than rename.

## Cardinality discipline

Labels are kept small and bounded:
- `tier`     ∈ {local, federation, external}            (3 values)
- `state`    ∈ {healthy, degraded, stale, dead}         (4 values)
- `outcome`  ∈ {success, path_missing, identity_recall_null,
                invalid_hash, outbound_error}           (5 values)
- `from`/`to` ∈ state values                            (4 × 4 = 16)

No subscriber-hash labels — that's per-row cardinality that explodes
time-series count. The `subscribers` gauge uses a custom Collector that
reads bridge status at scrape time, so the gauge values are always
fresh without scattered .set() calls in the fan-out code path.
"""

from __future__ import annotations

import logging
from typing import Callable, Optional, Tuple

from utils.safe_import import safe_import

_prom, _HAS_PROM = safe_import("prometheus_client")

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Metric definitions — NAMES ARE THE CONTRACT.
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
    from prometheus_client.core import GaugeMetricFamily

    REGISTRY = CollectorRegistry(auto_describe=True)

    FANOUTS_TOTAL = Counter(
        "meshanchor_lxmf_broadcast_fanouts_total",
        "LXMF broadcast fan-out attempts, by tier and outcome.",
        ["tier", "outcome"],
        registry=REGISTRY,
    )

    STATE_TRANSITIONS_TOTAL = Counter(
        "meshanchor_lxmf_broadcast_state_transitions_total",
        "Subscriber state transitions, by from/to state.",
        ["from_state", "to_state"],
        registry=REGISTRY,
    )

    FANOUT_DURATION = Histogram(
        "meshanchor_lxmf_broadcast_fanout_duration_seconds",
        "LXMF broadcast fan-out duration (seconds), labeled by tier.",
        ["tier"],
        # Span request_path waits, identity_recall RPCs, and handle_outbound.
        # Bridge currently allows up to 3s of path-resolution wait per
        # subscriber (30 × 0.1s in _send_to_subscriber), so the upper
        # buckets cover the slow path.
        buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5,
                 1.0, 2.5, 5.0, 10.0),
        registry=REGISTRY,
    )

    class _SubscribersCollector:
        """Yields the `subscribers{tier,state}` gauge at scrape time.

        Reading bridge state at scrape time (instead of .set()ing on every
        bridge event) avoids race conditions between subscribe / unsubscribe
        / state transitions and the gauge labels. Trade-off: each /metrics
        scrape opens a SubscriberStore read. At fleet scale (<50 subs) the
        cost is sub-millisecond — well below the histogram's smallest
        bucket.
        """

        def __init__(self, status_provider: Callable[[], Optional[dict]]):
            self._status_provider = status_provider

        def describe(self):
            # Required for CollectorRegistry(auto_describe=True).
            yield GaugeMetricFamily(
                "meshanchor_lxmf_broadcast_subscribers",
                "LXMF broadcast subscribers grouped by tier and state.",
                labels=["tier", "state"],
            )

        def collect(self):
            family = GaugeMetricFamily(
                "meshanchor_lxmf_broadcast_subscribers",
                "LXMF broadcast subscribers grouped by tier and state.",
                labels=["tier", "state"],
            )
            try:
                status = self._status_provider()
            except Exception as e:
                logger.debug("subscribers collector: status read failed: %s", e)
                yield family
                return
            if not status:
                yield family
                return
            counts: dict = {}
            for row in status.get("subscribers", []):
                key = (row.get("tier", "external"), row.get("state", "healthy"))
                counts[key] = counts.get(key, 0) + 1
            for (tier, state), count in counts.items():
                family.add_metric([tier, state], count)
            yield family

    _subscribers_collector: Optional[_SubscribersCollector] = None

else:
    REGISTRY = None
    CONTENT_TYPE_LATEST = "text/plain; version=0.0.4; charset=utf-8"

    def generate_latest(_registry=None) -> bytes:  # type: ignore[no-redef]
        return b""

    _SubscribersCollector = None  # type: ignore[assignment,misc]
    _subscribers_collector = None


# ---------------------------------------------------------------------------
# Helpers — call from bridge / config_api. All no-op gracefully without dep.
# ---------------------------------------------------------------------------


def is_available() -> bool:
    """True iff ``prometheus_client`` imported successfully."""
    return _HAS_PROM


def render() -> Tuple[bytes, str]:
    """Return ``(body, content_type)`` for the daemon's `/metrics` endpoint."""
    if not _HAS_PROM:
        return b"", CONTENT_TYPE_LATEST
    return generate_latest(REGISTRY), CONTENT_TYPE_LATEST


def register_status_provider(status_provider: Callable[[], Optional[dict]]) -> None:
    """Register a callable that returns the bridge's current `get_status()`
    dict. The subscribers gauge calls this on every scrape.

    Called once from `LXMFBroadcastBridge.start()`. Idempotent — re-registering
    swaps the provider rather than stacking collectors. No-op when
    `prometheus_client` isn't installed.
    """
    if not _HAS_PROM:
        return
    global _subscribers_collector
    if _subscribers_collector is not None:
        try:
            REGISTRY.unregister(_subscribers_collector)
        except (KeyError, ValueError):
            pass
    _subscribers_collector = _SubscribersCollector(status_provider)
    REGISTRY.register(_subscribers_collector)


def unregister_status_provider() -> None:
    """Drop the subscribers collector. Called from
    `LXMFBroadcastBridge.stop()`. No-op when prometheus isn't installed
    or no provider was registered."""
    if not _HAS_PROM:
        return
    global _subscribers_collector
    if _subscribers_collector is None:
        return
    try:
        REGISTRY.unregister(_subscribers_collector)
    except (KeyError, ValueError):
        pass
    _subscribers_collector = None


def record_fanout(tier: str, outcome: str, duration_s: Optional[float] = None) -> None:
    """Record one fan-out attempt outcome. `outcome` must be one of:
    success, path_missing, identity_recall_null, invalid_hash, outbound_error.

    `duration_s` is observed in the per-tier histogram when provided. Pass
    `None` for paths that fail before any RPC work (e.g. invalid_hash)
    so the histogram isn't polluted with zero-time observations.
    """
    if not _HAS_PROM:
        return
    FANOUTS_TOTAL.labels(tier=tier, outcome=outcome).inc()
    if duration_s is not None:
        FANOUT_DURATION.labels(tier=tier).observe(duration_s)


def record_state_transition(from_state: str, to_state: str) -> None:
    """Record one subscriber state transition. Called from
    `SubscriberStore.mark_failed` / `mark_fanout_enqueued` when the state
    actually changes (no-op for same-state writes)."""
    if not _HAS_PROM:
        return
    STATE_TRANSITIONS_TOTAL.labels(
        from_state=from_state, to_state=to_state,
    ).inc()
