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
module. The collector (S5, `meshanchor-fleet-collector.service`) is
the *canonical sole writer*. An earlier S4 bootstrap path in
`_serve_fleet_slo` also wrote opportunistically while the collector
was being built; that path was removed 2026-05-09 once the collector
had soaked cleanly.
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

-- Blackout intervals: start/end timestamps + kind classifier. Active
-- blackouts have ts_ended IS NULL; the watchdog opens a row on
-- detection and closes it (sets ts_ended) on recovery.
--
-- ``kind`` distinguishes flavors of silence:
--   "no_data"     — heartbeat table is empty (collector never ran or
--                   history DB freshly created).
--   "http_dead"   — most recent heartbeat is older than the watchdog's
--                   stale threshold; collector can't reach the map.
--   "frozen"      — heartbeat row landed but uptime_s isn't advancing
--                   across consecutive cycles. Note: uptime_s is the
--                   MAP service's uptime today, so this only catches a
--                   stuck map process — daemon-stuck-behind-healthy-map
--                   is covered by daemon_dead instead.
--   "daemon_dead" — meshanchor-daemon.service is not active for ≥ 2
--                   consecutive watchdog checks. The "healthy front
--                   door over a dead back end" detector (added after
--                   the post-S5b smoke surfaced this gap on 2026-05-09).
CREATE TABLE IF NOT EXISTS blackout_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_started  REAL NOT NULL,
    ts_ended    REAL,
    kind        TEXT NOT NULL,
    reason      TEXT
);

CREATE INDEX IF NOT EXISTS idx_blackout_active
    ON blackout_events(kind, ts_ended);

CREATE INDEX IF NOT EXISTS idx_blackout_started
    ON blackout_events(ts_started);
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

    # Pre-compute heartbeat fields. These MUST NOT raise — the heartbeat is the
    # load-bearing artifact (its absence fires a fleet-wide false http_dead
    # blackout), and `federation` comes from semi-trusted PEER data. A hostile
    # or malformed field (chat_total="x", "peers": 5, last_seen_age_s="soon")
    # used to raise here, BEFORE the write's try/except, losing the heartbeat.
    # Coerce defensively so one bad field degrades that one count, never the
    # write. (QA audit 2026-07-06.)
    def _sint(v, default=0):
        try:
            return int(v)
        except (TypeError, ValueError):
            return default

    services = slo.get("services") or {}
    services_total = _sint(services.get("total"))
    services_available = _sint(services.get("available"))
    overall_status = slo.get("overall_status") or "unknown"
    uptime_s = slo.get("uptime_s")
    if uptime_s is not None:
        try:
            uptime_s = float(uptime_s)
        except (TypeError, ValueError):
            uptime_s = None
    chat_total = _sint(activity.get("chat_total"))

    fed_peers = federation.get("peers")
    fed_peers = fed_peers if isinstance(fed_peers, list) else []
    federation_peer_count = len(fed_peers)
    federation_active_count = 0
    for p in fed_peers:
        if not isinstance(p, dict):
            continue
        age = p.get("last_seen_age_s")
        # 0 <= age <= threshold. A NEGATIVE age (forged future last_seen) is
        # untrustworthy, not "very fresh" — don't count it active (B-F10).
        if (isinstance(age, (int, float)) and not isinstance(age, bool)
                and 0 <= age <= FEDERATION_ACTIVE_AGE_S):
            federation_active_count += 1

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
# Blackouts
# ──────────────────────────────────────────────────────────────────────


def record_blackout_started(
    kind: str,
    *,
    reason: Optional[str] = None,
    ts: Optional[float] = None,
    db_path: Optional[Path] = None,
) -> Optional[int]:
    """Open a blackout interval. Idempotent per ``kind``: if an active
    blackout of the same kind exists, returns its existing id without
    inserting. Returns the row id (existing or new), or ``None`` on
    SQLite failure (caller logs + degrades — never raises)."""
    path = init_db(db_path)
    ts = ts if ts is not None else time.time()
    conn = connect_tuned(path)
    try:
        with conn:
            existing = conn.execute(
                """
                SELECT id FROM blackout_events
                WHERE kind = ? AND ts_ended IS NULL
                ORDER BY ts_started DESC LIMIT 1
                """,
                (kind,),
            ).fetchone()
            if existing is not None:
                return existing[0]
            cur = conn.execute(
                """
                INSERT INTO blackout_events (ts_started, ts_ended, kind, reason)
                VALUES (?, NULL, ?, ?)
                """,
                (ts, kind, reason),
            )
            return cur.lastrowid
    except sqlite3.Error as e:
        logger.error("fleet_history.record_blackout_started: %s", e)
        return None
    finally:
        conn.close()


def record_blackout_ended(
    kind: str,
    *,
    ts: Optional[float] = None,
    db_path: Optional[Path] = None,
) -> int:
    """Close every active blackout matching ``kind``. Returns the
    number of rows updated (0 if no active blackout of that kind).
    Idempotent: calling on an already-closed kind is a no-op."""
    path = init_db(db_path)
    ts = ts if ts is not None else time.time()
    conn = connect_tuned(path)
    try:
        with conn:
            cur = conn.execute(
                """
                UPDATE blackout_events
                SET ts_ended = ?
                WHERE kind = ? AND ts_ended IS NULL
                """,
                (ts, kind),
            )
            return cur.rowcount
    except sqlite3.Error as e:
        logger.error("fleet_history.record_blackout_ended: %s", e)
        return 0
    finally:
        conn.close()


def query_active_blackouts(
    *,
    db_path: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """Currently-open blackouts (ts_ended IS NULL). Used by the
    dashboard banner."""
    path = init_db(db_path)
    conn = connect_tuned(path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT id, ts_started, ts_ended, kind, reason
            FROM blackout_events
            WHERE ts_ended IS NULL
            ORDER BY ts_started ASC
            """
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def query_blackout_history(
    *,
    since: float,
    until: Optional[float] = None,
    include_active: bool = True,
    db_path: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """Past + (optionally) active blackouts in window."""
    path = init_db(db_path)
    until = until if until is not None else time.time()
    if until < since:
        return []
    conn = connect_tuned(path)
    conn.row_factory = sqlite3.Row
    try:
        if include_active:
            rows = conn.execute(
                """
                SELECT id, ts_started, ts_ended, kind, reason
                FROM blackout_events
                WHERE ts_started <= ?
                  AND (ts_ended IS NULL OR ts_ended >= ?)
                ORDER BY ts_started ASC
                """,
                (until, since),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT id, ts_started, ts_ended, kind, reason
                FROM blackout_events
                WHERE ts_started <= ? AND ts_ended >= ?
                ORDER BY ts_started ASC
                """,
                (until, since),
            ).fetchall()
        return [dict(r) for r in rows]
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
    expired.

    Service-state events and ended blackouts are kept on a longer
    horizon (≥ 2× retention or 7d minimum) — both are event logs that
    answer "did X flap last week?" long after raw rows aged out.
    Active blackouts (ts_ended IS NULL) are NEVER pruned — leaving
    them behind would lose the "still in trouble" signal.
    """
    path = init_db(db_path)
    cutoff = time.time() - retention_s
    deleted = {
        "heartbeat": 0,
        "boundary_snapshots": 0,
        "service_state_events": 0,
        "blackout_events": 0,
    }

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
            event_cutoff = time.time() - max(retention_s * 2, 7 * 24 * 3600)
            cur = conn.execute(
                "DELETE FROM service_state_events WHERE ts < ?",
                (event_cutoff,),
            )
            deleted["service_state_events"] = cur.rowcount
            cur = conn.execute(
                """
                DELETE FROM blackout_events
                WHERE ts_ended IS NOT NULL
                  AND ts_ended < ?
                """,
                (event_cutoff,),
            )
            deleted["blackout_events"] = cur.rowcount
    finally:
        conn.close()
    return deleted


# ──────────────────────────────────────────────────────────────────────
# Bootstrap throttle
# ──────────────────────────────────────────────────────────────────────


__all__ = [
    "FEDERATION_ACTIVE_AGE_S",
    "get_history_db_path",
    "init_db",
    "record_snapshot",
    "query_latest_heartbeat",
    "query_heartbeat_history",
    "query_boundary_history",
    "query_service_events",
    "list_boundary_labels",
    "prune_history",
    # Blackout API (S5a)
    "record_blackout_started",
    "record_blackout_ended",
    "query_active_blackouts",
    "query_blackout_history",
]
