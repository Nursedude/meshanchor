"""
Tests for NOAA SWPC Space Weather API integration.
"""

import pytest
from unittest.mock import patch, MagicMock
import json


class TestBandCondition:
    """Tests for BandCondition enum."""

    def test_condition_values(self):
        """Band condition enum has expected values."""
        from src.utils.space_weather import BandCondition

        assert BandCondition.EXCELLENT.value == "Excellent"
        assert BandCondition.GOOD.value == "Good"
        assert BandCondition.FAIR.value == "Fair"
        assert BandCondition.POOR.value == "Poor"
        assert BandCondition.VERY_POOR.value == "Very Poor"


class TestGeomagneticStorm:
    """Tests for GeomagneticStorm enum."""

    def test_storm_values(self):
        """Storm level enum has expected values."""
        from src.utils.space_weather import GeomagneticStorm

        assert GeomagneticStorm.QUIET.value == "Quiet"
        assert GeomagneticStorm.MINOR.value == "G1 Minor"
        assert GeomagneticStorm.EXTREME.value == "G5 Extreme"


class TestSpaceWeatherData:
    """Tests for SpaceWeatherData dataclass."""

    def test_data_creation(self):
        """Create space weather data with defaults."""
        from src.utils.space_weather import SpaceWeatherData, GeomagneticStorm

        data = SpaceWeatherData()

        assert data.solar_flux is None
        assert data.k_index is None
        assert data.geomag_storm == GeomagneticStorm.QUIET

    def test_data_to_dict(self):
        """Convert space weather data to dictionary."""
        from src.utils.space_weather import SpaceWeatherData, BandCondition

        data = SpaceWeatherData(
            solar_flux=125.5,
            k_index=2,
            band_conditions={'20m': BandCondition.GOOD}
        )

        d = data.to_dict()

        assert d['solar_flux'] == 125.5
        assert d['k_index'] == 2
        assert d['band_conditions']['20m'] == 'Good'


class TestSpaceWeatherAPI:
    """Tests for SpaceWeatherAPI class."""

    def test_api_initialization(self):
        """API initializes with correct defaults."""
        from src.utils.space_weather import SpaceWeatherAPI

        api = SpaceWeatherAPI()

        assert api.timeout == 15
        assert api.BASE_URL == "https://services.swpc.noaa.gov"

    def test_api_custom_timeout(self):
        """API accepts custom timeout."""
        from src.utils.space_weather import SpaceWeatherAPI

        api = SpaceWeatherAPI(timeout=30)

        assert api.timeout == 30

    def test_k_to_storm_level(self):
        """K-index correctly maps to storm level."""
        from src.utils.space_weather import SpaceWeatherAPI, GeomagneticStorm

        api = SpaceWeatherAPI()

        assert api.k_to_storm_level(0) == GeomagneticStorm.QUIET
        assert api.k_to_storm_level(3) == GeomagneticStorm.QUIET
        assert api.k_to_storm_level(4) == GeomagneticStorm.UNSETTLED
        assert api.k_to_storm_level(5) == GeomagneticStorm.MINOR
        assert api.k_to_storm_level(6) == GeomagneticStorm.MODERATE
        assert api.k_to_storm_level(7) == GeomagneticStorm.STRONG
        assert api.k_to_storm_level(8) == GeomagneticStorm.SEVERE
        assert api.k_to_storm_level(9) == GeomagneticStorm.EXTREME

    def test_flux_to_class_b(self):
        """X-ray flux converts to B class correctly."""
        from src.utils.space_weather import SpaceWeatherAPI

        api = SpaceWeatherAPI()

        result = api._flux_to_class(4.2e-7)
        assert result.startswith('B')

    def test_flux_to_class_c(self):
        """X-ray flux converts to C class correctly."""
        from src.utils.space_weather import SpaceWeatherAPI

        api = SpaceWeatherAPI()

        result = api._flux_to_class(1.5e-6)
        assert result.startswith('C')

    def test_flux_to_class_m(self):
        """X-ray flux converts to M class correctly."""
        from src.utils.space_weather import SpaceWeatherAPI

        api = SpaceWeatherAPI()

        result = api._flux_to_class(2.0e-5)
        assert result.startswith('M')

    def test_flux_to_class_x(self):
        """X-ray flux converts to X class correctly."""
        from src.utils.space_weather import SpaceWeatherAPI

        api = SpaceWeatherAPI()

        result = api._flux_to_class(1.5e-4)
        assert result.startswith('X')

    def test_assess_band_conditions_no_data(self):
        """Band assessment with no data returns defaults."""
        from src.utils.space_weather import SpaceWeatherAPI, BandCondition

        api = SpaceWeatherAPI()

        conditions = api.assess_band_conditions(None, None)

        assert '20m' in conditions
        assert '40m' in conditions
        assert conditions['20m'] == BandCondition.FAIR

    def test_assess_band_conditions_good(self):
        """Band assessment with good conditions."""
        from src.utils.space_weather import SpaceWeatherAPI, BandCondition

        api = SpaceWeatherAPI()

        # High flux, low K = good conditions
        conditions = api.assess_band_conditions(150.0, 1)

        assert conditions['20m'] in [BandCondition.EXCELLENT, BandCondition.GOOD]
        assert conditions['10m'] in [BandCondition.EXCELLENT, BandCondition.GOOD]

    def test_assess_band_conditions_storm(self):
        """Band assessment during geomagnetic storm."""
        from src.utils.space_weather import SpaceWeatherAPI, BandCondition

        api = SpaceWeatherAPI()

        # High K = storm, low bands affected
        conditions = api.assess_band_conditions(100.0, 6)

        assert conditions['160m'] in [BandCondition.POOR, BandCondition.VERY_POOR]
        assert conditions['80m'] in [BandCondition.POOR, BandCondition.VERY_POOR]

    @patch('urllib.request.urlopen')
    def test_get_k_index_success(self, mock_urlopen):
        """Successfully fetch K-index."""
        from src.utils.space_weather import SpaceWeatherAPI

        mock_response = MagicMock()
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_response.read.return_value = json.dumps([
            ["2026-01-12 09:00:00.000", 2.33],
            ["2026-01-12 10:00:00.000", 2.67],
        ]).encode('utf-8')
        mock_urlopen.return_value = mock_response

        api = SpaceWeatherAPI()
        result = api.get_k_index()

        assert result is not None
        k_index, timestamp = result
        assert k_index == 3  # Rounded from 2.67
        assert timestamp.hour == 10

    @patch('urllib.request.urlopen')
    def test_get_solar_flux_success(self, mock_urlopen):
        """Successfully fetch solar flux."""
        from src.utils.space_weather import SpaceWeatherAPI

        mock_response = MagicMock()
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_response.read.return_value = json.dumps([
            {"time_tag": "2026-01-12T00:00:00Z", "flux": 125.3},
            {"time_tag": "2026-01-12T12:00:00Z", "flux": 128.7},
        ]).encode('utf-8')
        mock_urlopen.return_value = mock_response

        api = SpaceWeatherAPI()
        result = api.get_solar_flux()

        assert result == 128.7

    @patch('urllib.request.urlopen')
    def test_fetch_error_handling(self, mock_urlopen):
        """Handle fetch errors gracefully."""
        from src.utils.space_weather import SpaceWeatherAPI
        import urllib.error

        mock_urlopen.side_effect = urllib.error.URLError("Network error")

        api = SpaceWeatherAPI()
        result = api.get_k_index()

        assert result is None

    def test_get_quick_summary_no_data(self):
        """Quick summary handles no data."""
        from src.utils.space_weather import SpaceWeatherAPI

        api = SpaceWeatherAPI()

        # Don't mock - just check it doesn't crash
        summary = api.get_quick_summary()
        assert isinstance(summary, str)


class TestConvenienceFunctions:
    """Tests for module-level convenience functions."""

    @patch('src.utils.space_weather.SpaceWeatherAPI.get_current_conditions')
    def test_get_space_weather(self, mock_get):
        """get_space_weather function works."""
        from src.utils.space_weather import get_space_weather, SpaceWeatherData

        mock_get.return_value = SpaceWeatherData(solar_flux=120.0)

        data = get_space_weather()

        assert data.solar_flux == 120.0

    @patch('src.utils.space_weather.SpaceWeatherAPI.get_quick_summary')
    def test_get_propagation_summary(self, mock_get):
        """get_propagation_summary function works."""
        from src.utils.space_weather import get_propagation_summary

        mock_get.return_value = "SFI:120 K:2 Quiet"

        summary = get_propagation_summary()

        assert summary == "SFI:120 K:2 Quiet"
