"""Daemon-side `/fleet/federation` endpoint.

Issue #102: the map process's directory cache only persists meshcore /
meshtastic node observations; RNS announces stay in the daemon's
`gateway.rns_services._service_registry`. The fleet monitor's
federation panel was therefore always empty in production. This
module is the daemon-side fix — exposes the registry over HTTP so the
map service can fetch and render it.

Same pattern as `utils.health_api`: the `ConfigAPIHandler` method
delegates here so `config_api.py` stays under the 1500-line cap.

Route:

    GET /fleet/federation  → {
        "peers": [
            {
                "hash_hex": "...",                # 16-byte hash hex
                "name": "moc",                    # display name
                "service_type": "LXMF_DELIVERY",  # RNSServiceType.name
                "aspect": "lxmf.delivery",
                "first_seen": 1788123456.0,       # unix
                "last_seen": 1788127200.0,        # unix
                "first_seen_age_s": 3744.0,
                "last_seen_age_s": 0.5,
            },
            ...
        ],
        "stats": {"LXMF_DELIVERY": 12, ...},
        "generated_at": 1788127200.5,
    }

Returns 503 when the gateway/RNS bridge isn't loaded — the map mixin
treats that as the cue to fall back to the directory-filter view.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


def _datetime_to_unix(value: Any) -> float:
    """Robust conversion — `parse_announce` stores `datetime.now()`
    objects, but a future rev that hands us unix floats should still
    work."""
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    # datetime / datetime-like
    try:
        return value.timestamp()
    except AttributeError:
        return 0.0


def _build_payload(now_unix: float) -> Dict[str, Any]:
    """Pull the registry snapshot and shape it for the wire."""
    from gateway.rns_services import get_service_registry

    registry = get_service_registry()
    snapshot = registry.get_all_with_timing()

    peers: List[Dict[str, Any]] = []
    for hash_hex, entry in snapshot.items():
        info = entry.get("info")
        first_unix = _datetime_to_unix(entry.get("first"))
        last_unix = _datetime_to_unix(entry.get("last"))
        # `info` carries display name + service_type + aspect.
        service_type = "UNKNOWN"
        aspect = ""
        name = ""
        latitude = longitude = battery = None
        if info is not None:
            try:
                service_type = info.service_type.name
            except AttributeError:
                service_type = str(getattr(info, "service_type", "UNKNOWN"))
            aspect = getattr(info, "aspect", "") or ""
            name = getattr(info, "display_name", "") or ""
            latitude = getattr(info, "latitude", None)
            longitude = getattr(info, "longitude", None)
            battery = getattr(info, "battery", None)
        peers.append({
            "hash_hex": hash_hex,
            "name": name,
            "service_type": service_type,
            "aspect": aspect,
            "first_seen": first_unix,
            "last_seen": last_unix,
            "first_seen_age_s": max(0.0, now_unix - first_unix) if first_unix else None,
            "last_seen_age_s": max(0.0, now_unix - last_unix) if last_unix else None,
            "latitude": latitude,
            "longitude": longitude,
            "battery": battery,
        })

    # Newest last_seen first — matches the directory-side sort so the
    # map mixin can merge results from both sources without re-sorting.
    peers.sort(
        key=lambda p: p["last_seen_age_s"] if p["last_seen_age_s"] is not None else 1e18
    )

    return {
        "peers": peers,
        "stats": registry.get_stats(),
        "generated_at": now_unix,
    }


def handle_get(handler: Any) -> None:
    """Serve `GET /fleet/federation`.

    Returns 503 when the gateway/RNS bridge module isn't importable —
    the map fallback path treats that as "no daemon-side data, use
    directory filter." 500 only on registry-internal exceptions; those
    are observability-relevant and shouldn't be masked.
    """
    try:
        # Tightly coupled import — surfacing the import failure as a
        # clean 503 lets the map service decide to fall back rather
        # than hide the problem.
        _build_payload  # noqa: F841 — referenced below
    except Exception:  # pragma: no cover — defensive; module always loads
        handler._send_error_json(503, "Fleet API not loaded")
        return

    try:
        from gateway.rns_services import get_service_registry  # noqa: F401
    except ImportError:
        handler._send_error_json(503, "RNS services module not loaded")
        return

    try:
        body = _build_payload(now_unix=time.time())
    except Exception as e:
        logger.error("fleet federation snapshot failed: %s", e)
        handler._send_error_json(500, f"Federation snapshot failed: {e}")
        return

    handler._send_json(body)
