"""
Tests for MapDataCollector — the source-merging engine for the :5000 map.

Phase 1 of the MeshCore-primary TUI rework adds two contracts that need
guarding against regression:

1. MeshCore is the explicit primary radio source. Its position-less nodes
   surface via the `nodes_without_position` side-panel pipeline.
2. The meshtasticd poll respects the deployment profile's `meshtastic`
   feature flag. When disabled, the collector skips meshtasticd entirely.

Run: python3 -m pytest tests/test_map_data_collector.py -v
"""

import pytest
from unittest.mock import MagicMock, patch

from src.utils.map_data_collector import MapDataCollector
from src.gateway.node_tracker import UnifiedNode
from src.gateway.node_models import Position


@pytest.fixture(autouse=True)
def _stub_external_collectors():
    """Block live network + on-disk caches during unit tests.

    PR #81 wired ``_collect_meshcore_public`` (hits map.meshcore.dev), and
    ``_collect_node_tracker`` / ``_collect_aredn`` / ``_collect_meshforge_maps``
    read from local cache files / mDNS / HTTP. Each can drop hundreds-to-thousands
    of features into a test that means to assert on a single mocked node.
    Stub them all to ``[]`` so per-test feature-count assertions stay deterministic.
    """
    base = "src.utils.map_data_collector.MapDataCollector"
    # _collect_rns_direct talks to the LIVE rnsd shared instance when one is
    # running (path-table RPC + on-disk position cache) — on a box with a real
    # rnsd (e.g. meshanchor-server) it can inject real features. Seal it like
    # the other live sources.
    with patch(f"{base}._collect_meshcore_public", return_value=[]), \
         patch(f"{base}._collect_node_tracker", return_value=[]), \
         patch(f"{base}._collect_aredn", return_value=[]), \
         patch(f"{base}._collect_meshforge_maps", return_value=[]), \
         patch(f"{base}._collect_mqtt", return_value=[]), \
         patch(f"{base}._collect_rns_direct", return_value=[]), \
         patch(f"{base}._load_cache", return_value=[]):
        yield


def _make_meshcore_node(node_id, name, position=None, is_online=True,
                       pubkey="abc123def456", role="client", hops=2):
    """Build a UnifiedNode shaped like one created by meshcore_handler._on_advertisement."""
    node = UnifiedNode(
        id=node_id,
        name=name,
        network="meshcore",
        is_online=is_online,
        meshcore_pubkey=pubkey,
        meshcore_role=role,
        meshcore_hops=hops,
        snr=-12.5,
        rssi=-95,
    )
    if position is not None:
        node.position = position
    return node


class TestMeshCoreSource:
    """MeshCore is the primary radio source in MeshAnchor."""

    @pytest.fixture(autouse=True)
    def _seal_local_box_state(self):
        """Seal the remaining box-local boundaries collect() reads.

        On meshanchor-server (real radio + live meshanchor-daemon),
        _collect_meshcore_self() fetches GET /radio and injects a synthetic
        'meshcore:<real 64-hex pubkey>' feature that is NOT part of the
        injected tracker mock — the real leak behind 'assert 2 == 1'.
        Patch _fetch_daemon_radio_state (the seam TestMeshCoreSelfFeature
        already uses) to None = daemon unreachable. Likewise
        get_position_store() reads the operator's on-disk
        meshcore_positions.json, which can promote placed/ghost features.
        Cannot live in the module autouse fixture: TestMeshCoreSelfFeature /
        TestFetchDaemonRadioState exercise those seams for real.
        """
        store = MagicMock()
        store.list.return_value = {}
        with patch.object(MapDataCollector, "_fetch_daemon_radio_state",
                          return_value=None), \
             patch('src.utils.map_data_collector.get_position_store',
                   return_value=store):
            yield

    def test_position_less_meshcore_node_surfaces_in_side_panel(self):
        """MeshCore advertisements without GPS land in nodes_without_position."""
        collector = MapDataCollector(meshtastic_enabled=False, enable_history=False, meshforge_maps_enabled=False)
        mock_tracker = MagicMock()
        mock_tracker.get_meshcore_nodes.return_value = [
            _make_meshcore_node("meshcore:abc123", "RS1"),
            _make_meshcore_node("meshcore:def456", "Portable-1"),
        ]
        mock_tracker.to_geojson.return_value = {"type": "FeatureCollection", "features": []}
        mock_tracker.get_all_nodes.return_value = []

        with patch('src.utils.map_data_collector.get_node_tracker', return_value=mock_tracker):
            result = collector.collect()

        position_less = result["properties"]["nodes_without_position"]
        ids = {entry["id"] for entry in position_less}
        assert "meshcore:abc123" in ids
        assert "meshcore:def456" in ids
        assert all(entry["network"] == "meshcore" for entry in position_less
                   if entry["id"].startswith("meshcore:"))

    def test_position_less_entry_carries_meshcore_metadata(self):
        """Side-panel entries include MeshCore-specific fields (pubkey, role, hops)."""
        collector = MapDataCollector(meshtastic_enabled=False, enable_history=False, meshforge_maps_enabled=False)
        mock_tracker = MagicMock()
        mock_tracker.get_meshcore_nodes.return_value = [
            _make_meshcore_node(
                "meshcore:abc123", "RS1",
                pubkey="abcdef123456", role="repeater", hops=3,
            ),
        ]
        mock_tracker.to_geojson.return_value = {"type": "FeatureCollection", "features": []}
        mock_tracker.get_all_nodes.return_value = []

        with patch('src.utils.map_data_collector.get_node_tracker', return_value=mock_tracker):
            result = collector.collect()

        entry = next(e for e in result["properties"]["nodes_without_position"]
                     if e["id"] == "meshcore:abc123")
        assert entry["pubkey"] == "abcdef123456"
        assert entry["role"] == "repeater"
        assert entry["hops_away"] == 3
        assert entry["snr"] == -12.5
        assert entry["rssi"] == -95

    def test_positioned_meshcore_node_becomes_geojson_feature(self):
        """When a MeshCore node has GPS, it appears as a real map feature."""
        collector = MapDataCollector(meshtastic_enabled=False, enable_history=False, meshforge_maps_enabled=False)
        position = Position(latitude=21.3069, longitude=-157.8583)
        mock_tracker = MagicMock()
        mock_tracker.get_meshcore_nodes.return_value = [
            _make_meshcore_node("meshcore:gps01", "GPS-Node", position=position),
        ]
        mock_tracker.to_geojson.return_value = {"type": "FeatureCollection", "features": []}
        mock_tracker.get_all_nodes.return_value = []

        with patch('src.utils.map_data_collector.get_node_tracker', return_value=mock_tracker):
            result = collector.collect()

        features = result["features"]
        assert len(features) == 1
        assert features[0]["properties"]["id"] == "meshcore:gps01"
        assert features[0]["properties"]["network"] == "meshcore"
        # GeoJSON coordinates are [lon, lat]
        assert features[0]["geometry"]["coordinates"] == [-157.8583, 21.3069]

    def test_meshcore_count_in_source_summary(self):
        """The sources summary reports meshcore count distinct from other sources."""
        collector = MapDataCollector(meshtastic_enabled=False, enable_history=False, meshforge_maps_enabled=False)
        position = Position(latitude=21.3, longitude=-157.8)
        mock_tracker = MagicMock()
        mock_tracker.get_meshcore_nodes.return_value = [
            _make_meshcore_node("meshcore:a", "A", position=position),
        ]
        mock_tracker.to_geojson.return_value = {"type": "FeatureCollection", "features": []}
        mock_tracker.get_all_nodes.return_value = []

        with patch('src.utils.map_data_collector.get_node_tracker', return_value=mock_tracker):
            result = collector.collect()

        sources = result["properties"]["sources"]
        assert sources["meshcore"] == 1
        assert sources["meshtastic_enabled"] is False


class TestMeshCoreSelfFeature:
    """The local USB MeshCore radio appears as a feature when /radio has coords.

    The collector fetches radio state from the daemon's HTTP /radio endpoint
    because meshanchor-map and meshanchor-daemon run in separate processes.
    Tests stub the fetch by patching ``_fetch_daemon_radio_state``.
    """

    def _radio_state(self, **overrides):
        state = {
            "node_name": "meshanchorRAK1",
            "public_key": "ab" * 32,
            "radio_lat": 19.435,
            "radio_lon": -155.213,
            "model": "RAK 4631",
            "fw_build": "19-Apr-2026",
            "source": "radio",
            "last_refresh_ts": 1778266653.0,
        }
        state.update(overrides)
        return state

    def _empty_tracker(self):
        t = MagicMock()
        t.get_meshcore_nodes.return_value = []
        t.to_geojson.return_value = {"type": "FeatureCollection", "features": []}
        t.get_all_nodes.return_value = []
        return t

    def _patch_radio(self, state):
        """Patch ``MapDataCollector._fetch_daemon_radio_state`` to return ``state``."""
        return patch.object(MapDataCollector, "_fetch_daemon_radio_state",
                            return_value=state)

    def test_self_feature_emitted_when_radio_has_coords_and_pubkey(self):
        collector = MapDataCollector(meshtastic_enabled=False, enable_history=False, meshforge_maps_enabled=False)
        with patch('src.utils.map_data_collector.get_node_tracker', return_value=self._empty_tracker()), \
             self._patch_radio(self._radio_state()):
            result = collector.collect()

        feats = result["features"]
        assert len(feats) == 1
        props = feats[0]["properties"]
        assert props["network"] == "meshcore"
        assert props["is_local"] is True
        assert props["source"] == "meshcore_self"
        assert props["pubkey"] == "ab" * 32
        assert props["name"] == "meshanchorRAK1"
        assert props["model"] == "RAK 4631"
        assert feats[0]["geometry"]["coordinates"] == [-155.213, 19.435]

    def test_no_self_feature_when_daemon_unreachable(self):
        collector = MapDataCollector(meshtastic_enabled=False, enable_history=False, meshforge_maps_enabled=False)
        with patch('src.utils.map_data_collector.get_node_tracker', return_value=self._empty_tracker()), \
             self._patch_radio(None):
            result = collector.collect()
        assert result["features"] == []

    def test_no_self_feature_when_pubkey_missing(self):
        collector = MapDataCollector(meshtastic_enabled=False, enable_history=False, meshforge_maps_enabled=False)
        with patch('src.utils.map_data_collector.get_node_tracker', return_value=self._empty_tracker()), \
             self._patch_radio(self._radio_state(public_key=None)):
            result = collector.collect()
        assert result["features"] == []

    def test_no_self_feature_when_coords_missing(self):
        collector = MapDataCollector(meshtastic_enabled=False, enable_history=False, meshforge_maps_enabled=False)
        with patch('src.utils.map_data_collector.get_node_tracker', return_value=self._empty_tracker()), \
             self._patch_radio(self._radio_state(radio_lat=None, radio_lon=None)):
            result = collector.collect()
        assert result["features"] == []

    def test_self_feature_dedups_against_discovered_contact_with_same_pubkey(self):
        """When the local pubkey also matches a discovered contact, self wins (dict overwrite by id)."""
        collector = MapDataCollector(meshtastic_enabled=False, enable_history=False, meshforge_maps_enabled=False)
        position = Position(latitude=20.0, longitude=-156.0)
        mock_tracker = MagicMock()
        # Discovered contact with the SAME pubkey-as-id as the local radio
        mock_tracker.get_meshcore_nodes.return_value = [
            _make_meshcore_node(f"meshcore:{'ab'*32}", "From-Contacts", position=position),
        ]
        mock_tracker.to_geojson.return_value = {"type": "FeatureCollection", "features": []}
        mock_tracker.get_all_nodes.return_value = []

        with patch('src.utils.map_data_collector.get_node_tracker', return_value=mock_tracker), \
             self._patch_radio(self._radio_state()):
            result = collector.collect()

        feats = result["features"]
        assert len(feats) == 1
        # Self overwrites because _collect_meshcore_self writes after the contacts pass.
        assert feats[0]["properties"]["source"] == "meshcore_self"
        assert feats[0]["properties"]["is_local"] is True


class TestFetchDaemonRadioState:
    """The HTTP fetch helper that bridges the map → daemon process boundary."""

    def _make_collector(self):
        return MapDataCollector(meshtastic_enabled=False, enable_history=False, meshforge_maps_enabled=False)

    def _urlopen_with_body(self, body_dict):
        from io import BytesIO
        cm = MagicMock()
        cm.__enter__ = lambda self: BytesIO(__import__("json").dumps(body_dict).encode("utf-8"))
        cm.__exit__ = lambda self, *a: False
        return patch("urllib.request.urlopen", return_value=cm)

    def test_returns_radio_dict_on_200(self):
        collector = self._make_collector()
        radio = {"node_name": "x", "public_key": "ab" * 32}
        with self._urlopen_with_body({"radio": radio}):
            out = collector._fetch_daemon_radio_state()
        assert out == radio

    def test_returns_none_when_daemon_unreachable(self):
        import urllib.error
        collector = self._make_collector()
        with patch("urllib.request.urlopen",
                   side_effect=urllib.error.URLError("connection refused")):
            out = collector._fetch_daemon_radio_state()
        assert out is None

    def test_returns_none_on_unexpected_response_shape(self):
        collector = self._make_collector()
        with self._urlopen_with_body({"unexpected": "shape"}):
            out = collector._fetch_daemon_radio_state()
        assert out is None

    def test_caches_within_ttl(self):
        """Concurrent collects don't re-hit the daemon every cycle."""
        collector = self._make_collector()
        radio = {"node_name": "x", "public_key": "ab" * 32}
        with patch("urllib.request.urlopen") as mock_open:
            from io import BytesIO
            cm = MagicMock()
            cm.__enter__ = lambda self: BytesIO(__import__("json").dumps({"radio": radio}).encode("utf-8"))
            cm.__exit__ = lambda self, *a: False
            mock_open.return_value = cm
            collector._fetch_daemon_radio_state()
            collector._fetch_daemon_radio_state()
            collector._fetch_daemon_radio_state()
        # One real HTTP call, two cache hits.
        assert mock_open.call_count == 1


class TestMeshtasticFeatureFlag:
    """When the deployment profile disables Meshtastic, the meshtasticd poll is skipped."""

    def test_disabled_flag_skips_meshtasticd(self):
        """meshtasticd HTTP/TCP polls do not run when meshtastic_enabled=False."""
        collector = MapDataCollector(meshtastic_enabled=False, enable_history=False, meshforge_maps_enabled=False)
        mock_tracker = MagicMock()
        mock_tracker.get_meshcore_nodes.return_value = []
        mock_tracker.to_geojson.return_value = {"type": "FeatureCollection", "features": []}
        mock_tracker.get_all_nodes.return_value = []

        with patch('src.utils.map_data_collector.get_node_tracker', return_value=mock_tracker), \
             patch.object(collector, '_collect_meshtasticd', return_value=[]) as mock_mt, \
             patch.object(collector, '_collect_direct_radio', return_value=[]) as mock_dr:
            collector.collect()

        mock_mt.assert_not_called()
        mock_dr.assert_not_called()

    def test_enabled_flag_calls_meshtasticd(self):
        """meshtasticd HTTP/TCP polls DO run when meshtastic_enabled=True (default)."""
        collector = MapDataCollector(meshtastic_enabled=True, enable_history=False,
                                     meshforge_maps_enabled=False)
        mock_tracker = MagicMock()
        mock_tracker.get_meshcore_nodes.return_value = []
        mock_tracker.to_geojson.return_value = {"type": "FeatureCollection", "features": []}
        mock_tracker.get_all_nodes.return_value = []

        with patch('src.utils.map_data_collector.get_node_tracker', return_value=mock_tracker), \
             patch.object(collector, '_collect_meshtasticd', return_value=[]) as mock_mt:
            collector.collect()

        mock_mt.assert_called_once()

    def test_default_constructor_keeps_meshtastic_enabled(self):
        """Backward-compat: bare MapDataCollector() defaults to meshtastic_enabled=True."""
        collector = MapDataCollector(enable_history=False)
        assert collector._meshtastic_enabled is True


class TestPositionLessExtension:
    """nodes_without_position is cleared once per cycle and extended by each source."""

    def test_position_less_resets_between_collects(self):
        """A fresh collect() empties stale position-less entries from prior cycles."""
        collector = MapDataCollector(meshtastic_enabled=False, enable_history=False, meshforge_maps_enabled=False)
        mock_tracker = MagicMock()
        mock_tracker.get_meshcore_nodes.return_value = [
            _make_meshcore_node("meshcore:cycle1", "Node1"),
        ]
        mock_tracker.to_geojson.return_value = {"type": "FeatureCollection", "features": []}
        mock_tracker.get_all_nodes.return_value = []

        with patch('src.utils.map_data_collector.get_node_tracker', return_value=mock_tracker):
            collector.collect(max_age_seconds=0)
            assert len(collector._nodes_without_position) == 1

            # Second cycle: tracker returns DIFFERENT nodes — old entries must drop.
            mock_tracker.get_meshcore_nodes.return_value = [
                _make_meshcore_node("meshcore:cycle2", "Node2"),
            ]
            collector.collect(max_age_seconds=0)
            assert len(collector._nodes_without_position) == 1
            assert collector._nodes_without_position[0]["id"] == "meshcore:cycle2"


class TestStaleWhileRevalidate:
    """Stale-cache requests return immediately; refresh runs in background."""

    def _make_collector(self):
        return MapDataCollector(
            meshtastic_enabled=False,
            enable_history=False,
            meshforge_maps_enabled=False,
        )

    def test_stale_cache_returns_immediately_and_kicks_off_refresh(self):
        """Stale cache: caller gets stale data; a background refresh runs."""
        import threading
        import time as _t

        collector = self._make_collector()

        # Prime the in-memory cache directly so we can drive the
        # stale-while-revalidate branch without doing a real collect.
        sentinel = {"type": "FeatureCollection", "features": [],
                    "properties": {"marker": "stale"}}
        collector._cached_geojson = sentinel
        collector._last_collect = _t.time() - 1000  # ancient

        refresh_started = threading.Event()
        refresh_can_finish = threading.Event()

        def slow_refresh():
            refresh_started.set()
            refresh_can_finish.wait(timeout=5)
            collector._cached_geojson = {"type": "FeatureCollection",
                                         "features": [],
                                         "properties": {"marker": "fresh"}}
            collector._last_collect = _t.time()

        with patch.object(collector, "_collect_locked", side_effect=slow_refresh):
            t0 = _t.time()
            result = collector.collect(max_age_seconds=30)
            elapsed = _t.time() - t0

            # Stale data returned immediately, NOT after refresh completes.
            assert result is sentinel
            assert elapsed < 0.5, f"stale path blocked for {elapsed:.2f}s"

            # Background refresh should have started.
            assert refresh_started.wait(timeout=2), "background refresh did not start"

            # Let the refresh finish and confirm the lock is released.
            refresh_can_finish.set()
            for _ in range(50):
                if collector._collect_lock.acquire(blocking=False):
                    collector._collect_lock.release()
                    break
                _t.sleep(0.05)
            else:
                pytest.fail("background refresh did not release the lock")

    def test_concurrent_stale_calls_spawn_only_one_refresh(self):
        """Second stale request while a refresh is in flight does not spawn another."""
        import threading
        import time as _t

        collector = self._make_collector()
        collector._cached_geojson = {"type": "FeatureCollection", "features": []}
        collector._last_collect = _t.time() - 1000

        refresh_calls = []
        proceed = threading.Event()

        def slow_refresh():
            refresh_calls.append(1)
            proceed.wait(timeout=5)

        with patch.object(collector, "_collect_locked", side_effect=slow_refresh):
            # First call: kicks off a background refresh.
            collector.collect(max_age_seconds=30)
            # Wait for the bg thread to grab the lock.
            for _ in range(50):
                if collector._collect_lock.locked():
                    break
                _t.sleep(0.02)
            # Second call: should see lock held, return stale, NOT spawn another refresh.
            collector.collect(max_age_seconds=30)
            collector.collect(max_age_seconds=30)

            proceed.set()
            # Drain the bg thread.
            for _ in range(50):
                if not collector._collect_lock.locked():
                    break
                _t.sleep(0.05)

        assert len(refresh_calls) == 1, (
            f"expected 1 background refresh, got {len(refresh_calls)}"
        )
