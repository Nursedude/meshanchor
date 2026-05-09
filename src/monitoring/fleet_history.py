"""Fleet observability — historical telemetry persistence.

Session 4 of the fleet monitor charter: the data substrate for
multi-day observability. Records heartbeat + boundary snapshots +
service-state transitions to a tuned SQLite DB. Sparklines on the
dashboard read from `query_boundary_history()`; S5's silence
watchdog reads `query_latest_heartbeat()` to detect blackouts.

Schema rationale
================
Three tables, each chosen for a specific access pattern:

1. ``heartbeat`` — one row per collector cycle. The watchdog only
   needs the most recent row, so this is small and write-light.
   Hosting host + uptime + service rollup keeps blackout detection
   self-contained: a stale row OR a non-increasing uptime both
   indicate trouble (one is HTTP-dead, the other is semantically
   frozen).

2. ``boundary_snapshots`` — (ts, label) primary key, indexed
   ``(label, ts)`` for sparkline range queries. ``count`` is the
   cumulative counter from `boundary_timing` (monotonic per process
   lifetime); the dashboard derives rate from successive points.
   ``p50_ms`` / ``p95_ms`` / ``p99_ms`` are stored at native scale —
   millisecond floats — so JSON consumers don't carry seconds.

3. ``service_state_events`` — only written on transitions. Saves
   ~99% of writes vs. a per-cycle table when services are stable
   (which is the common case). Compact log for "when did mosquitto
   flap?" queries.

Retention
=========
Native 60s rows for 7 days (default). `prune_history()` drops anything
older. The collector (S5) will call this opportunistically. For
queries spanning longer windows the API caller passes a
``resolution_s`` larger than 60 and the SQL aggregates with GROUP BY
— the rollup is computed on the fly. A future S4.1 may materialize
hourly rollups if 7d × 60s queries get expensive; for now it's
trivial (~600k rows for a single host across ~10 boundaries).

Coupling
========
The dashboard is a *reader* — `_serve_fleet_history` queries this
module. The collector (S5, separate systemd unit) is the canonical
*writer*. As an S4 v1 bootstrap, `_serve_fleet_slo` calls
`record_snapshot()` as a throttled side effect so sparklines have
data *immediately* without depending on S5 shipping. Marked clearly
for removal once S5 lands.
"""
from __future__ import annotations

import logging
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from utils.db_helpers import connect_tuned

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────
# Path resolution
# ──────────────────────────────────────────────────────────────────────


def get_history_db_path() -> Path:
    """Default DB path. FHS-correct: telemetry is application data,
    not user config, so it lives under ``~/.local/share`` not
    ``~/.config``. Honors `get_real_user_home()` (MF001)."""
    from utils.paths import get_real_user_home
    return get_real_user_home() / ".local" / "share" / "meshanchor" / "fleet_history.db"


# ──────────────────────────────────────────────────────────────────────
# Schema
# ──────────────────────────────────────────────────────────────────────


_SCHEMA = """
CREATE TABLE IF NOT EXISTS heartbeat (
    ts                       REAL PRIMARY KEY,
    host                     TEXT NOT NULL,
    uptime_s                 REAL,
    overall_status           TEXT,
    services_total           INTEGER,
    services_available       INTEGER,
    chat_total               INTEGER,
    federation_peer_count    INTEGER,
    federation_active_count  INTEGER,
    soft_error_count         INTEGER
);

CREATE TABLE IF NOT EXISTS boundary_snapshots (
    ts           REAL NOT NULL,
    label        TEXT NOT NULL,
    count        INTEGER NOT NULL,
    p50_ms       REAL,
    p95_ms       REAL,
    p99_ms       REAL,
    error_count  INTEGER NOT NULL DEFAULT 0,
    slow_count   INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (ts, label)
);

CREATE INDEX IF NOT EXISTS idx_boundary_label_ts
    ON boundary_snapshots(label, ts);

CREATE TABLE IF NOT EXISTS service_state_events (
    ts            REAL NOT NULL,
    service_name  TEXT NOT NULL,
    state         TEXT NOT NULL,
    available     INTEGER NOT NULL,
    PRIMARY KEY (ts, service_name)
);

CREATE INDEX IF NOT EXISTS idx_service_events_ts
    ON service_state_events(ts);
"""


_init_lock = threading.Lock()
_initialized_paths: set = set()


def init_db(db_path: Optional[Path] = None) -> Path:
    """Create the DB + tables if needed. Idempotent. Returns the path."""
    path = Path(db_path) if db_path else get_history_db_path()
    with _init_lock:
        if path in _initialized_paths and path.exists():
            return path
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = connect_tuned(path)
        try:
            conn.executescript(_SCHEMA)
            conn.commit()
        finally:
            conn.close()
        _initialized_paths.add(path)
    return path


# ──────────────────────────────────────────────────────────────────────
# Recording
# ──────────────────────────────────────────────────────────────────────


# Federation freshness threshold — must match the dashboard's
# FED_ACTIVE_AGE_S in fleet.html. Active = re-announced within this
# many seconds; the dashboard styling already classifies on this
# threshold.
FEDERATION_ACTIVE_AGE_S = 1200


def record_snapshot(
    slo: Dict[str, Any],
    activity: Dict[str, Any],
    federation: Dict[str, Any],
    *,
    host: str,
    ts: Optional[float] = None,
    db_path: Optional[Path] = None,
) -> Dict[str, int]:
    """Persist one observation cycle.

    Writes:
    - one ``heartbeat`` row;
    - one ``boundary_snapshots`` row per label in ``slo['boundaries_top']``;
    - ``service_state_events`` rows ONLY for services that changed
      state since the last recorded event (state transitions, not
      every poll — keeps the table small).

    Returns counts: ``{"heartbeat": 1, "boundaries": N, "events": M}``.
    Raises only on programmer error (bad shapes); SQLite errors log
    and degrade — `record_snapshot` should never break the caller.
    """
    path = init_db(db_path)
    ts = ts if ts is not None else time.time()

    # Pre-compute heartbeat fields from the slo + activity views.
    services = slo.get("services") or {}
    services_total = int(services.get("total") or 0)
    services_available = int(services.get("available") or 0)
    overall_status = slo.get("overall_status") or "unknown"
    uptime_s = slo.get("uptime_s")
    if uptime_s is not None:
        try:
            uptime_s = float(uptime_s)
        except (TypeError, ValueError):
            uptime_s = None
    chat_total = int(activity.get("chat_total") or 0)

    fed_peers = federation.get("peers") or []
    federation_peer_count = len(fed_peers)
    federation_active_count = sum(
        1 for p in fed_peers
        if isinstance(p, dict)
        and p.get("last_seen_age_s") is not None
        and p["last_seen_age_s"] <= FEDERATION_ACTIVE_AGE_S
    )

    soft_error_count = (
        len(slo.get("errors") or [])
        + len(activity.get("errors") or [])
        + len(federation.get("errors") or [])
    )

    counts = {"heartbeat": 0, "boundaries": 0, "events": 0}

    try:
        conn = connect_tuned(path)
    except sqlite3.Error as e:
        logger.error("fleet_history: connect_tuned failed: %s", e)
        return counts

    try:
        with conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO heartbeat
                    (ts, host, uptime_s, overall_status,
                     services_total, services_available,
                     chat_total,
                     federation_peer_count, federation_active_count,
                     soft_error_count)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (ts, host, uptime_s, overall_status,
                 services_total, services_available,
                 chat_total,
                 federation_peer_count, federation_active_count,
                 soft_error_count),
            )
            counts["heartbeat"] = 1

            # Boundary rows — only top-by-count are exposed in slo_view,
            # but that's the same set the dashboard renders, so the
            # sparkline fidelity matches what the operator sees.
            boundaries = slo.get("boundaries_top") or []
            for b in boundaries:
                if not isinstance(b, dict):
                    continue
                label = b.get("label")
                if not isinstance(label, str):
                    continue
                try:
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO boundary_snapshots
                            (ts, label, count, p50_ms, p95_ms, p99_ms,
                             error_count, slow_count)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (ts, label,
                         int(b.get("count") or 0),
                         (b.get("p50_s") or 0.0) * 1000.0,
                         (b.get("p95_s") or 0.0) * 1000.0,
                         (b.get("p99_s") or 0.0) * 1000.0,
                         int(b.get("error_count")
                             or round(float(b.get("error_rate") or 0)
                                      * float(b.get("count") or 0))),
                         int(b.get("slow_count")
                             or round(float(b.get("slow_rate") or 0)
                                      * float(b.get("count") or 0)))),
                    )
                    counts["boundaries"] += 1
                except (sqlite3.Error, TypeError, ValueError) as e:
                    logger.debug(
                        "fleet_history: skipping boundary %r: %s", label, e
                    )

            # Service-state events: write only on transition. The
            # services dict comes from `slo_view`'s rollup, which is
            # by-state counts, NOT per-service. We need the original
            # per-service dict from the snapshot. The dashboard's
            # /fleet/health gives that; we accept it via the
            # `services_detail` kwarg-shaped slo entry when present.
            services_detail = slo.get("_services_detail")
            if isinstance(services_detail, dict):
                for name, status in services_detail.items():
                    if not isinstance(status, dict):
                        continue
                    state = str(status.get("state") or "unknown")
                    available = 1 if status.get("available") else 0
                    last = conn.execute(
                        """
                        SELECT state, available
                        FROM service_state_events
                        WHERE service_name = ?
                        ORDER BY ts DESC
                        LIMIT 1
                        """,
                        (name,),
                    ).fetchone()
                    if last is None or last[0] != state or last[1] != available:
                        try:
                            conn.execute(
                                """
                                INSERT OR REPLACE INTO service_state_events
                                    (ts, service_name, state, available)
                                VALUES (?, ?, ?, ?)
                                """,
                                (ts, name, state, available),
                            )
                            counts["events"] += 1
                        except sqlite3.Error as e:
                            logger.debug(
                                "fleet_history: skipping event %r: %s", name, e
                            )
    except sqlite3.Error as e:
        logger.error("fleet_history: write transaction failed: %s", e)
    finally:
        conn.close()

    return counts


# ──────────────────────────────────────────────────────────────────────
# Queries
# ──────────────────────────────────────────────────────────────────────


def query_latest_heartbeat(
    db_path: Optional[Path] = None,
) -> Optional[Dict[str, Any]]:
    """Most recent heartbeat row. Used by S5's silence watchdog."""
    path = init_db(db_path)
    conn = connect_tuned(path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT * FROM heartbeat ORDER BY ts DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def query_heartbeat_history(
    *,
    since: float,
    until: Optional[float] = None,
    resolution_s: int = 60,
    db_path: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """Heartbeat rows in [since, until]. Resolution > native triggers
    GROUP BY bucket aggregation: services counts MAX, soft errors SUM,
    uptime LAST."""
    path = init_db(db_path)
    until = until if until is not None else time.time()
    if until < since:
        return []

    conn = connect_tuned(path)
    conn.row_factory = sqlite3.Row
    try:
        if resolution_s <= 60:
            rows = conn.execute(
                """
                SELECT ts, uptime_s, overall_status,
                       services_total, services_available,
                       chat_total,
                       federation_peer_count, federation_active_count,
                       soft_error_count
                FROM heartbeat
                WHERE ts >= ? AND ts <= ?
                ORDER BY ts ASC
                """,
                (since, until),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT
                    CAST(ts / ? AS INTEGER) * ?      AS ts,
                    MAX(uptime_s)                    AS uptime_s,
                    MAX(overall_status)              AS overall_status,
                    MAX(services_total)              AS services_total,
                    MAX(services_available)          AS services_available,
                    MAX(chat_total)                  AS chat_total,
                    MAX(federation_peer_count)       AS federation_peer_count,
                    MAX(federation_active_count)    AS federation_active_count,
                    SUM(soft_error_count)            AS soft_error_count
                FROM heartbeat
                WHERE ts >= ? AND ts <= ?
                GROUP BY CAST(ts / ? AS INTEGER)
                ORDER BY ts ASC
                """,
                (resolution_s, resolution_s, since, until, resolution_s),
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def query_boundary_history(
    *,
    label: str,
    since: float,
    until: Optional[float] = None,
    resolution_s: int = 60,
    db_path: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """Boundary timeseries for a single label. Sparkline source.

    Aggregation rule when ``resolution_s > 60``:
    - count: MAX in bucket (cumulative counter, last-seen value).
    - p50/p95/p99: AVG in bucket (typical latency over the bucket).
    - error_count, slow_count: MAX (cumulative).
    """
    path = init_db(db_path)
    until = until if until is not None else time.time()
    if until < since:
        return []

    conn = connect_tuned(path)
    conn.row_factory = sqlite3.Row
    try:
        if resolution_s <= 60:
            rows = conn.execute(
                """
                SELECT ts, count, p50_ms, p95_ms, p99_ms,
                       error_count, slow_count
                FROM boundary_snapshots
                WHERE label = ? AND ts >= ? AND ts <= ?
                ORDER BY ts ASC
                """,
                (label, since, until),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT
                    CAST(ts / ? AS INTEGER) * ? AS ts,
                    MAX(count)                  AS count,
                    AVG(p50_ms)                 AS p50_ms,
                    AVG(p95_ms)                 AS p95_ms,
                    AVG(p99_ms)                 AS p99_ms,
                    MAX(error_count)            AS error_count,
                    MAX(slow_count)             AS slow_count
                FROM boundary_snapshots
                WHERE label = ? AND ts >= ? AND ts <= ?
                GROUP BY CAST(ts / ? AS INTEGER)
                ORDER BY ts ASC
                """,
                (resolution_s, resolution_s,
                 label, since, until, resolution_s),
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def query_service_events(
    *,
    since: float,
    until: Optional[float] = None,
    db_path: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """Service state transitions in window. Used by the dashboard's
    service-event timeline."""
    path = init_db(db_path)
    until = until if until is not None else time.time()
    if until < since:
        return []
    conn = connect_tuned(path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT ts, service_name, state, available
            FROM service_state_events
            WHERE ts >= ? AND ts <= ?
            ORDER BY ts ASC
            """,
            (since, until),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def list_boundary_labels(
    *,
    since: Optional[float] = None,
    db_path: Optional[Path] = None,
) -> List[str]:
    """All distinct boundary labels seen in the window. Used by the
    dashboard to enumerate available sparkline rows."""
    path = init_db(db_path)
    conn = connect_tuned(path)
    try:
        if since is None:
            rows = conn.execute(
                "SELECT DISTINCT label FROM boundary_snapshots ORDER BY label"
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT DISTINCT label FROM boundary_snapshots
                WHERE ts >= ?
                ORDER BY label
                """,
                (since,),
            ).fetchall()
        return [r[0] for r in rows]
    finally:
        conn.close()


# ──────────────────────────────────────────────────────────────────────
# Retention
# ──────────────────────────────────────────────────────────────────────


def prune_history(
    *,
    retention_s: int = 7 * 24 * 3600,
    db_path: Optional[Path] = None,
) -> Dict[str, int]:
    """Delete rows older than ``retention_s`` seconds. Returns row
    counts removed per table. Idempotent and cheap when nothing's
    expired."""
    path = init_db(db_path)
    cutoff = time.time() - retention_s
    deleted = {"heartbeat": 0, "boundary_snapshots": 0, "service_state_events": 0}

    conn = connect_tuned(path)
    try:
        with conn:
            cur = conn.execute(
                "DELETE FROM heartbeat WHERE ts < ?", (cutoff,)
            )
            deleted["heartbeat"] = cur.rowcount
            cur = conn.execute(
                "DELETE FROM boundary_snapshots WHERE ts < ?", (cutoff,)
            )
            deleted["boundary_snapshots"] = cur.rowcount
            # Service events kept longer (event log, not snapshot stream)
            # — keep at least double the retention so "when did rnsd
            # flap last week?" still answers after the rest pruned.
            event_cutoff = time.time() - max(retention_s * 2, 7 * 24 * 3600)
            cur = conn.execute(
                "DELETE FROM service_state_events WHERE ts < ?",
                (event_cutoff,),
            )
            deleted["service_state_events"] = cur.rowcount
    finally:
        conn.close()
    return deleted


# ──────────────────────────────────────────────────────────────────────
# Bootstrap throttle
# ──────────────────────────────────────────────────────────────────────


# Until S5's collector ships, the dashboard's /fleet/slo endpoint
# records snapshots opportunistically. To avoid hammering SQLite from
# concurrent browser polls we throttle to once per ``MIN_RECORD_INTERVAL_S``.
MIN_RECORD_INTERVAL_S: float = 30.0
_last_record_ts_lock = threading.Lock()
_last_record_ts: float = 0.0


def should_record_now(*, min_interval_s: float = MIN_RECORD_INTERVAL_S) -> bool:
    """True if at least ``min_interval_s`` has elapsed since the last
    successful record. Atomic per process; concurrent callers either
    all see True together (rare) or get throttled. Used by the
    bootstrap path in `_serve_fleet_slo`."""
    global _last_record_ts
    now = time.time()
    with _last_record_ts_lock:
        if now - _last_record_ts < min_interval_s:
            return False
        _last_record_ts = now
        return True


def reset_record_throttle() -> None:
    """Test hook — clear the throttle so unit tests don't have to
    wait MIN_RECORD_INTERVAL_S between calls."""
    global _last_record_ts
    with _last_record_ts_lock:
        _last_record_ts = 0.0


__all__ = [
    "FEDERATION_ACTIVE_AGE_S",
    "MIN_RECORD_INTERVAL_S",
    "get_history_db_path",
    "init_db",
    "record_snapshot",
    "query_latest_heartbeat",
    "query_heartbeat_history",
    "query_boundary_history",
    "query_service_events",
    "list_boundary_labels",
    "prune_history",
    "should_record_now",
    "reset_record_throttle",
]
