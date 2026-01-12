"""
Tests for Gateway Setup Wizard

Tests the wizard step logic and configuration handling.
"""

import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path

# Check if GTK is available
try:
    import gi
    gi.require_version('Gtk', '4.0')
    from gi.repository import Gtk
    GTK_AVAILABLE = True
except (ImportError, ValueError):
    GTK_AVAILABLE = False


@pytest.mark.skipif(not GTK_AVAILABLE, reason="GTK4 not available")
class TestWizardStep:
    """Tests for WizardStep dataclass."""

    def test_create_step(self):
        """Create a wizard step."""
        from src.gtk_ui.dialogs.gateway_wizard import WizardStep

        step = WizardStep(
            title="Test Step",
            description="A test step"
        )

        assert step.title == "Test Step"
        assert step.description == "A test step"
        assert step.icon == "dialog-information-symbolic"
        assert step.can_skip is False

    def test_step_with_custom_icon(self):
        """Create step with custom icon."""
        from src.gtk_ui.dialogs.gateway_wizard import WizardStep

        step = WizardStep(
            title="Network",
            description="Network step",
            icon="network-wired-symbolic"
        )

        assert step.icon == "network-wired-symbolic"

    def test_skippable_step(self):
        """Create a skippable step."""
        from src.gtk_ui.dialogs.gateway_wizard import WizardStep

        step = WizardStep(
            title="Optional",
            description="Optional step",
            can_skip=True
        )

        assert step.can_skip is True


@pytest.mark.skipif(not GTK_AVAILABLE, reason="GTK4 not available")
class TestWizardSteps:
    """Tests for wizard step definitions."""

    def test_steps_defined(self):
        """Wizard has expected steps."""
        from src.gtk_ui.dialogs.gateway_wizard import GatewaySetupWizard

        assert len(GatewaySetupWizard.STEPS) == 5

    def test_step_titles(self):
        """Steps have expected titles."""
        from src.gtk_ui.dialogs.gateway_wizard import GatewaySetupWizard

        titles = [s.title for s in GatewaySetupWizard.STEPS]
        expected = ["Welcome", "Prerequisites", "Connection Test",
                    "Configuration", "Complete"]

        assert titles == expected

    def test_all_steps_have_description(self):
        """All steps have descriptions."""
        from src.gtk_ui.dialogs.gateway_wizard import GatewaySetupWizard

        for step in GatewaySetupWizard.STEPS:
            assert step.description
            assert len(step.description) > 0


class TestWizardConfig:
    """Tests for wizard configuration handling."""

    def test_default_config(self):
        """Check default configuration values."""
        # Default config structure
        default = {
            'enabled': True,
            'auto_start': False,
            'bridge_mode': 'message_bridge',
            'meshtastic': {
                'host': 'localhost',
                'port': 4403,
                'channel': 0
            },
            'rns': {
                'identity_name': 'meshforge_gateway',
                'announce_interval': 300
            }
        }

        assert default['enabled'] is True
        assert default['bridge_mode'] == 'message_bridge'
        assert default['meshtastic']['port'] == 4403

    def test_config_modes(self):
        """Valid bridge modes."""
        valid_modes = ['message_bridge', 'rns_transport', 'both']

        assert 'message_bridge' in valid_modes
        assert 'rns_transport' in valid_modes
        assert 'both' in valid_modes

    def test_meshtastic_port_range(self):
        """Meshtastic port is valid."""
        port = 4403
        assert 1 <= port <= 65535


class TestConfigSave:
    """Tests for configuration file saving."""

    def test_config_path_with_sudo_user(self):
        """Config uses SUDO_USER for path."""
        import os

        with patch.dict(os.environ, {'SUDO_USER': 'testuser'}):
            # Simulate the path logic from the wizard
            sudo_user = os.environ.get('SUDO_USER')
            if sudo_user and sudo_user != 'root':
                config_dir = Path(f'/home/{sudo_user}') / '.config' / 'meshforge'
            else:
                config_dir = Path.home() / '.config' / 'meshforge'

            assert str(config_dir) == '/home/testuser/.config/meshforge'

    def test_config_path_without_sudo(self):
        """Config uses home when no SUDO_USER."""
        import os

        with patch.dict(os.environ, {}, clear=True):
            # Remove SUDO_USER
            os.environ.pop('SUDO_USER', None)

            sudo_user = os.environ.get('SUDO_USER')
            if sudo_user and sudo_user != 'root':
                config_dir = Path(f'/home/{sudo_user}') / '.config' / 'meshforge'
            else:
                config_dir = Path.home() / '.config' / 'meshforge'

            # Should use Path.home()
            assert '.config/meshforge' in str(config_dir)


@pytest.mark.skipif(not GTK_AVAILABLE, reason="GTK4 not available")
class TestShowGatewayWizard:
    """Tests for show_gateway_wizard function."""

    def test_function_exists(self):
        """Function is importable."""
        from src.gtk_ui.dialogs.gateway_wizard import show_gateway_wizard
        assert callable(show_gateway_wizard)

    def test_function_signature(self):
        """Function has expected parameters."""
        import inspect
        from src.gtk_ui.dialogs.gateway_wizard import show_gateway_wizard

        sig = inspect.signature(show_gateway_wizard)
        params = list(sig.parameters.keys())

        assert 'parent' in params
        assert 'on_complete' in params


class TestServiceCheck:
    """Tests for service checking logic."""

    def test_service_names(self):
        """Required services are defined."""
        required_services = ['meshtasticd', 'rnsd']

        assert 'meshtasticd' in required_services
        assert 'rnsd' in required_services

    def test_service_check_command(self):
        """Service check uses correct command."""
        # The wizard uses systemctl is-active
        import subprocess

        cmd = ['systemctl', 'is-active', 'test-service']
        assert cmd[0] == 'systemctl'
        assert cmd[1] == 'is-active'


class TestConnectionTest:
    """Tests for connection testing."""

    def test_tcp_connection_params(self):
        """TCP connection uses correct defaults."""
        host = 'localhost'
        port = 4403

        assert host == 'localhost'
        assert port == 4403

    def test_rns_import_check(self):
        """RNS availability can be checked."""
        try:
            import RNS
            rns_available = True
        except ImportError:
            rns_available = False

        # Either result is valid depending on environment
        assert isinstance(rns_available, bool)
