"""
Tests for GPS Integration.

Tests cover:
- Position dataclass and validation
- NodeDistance calculations and formatting
- Haversine distance accuracy
- Initial bearing calculations
- GPSReader gpsd protocol parsing
- GPSManager caching and persistence
- Manual position setting
- Distance-to-nodes calculations
- Position report formatting
- Edge cases and error handling

Run with: pytest tests/test_gps_integration.py -v
"""

import pytest
import sys
import os
import json
import math
import time
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from utils.gps_integration import (
    Position, NodeDistance, GPSReader, GPSManager,
    haversine_distance, initial_bearing,
    GPSD_HOST, GPSD_PORT, POSITION_STALE_SEC,
)


# =============================================================================
# Position Tests
# =============================================================================


class TestPosition:
    """Test Position dataclass."""

    def test_creation(self):
        pos = Position(lat=21.3069, lon=-157.8583)
        assert pos.lat == 21.3069
        assert pos.lon == -157.8583

    def test_creation_with_all_fields(self):
        pos = Position(
            lat=21.3, lon=-157.8, alt=100.0,
            speed=5.0, heading=45.0, accuracy=10.0,
            timestamp=time.time(), source="gpsd"
        )
        assert pos.alt == 100.0
        assert pos.speed == 5.0
        assert pos.source == "gpsd"

    def test_is_valid_normal(self):
        pos = Position(lat=21.3, lon=-157.8)
        assert pos.is_valid is True

    def test_is_valid_poles(self):
        assert Position(lat=90.0, lon=0.0).is_valid is True
        assert Position(lat=-90.0, lon=0.0).is_valid is True

    def test_is_valid_dateline(self):
        assert Position(lat=0.0, lon=180.0).is_valid is True
        assert Position(lat=0.0, lon=-180.0).is_valid is True

    def test_is_valid_out_of_range_lat(self):
        assert Position(lat=91.0, lon=0.0).is_valid is False
        assert Position(lat=-91.0, lon=0.0).is_valid is False

    def test_is_valid_out_of_range_lon(self):
        assert Position(lat=0.0, lon=181.0).is_valid is False
        assert Position(lat=0.0, lon=-181.0).is_valid is False

    def test_is_stale_no_timestamp(self):
        pos = Position(lat=21.3, lon=-157.8, timestamp=0.0)
        assert pos.is_stale is True

    def test_is_stale_old(self):
        pos = Position(lat=21.3, lon=-157.8,
                       timestamp=time.time() - POSITION_STALE_SEC - 10)
        assert pos.is_stale is True

    def test_is_stale_fresh(self):
        pos = Position(lat=21.3, lon=-157.8, timestamp=time.time())
        assert pos.is_stale is False

    def test_to_dict(self):
        pos = Position(lat=21.3, lon=-157.8, source="manual")
        d = pos.to_dict()
        assert d['lat'] == 21.3
        assert d['lon'] == -157.8
        assert d['source'] == "manual"

    def test_from_dict(self):
        data = {'lat': 21.3, 'lon': -157.8, 'alt': 50.0, 'source': 'cached'}
        pos = Position.from_dict(data)
        assert pos.lat == 21.3
        assert pos.alt == 50.0

    def test_from_dict_ignores_unknown_fields(self):
        data = {'lat': 21.3, 'lon': -157.8, 'unknown_field': 'ignored'}
        pos = Position.from_dict(data)
        assert pos.lat == 21.3

    def test_roundtrip_dict(self):
        pos = Position(lat=21.3, lon=-157.8, alt=100.0,
                       speed=2.5, timestamp=1234567890.0, source="gpsd")
        restored = Position.from_dict(pos.to_dict())
        assert restored.lat == pos.lat
        assert restored.lon == pos.lon
        assert restored.alt == pos.alt
        assert restored.source == pos.source


# =============================================================================
# NodeDistance Tests
# =============================================================================


class TestNodeDistance:
    """Test NodeDistance calculations."""

    def test_creation(self):
        nd = NodeDistance(
            node_id="!abc123",
            node_name="Hilltop",
            distance_m=5000.0,
            bearing_deg=45.0,
            node_lat=21.4,
            node_lon=-157.7,
        )
        assert nd.node_id == "!abc123"
        assert nd.distance_m == 5000.0

    def test_distance_km(self):
        nd = NodeDistance("!a", "N", 5000.0, 0.0, 0.0, 0.0)
        assert nd.distance_km == 5.0

    def test_distance_display_meters(self):
        nd = NodeDistance("!a", "N", 500.0, 0.0, 0.0, 0.0)
        assert nd.distance_display == "500m"

    def test_distance_display_km_close(self):
        nd = NodeDistance("!a", "N", 2500.0, 0.0, 0.0, 0.0)
        assert nd.distance_display == "2.50km"

    def test_distance_display_km_far(self):
        nd = NodeDistance("!a", "N", 50000.0, 0.0, 0.0, 0.0)
        assert nd.distance_display == "50.0km"

    def test_cardinal_direction_north(self):
        nd = NodeDistance("!a", "N", 1000.0, 0.0, 0.0, 0.0)
        assert nd.cardinal_direction == "N"

    def test_cardinal_direction_east(self):
        nd = NodeDistance("!a", "N", 1000.0, 90.0, 0.0, 0.0)
        assert nd.cardinal_direction == "E"

    def test_cardinal_direction_south(self):
        nd = NodeDistance("!a", "N", 1000.0, 180.0, 0.0, 0.0)
        assert nd.cardinal_direction == "S"

    def test_cardinal_direction_west(self):
        nd = NodeDistance("!a", "N", 1000.0, 270.0, 0.0, 0.0)
        assert nd.cardinal_direction == "W"

    def test_cardinal_direction_northeast(self):
        nd = NodeDistance("!a", "N", 1000.0, 45.0, 0.0, 0.0)
        assert nd.cardinal_direction == "NE"

    def test_cardinal_direction_southwest(self):
        nd = NodeDistance("!a", "N", 1000.0, 225.0, 0.0, 0.0)
        assert nd.cardinal_direction == "SW"

    def test_cardinal_wrap_360(self):
        nd = NodeDistance("!a", "N", 1000.0, 350.0, 0.0, 0.0)
        # 350 degrees is close to North
        assert nd.cardinal_direction in ("N", "NNW")


# =============================================================================
# Distance Calculation Tests
# =============================================================================


class TestHaversineDistance:
    """Test haversine distance calculations."""

    def test_same_point_zero_distance(self):
        d = haversine_distance(21.3, -157.8, 21.3, -157.8)
        assert d == pytest.approx(0.0, abs=0.01)

    def test_honolulu_to_maui(self):
        # Honolulu to Kahului, Maui ~approx 160-170 km
        d = haversine_distance(21.3069, -157.8583, 20.8893, -156.4729)
        assert 150000 < d < 180000  # meters

    def test_equator_one_degree_lon(self):
        # 1 degree longitude at equator ~ 111.32 km
        d = haversine_distance(0.0, 0.0, 0.0, 1.0)
        assert 110000 < d < 112000

    def test_equator_one_degree_lat(self):
        # 1 degree latitude ~ 111.0 km
        d = haversine_distance(0.0, 0.0, 1.0, 0.0)
        assert 110000 < d < 112000

    def test_sf_to_la(self):
        # San Francisco to Los Angeles ~559 km
        d = haversine_distance(37.7749, -122.4194, 34.0522, -118.2437)
        assert 550000 < d < 570000

    def test_antipodal_points(self):
        # Opposite sides of Earth ~ 20015 km
        d = haversine_distance(0.0, 0.0, 0.0, 180.0)
        assert 20000000 < d < 20100000

    def test_short_distance(self):
        # ~100 meters apart
        d = haversine_distance(21.3069, -157.8583, 21.3078, -157.8583)
        assert 90 < d < 110


# =============================================================================
# Bearing Calculation Tests
# =============================================================================


class TestInitialBearing:
    """Test bearing calculations."""

    def test_north(self):
        # Due north
        b = initial_bearing(0.0, 0.0, 1.0, 0.0)
        assert b == pytest.approx(0.0, abs=0.1)

    def test_east(self):
        # Due east at equator
        b = initial_bearing(0.0, 0.0, 0.0, 1.0)
        assert b == pytest.approx(90.0, abs=0.1)

    def test_south(self):
        # Due south
        b = initial_bearing(1.0, 0.0, 0.0, 0.0)
        assert b == pytest.approx(180.0, abs=0.1)

    def test_west(self):
        # Due west at equator
        b = initial_bearing(0.0, 1.0, 0.0, 0.0)
        assert b == pytest.approx(270.0, abs=0.1)

    def test_northeast(self):
        b = initial_bearing(0.0, 0.0, 1.0, 1.0)
        assert 40 < b < 50  # ~45 degrees

    def test_bearing_range(self):
        # All bearings should be 0-360
        for lat2 in range(-90, 91, 30):
            for lon2 in range(-180, 180, 45):
                b = initial_bearing(0.0, 0.0, float(lat2), float(lon2))
                assert 0.0 <= b < 360.0

    def test_same_point(self):
        # Same point: bearing is 0 by convention
        b = initial_bearing(21.3, -157.8, 21.3, -157.8)
        assert 0.0 <= b < 360.0  # Just needs to be valid


# =============================================================================
# GPSReader Tests
# =============================================================================


class TestGPSReader:
    """Test gpsd protocol handling."""

    def test_creation(self):
        reader = GPSReader()
        assert reader._host == GPSD_HOST
        assert reader._port == GPSD_PORT

    def test_custom_config(self):
        reader = GPSReader(host="192.168.1.1", port=3000, timeout=5.0)
        assert reader._host == "192.168.1.1"
        assert reader._port == 3000

    def test_parse_tpv_3d_fix(self):
        reader = GPSReader()
        tpv = json.dumps({
            "class": "TPV",
            "mode": 3,
            "lat": 21.3069,
            "lon": -157.8583,
            "altMSL": 100.0,
            "speed": 2.5,
            "track": 45.0,
            "epx": 5.0,
        })
        pos = reader._parse_tpv(tpv)
        assert pos is not None
        assert pos.lat == 21.3069
        assert pos.lon == -157.8583
        assert pos.alt == 100.0
        assert pos.speed == 2.5
        assert pos.heading == 45.0
        assert pos.accuracy == 5.0
        assert pos.source == "gpsd"

    def test_parse_tpv_2d_fix(self):
        reader = GPSReader()
        tpv = json.dumps({
            "class": "TPV",
            "mode": 2,
            "lat": 21.3,
            "lon": -157.8,
        })
        pos = reader._parse_tpv(tpv)
        assert pos is not None
        assert pos.lat == 21.3
        assert pos.alt is None

    def test_parse_tpv_no_fix(self):
        reader = GPSReader()
        tpv = json.dumps({"class": "TPV", "mode": 1})
        pos = reader._parse_tpv(tpv)
        assert pos is None

    def test_parse_tpv_mode_0(self):
        reader = GPSReader()
        tpv = json.dumps({"class": "TPV", "mode": 0})
        pos = reader._parse_tpv(tpv)
        assert pos is None

    def test_parse_non_tpv_class(self):
        reader = GPSReader()
        sky = json.dumps({"class": "SKY", "satellites": []})
        pos = reader._parse_tpv(sky)
        assert pos is None

    def test_parse_invalid_json(self):
        reader = GPSReader()
        pos = reader._parse_tpv("not json")
        assert pos is None

    def test_parse_tpv_missing_lat(self):
        reader = GPSReader()
        tpv = json.dumps({"class": "TPV", "mode": 3, "lon": -157.8})
        pos = reader._parse_tpv(tpv)
        assert pos is None

    def test_parse_tpv_missing_lon(self):
        reader = GPSReader()
        tpv = json.dumps({"class": "TPV", "mode": 3, "lat": 21.3})
        pos = reader._parse_tpv(tpv)
        assert pos is None

    def test_parse_tpv_alt_fallback(self):
        """altMSL preferred, falls back to alt."""
        reader = GPSReader()
        tpv = json.dumps({
            "class": "TPV", "mode": 3,
            "lat": 21.3, "lon": -157.8,
            "alt": 50.0
        })
        pos = reader._parse_tpv(tpv)
        assert pos.alt == 50.0

    @patch('socket.socket')
    def test_is_available_true(self, mock_socket_cls):
        mock_sock = MagicMock()
        mock_sock.connect_ex.return_value = 0
        mock_socket_cls.return_value.__enter__ = MagicMock(return_value=mock_sock)
        mock_socket_cls.return_value.__exit__ = MagicMock(return_value=False)

        reader = GPSReader()
        assert reader.is_available is True

    @patch('socket.socket')
    def test_is_available_false(self, mock_socket_cls):
        mock_sock = MagicMock()
        mock_sock.connect_ex.return_value = 1
        mock_socket_cls.return_value.__enter__ = MagicMock(return_value=mock_sock)
        mock_socket_cls.return_value.__exit__ = MagicMock(return_value=False)

        reader = GPSReader()
        assert reader.is_available is False

    @patch('socket.socket')
    def test_read_position_connection_refused(self, mock_socket_cls):
        mock_sock = MagicMock()
        mock_sock.connect.side_effect = ConnectionRefusedError()
        mock_socket_cls.return_value.__enter__ = MagicMock(return_value=mock_sock)
        mock_socket_cls.return_value.__exit__ = MagicMock(return_value=False)

        reader = GPSReader()
        pos = reader.read_position()
        assert pos is None


# =============================================================================
# GPSManager Tests
# =============================================================================


class TestGPSManagerCreation:
    """Test GPS manager initialization."""

    def test_creation_with_path(self, tmp_path):
        config_path = tmp_path / "gps.json"
        gps = GPSManager(config_path=config_path)
        assert gps._config_path == config_path

    def test_loads_cached_position(self, tmp_path):
        config_path = tmp_path / "gps.json"
        config_path.write_text(json.dumps({
            'lat': 21.3, 'lon': -157.8,
            'timestamp': time.time(), 'source': 'manual'
        }))
        gps = GPSManager(config_path=config_path)
        assert gps.has_position is True
        pos = gps.get_position()
        assert pos.lat == 21.3
        assert pos.source == "cached"

    def test_handles_missing_cache(self, tmp_path):
        config_path = tmp_path / "nonexistent.json"
        gps = GPSManager(config_path=config_path)
        assert gps.has_position is False

    def test_handles_corrupt_cache(self, tmp_path):
        config_path = tmp_path / "gps.json"
        config_path.write_text("not json!!!")
        gps = GPSManager(config_path=config_path)
        assert gps.has_position is False


class TestGPSManagerManualPosition:
    """Test manual position setting."""

    def test_set_manual_position(self, tmp_path):
        gps = GPSManager(config_path=tmp_path / "gps.json")
        pos = gps.set_manual_position(21.3069, -157.8583)
        assert pos.lat == 21.3069
        assert pos.lon == -157.8583
        assert pos.source == "manual"

    def test_set_manual_with_alt(self, tmp_path):
        gps = GPSManager(config_path=tmp_path / "gps.json")
        pos = gps.set_manual_position(21.3, -157.8, alt=50.0)
        assert pos.alt == 50.0

    def test_set_manual_persists(self, tmp_path):
        config_path = tmp_path / "gps.json"
        gps = GPSManager(config_path=config_path)
        gps.set_manual_position(21.3, -157.8)
        assert config_path.exists()

        data = json.loads(config_path.read_text())
        assert data['lat'] == 21.3
        assert data['lon'] == -157.8

    def test_set_manual_invalid_lat(self, tmp_path):
        gps = GPSManager(config_path=tmp_path / "gps.json")
        with pytest.raises(ValueError):
            gps.set_manual_position(91.0, 0.0)

    def test_set_manual_invalid_lat_negative(self, tmp_path):
        gps = GPSManager(config_path=tmp_path / "gps.json")
        with pytest.raises(ValueError):
            gps.set_manual_position(-91.0, 0.0)

    def test_set_manual_invalid_lon(self, tmp_path):
        gps = GPSManager(config_path=tmp_path / "gps.json")
        with pytest.raises(ValueError):
            gps.set_manual_position(0.0, 181.0)

    def test_set_manual_boundary_values(self, tmp_path):
        gps = GPSManager(config_path=tmp_path / "gps.json")
        # These should work (boundary values are valid)
        gps.set_manual_position(90.0, 180.0)
        gps.set_manual_position(-90.0, -180.0)


class TestGPSManagerGetPosition:
    """Test position retrieval."""

    def test_get_position_no_gps_no_cache(self, tmp_path):
        gps = GPSManager(config_path=tmp_path / "gps.json")
        with patch.object(gps._reader, 'read_position', return_value=None):
            pos = gps.get_position()
        assert pos is None

    def test_get_position_from_gpsd(self, tmp_path):
        gps = GPSManager(config_path=tmp_path / "gps.json")
        gps_pos = Position(lat=21.3, lon=-157.8,
                           timestamp=time.time(), source="gpsd")
        with patch.object(gps._reader, 'read_position', return_value=gps_pos):
            pos = gps.get_position(force_refresh=True)
        assert pos.source == "gpsd"
        assert pos.lat == 21.3

    def test_get_position_caches(self, tmp_path):
        gps = GPSManager(config_path=tmp_path / "gps.json")
        gps.set_manual_position(21.3, -157.8)

        # Should use cache without calling reader
        with patch.object(gps._reader, 'read_position') as mock_read:
            pos = gps.get_position()
            mock_read.assert_not_called()
        assert pos.lat == 21.3

    def test_get_position_force_refresh(self, tmp_path):
        gps = GPSManager(config_path=tmp_path / "gps.json")
        gps.set_manual_position(21.3, -157.8)

        new_pos = Position(lat=21.4, lon=-157.9,
                           timestamp=time.time(), source="gpsd")
        with patch.object(gps._reader, 'read_position', return_value=new_pos):
            pos = gps.get_position(force_refresh=True)
        assert pos.lat == 21.4

    def test_get_position_falls_back_to_cache(self, tmp_path):
        config_path = tmp_path / "gps.json"
        config_path.write_text(json.dumps({
            'lat': 21.3, 'lon': -157.8,
            'timestamp': time.time() - 60, 'source': 'manual'
        }))
        gps = GPSManager(config_path=config_path)

        with patch.object(gps._reader, 'read_position', return_value=None):
            gps._last_read = 0  # Expire cache
            pos = gps.get_position()
        assert pos is not None
        assert pos.lat == 21.3

    def test_get_position_rejects_invalid_gps(self, tmp_path):
        gps = GPSManager(config_path=tmp_path / "gps.json")
        bad_pos = Position(lat=999.0, lon=999.0,
                           timestamp=time.time(), source="gpsd")
        with patch.object(gps._reader, 'read_position', return_value=bad_pos):
            pos = gps.get_position(force_refresh=True)
        # Should fall back (invalid GPS rejected)
        assert pos is None


class TestGPSManagerDistances:
    """Test distance-to-nodes calculations."""

    def test_distances_empty_list(self, tmp_path):
        gps = GPSManager(config_path=tmp_path / "gps.json")
        gps.set_manual_position(21.3, -157.8)
        distances = gps.distances_to_nodes([])
        assert distances == []

    def test_distances_no_position(self, tmp_path):
        gps = GPSManager(config_path=tmp_path / "gps.json")
        nodes = [{"id": "!abc", "lat": 21.4, "lon": -157.7}]
        distances = gps.distances_to_nodes(nodes)
        assert distances == []

    def test_distances_single_node(self, tmp_path):
        gps = GPSManager(config_path=tmp_path / "gps.json")
        gps.set_manual_position(21.3069, -157.8583)

        nodes = [{"id": "!abc123", "name": "Hilltop", "lat": 21.4, "lon": -157.7}]
        distances = gps.distances_to_nodes(nodes)
        assert len(distances) == 1
        assert distances[0].node_id == "!abc123"
        assert distances[0].distance_m > 0

    def test_distances_sorted_by_distance(self, tmp_path):
        gps = GPSManager(config_path=tmp_path / "gps.json")
        gps.set_manual_position(21.3, -157.8)

        nodes = [
            {"id": "!far", "lat": 22.0, "lon": -157.0},  # Far
            {"id": "!near", "lat": 21.31, "lon": -157.81},  # Near
            {"id": "!mid", "lat": 21.5, "lon": -157.5},  # Middle
        ]
        distances = gps.distances_to_nodes(nodes)
        assert distances[0].node_id == "!near"
        assert distances[-1].node_id == "!far"
        assert distances[0].distance_m < distances[1].distance_m
        assert distances[1].distance_m < distances[2].distance_m

    def test_distances_skips_nodes_without_position(self, tmp_path):
        gps = GPSManager(config_path=tmp_path / "gps.json")
        gps.set_manual_position(21.3, -157.8)

        nodes = [
            {"id": "!a", "lat": 21.4, "lon": -157.7},
            {"id": "!b"},  # No lat/lon
            {"id": "!c", "lat": None, "lon": None},
        ]
        distances = gps.distances_to_nodes(nodes)
        assert len(distances) == 1
        assert distances[0].node_id == "!a"

    def test_distances_uses_name_or_id(self, tmp_path):
        gps = GPSManager(config_path=tmp_path / "gps.json")
        gps.set_manual_position(21.3, -157.8)

        nodes = [
            {"id": "!a", "name": "Named", "lat": 21.4, "lon": -157.7},
            {"id": "!b", "lat": 21.5, "lon": -157.6},  # No name
        ]
        distances = gps.distances_to_nodes(nodes)
        assert distances[0].node_name == "Named"
        assert distances[1].node_name == "!b"  # Falls back to ID


class TestGPSManagerReport:
    """Test position report formatting."""

    def test_report_no_position(self, tmp_path):
        gps = GPSManager(config_path=tmp_path / "gps.json")
        report = gps.format_position_report()
        assert "No position available" in report

    def test_report_with_position(self, tmp_path):
        gps = GPSManager(config_path=tmp_path / "gps.json")
        gps.set_manual_position(21.3069, -157.8583)
        report = gps.format_position_report()
        assert "21.306900" in report
        assert "-157.858300" in report
        assert "manual" in report

    def test_report_with_altitude(self, tmp_path):
        gps = GPSManager(config_path=tmp_path / "gps.json")
        gps.set_manual_position(21.3, -157.8, alt=100.0)
        report = gps.format_position_report()
        assert "Altitude" in report
        assert "100" in report

    def test_report_with_nodes(self, tmp_path):
        gps = GPSManager(config_path=tmp_path / "gps.json")
        gps.set_manual_position(21.3, -157.8)

        nodes = [
            {"id": "!abc", "name": "Hilltop", "lat": 21.4, "lon": -157.7},
        ]
        report = gps.format_position_report(nodes=nodes)
        assert "Hilltop" in report
        assert "Distance" in report

    def test_report_stale_warning(self, tmp_path):
        config_path = tmp_path / "gps.json"
        config_path.write_text(json.dumps({
            'lat': 21.3, 'lon': -157.8,
            'timestamp': time.time() - POSITION_STALE_SEC - 100,
            'source': 'manual'
        }))
        gps = GPSManager(config_path=config_path)
        report = gps.format_position_report()
        assert "stale" in report.lower() or "WARNING" in report


class TestGPSManagerProperties:
    """Test manager properties."""

    def test_has_position_false(self, tmp_path):
        gps = GPSManager(config_path=tmp_path / "gps.json")
        assert gps.has_position is False

    def test_has_position_true(self, tmp_path):
        gps = GPSManager(config_path=tmp_path / "gps.json")
        gps.set_manual_position(21.3, -157.8)
        assert gps.has_position is True

    @patch('socket.socket')
    def test_gpsd_available(self, mock_socket_cls, tmp_path):
        mock_sock = MagicMock()
        mock_sock.connect_ex.return_value = 0
        mock_socket_cls.return_value.__enter__ = MagicMock(return_value=mock_sock)
        mock_socket_cls.return_value.__exit__ = MagicMock(return_value=False)

        gps = GPSManager(config_path=tmp_path / "gps.json")
        assert gps.gpsd_available is True


# =============================================================================
# Edge Cases
# =============================================================================


class TestEdgeCases:
    """Test edge cases and error handling."""

    def test_distance_zero(self):
        """Same point should give ~0 distance."""
        d = haversine_distance(21.3, -157.8, 21.3, -157.8)
        assert d == pytest.approx(0.0, abs=0.01)

    def test_bearing_same_point(self):
        """Same point bearing should be valid (not NaN)."""
        b = initial_bearing(21.3, -157.8, 21.3, -157.8)
        assert not math.isnan(b)

    def test_distance_at_poles(self):
        """Distance calculations should work near poles."""
        d = haversine_distance(89.0, 0.0, 89.0, 90.0)
        assert d > 0

    def test_bearing_across_dateline(self):
        """Bearing should work across the international date line."""
        b = initial_bearing(0.0, 179.0, 0.0, -179.0)
        # Should be eastward (~90 degrees)
        assert 80 < b < 100

    def test_manager_concurrent_access(self, tmp_path):
        """Basic thread safety for position access."""
        import threading
        gps = GPSManager(config_path=tmp_path / "gps.json")
        gps.set_manual_position(21.3, -157.8)

        errors = []

        def read_position():
            try:
                for _ in range(50):
                    pos = gps.get_position()
                    if pos:
                        _ = pos.lat
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=read_position) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
