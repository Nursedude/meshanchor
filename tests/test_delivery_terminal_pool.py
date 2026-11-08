"""Judged terminal events get their OWN prune pool (2026-11-07 port of
MeshForge e31fe908).

The events table pruned every non-refusal row as ONE pool. Measured on
meshanchor-server 2026-11-07: `recent_terminal` held 8 RNS terminals over
31.7 h while Meshtastic + MeshCore `sent` filled the 200-row `recent` ring in
28.7 h — the delivery-confirmation check sat in low_traffic (8 < 20), so a
TOTAL RNS collapse there would have read the same.
"""
import sqlite3

from gateway import delivery_counters as dc


def _c(tmp_path, cap=40):
    return dc.DeliveryCounters(db_path=tmp_path / "dc.db", run_preflight=False,
                               ring_cap=cap)


def _count(tmp_path, where, args=()):
    with sqlite3.connect(str(tmp_path / "dc.db")) as raw:
        return raw.execute(f"SELECT COUNT(*) FROM events WHERE {where}", args).fetchone()[0]


def test_inflight_flood_never_evicts_a_judged_terminal(tmp_path):
    c = _c(tmp_path)
    for i in range(5):
        c.record(dc.DeliveryState.CONFIRMED, f"ok{i}", protocol="rns", source="meshcore")
    c.record(dc.DeliveryState.DROPPED, "f0", protocol="rns", source="meshcore",
             drop_reason=dc.DropReason.RNS_DELIVERY_FAILED)
    for i in range(200):     # meshanchor-server's shape: mesh sends swamp the ring
        c.record(dc.DeliveryState.SENT, f"m{i}", protocol="meshtastic", source="rns")
        c.record(dc.DeliveryState.SENT, f"c{i}", protocol="meshcore", source="rns")
    states = [e["state"] for e in c.snapshot()["recent_terminal"]]
    assert states.count("confirmed") == 5 and states.count("dropped") == 1
    assert _count(tmp_path, "state = 'sent'") == 40


def test_benign_drop_flood_never_evicts_a_judged_terminal(tmp_path):
    c = _c(tmp_path)
    for i in range(5):
        c.record(dc.DeliveryState.CONFIRMED, f"ok{i}", protocol="rns", source="meshcore")
    for i in range(400):
        c.record(dc.DeliveryState.DROPPED, f"d{i}", protocol="meshcore", source="rns",
                 drop_reason=dc.DropReason.DEDUP)
    assert [e["id"] for e in c.snapshot()["recent_terminal"]] == [f"ok{i}" for i in range(5)]


def test_judged_pool_bounds_itself(tmp_path):
    c = _c(tmp_path)
    for i in range(100):
        c.record(dc.DeliveryState.CONFIRMED, f"ok{i}", protocol="rns", source="meshcore")
    ids = [e["id"] for e in c.snapshot()["recent_terminal"]]
    assert len(ids) == 40 and "ok99" in ids and "ok0" not in ids


def test_a_refusal_in_any_state_lives_only_in_the_refusal_pool(tmp_path):
    c = _c(tmp_path, cap=5)
    c.record(dc.DeliveryState.QUEUED, "ref", protocol="meshtastic", source="rns",
             drop_reason=dc.DropReason.INGRESS_REFUSED)
    for i in range(6):
        c.record(dc.DeliveryState.QUEUED, f"q{i}", protocol="meshtastic", source="rns")
    assert _count(tmp_path, "id = 'ref'") == 1


def test_python_and_sql_judged_predicate_agree_on_every_input():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE events (state TEXT, drop_reason TEXT)")
    combos = [(st.value, dr) for st in dc.DeliveryState
              for dr in [None, ""] + [r.value for r in dc.DropReason]]
    combos += [("weird", None), ("weird", "rns_delivery_failed")]
    conn.executemany("INSERT INTO events VALUES (?, ?)", combos)
    for st, dr in combos:
        w = "state IS ? AND drop_reason IS ?"
        judged = conn.execute(f"SELECT COUNT(*) FROM events WHERE {w} AND {dc.JUDGED_TERMINAL_SQL}",
                              (st, dr) + dc.JUDGED_TERMINAL_ARGS).fetchone()[0]
        other = conn.execute(f"SELECT COUNT(*) FROM events WHERE {w} AND {dc._OTHER_POOL_SQL}",
                             (st, dr) + dc._OTHER_POOL_ARGS).fetchone()[0]
        refused = conn.execute(f"SELECT COUNT(*) FROM events WHERE {w} AND "
                               "COALESCE(drop_reason, '') = 'ingress_refused'",
                               (st, dr)).fetchone()[0]
        assert judged == int(dc.is_judged_terminal(st, dr)), (st, dr)
        assert judged + other + refused == 1, (st, dr, judged, other, refused)
