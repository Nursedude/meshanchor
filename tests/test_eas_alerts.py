"""
Tests for the EAS Alerts Plugin.

Tests alert parsing, filtering, and API integration.
"""

import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime, timedelta
import json


class TestAlertSeverity:
    """Tests for AlertSeverity enum."""

    def test_severity_values(self):
        """Severity enum has expected values."""
        from src.plugins.eas_alerts import AlertSeverity

        assert AlertSeverity.EXTREME.value == "Extreme"
        assert AlertSeverity.SEVERE.value == "Severe"
        assert AlertSeverity.MODERATE.value == "Moderate"
        assert AlertSeverity.MINOR.value == "Minor"
        assert AlertSeverity.UNKNOWN.value == "Unknown"


class TestAlertSource:
    """Tests for AlertSource enum."""

    def test_source_values(self):
        """Source enum has expected values."""
        from src.plugins.eas_alerts import AlertSource

        assert AlertSource.NOAA.value == "NOAA/NWS"
        assert AlertSource.USGS.value == "USGS Volcano"
        assert AlertSource.FEMA.value == "FEMA iPAWS"


class TestAlert:
    """Tests for Alert dataclass."""

    def test_alert_creation(self):
        """Create an alert with required fields."""
        from src.plugins.eas_alerts import Alert, AlertSource, AlertSeverity

        alert = Alert(
            id="test-123",
            source=AlertSource.NOAA,
            title="Severe Thunderstorm Warning",
            description="Large hail and damaging winds expected",
            severity=AlertSeverity.SEVERE,
            event_type="SVR",
        )

        assert alert.id == "test-123"
        assert alert.source == AlertSource.NOAA
        assert alert.severity == AlertSeverity.SEVERE

    def test_alert_is_active_no_dates(self):
        """Alert without dates is active."""
        from src.plugins.eas_alerts import Alert, AlertSource, AlertSeverity

        alert = Alert(
            id="test-1",
            source=AlertSource.NOAA,
            title="Test",
            description="Test",
            severity=AlertSeverity.MINOR,
            event_type="TEST",
        )

        assert alert.is_active() is True

    def test_alert_is_active_expired(self):
        """Expired alert is not active."""
        from src.plugins.eas_alerts import Alert, AlertSource, AlertSeverity

        alert = Alert(
            id="test-1",
            source=AlertSource.NOAA,
            title="Test",
            description="Test",
            severity=AlertSeverity.MINOR,
            event_type="TEST",
            expires=datetime.now() - timedelta(hours=1),
        )

        assert alert.is_active() is False

    def test_alert_is_active_future(self):
        """Future alert is not active yet."""
        from src.plugins.eas_alerts import Alert, AlertSource, AlertSeverity

        alert = Alert(
            id="test-1",
            source=AlertSource.NOAA,
            title="Test",
            description="Test",
            severity=AlertSeverity.MINOR,
            event_type="TEST",
            effective=datetime.now() + timedelta(hours=1),
        )

        assert alert.is_active() is False

    def test_alert_to_dict(self):
        """Convert alert to dictionary."""
        from src.plugins.eas_alerts import Alert, AlertSource, AlertSeverity

        alert = Alert(
            id="test-123",
            source=AlertSource.USGS,
            title="Volcano Alert",
            description="Elevated activity",
            severity=AlertSeverity.MODERATE,
            event_type="Volcano-ADVISORY",
            areas=["Hawaii"],
        )

        data = alert.to_dict()

        assert data['id'] == "test-123"
        assert data['source'] == "USGS Volcano"
        assert data['severity'] == "Moderate"
        assert data['areas'] == ["Hawaii"]

    def test_alert_to_mesh_message(self):
        """Format alert for mesh broadcast."""
        from src.plugins.eas_alerts import Alert, AlertSource, AlertSeverity

        alert = Alert(
            id="test-1",
            source=AlertSource.NOAA,
            title="Tornado Warning",
            description="Take shelter immediately",
            severity=AlertSeverity.EXTREME,
            event_type="TOR",
            areas=["Adams County", "Jefferson County"],
        )

        msg = alert.to_mesh_message()

        assert "NWS" in msg
        assert "Tornado Warning" in msg
        assert len(msg) <= 200

    def test_alert_mesh_message_truncation(self):
        """Long alert message is truncated."""
        from src.plugins.eas_alerts import Alert, AlertSource, AlertSeverity

        alert = Alert(
            id="test-1",
            source=AlertSource.NOAA,
            title="A" * 300,  # Very long title
            description="Test",
            severity=AlertSeverity.MINOR,
            event_type="TEST",
        )

        msg = alert.to_mesh_message(max_length=100)
        assert len(msg) <= 100


class TestEASConfigTemplate:
    """Tests for configuration template."""

    def test_template_exists(self):
        """Config template is defined."""
        from src.plugins.eas_alerts import EAS_CONFIG_TEMPLATE

        assert len(EAS_CONFIG_TEMPLATE) > 100
        assert "[general]" in EAS_CONFIG_TEMPLATE
        assert "[noaa_weather]" in EAS_CONFIG_TEMPLATE
        assert "[usgs_volcano]" in EAS_CONFIG_TEMPLATE
        assert "[fema_ipaws]" in EAS_CONFIG_TEMPLATE

    def test_template_parseable(self):
        """Config template can be parsed."""
        import configparser
        from src.plugins.eas_alerts import EAS_CONFIG_TEMPLATE

        config = configparser.ConfigParser()
        config.read_string(EAS_CONFIG_TEMPLATE)

        assert config.getboolean('general', 'enabled') is True
        assert config.getint('general', 'poll_interval') == 300
        assert config.getboolean('noaa_weather', 'enabled') is True
        assert config.getboolean('usgs_volcano', 'enabled') is True

    def test_template_has_location(self):
        """Config template has location settings."""
        import configparser
        from src.plugins.eas_alerts import EAS_CONFIG_TEMPLATE

        config = configparser.ConfigParser()
        config.read_string(EAS_CONFIG_TEMPLATE)

        assert config.has_option('location', 'latitude')
        assert config.has_option('location', 'longitude')
        assert config.has_option('location', 'state')
        assert config.has_option('location', 'fips_codes')


class TestEASAlertsPlugin:
    """Tests for EASAlertsPlugin class."""

    def test_plugin_metadata(self):
        """Plugin has correct metadata."""
        from src.plugins.eas_alerts import EASAlertsPlugin

        meta = EASAlertsPlugin.get_metadata()

        assert meta.name == "eas-alerts"
        assert meta.version == "1.0.0"
        assert "Emergency Alert" in meta.description

    def test_plugin_initialization(self):
        """Plugin initializes correctly."""
        from src.plugins.eas_alerts import EASAlertsPlugin

        plugin = EASAlertsPlugin()

        assert plugin._config is None
        assert plugin._poll_thread is None
        assert plugin._current_alerts == []

    def test_get_config_template(self):
        """Plugin returns config template."""
        from src.plugins.eas_alerts import EASAlertsPlugin

        plugin = EASAlertsPlugin()
        template = plugin.get_config_template()

        assert "[general]" in template
        assert "[noaa_weather]" in template

    def test_plugin_callback_registration(self):
        """Callbacks can be registered and unregistered."""
        from src.plugins.eas_alerts import EASAlertsPlugin

        plugin = EASAlertsPlugin()
        callback = MagicMock()

        plugin.register_callback(callback)
        assert callback in plugin._alert_callbacks

        plugin.unregister_callback(callback)
        assert callback not in plugin._alert_callbacks

    def test_get_stats_before_activate(self):
        """Stats work before activation."""
        from src.plugins.eas_alerts import EASAlertsPlugin

        plugin = EASAlertsPlugin()
        stats = plugin.get_stats()

        assert stats['enabled'] is False
        assert stats['last_poll'] is None
        assert stats['current_alert_count'] == 0

    def test_parse_severity(self):
        """Severity parsing works correctly."""
        from src.plugins.eas_alerts import EASAlertsPlugin, AlertSeverity

        plugin = EASAlertsPlugin()

        assert plugin._parse_severity("Extreme") == AlertSeverity.EXTREME
        assert plugin._parse_severity("SEVERE") == AlertSeverity.SEVERE
        assert plugin._parse_severity("moderate") == AlertSeverity.MODERATE
        assert plugin._parse_severity("Minor") == AlertSeverity.MINOR
        assert plugin._parse_severity("unknown_value") == AlertSeverity.UNKNOWN

    def test_parse_datetime_valid(self):
        """Datetime parsing works for valid strings."""
        from src.plugins.eas_alerts import EASAlertsPlugin

        plugin = EASAlertsPlugin()

        dt = plugin._parse_datetime("2026-01-12T10:30:00")
        assert dt is not None
        assert dt.year == 2026
        assert dt.month == 1

    def test_parse_datetime_with_z(self):
        """Datetime parsing handles Z suffix."""
        from src.plugins.eas_alerts import EASAlertsPlugin

        plugin = EASAlertsPlugin()

        dt = plugin._parse_datetime("2026-01-12T10:30:00Z")
        assert dt is not None

    def test_parse_datetime_none(self):
        """Datetime parsing handles None."""
        from src.plugins.eas_alerts import EASAlertsPlugin

        plugin = EASAlertsPlugin()

        assert plugin._parse_datetime(None) is None
        assert plugin._parse_datetime("") is None


class TestEASConnectionMethods:
    """Tests for connect/disconnect/is_connected methods."""

    def test_is_connected_initially_false(self):
        """Plugin is not connected initially."""
        from src.plugins.eas_alerts import EASAlertsPlugin

        plugin = EASAlertsPlugin()
        assert plugin.is_connected() is False

    def test_connect_starts_polling(self, tmp_path):
        """Connect starts the polling thread."""
        from src.plugins.eas_alerts import EASAlertsPlugin
        import time

        plugin = EASAlertsPlugin()

        # Set config path to temp
        config_path = tmp_path / "eas_alerts.ini"
        plugin._config_path = config_path

        # Connect
        result = plugin.connect()

        assert result is True
        assert plugin.is_connected() is True

        # Clean up
        plugin.disconnect()

    def test_connect_when_already_connected(self, tmp_path):
        """Connect returns True when already connected."""
        from src.plugins.eas_alerts import EASAlertsPlugin

        plugin = EASAlertsPlugin()
        config_path = tmp_path / "eas_alerts.ini"
        plugin._config_path = config_path

        # First connect
        plugin.connect()
        assert plugin.is_connected() is True

        # Second connect should return True without error
        result = plugin.connect()
        assert result is True

        plugin.disconnect()

    def test_disconnect_stops_polling(self, tmp_path):
        """Disconnect stops the polling thread."""
        from src.plugins.eas_alerts import EASAlertsPlugin

        plugin = EASAlertsPlugin()
        config_path = tmp_path / "eas_alerts.ini"
        plugin._config_path = config_path

        plugin.connect()
        assert plugin.is_connected() is True

        plugin.disconnect()
        assert plugin.is_connected() is False

    def test_connect_when_disabled_in_config(self, tmp_path):
        """Connect returns False when plugin is disabled."""
        from src.plugins.eas_alerts import EASAlertsPlugin
        import configparser

        plugin = EASAlertsPlugin()

        # Create config with enabled=False
        config_path = tmp_path / "eas_alerts.ini"
        config_path.write_text("""
[general]
enabled = False
poll_interval = 300
""")
        plugin._config_path = config_path

        result = plugin.connect()
        assert result is False
        assert plugin.is_connected() is False


class TestConvenienceFunctions:
    """Tests for module-level convenience functions."""

    def test_create_default_config(self, tmp_path):
        """Create default config at custom path."""
        from src.plugins.eas_alerts import create_default_config

        config_path = tmp_path / "eas_alerts.ini"
        result = create_default_config(config_path)

        assert result == config_path
        assert config_path.exists()

        content = config_path.read_text()
        assert "[general]" in content
        assert "[noaa_weather]" in content

    @patch('urllib.request.urlopen')
    def test_get_quick_alerts_with_state(self, mock_urlopen):
        """Quick alerts function with state parameter."""
        from src.plugins.eas_alerts import get_quick_alerts

        # Mock response
        mock_response = MagicMock()
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_response.read.return_value = json.dumps({
            'features': [{
                'properties': {
                    'headline': 'Test Alert',
                    'severity': 'Moderate',
                    'event': 'TEST',
                }
            }]
        }).encode('utf-8')
        mock_urlopen.return_value = mock_response

        alerts = get_quick_alerts(state="WA")

        assert len(alerts) == 1
        assert alerts[0]['source'] == 'NOAA'
        assert alerts[0]['title'] == 'Test Alert'


class TestNOAAWeatherAlerts:
    """Tests for NOAA weather alert fetching."""

    @patch('urllib.request.urlopen')
    def test_get_weather_alerts_success(self, mock_urlopen):
        """Successfully fetch NOAA weather alerts."""
        from src.plugins.eas_alerts import EASAlertsPlugin
        import configparser

        # Create plugin with config
        plugin = EASAlertsPlugin()
        plugin._config = configparser.ConfigParser()
        plugin._config.read_string("""
[general]
user_agent = Test/1.0

[location]
latitude = 48.5
longitude = -123.0
state = WA

[noaa_weather]
enabled = True
severity_filter = Extreme,Severe
ignore_words = test
""")

        # Mock response
        mock_response = MagicMock()
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_response.read.return_value = json.dumps({
            'features': [{
                'properties': {
                    'id': 'NWS-IDP-123',
                    'headline': 'Severe Thunderstorm Warning',
                    'description': 'Large hail expected',
                    'severity': 'Severe',
                    'event': 'Severe Thunderstorm Warning',
                    'effective': '2026-01-12T10:00:00',
                    'expires': '2026-01-12T12:00:00',
                    'areaDesc': 'King County; Pierce County',
                }
            }]
        }).encode('utf-8')
        mock_urlopen.return_value = mock_response

        alerts = plugin.get_weather_alerts()

        assert len(alerts) == 1
        assert alerts[0].title == 'Severe Thunderstorm Warning'
        assert 'King County' in alerts[0].areas

    @patch('urllib.request.urlopen')
    def test_get_weather_alerts_filters_test(self, mock_urlopen):
        """NOAA alerts filters out test alerts."""
        from src.plugins.eas_alerts import EASAlertsPlugin
        import configparser

        plugin = EASAlertsPlugin()
        plugin._config = configparser.ConfigParser()
        plugin._config.read_string("""
[general]
user_agent = Test/1.0

[location]
state = WA

[noaa_weather]
enabled = True
severity_filter = Severe
ignore_words = test,exercise
""")

        mock_response = MagicMock()
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_response.read.return_value = json.dumps({
            'features': [
                {
                    'properties': {
                        'id': '1',
                        'headline': 'THIS IS A TEST - Tornado Warning',
                        'severity': 'Severe',
                        'event': 'TEST',
                    }
                },
                {
                    'properties': {
                        'id': '2',
                        'headline': 'Severe Thunderstorm Warning',
                        'severity': 'Severe',
                        'event': 'SVR',
                    }
                },
            ]
        }).encode('utf-8')
        mock_urlopen.return_value = mock_response

        alerts = plugin.get_weather_alerts()

        # Should filter out the test alert
        assert len(alerts) == 1
        assert 'TEST' not in alerts[0].title


class TestUSGSVolcanoAlerts:
    """Tests for USGS volcano alert fetching."""

    @patch('urllib.request.urlopen')
    def test_get_volcano_alerts_success(self, mock_urlopen):
        """Successfully fetch USGS volcano alerts."""
        from src.plugins.eas_alerts import EASAlertsPlugin, AlertSeverity
        import configparser

        plugin = EASAlertsPlugin()
        plugin._config = configparser.ConfigParser()
        plugin._config.read_string("""
[general]
user_agent = Test/1.0

[usgs_volcano]
enabled = True
level_filter = WARNING,WATCH,ADVISORY
color_filter = RED,ORANGE,YELLOW
volcano_list =
""")

        mock_response = MagicMock()
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_response.read.return_value = json.dumps([
            {
                'vnum': '311240',
                'volcano_name': 'Kilauea',
                'alert_level': 'WATCH',
                'color_code': 'ORANGE',
                'current_status': 'Lava fountaining continues',
                'current_status_timestamp': '2026-01-12T08:00:00',
                'state': 'HI',
                'latitude': 19.421,
                'longitude': -155.287,
            }
        ]).encode('utf-8')
        mock_urlopen.return_value = mock_response

        alerts = plugin.get_volcano_alerts()

        assert len(alerts) == 1
        assert 'Kilauea' in alerts[0].title
        assert alerts[0].severity == AlertSeverity.SEVERE  # ORANGE = Severe
        assert alerts[0].coordinates == (19.421, -155.287)


class TestFEMAAlerts:
    """Tests for FEMA iPAWS alert fetching."""

    @patch('urllib.request.urlopen')
    def test_get_fema_alerts_success(self, mock_urlopen):
        """Successfully fetch FEMA alerts."""
        from src.plugins.eas_alerts import EASAlertsPlugin
        import configparser

        plugin = EASAlertsPlugin()
        plugin._config = configparser.ConfigParser()
        plugin._config.read_string("""
[general]
user_agent = Test/1.0

[location]
fips_codes = 53

[fema_ipaws]
enabled = True
use_archived = True
ignore_words = test
event_types =
history_days = 1
""")

        mock_response = MagicMock()
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_response.read.return_value = json.dumps({
            'IpawsArchivedAlerts': [
                {
                    'id': 'FEMA-123',
                    'headline': 'Amber Alert',
                    'description': 'Missing child',
                    'severity': 'Severe',
                    'eventCode': 'CAE',
                    'areaDesc': 'King County, WA',
                }
            ]
        }).encode('utf-8')
        mock_urlopen.return_value = mock_response

        alerts = plugin.get_fema_alerts()

        assert len(alerts) == 1
        assert alerts[0].title == 'Amber Alert'
        assert alerts[0].source.value == 'FEMA iPAWS'
