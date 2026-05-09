"""Tests for monitoring.fleet_collector.

S5a — the always-on writer. Covers single-cycle behavior with mocked
HTTP, partial-failure resilience (one endpoint down still records a
heartbeat), and the run_loop's interval + stop-event mechanics.
"""
from __future__ import annotations

import threading
import time
from unittest.mock import patch

import pytest

from monitoring import fleet_collector as fc


@pytest.fixture
def db(tmp_path):
    """Per-test isolated DB so each cycle's writes don't bleed."""
    return tmp_path / "fleet_collector.db"


def _slo(**overrides):
    base = {
        "host": "test",
        "overall_status": "ready",
        "uptime_s": 100.0,
        "services": {"total": 6, "available": 4, "by_state": {}},
        "boundaries_top": [
            {"label": "x", "count": 5,
             "p50_s": 0.01, "p95_s": 0.02, "p99_s": 0.03,
             "error_rate": 0.0, "slow_rate": 0.0},
        ],
        "errors": [],
    }
    base.update(overrides)
    return base


def _activity():
    return {"chat_total": 0, "chat_recent": [], "slow_boundaries": [], "errors": []}


def _fed():
    return {"enabled": True, "peers": [], "errors": []}


# ──────────────────────────────────────────────────────────────────────
# Single cycle
# ──────────────────────────────────────────────────────────────────────


def test_collect_once_records_heartbeat_and_boundaries(db):
    def fake_fetch(url, *, timeout=10.0):
        if url.endswith("/fleet/slo"):
            return _slo(), None
        if url.endswith("/fleet/activity"):
            return _activity(), None
        if url.endswith("/fleet/federation"):
            return _fed(), None
        return None, "no match"

    with patch.object(fc, "_fetch_json", side_effect=fake_fetch):
        result = fc.collect_once("http://test", db_path=db)
    assert result["fetched"] == {"slo": True, "activity": True, "federation": True}
    assert result["recorded"]["heartbeat"] == 1
    assert result["recorded"]["boundaries"] == 1
    assert result["errors"] == []

    from monitoring.fleet_history import query_latest_heartbeat
    hb = query_latest_heartbeat(db_path=db)
    assert hb is not None
    assert hb["overall_status"] == "ready"


def test_collect_once_partial_failure_still_writes_heartbeat(db):
    """If /fleet/federation 503s, we still record a heartbeat — that's
    the load-bearing piece for the watchdog."""

    def fake_fetch(url, *, timeout=10.0):
        if url.endswith("/fleet/slo"):
            return _slo(), None
        if url.endswith("/fleet/activity"):
            return _activity(), None
        if url.endswith("/fleet/federation"):
            return None, "url error: 503"
        return None, "no match"

    with patch.object(fc, "_fetch_json", side_effect=fake_fetch):
        result = fc.collect_once("http://test", db_path=db)
    assert result["fetched"]["slo"] is True
    assert result["fetched"]["federation"] is False
    assert result["recorded"]["heartbeat"] == 1
    assert any(e["source"] == "federation" for e in result["errors"])

    from monitoring.fleet_history import query_latest_heartbeat
    hb = query_latest_heartbeat(db_path=db)
    assert hb is not None  # heartbeat landed even with federation down


def test_collect_once_full_failure_records_zero_count_heartbeat(db):
    """Even if every endpoint is down, the heartbeat still writes —
    the watchdog needs the row to know we tried."""

    def fake_fetch(url, *, timeout=10.0):
        return None, "url error: connection refused"

    with patch.object(fc, "_fetch_json", side_effect=fake_fetch):
        result = fc.collect_once("http://test", db_path=db)
    assert all(v is False for v in result["fetched"].values())
    # heartbeat still got written even with all-empty inputs (record_snapshot
    # accepts empty dicts — it records the cycle even though all the values
    # default).
    assert result["recorded"]["heartbeat"] == 1
    assert len(result["errors"]) == 3


def test_collect_once_swallows_record_exception(db):
    """If record_snapshot raises (it shouldn't, but defensive), the
    cycle reports the error rather than crashing the loop."""

    def fake_fetch(url, *, timeout=10.0):
        return _slo(), None  # any non-None body

    with patch.object(fc, "_fetch_json", side_effect=fake_fetch), \
         patch("monitoring.fleet_history.record_snapshot",
               side_effect=RuntimeError("boom")):
        result = fc.collect_once("http://test", db_path=db)
    assert any(e["source"] == "record" for e in result["errors"])


# ──────────────────────────────────────────────────────────────────────
# run_loop
# ──────────────────────────────────────────────────────────────────────


def test_run_loop_respects_max_cycles(db):
    def fake_fetch(url, *, timeout=10.0):
        if url.endswith("/fleet/slo"):
            return _slo(), None
        if url.endswith("/fleet/activity"):
            return _activity(), None
        return _fed(), None

    with patch.object(fc, "_fetch_json", side_effect=fake_fetch):
        cycles = fc.run_loop(
            base_url="http://test", interval_s=0.01,
            db_path=db, max_cycles=3,
        )
    assert cycles == 3


def test_run_loop_exits_on_stop_event(db):
    """stop_event.set() should break the loop within an interval."""
    stop = threading.Event()

    def fake_fetch(url, *, timeout=10.0):
        # On every fetch, signal the loop to stop.
        stop.set()
        return _slo(), None

    with patch.object(fc, "_fetch_json", side_effect=fake_fetch):
        cycles = fc.run_loop(
            base_url="http://test", interval_s=0.01,
            db_path=db, stop_event=stop,
        )
    assert cycles >= 1  # at least one full cycle ran
