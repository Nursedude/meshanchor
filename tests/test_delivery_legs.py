"""BIC-2 slice 1 (MeshAnchor twin of MeshForge tests/test_delivery_legs.py) —
per-leg delivery counts over 15 min / 1 h / 24 h. Counter-level tests only;
the bridge send-path tests port with the MA send slices.

A leg is ``<source>><destination>`` transport (operator 2026-10-23:
"transport + direction"). Counts live in 5-minute buckets (``BUCKET_S``) inside the
existing delivery_counters.db so a gateway restart does not blind them
(the DET-DCS class). Observe before alarm: these are counts only.

Seam: ``DeliveryCounters.record(..., source=...)`` → ``snapshot()["legs"]``.
"""
from __future__ import annotations

import time

import pytest

from gateway import delivery_counters as dc
from gateway.delivery_counters import DeliveryCounters, DeliveryState


@pytest.fixture
def counters(tmp_path):
    return DeliveryCounters(db_path=tmp_path / "dc.db", run_preflight=False)


def test_sent_on_a_leg_is_counted_in_every_window(counters):
    now = time.time()
    counters.record(DeliveryState.SENT, "m1", protocol="rns",
                    source="meshtastic", ts=now - 60)

    leg = counters.snapshot()["legs"]["meshtastic>rns"]

    assert leg["15m"]["sent"] == 1
    assert leg["1h"]["sent"] == 1
    assert leg["24h"]["sent"] == 1


def test_a_caller_that_names_no_source_is_counted_under_question_mark(counters):
    counters.record(DeliveryState.SENT, "m1", protocol="rns")

    legs = counters.snapshot()["legs"]

    assert "?>rns" in legs
    assert not any(k.endswith(">rns") and k != "?>rns" for k in legs)


def test_window_edges_two_hours_ago_is_only_in_24h(counters):
    now = time.time()
    counters.record(DeliveryState.SENT, "old", protocol="rns",
                    source="meshtastic", ts=now - 2 * 3600)

    leg = counters.snapshot()["legs"]["meshtastic>rns"]

    assert "15m" not in leg      # absent, not a zero
    assert "1h" not in leg
    assert leg["24h"]["sent"] == 1


def test_older_than_24h_is_in_no_window(counters):
    now = time.time()
    counters.record(DeliveryState.SENT, "ancient", protocol="rns",
                    source="meshtastic", ts=now - 3 * 86400)
    counters.record(DeliveryState.SENT, "fresh", protocol="meshtastic",
                    source="rns", ts=now)

    legs = counters.snapshot()["legs"]

    assert "meshtastic>rns" not in legs
    assert legs["rns>meshtastic"]["15m"]["sent"] == 1


def test_counts_survive_a_restart(tmp_path):
    db = tmp_path / "dc.db"
    DeliveryCounters(db_path=db, run_preflight=False).record(
        DeliveryState.CONFIRMED, "m1", protocol="rns", source="meshtastic")

    reopened = DeliveryCounters(db_path=db, run_preflight=False)

    assert reopened.snapshot()["legs"]["meshtastic>rns"]["15m"]["confirmed"] == 1


def test_delivery_failures_and_benign_drops_are_separate(counters):
    counters.record(DeliveryState.DROPPED, "f", protocol="rns",
                    source="meshtastic",
                    drop_reason=dc.DropReason.RETRIES_EXHAUSTED)
    counters.record(DeliveryState.DROPPED, "d", protocol="rns",
                    source="meshtastic", drop_reason=dc.DropReason.DEDUP)

    win = counters.snapshot()["legs"]["meshtastic>rns"]["15m"]

    assert win["failed"] == 1
    assert win["dropped_other"] == 1


def test_a_lane_is_counted_under_its_transport(counters):
    counters.record(DeliveryState.SENT, "m1", protocol="secondary",
                    source="rns")

    assert counters.snapshot()["legs"]["rns>meshtastic"]["15m"]["sent"] == 1


def test_an_unreadable_db_reads_unobservable_not_quiet(counters, monkeypatch):
    import sqlite3

    def boom():
        raise sqlite3.OperationalError("disk I/O error")
    monkeypatch.setattr(counters, "_connect", boom)

    assert counters.snapshot()["legs"] is None


def test_a_lane_named_as_source_is_counted_under_its_transport(counters):
    counters.record(DeliveryState.SENT, "m1", protocol="rns",
                    source="secondary")

    assert "meshtastic>rns" in counters.snapshot()["legs"]


def test_tracking_start_is_stamped_by_the_first_leg_write_not_by_a_reader(
        tmp_path):
    # Review R5: the map daemon (a reader) opening the file first must not
    # start the clock while an old gateway writes no leg counts at all.
    db = tmp_path / "dc.db"
    reader = DeliveryCounters(db_path=db, run_preflight=False).snapshot()
    assert reader["legs_since_ts"] is None
    assert {"15m", "1h", "24h"} <= set(reader["legs_partial_windows"])

    before = time.time()
    DeliveryCounters(db_path=db, run_preflight=False).record(
        DeliveryState.SENT, "m1", protocol="rns", source="meshtastic")
    since = DeliveryCounters(db_path=db, run_preflight=False).snapshot()[
        "legs_since_ts"]
    assert before - 1 <= since <= time.time() + 1

    DeliveryCounters(db_path=db, run_preflight=False).record(
        DeliveryState.SENT, "m2", protocol="rns", source="meshtastic")
    assert DeliveryCounters(db_path=db, run_preflight=False).snapshot()[
        "legs_since_ts"] == since


def test_windows_longer_than_tracking_are_named_partial(counters):
    # A fresh DB: 15m may be whole, 1h and 24h cannot be.
    assert {"1h", "24h"} <= set(counters.snapshot()["legs_partial_windows"])


def test_every_state_and_drop_reason_maps_into_the_closed_outcome_set():
    # hfm #7: a new DeliveryState or DropReason must land in OUTCOMES, not
    # invent a bucket word no reader knows.
    from gateway.delivery_counters import DeliveryEvent, DropReason, _leg_outcome
    from gateway.delivery_legs import OUTCOMES

    for state in DeliveryState:
        reasons = list(DropReason) if state == DeliveryState.DROPPED else [None]
        for reason in reasons:
            ev = DeliveryEvent(ts=0.0, id="x", state=state, drop_reason=reason)
            assert _leg_outcome(ev) in OUTCOMES, (state, reason)





def test_a_broken_leg_table_does_not_blind_the_core_snapshot(tmp_path):
    # R6: legs are additive and measure-only; a read error there must not
    # blank the counters, the confirmation view or the health block.
    db = tmp_path / "dc.db"
    c = DeliveryCounters(db_path=db, run_preflight=False)
    c.record(DeliveryState.SENT, "m1", protocol="rns", source="meshtastic")
    import sqlite3
    with sqlite3.connect(db) as conn:
        conn.execute("DROP TABLE leg_buckets")

    snap = c.snapshot()

    assert snap["legs"] is None
    assert snap["state_totals"]["sent"] == 1
    assert not snap["health"].get("db_unobservable")


def test_a_leg_write_error_keeps_the_core_event_and_leaves_a_witness(
        counters, monkeypatch):
    # R7: a leg-bookkeeping failure must not roll back core accounting.
    import sqlite3
    from gateway import delivery_legs

    def boom(*a, **k):
        raise sqlite3.OperationalError("leg table broke")
    monkeypatch.setattr(delivery_legs, "bump", boom)

    counters.record(DeliveryState.SENT, "m1", protocol="rns",
                    source="meshtastic")
    snap = counters.snapshot()

    assert snap["state_totals"]["sent"] == 1
    assert snap["legs_write_errors"] == 1


def test_an_events_source_is_persisted_for_the_per_message_trace(tmp_path):
    # R8: to_dict() serialised source but the events table dropped it.
    db = tmp_path / "dc.db"
    DeliveryCounters(db_path=db, run_preflight=False).record(
        DeliveryState.SENT, "m1", protocol="rns", source="meshtastic")

    recent = DeliveryCounters(db_path=db, run_preflight=False).snapshot()[
        "recent"]

    assert recent[0]["source"] == "meshtastic"


def test_the_15m_window_does_not_reach_back_20_minutes(counters):
    # R9: 15-min buckets let "15m" sum up to 30 min of traffic, so a
    # killed leg kept showing sends for half an hour.
    counters.record(DeliveryState.SENT, "m1", protocol="rns",
                    source="meshtastic", ts=time.time() - 20 * 60)

    leg = counters.snapshot()["legs"]["meshtastic>rns"]

    assert "15m" not in leg
    assert leg["1h"]["sent"] == 1


@pytest.mark.parametrize("source", ["Meshtastic", "mqtt_bridge", "radio"])
def test_a_source_outside_the_transport_vocabulary_counts_as_unknown(
        counters, source):
    # R10 / hfm #7: a typo must not mint a new leg nobody reads.
    counters.record(DeliveryState.SENT, "m1", protocol="rns", source=source)

    assert set(counters.snapshot()["legs"]) == {"?>rns"}


# ── review round 2 (non-author re-review of daa93e19, 10-23) ──────────

def test_a_half_done_leg_write_is_undone_whole(counters, monkeypatch):
    # F2: bump() is three statements; a failure partway must not leave a
    # counted bucket beside a witness that says it was not counted.
    import sqlite3
    from gateway import delivery_legs
    real = delivery_legs.bump

    def half(conn, ts, leg, outcome):
        real(conn, ts, leg, outcome)
        raise sqlite3.OperationalError("prune failed")
    monkeypatch.setattr(delivery_legs, "bump", half)

    counters.record(DeliveryState.SENT, "m1", protocol="rns",
                    source="meshtastic")
    snap = counters.snapshot()

    assert snap["state_totals"]["sent"] == 1          # core kept
    assert "meshtastic>rns" not in snap["legs"]        # leg undone whole
    assert snap["legs_since_ts"] is None               # stamp undone too
    assert snap["legs_write_errors"] == 1


def test_a_leg_error_that_rolled_back_the_core_write_is_a_core_write_error(
        counters, monkeypatch):
    # F1: SQLITE_FULL/IOERR roll the WHOLE transaction back. The core event
    # is then gone, and record() must say so — not report success.
    import sqlite3
    from gateway import delivery_legs

    def full(conn, ts, leg, outcome):
        conn.execute("ROLLBACK")       # what SQLite does on SQLITE_FULL
        raise sqlite3.OperationalError("database or disk is full")
    monkeypatch.setattr(delivery_legs, "bump", full)

    counters.record(DeliveryState.SENT, "m1", protocol="rns",
                    source="meshtastic")
    snap = counters.snapshot()

    assert snap["health"]["consecutive_write_errors"] >= 1
    # Review F2 (10-23): the witness names the REAL cause, not the
    # "no such savepoint" the follow-on ROLLBACK TO raises.
    assert "database or disk is full" in (
        snap["health"]["last_write_error"] or "")


def test_tracking_start_is_the_write_time_not_a_backdated_event_ts(counters):
    # F6: a replayed or clock-stepped first event must not pull since_ts
    # back and make an uncovered span read whole.
    before = time.time()
    counters.record(DeliveryState.SENT, "m1", protocol="rns",
                    source="meshtastic", ts=before - 3600)

    assert counters.snapshot()["legs_since_ts"] >= before - 1


def test_history_for_carries_the_source(counters):
    # F4: the per-message trace dropped the persisted source.
    counters.record(DeliveryState.SENT, "m1", protocol="rns",
                    source="meshtastic")

    assert [e.source for e in counters.history_for("m1")] == ["meshtastic"]


def test_a_leg_write_error_carries_when_it_last_happened(counters, monkeypatch):
    # F9: a lifetime count alone cannot tell "broke once" from "broken now".
    import sqlite3
    from gateway import delivery_legs

    def boom(*a, **k):
        raise sqlite3.OperationalError("leg table broke")
    monkeypatch.setattr(delivery_legs, "bump", boom)
    before = time.time()

    counters.record(DeliveryState.SENT, "m1", protocol="rns",
                    source="meshtastic")

    assert counters.snapshot()["legs_last_write_error_ts"] >= before - 1


def test_reset_for_tests_clears_the_leg_tables(counters):
    # F10: since_ts now comes from the first bump; a reset that kept it
    # would leak one test's legs into the next.
    counters.record(DeliveryState.SENT, "m1", protocol="rns",
                    source="meshtastic")

    counters._reset_for_tests()
    snap = counters.snapshot()

    assert snap["legs"] == {} and snap["legs_since_ts"] is None


def test_the_snapshot_publishes_its_bucket_size(counters):
    # The TUI pane derives each window's true reach (window + one bucket)
    # from this, never from a hardcoded sentence (pane review #5).
    from gateway import delivery_legs

    assert counters.snapshot()["legs_bucket_s"] == delivery_legs.BUCKET_S


# ── port review (contextless reviewer, 10-23) ──────────────────────

def test_a_window_is_whole_once_counting_covers_it():
    # F4: every other partial test runs with since_ts=None, where every
    # window is partial by construction; an always-partial bug passed them.
    from gateway.delivery_legs import partial_windows
    now = 1_000_000.0
    assert partial_windows(now - 20 * 60, now) == ["1h", "24h"]
    assert partial_windows(now - 2 * 3600, now) == ["24h"]
    assert partial_windows(now - 25 * 3600, now) == []


def test_a_migration_that_lost_the_race_to_another_process_is_not_an_error(
        tmp_path):
    # F1: PRAGMA-then-ALTER is not atomic; the gateway and the map daemon
    # migrate together on a deploy, and the loser's ALTER says "duplicate
    # column name" out of the constructor.
    import sqlite3
    from gateway.delivery_counters import _add_column
    conn = sqlite3.connect(str(tmp_path / "x.db"))
    conn.execute("CREATE TABLE events (ts REAL)")
    conn.execute("ALTER TABLE events ADD COLUMN source TEXT NOT NULL DEFAULT ''")

    _add_column(conn, "events", "source")      # the other process won

    with pytest.raises(sqlite3.OperationalError):
        _add_column(conn, "no_such_table", "source")


def test_a_pre_bic2_db_gains_the_source_column_and_keeps_its_rows(tmp_path):
    # F3: every other test builds a fresh DB, so the ALTER branch never ran.
    import sqlite3
    db = tmp_path / "legacy.db"
    conn = sqlite3.connect(str(db))
    conn.execute(
        "CREATE TABLE events (ts REAL NOT NULL, id TEXT NOT NULL DEFAULT '',"
        " state TEXT NOT NULL, protocol TEXT, drop_reason TEXT,"
        " note TEXT NOT NULL DEFAULT '')")
    conn.execute("CREATE TABLE counters (key TEXT PRIMARY KEY,"
                 " value INTEGER NOT NULL DEFAULT 0)")
    conn.execute("INSERT INTO events VALUES(1.0, 'old', 'sent', 'rns', NULL, '')")
    conn.commit()
    conn.close()

    c = DeliveryCounters(db_path=db, run_preflight=False)
    c.record(DeliveryState.SENT, "new", protocol="rns", source="meshcore")

    cols = {r[1] for r in
            sqlite3.connect(str(db)).execute("PRAGMA table_info(events)")}
    assert "source" in cols
    by_id = {e.id: e for e in c.recent()}
    assert by_id["old"].source is None
    assert by_id["new"].source == "meshcore"
    assert c.snapshot()["legs"]["meshcore>rns"]["15m"]["sent"] == 1
