"""
Tests for MeshForge Propagation Commands (standalone space weather).

Tests the NOAA-primary propagation module that works without
any external services (HamClock/OpenHamClock are optional).
"""

import pytest
import sys
import os
from unittest.mock import patch, MagicMock
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from commands.propagation import (
    DataSource, SourceConfig,
    configure_source, get_sources,
    get_space_weather, get_band_conditions,
    get_alerts, get_propagation_summary,
    get_enhanced_data, check_source,
)
from commands.base import CommandResult


class TestDataSource:
    """Test DataSource enum."""

    def test_data_source_values(self):
        assert DataSource.NOAA.value == "noaa"
        assert DataSource.OPENHAMCLOCK.value == "openhamclock"
        assert DataSource.HAMCLOCK.value == "hamclock"

    def test_all_sources_defined(self):
        assert len(DataSource) == 3


class TestSourceConfig:
    """Test SourceConfig dataclass."""

    def test_default_config(self):
        cfg = SourceConfig(source=DataSource.NOAA)
        assert cfg.host == "localhost"
        assert cfg.port == 0
        assert cfg.enabled is False
        assert cfg.timeout == 10

    def test_openhamclock_base_url(self):
        cfg = SourceConfig(
            source=DataSource.OPENHAMCLOCK,
            host="192.168.1.100",
            port=3000,
        )
        assert cfg.base_url == "http://192.168.1.100:3000"

    def test_hamclock_base_url(self):
        cfg = SourceConfig(
            source=DataSource.HAMCLOCK,
            host="hamclock.local",
            port=8080,
        )
        assert cfg.base_url == "http://hamclock.local:8080"

    def test_noaa_base_url_empty(self):
        cfg = SourceConfig(source=DataSource.NOAA)
        assert cfg.base_url == ""


class TestConfigureSource:
    """Test source configuration."""

    def test_configure_noaa_always_enabled(self):
        result = configure_source(DataSource.NOAA)
        assert result.success is True
        assert "always enabled" in result.message.lower()

    def test_configure_openhamclock(self):
        result = configure_source(
            DataSource.OPENHAMCLOCK,
            host="myhost",
            port=3000,
        )
        assert result.success is True
        assert result.data['source'] == 'openhamclock'
        assert result.data['host'] == 'myhost'
        assert result.data['port'] == 3000
        assert result.data['enabled'] is True

    def test_configure_hamclock(self):
        result = configure_source(
            DataSource.HAMCLOCK,
            host="192.168.1.50",
            port=8082,
        )
        assert result.success is True
        assert result.data['port'] == 8082

    def test_configure_default_port_openhamclock(self):
        result = configure_source(DataSource.OPENHAMCLOCK, host="localhost")
        assert result.success is True
        assert result.data['port'] == 3000

    def test_configure_default_port_hamclock(self):
        result = configure_source(DataSource.HAMCLOCK, host="localhost")
        assert result.success is True
        assert result.data['port'] == 8080

    def test_configure_empty_host_rejected(self):
        result = configure_source(DataSource.OPENHAMCLOCK, host="")
        assert result.success is False

    def test_configure_invalid_port_rejected(self):
        result = configure_source(
            DataSource.OPENHAMCLOCK,
            host="localhost",
            port=99999,
        )
        assert result.success is False

    def test_get_sources(self):
        result = get_sources()
        assert result.success is True
        sources = result.data['sources']
        assert 'noaa' in sources
        assert 'openhamclock' in sources
        assert 'hamclock' in sources
        assert sources['noaa']['enabled'] is True


class TestGetSpaceWeather:
    """Test space weather retrieval from NOAA."""

    @patch('commands.propagation.SpaceWeatherAPI', create=True)
    def test_space_weather_success(self, _):
        """Space weather returns CommandResult."""
        result = get_space_weather()
        assert isinstance(result, CommandResult)

    def test_space_weather_returns_source(self):
        """Result includes source information."""
        result = get_space_weather()
        assert isinstance(result, CommandResult)
        if result.success:
            assert result.data.get('source') == 'NOAA SWPC'


class TestGetBandConditions:
    """Test band condition assessment."""

    def test_band_conditions_returns_result(self):
        result = get_band_conditions()
        assert isinstance(result, CommandResult)
        if result.success:
            assert 'bands' in result.data
            assert 'overall' in result.data
            assert result.data.get('source') == 'NOAA SWPC'


class TestGetAlerts:
    """Test NOAA alerts retrieval."""

    def test_alerts_returns_result(self):
        result = get_alerts()
        assert isinstance(result, CommandResult)
        if result.success:
            assert 'alerts' in result.data
            assert 'count' in result.data


class TestGetPropagationSummary:
    """Test propagation summary."""

    def test_summary_returns_result(self):
        result = get_propagation_summary()
        assert isinstance(result, CommandResult)
        if result.success:
            assert 'summary' in result.data
            assert 'overall' in result.data
            assert 'source' in result.data


class TestGetEnhancedData:
    """Test enhanced data with optional sources."""

    def test_enhanced_without_sources_returns_noaa(self):
        """Without optional sources, returns NOAA-only data."""
        # Disable optional sources
        configure_source(DataSource.OPENHAMCLOCK, host="localhost", enabled=False)
        configure_source(DataSource.HAMCLOCK, host="localhost", enabled=False)

        result = get_enhanced_data()
        assert isinstance(result, CommandResult)
        if result.success:
            assert result.data.get('enhanced_source') is None
            assert 'space_weather' in result.data


class TestCheckSource:
    """Test source connectivity testing."""

    def test_test_noaa(self):
        """NOAA test returns a result."""
        result = check_source(DataSource.NOAA)
        assert isinstance(result, CommandResult)

    def test_test_unconfigured_openhamclock(self):
        """Unconfigured OpenHamClock returns failure."""
        configure_source(DataSource.OPENHAMCLOCK, host="localhost", enabled=False)
        result = check_source(DataSource.OPENHAMCLOCK)
        assert isinstance(result, CommandResult)
        assert result.success is False

    def test_test_unconfigured_hamclock(self):
        """Unconfigured HamClock returns failure."""
        configure_source(DataSource.HAMCLOCK, host="localhost", enabled=False)
        result = check_source(DataSource.HAMCLOCK)
        assert isinstance(result, CommandResult)
        assert result.success is False


class TestModuleExports:
    """Test that propagation module is properly exported."""

    def test_propagation_importable(self):
        from commands import propagation
        assert propagation is not None

    def test_propagation_has_key_functions(self):
        from commands import propagation
        assert hasattr(propagation, 'get_space_weather')
        assert hasattr(propagation, 'get_band_conditions')
        assert hasattr(propagation, 'get_propagation_summary')
        assert hasattr(propagation, 'get_enhanced_data')
        assert hasattr(propagation, 'configure_source')
        assert hasattr(propagation, 'check_source')
        assert hasattr(propagation, 'DataSource')

    def test_propagation_in_commands_all(self):
        import commands
        assert 'propagation' in commands.__all__

    def test_hamclock_still_importable(self):
        """HamClock module still works for backward compatibility."""
        from commands import hamclock
        assert hasattr(hamclock, 'configure')
        assert hasattr(hamclock, 'get_space_weather')
        assert hasattr(hamclock, 'get_voacap')


class TestConfigPersistence:
    """Test that source config persists to disk via SettingsManager."""

    def test_configure_source_returns_persisted_flag(self):
        """configure_source result includes persisted status."""
        result = configure_source(
            DataSource.OPENHAMCLOCK, host="persist-test", port=3000
        )
        assert result.success is True
        assert 'persisted' in result.data

    def test_save_and_load_round_trip(self, tmp_path):
        """Config survives save/load cycle."""
        import commands.propagation as prop

        # Patch SettingsManager to use temp directory
        from utils.common import SettingsManager
        test_settings = SettingsManager(
            "propagation_test", config_dir=tmp_path,
            defaults={"sources": {
                "openhamclock": {"host": "localhost", "port": 3000, "enabled": False, "timeout": 10},
                "hamclock": {"host": "localhost", "port": 8080, "enabled": False, "timeout": 10},
            }}
        )

        # Swap in test settings
        orig_settings = prop._settings
        orig_has = prop._HAS_SETTINGS
        prop._settings = test_settings
        prop._HAS_SETTINGS = True

        try:
            # Configure a source (triggers save)
            result = configure_source(
                DataSource.OPENHAMCLOCK, host="10.0.0.5", port=3001
            )
            assert result.success is True
            assert result.data['persisted'] is True

            # Reset in-memory to defaults
            prop._sources[DataSource.OPENHAMCLOCK] = SourceConfig(
                source=DataSource.OPENHAMCLOCK, port=3000, enabled=False
            )
            assert prop._sources[DataSource.OPENHAMCLOCK].host == "localhost"

            # Reload from disk
            prop._load_sources()
            cfg = prop._sources[DataSource.OPENHAMCLOCK]
            assert cfg.host == "10.0.0.5"
            assert cfg.port == 3001
            assert cfg.enabled is True
        finally:
            prop._settings = orig_settings
            prop._HAS_SETTINGS = orig_has

    def test_noaa_config_not_persisted(self):
        """NOAA config is not persisted (always enabled)."""
        result = configure_source(DataSource.NOAA)
        assert result.success is True
        assert 'persisted' not in result.data

    def test_persistence_graceful_without_settings(self):
        """Module works even if SettingsManager unavailable."""
        import commands.propagation as prop

        orig_has = prop._HAS_SETTINGS
        prop._HAS_SETTINGS = False
        try:
            result = configure_source(
                DataSource.HAMCLOCK, host="fallback-test", port=8080
            )
            assert result.success is True
            assert result.data['persisted'] is False
        finally:
            prop._HAS_SETTINGS = orig_has

    def test_settings_file_created(self, tmp_path):
        """Settings file is actually written to disk."""
        import commands.propagation as prop
        from utils.common import SettingsManager

        test_settings = SettingsManager(
            "propagation_file_test", config_dir=tmp_path,
            defaults={"sources": {}}
        )

        orig_settings = prop._settings
        orig_has = prop._HAS_SETTINGS
        prop._settings = test_settings
        prop._HAS_SETTINGS = True

        try:
            configure_source(DataSource.HAMCLOCK, host="filetest", port=8082)
            assert (tmp_path / "propagation_file_test.json").exists()
        finally:
            prop._settings = orig_settings
            prop._HAS_SETTINGS = orig_has


class TestStandaloneFunctions:
    """Test standalone data source functions."""

    def test_dx_spots_telnet_importable(self):
        from commands.propagation import get_dx_spots_telnet
        assert callable(get_dx_spots_telnet)

    def test_voacap_online_requires_coordinates(self):
        from commands.propagation import get_voacap_online
        result = get_voacap_online()
        assert result.success is False
        assert "coordinates" in result.message.lower()

    def test_voacap_online_importable(self):
        from commands.propagation import get_voacap_online
        assert callable(get_voacap_online)

    def test_ionosonde_importable(self):
        from commands.propagation import get_ionosonde_data
        assert callable(get_ionosonde_data)

    def test_satellite_tle_importable(self):
        from commands.propagation import get_satellite_tle
        assert callable(get_satellite_tle)

    def test_parse_dx_spot(self):
        from commands.propagation import _parse_dx_spot
        spot = _parse_dx_spot("DX de W1AW:     14074.0  JA1ABC       FT8 -12 dB 1234Z")
        assert spot.get('spotter') == 'W1AW'
        assert spot.get('dx_call') == 'JA1ABC'

    def test_parse_dx_spot_minimal(self):
        from commands.propagation import _parse_dx_spot
        spot = _parse_dx_spot("DX de W1AW: 14074.0 JA1ABC comment 1234Z")
        assert 'raw' in spot


class TestDeprecationWarnings:
    """Test that deprecated hamclock functions emit warnings."""

    def test_get_space_weather_auto_warns(self):
        import warnings
        from commands import hamclock
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            hamclock.get_space_weather_auto()
            assert any("deprecated" in str(warning.message).lower() for warning in w)

    def test_get_band_conditions_auto_warns(self):
        import warnings
        from commands import hamclock
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            hamclock.get_band_conditions_auto()
            assert any("deprecated" in str(warning.message).lower() for warning in w)

    def test_get_propagation_summary_warns(self):
        import warnings
        from commands import hamclock
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            hamclock.get_propagation_summary()
            assert any("deprecated" in str(warning.message).lower() for warning in w)


class TestBackwardCompatibility:
    """Ensure existing hamclock tests still pass."""

    def test_hamclock_configure(self):
        from commands import hamclock
        result = hamclock.configure("localhost", api_port=8082)
        assert result.success is True

    def test_hamclock_get_config(self):
        from commands import hamclock
        hamclock.configure("testhost", api_port=8082)
        config = hamclock.get_config()
        assert config.host == 'testhost'

    def test_hamclock_reliability_mapping(self):
        from commands import hamclock
        assert hamclock._reliability_to_status(90) == 'excellent'
        assert hamclock._reliability_to_status(0) == 'closed'

    def test_hamclock_auto_functions_exist(self):
        """Auto functions still exist for backward compat."""
        from commands import hamclock
        assert hasattr(hamclock, 'get_space_weather_auto')
        assert hasattr(hamclock, 'get_band_conditions_auto')
        assert hasattr(hamclock, 'get_propagation_summary')
