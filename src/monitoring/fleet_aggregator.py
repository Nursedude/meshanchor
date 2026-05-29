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
import os
import socket
import subprocess
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


MESHTASTICD_API_PORT = 4403
"""meshtasticd's TCP API. A live listener here is sufficient evidence
that the box has a working Meshtastic radio stack — the firmware only
opens this socket after the radio handshake completes."""

MESHCORE_TTY = "/dev/ttyMeshCore"
"""Symlink installed by meshcore_radio.rules when a MeshCore radio is
plugged in. Used as a presence signal when the MA daemon hasn't yet
handshook with the radio (e.g. daemon just started, or the operator
runs MA on a host where the MeshCore radio is owned by another process)."""


def _tcp_listener_alive(host: str, port: int, timeout: float = 0.5) -> bool:
    """Lightweight TCP-connect probe. Returns True if `host:port` accepts
    a connection within `timeout`. Used as the meshtasticd presence
    signal — the firmware opens :4403 only after the radio handshake,
    so a successful connect is reliable evidence of a working stack."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            return sock.connect_ex((host, port)) == 0
    except (OSError, socket.timeout):
        return False


def _collect_radio(daemon_url: str, timeout: float):
    """Collect radio status with multi-stack fallback.

    Order of evidence (first match wins):
    1. MA daemon's `/radio` reports a populated MeshCore body
       (`radio_freq_mhz` set, or explicit `connected=True`).
    2. Local meshtasticd answers on :4403 — MF radio stack is alive on
       this box even though MA's `/radio` is MeshCore-only.
    3. `/dev/ttyMeshCore` symlink exists — a MeshCore radio is plugged
       in but the daemon hasn't handshook with it yet.

    Without (2) and (3), a Meshtastic-only host (e.g. VolcanoAI) was
    falsely reporting `connected=False` because MA's `/radio` endpoint
    only knows MeshCore. The Subsystem Health panel then warn-chipped
    "radio offline" on a box with a healthy meshtasticd stack. Mirrors
    MF's `_probe_radio()` in `meshforge/src/utils/fleet_snapshot.py`.
    """
    body, err = _http_get_json(f"{daemon_url}/radio", timeout=timeout)
    if err is not None:
        meshcore_view = None
        radio_err: Optional[Dict[str, str]] = {"source": "radio", "error": err}
    elif not isinstance(body, dict):
        meshcore_view = None
        radio_err = {"source": "radio", "error": "non-dict body"}
    else:
        raw = body.get("radio")
        meshcore_view = (
            _radio_slo_shape(raw) if isinstance(raw, dict) else None
        )
        radio_err = None

    if meshcore_view is not None and meshcore_view.get("connected"):
        return meshcore_view, None

    if _tcp_listener_alive("127.0.0.1", MESHTASTICD_API_PORT):
        return {
            "connected": True,
            "name": "meshtasticd",
            "preset": None,
            "battery_pct": None,
        }, None

    if os.path.exists(MESHCORE_TTY):
        return {
            "connected": True,
            "name": "meshcore",
            "preset": None,
            "battery_pct": None,
        }, None

    if meshcore_view is not None:
        return meshcore_view, None
    return None, radio_err


def _radio_slo_shape(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Translate the daemon's MeshCore `/radio` body into slo_view's shape.

    The daemon publishes the radio's *config* (`radio_freq_mhz`, `radio_sf`,
    `node_name`, …) — those fields are only populated after a live serial
    handshake with the radio, so a populated `radio_freq_mhz` is the
    canonical "connected" signal. Without this translation, slo_view
    reads non-existent `radio.connected` / `radio.name` keys and renders
    every healthy peer as `connected=False` (regression observed in
    fleet rollup on 2026-05-11 — the meshanchor-server peer reported
    radio-disconnected while the daemon's `/radio` showed full config
    incl. 910.525 MHz / SF7 / node_name=meshanchorRAK1).

    `connected` precedence:
    1. Explicit `connected=True` if the daemon ever starts emitting one.
    2. Populated `radio_freq_mhz` — only set on a live read.
    3. Fall back to False.
    """
    connected = (
        raw.get("connected") is True
        or raw.get("radio_freq_mhz") is not None
    )
    name = raw.get("node_name") or raw.get("name")
    preset = raw.get("preset")
    if preset is None:
        bw = raw.get("radio_bw_khz")
        sf = raw.get("radio_sf")
        if bw is not None and sf is not None:
            try:
                preset = f"BW{int(bw)}/SF{int(sf)}"
            except (TypeError, ValueError):
                preset = None
    return {
        "connected": bool(connected),
        "name": name,
        "preset": preset,
        "battery_pct": raw.get("battery_pct"),
    }


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


# ──────────────────────────────────────────────────────────────────────
# Schedules block — mirrors meshforge/src/utils/fleet_snapshot.py
# (commits a44d2ef + 2dfca78). Surfaces fleet timer health so the
# dashboard's "self" row shows MA's own timer state alongside MF peers.
# ──────────────────────────────────────────────────────────────────────

SCHEDULE_UNIT_PREFIXES = ("meshforge", "meshanchor", "moc-")
SCHEDULE_STALE_MULTIPLIER = 2.0
# A timer with NEXT unset is only "stale" if it ALSO has no recent fire.
# systemd momentarily reports NEXT=0 at the fire instant while it
# recomputes the next elapse (notably monotonic OnUnitActiveSec timers);
# such a timer just ran and must not flicker the Subsystem Health banner.
SCHEDULE_NO_NEXT_GRACE_S = 3600.0


def _list_timers_scope(scope: str) -> List[Dict[str, Any]]:
    """Return systemctl timers in the given scope. Same env-injection
    fix as MF's fleet_snapshot for daemon-context `--user` calls.

    Root-firing-operator case: when this process runs as root (the
    meshanchor-map.service-on-meshanchor-server case) and
    ``scope == "user"``, root's own /run/user/0 has no bus socket.
    Drop privilege to the operator user via ``sudo -n -u <op>``
    so the call lands on the operator's user systemd manager.
    Mirrors the fire_unit pattern from MA c6d7609. Requires a
    sudoers entry on root-daemon hosts.
    """
    cmd: List[str]
    env: Optional[Dict[str, str]] = None

    if scope == "user" and os.geteuid() == 0:
        from utils.fleet_test_runner import _find_operator_user
        op = _find_operator_user()
        if op is None:
            return []
        op_uid, op_name = op
        cmd = [
            "sudo", "-n", "-u", op_name,
            "env", f"XDG_RUNTIME_DIR=/run/user/{op_uid}",
            "systemctl", "--user", "list-timers", "--all", "--output=json",
        ]
    else:
        cmd = ["systemctl"]
        if scope == "user":
            cmd.append("--user")
            if "XDG_RUNTIME_DIR" not in os.environ:
                env = os.environ.copy()
                env["XDG_RUNTIME_DIR"] = f"/run/user/{os.geteuid()}"
        cmd.extend(["list-timers", "--all", "--output=json"])

    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=5, env=env)
        if r.returncode != 0 or not r.stdout.strip():
            return []
        return json.loads(r.stdout)
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError, json.JSONDecodeError):
        return []


def _normalize_timer(raw: Dict[str, Any], scope: str,
                     now_unix: float) -> Optional[Dict[str, Any]]:
    name = raw.get("unit")
    if not name:
        return None

    def _us_to_unix(us: Any) -> Optional[float]:
        try:
            us = int(us)
        except (TypeError, ValueError):
            return None
        return (us / 1_000_000.0) if us > 0 else None

    next_unix = _us_to_unix(raw.get("next"))
    last_unix = _us_to_unix(raw.get("last"))
    age_s = (now_unix - last_unix) if last_unix is not None else None

    # Stale signature 1: NEXT is unset AND there is no recent fire to
    # vouch for the timer. A genuinely wedged timer (cf MF's moc1
    # tracer.timer, NEXT unset ~18h) sits here with a stale `last`. But
    # systemd ALSO briefly reports NEXT=0 at the fire instant while it
    # recomputes the next elapse (monotonic OnUnitActiveSec timers like
    # meshanchor-map-poke.timer) — that timer's `last` is ~now, so it
    # just ran. Gate on a recent fire so the banner doesn't flicker.
    if next_unix is None:
        stale = age_s is None or age_s > SCHEDULE_NO_NEXT_GRACE_S
    elif last_unix is not None:
        # Stale signature 2: last fire older than 2× the nominal interval
        # (interval ≈ next - last). Negative age (clock skew on a
        # just-fired timer) is never stale — the comparison handles it.
        interval = next_unix - last_unix
        stale = bool(interval > 0 and age_s is not None
                     and age_s > SCHEDULE_STALE_MULTIPLIER * interval)
    else:
        # NEXT known, never fired yet (fresh boot): not stale.
        stale = False
    return {
        "name": name, "scope": scope,
        "next_fire_unix": next_unix, "last_fire_unix": last_unix,
        "age_s": round(age_s, 1) if age_s is not None else None,
        "stale": stale,
    }


def _schedules_block() -> Dict[str, Any]:
    now = time.time()
    units: List[Dict[str, Any]] = []
    for scope in ("system", "user"):
        for raw in _list_timers_scope(scope):
            entry = _normalize_timer(raw, scope, now)
            if entry is None:
                continue
            if not any(entry["name"].startswith(p) for p in SCHEDULE_UNIT_PREFIXES):
                continue
            units.append(entry)
    units.sort(key=lambda u: (not u["stale"], u["name"]))
    stale_count = sum(1 for u in units if u["stale"])
    return {
        "healthy": stale_count == 0,
        "stale_count": stale_count,
        "units": units,
    }


# Ecosystem CI status — twice-daily timer writes ``~/.meshforge-ci-status``
# (see ``meshforge/scripts/ecosystem_ci_status.sh``). Only the fleet box
# that enables the timer writes the file; others return
# ``available=False`` and the dashboard picks the freshest peer.
CI_STATUS_STALE_AFTER_S = 14 * 3600


def _operator_home() -> Optional[Any]:
    """Resolve the operator user's home directory.

    Mirror of meshforge fleet_snapshot._operator_home. Non-root daemon:
    ``get_real_user_home()`` resolves the operator. Root daemon (the
    MA-on-root case): walk ``/run/user/<uid>/bus`` via
    ``_find_operator_user``. Returns ``None`` when no operator can be
    resolved.
    """
    from pathlib import Path
    if os.geteuid() != 0:
        from utils.paths import get_real_user_home
        return get_real_user_home()
    try:
        from utils.fleet_test_runner import _find_operator_user
    except ImportError:
        return None
    op = _find_operator_user()
    if op is None:
        return None
    op_uid, _ = op
    try:
        import pwd
        return Path(pwd.getpwuid(op_uid).pw_dir)
    except (KeyError, ImportError, OSError):
        return None


def _ci_overall(repos: List[Dict[str, str]]) -> str:
    """Aggregate per-repo state to a single pill color.

    Precedence: failure > in_progress > anything-not-success > success.
    Empty repo list returns ``"unknown"``.
    """
    if not repos:
        return "unknown"
    states = {r["state"] for r in repos}
    if "failure" in states:
        return "failure"
    if "in_progress" in states:
        return "in_progress"
    if states <= {"success"}:
        return "success"
    return "degraded"


def _parse_ci_status_file(text: str) -> Dict[str, Any]:
    """Parse the plain-text CI status file into a structured block.

    Mirror of meshforge fleet_snapshot._parse_ci_status_file. Format:
    ``# generated <iso>`` header + one ``  <repo>  <state>  <sha7> ...``
    line per repo. Trailing ``# Overdue open PRs`` section is ignored.
    """
    from datetime import datetime
    generated_at: Optional[str] = None
    generated_unix: Optional[float] = None
    repos: List[Dict[str, str]] = []
    in_overdue_section = False

    for line in text.splitlines():
        if not line.strip():
            continue
        if line.startswith("#"):
            if "Overdue" in line:
                in_overdue_section = True
                continue
            if "generated" in line and generated_at is None:
                marker = "generated "
                idx = line.find(marker)
                if idx >= 0:
                    generated_at = line[idx + len(marker):].strip()
                    try:
                        generated_unix = datetime.fromisoformat(generated_at).timestamp()
                    except (ValueError, TypeError):
                        generated_unix = None
            continue
        if in_overdue_section:
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        name, state = parts[0], parts[1]
        if state == "no-runs":
            repos.append({"name": name, "state": state, "sha": ""})
            continue
        if len(parts) < 3:
            continue
        sha = parts[2]
        if len(sha) == 7 and all(c in "0123456789abcdef" for c in sha):
            repos.append({"name": name, "state": state, "sha": sha})

    overall = _ci_overall(repos)
    return {
        "available": True,
        "generated_at": generated_at,
        "generated_unix": generated_unix,
        "overall": overall,
        "red_count": sum(1 for r in repos if r["state"] == "failure"),
        "in_progress_count": sum(1 for r in repos if r["state"] == "in_progress"),
        "repos": repos,
    }


def _ci_status_block() -> Dict[str, Any]:
    """Read ``~/.meshforge-ci-status`` and structure it for the pill.

    Returns ``{"available": False, "reason": ...}`` when the file is
    missing or the operator home can't be resolved. The dashboard JS
    picks the freshest ``available=True`` block across all peers.
    """
    home = _operator_home()
    if home is None:
        return {"available": False, "reason": "no_operator_home"}
    path = home / ".meshforge-ci-status"
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return {"available": False, "reason": "no_file"}
    except (PermissionError, OSError) as e:
        return {"available": False, "reason": f"read_error: {e.__class__.__name__}"}
    block = _parse_ci_status_file(text)
    if block.get("generated_unix") is not None:
        age = time.time() - block["generated_unix"]
        block["age_s"] = round(age, 1)
        block["stale"] = age > CI_STATUS_STALE_AFTER_S
    else:
        block["age_s"] = None
        block["stale"] = False
    return block


def slo_view(snapshot: FleetSnapshot) -> Dict[str, Any]:
    """Top-line health rollup. The web dashboard renders this above the
    fold; the TUI panel uses identical fields."""
    radio = snapshot.radio or {}
    daemon_health = snapshot.daemon_health or {}
    overall = daemon_health.get("overall_status") if daemon_health else None
    if overall is None:
        overall = _derive_overall_status(snapshot)
    out = {
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
        "schedules": _schedules_block(),
        "ci_status": _ci_status_block(),
    }
    # Cross-stack pass-through (Track 2.6): if MeshForge is co-installed
    # on this box, fold its observability blocks into our slo so MA peers
    # polling /fleet/slo see the operator's path-of-record data without
    # configuring MF as a separate peer.
    try:
        from monitoring.fleet_rollup import _merge_mesh_forge_blocks
        _merge_mesh_forge_blocks(out)
    except Exception:
        # Defensive — slo_view must NEVER fail because of a co-install
        # probe. Worst case: the three new blocks don't appear.
        pass
    return out


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
