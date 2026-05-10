"""Fleet aggregator — composite snapshot of NOC health for the fleet monitor.

Session 1 scope (single host):
    `collect_local_snapshot()` returns a `FleetSnapshot` built from the
    long-running map process plus best-effort HTTP fetches against the
    daemon (`127.0.0.1:8081`). Sources that are down degrade gracefully —
    the snapshot always returns; missing sources show up as `errors`
    entries the dashboard can render as warnings, not as raised
    exceptions.

Sources composed:

    services       — `service_check.check_service()` over `KNOWN_SERVICES`.
    boundaries     — `boundary_timing.get_boundary_stats()` from this
                     (map) process. Daemon-side boundaries arrive in
                     Session 2 via a daemon `/boundaries` endpoint.
    daemon_health  — `GET /health` on the daemon (profile-aware).
    radio          — `GET /radio` on the daemon (MeshCore state).
    chat           — `GET /chat/messages` on the daemon (recent ring).
    collector      — In-process `MapDataCollector.get_history_stats()`
                     when a collector is supplied.

Two derived views are also exported so the HTTP layer can serve them
without re-collecting:

    slo_view(snapshot)      — Top-line "is the network healthy" rollup:
                              services up/down/degraded, boundary error
                              rates and p95 across the most-trafficked
                              labels, radio connected, uptime.
    activity_view(snapshot) — Recent chat messages, recent slow
                              boundaries, and last-state hints — the
                              live feed surface.

The shapes are designed for the TUI panel first; the web dashboard
renders the same JSON.
"""
from __future__ import annotations

import json
import logging
import socket
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


_PROCESS_START_TIME = time.time()
"""Monotonic-ish process start; used for `uptime_s` in snapshots."""

DEFAULT_DAEMON_URL = "http://127.0.0.1:8081"
DEFAULT_HTTP_TIMEOUT_S = 2.0
"""Soft timeout per HTTP fetch. Keeps the snapshot under ~6s worst-case
when the daemon is fully wedged (3 fetches × 2s each)."""

CHAT_RECENT_LIMIT = 20
"""How many recent chat entries to keep in the snapshot. The daemon ring
buffer holds more; we trim here so dashboards don't ship huge payloads."""

ACTIVITY_SLOW_BOUNDARY_LIMIT = 10
"""How many slowest-boundary labels to surface in the activity feed."""


# ──────────────────────────────────────────────────────────────────────
# Snapshot dataclass
# ──────────────────────────────────────────────────────────────────────


@dataclass
class FleetSnapshot:
    """Composite NOC snapshot. Always returns; degraded fields are None
    or empty with an `errors` entry pointing at the failed source."""

    generated_at: float
    """Unix timestamp when the snapshot was assembled."""

    host: str
    """`socket.gethostname()` — multi-host rollups (Session 2) key on this."""

    uptime_s: float
    """Process uptime of the aggregator host (typically the map process)."""

    services: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    """Per-service `{name: {state, available, message, port,
    detection_method}}`. Keyed by `KNOWN_SERVICES` name."""

    boundaries: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    """`get_boundary_stats()` output for *this* process. Session 2 will
    add daemon-side boundaries under a separate key."""

    daemon_health: Optional[Dict[str, Any]] = None
    """Body of daemon `GET /health`'s `health` field, or None if the
    fetch failed. `errors` will carry the reason."""

    radio: Optional[Dict[str, Any]] = None
    """Body of daemon `GET /radio`'s `radio` field, or None on failure."""

    chat_recent: List[Dict[str, Any]] = field(default_factory=list)
    """Most recent chat entries (CHAT_RECENT_LIMIT cap), newest last."""

    chat_total: int = 0
    """Total entries the daemon's ring reports — surfaces traffic volume
    even when we only show the tail."""

    collector_stats: Optional[Dict[str, Any]] = None
    """In-process `MapDataCollector` stats when collector was supplied
    (history.get_stats() output). None outside the map process."""

    errors: List[Dict[str, str]] = field(default_factory=list)
    """Soft-fail notes from failed sources. Each entry: `{source, error}`.
    The aggregator never raises — anything broken lands here."""

    def to_dict(self) -> Dict[str, Any]:
        """JSON-safe dict — `dataclasses.asdict` flattens defaults too."""
        return asdict(self)


# ──────────────────────────────────────────────────────────────────────
# Source helpers (each returns (value, error_dict_or_none))
# ──────────────────────────────────────────────────────────────────────


def _collect_services() -> tuple[Dict[str, Dict[str, Any]], Optional[Dict[str, str]]]:
    """Iterate `KNOWN_SERVICES` and call `check_service()` on each.

    Importing inside the function keeps the aggregator usable in unit
    tests that monkeypatch `service_check` without paying for the import
    chain (`utils.boundary_timing` → systemd subprocess at module load).
    """
    try:
        from utils.service_check import check_service, KNOWN_SERVICES
    except Exception as e:
        return {}, {"source": "services", "error": f"import: {e}"}

    out: Dict[str, Dict[str, Any]] = {}
    for name, cfg in KNOWN_SERVICES.items():
        # Carry the `optional` flag through to the snapshot so downstream
        # rollups can split required vs optional without re-importing
        # `KNOWN_SERVICES` (and so peer snapshots fetched over HTTP from
        # other hosts stay self-describing).
        optional = bool(cfg.get("optional", False))
        try:
            status = check_service(name)
        except Exception as e:
            out[name] = {
                "state": "unknown",
                "available": False,
                "message": f"check_service raised: {e}",
                "port": None,
                "detection_method": "exception",
                "optional": optional,
            }
            continue
        out[name] = {
            "state": status.state.value if hasattr(status.state, "value") else str(status.state),
            "available": bool(status.available),
            "message": status.message,
            "port": status.port,
            "detection_method": status.detection_method,
            "optional": optional,
        }
    return out, None


def _collect_boundaries() -> tuple[Dict[str, Dict[str, Any]], Optional[Dict[str, str]]]:
    """Pull boundary stats from this process. Empty dict is fine — means
    no boundary calls have fired yet (fresh start)."""
    try:
        from utils.boundary_timing import get_boundary_stats
        return get_boundary_stats(), None
    except Exception as e:
        return {}, {"source": "boundaries", "error": str(e)}


def _http_get_json(
    url: str,
    timeout: float = DEFAULT_HTTP_TIMEOUT_S,
) -> tuple[Optional[Any], Optional[str]]:
    """GET JSON from `url`. Returns `(parsed, None)` or `(None, error)`.

    Used for the daemon side of the aggregator. Caller owns the URL —
    this helper just wraps timeout + JSON decode + targeted exception
    swallowing so the snapshot never raises.
    """
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
        return json.loads(body.decode("utf-8")), None
    except urllib.error.URLError as e:
        return None, f"url error: {e.reason if hasattr(e, 'reason') else e}"
    except (TimeoutError, socket.timeout) as e:
        return None, f"timeout: {e}"
    except json.JSONDecodeError as e:
        return None, f"bad json: {e}"
    except Exception as e:
        return None, f"unexpected: {e}"


def _collect_daemon_health(daemon_url: str, timeout: float):
    body, err = _http_get_json(f"{daemon_url}/health", timeout=timeout)
    if err is not None:
        return None, {"source": "daemon_health", "error": err}
    if not isinstance(body, dict):
        return None, {"source": "daemon_health", "error": "non-dict body"}
    return body.get("health"), None


def _collect_radio(daemon_url: str, timeout: float):
    body, err = _http_get_json(f"{daemon_url}/radio", timeout=timeout)
    if err is not None:
        return None, {"source": "radio", "error": err}
    if not isinstance(body, dict):
        return None, {"source": "radio", "error": "non-dict body"}
    return body.get("radio"), None


def _collect_chat(daemon_url: str, timeout: float):
    body, err = _http_get_json(
        f"{daemon_url}/chat/messages", timeout=timeout
    )
    if err is not None:
        return [], 0, {"source": "chat", "error": err}
    if not isinstance(body, dict):
        return [], 0, {"source": "chat", "error": "non-dict body"}
    msgs = body.get("messages") or []
    if not isinstance(msgs, list):
        msgs = []
    total = body.get("count")
    if not isinstance(total, int):
        total = len(msgs)
    return msgs[-CHAT_RECENT_LIMIT:], total, None


def _collect_collector_stats(collector: Any) -> tuple[Optional[Dict[str, Any]], Optional[Dict[str, str]]]:
    """In-process collector lookup. The map service can pass its
    `MapDataCollector` directly; from any other process this stays None."""
    if collector is None:
        return None, None
    history = getattr(collector, "_history", None)
    if history is None:
        return None, None
    try:
        stats = history.get_stats()
    except Exception as e:
        return None, {"source": "collector", "error": str(e)}
    if not isinstance(stats, dict):
        return None, None
    return stats, None


# ──────────────────────────────────────────────────────────────────────
# Public entry point
# ──────────────────────────────────────────────────────────────────────


def collect_local_snapshot(
    *,
    daemon_url: str = DEFAULT_DAEMON_URL,
    timeout: float = DEFAULT_HTTP_TIMEOUT_S,
    collector: Any = None,
    include_daemon_health: bool = True,
) -> FleetSnapshot:
    """Assemble a `FleetSnapshot` for this host.

    `collector` is an optional in-process `MapDataCollector`; when the
    aggregator runs inside the map service, pass it in so the snapshot
    can include node history without an extra HTTP hop.

    `include_daemon_health` toggles the daemon `GET /health` fetch.
    The endpoint is currently slow (~2s — serial `systemctl is-active`
    over every known service); the lightweight SLO and activity views
    don't need it (their `overall_status` and service rollups derive
    from the in-process `check_service` loop), so they pass False.
    The full `/fleet/health` endpoint keeps the default True for the
    diagnostic deep dive.

    The fetch order keeps cheap, always-local sources first so the
    snapshot is partially useful even when the daemon is unreachable.
    """
    snap = FleetSnapshot(
        generated_at=time.time(),
        host=socket.gethostname(),
        uptime_s=time.time() - _PROCESS_START_TIME,
    )

    services, err = _collect_services()
    snap.services = services
    if err:
        snap.errors.append(err)

    boundaries, err = _collect_boundaries()
    snap.boundaries = boundaries
    if err:
        snap.errors.append(err)

    if include_daemon_health:
        health, err = _collect_daemon_health(daemon_url, timeout)
        snap.daemon_health = health
        if err:
            snap.errors.append(err)

    radio, err = _collect_radio(daemon_url, timeout)
    snap.radio = radio
    if err:
        snap.errors.append(err)

    chat_recent, chat_total, err = _collect_chat(daemon_url, timeout)
    snap.chat_recent = chat_recent
    snap.chat_total = chat_total
    if err:
        snap.errors.append(err)

    collector_stats, err = _collect_collector_stats(collector)
    snap.collector_stats = collector_stats
    if err:
        snap.errors.append(err)

    return snap


# ──────────────────────────────────────────────────────────────────────
# Derived views for /fleet/slo and /fleet/activity
# ──────────────────────────────────────────────────────────────────────


def _services_rollup(services: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """Top-level counts plus a required/optional split.

    `total` / `available` / `by_state` count *all* services for
    back-compat with the SQLite history schema and any external
    consumer keyed off the legacy fields. The `required` and `optional`
    sub-dicts give the dashboard the SLO denominator it actually wants:
    a MeshCore-primary NOC where Meshtastic-side daemons are intentionally
    down should read 2/2 required-available, not 2/6.
    """
    by_state_all: Dict[str, int] = {}
    by_state_req: Dict[str, int] = {}
    by_state_opt: Dict[str, int] = {}
    avail_all = avail_req = avail_opt = 0
    total_req = total_opt = 0
    for s in services.values():
        st = s.get("state", "unknown")
        is_avail = bool(s.get("available"))
        is_optional = bool(s.get("optional", False))
        by_state_all[st] = by_state_all.get(st, 0) + 1
        if is_avail:
            avail_all += 1
        if is_optional:
            total_opt += 1
            by_state_opt[st] = by_state_opt.get(st, 0) + 1
            if is_avail:
                avail_opt += 1
        else:
            total_req += 1
            by_state_req[st] = by_state_req.get(st, 0) + 1
            if is_avail:
                avail_req += 1
    return {
        "total": len(services),
        "available": avail_all,
        "by_state": by_state_all,
        "required": {
            "total": total_req,
            "available": avail_req,
            "by_state": by_state_req,
        },
        "optional": {
            "total": total_opt,
            "available": avail_opt,
            "by_state": by_state_opt,
        },
    }


def _boundary_top(
    boundaries: Dict[str, Dict[str, Any]],
    *,
    limit: int = 8,
) -> List[Dict[str, Any]]:
    """Top boundary labels by call count — these are where time is
    actually being spent. Includes p95 and error rate so the SLO panel
    has a tight view of what's slow."""
    rows = []
    for label, stats in boundaries.items():
        count = stats.get("count", 0)
        if count == 0:
            continue
        errors = stats.get("error_count", 0)
        rows.append({
            "label": label,
            "count": count,
            "error_rate": errors / count if count else 0.0,
            "slow_rate": stats.get("slow_count", 0) / count if count else 0.0,
            "p50_s": stats.get("p50_s", 0.0),
            "p95_s": stats.get("p95_s", 0.0),
            "p99_s": stats.get("p99_s", 0.0),
        })
    rows.sort(key=lambda r: r["count"], reverse=True)
    return rows[:limit]


def _slowest_boundaries(
    boundaries: Dict[str, Dict[str, Any]],
    *,
    limit: int = ACTIVITY_SLOW_BOUNDARY_LIMIT,
) -> List[Dict[str, Any]]:
    rows = []
    for label, stats in boundaries.items():
        if stats.get("slow_count", 0) == 0 and stats.get("error_count", 0) == 0:
            continue
        rows.append({
            "label": label,
            "count": stats.get("count", 0),
            "slow_count": stats.get("slow_count", 0),
            "error_count": stats.get("error_count", 0),
            "p99_s": stats.get("p99_s", 0.0),
        })
    rows.sort(
        key=lambda r: (r["error_count"], r["slow_count"], r["p99_s"]),
        reverse=True,
    )
    return rows[:limit]


def _derive_overall_status(snapshot: FleetSnapshot) -> str:
    """Local fallback when daemon_health wasn't fetched.

    Mirrors the spirit of `startup_health.run_health_check`: ready when
    every required service is available; degraded when at least one
    required service is down but at least one is still up; error when
    no required services are up. Optional services (meshtasticd*,
    nomadnet, meshcore-radio supervisor — see `KNOWN_SERVICES`) are
    advisory and don't affect the rollup. Returns "unknown" if the
    local services map itself failed to populate.
    """
    if not snapshot.services:
        return "unknown"
    required = [s for s in snapshot.services.values() if not s.get("optional")]
    if not required:
        # Pathological config: every service marked optional. Fall back
        # to the full set so we still produce a meaningful answer.
        required = list(snapshot.services.values())
    available = sum(1 for s in required if s.get("available"))
    total = len(required)
    if available == total:
        return "ready"
    return "degraded" if available > 0 else "error"


def slo_view(snapshot: FleetSnapshot) -> Dict[str, Any]:
    """Top-line health rollup. The web dashboard renders this above the
    fold; the TUI panel uses identical fields."""
    radio = snapshot.radio or {}
    daemon_health = snapshot.daemon_health or {}
    overall = daemon_health.get("overall_status") if daemon_health else None
    if overall is None:
        overall = _derive_overall_status(snapshot)
    return {
        "generated_at": snapshot.generated_at,
        "host": snapshot.host,
        "uptime_s": snapshot.uptime_s,
        "overall_status": overall,
        "services": _services_rollup(snapshot.services),
        "boundaries_top": _boundary_top(snapshot.boundaries),
        "radio": {
            "connected": bool(radio.get("connected")) if radio else False,
            "name": radio.get("name") if radio else None,
            "preset": radio.get("preset") if radio else None,
            "battery_pct": radio.get("battery_pct") if radio else None,
        },
        "errors": snapshot.errors,
    }


def activity_view(snapshot: FleetSnapshot) -> Dict[str, Any]:
    """Live-feed surface — recent chat plus boundary anomalies. The
    dashboard renders this as a scrolling log below the SLO panel."""
    return {
        "generated_at": snapshot.generated_at,
        "host": snapshot.host,
        "chat_total": snapshot.chat_total,
        "chat_recent": snapshot.chat_recent,
        "slow_boundaries": _slowest_boundaries(snapshot.boundaries),
        "errors": snapshot.errors,
    }


__all__ = [
    "FleetSnapshot",
    "collect_local_snapshot",
    "slo_view",
    "activity_view",
    "DEFAULT_DAEMON_URL",
    "DEFAULT_HTTP_TIMEOUT_S",
]
