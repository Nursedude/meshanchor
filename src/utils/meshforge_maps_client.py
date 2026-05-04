"""Read-only discovery client for the meshforge-maps :8808 service.

Phase 6 scaffold: meshforge-maps is a sister project that owns its own HTTP
server on :8808 with `/api/status`, `/api/health`, `/api/config`, and
`/api/sources` endpoints. MeshAnchor's role is to *discover, surface, and
link* — not to own the lifecycle. This client is the discovery layer.

Usage:
    from utils.meshforge_maps_client import MeshforgeMapsClient

    client = MeshforgeMapsClient()
    status = client.probe()
    if status.available:
        print(f"meshforge-maps {status.version} on {client.web_url}")
        print(f"  health: {status.health_score}/100")
        print(f"  sources: {', '.join(status.sources)}")
    else:
        print(f"meshforge-maps not reachable: {status.error}")

The probe is single-shot and bounded by the constructor's `timeout`. It never
raises — connection refused, timeout, 404, malformed JSON all collapse into
`MapsServiceStatus(available=False, error=...)`. Callers can render the error
string as a fix hint without try/except gymnastics.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import List, Optional

logger = logging.getLogger(__name__)


DEFAULT_HOST = "localhost"
DEFAULT_PORT = 8808
DEFAULT_TIMEOUT = 3.0


@dataclass
class MapsServiceStatus:
    """Snapshot of meshforge-maps reachability and capabilities.

    `available=False` means the probe couldn't reach the service or
    couldn't parse its response. `error` carries a short human-readable
    reason in that case (e.g. "connection refused", "timeout", "non-JSON
    response"). When `available=True`, `error` is None and the other
    fields reflect the parsed `/api/status` + `/api/health` payload.
    """

    available: bool
    host: str
    port: int
    version: Optional[str] = None
    health_score: Optional[int] = None
    sources: List[str] = field(default_factory=list)
    uptime_seconds: Optional[float] = None
    error: Optional[str] = None


class MeshforgeMapsClient:
    """Probes meshforge-maps' :8808 service for status + capabilities.

    Stateless: each `probe()` call hits the wire fresh. No caching here —
    callers that want to display a stale-but-quick snapshot should hold
    the previous `MapsServiceStatus` themselves.
    """

    def __init__(
        self,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout

    @property
    def web_url(self) -> str:
        """Base URL — what to open in a browser for the Leaflet UI."""
        return f"http://{self.host}:{self.port}"

    def _api(self, path: str) -> str:
        return f"{self.web_url}{path}"

    def _fetch_json(self, path: str) -> Optional[dict]:
        """GET `path` and parse JSON. Returns None on any failure."""
        url = self._api(path)
        try:
            with urllib.request.urlopen(url, timeout=self.timeout) as resp:
                if resp.status != 200:
                    logger.debug("meshforge-maps %s -> HTTP %s", path, resp.status)
                    return None
                body = resp.read()
        except (urllib.error.URLError, ConnectionError, TimeoutError, OSError) as e:
            logger.debug("meshforge-maps %s unreachable: %s", path, e)
            return None
        try:
            return json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            logger.debug("meshforge-maps %s non-JSON response: %s", path, e)
            return None

    def probe(self) -> MapsServiceStatus:
        """Single-shot reachability + capability probe.

        Hits `/api/status` first. If that fails, the service is treated as
        unavailable and we don't bother with `/api/health`. If it succeeds,
        we layer in `/api/health` and `/api/sources` (best-effort — partial
        responses still produce a usable status).
        """
        status_payload = self._fetch_json("/api/status")
        if status_payload is None:
            return MapsServiceStatus(
                available=False,
                host=self.host,
                port=self.port,
                error=f"unreachable on {self.host}:{self.port} (is meshforge-maps running?)",
            )

        version = _coerce_str(status_payload.get("version"))
        uptime = _coerce_float(status_payload.get("uptime_seconds"))

        health_payload = self._fetch_json("/api/health") or {}
        health_score = _coerce_int(health_payload.get("score"))

        sources_payload = self._fetch_json("/api/sources") or {}
        sources = _extract_source_names(sources_payload)

        return MapsServiceStatus(
            available=True,
            host=self.host,
            port=self.port,
            version=version,
            health_score=health_score,
            sources=sources,
            uptime_seconds=uptime,
            error=None,
        )

    def fetch_nodes(self) -> Optional[dict]:
        """Fetch the aggregated GeoJSON FeatureCollection from `/api/nodes/geojson`.

        Phase 6.1 (bidirectional handshake): meshforge-maps already aggregates
        nodes from its own collectors (Meshtastic / MeshCore / RNS / MQTT /
        AREDN / HamClock); this lets MeshAnchor pull that aggregate into its
        own NOC view as a low-priority *external_maps* source.

        Returns the parsed FeatureCollection on success, or None on any
        failure (unreachable, non-200, non-JSON, malformed shape). Callers
        treat None as "no nodes from meshforge-maps this collect cycle" —
        same shape contract as the rest of the collector pipeline.
        """
        payload = self._fetch_json("/api/nodes/geojson")
        if payload is None:
            return None
        # Validate the minimum shape — type=FeatureCollection + features list.
        # An aggregator that's still warming up returns properties.collecting=True
        # with an empty features list; we accept that as "valid but empty".
        if not isinstance(payload, dict):
            return None
        if payload.get("type") != "FeatureCollection":
            return None
        if not isinstance(payload.get("features"), list):
            return None
        return payload


# ─────────────────────────────────────────────────────────────────────
# Coercion helpers — meshforge-maps' API surface may evolve, so be lenient
# about whether fields arrive as strings, ints, or are missing entirely.
# ─────────────────────────────────────────────────────────────────────


def _coerce_str(value) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, str):
        return value or None
    return str(value)


def _coerce_int(value) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _coerce_float(value) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _extract_source_names(payload: dict) -> List[str]:
    """Pull the list of enabled data sources from /api/sources.

    Tolerant of two payload shapes the meshforge-maps API has used:
        {"sources": ["meshtastic", "reticulum", ...]}
        {"sources": [{"name": "meshtastic", "enabled": true}, ...]}
    """
    raw = payload.get("sources")
    if not isinstance(raw, list):
        return []
    out: List[str] = []
    for item in raw:
        if isinstance(item, str):
            out.append(item)
        elif isinstance(item, dict):
            name = item.get("name")
            if isinstance(name, str) and item.get("enabled", True):
                out.append(name)
    return out
