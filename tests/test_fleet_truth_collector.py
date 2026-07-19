"""Tests for MeshAnchor's fleet-truth collector shim (fleet_truth_collector).

The shared builder is tested in test_fleet_truth.py; here we pin the
DOMAIN-SPECIFIC collector layer: peers come from fleet.json
(monitoring.fleet_config), an unreachable box becomes a DARK snapshot (never
dropped), the snapshot carries the peer's display NAME + a resolution label
but NEVER the configured host (which may be a raw LAN IP — MF014/MF015), the
TTL cache single-flights, and a build blow-up yields a self-describing dark
document rather than a 500.

Run: python3 -m pytest tests/test_fleet_truth_collector.py -v
"""
import sys
from pathlib import Path
from unittest.mock import patch

_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from utils import fleet_truth_collector as c  # noqa: E402


class TestFetchBox:
    def test_both_none_is_error_dark_snapshot(self):
        with patch.object(c, "_http_get_json", return_value=None):
            snap = c._fetch_box("peer-x", "http://198.51.100.7:5001", "config")
        assert snap["status"] is None and snap["slo"] is None
        assert snap["error"] and "peer-x" in snap["error"]
        assert snap["answered_at"] is None
        assert snap["resolution_method"] == "config"

    def test_answered_snapshot_carries_data(self):
        with patch.object(c, "_http_get_json",
                          side_effect=[{"overall_status": "ready"}, {"app": {}}]):
            snap = c._fetch_box("peer-y", "http://peer-y:5001", "config")
        assert snap["slo"] == {"overall_status": "ready"}
        assert snap["status"] == {"app": {}}
        assert snap["answered_at"] is not None

    def test_snapshot_never_carries_the_host(self):
        """MF014/MF015: fleet.json hosts may be raw LAN IPs — the snapshot
        (and therefore the truth schema) must never contain the base_url."""
        with patch.object(c, "_http_get_json", return_value=None):
            snap = c._fetch_box("peer-x", "http://198.51.100.7:5001", "config")
        flat = repr(snap)
        assert "198.51.100.7" not in flat
        assert "base_url" not in snap


class TestCollectSnapshots:
    def test_peers_come_from_fleet_config(self):
        class FakePeer:
            def __init__(self, name):
                self.name = name
            def base_url(self):
                return f"http://{self.name}:5001"

        class FakeCfg:
            def non_self_peers(self, *, hostname=None):
                return [FakePeer("noc-b"), FakePeer("noc-c")]

        with patch.object(c, "_http_get_json", return_value=None), \
             patch("monitoring.fleet_config.load_fleet_config",
                   return_value=FakeCfg()):
            snaps, declared = c.collect_snapshots(self_port=5001)
        assert declared == 3  # self + 2 peers
        aliases = {s["alias"] for s in snaps}
        assert {"noc-b", "noc-c"} <= aliases
        methods = {s["alias"]: s["resolution_method"] for s in snaps}
        assert methods["noc-b"] == "config"
        # self row present with the self label
        self_rows = [s for s in snaps if s["resolution_method"] == "self"]
        assert len(self_rows) == 1


class TestIpNameMasking:
    def test_ip_shaped_peer_name_never_becomes_the_alias(self):
        """2026-07-19 adversarial review (MF014/MF015): an IP-shaped NAME in
        fleet.json must not surface as the box alias."""
        import re

        class FakePeer:
            name = "203.0.113.9"
            def base_url(self):
                return "http://203.0.113.9:5001"

        class FakeCfg:
            def non_self_peers(self, *, hostname=None):
                return [FakePeer()]

        with patch.object(c, "_http_get_json", return_value=None), \
             patch("monitoring.fleet_config.load_fleet_config",
                   return_value=FakeCfg()):
            snaps, _ = c.collect_snapshots(self_port=5001)
        peer_rows = [s for s in snaps if s["resolution_method"] == "config"]
        assert len(peer_rows) == 1
        assert peer_rows[0]["alias"].startswith("ip-entry-")
        assert not re.search(r"\d+\.\d+\.\d+\.\d+", repr(peer_rows[0]))


class TestCache:
    def setup_method(self):
        c._cache["truth"] = None
        c._cache["built_at"] = 0.0

    def test_ttl_single_flight(self):
        calls = {"n": 0}

        def fake_collect(*, self_port):
            calls["n"] += 1
            return ([], 0)

        with patch.object(c, "collect_snapshots", side_effect=fake_collect):
            t1 = c.get_fleet_truth(self_port=5001, force=True)   # builds
            t2 = c.get_fleet_truth(self_port=5001)               # cached
        assert calls["n"] == 1
        assert t1 is t2

    def test_force_rebuilds(self):
        calls = {"n": 0}

        def fake_collect(*, self_port):
            calls["n"] += 1
            return ([], 0)

        with patch.object(c, "collect_snapshots", side_effect=fake_collect):
            c.get_fleet_truth(self_port=5001, force=True)
            c.get_fleet_truth(self_port=5001, force=True)
        assert calls["n"] == 2

    def test_build_error_yields_dark_doc_not_raise(self):
        with patch.object(c, "collect_snapshots", side_effect=RuntimeError("boom")):
            t = c.get_fleet_truth(self_port=5001, force=True)
        assert t["fleet_state"] == "dark"
        assert t["fanout"]["stale"] is True
        assert "boom" in t["fanout"].get("error", "")
        assert t["schema"] == "fleet_truth/v1"

    def test_signal_classes_are_blackout_kinds_and_blockless_status_reads_dark(self):
        """MA's closed enum = its blackout kinds (2026-07-19 enrichment). A
        box whose status carries NO watchdog block gets every kind dark —
        never inferred green."""
        from monitoring.fleet_watchdog import ALL_KINDS

        def fake_collect(*, self_port):
            return ([{"alias": "self-box", "resolution_method": "self",
                      "status": {"app": {}}, "slo": {"overall_status": "ready"},
                      "error": None, "answered_at": 1.0}], 1)

        with patch.object(c, "collect_snapshots", side_effect=fake_collect):
            t = c.get_fleet_truth(self_port=5001, force=True)
        cov = t["boxes"][0]["coverage"]
        assert cov["total"] == len(ALL_KINDS)
        assert cov["green"] == 0
        assert cov["dark"] == len(ALL_KINDS)
