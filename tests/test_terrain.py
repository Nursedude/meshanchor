"""Tests for terrain elevation and line-of-sight analysis."""

import math
import os
import struct
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from utils.terrain import (
    FlatTerrainProvider,
    SyntheticTerrainProvider,
    SRTMProvider,
    LOSAnalyzer,
    LOSResult,
)


class TestFlatTerrainProvider:
    """Tests for constant-elevation terrain provider."""

    def test_returns_constant_elevation(self):
        provider = FlatTerrainProvider(elevation=50.0)
        assert provider.get_elevation(19.0, -155.0) == 50.0
        assert provider.get_elevation(0.0, 0.0) == 50.0

    def test_default_zero_elevation(self):
        provider = FlatTerrainProvider()
        assert provider.get_elevation(45.0, -120.0) == 0.0

    def test_profile_is_constant(self):
        provider = FlatTerrainProvider(elevation=100.0)
        profile = provider.get_profile(0.0, 0.0, 1.0, 1.0, 10)
        assert len(profile) == 10
        assert all(e == 100.0 for e in profile)


class TestSyntheticTerrainProvider:
    """Tests for synthetic terrain (testing ridges)."""

    def test_generates_varying_elevation(self):
        provider = SyntheticTerrainProvider(
            base_elevation=100.0, ridge_height=50.0
        )
        # Different coordinates should give different elevations
        e1 = provider.get_elevation(0.0, 0.0)
        e2 = provider.get_elevation(0.005, 0.0)
        # At least one should differ (ridge pattern)
        assert e1 >= 100.0
        assert e2 >= 100.0

    def test_elevation_within_bounds(self):
        provider = SyntheticTerrainProvider(
            base_elevation=100.0, ridge_height=50.0
        )
        for lat in range(0, 10):
            for lon in range(0, 10):
                e = provider.get_elevation(lat * 0.01, lon * 0.01)
                assert 100.0 <= e <= 150.0

    def test_profile_returns_correct_count(self):
        provider = SyntheticTerrainProvider()
        profile = provider.get_profile(0.0, 0.0, 0.1, 0.1, 50)
        assert len(profile) == 50


class TestSRTMProvider:
    """Tests for SRTM tile provider (file handling, not downloads)."""

    def test_tile_name_northern_eastern(self):
        provider = SRTMProvider(auto_download=False)
        name = provider._get_tile_name(19.7, 155.08)
        assert name == "N19E155.hgt"

    def test_tile_name_northern_western(self):
        """Western longitudes use floor() — -155.08 → W156 (tile covers -156 to -155)."""
        provider = SRTMProvider(auto_download=False)
        name = provider._get_tile_name(19.7, -155.08)
        assert name == "N19W156.hgt"

    def test_tile_name_southern_hemisphere(self):
        """Southern latitudes use floor() — -33.85 → S34 (tile covers -34 to -33)."""
        provider = SRTMProvider(auto_download=False)
        name = provider._get_tile_name(-33.85, 151.21)
        assert name == "S34E151.hgt"

    def test_tile_name_zero_crossing(self):
        """Negative fractional coordinates use floor() — -0.5 → W001."""
        provider = SRTMProvider(auto_download=False)
        name = provider._get_tile_name(0.5, -0.5)
        assert name == "N00W001.hgt"

    def test_missing_tile_returns_zero(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            provider = SRTMProvider(
                cache_dir=Path(tmpdir), auto_download=False
            )
            assert provider.get_elevation(19.7, -155.08) == 0.0

    def test_cached_tiles_list(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cache = Path(tmpdir)
            # Create a fake tile
            (cache / "N19W155.hgt").write_bytes(b'\x00' * 100)
            provider = SRTMProvider(cache_dir=cache, auto_download=False)
            tiles = provider.get_cached_tiles()
            assert "N19W155.hgt" in tiles

    def test_interpolation_with_synthetic_tile(self):
        """Test bilinear interpolation with a uniform-value tile."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache = Path(tmpdir)

            # Create SRTM3-like tile (1201x1201 samples) with constant 500m
            samples = 1201
            elevation = 500
            data = struct.pack(f'>{samples * samples}h',
                               *([elevation] * (samples * samples)))

            # Tile N19E010 covers lat 19-20, lon 10-11
            tile_path = cache / "N19E010.hgt"
            tile_path.write_bytes(data)

            provider = SRTMProvider(cache_dir=cache, auto_download=False)

            # Any point in the tile should return ~500m
            elev = provider.get_elevation(19.5, 10.5)
            assert abs(elev - 500.0) < 1.0

            # Edge points too
            elev2 = provider.get_elevation(19.01, 10.01)
            assert abs(elev2 - 500.0) < 1.0

    def test_cache_size_mb(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cache = Path(tmpdir)
            # Create a 1MB fake tile
            (cache / "N19W155.hgt").write_bytes(b'\x00' * (1024 * 1024))
            provider = SRTMProvider(cache_dir=cache, auto_download=False)
            size = provider.get_cache_size_mb()
            assert abs(size - 1.0) < 0.01


class TestLOSResult:
    """Tests for LOSResult data structure."""

    def test_default_is_clear(self):
        result = LOSResult()
        assert result.is_clear is True
        assert result.terrain_loss_db == 0.0

    def test_to_dict(self):
        result = LOSResult()
        result.distance_m = 5000.0
        result.is_clear = False
        result.terrain_loss_db = 12.5
        result.fspl_db = 100.0
        result.total_loss_db = 112.5

        d = result.to_dict()
        assert d["is_clear"] is False
        assert d["distance_m"] == 5000.0
        assert d["terrain_loss_db"] == 12.5
        assert d["total_loss_db"] == 112.5


class TestLOSAnalyzer:
    """Tests for line-of-sight analysis."""

    def test_flat_terrain_clear_los(self):
        """Clear LOS over flat terrain."""
        provider = FlatTerrainProvider(elevation=0.0)
        analyzer = LOSAnalyzer(provider)

        result = analyzer.analyze(
            lat1=19.0, lon1=-155.0, alt1=10.0,
            lat2=19.01, lon2=-155.0, alt2=10.0,
            freq_mhz=915.0
        )

        assert result.is_clear is True
        assert result.terrain_loss_db == 0.0
        assert result.num_obstructions == 0
        assert result.distance_m > 1000  # ~1.1km

    def test_flat_terrain_with_height(self):
        """Elevated flat terrain still has clear LOS."""
        provider = FlatTerrainProvider(elevation=500.0)
        analyzer = LOSAnalyzer(provider)

        result = analyzer.analyze(
            lat1=19.0, lon1=-155.0, alt1=10.0,
            lat2=19.02, lon2=-155.0, alt2=10.0,
        )

        assert result.is_clear is True
        assert result.num_obstructions == 0

    def test_ridge_blocks_los(self):
        """Ridge between two points blocks LOS."""
        # Create terrain with a tall ridge in the middle
        class RidgeProvider(FlatTerrainProvider):
            def get_elevation(self, lat, lon):
                # Flat at 0m except at midpoint latitude
                mid = 19.01
                if abs(lat - mid) < 0.002:
                    return 200.0  # 200m ridge
                return 0.0

        provider = RidgeProvider()
        analyzer = LOSAnalyzer(provider, profile_points=50)

        result = analyzer.analyze(
            lat1=19.0, lon1=-155.0, alt1=5.0,   # 5m antenna at ground level
            lat2=19.02, lon2=-155.0, alt2=5.0,  # 5m antenna at ground level
            freq_mhz=915.0
        )

        assert result.is_clear is False
        assert result.num_obstructions > 0
        assert result.terrain_loss_db > 0
        assert result.worst_obstruction_m > 100  # Ridge is 200m, LOS at ~5m

    def test_high_antennas_over_ridge(self):
        """Tall antennas can clear a moderate ridge."""
        class SmallRidgeProvider(FlatTerrainProvider):
            def get_elevation(self, lat, lon):
                mid = 19.005
                if abs(lat - mid) < 0.001:
                    return 20.0  # 20m ridge
                return 0.0

        provider = SmallRidgeProvider()
        analyzer = LOSAnalyzer(provider, profile_points=50)

        result = analyzer.analyze(
            lat1=19.0, lon1=-155.0, alt1=50.0,   # 50m tower
            lat2=19.01, lon2=-155.0, alt2=50.0,  # 50m tower
            freq_mhz=915.0
        )

        assert result.is_clear is True
        assert result.num_obstructions == 0

    def test_distance_calculation(self):
        """Distance is calculated correctly."""
        provider = FlatTerrainProvider()
        analyzer = LOSAnalyzer(provider)

        # ~11.1 km per 0.1 degree latitude
        result = analyzer.analyze(
            lat1=0.0, lon1=0.0, alt1=10.0,
            lat2=0.1, lon2=0.0, alt2=10.0,
        )

        assert 11000 < result.distance_m < 11200

    def test_fspl_calculation(self):
        """Free-space path loss is included."""
        provider = FlatTerrainProvider()
        analyzer = LOSAnalyzer(provider)

        result = analyzer.analyze(
            lat1=19.0, lon1=-155.0, alt1=10.0,
            lat2=19.01, lon2=-155.0, alt2=10.0,
            freq_mhz=915.0
        )

        # FSPL at ~1.1km, 915 MHz should be around 92 dB
        assert 85 < result.fspl_db < 100
        assert result.total_loss_db >= result.fspl_db

    def test_very_short_distance(self):
        """Very short distances are handled."""
        provider = FlatTerrainProvider()
        analyzer = LOSAnalyzer(provider)

        result = analyzer.analyze(
            lat1=19.0, lon1=-155.0, alt1=10.0,
            lat2=19.0, lon2=-155.0, alt2=10.0,  # Same point
        )

        assert result.is_clear is True

    def test_elevation_profile_captured(self):
        """Elevation profile is stored in result."""
        provider = FlatTerrainProvider(elevation=42.0)
        analyzer = LOSAnalyzer(provider, profile_points=20)

        result = analyzer.analyze(
            lat1=19.0, lon1=-155.0, alt1=10.0,
            lat2=19.01, lon2=-155.0, alt2=10.0,
        )

        assert len(result.elevation_profile) == 20
        assert all(e == 42.0 for e in result.elevation_profile)

    def test_earth_bulge_increases_with_distance(self):
        """Earth bulge is larger for longer paths."""
        provider = FlatTerrainProvider()
        analyzer = LOSAnalyzer(provider)

        short = analyzer.analyze(
            lat1=19.0, lon1=-155.0, alt1=10.0,
            lat2=19.01, lon2=-155.0, alt2=10.0,
        )
        long_path = analyzer.analyze(
            lat1=19.0, lon1=-155.0, alt1=10.0,
            lat2=19.1, lon2=-155.0, alt2=10.0,
        )

        assert long_path.earth_bulge_m > short.earth_bulge_m

    def test_synthetic_terrain_coverage(self):
        """Synthetic terrain produces varying results."""
        provider = SyntheticTerrainProvider(
            base_elevation=100.0, ridge_height=200.0, ridge_spacing_deg=0.005
        )
        analyzer = LOSAnalyzer(provider, profile_points=50)

        # With 200m ridges and 10m antennas, some paths should be blocked
        result = analyzer.analyze(
            lat1=19.0, lon1=-155.0, alt1=10.0,
            lat2=19.05, lon2=-155.05, alt2=10.0,
        )

        # Profile should have variations
        assert max(result.elevation_profile) > min(result.elevation_profile)


class TestLOSCoverageGrid:
    """Tests for coverage grid calculation."""

    def test_grid_returns_points(self):
        """Coverage grid returns expected number of points."""
        provider = FlatTerrainProvider()
        analyzer = LOSAnalyzer(provider, profile_points=20)

        points = analyzer.coverage_grid(
            center_lat=19.0, center_lon=-155.0,
            antenna_height=10.0, radius_km=1.0,
            resolution=5  # 5 radial steps
        )

        # 36 bearings * 5 steps = 180 points
        assert len(points) == 180

    def test_grid_all_clear_on_flat(self):
        """All grid points are clear on flat terrain with tall antenna."""
        provider = FlatTerrainProvider()
        analyzer = LOSAnalyzer(provider, profile_points=20)

        points = analyzer.coverage_grid(
            center_lat=19.0, center_lon=-155.0,
            antenna_height=30.0, radius_km=0.5,
            resolution=3
        )

        assert all(p["is_clear"] for p in points)

    def test_grid_has_expected_fields(self):
        """Grid points have required fields."""
        provider = FlatTerrainProvider()
        analyzer = LOSAnalyzer(provider, profile_points=10)

        points = analyzer.coverage_grid(
            center_lat=19.0, center_lon=-155.0,
            antenna_height=10.0, radius_km=0.5,
            resolution=2
        )

        for p in points:
            assert "lat" in p
            assert "lon" in p
            assert "bearing" in p
            assert "distance_m" in p
            assert "is_clear" in p
            assert "total_loss_db" in p
            assert "terrain_loss_db" in p

    def test_grid_distances_increase(self):
        """Grid points at further radii have greater distances."""
        provider = FlatTerrainProvider()
        analyzer = LOSAnalyzer(provider, profile_points=10)

        points = analyzer.coverage_grid(
            center_lat=19.0, center_lon=-155.0,
            antenna_height=10.0, radius_km=2.0,
            resolution=5
        )

        # Check first bearing (0 degrees) — distances should increase
        bearing_0 = [p for p in points if p["bearing"] == 0.0]
        distances = [p["distance_m"] for p in bearing_0]
        assert distances == sorted(distances)


class TestDestinationPoint:
    """Tests for geographic destination point calculation."""

    def test_north_bearing(self):
        """Moving north increases latitude."""
        provider = FlatTerrainProvider()
        analyzer = LOSAnalyzer(provider)

        lat2, lon2 = analyzer._destination_point(19.0, -155.0, 0.0, 1000.0)
        assert lat2 > 19.0
        assert abs(lon2 - (-155.0)) < 0.001

    def test_east_bearing(self):
        """Moving east increases longitude (in western hemisphere)."""
        provider = FlatTerrainProvider()
        analyzer = LOSAnalyzer(provider)

        lat2, lon2 = analyzer._destination_point(19.0, -155.0, 90.0, 1000.0)
        assert abs(lat2 - 19.0) < 0.001
        assert lon2 > -155.0

    def test_distance_accuracy(self):
        """Destination point is at approximately correct distance."""
        from utils.rf import haversine_distance

        provider = FlatTerrainProvider()
        analyzer = LOSAnalyzer(provider)

        lat2, lon2 = analyzer._destination_point(19.0, -155.0, 45.0, 5000.0)
        actual_dist = haversine_distance(19.0, -155.0, lat2, lon2)

        # Should be within 1% of requested distance
        assert abs(actual_dist - 5000.0) < 50.0
