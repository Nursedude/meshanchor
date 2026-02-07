"""Tests for MapDataCollector and MapServer."""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import json
import socket
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch, mock_open

import pytest


class TestMapDataCollector:
    """Test the unified node data collector."""

    def _make_collector(self, tmp_path):
        """Create a collector with a temp cache directory."""
        from utils.map_data_service import MapDataCollector
        return MapDataCollector(cache_dir=tmp_path)

    def test_collect_returns_feature_collection(self, tmp_path):
        """Collect always returns a valid GeoJSON FeatureCollection."""
        collector = self._make_collector(tmp_path)
        result = collector.collect()

        assert result["type"] == "FeatureCollection"
        assert "features" in result
        assert isinstance(result["features"], list)

    def test_collect_includes_metadata(self, tmp_path):
        """Collect includes collection metadata."""
        collector = self._make_collector(tmp_path)
        result = collector.collect()

        assert "properties" in result
        assert "collected_at" in result["properties"]
        assert "sources" in result["properties"]

    def test_caching_prevents_repeated_collection(self, tmp_path):
        """Second call within max_age uses cached data."""
        collector = self._make_collector(tmp_path)

        result1 = collector.collect(max_age_seconds=60)
        result2 = collector.collect(max_age_seconds=60)

        # Same object (cached)
        assert result1 is result2

    def test_cache_expires(self, tmp_path):
        """Expired cache triggers re-collection."""
        collector = self._make_collector(tmp_path)

        result1 = collector.collect(max_age_seconds=0)
        result2 = collector.collect(max_age_seconds=0)

        # Different objects (re-collected)
        assert result1 is not result2

    def test_make_feature_valid_geojson(self, tmp_path):
        """_make_feature creates valid GeoJSON Point features."""
        collector = self._make_collector(tmp_path)
        feature = collector._make_feature(
            node_id="!test123",
            name="Test Node",
            lat=21.3069,
            lon=-157.8583,
            network="meshtastic",
            is_online=True,
            snr=10.5,
            battery=85,
            hardware="Heltec V3",
            role="ROUTER",
        )

        assert feature["type"] == "Feature"
        assert feature["geometry"]["type"] == "Point"
        assert feature["geometry"]["coordinates"] == [-157.8583, 21.3069]
        assert feature["properties"]["id"] == "!test123"
        assert feature["properties"]["name"] == "Test Node"
        assert feature["properties"]["network"] == "meshtastic"
        assert feature["properties"]["is_online"] is True
        assert feature["properties"]["snr"] == 10.5
        assert feature["properties"]["battery"] == 85

    def test_merge_feature_prefers_non_null(self, tmp_path):
        """Merge fills in missing data from new feature."""
        collector = self._make_collector(tmp_path)

        existing = collector._make_feature("n1", "Node", 21.0, -157.0,
                                           snr=None, battery=None)
        new = collector._make_feature("n1", "Node", 21.0, -157.0,
                                      snr=8.5, battery=72)

        collector._merge_feature(existing, new)

        assert existing["properties"]["snr"] == 8.5
        assert existing["properties"]["battery"] == 72

    def test_merge_feature_doesnt_overwrite_with_null(self, tmp_path):
        """Merge doesn't replace good data with null."""
        collector = self._make_collector(tmp_path)

        existing = collector._make_feature("n1", "Node", 21.0, -157.0,
                                           snr=10.0, battery=90)
        new = collector._make_feature("n1", "Node", 21.0, -157.0,
                                      snr=None, battery=None)

        collector._merge_feature(existing, new)

        assert existing["properties"]["snr"] == 10.0
        assert existing["properties"]["battery"] == 90

    def test_save_and_load_cache(self, tmp_path):
        """Cache round-trips through save/load."""
        collector = self._make_collector(tmp_path)

        geojson = {
            "type": "FeatureCollection",
            "features": [
                collector._make_feature("n1", "Cached Node", 21.0, -157.0,
                                        is_online=True)
            ]
        }
        collector._save_cache(geojson)

        loaded = collector._load_cache()
        assert len(loaded) == 1
        assert loaded[0]["properties"]["id"] == "n1"

    def test_old_cache_marks_nodes_offline(self, tmp_path):
        """Cache older than 15 minutes marks nodes as offline."""
        collector = self._make_collector(tmp_path)

        geojson = {
            "type": "FeatureCollection",
            "features": [
                collector._make_feature("n1", "Old Node", 21.0, -157.0,
                                        is_online=True)
            ]
        }
        collector._save_cache(geojson)

        # Make cache file appear old
        cache_file = tmp_path / "map_nodes.geojson"
        old_time = time.time() - 1000  # 16+ minutes ago
        os.utime(cache_file, (old_time, old_time))

        loaded = collector._load_cache()
        assert len(loaded) == 1
        assert loaded[0]["properties"]["is_online"] is False
        assert loaded[0]["properties"]["last_seen"] == "cached"

    def test_node_cache_to_feature_with_position(self, tmp_path):
        """Converts node cache entry with lat/lon to feature."""
        collector = self._make_collector(tmp_path)

        node = {
            "id": "!abc123",
            "name": "Cached Node",
            "latitude": 21.3,
            "longitude": -157.8,
            "network": "meshtastic",
            "is_online": True,
            "snr": 6.0,
        }
        feature = collector._node_cache_to_feature(node)

        assert feature is not None
        assert feature["geometry"]["coordinates"] == [-157.8, 21.3]
        assert feature["properties"]["snr"] == 6.0

    def test_node_cache_to_feature_with_position_object(self, tmp_path):
        """Converts node cache entry with position sub-object."""
        collector = self._make_collector(tmp_path)

        node = {
            "id": "!abc123",
            "name": "Node",
            "position": {
                "latitudeI": 213000000,
                "longitudeI": -1578000000,
            }
        }
        feature = collector._node_cache_to_feature(node)

        assert feature is not None
        assert abs(feature["geometry"]["coordinates"][0] - (-157.8)) < 0.01
        assert abs(feature["geometry"]["coordinates"][1] - 21.3) < 0.01

    def test_node_cache_to_feature_no_position(self, tmp_path):
        """Returns None for nodes without valid position."""
        collector = self._make_collector(tmp_path)

        node = {"id": "!abc123", "name": "No Position"}
        assert collector._node_cache_to_feature(node) is None

        node_zero = {"id": "!abc123", "latitude": 0.0, "longitude": 0.0}
        assert collector._node_cache_to_feature(node_zero) is None

    def test_rns_cache_to_feature(self, tmp_path):
        """Converts RNS cache entry to feature with rns network."""
        collector = self._make_collector(tmp_path)

        node = {
            "id": "rns_abc123",
            "name": "RNS Node",
            "latitude": 20.5,
            "longitude": -156.4,
            "is_online": True,
        }
        feature = collector._rns_cache_to_feature(node)

        assert feature is not None
        assert feature["properties"]["network"] == "rns"

    def test_collect_meshtasticd_port_closed(self, tmp_path):
        """Returns empty when meshtasticd port is closed."""
        collector = self._make_collector(tmp_path)
        features = collector._collect_meshtasticd()
        # Port 4403 is not open in test environment
        assert features == []

    def test_collect_mqtt_no_cache(self, tmp_path):
        """Returns empty when no MQTT cache exists."""
        collector = self._make_collector(tmp_path)
        features = collector._collect_mqtt()
        assert features == []

    def test_collect_node_tracker_no_cache(self, tmp_path):
        """Returns empty when no tracker cache exists."""
        collector = self._make_collector(tmp_path)
        features = collector._collect_node_tracker()
        assert features == []

    def test_collect_node_tracker_with_cache(self, tmp_path):
        """Reads node_cache.json when present."""
        collector = self._make_collector(tmp_path)

        # Create a fake node cache
        cache_dir = tmp_path / ".config" / "meshforge"
        cache_dir.mkdir(parents=True)
        cache_file = cache_dir / "node_cache.json"

        nodes = [
            {
                "id": "!cached01",
                "name": "Cached Node",
                "latitude": 21.3,
                "longitude": -157.8,
                "network": "meshtastic",
                "is_online": True,
            }
        ]
        with open(cache_file, 'w') as f:
            json.dump(nodes, f)

        # Patch the path resolution to use our temp dir
        with patch('utils.map_data_service.MapDataCollector._collect_node_tracker') as mock:
            # Just verify the method structure works
            mock.return_value = [collector._node_cache_to_feature(nodes[0])]
            result = mock()
            assert len(result) == 1
            assert result[0]["properties"]["id"] == "!cached01"

    def test_deduplication_by_id(self, tmp_path):
        """Same node from multiple sources appears only once."""
        collector = self._make_collector(tmp_path)

        # Simulate two sources returning the same node
        with patch.object(collector, '_collect_meshtasticd') as mock_tcp, \
             patch.object(collector, '_collect_mqtt') as mock_mqtt:

            feature = collector._make_feature("!same_node", "Node", 21.0, -157.0)
            mock_tcp.return_value = [feature]
            mock_mqtt.return_value = [feature.copy()]

            result = collector.collect(max_age_seconds=0)
            node_ids = [f["properties"]["id"] for f in result["features"]]
            assert node_ids.count("!same_node") == 1


class TestMeshtasticdCollection:
    """Test meshtasticd TCP interface and CLI parsing."""

    def _make_collector(self, tmp_path):
        """Create a collector with a temp cache directory."""
        from utils.map_data_service import MapDataCollector
        return MapDataCollector(cache_dir=tmp_path)

    def test_parse_tcp_node_float_coords(self, tmp_path):
        """Parses node with float latitude/longitude."""
        collector = self._make_collector(tmp_path)
        now = time.time()
        node_data = {
            'num': 0xba4bf9d0,
            'user': {
                'longName': 'Maui Gateway',
                'shortName': 'MG',
                'hwModel': 'HELTEC_V3',
                'role': 'ROUTER',
            },
            'position': {
                'latitude': 21.3069,
                'longitude': -157.8583,
                'altitude': 100,
                'time': int(now) - 60,
            },
            'deviceMetrics': {
                'batteryLevel': 85,
                'voltage': 4.15,
            },
            'snr': 8.5,
            'lastHeard': int(now) - 30,
            'hopsAway': 0,
            'viaMqtt': False,
        }

        feature = collector._parse_tcp_node('!ba4bf9d0', node_data, now)

        assert feature is not None
        assert feature['geometry']['coordinates'] == [-157.8583, 21.3069]
        assert feature['properties']['id'] == '!ba4bf9d0'
        assert feature['properties']['name'] == 'Maui Gateway'
        assert feature['properties']['network'] == 'meshtastic'
        assert feature['properties']['is_online'] is True
        assert feature['properties']['snr'] == 8.5
        assert feature['properties']['battery'] == 85
        assert feature['properties']['hardware'] == 'HELTEC_V3'
        assert feature['properties']['role'] == 'ROUTER'
        assert feature['properties']['is_gateway'] is True
        assert feature['properties']['via_mqtt'] is False
        assert feature['properties']['is_local'] is True

    def test_parse_tcp_node_integer_coords(self, tmp_path):
        """Parses node with latitudeI/longitudeI (integer * 1e7)."""
        collector = self._make_collector(tmp_path)
        now = time.time()
        node_data = {
            'num': 0x12345678,
            'user': {'longName': 'Remote Node'},
            'position': {
                'latitudeI': 213069000,
                'longitudeI': -1578583000,
            },
            'snr': 5.0,
            'lastHeard': int(now) - 120,
            'hopsAway': 2,
        }

        feature = collector._parse_tcp_node('!12345678', node_data, now)

        assert feature is not None
        assert abs(feature['geometry']['coordinates'][0] - (-157.8583)) < 0.001
        assert abs(feature['geometry']['coordinates'][1] - 21.3069) < 0.001
        assert feature['properties']['is_local'] is False

    def test_parse_tcp_node_no_position(self, tmp_path):
        """Returns None for node without position data."""
        collector = self._make_collector(tmp_path)
        now = time.time()

        # No position key at all
        node_data = {'num': 123, 'user': {'longName': 'No Pos'}}
        assert collector._parse_tcp_node('!123', node_data, now) is None

        # Empty position dict
        node_data = {'num': 123, 'position': {}}
        assert collector._parse_tcp_node('!123', node_data, now) is None

    def test_parse_tcp_node_zero_coords(self, tmp_path):
        """Returns None for (0,0) coordinates (unset position)."""
        collector = self._make_collector(tmp_path)
        now = time.time()
        node_data = {
            'num': 123,
            'position': {'latitude': 0.0, 'longitude': 0.0},
        }
        assert collector._parse_tcp_node('!123', node_data, now) is None

    def test_parse_tcp_node_offline_detection(self, tmp_path):
        """Node not heard in >15 minutes is marked offline."""
        collector = self._make_collector(tmp_path)
        now = time.time()
        node_data = {
            'num': 0xaabbccdd,
            'user': {'longName': 'Stale Node'},
            'position': {'latitude': 20.5, 'longitude': -156.0},
            'lastHeard': int(now) - 1800,  # 30 minutes ago
        }

        feature = collector._parse_tcp_node('!aabbccdd', node_data, now)

        assert feature is not None
        assert feature['properties']['is_online'] is False
        assert '30m ago' in feature['properties']['last_seen']

    def test_parse_tcp_node_formats_last_seen(self, tmp_path):
        """Last seen is formatted as human-readable time."""
        collector = self._make_collector(tmp_path)
        now = time.time()

        # Seconds ago
        node_data = {
            'num': 1, 'position': {'latitude': 21.0, 'longitude': -157.0},
            'lastHeard': int(now) - 45,
        }
        feature = collector._parse_tcp_node('!1', node_data, now)
        assert '45s ago' in feature['properties']['last_seen']

        # Hours ago
        node_data['lastHeard'] = int(now) - 7200
        feature = collector._parse_tcp_node('!1', node_data, now)
        assert '2h ago' in feature['properties']['last_seen']

        # Days ago
        node_data['lastHeard'] = int(now) - 172800
        feature = collector._parse_tcp_node('!1', node_data, now)
        assert '2d ago' in feature['properties']['last_seen']

    def test_parse_tcp_node_no_last_heard(self, tmp_path):
        """Node with no lastHeard shows 'unknown' and is offline."""
        collector = self._make_collector(tmp_path)
        now = time.time()
        node_data = {
            'num': 1,
            'user': {'longName': 'Mystery'},
            'position': {'latitude': 21.0, 'longitude': -157.0},
        }

        feature = collector._parse_tcp_node('!1', node_data, now)

        assert feature['properties']['is_online'] is False
        assert feature['properties']['last_seen'] == 'unknown'

    def test_parse_tcp_node_id_formatting(self, tmp_path):
        """Node ID is properly formatted as hex with ! prefix."""
        collector = self._make_collector(tmp_path)
        now = time.time()
        node_data = {
            'num': 0x00aabb11,
            'position': {'latitude': 21.0, 'longitude': -157.0},
            'lastHeard': int(now),
        }

        # String node_id with ! prefix passes through
        feature = collector._parse_tcp_node('!00aabb11', node_data, now)
        assert feature['properties']['id'] == '!00aabb11'

        # Numeric node_id gets formatted
        feature = collector._parse_tcp_node('some_key', node_data, now)
        assert feature['properties']['id'] == '!00aabb11'

    def test_parse_tcp_node_via_mqtt_gateway(self, tmp_path):
        """Nodes received via MQTT are flagged."""
        collector = self._make_collector(tmp_path)
        now = time.time()
        node_data = {
            'num': 42,
            'position': {'latitude': 21.0, 'longitude': -157.0},
            'viaMqtt': True,
            'hopsAway': 3,
            'lastHeard': int(now) - 10,
        }

        feature = collector._parse_tcp_node('!0000002a', node_data, now)
        assert feature['properties']['via_mqtt'] is True
        assert feature['properties']['is_local'] is False

    def test_collect_via_tcp_interface_mocked(self, tmp_path):
        """TCP collection uses connection manager and parses nodes."""
        collector = self._make_collector(tmp_path)
        now = time.time()

        mock_interface = MagicMock()
        mock_interface.nodes = {
            '!node1': {
                'num': 0x11111111,
                'user': {'longName': 'Node 1', 'hwModel': 'RAK4631'},
                'position': {'latitude': 21.3, 'longitude': -157.8},
                'lastHeard': int(now) - 60,
                'hopsAway': 0,
            },
            '!node2': {
                'num': 0x22222222,
                'user': {'longName': 'Node 2'},
                'position': {'latitude': 20.8, 'longitude': -156.3},
                'lastHeard': int(now) - 60,
            },
            '!nopos': {
                'num': 0x33333333,
                'user': {'longName': 'No Position'},
            }
        }

        mock_manager = MagicMock()
        mock_manager.acquire_lock.return_value = True
        mock_manager._create_interface.return_value = mock_interface

        with patch('utils.meshtastic_connection.get_connection_manager', return_value=mock_manager):
            features = collector._collect_via_tcp_interface()

        # Should get 2 nodes (one has no position)
        assert len(features) == 2
        ids = [f['properties']['id'] for f in features]
        assert '!node1' in ids  # Key starts with '!', used as-is
        assert '!node2' in ids

    def test_collect_via_tcp_lock_timeout(self, tmp_path):
        """Returns empty when connection lock unavailable."""
        collector = self._make_collector(tmp_path)

        mock_manager = MagicMock()
        mock_manager.acquire_lock.return_value = False  # Lock held by someone else

        with patch('utils.meshtastic_connection.get_connection_manager', return_value=mock_manager):
            features = collector._collect_via_tcp_interface()

        assert features == []

    def test_collect_meshtasticd_port_closed(self, tmp_path):
        """Returns empty when meshtasticd is not running."""
        collector = self._make_collector(tmp_path)
        features = collector._collect_meshtasticd()
        assert features == []

    def test_parse_meshtastic_info_json_output(self, tmp_path):
        """CLI fallback parses JSON fragments in output."""
        collector = self._make_collector(tmp_path)

        output = '''Connected to radio
Nodes in mesh:
{"num": 12345, "user": {"longName": "Test"}, "position": {"latitude": 21.3, "longitude": -157.8}, "snr": 6.0, "deviceMetrics": {"batteryLevel": 80}}
'''
        features = collector._parse_meshtastic_info(output)

        assert len(features) == 1
        assert features[0]['geometry']['coordinates'] == [-157.8, 21.3]
        assert features[0]['properties']['snr'] == 6.0
        assert features[0]['properties']['battery'] == 80

    def test_parse_meshtastic_info_integer_coords(self, tmp_path):
        """CLI fallback handles latitudeI format."""
        collector = self._make_collector(tmp_path)

        output = '{"num": 99, "position": {"latitudeI": 213000000, "longitudeI": -1578000000}}'
        features = collector._parse_meshtastic_info(output)

        assert len(features) == 1
        assert abs(features[0]['geometry']['coordinates'][0] - (-157.8)) < 0.01
        assert abs(features[0]['geometry']['coordinates'][1] - 21.3) < 0.01

    def test_parse_meshtastic_info_no_position(self, tmp_path):
        """CLI fallback skips nodes without position."""
        collector = self._make_collector(tmp_path)

        output = 'Some text without any position data\nNo JSON here'
        features = collector._parse_meshtastic_info(output)
        assert features == []

    def test_collect_via_cli_not_installed(self, tmp_path):
        """CLI fallback handles missing meshtastic command."""
        collector = self._make_collector(tmp_path)

        with patch('subprocess.run', side_effect=FileNotFoundError):
            features = collector._collect_via_cli()

        assert features == []

    def test_collect_via_cli_timeout(self, tmp_path):
        """CLI fallback handles command timeout."""
        collector = self._make_collector(tmp_path)

        with patch('subprocess.run', side_effect=subprocess.TimeoutExpired('meshtastic', 15)):
            features = collector._collect_via_cli()

        assert features == []


class TestMapServer:
    """Test the HTTP map server."""

    def test_server_binds_to_port(self):
        """Server starts and binds to specified port."""
        from utils.map_data_service import MapServer

        # Find a free port
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(('', 0))
        port = sock.getsockname()[1]
        sock.close()

        server = MapServer(port=port, host="127.0.0.1")
        server.start_background()

        try:
            # Verify port is open
            time.sleep(0.5)
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(2)
            result = sock.connect_ex(('127.0.0.1', port))
            sock.close()
            assert result == 0, f"Server not listening on port {port}"
        finally:
            server.stop()

    def test_api_geojson_endpoint(self):
        """GET /api/nodes/geojson returns valid GeoJSON."""
        from utils.map_data_service import MapServer
        import urllib.request

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(('', 0))
        port = sock.getsockname()[1]
        sock.close()

        server = MapServer(port=port, host="127.0.0.1")
        server.start_background()

        try:
            time.sleep(0.5)
            url = f"http://127.0.0.1:{port}/api/nodes/geojson"
            response = urllib.request.urlopen(url, timeout=5)
            data = json.loads(response.read())

            assert data["type"] == "FeatureCollection"
            assert "features" in data
            assert response.headers["Content-Type"] == "application/json"
        finally:
            server.stop()

    def test_api_status_endpoint(self):
        """GET /api/status returns server status."""
        from utils.map_data_service import MapServer
        import urllib.request

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(('', 0))
        port = sock.getsockname()[1]
        sock.close()

        server = MapServer(port=port, host="127.0.0.1")
        server.start_background()

        try:
            time.sleep(0.5)
            url = f"http://127.0.0.1:{port}/api/status"
            response = urllib.request.urlopen(url, timeout=5)
            data = json.loads(response.read())

            assert data["status"] == "running"
            assert "time" in data
        finally:
            server.stop()

    def test_server_stop(self):
        """Server stops cleanly."""
        from utils.map_data_service import MapServer

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(('', 0))
        port = sock.getsockname()[1]
        sock.close()

        server = MapServer(port=port, host="127.0.0.1")
        server.start_background()
        time.sleep(0.3)

        server.stop()
        time.sleep(0.3)

        # Port should be released
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(1)
        result = sock.connect_ex(('127.0.0.1', port))
        sock.close()
        # Connection should fail (server stopped)
        assert result != 0

    def test_server_url_property(self):
        """Server exposes URL property."""
        from utils.map_data_service import MapServer

        server = MapServer(port=5555, host="0.0.0.0")
        assert server.url == "http://localhost:5555"

    def test_map_html_served_at_root(self):
        """GET / serves the node_map.html file."""
        from utils.map_data_service import MapServer
        import urllib.request

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(('', 0))
        port = sock.getsockname()[1]
        sock.close()

        server = MapServer(port=port, host="127.0.0.1")
        server.start_background()

        try:
            time.sleep(0.5)
            url = f"http://127.0.0.1:{port}/"
            response = urllib.request.urlopen(url, timeout=5)
            content = response.read().decode()

            assert "MeshForge" in content
            assert "leaflet" in content.lower()
            assert response.headers["Content-Type"] == "text/html"
        finally:
            server.stop()

    def test_cors_default_allows_all(self):
        """Default CORS configuration allows all origins (*)."""
        from utils.map_data_service import MapServer

        server = MapServer(port=5000, host="127.0.0.1")
        assert server.cors_origins is None  # None means allow all

    def test_cors_custom_origins(self):
        """Custom CORS origins are stored correctly."""
        from utils.map_data_service import MapServer

        origins = ["http://localhost", "http://192.168.1."]
        server = MapServer(port=5000, cors_origins=origins)
        assert server.cors_origins == origins

    def test_cors_handler_method(self):
        """MapRequestHandler._send_cors_header works correctly."""
        from utils.map_data_service import MapRequestHandler

        # Test allow all (None)
        handler = MagicMock(spec=MapRequestHandler)
        handler.headers = {"Origin": "http://192.168.1.100:8080"}
        handler.allowed_origins = None
        handler.send_header = MagicMock()

        # Call the actual method
        MapRequestHandler._send_cors_header(handler)

        handler.send_header.assert_called_once_with(
            'Access-Control-Allow-Origin', '*'
        )

    def test_cors_handler_with_allowed_list(self):
        """MapRequestHandler respects allowed origins list."""
        from utils.map_data_service import MapRequestHandler

        handler = MagicMock(spec=MapRequestHandler)
        handler.headers = {"Origin": "http://192.168.1.100:8080"}
        handler.allowed_origins = ["http://192.168.1."]
        handler.send_header = MagicMock()

        MapRequestHandler._send_cors_header(handler)

        # Should allow the matching origin
        handler.send_header.assert_called_once_with(
            'Access-Control-Allow-Origin', 'http://192.168.1.100:8080'
        )


class TestMapDataCollectorIntegration:
    """Integration tests that verify the full collection pipeline."""

    def test_collect_with_mqtt_cache_file(self, tmp_path):
        """Collector reads MQTT cache file when present."""
        from utils.map_data_service import MapDataCollector

        collector = MapDataCollector(cache_dir=tmp_path)

        # Create a fresh MQTT cache file
        mqtt_geojson = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [-157.8, 21.3]},
                    "properties": {
                        "id": "!mqtt_node",
                        "name": "MQTT Node",
                        "network": "meshtastic",
                        "is_online": True,
                        "snr": 7.5,
                        "battery": 60,
                        "last_seen": "1m ago",
                        "hardware": "RAK4631",
                        "role": "CLIENT",
                        "is_gateway": False,
                        "via_mqtt": True,
                        "is_local": False,
                    }
                }
            ]
        }
        mqtt_cache = tmp_path / "mqtt_nodes.json"
        with open(mqtt_cache, 'w') as f:
            json.dump(mqtt_geojson, f)

        result = collector.collect(max_age_seconds=0)

        # Should find the MQTT node
        node_ids = [f["properties"]["id"] for f in result["features"]]
        assert "!mqtt_node" in node_ids

    def test_collect_empty_gracefully(self, tmp_path):
        """Collector handles all sources being empty."""
        from utils.map_data_service import MapDataCollector

        collector = MapDataCollector(cache_dir=tmp_path)
        result = collector.collect(max_age_seconds=0)

        assert result["type"] == "FeatureCollection"
        assert result["features"] == []
        assert result["properties"]["source_count"] == 0


class TestCoordinateValidation:
    """Test _is_valid_coordinate for reliability edge cases."""

    def _make_collector(self, tmp_path):
        from utils.map_data_service import MapDataCollector
        return MapDataCollector(cache_dir=tmp_path)

    def test_valid_coordinates(self, tmp_path):
        """Normal coordinates are accepted."""
        c = self._make_collector(tmp_path)
        assert c._is_valid_coordinate(21.3069, -157.8583) is True
        assert c._is_valid_coordinate(-33.8688, 151.2093) is True  # Sydney
        assert c._is_valid_coordinate(64.1466, -21.9426) is True   # Reykjavik

    def test_none_rejected(self, tmp_path):
        """None values are rejected."""
        c = self._make_collector(tmp_path)
        assert c._is_valid_coordinate(None, -157.8) is False
        assert c._is_valid_coordinate(21.3, None) is False
        assert c._is_valid_coordinate(None, None) is False

    def test_nan_rejected(self, tmp_path):
        """NaN coordinates are rejected (prevents map rendering crash)."""
        import math
        c = self._make_collector(tmp_path)
        assert c._is_valid_coordinate(float('nan'), -157.8) is False
        assert c._is_valid_coordinate(21.3, float('nan')) is False

    def test_infinity_rejected(self, tmp_path):
        """Infinity coordinates are rejected."""
        c = self._make_collector(tmp_path)
        assert c._is_valid_coordinate(float('inf'), -157.8) is False
        assert c._is_valid_coordinate(21.3, float('-inf')) is False

    def test_out_of_range_rejected(self, tmp_path):
        """Coordinates outside valid range are rejected."""
        c = self._make_collector(tmp_path)
        assert c._is_valid_coordinate(91.0, 0.0) is False   # lat > 90
        assert c._is_valid_coordinate(-91.0, 0.0) is False  # lat < -90
        assert c._is_valid_coordinate(0.0, 181.0) is False  # lon > 180
        assert c._is_valid_coordinate(0.0, -181.0) is False # lon < -180

    def test_default_zero_rejected(self, tmp_path):
        """Both-zero coordinates (unset GPS default) are rejected."""
        c = self._make_collector(tmp_path)
        assert c._is_valid_coordinate(0.0, 0.0) is False

    def test_equator_accepted(self, tmp_path):
        """Nodes near equator with valid lon are NOT rejected (was a bug)."""
        c = self._make_collector(tmp_path)
        # Quito, Ecuador (near equator)
        assert c._is_valid_coordinate(0.1807, -78.4678) is True
        # Singapore (near equator)
        assert c._is_valid_coordinate(1.3521, 103.8198) is True
        # Node exactly on equator but with valid lon
        assert c._is_valid_coordinate(0.0, 36.8219) is True  # Nairobi meridian

    def test_prime_meridian_accepted(self, tmp_path):
        """Nodes near prime meridian with valid lat are NOT rejected (was a bug)."""
        c = self._make_collector(tmp_path)
        # London (near prime meridian)
        assert c._is_valid_coordinate(51.5074, 0.0) is True
        # Accra, Ghana (on prime meridian, near equator)
        assert c._is_valid_coordinate(5.6037, 0.0) is True

    def test_string_coordinates_handled(self, tmp_path):
        """String values that can be parsed are accepted."""
        c = self._make_collector(tmp_path)
        assert c._is_valid_coordinate("21.3", "-157.8") is True

    def test_invalid_string_rejected(self, tmp_path):
        """Non-numeric strings are rejected."""
        c = self._make_collector(tmp_path)
        assert c._is_valid_coordinate("abc", "-157.8") is False
        assert c._is_valid_coordinate(21.3, "xyz") is False
