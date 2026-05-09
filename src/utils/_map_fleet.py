"""Fleet endpoints for MapRequestHandler.

Serves `/fleet/health`, `/fleet/slo`, `/fleet/activity` — the always-on
web surface for the engineering-grade NOC dashboard. The aggregator
itself lives in `monitoring.fleet_aggregator`; this mixin is just the
HTTP glue that turns a request into a snapshot + JSON response.

Extracted from map_http_handler.py for file size compliance (CLAUDE.md #6).

Expects the following attributes/methods on the host class:
- self.collector              : MapDataCollector instance (or None)
- self.path                   : current request path
- self._serve_json(obj, ...)  : method
- self._send_cors_header()    : method
"""
from __future__ import annotations

import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)


class FleetEndpointsMixin:
    """Adds `/fleet/*` JSON endpoints to MapRequestHandler.

    All three endpoints are LAN-readable like the rest of the map's GET
    surface. The data exposed is observability-grade — service states,
    boundary timings, recent chat (no secrets), and the radio's
    advertised name/preset. No mutating operations live here.
    """

    def _serve_fleet_health(self) -> None:
        """Full snapshot — services + boundaries + daemon health + radio
        + recent chat + collector stats. This is the diagnostic deep
        dive; the daemon `/health` fetch in here is currently a ~2s
        serial-systemctl walk, so the always-on dashboard polls
        `/fleet/slo` and `/fleet/activity` instead."""
        snap = self._collect_fleet_snapshot(include_daemon_health=True)
        self._serve_json(snap.to_dict())

    def _serve_fleet_slo(self) -> None:
        """Top-line SLO rollup for the dashboard's upper panel.

        Skips the slow daemon `/health` fetch — `slo_view` derives
        `overall_status` from the local `check_service` rollup when
        `daemon_health` is absent."""
        from monitoring.fleet_aggregator import slo_view
        snap = self._collect_fleet_snapshot(include_daemon_health=False)
        self._serve_json(slo_view(snap))

    def _serve_fleet_activity(self) -> None:
        """Live-feed surface for the dashboard's lower panel."""
        from monitoring.fleet_aggregator import activity_view
        snap = self._collect_fleet_snapshot(include_daemon_health=False)
        self._serve_json(activity_view(snap))

    def _collect_fleet_snapshot(self, *, include_daemon_health: bool):
        """Single collection point so all three endpoints share the same
        defaults (daemon URL, timeout). Each request collects fresh —
        TTL caching can land in Session 4 alongside retention."""
        from monitoring.fleet_aggregator import collect_local_snapshot
        try:
            return collect_local_snapshot(
                collector=self.collector,
                include_daemon_health=include_daemon_health,
            )
        except Exception as e:
            # The aggregator is supposed to never raise; a hit here means
            # something deeper exploded. Return a minimal-but-valid
            # snapshot so the dashboard never breaks the demo.
            logger.error("fleet snapshot collection failed: %s", e)
            from monitoring.fleet_aggregator import FleetSnapshot
            import socket as _socket
            import time as _time
            snap = FleetSnapshot(
                generated_at=_time.time(),
                host=_socket.gethostname(),
                uptime_s=0.0,
            )
            snap.errors.append({"source": "aggregator", "error": str(e)})
            return snap
