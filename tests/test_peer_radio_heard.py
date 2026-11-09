"""MAP-H H14 peer half (port of MeshForge 9672ca99 + follow-ups, 2026-11-09).

A MeshForge manager federates this box's ``/api/nodes/directory``. A row's
``last_seen`` is the directory WRITE stamp, never the time a radio heard the
node, so the directory now also exports ``radio_heard``: THIS box's radio's
last-heard epoch, from the radio nodedb drain only. A row with none says
``None`` — the KEY is what tells the manager this peer exports radio times
(``radio_exported``); its absence reads "old code".

Only the peer half lives here: MeshAnchor runs no federation manager. The
manager-side trust (``peer_radio_trust`` / ``peer_heard_verdict``) stays in
MeshForge. MeshCore rows stay NULL by design — witnessing a MeshCore radio is
its own design item, not a port.
"""

import time as _time
from unittest.mock import patch

import pytest

from utils.map_data_collector import MapDataCollector
from utils.node_history import NodeHistoryDB
from utils.peer_radio_heard import coerce_radio_ts

NOW = 1_794_175_000.0


class TestCoerceRadioTs:
    @pytest.mark.parametrize("bad", [None, 0, -5, "", "x", float("nan"),
                                     float("inf"), True, [1], {"a": 1}])
    def test_not_a_hearing_time(self, bad):
        assert coerce_radio_ts(bad, NOW) is None

    def test_future_beyond_skew_is_not_a_hearing_time(self):
        assert coerce_radio_ts(NOW + 301, NOW) is None

    def test_small_skew_kept(self):
        assert coerce_radio_ts(NOW + 10, NOW) == NOW + 10

    def test_numeric_string_accepted(self):
        assert coerce_radio_ts(str(NOW - 60), NOW) == NOW - 60

    def test_nonfinite_now_is_none(self):
        assert coerce_radio_ts(NOW, float("nan")) is None


# ── node_history directory carries radio_heard ─────────────────────────────

def _radio_feat(nid, last_heard, origin="local_radio", stamp=True,
                network="meshtastic", **extra):
    props = {"id": nid, "name": nid, "network": network,
             "source_origin": origin, "last_heard": last_heard}
    if stamp:   # as the radio drain stamps it
        props["radio_last_heard"] = last_heard
    props.update(extra)
    return {"type": "Feature",
            "geometry": {"type": "Point", "coordinates": [-155.0, 19.5]},
            "properties": props}


def _dir_row(hist, nid):
    feats, pl = hist.get_directory_snapshot(include_position_less=True)
    for p in [f["properties"] for f in feats] + pl:
        if p["id"] == nid:
            return p
    raise AssertionError(f"{nid} not in directory")


@pytest.fixture
def hist(tmp_path):
    return NodeHistoryDB(db_path=tmp_path / "nh.db", retention_seconds=86400)


class TestDirectoryRadioHeard:
    def test_local_radio_row_exports_its_radio_heard(self, hist):
        heard = _time.time() - 420
        hist.record_observations([_radio_feat("!ebfa1b11", heard)])
        row = _dir_row(hist, "!ebfa1b11")
        assert row["radio_heard"] == pytest.approx(heard, abs=1.0)
        # last_seen is still the write stamp — two different quantities
        assert row["last_seen"] > heard + 60

    def test_key_present_even_when_none(self, hist):
        """The manager reads the KEY as 'this peer exports radio times'."""
        hist.record_observations([_radio_feat("!nostamp", None, stamp=False)])
        row = _dir_row(hist, "!nostamp")
        assert "radio_heard" in row and row["radio_heard"] is None

    def test_meshcore_row_stays_null(self, hist):
        """No MeshCore stamp exists: witnessing that radio is a design item."""
        hist.record_observations([_radio_feat(
            "mc1", _time.time() - 5, stamp=False, network="meshcore")])
        assert _dir_row(hist, "mc1")["radio_heard"] is None

    def test_federated_copy_never_writes_radio_heard(self, hist):
        hist.record_observations([_radio_feat(
            "!relay", _time.time() - 5, federated=True, federated_from="moc2")])
        assert _dir_row(hist, "!relay")["radio_heard"] is None

    @pytest.mark.parametrize("origin", ["mqtt_local", "node_tracker",
                                        "meshcore_public", "rns_path_table"])
    def test_non_radio_origin_never_writes_radio_heard(self, hist, origin):
        hist.record_observations([_radio_feat("!x", _time.time() - 5, origin=origin)])
        assert _dir_row(hist, "!x")["radio_heard"] is None

    @pytest.mark.parametrize("bad", [None, 0, -1, "junk", float("nan")])
    def test_bad_last_heard_is_null(self, hist, bad):
        hist.record_observations([_radio_feat("!b", bad)])
        assert _dir_row(hist, "!b")["radio_heard"] is None

    def test_future_last_heard_is_null(self, hist):
        hist.record_observations([_radio_feat("!f", _time.time() + 86400)])
        assert _dir_row(hist, "!f")["radio_heard"] is None

    def test_small_future_skew_is_null_too(self, hist):
        """coerce_radio_ts keeps <= 300 s of skew for the manager's reading;
        the directory still refuses to STORE a hearing time after now (a
        mutant dropping that guard survived the +1 d test, 2026-11-09)."""
        hist.record_observations([_radio_feat("!s", _time.time() + 120)])
        assert _dir_row(hist, "!s")["radio_heard"] is None

    def test_mqtt_downlinked_node_is_not_this_radios_hearing(self, hist):
        hist.record_observations([_radio_feat("!mq", _time.time() - 5, via_mqtt=True)])
        assert _dir_row(hist, "!mq")["radio_heard"] is None

    def test_directory_rejects_a_stamped_own_row(self, hist):
        hist.record_observations([_radio_feat("!self", _time.time() - 5, is_self=True)])
        assert _dir_row(hist, "!self")["radio_heard"] is None

    def test_tracker_or_cache_feature_never_stamps(self, hist):
        hist.record_observations([_radio_feat("!trk", _time.time() - 5, stamp=False)])
        assert _dir_row(hist, "!trk")["radio_heard"] is None

    def test_monotonic_and_null_never_erases(self, hist):
        now = _time.time()
        hist.record_observations([_radio_feat("!m", now - 100)])
        hist.record_observations([_radio_feat("!m", now - 900)])   # older
        assert _dir_row(hist, "!m")["radio_heard"] == pytest.approx(now - 100, abs=1.0)
        hist.record_observations([_radio_feat("!m", None)])        # no stamp
        assert _dir_row(hist, "!m")["radio_heard"] == pytest.approx(now - 100, abs=1.0)
        hist.record_observations([_radio_feat("!m", now - 10)])    # newer
        assert _dir_row(hist, "!m")["radio_heard"] == pytest.approx(now - 10, abs=1.0)

    def test_pre_h14_db_migrates_and_keeps_rows(self, tmp_path):
        import sqlite3
        db = tmp_path / "old.db"
        h = NodeHistoryDB(db_path=db, retention_seconds=86400)
        h.record_observations([_radio_feat("!old", 1.0)])
        con = sqlite3.connect(db)
        con.execute("ALTER TABLE nodes DROP COLUMN radio_heard")
        con.commit()
        con.close()
        h2 = NodeHistoryDB(db_path=db, retention_seconds=86400)
        assert _dir_row(h2, "!old")["radio_heard"] is None


# ── the radio drain is the ONLY stamper ────────────────────────────────────

class _FakeIface:
    def __init__(self, nodes, my_num=None, local_num=None):
        self.nodes = nodes
        if my_num is not None:
            self.myInfo = type("MI", (), {"my_node_num": my_num})()
        if local_num is not None:
            self.localNode = type("LN", (), {"nodeNum": local_num})()


def _nodedb(num, age, via_mqtt=False, pos=True):
    d = {"num": num, "lastHeard": _time.time() - age, "viaMqtt": via_mqtt,
         "user": {"id": "!%08x" % num, "longName": "n%x" % num}}
    if pos:
        d["position"] = {"latitude": 19.5, "longitude": -155.0}
    return d


def _collector(tmp_path, history=True):
    return MapDataCollector(cache_dir=tmp_path / "cache", enable_history=history,
                            meshforge_maps_enabled=False)


_SOURCES = ("_collect_aredn", "_collect_direct_radio", "_collect_meshcore",
            "_collect_meshcore_public", "_collect_meshforge_maps",
            "_collect_meshtasticd", "_collect_mqtt", "_collect_node_tracker",
            "_collect_rns_direct", "_collect_unified_tracker")


def _collect_with(c, **by_source):
    """Run collect() with every source stubbed; ``by_source`` maps a method
    name to a callable(self) -> list of features."""
    with patch.object(MapDataCollector, "_collect_meshcore_self", lambda self: None), \
         patch.object(MapDataCollector, "_meshtasticd_present", lambda self: True):
        patches = [patch.object(MapDataCollector, name,
                                by_source.get(name, lambda self: []))
                   for name in _SOURCES]
        for p in patches:
            p.start()
        try:
            return c.collect(max_age_seconds=0)
        finally:
            for p in patches:
                p.stop()


class TestRadioDrainStamp:
    def _drain(self, collector, iface):
        feats, nopos, _ = collector._drain_interface_nodes(iface, "test")
        by = {f["properties"]["id"]: f["properties"] for f in feats}
        by.update({e["id"]: e for e in nopos})
        return by

    def test_radio_stamp_is_set_only_by_the_radio_drain(self, tmp_path):
        by = self._drain(_collector(tmp_path, False), _FakeIface({
            "!0000aaa1": _nodedb(0xaaa1, 300),
            "!0000aaa2": _nodedb(0xaaa2, 400, pos=False),
            "!0000aaa3": _nodedb(0xaaa3, 50, via_mqtt=True)}, my_num=0xbeef))
        assert by["!0000aaa1"]["radio_last_heard"] == pytest.approx(_time.time() - 300, abs=2)
        assert by["!0000aaa2"]["radio_last_heard"] == pytest.approx(_time.time() - 400, abs=2)
        assert by["!0000aaa3"].get("radio_last_heard") is None   # MQTT-only

    @pytest.mark.parametrize("kw", [{"my_num": 0xddfb8065},
                                    {"local_num": 0xddfb8065}])
    def test_own_node_is_tagged_from_the_connection_never_stamped(self, tmp_path, kw):
        by = self._drain(_collector(tmp_path, False), _FakeIface({
            "!ddfb8065": _nodedb(0xddfb8065, 1),
            "!0000aaa1": _nodedb(0xaaa1, 300)}, **kw))
        assert by["!ddfb8065"]["is_self"] is True
        assert by["!ddfb8065"].get("radio_last_heard") is None
        assert by["!0000aaa1"].get("is_self") is not True

    def test_unknown_own_id_is_not_inferred(self, tmp_path):
        by = self._drain(_collector(tmp_path, False),
                         _FakeIface({"!ddfb8065": _nodedb(0xddfb8065, 1)}))
        assert "is_self" not in by["!ddfb8065"]

    def test_http_no_position_entry_carries_via_mqtt(self):
        import os
        src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                   "src", "utils", "_map_collector_meshtastic.py"),
                   encoding="utf-8").read()
        block = src[src.index("no_position_nodes.append({"):][:700]
        assert '"via_mqtt": node.via_mqtt' in block


class TestEndToEndDirectory:
    def test_gps_less_node_reaches_the_directory_with_radio_heard(self, tmp_path):
        """Most Meshtastic nodes have no GPS: parser dict -> collect's
        history feature -> radio_heard."""
        c = _collector(tmp_path)
        now = _time.time()
        _, nopos, _ = c._drain_interface_nodes(_FakeIface({"!c0ffee01": {
            "num": 0xC0FFEE01, "lastHeard": now - 300,
            "user": {"id": "!c0ffee01", "longName": "nogps"}}}, my_num=1), "t")
        (entry,) = nopos
        assert entry["radio_last_heard"] == now - 300

        def radio(self):
            self._nodes_without_position.append(dict(entry))
            return []

        _collect_with(c, _collect_meshtasticd=radio)
        row = _dir_row(c._history, "!c0ffee01")
        assert row["radio_heard"] == pytest.approx(now - 300, abs=1.0)
        # Review F1 (2026-11-09): the REAL drain's entry must land under the
        # network the manager judges it on, never "unknown".
        assert row["network"] == "meshtastic"

    def test_http_gps_less_entry_is_meshtastic_network(self):
        import os
        src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                   "src", "utils", "_map_collector_meshtastic.py"),
                   encoding="utf-8").read()
        block = src[src.index("no_position_nodes.append({"):][:700]
        assert '"network": "meshtastic"' in block

    def test_replayed_cache_feature_writes_no_radio_heard(self, tmp_path):
        """Review F4: a cached feature keeps its first source_origin tag
        (local_radio) and its old stamp; a replay is not this cycle's radio."""
        c = _collector(tmp_path)
        cached = _radio_feat("!0000cace", _time.time() - 600)
        with patch.object(MapDataCollector, "_load_cache", lambda self: [cached]):
            geo = _collect_with(c)
        assert any(f["properties"]["id"] == "!0000cace" for f in geo["features"]), \
            "precondition: the cache replay ran"
        assert _dir_row(c._history, "!0000cace")["radio_heard"] is None

    def test_relayed_meshforge_maps_feature_writes_no_radio_heard(self, tmp_path):
        """Review F3: an aggregate relays another box's local_radio + stamp."""
        c = MapDataCollector(cache_dir=tmp_path / "cache", enable_history=True,
                             meshforge_maps_enabled=True)
        relayed = _radio_feat("!0000feed", _time.time() - 60)
        geo = _collect_with(c, _collect_meshforge_maps=lambda self: [relayed])
        assert any(f["properties"]["id"] == "!0000feed" for f in geo["features"]), \
            "precondition: the meshforge-maps feature was admitted"
        assert _dir_row(c._history, "!0000feed")["radio_heard"] is None

    def test_fresher_mqtt_copy_never_moves_the_radio_time(self, tmp_path):
        """Radio heard it 3 d ago, MQTT 60 s ago -> radio_heard stays 3 d."""
        c = _collector(tmp_path)
        radio, _, _ = c._drain_interface_nodes(
            _FakeIface({"!0000abcd": _nodedb(0xabcd, 3 * 86400)}, my_num=1), "t")
        mqtt = [{"type": "Feature",
                 "geometry": {"type": "Point", "coordinates": [-155.0, 19.5]},
                 "properties": {"id": "!0000abcd", "network": "meshtastic",
                                "via_mqtt": True, "last_heard": _time.time() - 60}}]
        _collect_with(c, _collect_meshtasticd=lambda self: radio,
                      _collect_mqtt=lambda self: mqtt)
        row = _dir_row(c._history, "!0000abcd")
        assert row["radio_heard"] == pytest.approx(_time.time() - 3 * 86400, abs=5)

    @pytest.mark.parametrize("extra", [{}, {"via_mqtt": True}, {"is_self": True}])
    def test_stampless_or_flagged_gps_less_entry_writes_nothing(self, tmp_path, extra):
        c = _collector(tmp_path)
        entry = {"id": "!0000e1e1", "name": "x", "network": "meshtastic",
                 "last_heard": _time.time() - 30, **extra}
        if extra:
            entry["radio_last_heard"] = _time.time() - 30

        def radio(self):
            self._nodes_without_position.append(dict(entry))
            return []

        _collect_with(c, _collect_meshtasticd=radio)
        assert _dir_row(c._history, "!0000e1e1")["radio_heard"] is None
