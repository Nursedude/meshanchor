"""Tests for monitoring.fleet_watchdog.

S5a — silence detector. Kinds:
  no_data     — heartbeat table empty.
  http_dead   — most recent heartbeat older than stale_threshold_s.
  frozen      — an OBSERVED uptime_s stuck (not advancing) across last N
                heartbeats, or the uptime source dark for the whole window;
                a lone missing sample is insufficient evidence, not a freeze.
  daemon_dead — meshanchor-daemon.service inactive ≥ 2 consecutive checks
                (added 2026-05-09 after BLACKOUT smoke surfaced the gap).

Plus reconcile_blackouts: open-on-detect + close-on-recover, idempotent.
"""
from __future__ import annotations

import json
import os
import time
from unittest.mock import patch

import pytest

from monitoring import fleet_history as fh
from monitoring import fleet_watchdog as wd

# Saved BEFORE the autouse fixture below patches the module attr —
# tests that need to exercise the real wrapper (e.g. the
# exception-handling test) call this reference directly.
_real_check_daemon_active = wd._check_daemon_active


@pytest.fixture
def db(tmp_path):
    return tmp_path / "fleet_watchdog.db"


@pytest.fixture(autouse=True)
def _stub_daemon_probe():
    """Default: daemon is active, streak resets every test.

    The watchdog's daemon_dead check shells out to systemctl, which
    isn't hermetic in CI. Every test gets a clean module-level streak
    counter and a probe stubbed to "active" by default — tests that
    care about the daemon-dead behavior override the patch with a
    nested ``with patch(...)`` block.
    """
    wd._reset_daemon_state()
    with patch.object(wd, "_check_daemon_active", return_value=True):
        yield
    wd._reset_daemon_state()


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


def test_frozen_abstains_on_transient_missing_uptime(db):
    """A single degraded cycle (one NULL uptime_s among fresh, ADVANCING
    heartbeats) must NOT fire frozen. The collector writes a heartbeat
    even when a /fleet/slo fetch times out, storing uptime_s=None; reading
    that lone gap as a freeze was the flapping "uptime_s missing" false
    blackout (honest_failure_modes #1/#2 — absence of a sample is not
    evidence of a stuck counter)."""
    _heartbeat(db, ts=1000.0, uptime_s=10.0)    # observed, advancing
    _heartbeat(db, ts=1060.0, uptime_s=None)    # one degraded cycle
    _heartbeat(db, ts=1120.0, uptime_s=130.0)   # observed, advancing
    out = wd.detect_silence(
        db_path=db, now=1130.0,
        stale_threshold_s=180.0,
        frozen_window_cycles=3,
    )
    assert out[wd.KIND_FROZEN] is None
    assert out[wd.KIND_HTTP_DEAD] is None


def test_frozen_abstains_on_partial_missing_window(db):
    """Even a partial window that LOOKS stuck (present values identical)
    is insufficient evidence when a sample is missing — a holey window
    can't establish non-advancement. Abstain rather than accuse."""
    _heartbeat(db, ts=1000.0, uptime_s=100.0)
    _heartbeat(db, ts=1060.0, uptime_s=None)    # gap
    _heartbeat(db, ts=1120.0, uptime_s=100.0)
    out = wd.detect_silence(
        db_path=db, now=1130.0,
        stale_threshold_s=180.0,
        frozen_window_cycles=3,
    )
    assert out[wd.KIND_FROZEN] is None


def test_frozen_surfaces_when_all_uptime_missing(db):
    """When EVERY heartbeat in the window carries no uptime_s while the
    heartbeats keep landing (fresh ts → invisible to http_dead), the
    uptime source is dark — a real signal we must not swallow (#9). Fires
    frozen with the honest 'missing' reason."""
    _heartbeat(db, ts=1000.0, uptime_s=None)
    _heartbeat(db, ts=1060.0, uptime_s=None)
    _heartbeat(db, ts=1120.0, uptime_s=None)
    out = wd.detect_silence(
        db_path=db, now=1130.0,
        stale_threshold_s=180.0,
        frozen_window_cycles=3,
    )
    assert out[wd.KIND_FROZEN] is not None
    assert "missing" in out[wd.KIND_FROZEN]
    assert out[wd.KIND_HTTP_DEAD] is None


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


# ──────────────────────────────────────────────────────────────────────
# detect_silence — daemon_dead (added 2026-05-09)
#
# These tests target the failure mode that survived the original
# three-rule design: meshanchor-daemon down + map up. The map keeps
# serving /fleet/*, heartbeats land, frozen-rule reads the map's
# still-incrementing uptime → none of the original rules fire.
# `daemon_dead` queries `check_service('meshanchor-daemon')` directly
# with 2-cycle hysteresis to avoid flap-firing during routine restarts.
# ──────────────────────────────────────────────────────────────────────


def test_daemon_dead_listed_in_all_kinds():
    """Regression guard — reconcile_blackouts iterates ALL_KINDS,
    so adding the kind to the constant is what wires it through to
    the open/close lifecycle."""
    assert wd.KIND_DAEMON_DEAD in wd.ALL_KINDS
    assert wd.KIND_DAEMON_DEAD == "daemon_dead"


def test_daemon_dead_does_not_fire_on_single_inactive_read(db):
    """Hysteresis: a single 'not active' result must not fire — that's
    almost always a transient (a 5s daemon restart caught mid-cycle)."""
    _heartbeat(db, ts=1000.0, uptime_s=10.0)
    with patch.object(wd, "_check_daemon_active", return_value=False):
        out = wd.detect_silence(db_path=db, now=1010.0)
    assert out[wd.KIND_DAEMON_DEAD] is None
    assert wd._daemon_state["inactive_streak"] == 1


def test_daemon_dead_fires_on_second_consecutive_inactive_read(db):
    """Two consecutive inactive reads = real outage. Default
    DAEMON_HYSTERESIS_CYCLES is 2 so this is the "fires for real" case."""
    _heartbeat(db, ts=1000.0, uptime_s=10.0)
    with patch.object(wd, "_check_daemon_active", return_value=False):
        wd.detect_silence(db_path=db, now=1010.0)  # streak → 1
        out = wd.detect_silence(db_path=db, now=1040.0)  # streak → 2 ⇒ fire
    assert out[wd.KIND_DAEMON_DEAD] is not None
    assert "not active" in out[wd.KIND_DAEMON_DEAD]
    assert "2 consecutive" in out[wd.KIND_DAEMON_DEAD]


def test_daemon_dead_streak_resets_on_recovery(db):
    """A single 'active' read between two 'inactive' reads must NOT
    fire — the streak resets on every active confirmation."""
    _heartbeat(db, ts=1000.0, uptime_s=10.0)
    with patch.object(wd, "_check_daemon_active", return_value=False):
        wd.detect_silence(db_path=db, now=1010.0)  # streak → 1
    with patch.object(wd, "_check_daemon_active", return_value=True):
        wd.detect_silence(db_path=db, now=1040.0)  # streak → 0
    assert wd._daemon_state["inactive_streak"] == 0
    with patch.object(wd, "_check_daemon_active", return_value=False):
        out = wd.detect_silence(db_path=db, now=1070.0)  # streak → 1, no fire
    assert out[wd.KIND_DAEMON_DEAD] is None


def test_daemon_dead_clears_immediately_on_recovery(db):
    """Once daemon comes back, the next detect call returns None even
    if a previous cycle had reported daemon_dead. reconcile_blackouts
    will then close the open blackout row on the next reconcile pass."""
    _heartbeat(db, ts=1000.0, uptime_s=10.0)
    with patch.object(wd, "_check_daemon_active", return_value=False):
        wd.detect_silence(db_path=db, now=1010.0)
        out_fired = wd.detect_silence(db_path=db, now=1040.0)
    assert out_fired[wd.KIND_DAEMON_DEAD] is not None
    with patch.object(wd, "_check_daemon_active", return_value=True):
        out_recovered = wd.detect_silence(db_path=db, now=1070.0)
    assert out_recovered[wd.KIND_DAEMON_DEAD] is None
    assert wd._daemon_state["inactive_streak"] == 0


def test_daemon_dead_probe_exception_leaves_streak_unchanged(db):
    """If check_service raises (transient systemctl error), the probe
    returns None — the streak counter must NOT change. Otherwise an
    intermittent systemctl failure would either falsely fire or
    falsely clear a real outage."""
    _heartbeat(db, ts=1000.0, uptime_s=10.0)
    with patch.object(wd, "_check_daemon_active", return_value=False):
        wd.detect_silence(db_path=db, now=1010.0)  # streak → 1
    with patch.object(wd, "_check_daemon_active", return_value=None):
        out = wd.detect_silence(db_path=db, now=1040.0)  # streak unchanged
    assert wd._daemon_state["inactive_streak"] == 1
    assert out[wd.KIND_DAEMON_DEAD] is None


def test_check_daemon_active_swallows_check_service_exception():
    """The wrapper itself must not propagate. The watchdog can't crash
    on a transient systemctl error mid-cycle. Calls the saved real
    function so the autouse fixture's stub doesn't short-circuit."""
    with patch("utils.service_check.check_service",
               side_effect=RuntimeError("systemctl boom")):
        result = _real_check_daemon_active()
    assert result is None


def test_reconcile_opens_daemon_dead_through_to_blackout_row(db):
    """End-to-end: detect → reconcile → row in blackout_events with
    kind='daemon_dead'. The wiring through ALL_KINDS is what makes
    this work without any reconcile-side changes."""
    _heartbeat(db, ts=1000.0, uptime_s=10.0)
    with patch.object(wd, "_check_daemon_active", return_value=False):
        wd.detect_silence(db_path=db, now=1010.0)
        decisions = wd.detect_silence(db_path=db, now=1040.0)
    summary = wd.reconcile_blackouts(decisions, now=1040.0, db_path=db)
    assert summary[wd.KIND_DAEMON_DEAD] == "opened"
    rows = fh.query_active_blackouts(db_path=db)
    kinds = {r["kind"] for r in rows}
    assert "daemon_dead" in kinds


def test_daemon_dead_evaluated_on_empty_heartbeat_table(db):
    """QA audit 2026-07-06: the empty-table early return must NOT skip the
    daemon_dead check (it's independent of heartbeat data) — else reconcile
    would wrongly CLOSE a valid daemon_dead blackout (honest_failure #2)."""
    wd._daemon_state["inactive_streak"] = wd.DAEMON_HYSTERESIS_CYCLES
    with patch.object(wd, "_check_daemon_active", return_value=False):
        out = wd.detect_silence(db_path=db, now=1000.0)
    assert out[wd.KIND_NO_DATA] is not None       # table is empty
    assert out[wd.KIND_DAEMON_DEAD] is not None   # still evaluated, not skipped
    wd._daemon_state["inactive_streak"] = 0


# ── mini_dead: EXTERNAL watcher for total mini-dudeai death (WS-A) ───────────
# The mini's own within-tick MiniSelfSource cannot detect total death (a source
# can't run if its loop is dead). fleet_watchdog closes that gap by watching the
# operator's mini_dudeai_state.json freshness — inert on a box with no mini.

def _ma_mini_dir(home):
    d = home / ".local" / "share" / "meshanchor" / "mini"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _write_mini_state(home, last_tick_ts):
    (_ma_mini_dir(home) / "state.json").write_text(
        json.dumps({"last_tick_ts": last_tick_ts}))


def test_mini_dead_reads_ma_namespaced_state():
    # honest_failure_modes #5: the watchdog's mini_dead path MUST equal the MA
    # preset's ma_mini_dir — two consumers, one location. If either moves, this
    # fails. (The preset is the SSOT for the location.)
    import os as _os
    from mini_dudeai.presets.meshanchor_fleet import ma_mini_dir
    home = "/home/someuser"
    expect = _os.path.join(ma_mini_dir(home), "state.json")
    from unittest.mock import patch as _patch
    with _patch("utils.paths.get_real_user_home", return_value=home):
        # exercise the reader against a home with NO state → None (not installed),
        # proving it looks in the MA namespace (a MeshForge-namespace file would
        # not be found here either, which is the point).
        wd._reset_mini_dead_state()
        assert wd._mini_dead_reason(now=1.0) is None
    assert expect.endswith("/.local/share/meshanchor/mini/state.json")


def test_mini_dead_listed_in_all_kinds():
    """Regression guard — reconcile_blackouts + _notify iterate ALL_KINDS, so
    the constant membership is what wires mini_dead through the lifecycle."""
    assert wd.KIND_MINI_DEAD in wd.ALL_KINDS
    assert wd.KIND_MINI_DEAD == "mini_dead"
    assert wd.KIND_MINI_DEAD in wd._KIND_PRIORITY and wd.KIND_MINI_DEAD in wd._KIND_TAGS


def test_mini_dead_none_when_state_absent(tmp_path):
    # No mini installed on this box → not applicable (declared-absent ≠ dead).
    wd._reset_mini_dead_state()
    with patch("utils.paths.get_real_user_home", return_value=tmp_path):
        assert wd._mini_dead_reason(now=10_000.0) is None
    assert wd._mini_dead_state["stale_streak"] == 0


def test_mini_dead_none_when_state_fresh(tmp_path):
    wd._reset_mini_dead_state()
    _write_mini_state(tmp_path, last_tick_ts=10_000.0)
    with patch("utils.paths.get_real_user_home", return_value=tmp_path):
        assert wd._mini_dead_reason(now=10_060.0) is None   # 60s < 300s
    assert wd._mini_dead_state["stale_streak"] == 0


def test_mini_dead_hysteresis_then_fires(tmp_path):
    wd._reset_mini_dead_state()
    _write_mini_state(tmp_path, last_tick_ts=10_000.0)
    with patch("utils.paths.get_real_user_home", return_value=tmp_path):
        first = wd._mini_dead_reason(now=10_500.0)    # 500s stale, streak → 1
        second = wd._mini_dead_reason(now=10_530.0)   # streak → 2 ⇒ fire
    assert first is None
    assert second is not None
    assert "second brain" in second and "stale" in second


def test_mini_dead_silent_on_graceful_stop(tmp_path):
    # A clean-exit marker newer than the last tick = the operator stopped it on
    # purpose (the engine stamps it on SIGTERM) → not a death, no false page.
    wd._reset_mini_dead_state()
    _write_mini_state(tmp_path, last_tick_ts=10_000.0)
    marker = _ma_mini_dir(tmp_path) / "clean_exit"
    marker.write_text("stopped")
    os.utime(marker, (10_050.0, 10_050.0))            # newer than last tick
    with patch("utils.paths.get_real_user_home", return_value=tmp_path):
        assert wd._mini_dead_reason(now=10_500.0) is None
        assert wd._mini_dead_reason(now=10_530.0) is None
    assert wd._mini_dead_state["stale_streak"] == 0


def test_mini_dead_indeterminate_on_unreadable_state(tmp_path):
    # Malformed state → indeterminate: never accuse AND never clear — leave the
    # streak unchanged (a transient read error must not move the verdict).
    wd._reset_mini_dead_state()
    wd._mini_dead_state["stale_streak"] = 1
    (_ma_mini_dir(tmp_path) / "state.json").write_text("{not valid json")
    with patch("utils.paths.get_real_user_home", return_value=tmp_path):
        assert wd._mini_dead_reason(now=10_500.0) is None
    assert wd._mini_dead_state["stale_streak"] == 1


def test_mini_dead_evaluated_on_empty_heartbeat_table(db, tmp_path):
    # Independent of heartbeat data — an empty table must still evaluate mini_dead
    # (else reconcile would wrongly CLOSE a genuinely-active mini_dead blackout).
    wd._reset_mini_dead_state()
    _write_mini_state(tmp_path, last_tick_ts=10_000.0)
    with patch("utils.paths.get_real_user_home", return_value=tmp_path):
        wd.detect_silence(db_path=db, now=10_500.0)           # streak → 1
        out = wd.detect_silence(db_path=db, now=10_530.0)     # streak → 2 ⇒ fire
    assert out[wd.KIND_MINI_DEAD] is not None
    assert out[wd.KIND_NO_DATA] == "heartbeat table is empty"   # empty-table path
