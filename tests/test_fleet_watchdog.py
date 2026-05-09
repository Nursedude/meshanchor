"""Tests for monitoring.fleet_watchdog.

S5a — silence detector. Two flavors:
  no_data   — heartbeat table empty.
  http_dead — most recent heartbeat older than stale_threshold_s.
  frozen    — uptime_s not strictly increasing across last N heartbeats.

Plus reconcile_blackouts: open-on-detect + close-on-recover, idempotent.
"""
from __future__ import annotations

import time

import pytest

from monitoring import fleet_history as fh
from monitoring import fleet_watchdog as wd


@pytest.fixture
def db(tmp_path):
    return tmp_path / "fleet_watchdog.db"


def _heartbeat(db_path, *, ts, uptime_s):
    """Write a heartbeat row at ``ts`` with the given uptime."""
    fh.record_snapshot(
        {
            "overall_status": "ready",
            "uptime_s": uptime_s,
            "services": {"total": 6, "available": 4, "by_state": {}},
            "boundaries_top": [],
            "errors": [],
        },
        {"chat_total": 0, "errors": []},
        {"peers": [], "errors": []},
        host="test", ts=ts, db_path=db_path,
    )


# ──────────────────────────────────────────────────────────────────────
# detect_silence — no_data
# ──────────────────────────────────────────────────────────────────────


def test_no_data_when_heartbeat_table_empty(db):
    fh.init_db(db)  # create schema, no rows
    out = wd.detect_silence(db_path=db, now=1000.0)
    assert out[wd.KIND_NO_DATA] is not None
    assert out[wd.KIND_HTTP_DEAD] is None
    assert out[wd.KIND_FROZEN] is None


def test_no_data_clears_after_first_heartbeat(db):
    _heartbeat(db, ts=1000.0, uptime_s=10.0)
    out = wd.detect_silence(db_path=db, now=1010.0)
    assert out[wd.KIND_NO_DATA] is None


# ──────────────────────────────────────────────────────────────────────
# detect_silence — http_dead
# ──────────────────────────────────────────────────────────────────────


def test_http_dead_when_heartbeat_older_than_stale_threshold(db):
    _heartbeat(db, ts=1000.0, uptime_s=10.0)
    # 200s after the last heartbeat with a 120s stale threshold ⇒ dead.
    out = wd.detect_silence(db_path=db, now=1200.0, stale_threshold_s=120.0)
    assert out[wd.KIND_HTTP_DEAD] is not None
    assert "200s old" in out[wd.KIND_HTTP_DEAD] or "200" in out[wd.KIND_HTTP_DEAD]


def test_http_dead_clear_when_fresh_heartbeat(db):
    _heartbeat(db, ts=1000.0, uptime_s=10.0)
    out = wd.detect_silence(db_path=db, now=1060.0, stale_threshold_s=120.0)
    assert out[wd.KIND_HTTP_DEAD] is None


# ──────────────────────────────────────────────────────────────────────
# detect_silence — frozen
# ──────────────────────────────────────────────────────────────────────


def test_frozen_when_uptime_not_advancing(db):
    # Three heartbeats, uptime stuck at the same value — frozen.
    _heartbeat(db, ts=1000.0, uptime_s=100.0)
    _heartbeat(db, ts=1060.0, uptime_s=100.0)
    _heartbeat(db, ts=1120.0, uptime_s=100.0)
    out = wd.detect_silence(
        db_path=db, now=1130.0,
        stale_threshold_s=120.0,
        frozen_window_cycles=3,
    )
    assert out[wd.KIND_FROZEN] is not None
    assert out[wd.KIND_HTTP_DEAD] is None


def test_frozen_clear_when_uptime_advances(db):
    _heartbeat(db, ts=1000.0, uptime_s=10.0)
    _heartbeat(db, ts=1060.0, uptime_s=70.0)
    _heartbeat(db, ts=1120.0, uptime_s=130.0)
    out = wd.detect_silence(
        db_path=db, now=1130.0,
        stale_threshold_s=180.0,
        frozen_window_cycles=3,
    )
    assert out[wd.KIND_FROZEN] is None


def test_frozen_clear_after_daemon_restart(db):
    """uptime_s dropping back to ~0 is a daemon restart, NOT frozen.
    The HTTP-dead check covers the actual outage; once the new
    process is alive the watchdog should let the count rebuild
    rather than falsely accusing frozen."""
    _heartbeat(db, ts=1000.0, uptime_s=120.0)   # pre-restart
    _heartbeat(db, ts=1060.0, uptime_s=0.5)     # post-restart, fresh
    _heartbeat(db, ts=1120.0, uptime_s=60.5)    # ticking
    out = wd.detect_silence(
        db_path=db, now=1130.0,
        stale_threshold_s=180.0,
        frozen_window_cycles=3,
    )
    assert out[wd.KIND_FROZEN] is None


def test_frozen_abstains_when_too_few_heartbeats(db):
    """With only 2 heartbeats and frozen_window_cycles=3, can't decide.
    Don't false-flag."""
    _heartbeat(db, ts=1000.0, uptime_s=10.0)
    _heartbeat(db, ts=1060.0, uptime_s=10.0)  # stuck, but only 2 points
    out = wd.detect_silence(
        db_path=db, now=1100.0,
        stale_threshold_s=180.0,
        frozen_window_cycles=3,
    )
    assert out[wd.KIND_FROZEN] is None


# ──────────────────────────────────────────────────────────────────────
# reconcile_blackouts
# ──────────────────────────────────────────────────────────────────────


def test_reconcile_opens_on_detect(db):
    fh.init_db(db)
    decisions = {wd.KIND_NO_DATA: "test reason",
                 wd.KIND_HTTP_DEAD: None,
                 wd.KIND_FROZEN: None}
    summary = wd.reconcile_blackouts(decisions, db_path=db, now=1000.0)
    assert summary[wd.KIND_NO_DATA] == "opened"
    assert summary[wd.KIND_HTTP_DEAD] == "no_change"
    active = fh.query_active_blackouts(db_path=db)
    assert [a["kind"] for a in active] == [wd.KIND_NO_DATA]


def test_reconcile_idempotent_when_kind_already_active(db):
    fh.init_db(db)
    decisions = {wd.KIND_NO_DATA: "first", wd.KIND_HTTP_DEAD: None, wd.KIND_FROZEN: None}
    wd.reconcile_blackouts(decisions, db_path=db, now=1000.0)
    # Same decisions again — no new row.
    summary2 = wd.reconcile_blackouts(decisions, db_path=db, now=1100.0)
    assert summary2[wd.KIND_NO_DATA] == "no_change"
    active = fh.query_active_blackouts(db_path=db)
    assert len(active) == 1


def test_reconcile_closes_on_recover(db):
    fh.init_db(db)
    open_d = {wd.KIND_HTTP_DEAD: "stale", wd.KIND_NO_DATA: None, wd.KIND_FROZEN: None}
    wd.reconcile_blackouts(open_d, db_path=db, now=1000.0)
    close_d = {k: None for k in wd.ALL_KINDS}
    summary = wd.reconcile_blackouts(close_d, db_path=db, now=1200.0)
    assert summary[wd.KIND_HTTP_DEAD] == "closed"
    assert fh.query_active_blackouts(db_path=db) == []


def test_reconcile_close_already_closed_is_noop(db):
    fh.init_db(db)
    decisions = {k: None for k in wd.ALL_KINDS}
    summary = wd.reconcile_blackouts(decisions, db_path=db, now=1000.0)
    assert all(v == "no_change" for v in summary.values())


def test_multiple_kinds_can_coexist(db):
    fh.init_db(db)
    decisions = {
        wd.KIND_HTTP_DEAD: "stale",
        wd.KIND_FROZEN: "stuck",
        wd.KIND_NO_DATA: None,
    }
    wd.reconcile_blackouts(decisions, db_path=db, now=1000.0)
    active = fh.query_active_blackouts(db_path=db)
    kinds = sorted(a["kind"] for a in active)
    assert kinds == sorted([wd.KIND_HTTP_DEAD, wd.KIND_FROZEN])


# ──────────────────────────────────────────────────────────────────────
# run_loop
# ──────────────────────────────────────────────────────────────────────


def test_run_loop_respects_max_cycles(db):
    fh.init_db(db)
    cycles = wd.run_loop(
        interval_s=0.01, db_path=db, max_cycles=4,
    )
    assert cycles == 4


def test_run_loop_opens_blackout_when_no_heartbeat(db):
    fh.init_db(db)
    wd.run_loop(interval_s=0.01, db_path=db, max_cycles=1)
    active = fh.query_active_blackouts(db_path=db)
    assert any(a["kind"] == wd.KIND_NO_DATA for a in active)


def test_run_loop_closes_blackout_after_recovery(db):
    """Open a no_data blackout via empty DB, then write a heartbeat
    and run another cycle — the blackout should close."""
    fh.init_db(db)
    wd.run_loop(interval_s=0.01, db_path=db, max_cycles=1)
    assert fh.query_active_blackouts(db_path=db) != []
    _heartbeat(db, ts=time.time(), uptime_s=10.0)
    wd.run_loop(interval_s=0.01, db_path=db, max_cycles=1,
                stale_threshold_s=600.0)
    assert fh.query_active_blackouts(db_path=db) == []


# ──────────────────────────────────────────────────────────────────────
# fleet_history.blackout API (sanity)
# ──────────────────────────────────────────────────────────────────────


def test_blackout_history_includes_active_by_default(db):
    fh.init_db(db)
    fh.record_blackout_started("http_dead", reason="r", ts=1000.0, db_path=db)
    rows = fh.query_blackout_history(since=999, until=1100, db_path=db)
    assert len(rows) == 1
    assert rows[0]["ts_ended"] is None


def test_blackout_history_can_exclude_active(db):
    fh.init_db(db)
    fh.record_blackout_started("http_dead", reason="r", ts=1000.0, db_path=db)
    rows = fh.query_blackout_history(
        since=999, until=1100, include_active=False, db_path=db,
    )
    assert rows == []
