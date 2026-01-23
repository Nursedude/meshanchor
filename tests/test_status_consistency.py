"""
Status Consistency Tests

Sprint 1.5: Regression tests to ensure all MeshForge UIs report consistent
service status for rnsd and meshtasticd.

These tests verify that the Single Source of Truth pattern is maintained:
- All status checks should use check_service() from utils.service_check
- No duplicate implementations that could drift out of sync
"""

import pytest
import sys
import os
from unittest.mock import patch, MagicMock

# Add src directory to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))


class TestStatusConsistency:
    """Verify all status implementations use centralized check_service()."""

    def test_gtk_rns_panel_uses_check_service(self):
        """GTK RNS panel should delegate to check_service() for rnsd status."""
        # Import the module to check it has the right structure
        try:
            from src.gtk_ui.panels.rns_mixins import components

            # Verify it imports check_service
            assert hasattr(components, 'HAS_SERVICE_CHECK'), \
                "GTK RNS panel should import check_service flag"

            # Verify _check_rns_service exists and is the delegating version
            if hasattr(components, 'RNSComponentsMixin'):
                mixin = components.RNSComponentsMixin
                assert hasattr(mixin, '_check_rns_service'), \
                    "RNSComponentsMixin should have _check_rns_service method"
        except ImportError:
            pytest.skip("GTK UI not available")

    def test_commands_rns_uses_check_service(self):
        """commands/rns.py should use check_service() for status."""
        from src.commands import rns

        # Verify module has the service check import
        assert hasattr(rns, 'HAS_SERVICE_CHECK'), \
            "commands/rns.py should import HAS_SERVICE_CHECK"

        # Verify get_status exists
        assert hasattr(rns, 'get_status'), \
            "commands/rns.py should have get_status function"

    def test_commands_service_uses_check_service(self):
        """commands/service.py should use check_service() for rnsd/meshtasticd."""
        from src.commands import service

        # Verify module has the service check import
        assert hasattr(service, 'HAS_SERVICE_CHECK'), \
            "commands/service.py should import HAS_SERVICE_CHECK"

        # Verify rnsd config has UDP port
        assert 'rnsd' in service.KNOWN_SERVICES, \
            "KNOWN_SERVICES should include rnsd"
        assert service.KNOWN_SERVICES['rnsd'].get('port') == 37428, \
            "rnsd should have UDP port 37428 configured"


class TestServiceCheckContract:
    """Verify check_service() API contract."""

    def test_check_service_returns_service_status(self):
        """check_service() should return ServiceStatus object."""
        from src.utils.service_check import check_service, ServiceStatus

        # Mock the actual service check to avoid system dependencies
        with patch('src.utils.service_check.check_port') as mock_port, \
             patch('src.utils.service_check.check_udp_port') as mock_udp, \
             patch('src.utils.service_check.check_process_running') as mock_proc, \
             patch('src.utils.service_check.check_systemd_service') as mock_systemd:

            mock_port.return_value = False
            mock_udp.return_value = False
            mock_proc.return_value = False
            mock_systemd.return_value = (False, False)  # (active, enabled)

            result = check_service('rnsd')

            # Verify return type
            assert isinstance(result, ServiceStatus), \
                "check_service should return ServiceStatus"

            # Verify required attributes
            assert hasattr(result, 'available'), \
                "ServiceStatus must have 'available' attribute"
            assert hasattr(result, 'state'), \
                "ServiceStatus must have 'state' attribute"
            assert hasattr(result, 'message'), \
                "ServiceStatus must have 'message' attribute"

    def test_check_service_never_returns_none(self):
        """check_service() should never return None."""
        from src.utils.service_check import check_service

        with patch('src.utils.service_check.check_port') as mock_port, \
             patch('src.utils.service_check.check_udp_port') as mock_udp, \
             patch('src.utils.service_check.check_process_running') as mock_proc, \
             patch('src.utils.service_check.check_systemd_service') as mock_systemd:

            mock_port.return_value = False
            mock_udp.return_value = False
            mock_proc.return_value = False
            mock_systemd.return_value = (False, False)

            result = check_service('rnsd')
            assert result is not None, "check_service should never return None"

            result = check_service('meshtasticd')
            assert result is not None, "check_service should never return None"

            result = check_service('unknown_service')
            assert result is not None, "check_service should never return None"

    def test_rnsd_check_uses_udp_port(self):
        """rnsd status check should include UDP port 37428 check."""
        from src.utils.service_check import KNOWN_SERVICES

        # Verify rnsd is configured with UDP port
        assert 'rnsd' in KNOWN_SERVICES, "rnsd should be in KNOWN_SERVICES"
        rnsd_config = KNOWN_SERVICES['rnsd']
        # Port may be the constant value 37428
        assert rnsd_config.get('port') == 37428, \
            "rnsd should be configured with UDP port 37428"
        assert rnsd_config.get('port_type') == 'udp', \
            "rnsd should be configured with port_type 'udp'"


class TestRnsdStatusAcrossUIs:
    """Integration test: rnsd status should be consistent across all UIs."""

    @patch('src.utils.service_check.check_udp_port')
    @patch('src.utils.service_check.check_process_running')
    @patch('src.utils.service_check.check_systemd_service')
    def test_rnsd_running_consistent(self, mock_systemd, mock_proc, mock_udp):
        """When rnsd is running, all UIs should report it as running."""
        # Simulate rnsd running (UDP port in use)
        mock_udp.return_value = True  # Port check succeeds (in use)
        mock_proc.return_value = True  # Process found
        mock_systemd.return_value = (True, True)  # (active, enabled)

        from src.utils.service_check import check_service

        result = check_service('rnsd')
        assert result.available is True, \
            "rnsd should be reported as available when UDP port is in use"

    @patch('src.utils.service_check.check_udp_port')
    @patch('src.utils.service_check.check_process_running')
    @patch('src.utils.service_check.check_systemd_service')
    def test_rnsd_stopped_consistent(self, mock_systemd, mock_proc, mock_udp):
        """When rnsd is stopped, all UIs should report it as stopped."""
        # Simulate rnsd stopped
        mock_udp.return_value = False  # Port not in use
        mock_proc.return_value = False  # No process
        mock_systemd.return_value = (False, False)  # (inactive, disabled)

        from src.utils.service_check import check_service

        result = check_service('rnsd')
        assert result.available is False, \
            "rnsd should be reported as unavailable when stopped"


class TestMeshtasticdStatusConsistency:
    """Verify meshtasticd status is consistent across UIs."""

    @patch('subprocess.run')
    def test_meshtasticd_running_consistent(self, mock_run):
        """When meshtasticd is running, all UIs should report it as running.

        Issue #17: meshtasticd is a systemd service, so we trust systemctl only.
        """
        # First call: systemctl is-active → "active"
        # Second call: systemctl show --property=SubState → "SubState=running"
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout='active\n'),
            MagicMock(returncode=0, stdout='SubState=running\n'),
        ]

        from src.utils.service_check import check_service

        result = check_service('meshtasticd')
        assert result.available is True, \
            "meshtasticd should be reported as available when systemctl says active"
        assert result.detection_method == "systemctl", \
            "meshtasticd status should be via systemctl"

    @patch('subprocess.run')
    def test_meshtasticd_stopped_consistent(self, mock_run):
        """When meshtasticd is stopped, all UIs should report it as stopped.

        Issue #17: meshtasticd is a systemd service, so we trust systemctl only.
        """
        # systemctl is-active meshtasticd returns "inactive"
        mock_run.return_value = MagicMock(
            returncode=3,
            stdout='inactive\n'
        )

        from src.utils.service_check import check_service

        result = check_service('meshtasticd')
        assert result.available is False, \
            "meshtasticd should be reported as unavailable when systemctl says inactive"


class TestNoOrphanedImplementations:
    """Verify no orphaned/duplicate status implementations exist."""

    def test_no_duplicate_rnsd_check_in_commands_rns(self):
        """commands/rns.py should delegate to check_service, not implement own."""
        from src.commands import rns
        import inspect
        source = inspect.getsource(rns.get_status)

        # Verify it uses check_service
        assert 'check_service' in source or 'HAS_SERVICE_CHECK' in source, \
            "commands/rns.py get_status should use check_service"
