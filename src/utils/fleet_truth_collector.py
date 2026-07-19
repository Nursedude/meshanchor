"""HTTP fan-out + TTL cache that feeds MeshAnchor's honest fleet-truth SSOT.

MeshAnchor's collector shim for the SHARED tri-state builder
(``utils.fleet_truth`` — byte-identical with MeshForge's copy, enforced by the
lead repo's ``parity_check.py``). The builder is parity; THIS module is the
deliberately domain-specific half: MeshAnchor's peers come from ``fleet.json``
(``monitoring.fleet_config``), each with its own ``base_url()``, where
MeshForge's come from ``fleet_hosts`` + ``fleet_naming``.

Honesty properties carried over from the lead implementation:
- a peer that times out (~3s) becomes a DARK box in the schema, never a
  dropped row, and an incomplete fan-out forces the fleet verdict non-green;
- the schema carries the peer's configured display NAME plus a resolution
  label ("config"/"self") — never the host field, which may be a raw LAN IP
  (MF014/MF015);
- MeshAnchor has no closed watchdog signal-class enum yet, so
  ``signal_classes`` is EMPTY: the coverage map is honestly 0/0/0 rather than
  invented. When MA grows a per-class producer, wire its enum here and the
  coverage panel lights up with no builder change.
- results are TTL-cached (~12s) single-flight so dashboard polls and a
  Claude session's orientation coalesce onto one fan-out.
"""
from __future__ import annotations

import hashlib
import http.client
import json
import re
import socket
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional

from utils.logging_config import get_logger

logger = get_logger(__name__)

PEER_TIMEOUT_S = 3.0     # same budget as the MF twin; Pi-class /fleet/slo fits
CACHE_TTL_S = 12.0
MAX_WORKERS = 16
# Bounded read (2026-07-19 adversarial review): a byte-dripping or huge-body
# peer must not hang the fan-out (the lock is held across it) nor buffer
# unbounded bytes. Both caps are far above any legitimate truth-fan-out body.
MAX_BODY_BYTES = 8 * 1024 * 1024
READ_DEADLINE_S = 10.0

_IPV4_RE = re.compile(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$")


def _mask_ip_alias(alias: str) -> str:
    """An IP-shaped fleet.json peer NAME must never surface as the box alias
    (MF014/MF015 — the schema carries names, never addresses). Deterministic
    non-reversible label so the operator can still correlate entries."""
    return "ip-entry-" + hashlib.sha1(alias.encode()).hexdigest()[:6]


def _read_bounded(resp, max_bytes: int, deadline_s: float) -> bytes:
    """Read a response with a total-wall deadline and a size cap. The socket
    timeout bounds each recv; this bounds the WHOLE read (drip-feed peers)."""
    deadline = time.monotonic() + deadline_s
    chunks: List[bytes] = []
    total = 0
    while True:
        if time.monotonic() > deadline:
            raise TimeoutError("bounded read deadline exceeded")
        chunk = resp.read(65536)
        if not chunk:
            return b"".join(chunks)
        total += len(chunk)
        if total > max_bytes:
            raise ValueError("response exceeds size cap")
        chunks.append(chunk)

# MeshAnchor has no SIGNAL_CLASSES-style closed enum (its watchdog speaks
# blackout-rows-by-kind, not per-class signals). Empty = the builder's
# coverage map reports total 0 — honest, never inferred.
SIGNAL_CLASSES: List[str] = []


def _http_get_json(url: str, timeout: float) -> Optional[Dict[str, Any]]:
    """GET a JSON body. Returns the parsed dict, or None on any failure
    (unreachable / timeout / non-JSON) — the caller treats None as DARK."""
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 (LAN)
            body = _read_bounded(r, MAX_BODY_BYTES, READ_DEADLINE_S)
        data = json.loads(body)
        return data if isinstance(data, dict) else None
    except (urllib.error.URLError, http.client.HTTPException, OSError,
            ValueError, TimeoutError) as e:
        logger.debug("fleet_truth fetch failed %s: %s", url, e)
        return None


def _fetch_box(alias: str, base_url: str, method: str) -> Dict[str, Any]:
    """Fetch one box's /fleet/slo + /api/status. Returns a snapshot dict
    shaped for ``fleet_truth.build_box_truth``. ``base_url`` is used only to
    build the fetch URLs — never surfaced (it may contain a raw IP)."""
    slo = _http_get_json(f"{base_url}/fleet/slo", PEER_TIMEOUT_S)
    status = _http_get_json(f"{base_url}/api/status", PEER_TIMEOUT_S)
    error = None
    if slo is None and status is None:
        error = f"no response from {alias} ({method})"
    return {
        "alias": alias,
        "resolution_method": method,
        "status": status,
        "slo": slo,
        "error": error,
        "answered_at": time.time() if (slo or status) else None,
    }


def collect_snapshots(*, self_port: int) -> "tuple[List[Dict[str, Any]], int]":
    """Fan out to self + every fleet.json peer in parallel. Returns
    ``(snapshots, hosts_declared)``; hosts_declared includes self."""
    from monitoring.fleet_config import load_fleet_config
    self_host = socket.gethostname()
    cfg = load_fleet_config()
    peers = cfg.non_self_peers(hostname=self_host)
    declared = len(peers) + 1  # + self

    jobs = [(self_host, f"http://127.0.0.1:{self_port}", "self")] + [
        # MF014/MF015: an IP-shaped peer NAME must not become the served alias.
        (_mask_ip_alias(p.name) if _IPV4_RE.match(p.name) else p.name,
         p.base_url(), "config")
        for p in peers
    ]
    snapshots: List[Dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(jobs))) as ex:
        futs = {ex.submit(_fetch_box, alias, base, method): alias
                for alias, base, method in jobs}
        for fut in as_completed(futs):
            try:
                snapshots.append(fut.result())
            except Exception as e:  # a fan-out worker must never sink the build
                alias = futs[fut]
                logger.warning("fleet_truth fetch worker for %s raised: %s", alias, e)
                snapshots.append({"alias": alias, "resolution_method": "error",
                                  "status": None, "slo": None,
                                  "error": f"fetch worker error: {e}",
                                  "answered_at": None})
    return snapshots, declared


# ── TTL-cached singleton ────────────────────────────────────────────────
_lock = threading.Lock()
_cache: Dict[str, Any] = {"truth": None, "built_at": 0.0}


def get_fleet_truth(*, self_port: int, ttl_s: float = CACHE_TTL_S,
                    force: bool = False) -> Dict[str, Any]:
    """Return the current fleet-truth document, refreshing via fan-out when
    the TTL has expired. Thread-safe single-flight. Never raises — a build
    error yields a self-describing dark document rather than a 500."""
    # TTL runs on the MONOTONIC clock (2026-07-19 review): RTC-less Pis take
    # NTP steps, and a wall-clock TTL would serve a frozen doc as fresh for
    # the whole backstep (honest_failure_modes #6).
    now_mono = time.monotonic()
    with _lock:
        cached = _cache["truth"]
        if not force and cached is not None and (now_mono - _cache["built_at"]) < ttl_s:
            return cached
        try:
            from utils.fleet_truth import build_fleet_truth
            snapshots, declared = collect_snapshots(self_port=self_port)
            truth = build_fleet_truth(
                snapshots, now=time.time(),
                signal_classes=list(SIGNAL_CLASSES),
                noc_host=socket.gethostname(),
                hosts_declared=declared, ttl_s=ttl_s,
            )
            # Membership transparency (2026-07-19 adversarial review): an
            # empty fleet.json peers list means a 1-box view — surface the
            # mode so a healthy fleet-of-one can't masquerade as a watched
            # fleet (a lost/empty config is indistinguishable from a
            # deliberately standalone box; say which world we're in).
            truth["fanout"]["membership"] = "fleet" if declared > 1 else "standalone"
        except Exception as e:
            logger.error("fleet_truth build failed: %s", e)
            truth = {
                "schema": "fleet_truth/v1", "generated_at": time.time(),
                "noc_host": socket.gethostname(),
                "fleet_state": "dark",
                "fanout": {"hosts_declared": None, "hosts_answered": 0,
                           "ttl_s": ttl_s, "stale": True, "error": str(e)},
                "counts": {"healthy": 0, "failed": 0, "dark": 0},
                "boxes": [], "structural_dark": [],
            }
        _cache["truth"] = truth
        _cache["built_at"] = time.monotonic()
        return truth
