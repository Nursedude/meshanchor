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

    def test_position_less_meshcore_node_surfaces_in_side_panel(self):
        """MeshCore advertisements without GPS land in nodes_without_position."""
        collector = MapDataCollector(meshtastic_enabled=False, enable_history=False)
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
        collector = MapDataCollector(meshtastic_enabled=False, enable_history=False)
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
        collector = MapDataCollector(meshtastic_enabled=False, enable_history=False)
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
        collector = MapDataCollector(meshtastic_enabled=False, enable_history=False)
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


class TestMeshtasticFeatureFlag:
    """When the deployment profile disables Meshtastic, the meshtasticd poll is skipped."""

    def test_disabled_flag_skips_meshtasticd(self):
        """meshtasticd HTTP/TCP polls do not run when meshtastic_enabled=False."""
        collector = MapDataCollector(meshtastic_enabled=False, enable_history=False)
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
        collector = MapDataCollector(meshtastic_enabled=True, enable_history=False)
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
        collector = MapDataCollector(meshtastic_enabled=False, enable_history=False)
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
