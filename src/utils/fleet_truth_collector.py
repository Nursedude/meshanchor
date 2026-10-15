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

from utils.box_truth_center import _ALIAS_RE as _SPOOL_ALIAS_RE  # noqa: E402
from utils.box_truth_center import read_spool  # noqa: E402  (shared spool reader)
from utils.status_blocks import (  # noqa: E402  (shared with MeshForge)
    mini_block_from_payload, radio_connection_from_probe,
    watchdog_block_from_payload)


def _blocks_from_raw(spool: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """``/api/status``-shaped blocks for a map-less box, from the raw state its
    export carried, through the shared transforms. The box's OWN account of a
    section it could not send (section_errors) becomes that block's reason —
    never "absent" for a source that exists. None when there is nothing."""
    errs = ((spool.get("box_truth") or {}).get("section_errors") or {})
    status: Dict[str, Any] = {}
    for raw_key, block_key, fn in (("raw_watchdog", "watchdog", watchdog_block_from_payload),
                                   ("raw_mini", "mini_dudeai", mini_block_from_payload)):
        raw = spool.get(raw_key)
        if isinstance(raw, dict):
            status[block_key] = fn(raw)
        elif isinstance(errs.get(raw_key), str):
            status[block_key] = {"reason": f"read_error: box export — {errs[raw_key]}"}
    if not status:
        return None
    deploy = spool.get("deployment")
    if isinstance(deploy, dict) and isinstance(deploy.get("role"), str):
        bt_app = (spool.get("box_truth") or {}).get("app")
        status["app"] = {"name": bt_app if isinstance(bt_app, str) else "unknown",
                         "role": deploy["role"]}
    return status

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

# MeshAnchor's closed signal enum IS its blackout-kind enum (2026-07-19
# status enrichment): the fleet-watchdog evaluates every kind each cycle and
# /api/status.watchdog now reports per-kind coverage. Import failure → []
# (coverage honestly empty), never a guess.
def _signal_classes() -> List[str]:
    try:
        from monitoring.fleet_watchdog import ALL_KINDS
        return list(ALL_KINDS)
    except Exception as e:
        logger.debug("blackout-kind enum unavailable: %s", e)
        return []


SIGNAL_CLASSES: List[str] = _signal_classes()


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


def _fetch_box(alias: str, base_url: str, method: str,
               spool_alias: Optional[str] = None,
               duplicate: bool = False) -> Dict[str, Any]:
    """Fetch one box's /fleet/slo + /api/status. Returns a snapshot dict
    shaped for ``fleet_truth.build_box_truth``. ``base_url`` is used only to
    build the fetch URLs — never surfaced (it may contain a raw IP).

    When the direct fetch fails, the NOC read-grant spool answers (session 3
    step 3, 2026-10-15): fresh-only (the SHARED reader), so a dead spool cron
    reads dark, never last-known-healthy. An empty-handed spool carries HOW
    the read ended (S1) — a refused grant, a wrong host key and a dead box
    are three different fixes and must not share one "no response". The
    box's own role verdicts ride through as True/False/None, never coerced."""
    slo = _http_get_json(f"{base_url}/fleet/slo", PEER_TIMEOUT_S)
    status = _http_get_json(f"{base_url}/api/status", PEER_TIMEOUT_S)
    error = None
    answered_at = time.time() if (slo or status) else None
    snap: Dict[str, Any] = {}
    if slo is None and status is None:
        error = f"no response from {alias} ({method})"
        sp_alias = spool_alias or alias
        spool = None
        if method == "self":
            pass
        elif duplicate:
            # step-8 pass F1: an ambiguous alias reads DARK, never either box
            error = (f"no response from {alias}; fleet.json names {sp_alias} more "
                     "than once — its spool is not read until each is unique")
        elif not _SPOOL_ALIAS_RE.match(sp_alias):
            error = f"no response from {alias}; {sp_alias!r} is not a valid alias (spool not read)"
        else:
            spool = read_spool(sp_alias)
            if spool is not None and spool.get("alias") != sp_alias:
                error = (f"no response from {alias}; spool file for {sp_alias} "
                         f"names {spool.get('alias')} — not used")
                spool = None
        if spool is not None:
            sp_status = spool.get("status") if isinstance(spool.get("status"), dict) else None
            sp_slo = spool.get("slo") if isinstance(spool.get("slo"), dict) else None
            for k in ("http_surface_expected", "watchdog_expected"):
                if isinstance(spool.get(k), bool):
                    snap[k] = spool[k]
            # A map-less box (no /api/status, no /fleet/slo) is described by
            # the SAME shared transforms its own map would run, from the raw
            # state it exported (the deferred move, 2026-10-15): a stale raw
            # file reads not-ok, never green-with-old-numbers.
            if sp_status is None:
                sp_status = _blocks_from_raw(spool)
            if sp_slo is None and isinstance(spool.get("radio_probe"), dict):
                rp = spool["radio_probe"]
                connected, mode = radio_connection_from_probe(
                    bool(rp.get("tcp_listening")), bool(rp.get("usb_present")))
                sp_slo = {"radio": {"connected": connected, "mode": mode}}
            if sp_status is not None or sp_slo is not None:
                status, slo, error = sp_status, sp_slo, None
                method = "ssh_spool"
                answered_at = float(spool["fetched_at"])
            else:
                t = spool.get("transport")
                reason = (t.get("reason") if isinstance(t, dict)
                          and t.get("outcome") not in (None, "ok") else None)
                error = (str(reason) if reason else
                         f"no response from {alias} ({method}); "
                         "ssh spool fresh but empty-handed")
    return {
        "alias": alias,
        "resolution_method": method,
        "status": status,
        "slo": slo,
        "error": error,
        "answered_at": answered_at,
        **snap,
    }


def collect_snapshots(*, self_port: int) -> "tuple[List[Dict[str, Any]], int]":
    """Fan out to self + every fleet.json peer in parallel. Returns
    ``(snapshots, hosts_declared)``; hosts_declared includes self."""
    from monitoring.fleet_config import load_fleet_config
    self_host = socket.gethostname()
    cfg = load_fleet_config()
    peers = cfg.non_self_peers(hostname=self_host)
    declared = len(peers) + 1  # + self

    jobs = [(self_host, f"http://127.0.0.1:{self_port}", "self", None)] + [
        # MF014/MF015: an IP-shaped peer NAME must not become the served alias.
        (_mask_ip_alias(p.name) if _IPV4_RE.match(p.name) else p.name,
         p.base_url(), "config", getattr(p, "ssh_alias", None) or p.name)
        for p in peers
    ]
    snapshots: List[Dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(jobs))) as ex:
        futs = {ex.submit(_fetch_box, alias, base, method, sp,
                          bool(sp) and sp in getattr(cfg, "duplicate_aliases", ())): alias
                for alias, base, method, sp in jobs}
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
