"""
Propagation Mixin TUI Tests

Tests the Space Weather & Propagation submenu wiring and display formatting.
"""

import os
import sys
from unittest.mock import MagicMock, patch

import pytest

# Ensure src and launcher_tui directories are importable
# Insert launcher_tui FIRST so we can import the mixin directly
# without triggering __init__.py (which pulls all mixin deps).
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src', 'launcher_tui'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from commands.base import CommandResult
from propagation_mixin import PropagationMixin


class FakeDialog:
    """Minimal dialog stub for testing mixin display methods."""

    def __init__(self):
        self.last_msgbox_title = None
        self.last_msgbox_text = None
        self.last_menu_title = None
        self.inputbox_returns = []
        self._menu_return = None

    def msgbox(self, title, text, height=None, width=None):
        self.last_msgbox_title = title
        self.last_msgbox_text = text

    def menu(self, title, text, choices):
        self.last_menu_title = title
        return self._menu_return

    def inputbox(self, title, text, default=""):
        if self.inputbox_returns:
            return self.inputbox_returns.pop(0)
        return default


def _make_launcher():
    """Create a minimal launcher instance with PropagationMixin."""

    class StubLauncher(PropagationMixin):
        def __init__(self):
            self.dialog = FakeDialog()

        def _safe_call(self, name, method, *args, **kwargs):
            method(*args, **kwargs)

    return StubLauncher()


# ===========================================================================
# Import / smoke tests
# ===========================================================================

class TestPropagationMixinImport:
    """Verify the mixin can be imported cleanly."""

    def test_import(self):
        assert hasattr(PropagationMixin, '_propagation_menu')

    def test_has_all_display_methods(self):
        expected = [
            '_propagation_menu',
            '_show_propagation_summary',
            '_show_space_weather',
            '_show_band_conditions',
            '_show_noaa_alerts',
            '_show_dx_spots',
            '_show_ionosonde',
            '_show_voacap',
            '_configure_prop_sources',
        ]
        for method in expected:
            assert hasattr(PropagationMixin, method), f"Missing {method}"


# ===========================================================================
# Space Weather display
# ===========================================================================

class TestShowSpaceWeather:
    """Test _show_space_weather formatting."""

    @patch('commands.propagation.get_space_weather')
    def test_success(self, mock_get):
        mock_get.return_value = CommandResult.ok(
            "Space weather: SFI=150, Kp=2, A=8 (Quiet)",
            data={
                'solar_flux': 150,
                'sunspot_number': 120,
                'k_index': 2,
                'a_index': 8,
                'xray_class': 'B5.3',
                'geomag_storm': 'Quiet',
                'source': 'NOAA SWPC',
                'updated': '2026-02-26T12:00:00',
            }
        )
        launcher = _make_launcher()
        launcher._show_space_weather()
        assert launcher.dialog.last_msgbox_title == "Space Weather"
        text = launcher.dialog.last_msgbox_text
        assert "150" in text
        assert "Kp" in text
        assert "NOAA SWPC" in text

    @patch('commands.propagation.get_space_weather')
    def test_failure(self, mock_get):
        mock_get.return_value = CommandResult.fail("Network error")
        launcher = _make_launcher()
        launcher._show_space_weather()
        assert launcher.dialog.last_msgbox_title == "Error"
        assert "Network error" in launcher.dialog.last_msgbox_text


# ===========================================================================
# Propagation Summary display
# ===========================================================================

class TestShowPropagationSummary:

    @patch('commands.propagation.get_propagation_summary')
    def test_success(self, mock_get):
        mock_get.return_value = CommandResult.ok(
            "SFI=150 Kp=2 — Good",
            data={
                'overall': 'Good',
                'solar_flux': 150,
                'k_index': 2,
                'a_index': 8,
                'geomag_storm': 'Quiet',
                'source': 'NOAA SWPC',
            }
        )
        launcher = _make_launcher()
        launcher._show_propagation_summary()
        assert launcher.dialog.last_msgbox_title == "Propagation Summary"
        text = launcher.dialog.last_msgbox_text
        assert "Good" in text
        assert "150" in text

    @patch('commands.propagation.get_propagation_summary')
    def test_failure(self, mock_get):
        mock_get.return_value = CommandResult.fail("No data")
        launcher = _make_launcher()
        launcher._show_propagation_summary()
        assert launcher.dialog.last_msgbox_title == "Error"


# ===========================================================================
# Band Conditions display
# ===========================================================================

class TestShowBandConditions:

    @patch('commands.propagation.get_band_conditions')
    def test_success(self, mock_get):
        mock_get.return_value = CommandResult.ok(
            "Band conditions: Good (10 bands assessed)",
            data={
                'bands': {
                    '20m': 'GOOD',
                    '40m': 'FAIR',
                    '80m': 'POOR',
                    '10m': 'EXCELLENT',
                },
                'overall': 'Good',
                'solar_flux': 130,
                'k_index': 1,
                'a_index': 5,
                'source': 'NOAA SWPC',
            }
        )
        launcher = _make_launcher()
        launcher._show_band_conditions()
        assert launcher.dialog.last_msgbox_title == "HF Band Conditions"
        text = launcher.dialog.last_msgbox_text
        assert "20m" in text
        assert "GOOD" in text
        assert "Good" in text

    @patch('commands.propagation.get_band_conditions')
    def test_failure(self, mock_get):
        mock_get.return_value = CommandResult.fail("Timeout")
        launcher = _make_launcher()
        launcher._show_band_conditions()
        assert launcher.dialog.last_msgbox_title == "Error"


# ===========================================================================
# NOAA Alerts display
# ===========================================================================

class TestShowNoaaAlerts:

    @patch('commands.propagation.get_alerts')
    def test_with_alerts(self, mock_get):
        mock_get.return_value = CommandResult.ok(
            "2 space weather alerts",
            data={
                'alerts': [
                    {'message': 'Geomagnetic storm warning', 'issue_datetime': '2026-02-26'},
                    {'message': 'Solar radiation storm', 'issue_datetime': '2026-02-25'},
                ],
                'count': 2,
            }
        )
        launcher = _make_launcher()
        launcher._show_noaa_alerts()
        text = launcher.dialog.last_msgbox_text
        assert "Geomagnetic storm warning" in text
        assert "Alert 1" in text

    @patch('commands.propagation.get_alerts')
    def test_no_alerts(self, mock_get):
        mock_get.return_value = CommandResult.ok(
            "0 space weather alerts",
            data={'alerts': [], 'count': 0}
        )
        launcher = _make_launcher()
        launcher._show_noaa_alerts()
        assert "No active" in launcher.dialog.last_msgbox_text


# ===========================================================================
# DX Spots display
# ===========================================================================

class TestShowDxSpots:

    @patch('commands.propagation.get_dx_spots_telnet')
    def test_success(self, mock_get):
        mock_get.return_value = CommandResult.ok(
            "5 DX spots from dxc.nc7j.com",
            data={
                'spots': [
                    {'frequency': '14025.0', 'dx_call': 'JA1ABC',
                     'spotter': 'W1XYZ', 'comment': 'CQ', 'time': '1234Z'},
                ],
                'count': 1,
                'server': 'dxc.nc7j.com',
            }
        )
        launcher = _make_launcher()
        launcher._show_dx_spots()
        text = launcher.dialog.last_msgbox_text
        assert "JA1ABC" in text
        assert "14025.0" in text

    @patch('commands.propagation.get_dx_spots_telnet')
    def test_failure(self, mock_get):
        mock_get.return_value = CommandResult.fail("Connection timed out")
        launcher = _make_launcher()
        launcher._show_dx_spots()
        assert "Connection timed out" in launcher.dialog.last_msgbox_text


# ===========================================================================
# Ionosonde display
# ===========================================================================

class TestShowIonosonde:

    @patch('commands.propagation.get_ionosonde_data')
    def test_success(self, mock_get):
        mock_get.return_value = CommandResult.ok(
            "Ionosonde: 5 stations (foF2=5.2MHz, MUF=18.3MHz)",
            data={
                'stations': [
                    {'name': 'Boulder', 'fof2': 5.2, 'muf': 18.3},
                    {'name': 'Millstone Hill', 'fof2': 4.8, 'muf': 16.1},
                ],
                'count': 2,
                'avg_fof2': 5.0,
                'avg_muf': 17.2,
                'source': 'prop.kc2g.com',
            }
        )
        launcher = _make_launcher()
        launcher._show_ionosonde()
        text = launcher.dialog.last_msgbox_text
        assert "Boulder" in text
        assert "5.0" in text  # avg foF2
        assert "prop.kc2g.com" in text


# ===========================================================================
# VOACAP display
# ===========================================================================

class TestShowVoacap:

    @patch('commands.propagation.get_voacap_online')
    def test_success(self, mock_get):
        mock_get.return_value = CommandResult.ok(
            "VOACAP prediction: 5 bands",
            data={
                'bands': {'20m': '85%', '40m': '60%'},
                'tx': {'lat': 21.3, 'lon': -157.8},
                'rx': {'lat': 37.8, 'lon': -122.4},
                'source': 'VOACAP Online',
            }
        )
        launcher = _make_launcher()
        launcher.dialog.inputbox_returns = ["21.3", "-157.8", "37.8", "-122.4"]
        launcher._show_voacap()
        text = launcher.dialog.last_msgbox_text
        assert "20m" in text
        assert "VOACAP" in launcher.dialog.last_msgbox_title

    def test_cancel_on_first_input(self):
        launcher = _make_launcher()
        launcher.dialog.inputbox_returns = [None]
        launcher._show_voacap()
        # Should not crash; no msgbox shown
        assert launcher.dialog.last_msgbox_title is None

    @patch('commands.propagation.get_voacap_online')
    def test_invalid_coords(self, mock_get):
        launcher = _make_launcher()
        launcher.dialog.inputbox_returns = ["abc", "def", "ghi", "jkl"]
        launcher._show_voacap()
        assert launcher.dialog.last_msgbox_title == "Error"
        assert "Invalid" in launcher.dialog.last_msgbox_text


# ===========================================================================
# Source configuration
# ===========================================================================

class TestConfigureSources:

    @patch('commands.propagation.get_sources')
    def test_menu_renders(self, mock_get):
        mock_get.return_value = CommandResult.ok(
            "sources",
            data={
                'sources': {
                    'noaa': {'enabled': True},
                    'openhamclock': {'enabled': False, 'host': 'localhost', 'port': 3000},
                    'hamclock': {'enabled': False, 'host': 'localhost', 'port': 8080},
                    'pskreporter': {'enabled': False},
                }
            }
        )
        launcher = _make_launcher()
        # Menu returns "back" immediately
        launcher.dialog._menu_return = "back"
        launcher._configure_prop_sources()
        assert launcher.dialog.last_menu_title == "Propagation Sources"

    @patch('commands.propagation.check_source')
    @patch('commands.propagation.get_sources')
    def test_test_connectivity(self, mock_sources, mock_check):
        mock_sources.return_value = CommandResult.ok(
            "sources",
            data={'sources': {
                'noaa': {'enabled': True},
                'openhamclock': {'enabled': False},
                'hamclock': {'enabled': False},
                'pskreporter': {'enabled': False},
            }}
        )
        mock_check.return_value = CommandResult.ok("Connected")
        launcher = _make_launcher()
        launcher._test_prop_sources()
        assert "OK" in launcher.dialog.last_msgbox_text


# ===========================================================================
# Menu dispatch wiring
# ===========================================================================

class TestMenuWiring:
    """Verify the mixin is wired into MeshForgeLauncher."""

    def test_propagation_menu_exists_on_mixin(self):
        assert callable(getattr(PropagationMixin, '_propagation_menu', None))

    def test_mixin_module_has_class(self):
        """Verify the propagation_mixin module exports PropagationMixin."""
        import propagation_mixin as mod
        assert hasattr(mod, 'PropagationMixin')
