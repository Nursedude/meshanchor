"""Multi-host fleet rollup composer.

`collect_fleet_rollup(config)` combines the local snapshot (this host)
with HTTP-fetched snapshots from every peer in `fleet.json`. The
dashboard polls the result via `GET /fleet/rollup` to render the
per-host SLO grid.

Design rules mirror `fleet_aggregator.py`:
- Per-peer fetches degrade soft — a 503 or timeout populates `error`
  on that row, the rollup itself still returns 200.
- Each fetch runs sequentially with a tight timeout so the overall
  rollup latency is bounded by `len(peers) × timeout` worst case.
  Concurrent fetching is a Session 4 perf concern; the current peer
  count is small enough that serial fetches are fine.
- Federation peer view is computed from the local map's directory
  cache (filtered to RNS-network entries with fresh `last_seen_age_s`)
  — no extra cross-process call needed.
"""
from __future__ import annotations

import logging
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


PEER_HTTP_TIMEOUT_S = 3.0
"""Per-peer HTTP fetch timeout. /fleet/health on a healthy peer comes
back in ~140ms, so 3s is plenty of headroom for a slightly congested
LAN; a wedged peer doesn't block the whole rollup for long."""


@dataclass
class PeerSnapshot:
    """One host's row in the rollup. Always populated — `error` is set
    instead of `snapshot` when the fetch failed."""

    name: str
    host: str
    port: int
    kind: str
    snapshot: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    fetched_at: float = 0.0
    fetch_duration_s: float = 0.0


@dataclass
class FederationPeer:
    """One LXMF / RNS peer surfaced in the federation panel."""

    node_id: str
    name: str
    network: str
    source_origin: str
    last_seen: Optional[float]
    last_seen_age_s: Optional[float]


@dataclass
class FleetRollup:
    """Top-level rollup payload."""

    generated_at: float
    config_source: Optional[str]
    self_host: str
    self_snapshot: Dict[str, Any]
    peers: List[PeerSnapshot] = field(default_factory=list)
    federation_peers: List[FederationPeer] = field(default_factory=list)
    errors: List[Dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ──────────────────────────────────────────────────────────────────────
# Peer fetching
# ──────────────────────────────────────────────────────────────────────


PEER_SNAPSHOT_PATH = "/fleet/slo"
"""Each peer-row in the rollup is the peer's SLO view, not the full
diagnostic snapshot. SLO is the dashboard-facing surface (~140ms vs.
~2s for /fleet/health), so the rollup stays poll-ready even with a
handful of peers. The dashboard can click into a host's full
/fleet/health on demand."""


def _fetch_peer_snapshot(peer, timeout: float):
    """Pull `/fleet/slo` from a single peer. Returns
    `(snapshot_dict_or_None, error_str_or_None, duration_s)`."""
    from monitoring.fleet_aggregator import _http_get_json

    url = f"{peer.base_url()}{PEER_SNAPSHOT_PATH}"
    t0 = time.monotonic()
    body, err = _http_get_json(url, timeout=timeout)
    duration = time.monotonic() - t0
    if err is not None:
        return None, err, duration
    if not isinstance(body, dict):
        return None, "non-dict body", duration
    return body, None, duration


# ──────────────────────────────────────────────────────────────────────
# Federation peer view
# ──────────────────────────────────────────────────────────────────────


def _collect_federation_peers(collector, fresh_window_s: int) -> List[FederationPeer]:
    """Filter the directory snapshot to RNS-network nodes seen within
    the freshness window. Returns at most ~50 entries — enough for a
    HAM-friendly federation panel without overwhelming the dashboard."""
    if collector is None:
        return []
    history = getattr(collector, "_history", None)
    if history is None:
        return []
    try:
        features, position_less = history.get_directory_snapshot(
            include_position_less=True
        )
    except Exception as e:
        logger.debug("federation directory fetch failed: %s", e)
        return []

    peers: List[FederationPeer] = []
    cutoff_age = float(fresh_window_s) if fresh_window_s > 0 else None

    def _consume(entry: Dict[str, Any]) -> None:
        network = entry.get("network", "")
        if network != "rns":
            return
        age = entry.get("last_seen_age_s")
        if cutoff_age is not None:
            if age is None or age > cutoff_age:
                return
        peers.append(FederationPeer(
            node_id=str(entry.get("id", "")),
            name=str(entry.get("name", "")),
            network=str(network),
            source_origin=str(entry.get("source_origin", "")),
            last_seen=entry.get("last_seen"),
            last_seen_age_s=age,
        ))

    for feature in features:
        props = feature.get("properties", {}) if isinstance(feature, dict) else {}
        if isinstance(props, dict):
            _consume(props)
    for entry in position_less:
        if isinstance(entry, dict):
            _consume(entry)

    # Newest first so the dashboard's top rows are the most recent.
    peers.sort(key=lambda p: p.last_seen_age_s if p.last_seen_age_s is not None else 1e18)
    return peers[:50]


# ──────────────────────────────────────────────────────────────────────
# Public entry point
# ──────────────────────────────────────────────────────────────────────


def collect_fleet_rollup(
    config,
    *,
    collector: Any = None,
    timeout: float = PEER_HTTP_TIMEOUT_S,
    self_url: Optional[str] = None,
) -> FleetRollup:
    """Compose self + every non-self peer's snapshot.

    `self_url` is an optional override (default: skip the HTTP hop and
    call `collect_local_snapshot()` directly — cheaper). Tests pass it
    when they want to mock the local source the same way as peers.
    """
    from monitoring.fleet_aggregator import (
        collect_local_snapshot, _http_get_json,
    )
    import socket

    rollup = FleetRollup(
        generated_at=time.time(),
        config_source=config.source_path,
        self_host=socket.gethostname(),
        self_snapshot={},
    )

    if config.parse_error:
        rollup.errors.append({"source": "config", "error": config.parse_error})

    if self_url:
        body, err = _http_get_json(self_url, timeout=timeout)
        rollup.self_snapshot = body if isinstance(body, dict) else {}
        if err:
            rollup.errors.append({"source": "self", "error": err})
    else:
        from monitoring.fleet_aggregator import slo_view
        try:
            # The rollup keeps every host on the same shape — peers are
            # served as `/fleet/slo` views, so self matches. Skip the
            # slow daemon /health fetch (`include_daemon_health=False`)
            # since `slo_view` derives `overall_status` from the local
            # services rollup as fallback.
            snap = collect_local_snapshot(
                collector=collector,
                include_daemon_health=False,
            )
            rollup.self_snapshot = slo_view(snap)
        except Exception as e:
            rollup.errors.append({"source": "self", "error": str(e)})

    for peer in config.non_self_peers(hostname=rollup.self_host):
        body, err, duration = _fetch_peer_snapshot(peer, timeout=timeout)
        rollup.peers.append(PeerSnapshot(
            name=peer.name,
            host=peer.host,
            port=peer.port,
            kind=peer.kind,
            snapshot=body,
            error=err,
            fetched_at=time.time(),
            fetch_duration_s=duration,
        ))

    if config.federation.scrape_rns_announces:
        rollup.federation_peers = _collect_federation_peers(
            collector,
            fresh_window_s=config.federation.fresh_window_s,
        )

    return rollup


__all__ = [
    "FleetRollup",
    "PeerSnapshot",
    "FederationPeer",
    "collect_fleet_rollup",
    "PEER_HTTP_TIMEOUT_S",
]
