"""
Tests for tile_cache module.

Covers coordinate conversion, region enumeration, dateline crossing,
Mercator edge cases, and cache operations.
"""

import math
import os
import sys
import time
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from utils.tile_cache import (
    TileCache, BoundingBox,
    lon_to_tile_x, lat_to_tile_y, tile_to_lon, tile_to_lat,
    count_tiles_in_region, get_tiles_for_region,
    HAWAII_BOUNDS, MAX_TILES_PER_SESSION, MAX_TILE_BYTES,
    MAX_MERCATOR_LAT, DEFAULT_ZOOM_MIN, DEFAULT_ZOOM_MAX,
)


# =============================================================================
# BoundingBox Tests
# =============================================================================


class TestBoundingBox:
    """Test BoundingBox dataclass."""

    def test_from_tuple(self):
        bbox = BoundingBox.from_tuple((21.0, -158.5, 21.7, -157.5))
        assert bbox.south == 21.0
        assert bbox.west == -158.5
        assert bbox.north == 21.7
        assert bbox.east == -157.5

    def test_valid_box(self):
        bbox = BoundingBox(south=21.0, west=-158.5, north=21.7, east=-157.5)
        assert bbox.is_valid

    def test_invalid_south_gt_north(self):
        bbox = BoundingBox(south=22.0, west=-158.5, north=21.0, east=-157.5)
        assert not bbox.is_valid

    def test_invalid_lat_out_of_range(self):
        bbox = BoundingBox(south=-91.0, west=0, north=0, east=10)
        assert not bbox.is_valid

    def test_invalid_lon_out_of_range(self):
        bbox = BoundingBox(south=0, west=-181, north=10, east=10)
        assert not bbox.is_valid

    def test_dateline_crossing_is_valid(self):
        """Dateline crossing (west > east) should still be valid."""
        bbox = BoundingBox(south=-10, west=170, north=10, east=-170)
        assert bbox.is_valid


# =============================================================================
# Coordinate Conversion Tests
# =============================================================================


class TestCoordinateConversion:
    """Test lon/lat to tile and back."""

    def test_lon_to_tile_x_zero(self):
        """Longitude 0 should be at tile n/2."""
        x = lon_to_tile_x(0.0, 8)
        n = 2 ** 8
        assert x == n // 2

    def test_lon_to_tile_x_neg180(self):
        """Longitude -180 should be tile 0."""
        x = lon_to_tile_x(-180.0, 8)
        assert x == 0

    def test_lon_to_tile_x_wraps(self):
        """Tile X should wrap around at 180 degrees."""
        x = lon_to_tile_x(180.0, 8)
        # At exactly 180, wraps to 0
        assert x == 0

    def test_lat_to_tile_y_equator(self):
        """Equator should be at tile n/2."""
        y = lat_to_tile_y(0.0, 8)
        n = 2 ** 8
        assert y == n // 2

    def test_lat_to_tile_y_north_is_lower(self):
        """Higher latitude should have lower Y (y increases southward)."""
        y_north = lat_to_tile_y(45.0, 8)
        y_south = lat_to_tile_y(-45.0, 8)
        assert y_north < y_south

    def test_tile_to_lon_roundtrip(self):
        """tile_to_lon(lon_to_tile_x(lon)) should be close to lon."""
        lon = -157.8
        x = lon_to_tile_x(lon, 12)
        recovered = tile_to_lon(x, 12)
        # Should be within one tile width
        tile_width = 360.0 / (2 ** 12)
        assert abs(recovered - lon) < tile_width

    def test_tile_to_lat_roundtrip(self):
        """tile_to_lat(lat_to_tile_y(lat)) should be close to lat."""
        lat = 21.3
        y = lat_to_tile_y(lat, 12)
        recovered = tile_to_lat(y, 12)
        # Should be within one tile height (varies by latitude)
        assert abs(recovered - lat) < 0.1

    def test_hawaii_coordinates(self):
        """Hawaii tile coordinates should be reasonable."""
        x = lon_to_tile_x(-157.8, 8)
        y = lat_to_tile_y(21.3, 8)
        n = 2 ** 8  # 256
        # Hawaii is at ~-157.8 lon: (180-157.8)/360*256 ~ 15
        assert 0 <= x < n
        # Latitude 21.3 is in the tropics, Y should be in lower half of map
        assert n // 4 < y < 3 * n // 4


# =============================================================================
# Region Enumeration Tests
# =============================================================================


class TestRegionEnumeration:
    """Test tile counting and enumeration for regions."""

    def test_count_tiles_basic(self):
        """Counting tiles for a small region should be reasonable."""
        bbox = BoundingBox(south=21.0, west=-158.5, north=21.7, east=-157.5)
        count = count_tiles_in_region(bbox, 8, 8)
        # At zoom 8, a 0.7x1.0 degree box is about 1-2 tiles
        assert 1 <= count <= 4

    def test_count_tiles_increases_with_zoom(self):
        """Higher zoom should have more tiles."""
        bbox = BoundingBox(south=21.0, west=-158.5, north=21.7, east=-157.5)
        count_8 = count_tiles_in_region(bbox, 8, 8)
        count_12 = count_tiles_in_region(bbox, 12, 12)
        assert count_12 > count_8

    def test_count_tiles_multi_zoom(self):
        """Multi-zoom count should be sum of individual zoom counts."""
        bbox = BoundingBox(south=21.0, west=-158.5, north=21.7, east=-157.5)
        total = count_tiles_in_region(bbox, 8, 10)
        sum_individual = sum(
            count_tiles_in_region(bbox, z, z) for z in range(8, 11)
        )
        assert total == sum_individual

    def test_get_tiles_returns_correct_zoom(self):
        """All returned tiles should have the requested zoom level."""
        bbox = BoundingBox(south=21.0, west=-158.5, north=21.7, east=-157.5)
        tiles = get_tiles_for_region(bbox, 10)
        for z, x, y in tiles:
            assert z == 10

    def test_get_tiles_count_matches(self):
        """get_tiles_for_region count should match count_tiles_in_region."""
        bbox = BoundingBox(south=21.0, west=-158.5, north=21.7, east=-157.5)
        tiles = get_tiles_for_region(bbox, 10)
        count = count_tiles_in_region(bbox, 10, 10)
        assert len(tiles) == count

    def test_hawaii_bounds_reasonable(self):
        """Hawaii bounds at low zoom should produce a manageable tile count."""
        bbox = BoundingBox.from_tuple(HAWAII_BOUNDS)
        # Use zoom 8-12 (not full range to 14) for a reasonable count
        count = count_tiles_in_region(bbox, 8, 12)
        assert count < MAX_TILES_PER_SESSION

    def test_hawaii_bounds_full_zoom_large(self):
        """Hawaii at full zoom range exceeds session limit (expected)."""
        bbox = BoundingBox.from_tuple(HAWAII_BOUNDS)
        count = count_tiles_in_region(bbox, DEFAULT_ZOOM_MIN, DEFAULT_ZOOM_MAX)
        # Full zoom 8-14 for 4x6 degree area is large
        assert count > MAX_TILES_PER_SESSION


# =============================================================================
# Edge Case Tests (from code review)
# =============================================================================


class TestMercatorEdgeCases:
    """Test Mercator projection edge cases."""

    def test_lat_to_tile_y_near_north_pole(self):
        """Should not raise at extreme north latitude."""
        y = lat_to_tile_y(89.99, 8)
        assert y >= 0

    def test_lat_to_tile_y_near_south_pole(self):
        """Should not raise at extreme south latitude."""
        y = lat_to_tile_y(-89.99, 8)
        n = 2 ** 8
        assert y < n

    def test_lat_to_tile_y_at_mercator_limit(self):
        """Should handle exactly the Mercator limit."""
        y = lat_to_tile_y(MAX_MERCATOR_LAT, 8)
        assert y == 0

    def test_lat_to_tile_y_beyond_mercator_limit(self):
        """Should clamp and not raise for latitudes beyond Mercator limit."""
        y1 = lat_to_tile_y(86.0, 8)
        y2 = lat_to_tile_y(90.0, 8)
        # Both should be clamped to the same value
        assert y1 == y2

    def test_lat_to_tile_y_exactly_90(self):
        """Should not raise for exactly 90 degrees."""
        y = lat_to_tile_y(90.0, 12)
        assert y >= 0

    def test_lat_to_tile_y_exactly_minus_90(self):
        """Should not raise for exactly -90 degrees."""
        y = lat_to_tile_y(-90.0, 12)
        assert y >= 0


class TestDatelineCrossing:
    """Test dateline-crossing bounding boxes."""

    def test_count_tiles_dateline_crossing(self):
        """Dateline-crossing region should still count tiles correctly."""
        # Box crossing the dateline: 170E to 170W
        bbox = BoundingBox(south=-10, west=170, north=10, east=-170)
        count = count_tiles_in_region(bbox, 8, 8)
        # Should be a small number of tiles (not negative or zero)
        assert count > 0

    def test_get_tiles_dateline_crossing(self):
        """Tiles for dateline-crossing region should wrap correctly."""
        bbox = BoundingBox(south=-10, west=170, north=10, east=-170)
        tiles = get_tiles_for_region(bbox, 4)
        assert len(tiles) > 0
        # All tile x coords should be valid (0 to 2^z - 1)
        n = 2 ** 4
        for z, x, y in tiles:
            assert 0 <= x < n
            assert 0 <= y < n

    def test_dateline_count_equals_tile_list(self):
        """count_tiles should match len(get_tiles) for dateline box."""
        bbox = BoundingBox(south=-5, west=175, north=5, east=-175)
        count = count_tiles_in_region(bbox, 6, 6)
        tiles = get_tiles_for_region(bbox, 6)
        assert count == len(tiles)

    def test_non_crossing_larger_than_crossing(self):
        """A full-width box should have more tiles than a dateline-crossing box."""
        full = BoundingBox(south=-10, west=-170, north=10, east=170)
        crossing = BoundingBox(south=-10, west=170, north=10, east=-170)
        full_count = count_tiles_in_region(full, 4, 4)
        crossing_count = count_tiles_in_region(crossing, 4, 4)
        assert full_count > crossing_count


class TestZoomValidation:
    """Test zoom range validation in download and estimate."""

    def test_download_region_invalid_zoom_min(self):
        """Negative zoom min should return error."""
        cache = TileCache(cache_dir=Path('/tmp/test_tiles'))
        result = cache.download_region(HAWAII_BOUNDS, zoom_range=(-1, 10))
        assert 'error' in result

    def test_download_region_invalid_zoom_max(self):
        """Zoom max > 19 should return error."""
        cache = TileCache(cache_dir=Path('/tmp/test_tiles'))
        result = cache.download_region(HAWAII_BOUNDS, zoom_range=(8, 25))
        assert 'error' in result

    def test_download_region_zoom_min_gt_max(self):
        """Zoom min > max should return error."""
        cache = TileCache(cache_dir=Path('/tmp/test_tiles'))
        result = cache.download_region(HAWAII_BOUNDS, zoom_range=(14, 8))
        assert 'error' in result

    def test_estimate_invalid_zoom(self):
        """Invalid zoom should return zero estimates."""
        result = TileCache.estimate_download_size(HAWAII_BOUNDS, zoom_range=(-1, 25))
        assert result['total_tiles'] == 0

    def test_estimate_valid(self):
        """Valid estimate should produce reasonable results."""
        result = TileCache.estimate_download_size(HAWAII_BOUNDS, zoom_range=(8, 10))
        assert result['total_tiles'] > 0
        assert result['estimated_mb'] > 0
        assert result['within_limit'] is True


class TestTileCacheOperations:
    """Test TileCache class operations (filesystem-based)."""

    def test_get_tile_path_not_cached(self):
        """Non-existent tile should return None."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache = TileCache(cache_dir=Path(tmpdir))
            assert cache.get_tile_path(8, 100, 100) is None

    def test_get_tile_path_cached(self):
        """Existing tile should return path."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache = TileCache(cache_dir=Path(tmpdir))
            tile_path = Path(tmpdir) / "openstreetmap" / "8" / "100" / "100.png"
            tile_path.parent.mkdir(parents=True)
            tile_path.write_bytes(b'\x89PNG' + b'\x00' * 200)
            result = cache.get_tile_path(8, 100, 100)
            assert result == tile_path

    def test_get_stats_empty(self):
        """Empty cache should return zero stats."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache = TileCache(cache_dir=Path(tmpdir))
            stats = cache.get_stats()
            assert stats['tile_count'] == 0
            assert stats['total_bytes'] == 0
            assert stats['oldest'] is None

    def test_get_stats_with_tiles(self):
        """Cache with tiles should report correct count and size."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache = TileCache(cache_dir=Path(tmpdir))
            # Create 3 fake tiles
            for i in range(3):
                tile_path = Path(tmpdir) / "openstreetmap" / "8" / str(i) / "0.png"
                tile_path.parent.mkdir(parents=True)
                tile_path.write_bytes(b'\x89PNG' + b'\x00' * 500)
            stats = cache.get_stats()
            assert stats['tile_count'] == 3
            assert stats['total_bytes'] == 3 * 504  # 4 header + 500 body

    def test_clear_expired_removes_old(self):
        """Expired tiles should be removed."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache = TileCache(cache_dir=Path(tmpdir))
            tile_path = Path(tmpdir) / "openstreetmap" / "8" / "0" / "0.png"
            tile_path.parent.mkdir(parents=True)
            tile_path.write_bytes(b'\x89PNG' + b'\x00' * 200)
            # Set mtime to 60 days ago
            old_time = time.time() - (60 * 86400)
            os.utime(tile_path, (old_time, old_time))
            result = cache.clear_expired(max_age_days=30)
            assert result['removed'] == 1
            assert not tile_path.exists()

    def test_clear_expired_keeps_recent(self):
        """Recent tiles should not be removed."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache = TileCache(cache_dir=Path(tmpdir))
            tile_path = Path(tmpdir) / "openstreetmap" / "8" / "0" / "0.png"
            tile_path.parent.mkdir(parents=True)
            tile_path.write_bytes(b'\x89PNG' + b'\x00' * 200)
            result = cache.clear_expired(max_age_days=30)
            assert result['removed'] == 0
            assert tile_path.exists()

    def test_download_region_invalid_bbox(self):
        """Invalid bounding box should return error."""
        cache = TileCache(cache_dir=Path('/tmp/test_tiles'))
        result = cache.download_region((50, 0, 10, 20))  # south > north
        assert 'error' in result
        assert result['downloaded'] == 0

    def test_download_region_too_many_tiles(self):
        """Region with too many tiles should return error."""
        cache = TileCache(cache_dir=Path('/tmp/test_tiles'))
        # Entire world at high zoom
        result = cache.download_region((-80, -180, 80, 180), zoom_range=(1, 18))
        assert 'error' in result


class TestDownloadTile:
    """Test _download_tile with mocked network."""

    def test_download_success(self):
        """Successful download should write tile to cache."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache = TileCache(cache_dir=Path(tmpdir), rate_limit=0.0)
            fake_data = b'\x89PNG' + b'\x00' * 500

            with patch('utils.tile_cache.urlopen') as mock_urlopen:
                mock_response = MagicMock()
                mock_response.read.return_value = fake_data
                mock_response.__enter__ = MagicMock(return_value=mock_response)
                mock_response.__exit__ = MagicMock(return_value=False)
                mock_urlopen.return_value = mock_response

                result = cache._download_tile(8, 100, 100)
                assert result is True
                tile_path = Path(tmpdir) / "openstreetmap" / "8" / "100" / "100.png"
                assert tile_path.exists()
                assert tile_path.read_bytes() == fake_data

    def test_download_too_large(self):
        """Oversized response should be rejected."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache = TileCache(cache_dir=Path(tmpdir), rate_limit=0.0)
            # Response larger than MAX_TILE_BYTES
            fake_data = b'\x00' * (MAX_TILE_BYTES + 2)

            with patch('utils.tile_cache.urlopen') as mock_urlopen:
                mock_response = MagicMock()
                mock_response.read.return_value = fake_data
                mock_response.__enter__ = MagicMock(return_value=mock_response)
                mock_response.__exit__ = MagicMock(return_value=False)
                mock_urlopen.return_value = mock_response

                result = cache._download_tile(8, 100, 100)
                assert result is False

    def test_download_network_error(self):
        """Network error should return False, not raise."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache = TileCache(cache_dir=Path(tmpdir), rate_limit=0.0)

            with patch('utils.tile_cache.urlopen', side_effect=OSError("timeout")):
                result = cache._download_tile(8, 100, 100)
                assert result is False

    def test_download_skips_cached(self):
        """Already-cached tile should be skipped (returns True)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache = TileCache(cache_dir=Path(tmpdir), rate_limit=0.0)
            # Pre-create tile
            tile_path = Path(tmpdir) / "openstreetmap" / "8" / "100" / "100.png"
            tile_path.parent.mkdir(parents=True)
            tile_path.write_bytes(b'\x89PNG' + b'\x00' * 200)

            # Should not call urlopen
            with patch('utils.tile_cache.urlopen') as mock_urlopen:
                result = cache._download_tile(8, 100, 100)
                assert result is True
                mock_urlopen.assert_not_called()


class TestEstimateDownloadSize:
    """Test download size estimation."""

    def test_estimate_hawaii(self):
        """Hawaii estimate at moderate zoom should be reasonable."""
        result = TileCache.estimate_download_size(HAWAII_BOUNDS, zoom_range=(8, 12))
        assert result['total_tiles'] > 0
        assert result['within_limit'] is True
        assert 8 in result['per_zoom']
        assert 12 in result['per_zoom']

    def test_estimate_hawaii_full_zoom_exceeds_limit(self):
        """Hawaii at full zoom range should exceed session limit."""
        result = TileCache.estimate_download_size(HAWAII_BOUNDS)
        assert result['total_tiles'] > 0
        assert result['within_limit'] is False

    def test_estimate_invalid_bounds(self):
        """Invalid bounds should return zero."""
        result = TileCache.estimate_download_size((50, 0, 10, 20))
        assert result['total_tiles'] == 0

    def test_estimate_per_zoom_sums_to_total(self):
        """Sum of per_zoom counts should equal total_tiles."""
        result = TileCache.estimate_download_size(HAWAII_BOUNDS, zoom_range=(8, 12))
        assert sum(result['per_zoom'].values()) == result['total_tiles']
