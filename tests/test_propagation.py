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
