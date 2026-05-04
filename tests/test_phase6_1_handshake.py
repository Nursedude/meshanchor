"""Phase 6.1 — meshforge-maps bidirectional data handshake.

meshforge-maps' `/api/nodes/geojson` aggregates nodes from its own
collectors (Meshtastic / MeshCore / RNS / MQTT / AREDN / HamClock).
Phase 6.1 lets MeshAnchor pull that aggregate as a low-priority
`external_maps` source so anything meshforge-maps sees but MeshAnchor's
local collectors haven't, fills the gap. Endpoint config is the same
SettingsManager-backed `meshforge_maps` config Phase 6.3 introduced —
so a non-localhost meshforge-maps deployment works without code change.

Three layers under test:

1. `MeshforgeMapsClient.fetch_nodes()` — single-shot fetch that returns
   the parsed FeatureCollection or None on any failure (unreachable,
   non-200, non-JSON, wrong shape).
2. `MapDataCollector._collect_meshforge_maps()` — gated on the
   `meshforge_maps_enabled` constructor flag, calls the client built
   from `load_maps_config()`, returns features list (empty on any
   failure — never raises).
3. `MapDataCollector._collect_locked` integration — Source 6 wiring,
   dedup priority (local collectors win), source summary inclusion.
"""

import json
import os
import sys
import urllib.error
from unittest.mock import MagicMock, patch

import pytest

SRC_DIR = os.path.join(os.path.dirname(__file__), "..", "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from utils.meshforge_maps_client import MeshforgeMapsClient


def _fake_response(payload: dict, status: int = 200) -> MagicMock:
    body = json.dumps(payload).encode("utf-8")
    cm = MagicMock()
    resp = MagicMock()
    resp.status = status
    resp.read.return_value = body
    cm.__enter__.return_value = resp
    cm.__exit__.return_value = False
    return cm


def _feature_collection(*ids):
    """Build a minimal valid FeatureCollection with one feature per id."""
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [-122.0, 37.0]},
                "properties": {"id": fid, "name": f"Node {fid}", "network": "meshcore"},
            }
            for fid in ids
        ],
        "properties": {"total_nodes": len(ids), "sources": {"meshcore": len(ids)}},
    }


# ─────────────────────────────────────────────────────────────────────
# 1. Discovery client — fetch_nodes()
# ─────────────────────────────────────────────────────────────────────


class TestFetchNodesHappyPath:
    def test_returns_full_feature_collection(self):
        client = MeshforgeMapsClient()
        with patch(
            "utils.meshforge_maps_client.urllib.request.urlopen",
            return_value=_fake_response(_feature_collection("a", "b", "c")),
        ):
            payload = client.fetch_nodes()
        assert payload is not None
        assert payload["type"] == "FeatureCollection"
        assert len(payload["features"]) == 3
        assert {f["properties"]["id"] for f in payload["features"]} == {"a", "b", "c"}

    def test_empty_features_is_valid(self):
        """Aggregator may return empty features while warming up
        (`properties.collecting=true`). That's a valid response."""
        client = MeshforgeMapsClient()
        empty = {
            "type": "FeatureCollection",
            "features": [],
            "properties": {"collecting": True, "total_nodes": 0},
        }
        with patch(
            "utils.meshforge_maps_client.urllib.request.urlopen",
            return_value=_fake_response(empty),
        ):
            payload = client.fetch_nodes()
        assert payload is not None
        assert payload["features"] == []

    def test_uses_configured_endpoint(self):
        client = MeshforgeMapsClient(host="maps.lan", port=9090, timeout=5.0)
        captured = {}

        def fake_urlopen(url, timeout=None):
            captured["url"] = url
            captured["timeout"] = timeout
            return _fake_response(_feature_collection("x"))

        with patch(
            "utils.meshforge_maps_client.urllib.request.urlopen",
            side_effect=fake_urlopen,
        ):
            client.fetch_nodes()
        assert captured["url"] == "http://maps.lan:9090/api/nodes/geojson"
        assert captured["timeout"] == 5.0


class TestFetchNodesFailureModes:
    def test_url_error_returns_none(self):
        client = MeshforgeMapsClient()
        with patch(
            "utils.meshforge_maps_client.urllib.request.urlopen",
            side_effect=urllib.error.URLError("connection refused"),
        ):
            assert client.fetch_nodes() is None

    def test_timeout_returns_none(self):
        client = MeshforgeMapsClient()
        with patch(
            "utils.meshforge_maps_client.urllib.request.urlopen",
            side_effect=TimeoutError("slow"),
        ):
            assert client.fetch_nodes() is None

    def test_oserror_returns_none(self):
        client = MeshforgeMapsClient()
        with patch(
            "utils.meshforge_maps_client.urllib.request.urlopen",
            side_effect=OSError("Network is unreachable"),
        ):
            assert client.fetch_nodes() is None

    def test_non_200_returns_none(self):
        client = MeshforgeMapsClient()
        with patch(
            "utils.meshforge_maps_client.urllib.request.urlopen",
            return_value=_fake_response({}, status=503),
        ):
            assert client.fetch_nodes() is None

    def test_non_json_returns_none(self):
        client = MeshforgeMapsClient()

        def fake_urlopen(url, timeout=None):
            cm = MagicMock()
            resp = MagicMock()
            resp.status = 200
            resp.read.return_value = b"<html>not json</html>"
            cm.__enter__.return_value = resp
            cm.__exit__.return_value = False
            return cm

        with patch(
            "utils.meshforge_maps_client.urllib.request.urlopen",
            side_effect=fake_urlopen,
        ):
            assert client.fetch_nodes() is None


class TestFetchNodesShapeValidation:
    """Don't trust upstream blindly — a payload that's JSON but the wrong
    shape (e.g. an error envelope) shouldn't crash MapDataCollector."""

    def test_non_dict_returns_none(self):
        client = MeshforgeMapsClient()
        with patch(
            "utils.meshforge_maps_client.urllib.request.urlopen",
            return_value=_fake_response(["not", "a", "dict"]),
        ):
            assert client.fetch_nodes() is None

    def test_wrong_type_field_returns_none(self):
        """`/api/nodes/geojson` must return type=FeatureCollection."""
        client = MeshforgeMapsClient()
        bad = {"type": "Feature", "features": []}
        with patch(
            "utils.meshforge_maps_client.urllib.request.urlopen",
            return_value=_fake_response(bad),
        ):
            assert client.fetch_nodes() is None

    def test_missing_features_returns_none(self):
        client = MeshforgeMapsClient()
        with patch(
            "utils.meshforge_maps_client.urllib.request.urlopen",
            return_value=_fake_response({"type": "FeatureCollection"}),
        ):
            assert client.fetch_nodes() is None

    def test_features_not_a_list_returns_none(self):
        client = MeshforgeMapsClient()
        bad = {"type": "FeatureCollection", "features": "not a list"}
        with patch(
            "utils.meshforge_maps_client.urllib.request.urlopen",
            return_value=_fake_response(bad),
        ):
            assert client.fetch_nodes() is None


# ─────────────────────────────────────────────────────────────────────
# 2. MapDataCollector._collect_meshforge_maps()
# ─────────────────────────────────────────────────────────────────────


class TestCollectMeshforgeMaps:
    def _make_collector(self, **kwargs):
        from utils.map_data_collector import MapDataCollector
        # enable_history=False avoids touching ~/.local/share/meshanchor/
        # node_history.db on the dev box during a unit test.
        return MapDataCollector(enable_history=False, **kwargs)

    def test_returns_features_when_client_succeeds(self, tmp_path, monkeypatch):
        monkeypatch.setattr("utils.common.CONFIG_DIR", tmp_path)
        c = self._make_collector(cache_dir=tmp_path)

        fake_payload = _feature_collection("alpha", "bravo")
        with patch(
            "utils.meshforge_maps_client.urllib.request.urlopen",
            return_value=_fake_response(fake_payload),
        ):
            features = c._collect_meshforge_maps()
        assert len(features) == 2
        assert {f["properties"]["id"] for f in features} == {"alpha", "bravo"}

    def test_returns_empty_when_unreachable(self, tmp_path, monkeypatch):
        monkeypatch.setattr("utils.common.CONFIG_DIR", tmp_path)
        c = self._make_collector(cache_dir=tmp_path)

        with patch(
            "utils.meshforge_maps_client.urllib.request.urlopen",
            side_effect=urllib.error.URLError("nope"),
        ):
            features = c._collect_meshforge_maps()
        assert features == []

    def test_returns_empty_when_payload_malformed(self, tmp_path, monkeypatch):
        monkeypatch.setattr("utils.common.CONFIG_DIR", tmp_path)
        c = self._make_collector(cache_dir=tmp_path)

        bad = {"type": "FeatureCollection", "features": "not a list"}
        with patch(
            "utils.meshforge_maps_client.urllib.request.urlopen",
            return_value=_fake_response(bad),
        ):
            features = c._collect_meshforge_maps()
        assert features == []

    def test_filters_non_dict_features(self, tmp_path, monkeypatch):
        """Defensive: even if upstream returns a list with junk entries,
        we only keep dict-shaped features so the dedup loop downstream
        doesn't crash on .get('properties')."""
        monkeypatch.setattr("utils.common.CONFIG_DIR", tmp_path)
        c = self._make_collector(cache_dir=tmp_path)

        mixed = {
            "type": "FeatureCollection",
            "features": [
                {"type": "Feature", "geometry": {}, "properties": {"id": "ok"}},
                "not a feature",
                None,
                42,
            ],
        }
        with patch(
            "utils.meshforge_maps_client.urllib.request.urlopen",
            return_value=_fake_response(mixed),
        ):
            features = c._collect_meshforge_maps()
        assert len(features) == 1
        assert features[0]["properties"]["id"] == "ok"

    def test_uses_configured_endpoint_via_phase6_3(self, tmp_path, monkeypatch):
        """The collector must respect the Phase 6.3 endpoint config.
        Save a non-localhost host, then assert fetch_nodes hits it."""
        monkeypatch.setattr("utils.common.CONFIG_DIR", tmp_path)

        from utils.meshforge_maps_config import save_maps_config
        save_maps_config(host="far.lan", port=9090, timeout=4.0)

        c = self._make_collector(cache_dir=tmp_path)

        captured = {}

        def fake_urlopen(url, timeout=None):
            captured["url"] = url
            return _fake_response(_feature_collection("z"))

        with patch(
            "utils.meshforge_maps_client.urllib.request.urlopen",
            side_effect=fake_urlopen,
        ):
            c._collect_meshforge_maps()
        assert captured["url"] == "http://far.lan:9090/api/nodes/geojson"


# ─────────────────────────────────────────────────────────────────────
# 3. Constructor flag — gating + default
# ─────────────────────────────────────────────────────────────────────


class TestMeshforgeMapsEnabledFlag:
    def _make_collector(self, **kwargs):
        from utils.map_data_collector import MapDataCollector
        return MapDataCollector(enable_history=False, **kwargs)

    def test_default_is_enabled(self, tmp_path):
        c = self._make_collector(cache_dir=tmp_path)
        assert c._meshforge_maps_enabled is True

    def test_can_be_disabled(self, tmp_path):
        c = self._make_collector(cache_dir=tmp_path, meshforge_maps_enabled=False)
        assert c._meshforge_maps_enabled is False

    def test_disabled_skips_collect_call_in_collect_locked(
        self, tmp_path, monkeypatch
    ):
        """When the flag is False, _collect_locked must NOT call
        _collect_meshforge_maps (no wasted HTTP attempt)."""
        monkeypatch.setattr("utils.common.CONFIG_DIR", tmp_path)
        c = self._make_collector(
            cache_dir=tmp_path, meshforge_maps_enabled=False,
        )

        called = {"n": 0}
        original = c._collect_meshforge_maps

        def spy():
            called["n"] += 1
            return original()

        c._collect_meshforge_maps = spy

        # Stub every other source so _collect_locked completes quickly.
        with patch.object(c, "_collect_meshcore", return_value=[]), \
             patch.object(c, "_collect_unified_tracker", return_value=[]), \
             patch.object(c, "_collect_meshtasticd", return_value=[]), \
             patch.object(c, "_collect_direct_radio", return_value=[]), \
             patch.object(c, "_collect_mqtt", return_value=[]), \
             patch.object(c, "_collect_node_tracker", return_value=[]), \
             patch.object(c, "_collect_aredn", return_value=[]), \
             patch.object(c, "_collect_rns_direct", return_value=[]):
            c._collect_locked()

        assert called["n"] == 0

    def test_enabled_calls_collect_in_collect_locked(self, tmp_path, monkeypatch):
        """Symmetric: when flag is True, the source is consulted."""
        monkeypatch.setattr("utils.common.CONFIG_DIR", tmp_path)
        c = self._make_collector(
            cache_dir=tmp_path, meshforge_maps_enabled=True,
        )

        with patch.object(c, "_collect_meshcore", return_value=[]), \
             patch.object(c, "_collect_unified_tracker", return_value=[]), \
             patch.object(c, "_collect_meshtasticd", return_value=[]), \
             patch.object(c, "_collect_direct_radio", return_value=[]), \
             patch.object(c, "_collect_mqtt", return_value=[]), \
             patch.object(c, "_collect_node_tracker", return_value=[]), \
             patch.object(c, "_collect_aredn", return_value=[]), \
             patch.object(c, "_collect_rns_direct", return_value=[]), \
             patch.object(c, "_collect_meshforge_maps", return_value=[]) as spy:
            c._collect_locked()
        assert spy.called


# ─────────────────────────────────────────────────────────────────────
# 4. Integration — dedup priority, source tagging, summary inclusion
# ─────────────────────────────────────────────────────────────────────


class TestCollectLockedIntegration:
    def _make_collector(self, **kwargs):
        from utils.map_data_collector import MapDataCollector
        return MapDataCollector(enable_history=False, **kwargs)

    def test_meshforge_maps_features_tagged_external_maps(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setattr("utils.common.CONFIG_DIR", tmp_path)
        c = self._make_collector(cache_dir=tmp_path)

        mf_features = [
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [-122, 37]},
             "properties": {"id": "external1", "network": "meshcore"}},
        ]
        with patch.object(c, "_collect_meshcore", return_value=[]), \
             patch.object(c, "_collect_unified_tracker", return_value=[]), \
             patch.object(c, "_collect_meshtasticd", return_value=[]), \
             patch.object(c, "_collect_direct_radio", return_value=[]), \
             patch.object(c, "_collect_mqtt", return_value=[]), \
             patch.object(c, "_collect_node_tracker", return_value=[]), \
             patch.object(c, "_collect_aredn", return_value=[]), \
             patch.object(c, "_collect_rns_direct", return_value=[]), \
             patch.object(c, "_collect_meshforge_maps", return_value=mf_features):
            geojson = c._collect_locked()

        ids = [f["properties"]["id"] for f in geojson["features"]]
        assert "external1" in ids
        # Phase 6.1's tier tag — external_bulk equivalent that aggregates
        # multiple upstream radios into one feed.
        ext = [f for f in geojson["features"] if f["properties"]["id"] == "external1"][0]
        assert ext["properties"].get("source_origin") == "external_maps"

    def test_local_meshcore_wins_over_meshforge_maps_for_same_id(
        self, tmp_path, monkeypatch
    ):
        """Local collectors must win on dedup. If MeshAnchor's own MeshCore
        radio reports node X, meshforge-maps' aggregated entry for the
        same X must NOT shadow it."""
        monkeypatch.setattr("utils.common.CONFIG_DIR", tmp_path)
        c = self._make_collector(cache_dir=tmp_path)

        local = [
            {"type": "Feature",
             "geometry": {"type": "Point", "coordinates": [-100, 30]},
             "properties": {"id": "shared", "network": "meshcore", "name": "LocalName"}},
        ]
        external = [
            {"type": "Feature",
             "geometry": {"type": "Point", "coordinates": [-200, 30]},
             "properties": {"id": "shared", "network": "meshcore", "name": "ExternalName"}},
        ]

        with patch.object(c, "_collect_meshcore", return_value=local), \
             patch.object(c, "_collect_unified_tracker", return_value=[]), \
             patch.object(c, "_collect_meshtasticd", return_value=[]), \
             patch.object(c, "_collect_direct_radio", return_value=[]), \
             patch.object(c, "_collect_mqtt", return_value=[]), \
             patch.object(c, "_collect_node_tracker", return_value=[]), \
             patch.object(c, "_collect_aredn", return_value=[]), \
             patch.object(c, "_collect_rns_direct", return_value=[]), \
             patch.object(c, "_collect_meshforge_maps", return_value=external):
            geojson = c._collect_locked()

        shared = [f for f in geojson["features"] if f["properties"]["id"] == "shared"]
        assert len(shared) == 1
        # Local data wins.
        assert shared[0]["properties"]["name"] == "LocalName"
        # And the source_origin reflects the local tier, not external_maps.
        assert shared[0]["properties"].get("source_origin") != "external_maps"

    def test_source_summary_includes_meshforge_maps_count(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setattr("utils.common.CONFIG_DIR", tmp_path)
        c = self._make_collector(cache_dir=tmp_path)

        mf_features = [
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [-122, 37]},
             "properties": {"id": f"ext{i}"}}
            for i in range(3)
        ]
        with patch.object(c, "_collect_meshcore", return_value=[]), \
             patch.object(c, "_collect_unified_tracker", return_value=[]), \
             patch.object(c, "_collect_meshtasticd", return_value=[]), \
             patch.object(c, "_collect_direct_radio", return_value=[]), \
             patch.object(c, "_collect_mqtt", return_value=[]), \
             patch.object(c, "_collect_node_tracker", return_value=[]), \
             patch.object(c, "_collect_aredn", return_value=[]), \
             patch.object(c, "_collect_rns_direct", return_value=[]), \
             patch.object(c, "_collect_meshforge_maps", return_value=mf_features):
            geojson = c._collect_locked()

        sources = geojson["properties"]["sources"]
        assert sources["meshforge_maps"] == 3
        assert sources["meshforge_maps_enabled"] is True

    def test_source_summary_reports_disabled_state(self, tmp_path, monkeypatch):
        monkeypatch.setattr("utils.common.CONFIG_DIR", tmp_path)
        c = self._make_collector(
            cache_dir=tmp_path, meshforge_maps_enabled=False,
        )

        with patch.object(c, "_collect_meshcore", return_value=[]), \
             patch.object(c, "_collect_unified_tracker", return_value=[]), \
             patch.object(c, "_collect_meshtasticd", return_value=[]), \
             patch.object(c, "_collect_direct_radio", return_value=[]), \
             patch.object(c, "_collect_mqtt", return_value=[]), \
             patch.object(c, "_collect_node_tracker", return_value=[]), \
             patch.object(c, "_collect_aredn", return_value=[]), \
             patch.object(c, "_collect_rns_direct", return_value=[]):
            geojson = c._collect_locked()

        sources = geojson["properties"]["sources"]
        assert sources["meshforge_maps"] == 0
        assert sources["meshforge_maps_enabled"] is False
