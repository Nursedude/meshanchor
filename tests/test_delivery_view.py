"""MeshAnchor Delivery screen (MF port, 2026-09-23).

Pinned: reads the local map over HTTP (never the DB); tri-state per leg;
map-not-answering is UNKNOWN unless the daemon is absent/disabled by design;
thin / no-confirmable / no-ring windows read QUIET (the MF review's MED);
dead letters read DEGRADED; the window is the stall check's OWN function.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from utils import delivery_view as dv  # noqa: E402
from utils import active_health_checks_delivery as ahcd  # noqa: E402

NOW = 1_800_000_000.0


def _snap(confirmed=200, failed=0, confirmable=True, ring=True, **kw):
    r = ([{"ts": NOW - 60, "state": "confirmed", "protocol": "rns"}] * confirmed
         + [{"ts": NOW - 60, "state": "dropped", "protocol": "rns",
             "drop_reason": "rns_delivery_failed"}] * failed)
    s = {"state_totals": {"confirmed": 9, "sent": 3, "dropped": 1},
         "drop_reasons": {"dedup": 1},
         "state_by_protocol": {"confirmed": {"rns": 9} if confirmable else {}},
         "last_event_ts": NOW - 30, "health": {"preflight_ok": True}}
    if ring:
        s["recent_terminal"] = r
    s.update(kw)
    return s


def _queue(**kw):
    q = {"delivered": 5, "failed": 0, "retried": 0, "shed": 0, "pending": 0,
         "in_progress": 0, "dead_letter": 0, "queue_depth": 0,
         "max_queue_size": 1000, "timestamp": NOW - 1}
    q.update(kw)
    return q


def _fetcher(delivery=None, queue=None, down=False):
    def f(url):
        if down:
            raise OSError("connection refused")
        return delivery if url.endswith("/delivery") else queue
    return f


def _gather(tmp_path, organ="ok", enabled=True, **fk):
    return dv.gather(home=str(tmp_path), now=NOW,
                     unit_resolver=lambda u: (organ if u == dv.DAEMON_UNIT else "absent", None),
                     unit_enabled_fn=lambda u: enabled,
                     fetcher=_fetcher(**fk))


def test_window_is_the_checks_own_function():
    assert dv.confirmation_window is ahcd.confirmation_window
    assert dv.DELIVERY_STALL_MIN_TERMINAL is ahcd.DELIVERY_STALL_MIN_TERMINAL


def test_healthy_reads_arriving(tmp_path):
    v = _gather(tmp_path, delivery=_snap(), queue=_queue())
    assert v.headline().startswith("Messages are arriving")


def test_window_uses_the_screens_clock_not_the_wall(tmp_path, monkeypatch):
    """Fable MA r1 L1 (port of MF's 2026-11-07 pin): the 7-day window must be
    judged on the `now` the screen was handed. With the window's wall clock
    30 d past this file's fixed NOW, the ring must still read as judged — a
    wall-clock window would read TOO FEW (the 2027-01-22 time-bomb)."""
    from utils import active_health_checks_delivery as h
    monkeypatch.setattr(h.time, "time", lambda: NOW + 30 * 86400.0)
    v = _gather(tmp_path, delivery=_snap(), queue=_queue())
    lines = [ln for leg in v.legs for ln in leg.lines]
    assert any("200 confirmed / 0 failed of 200" in ln for ln in lines), lines
    assert any("72 h:" in ln for ln in lines), lines   # the format_window line


def test_span_line_reads_stamps_like_the_window(tmp_path):
    """Fable MA r3 LOW (2026-11-07): a ts=0 entry is 'unknown age' to the
    window; the span line measured it and printed 500000.0 h."""
    d = _snap(confirmed=25)
    d["recent_terminal"].append({"ts": 0, "state": "confirmed", "protocol": "rns"})
    v = _gather(tmp_path, delivery=d, queue=_queue())
    lines = [ln for leg in v.legs for ln in leg.lines]
    assert any("of unknown age" in ln for ln in lines), lines
    assert not any("500000" in ln for ln in lines), lines


def test_map_down_with_daemon_running_is_unknown_and_prints_no_number(tmp_path):
    v = _gather(tmp_path, down=True)
    assert v.headline().startswith("UNKNOWN")
    assert all(leg.lines == [] for leg in v.legs)


def test_no_daemon_is_inert(tmp_path):
    v = _gather(tmp_path, organ="absent", down=True)
    assert v.headline().startswith("NO DELIVERY ORGAN")


def test_stopped_and_disabled_daemon_is_inert_but_enabled_is_unknown(tmp_path):
    assert _gather(tmp_path, organ="down", enabled=False, down=True).headline().startswith("NO DELIVERY")
    assert _gather(tmp_path, organ="down", enabled=True, down=True).headline().startswith("UNKNOWN")
    assert _gather(tmp_path, organ="down", enabled=None, down=True).headline().startswith("UNKNOWN")


def test_thin_window_reads_quiet(tmp_path):
    v = _gather(tmp_path, delivery=_snap(confirmed=5), queue=_queue())
    assert v.headline().startswith("QUIET")


def test_no_confirmable_protocol_never_reads_arriving(tmp_path):
    v = _gather(tmp_path, delivery=_snap(confirmable=False), queue=_queue())
    assert v.headline().startswith("QUIET")


def test_missing_ring_never_reads_arriving(tmp_path):
    v = _gather(tmp_path, delivery=_snap(ring=False), queue=_queue())
    assert v.headline().startswith("QUIET")


def test_dead_letters_read_degraded(tmp_path):
    v = _gather(tmp_path, delivery=_snap(), queue=_queue(dead_letter=6))
    assert v.headline().startswith("DEGRADED")
    assert "DEAD LETTERS 6" in dv.render(v)


def test_db_unobservable_is_unknown(tmp_path):
    s = _snap(health={"db_unobservable": True, "preflight_ok": None})
    v = _gather(tmp_path, delivery=s, queue=_queue())
    assert v.legs[0].status == dv.UNKNOWN


def test_map_error_payload_is_unknown(tmp_path):
    v = _gather(tmp_path, delivery={"error": "delivery_counters_unavailable",
                                    "reason": "boom"}, queue=_queue())
    assert v.legs[0].status == dv.UNKNOWN and "delivery_counters_unavailable" in v.legs[0].why
