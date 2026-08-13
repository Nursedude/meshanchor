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
import math
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


MESHFORGE_INSTALL_PATH = Path("/opt/meshforge")
"""Canonical MeshForge install root. The `_merge_mesh_forge_blocks`
co-install pass-through skips when this is absent — without the check,
MA fetches its own ``localhost:5000/fleet/slo`` thinking it's MF's,
producing a self-recursion storm. Verified 2026-05-20 on
meshanchor-server: 7 req/s sustained + matching ``BrokenPipeError``
rate, every external ``/fleet/slo`` request triggering a recursive
cascade bounded only by the 0.5 s fetch timeout."""


SELF_HTTP_PORT: Optional[int] = None
"""The port THIS process serves on, registered by the map at bind time.

Exists because "is MeshForge co-installed?" turned out not to answer the
question the passthrough actually needs answered, which is "is the thing on
localhost:5000 somebody ELSE?" See ``_merge_mesh_forge_blocks``.
"""


def set_self_http_port(port: Optional[int]) -> None:
    """Record the port this process listens on. Single writer: the map server
    at bind time. Idempotent; ``None`` clears it (tests)."""
    global SELF_HTTP_PORT
    try:
        SELF_HTTP_PORT = int(port) if port is not None else None
    except (TypeError, ValueError):
        SELF_HTTP_PORT = None


@lru_cache(maxsize=1)
def _meshforge_co_installed() -> bool:
    """Cached existence check for the MeshForge install root.

    Module-level lru_cache(1) — the answer is a deployment fact, not
    request state, so caching saves a stat() per /fleet/slo hit on
    every fleet-collector/watchdog cycle. Tests that need to flip the
    value can call ``_meshforge_co_installed.cache_clear()``.
    """
    return MESHFORGE_INSTALL_PATH.exists()


PEER_HTTP_TIMEOUT_S = 3.0
"""Per-peer HTTP fetch timeout. /fleet/health on a healthy peer comes
back in ~140ms, so 3s is plenty of headroom for a slightly congested
LAN; a wedged peer doesn't block the whole rollup for long."""

MAX_PEER_WORKERS = 8
"""Concurrent peer fetches. Bounded rather than unlimited: these run on
Pi-class boxes, and the fleet's own creed is that observability must not
cost more than what it observes. 8 covers the current fleet in one wave."""


def rollup_worst_case_s(peer_count: int) -> float:
    """Worst-case wall time for one `collect_fleet_rollup()`.

    THE SERVER OWNS THIS NUMBER. The TUI client budget derives from it
    rather than hardcoding its own — that exact drift (two consumers of
    one contract, independently hardcoded) is what put the Fleet Monitor
    on the WebSocket port and then gave it a 5s budget against a 24s
    server. Retune the timeouts here and every consumer follows.

    Peers are fetched concurrently in waves of `MAX_PEER_WORKERS`, so the
    peer term is `ceil(peers / workers) x PEER_HTTP_TIMEOUT_S` — not the
    old linear `peers x timeout`.
    """
    from monitoring.fleet_aggregator import DEFAULT_HTTP_TIMEOUT_S

    peers = max(int(peer_count), 0)
    waves = math.ceil(peers / MAX_PEER_WORKERS) if peers else 0
    return (
        3 * DEFAULT_HTTP_TIMEOUT_S           # local snapshot: 3 daemon fetches
        + max(waves, 1) * PEER_HTTP_TIMEOUT_S  # peer fan-out, >=1 wave of slack
        + PEER_HTTP_TIMEOUT_S                # federation view
    )


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


def _collect_daemon_federation_peers(
    *, timeout: float, fresh_window_s: int,
) -> Tuple[List[FederationPeer], Optional[str]]:
    """Fetch RNS announces from the daemon's `/fleet/federation` registry.

    The canonical source — daemon's `rns_services._service_registry`
    holds every LXMF / RNS announce the box has heard. Without this,
    the rollup only sees the directory cache (filtered to `network==rns`),
    which is empty on hosts that don't persist RNS announces into the
    directory.

    Regression observed 2026-05-11 on meshanchor-server:
    `/fleet/federation` directly returned 17 peers while
    `/fleet/rollup.federation_peers` returned 0. This helper closes the
    gap by mirroring the daemon-fetch already in `_serve_fleet_federation`.
    """
    from monitoring.fleet_aggregator import (
        _http_get_json, DEFAULT_DAEMON_URL,
    )

    body, err = _http_get_json(
        f"{DEFAULT_DAEMON_URL}/fleet/federation", timeout=timeout,
    )
    if err is not None or not isinstance(body, dict):
        # Return the failure reason (not just []) so the caller can record it —
        # a silent [] reads identical to "heard 0 announces" (S8 L4).
        return [], f"federation registry unreachable: {err or 'non-dict response'}"

    cutoff = float(fresh_window_s) if fresh_window_s > 0 else None
    out: List[FederationPeer] = []
    for entry in body.get("peers") or []:
        if not isinstance(entry, dict):
            continue
        age = entry.get("last_seen_age_s")
        if cutoff is not None and (age is None or age > cutoff):
            continue
        hash_hex = entry.get("hash_hex", "")
        out.append(FederationPeer(
            node_id=str(hash_hex),
            name=str(entry.get("name") or hash_hex),
            network="rns",
            source_origin="rns_announce",
            last_seen=entry.get("last_seen"),
            last_seen_age_s=age,
        ))
    return out, None


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


MESHFORGE_LOCAL_SLO_URL = "http://localhost:5000/fleet/slo"
"""When MeshAnchor and MeshForge are co-installed on the same box
(typical for the operator's primary NOC), MA's dashboard should
surface MF's observability blocks (`path_table`, `interfaces`,
`cascade`) so the operator gets a single view across both stacks
without having to add MF peers to MA's fleet.json. We fetch MF's
own `/fleet/slo` on a tight timeout — the data is already local;
this is just a cross-stack pass-through.

Defined here rather than in fleet_aggregator so the merge stays
co-located with the rollup that consumes it (Track 2.6 of the
we-have-a-cycle-jolly-wadler stability arc)."""

_MF_PASSTHROUGH_BLOCKS = ("path_table", "interfaces", "cascade")
"""Blocks MA pulls from a co-installed MF's /fleet/slo and merges
into MA's self_snapshot. Additive — never overwrites a key MA
already populated. New blocks land here when shipped by MF."""


_self_passthrough_warned = False
"""One-shot flag for the self-passthrough skip. The condition is a permanent
deployment fact, so it must not log once per /fleet/slo -- that would replace a
traceback storm with a logging storm (honest_failure_modes #9 wants a witness,
not a flood)."""


def _passthrough_port() -> Optional[int]:
    """Port of ``MESHFORGE_LOCAL_SLO_URL``, or None if unparseable."""
    try:
        return urlparse(MESHFORGE_LOCAL_SLO_URL).port
    except (ValueError, TypeError):
        return None


def _merge_mesh_forge_blocks(
    self_snapshot: Dict[str, Any],
    timeout: float = 0.5,
    fetch: Optional[Any] = None,
) -> None:
    """Best-effort merge of MeshForge's /fleet/slo observability blocks
    into MA's self_snapshot.

    Never raises. Silent miss if MF isn't co-installed or its map service
    isn't reachable on localhost:5000 — MA's slo just won't carry the
    blocks for self (peers still do via the normal /fleet/slo poll).

    `fetch` is injectable for tests so we don't have to bind a real port.
    """
    if not isinstance(self_snapshot, dict):
        return
    # Skip if MF isn't installed on this box. Without this guard, MA
    # fetches its own /fleet/slo (port 5000 collision when only MA is
    # installed) producing a recursion storm — every external slo
    # request triggers a recursive fetch bounded only by the 0.5 s
    # timeout. Verified 2026-05-20 on meshanchor-server: ~7 req/s
    # sustained, 1:1 BrokenPipeError rate, 415 CPU min in 6 h 13 m wall.
    if not _meshforge_co_installed():
        return
    # ...and skip if the "MeshForge" map we are about to call is US.
    #
    # 2026-08-13: the co-install check above is a PROXY for "somebody else
    # serves localhost:5000", and on meshanchor-server the proxy went false.
    # /opt/meshforge has been installed there since 2026-07-14 -- it is a
    # MeshForge FLEET MEMBER, so it carries MF's code for the watchdog and
    # fleet_pull -- while `meshforge-map` is inactive and MeshAnchor's own map
    # owns :5000. Existence of a directory never meant a different service was
    # listening. The 2026-05-20 storm this guard was written for came straight
    # back and ran for ~30 days: /fleet/slo -> slo_view -> here -> GET
    # localhost:5000/fleet/slo -> ourselves, recursing until the 0.5s timeout
    # cut each level, one BrokenPipeError per level. Measured before the fix:
    # ~4.5 self-fetches/s, 13,133 handler broken pipes in ten minutes, ~20k
    # journal lines/min -- which rotated the box's whole volatile journal every
    # ~10 minutes and left its user timers unjudgeable.
    #
    # The cure is a POSITIVE identity check, the same standard
    # fleet_config.non_self_peers already holds itself to: do not ask whether
    # MF might be here, ask whether this URL is our own listening port.
    if SELF_HTTP_PORT is not None and _passthrough_port() == SELF_HTTP_PORT:
        global _self_passthrough_warned
        if not _self_passthrough_warned:
            _self_passthrough_warned = True
            logger.info(
                "MeshForge slo passthrough disabled: %s points at our own "
                "listening port %d, so fetching it would be self-recursion. "
                "MeshForge is installed here but is not the service on that "
                "port. Self blocks (path_table/interfaces/cascade) will be "
                "absent; peers are unaffected.",
                MESHFORGE_LOCAL_SLO_URL, SELF_HTTP_PORT,
            )
        return
    try:
        if fetch is None:
            from monitoring.fleet_aggregator import _http_get_json
            fetch = _http_get_json
        body, _err = fetch(MESHFORGE_LOCAL_SLO_URL, timeout=timeout)
    except Exception:
        return
    if not isinstance(body, dict):
        return
    for key in _MF_PASSTHROUGH_BLOCKS:
        if key in self_snapshot:
            # MA already populated this key locally — don't clobber.
            continue
        val = body.get(key)
        if val is not None:
            self_snapshot[key] = val


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
    # Cross-stack pass-through happens inside `slo_view` itself
    # (see fleet_aggregator.slo_view), so this rollup's self_snapshot
    # already has MF's observability blocks merged when slo_view is
    # the source. self_url paths skip the merge — caller is responsible
    # for serving an already-merged slo.

    # Peers are fetched CONCURRENTLY (2026-07-25). Serially, rollup latency
    # was len(peers) x timeout, which blew past every reasonable client
    # budget once the fleet grew — see rollup_worst_case_s(). Rows are
    # written back BY INDEX so config order survives: completion order is
    # nondeterministic, but a grid that reshuffles each poll is unreadable
    # and row identity is positional in the truth schema.
    peer_list = list(config.non_self_peers(hostname=rollup.self_host))
    rows: List[Optional[PeerSnapshot]] = [None] * len(peer_list)

    def _gateway_row(peer) -> PeerSnapshot:
        # Gateway peers don't run a map service on :5000 (e.g. moc3
        # is gateway-only — see persistent_issues.md and the MF
        # project_moc3_hardware_constraint memory). Skip the HTTP
        # fetch entirely: no error, no chip, no wasted round trip.
        # The row still lands in the rollup so the dashboard can
        # show the box's presence; the renderer treats gateway-kind
        # rows as a distinct visual state instead of an error.
        return PeerSnapshot(
            name=peer.name, host=peer.host, port=peer.port, kind=peer.kind,
            snapshot=None, error=None,
            fetched_at=time.time(), fetch_duration_s=0.0,
        )

    def _fetch_row(peer) -> PeerSnapshot:
        """Never raises: a worker exception must degrade into ITS OWN row,
        not abort the whole rollup (the never-a-dropped-row promise)."""
        t0 = time.monotonic()
        try:
            body, err, duration = _fetch_peer_snapshot(peer, timeout=timeout)
        except Exception as exc:                       # noqa: BLE001
            logger.warning("peer %s fetch raised: %s", peer.name, exc)
            body, err, duration = None, f"{type(exc).__name__}: {exc}", \
                time.monotonic() - t0
        return PeerSnapshot(
            name=peer.name, host=peer.host, port=peer.port, kind=peer.kind,
            snapshot=body, error=err,
            fetched_at=time.time(), fetch_duration_s=duration,
        )

    fetchable = [(i, p) for i, p in enumerate(peer_list) if p.kind != "gateway"]
    for i, peer in enumerate(peer_list):
        if peer.kind == "gateway":
            rows[i] = _gateway_row(peer)

    if fetchable:
        workers = min(MAX_PEER_WORKERS, len(fetchable))
        with ThreadPoolExecutor(max_workers=workers,
                                thread_name_prefix="rollup-peer") as pool:
            futures = {pool.submit(_fetch_row, p): i for i, p in fetchable}
            for future in as_completed(futures):
                rows[futures[future]] = future.result()

    rollup.peers.extend(row for row in rows if row is not None)

    if config.federation.scrape_rns_announces:
        # Daemon registry is the canonical source (live RNS announces);
        # directory cache is the fallback for hosts that persist RNS
        # entries via a future collector. Union, daemon-first, dedup by
        # node_id — matches the pattern in `_serve_fleet_federation`.
        daemon_peers, daemon_err = _collect_daemon_federation_peers(
            timeout=timeout,
            fresh_window_s=config.federation.fresh_window_s,
        )
        if daemon_err:
            # Surface the registry-fetch failure instead of letting it collapse
            # to a silent "0 peers" (indistinguishable from "heard nothing") — S8 L4.
            rollup.errors.append({"source": "federation", "error": daemon_err})
        directory_peers = _collect_federation_peers(
            collector,
            fresh_window_s=config.federation.fresh_window_s,
        )
        seen_ids = {p.node_id for p in daemon_peers}
        merged = list(daemon_peers)
        for fp in directory_peers:
            if fp.node_id not in seen_ids:
                merged.append(fp)
                seen_ids.add(fp.node_id)
        # Newest first; absent ages sort to the bottom.
        merged.sort(
            key=lambda p: p.last_seen_age_s if p.last_seen_age_s is not None else 1e18,
        )
        rollup.federation_peers = merged[:50]

    return rollup


__all__ = [
    "FleetRollup",
    "PeerSnapshot",
    "FederationPeer",
    "collect_fleet_rollup",
    "PEER_HTTP_TIMEOUT_S",
]
