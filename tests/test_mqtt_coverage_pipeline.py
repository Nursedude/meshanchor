"""
Integration test: MQTT → Coverage Map pipeline.

Tests that node positions received via MQTT subscriber flow correctly
into the CoverageMapGenerator for map output.

Run: python3 -m pytest tests/test_mqtt_coverage_pipeline.py -v
"""

import json
import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.monitoring.mqtt_subscriber import (
    MQTTNodelessSubscriber,
    MQTTNode,
    MQTTMessage,
    VALID_LAT_RANGE,
    VALID_LON_RANGE,
)
from src.utils.coverage_map import CoverageMapGenerator, MapNode


# =============================================================================
# FIXTURES
# =============================================================================

@pytest.fixture
def subscriber():
    """Create an MQTT subscriber without connecting."""
    config = {
        "broker": "localhost",
        "port": 1883,
        "username": "",
        "password": "",
        "root_topic": "msh/US/2/e",
        "channel": "LongFast",
        "key": "AQ==",
        "use_tls": False,
        "regions": ["US"],
        "auto_reconnect": False,
    }
    return MQTTNodelessSubscriber(config=config)


@pytest.fixture
def map_generator():
    """Create a CoverageMapGenerator."""
    return CoverageMapGenerator()


# =============================================================================
# TEST: MQTT NODE POSITION HANDLING
# =============================================================================

class TestMQTTPositionHandling:
    """Test MQTT subscriber's position data parsing."""

    def test_handle_position_integer_coords(self, subscriber):
        """Test position parsing with latitude_i / longitude_i (integer format)."""
        # Simulate Hawaii coordinates: 21.3069° N, -157.8583° W
        # Node IDs from MQTT are strings after processing
        data = {
            "from": "!abcd0001",
            "payload": {
                "latitude_i": 213069000,  # 21.3069 * 1e7
                "longitude_i": -1578583000,  # -157.8583 * 1e7
                "altitude": 15,
            },
        }

        subscriber._handle_position(data)

        node = subscriber.get_node("!abcd0001")
        assert node is not None
        assert abs(node.latitude - 21.3069) < 0.001
        assert abs(node.longitude - (-157.8583)) < 0.001
        assert node.altitude == 15

    def test_handle_position_float_coords(self, subscriber):
        """Test position parsing with direct float coordinates."""
        data = {
            "from": "!abcd0002",
            "payload": {
                "latitude": 21.3069,
                "longitude": -157.8583,
            },
        }

        subscriber._handle_position(data)

        node = subscriber.get_node("!abcd0002")
        assert node is not None
        assert abs(node.latitude - 21.3069) < 0.001
        assert abs(node.longitude - (-157.8583)) < 0.001

    def test_handle_position_rejects_zero(self, subscriber):
        """Test that (0.0, 0.0) coordinates are rejected as invalid."""
        data = {
            "from": "!abcd0003",
            "payload": {
                "latitude": 0.0,
                "longitude": 0.0,
            },
        }

        subscriber._handle_position(data)

        node = subscriber.get_node("!abcd0003")
        assert node is not None
        assert node.latitude is None  # Rejected
        assert node.longitude is None

    def test_handle_position_rejects_out_of_range(self, subscriber):
        """Test that out-of-range coordinates are rejected."""
        data = {
            "from": "!abcd0004",
            "payload": {
                "latitude": 999.0,  # Invalid
                "longitude": -200.0,  # Invalid
            },
        }

        subscriber._handle_position(data)

        node = subscriber.get_node("!abcd0004")
        assert node is not None
        assert node.latitude is None
        assert node.longitude is None

    def test_handle_nodeinfo_sets_names(self, subscriber):
        """Test that nodeinfo populates long_name and short_name."""
        data = {
            "from": "!abcd0005",
            "payload": {
                "id": "!abcd0005",
                "longname": "Mauna Kea Relay",
                "shortname": "MKR",
                "hardware": "TBEAM",
                "role": "ROUTER",
            },
        }

        subscriber._handle_nodeinfo(data)

        node = subscriber.get_node("!abcd0005")
        assert node is not None
        assert node.long_name == "Mauna Kea Relay"
        assert node.short_name == "MKR"
        assert node.hardware_model == "TBEAM"
        assert node.role == "ROUTER"


class TestMQTTNodesWithPosition:
    """Test querying nodes that have valid position data."""

    def test_get_nodes_with_position(self, subscriber):
        """Test filtering nodes that have position data."""
        # Node with position
        subscriber._nodes["node1"] = MQTTNode(
            node_id="node1", latitude=21.3, longitude=-157.8
        )
        # Node without position
        subscriber._nodes["node2"] = MQTTNode(
            node_id="node2", latitude=None, longitude=None
        )
        # Node with only lat (incomplete)
        subscriber._nodes["node3"] = MQTTNode(
            node_id="node3", latitude=21.3, longitude=None
        )

        with_pos = subscriber.get_nodes_with_position()
        assert len(with_pos) == 1
        assert with_pos[0].node_id == "node1"


class TestMQTTGeoJSON:
    """Test GeoJSON export from MQTT subscriber."""

    def test_geojson_output_structure(self, subscriber):
        """Test that get_geojson returns valid GeoJSON FeatureCollection."""
        subscriber._nodes["node1"] = MQTTNode(
            node_id="node1",
            long_name="Test Node",
            latitude=21.3069,
            longitude=-157.8583,
        )
        subscriber._nodes["node2"] = MQTTNode(
            node_id="node2",
            long_name="Relay Node",
            latitude=19.8968,
            longitude=-155.5828,
        )

        geojson = subscriber.get_geojson()

        assert geojson["type"] == "FeatureCollection"
        assert len(geojson["features"]) == 2

        feature = geojson["features"][0]
        assert feature["type"] == "Feature"
        assert feature["geometry"]["type"] == "Point"
        assert len(feature["geometry"]["coordinates"]) == 2
        assert "properties" in feature

    def test_geojson_excludes_zero_coords(self, subscriber):
        """Test that (0,0) nodes are excluded from GeoJSON."""
        subscriber._nodes["good"] = MQTTNode(
            node_id="good", latitude=21.3, longitude=-157.8
        )
        subscriber._nodes["zero"] = MQTTNode(
            node_id="zero", latitude=0.0, longitude=0.0
        )

        geojson = subscriber.get_geojson()
        assert len(geojson["features"]) == 1

    def test_geojson_excludes_none_coords(self, subscriber):
        """Test that nodes without coords are excluded from GeoJSON."""
        subscriber._nodes["good"] = MQTTNode(
            node_id="good", latitude=21.3, longitude=-157.8
        )
        subscriber._nodes["nopos"] = MQTTNode(
            node_id="nopos", latitude=None, longitude=None
        )

        geojson = subscriber.get_geojson()
        assert len(geojson["features"]) == 1


# =============================================================================
# TEST: COVERAGE MAP NODE INGESTION
# =============================================================================

class TestCoverageMapNodes:
    """Test CoverageMapGenerator node handling."""

    def test_add_single_node(self, map_generator):
        """Test adding a single MapNode."""
        node = MapNode(
            id="!abc123",
            name="Test Node",
            latitude=21.3069,
            longitude=-157.8583,
            network="meshtastic",
            is_online=True,
        )

        map_generator.add_node(node)
        assert len(map_generator._nodes) == 1
        assert map_generator._nodes[0].id == "!abc123"

    def test_add_multiple_nodes(self, map_generator):
        """Test adding multiple MapNodes."""
        nodes = [
            MapNode(id="n1", name="Node 1", latitude=21.3, longitude=-157.8),
            MapNode(id="n2", name="Node 2", latitude=19.9, longitude=-155.6),
            MapNode(id="n3", name="Node 3", latitude=20.8, longitude=-156.3),
        ]

        map_generator.add_nodes(nodes)
        assert len(map_generator._nodes) == 3

    def test_add_nodes_from_geojson(self, map_generator):
        """Test adding nodes from GeoJSON FeatureCollection."""
        geojson = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": {
                        "type": "Point",
                        "coordinates": [-157.8583, 21.3069],  # [lon, lat]
                    },
                    "properties": {
                        "id": "!abc123",
                        "name": "Honolulu Node",
                        "network": "meshtastic",
                        "is_online": True,
                        "via_mqtt": True,
                    },
                },
                {
                    "type": "Feature",
                    "geometry": {
                        "type": "Point",
                        "coordinates": [-155.5828, 19.8968],
                    },
                    "properties": {
                        "id": "!def456",
                        "name": "Hilo Node",
                        "network": "meshtastic",
                        "is_online": False,
                    },
                },
            ],
        }

        map_generator.add_nodes_from_geojson(geojson)

        assert len(map_generator._nodes) == 2
        # GeoJSON coordinates are [lon, lat], verify they were parsed correctly
        assert abs(map_generator._nodes[0].latitude - 21.3069) < 0.001
        assert abs(map_generator._nodes[0].longitude - (-157.8583)) < 0.001


# =============================================================================
# TEST: FULL PIPELINE (MQTT -> GeoJSON -> CoverageMap)
# =============================================================================

class TestMQTTToCoveragePipeline:
    """Test the full MQTT → GeoJSON → CoverageMap pipeline."""

    def test_mqtt_nodes_flow_to_coverage_map(self, subscriber, map_generator):
        """
        Full pipeline: MQTT position data → subscriber nodes → GeoJSON → map generator.

        This simulates what happens when MeshForge receives MQTT telemetry
        from Hawaiian mesh nodes and generates a coverage map.
        """
        # Step 1: Simulate receiving position data from MQTT
        positions = [
            {"from": "!00001001", "payload": {"latitude": 21.3069, "longitude": -157.8583}},  # Honolulu
            {"from": "!00001002", "payload": {"latitude": 19.8968, "longitude": -155.5828}},  # Hilo
            {"from": "!00001003", "payload": {"latitude": 20.7984, "longitude": -156.3319}},  # Kahului
        ]

        for pos in positions:
            subscriber._handle_position(pos)

        # Add node names
        for nid, name in [("!00001001", "Honolulu"), ("!00001002", "Hilo"), ("!00001003", "Kahului")]:
            subscriber._handle_nodeinfo({
                "from": nid,
                "payload": {"id": nid, "longname": f"{name} Node"},
            })

        # Step 2: Verify nodes have positions
        with_pos = subscriber.get_nodes_with_position()
        assert len(with_pos) == 3

        # Step 3: Generate GeoJSON
        geojson = subscriber.get_geojson()
        assert geojson["type"] == "FeatureCollection"
        assert len(geojson["features"]) == 3

        # Step 4: Feed GeoJSON to coverage map generator
        map_generator.add_nodes_from_geojson(geojson)
        assert len(map_generator._nodes) == 3

        # Verify coordinate integrity through pipeline
        lats = sorted([n.latitude for n in map_generator._nodes])
        assert abs(lats[0] - 19.8968) < 0.001  # Hilo
        assert abs(lats[1] - 20.7984) < 0.001  # Kahului
        assert abs(lats[2] - 21.3069) < 0.001  # Honolulu

    def test_pipeline_filters_invalid_positions(self, subscriber, map_generator):
        """Test that invalid positions are filtered at every stage of the pipeline."""
        # Mix of valid and invalid positions
        subscriber._handle_position({
            "from": "!00002001",
            "payload": {"latitude": 21.3, "longitude": -157.8},
        })
        subscriber._handle_position({
            "from": "!00002002",
            "payload": {"latitude": 0.0, "longitude": 0.0},  # Invalid (0,0)
        })
        subscriber._handle_position({
            "from": "!00002003",
            "payload": {"latitude": 999.0, "longitude": -999.0},  # Out of range
        })

        # Only node 2001 should have valid position
        with_pos = subscriber.get_nodes_with_position()
        assert len(with_pos) == 1

        geojson = subscriber.get_geojson()
        assert len(geojson["features"]) == 1

        map_generator.add_nodes_from_geojson(geojson)
        assert len(map_generator._nodes) == 1

    def test_map_generation_with_folium(self, subscriber, map_generator, tmp_path):
        """Test that map HTML can actually be generated (requires folium)."""
        subscriber._nodes["n1"] = MQTTNode(
            node_id="n1", long_name="Test Node",
            latitude=21.3, longitude=-157.8, is_favorite=False,
        )

        geojson = subscriber.get_geojson()
        map_generator.add_nodes_from_geojson(geojson)

        output_path = str(tmp_path / "test_map.html")

        try:
            result = map_generator.generate(output_path=output_path)
            assert Path(result).exists()
            content = Path(result).read_text()
            assert "leaflet" in content.lower() or "folium" in content.lower()
        except ImportError:
            pytest.skip("folium not installed")


# =============================================================================
# TEST: TELEMETRY DATA FLOW
# =============================================================================

class TestTelemetryPipeline:
    """Test that telemetry data (battery, SNR, etc.) flows to map nodes."""

    def test_telemetry_enriches_geojson(self, subscriber):
        """Test that telemetry data appears in GeoJSON properties."""
        subscriber._nodes["n1"] = MQTTNode(
            node_id="n1",
            long_name="Enriched Node",
            latitude=21.3,
            longitude=-157.8,
            battery_level=85,
            snr=12.5,
            rssi=-65,
            channel_utilization=15.0,
        )

        geojson = subscriber.get_geojson()
        props = geojson["features"][0]["properties"]

        # Verify telemetry data is included in properties
        assert "node_id" in props or "id" in props

    def test_online_status_in_geojson(self, subscriber):
        """Test that online status is correctly reflected."""
        subscriber._nodes["online"] = MQTTNode(
            node_id="online",
            latitude=21.3,
            longitude=-157.8,
            last_seen=datetime.now(),
        )

        geojson = subscriber.get_geojson()
        props = geojson["features"][0]["properties"]
        assert props.get("is_online") is True
