"""The 7-day confirmation window + the 72 h collapse COUNTER (2026-11-07 port
of MeshForge e31fe908; operator decision: the counter is OBSERVATION ONLY).

meshanchor-server measured 2026-11-07: 8 RNS terminals over 31.7 h → the
check sat in low_traffic. These pin the TIME window, the clock-hazard
directions, the honest labels, and that the counter never flips `healthy`.
"""
import pytest

from utils import active_health_checks_delivery as h

NOW = 1_800_000_000.0
DAY = 86400.0


def _ev(age_s, state="confirmed", proto="rns", reason=None, ts=...):
    return {"ts": (NOW - age_s) if ts is ... else ts, "id": "x", "state": state,
            "protocol": proto, "drop_reason": reason}


def _snap(ring, key="recent_terminal"):
    return {"state_by_protocol": {"confirmed": {"rns": 1599}}, key: ring}


def W(ring, **kw):
    return h.confirmation_window(_snap(ring), now=kw.pop("now", NOW), **kw)


def test_events_older_than_the_window_are_out_and_counted():
    w = W([_ev(h.DELIVERY_STALL_WINDOW_S + 60)] * 3 + [_ev(DAY)] * 4)
    assert (w["confirmed"] + w["failed"], w["out_of_window"]) == (4, 3)
    assert len(w["ring"]) == 4


def test_set_semantics_and_no_walk_without_a_confirmable_protocol_are_kept():
    """MA's own review history (2026-09-23 differential fuzz): confirmable is
    a SET, and a corrupt ring is never walked when nothing can confirm."""
    w = h.confirmation_window({"state_by_protocol": {"confirmed": {}},
                               "recent_terminal": [{"protocol": ["unhashable"]}]}, now=NOW)
    assert w["confirmable"] == set() and w["confirmed"] == 0


def test_future_beyond_slop_and_undated_stay_in_window_never_in_counter():
    ring = ([_ev(-5 * DAY, "dropped", reason="rns_delivery_failed")] * 3
            + [_ev(0, "dropped", reason="rns_delivery_failed", ts=None)] * 3)
    w = W(ring)
    assert w["failed"] == 6 and w["undated"] == 6
    assert w["collapse"]["terminal"] == 0 and w["collapse"]["candidate"] is False


def test_clock_jumped_forward_reads_too_few_never_healthy_ok():
    r = h.check_delivery_confirmation_stall(snap=_snap([_ev(0)] * 30), now=NOW + 30 * DAY)
    assert r.reason.startswith("low_traffic")


def test_collapse_counter_is_observation_only():
    ring = [_ev(i * 3600, "dropped", reason="rns_delivery_failed") for i in range(6)]
    c = W(ring)["collapse"]
    assert c["candidate"] is True and c["observe_only"] is True
    r = h.check_delivery_confirmation_stall(snap=_snap(ring), now=NOW)
    assert r.healthy is True and r.reason.startswith("low_traffic")
    assert "COLLAPSE CANDIDATE (observe-only)" in r.reason


def test_quiet_box_over_six_days_is_judged():
    r = h.check_delivery_confirmation_stall(
        snap=_snap([_ev(i * 6 * 3600) for i in range(24)]), now=NOW)
    assert r.reason.startswith("confirm_ok") and "last 7 d" in r.reason


def test_rate_failure_still_unhealthy_and_carries_the_window():
    ring = [_ev(i * 3600, "dropped", reason="rns_delivery_failed") for i in range(25)]
    r = h.check_delivery_confirmation_stall(snap=_snap(ring), now=NOW)
    assert r.healthy is False and "wedge" in r.reason and "last 7 d" in r.reason


def test_count_bounded_label_either_ring():
    w = W([_ev(i * 600) for i in range(h.SERVED_TERMINAL_LIMIT)])
    assert w["truncated"] and h.format_window(w).startswith("newest 200 terminals")
    legacy = ([_ev(i * 400, "sent", "meshtastic") for i in range(170)]
              + [_ev(i * 400) for i in range(30)])
    w = h.confirmation_window(_snap(legacy, key="recent"), now=NOW)
    assert w["truncated"] and h.format_window(w).startswith("newest 200 `recent` events")


def test_reason_names_the_ring_it_judged():
    r = h.check_delivery_confirmation_stall(
        snap=_snap([_ev(30 * DAY)] * 7 + [_ev(60)] * 2), now=NOW)
    assert "2 in-window of 9 `recent_terminal` events" in r.reason
    assert "7 older outside" in r.reason


def test_shared_constants_pinned():
    from gateway import delivery_counters as dc
    assert h.SERVED_TERMINAL_LIMIT == dc.SNAPSHOT_RECENT_LIMIT


def test_full_ring_reaching_past_the_edge_is_age_bounded():
    """Fable MA r1 L2: old NON-confirmable entries in a full ring still prove
    the ring reaches past the window edge — not "count-bounded"."""
    ring = ([_ev(8 * DAY, "dropped", "meshtastic", "unsendable")] * 150
            + [_ev(i * 600) for i in range(50)])
    w = W(ring)
    assert w["out_of_window"] == 0 and w["truncated"] is False
    assert h.format_window(w).startswith("last 7 d")


@pytest.mark.parametrize("ts", [0, 0.0, -5])
def test_epoch_zero_ts_is_undated_not_ancient(ts):
    """Fable MA r2 INFO: `now - 0` is the absent-value sentinel class."""
    w = W([_ev(0, ts=ts)])
    assert (w["undated"], w["out_of_window"]) == (1, 0)
