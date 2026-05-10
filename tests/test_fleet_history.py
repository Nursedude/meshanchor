"""Tests for monitoring.fleet_history.

S4 data foundation. Covers schema migration, record/query round-trip,
service-state event de-duplication (only writes on transition),
resolution-aggregation correctness, and retention pruning. The
canonical writer is `monitoring.fleet_collector` (S5).
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from monitoring import fleet_history as fh


# ──────────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────────


@pytest.fixture
def db(tmp_path):
    """Per-test isolated SQLite — fresh DB per test, no shared state."""
    yield tmp_path / "fleet_history.db"


def _slo(boundaries=None, services_detail=None, **overrides):
    base = {
        "overall_status": "ready",
        "uptime_s": 100.0,
        "services": {"total": 6, "available": 4, "by_state": {"available": 4, "not_running": 2}},
        "boundaries_top": boundaries or [],
        "errors": [],
    }
    if services_detail is not None:
        base["_services_detail"] = services_detail
    base.update(overrides)
    return base


def _activity(**overrides):
    base = {"chat_total": 0, "errors": []}
    base.update(overrides)
    return base


def _fed(peers=None, **overrides):
    base = {"peers": peers or [], "errors": []}
    base.update(overrides)
    return base


# ──────────────────────────────────────────────────────────────────────
# Schema + init
# ──────────────────────────────────────────────────────────────────────


def test_init_creates_tables(db):
    fh.init_db(db)
    import sqlite3
    conn = sqlite3.connect(str(db))
    try:
        names = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
    finally:
        conn.close()
    assert {"heartbeat", "boundary_snapshots", "service_state_events"}.issubset(names)


def test_init_is_idempotent(db):
    fh.init_db(db)
    fh.init_db(db)
    fh.init_db(db)
    assert db.exists()


# ──────────────────────────────────────────────────────────────────────
# record_snapshot
# ──────────────────────────────────────────────────────────────────────


def test_record_writes_heartbeat_and_boundaries(db):
    boundaries = [
        {"label": "systemd.is_active[mosquitto]",
         "count": 100, "p50_s": 0.012, "p95_s": 0.045, "p99_s": 0.080,
         "error_rate": 0.0, "slow_rate": 0.0},
    ]
    fed_peers = [
        {"last_seen_age_s": 30},     # active
        {"last_seen_age_s": 1500},   # stale
    ]
    counts = fh.record_snapshot(
        _slo(boundaries=boundaries),
        _activity(chat_total=7),
        _fed(peers=fed_peers),
        host="testhost",
        ts=1000.0,
        db_path=db,
    )
    assert counts == {"heartbeat": 1, "boundaries": 1, "events": 0}

    hb = fh.query_latest_heartbeat(db_path=db)
    assert hb["host"] == "testhost"
    assert hb["chat_total"] == 7
    assert hb["federation_peer_count"] == 2
    assert hb["federation_active_count"] == 1


def test_boundary_seconds_converted_to_ms(db):
    fh.record_snapshot(
        _slo(boundaries=[{
            "label": "x", "count": 10,
            "p50_s": 0.012, "p95_s": 0.045, "p99_s": 0.080,
            "error_rate": 0, "slow_rate": 0,
        }]),
        _activity(), _fed(), host="h", ts=1000.0, db_path=db,
    )
    rows = fh.query_boundary_history(label="x", since=999, until=1001, db_path=db)
    assert len(rows) == 1
    assert rows[0]["p50_ms"] == pytest.approx(12.0)
    assert rows[0]["p95_ms"] == pytest.approx(45.0)
    assert rows[0]["p99_ms"] == pytest.approx(80.0)


def test_service_state_events_only_on_transition(db):
    detail = {
        "mosquitto": {"state": "available", "available": True},
        "rnsd": {"state": "available", "available": True},
    }
    # First record: 2 services × first time → 2 events.
    c1 = fh.record_snapshot(
        _slo(services_detail=detail), _activity(), _fed(),
        host="h", ts=1000.0, db_path=db,
    )
    assert c1["events"] == 2

    # Same state again: zero new events.
    c2 = fh.record_snapshot(
        _slo(services_detail=detail), _activity(), _fed(),
        host="h", ts=1060.0, db_path=db,
    )
    assert c2["events"] == 0

    # Toggle one service: exactly one new event.
    detail2 = dict(detail)
    detail2["rnsd"] = {"state": "not_running", "available": False}
    c3 = fh.record_snapshot(
        _slo(services_detail=detail2), _activity(), _fed(),
        host="h", ts=1120.0, db_path=db,
    )
    assert c3["events"] == 1

    events = fh.query_service_events(since=999, until=1200, db_path=db)
    assert len(events) == 3
    rnsd_events = [e for e in events if e["service_name"] == "rnsd"]
    assert [e["state"] for e in rnsd_events] == ["available", "not_running"]


def test_record_with_no_services_detail_skips_events(db):
    # When _services_detail is absent (caller didn't pass it), no
    # service-state events are written. The slo_view by-state rollup
    # alone isn't enough to know per-service.
    counts = fh.record_snapshot(
        _slo(boundaries=[{
            "label": "x", "count": 1,
            "p50_s": 0, "p95_s": 0, "p99_s": 0,
            "error_rate": 0, "slow_rate": 0,
        }]),
        _activity(), _fed(),
        host="h", ts=1000.0, db_path=db,
    )
    assert counts["events"] == 0


def test_record_swallows_malformed_boundary_rows(db):
    boundaries = [
        {"label": "ok", "count": 5,
         "p50_s": 0, "p95_s": 0, "p99_s": 0,
         "error_rate": 0, "slow_rate": 0},
        {"label": None, "count": 5},        # bad label → skipped
        {"count": 5},                       # missing label → skipped
        "not even a dict",                  # malformed → skipped
    ]
    counts = fh.record_snapshot(
        _slo(boundaries=boundaries), _activity(), _fed(),
        host="h", ts=1000.0, db_path=db,
    )
    assert counts["boundaries"] == 1


# ──────────────────────────────────────────────────────────────────────
# Resolution / aggregation
# ──────────────────────────────────────────────────────────────────────


def test_native_resolution_returns_raw_rows(db):
    for i, ts in enumerate([1000.0, 1060.0, 1120.0]):
        fh.record_snapshot(
            _slo(boundaries=[{
                "label": "x", "count": 10 * (i + 1),
                "p50_s": 0.01, "p95_s": 0.05 + 0.01 * i, "p99_s": 0.1,
                "error_rate": 0, "slow_rate": 0,
            }]),
            _activity(), _fed(), host="h", ts=ts, db_path=db,
        )
    rows = fh.query_boundary_history(
        label="x", since=999, until=1200, resolution_s=60, db_path=db,
    )
    assert len(rows) == 3
    assert rows[0]["count"] == 10
    assert rows[2]["count"] == 30


def test_coarser_resolution_aggregates(db):
    # Five points 60s apart, all aligned inside a single 300s bucket.
    # Bucketing is `ts // 300` so we need ts values where every entry
    # rounds to the same integer — pick ts 1500..1740 (bucket 5).
    base = 1500.0
    for i in range(5):
        fh.record_snapshot(
            _slo(boundaries=[{
                "label": "x", "count": 10 * (i + 1),
                "p50_s": 0.01,
                "p95_s": 0.05 + 0.001 * i,
                "p99_s": 0.1,
                "error_rate": 0, "slow_rate": 0,
            }]),
            _activity(), _fed(), host="h",
            ts=base + 50 * i,
            db_path=db,
        )
    rows = fh.query_boundary_history(
        label="x", since=base - 1, until=base + 300, resolution_s=300, db_path=db,
    )
    assert len(rows) == 1
    # MAX(count) preserves the latest cumulative value.
    assert rows[0]["count"] == 50
    # AVG(p95) of [50, 51, 52, 53, 54] ms = 52ms.
    assert rows[0]["p95_ms"] == pytest.approx(52.0, abs=0.01)


# ──────────────────────────────────────────────────────────────────────
# Heartbeat queries
# ──────────────────────────────────────────────────────────────────────


def test_heartbeat_history_returns_in_order(db):
    for ts in [1100.0, 1000.0, 1060.0]:  # out-of-order writes
        fh.record_snapshot(
            _slo(uptime_s=ts - 999), _activity(), _fed(),
            host="h", ts=ts, db_path=db,
        )
    rows = fh.query_heartbeat_history(since=999, until=1200, db_path=db)
    assert [r["ts"] for r in rows] == sorted([r["ts"] for r in rows])
    assert len(rows) == 3


def test_query_latest_heartbeat_returns_none_when_empty(db):
    fh.init_db(db)
    assert fh.query_latest_heartbeat(db_path=db) is None


def test_window_with_until_before_since_returns_empty(db):
    fh.record_snapshot(
        _slo(), _activity(), _fed(), host="h", ts=1000.0, db_path=db,
    )
    assert fh.query_heartbeat_history(since=2000, until=1000, db_path=db) == []
    assert fh.query_boundary_history(
        label="x", since=2000, until=1000, db_path=db,
    ) == []


# ──────────────────────────────────────────────────────────────────────
# list_boundary_labels
# ──────────────────────────────────────────────────────────────────────


def test_list_boundary_labels_distinct_sorted(db):
    fh.record_snapshot(
        _slo(boundaries=[
            {"label": "z.b", "count": 1, "p50_s": 0, "p95_s": 0, "p99_s": 0, "error_rate": 0, "slow_rate": 0},
            {"label": "a.x", "count": 1, "p50_s": 0, "p95_s": 0, "p99_s": 0, "error_rate": 0, "slow_rate": 0},
            {"label": "z.b", "count": 2, "p50_s": 0, "p95_s": 0, "p99_s": 0, "error_rate": 0, "slow_rate": 0},
        ]),
        _activity(), _fed(), host="h", ts=1000.0, db_path=db,
    )
    labels = fh.list_boundary_labels(db_path=db)
    assert labels == ["a.x", "z.b"]


# ──────────────────────────────────────────────────────────────────────
# Retention
# ──────────────────────────────────────────────────────────────────────


def test_prune_history_removes_old_rows(db):
    now = time.time()
    fh.record_snapshot(_slo(boundaries=[{"label": "x", "count": 1, "p50_s": 0, "p95_s": 0, "p99_s": 0, "error_rate": 0, "slow_rate": 0}]),
                      _activity(), _fed(), host="h", ts=now - 86400 * 30, db_path=db)
    fh.record_snapshot(_slo(boundaries=[{"label": "x", "count": 2, "p50_s": 0, "p95_s": 0, "p99_s": 0, "error_rate": 0, "slow_rate": 0}]),
                      _activity(), _fed(), host="h", ts=now - 60, db_path=db)
    deleted = fh.prune_history(retention_s=86400 * 7, db_path=db)
    assert deleted["heartbeat"] == 1
    assert deleted["boundary_snapshots"] == 1
    # Recent row survived.
    rows = fh.query_boundary_history(
        label="x", since=now - 3600, until=now, db_path=db,
    )
    assert len(rows) == 1


def test_service_events_kept_double_retention(db):
    now = time.time()
    detail = {"mosquitto": {"state": "available", "available": True}}
    fh.record_snapshot(_slo(services_detail=detail), _activity(), _fed(),
                      host="h", ts=now - 86400 * 8, db_path=db)
    # 7-day retention should NOT touch a service event 8 days old —
    # events keep ≥ 2x retention (or a 7d minimum).
    deleted = fh.prune_history(retention_s=86400 * 7, db_path=db)
    assert deleted["service_state_events"] == 0
    events = fh.query_service_events(since=0, until=now, db_path=db)
    assert len(events) == 1


# ──────────────────────────────────────────────────────────────────────
# Inventory wiring
# ──────────────────────────────────────────────────────────────────────


def test_dbspec_registered():
    """fleet_history must be in INVENTORY so the audit script + the
    test_db_inventory regression catch un-registered DBs."""
    from utils.db_inventory import find_spec
    spec = find_spec("fleet_history")
    assert spec is not None
    assert spec.has_auto_prune is True
    assert spec.retention_days == 7
    assert spec.creator_module == "monitoring.fleet_history"
