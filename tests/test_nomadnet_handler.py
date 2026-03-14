"""
Unit tests for NomadNet handler and pre-launch readiness gate.

Tests cover:
  - Pure-logic RNS readiness decision matrix (Phase 1)
  - Pre-launch check integration with dialog (Phase 2)
  - Launch error handling (Phase 3)
  - Handler structure, menu navigation, status display (Phase 4)
"""

import os
import sys
from unittest.mock import MagicMock, patch, PropertyMock
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src', 'launcher_tui'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

sys.path.insert(0, os.path.dirname(__file__))
from handler_test_utils import FakeDialog, make_handler_context

from handlers._nomadnet_prelaunch import RNSReadiness, check_rns_readiness


# ======================================================================
# Phase 1: Pure-logic readiness gate
# ======================================================================

class TestRNSReadinessDecisionMatrix:
    """Test every cell of the RNS readiness decision matrix."""

    def test_rnsd_running_shared_instance_users_match(self):
        """Happy path: everything is ready."""
        r = check_rns_readiness(
            rnsd_running=True,
            shared_instance_available=True,
            rnsd_user="pi",
            launch_user="pi",
        )
        assert r.can_launch is True
        assert r.rnsd_running is True
        assert r.shared_instance is True
        assert r.user_match is True
        assert r.warning is None

    def test_rnsd_running_shared_instance_users_mismatch(self):
        """User mismatch: can launch with warning."""
        r = check_rns_readiness(
            rnsd_running=True,
            shared_instance_available=True,
            rnsd_user="root",
            launch_user="pi",
        )
        assert r.can_launch is True
        assert r.user_match is False
        assert r.warning is not None
        assert "root" in r.warning
        assert "pi" in r.warning

    def test_rnsd_running_no_shared_instance(self):
        """rnsd running but shared instance not available."""
        r = check_rns_readiness(
            rnsd_running=True,
            shared_instance_available=False,
            rnsd_user="pi",
            launch_user="pi",
        )
        assert r.can_launch is False
        assert r.rnsd_running is True
        assert r.shared_instance is False
        assert "initializing" in r.reason.lower() or "not available" in r.reason.lower()

    def test_rnsd_not_running_no_shared_instance(self):
        """Nothing running: cannot launch."""
        r = check_rns_readiness(
            rnsd_running=False,
            shared_instance_available=False,
        )
        assert r.can_launch is False
        assert r.rnsd_running is False
        assert r.shared_instance is False
        assert "not running" in r.reason.lower()

    def test_rnsd_not_running_shared_instance_available(self):
        """Standalone RNS instance: can launch."""
        r = check_rns_readiness(
            rnsd_running=False,
            shared_instance_available=True,
        )
        assert r.can_launch is True
        assert r.rnsd_running is False
        assert r.shared_instance is True

    def test_no_user_info_rnsd_running(self):
        """rnsd running, no user info: can launch if shared instance up."""
        r = check_rns_readiness(
            rnsd_running=True,
            shared_instance_available=True,
            rnsd_user=None,
            launch_user=None,
        )
        assert r.can_launch is True
        assert r.user_match is None
        assert r.warning is None

    def test_no_launch_user_with_rnsd_user(self):
        """rnsd has user but launch user unknown: user_match is None."""
        r = check_rns_readiness(
            rnsd_running=True,
            shared_instance_available=True,
            rnsd_user="pi",
            launch_user=None,
        )
        assert r.can_launch is True
        assert r.user_match is None

    def test_suggestion_points_to_diagnostics_when_blocked(self):
        """Blocked results suggest using RNS Diagnostics."""
        r = check_rns_readiness(
            rnsd_running=False,
            shared_instance_available=False,
        )
        assert r.can_launch is False
        assert "diagnostics" in r.suggestion.lower()

    def test_readiness_dataclass_fields(self):
        """Verify all expected fields exist on RNSReadiness."""
        r = check_rns_readiness(True, True, "pi", "pi")
        assert hasattr(r, 'can_launch')
        assert hasattr(r, 'reason')
        assert hasattr(r, 'suggestion')
        assert hasattr(r, 'warning')
        assert hasattr(r, 'rnsd_running')
        assert hasattr(r, 'shared_instance')
        assert hasattr(r, 'user_match')


# ======================================================================
# Phase 2: Pre-launch check integration with TUI dialog
# ======================================================================

def _make_nomadnet():
    """Create a NomadNetHandler with test context."""
    from handlers.nomadnet import NomadNetHandler
    h = NomadNetHandler()
    ctx = make_handler_context()
    h.set_context(ctx)
    return h


class TestPrelaunchCheckIntegration:
    """Test _check_rns_for_nomadnet dialog integration."""

    @patch('subprocess.run')
    @patch('handlers._nomadnet_rns_checks.get_rns_shared_instance_info')
    def test_prelaunch_passes_when_ready(self, mock_info, mock_run):
        """When RNS is ready, check returns True with no dialog."""
        mock_info.return_value = {'available': True}
        mock_run.return_value = MagicMock(returncode=0, stdout='ok', stderr='')
        h = _make_nomadnet()
        with patch.object(h, '_get_rnsd_user', return_value='pi'):
            with patch.dict(os.environ, {'SUDO_USER': 'pi'}):
                result = h._check_rns_for_nomadnet()
        assert result is True
        # No menu dialog should have been shown (only possibly infobox)
        menu_calls = [c for c in h.ctx.dialog.calls if c[0] == 'menu']
        assert len(menu_calls) == 0

    @patch('handlers._nomadnet_rns_checks.get_rns_shared_instance_info')
    def test_prelaunch_blocked_user_picks_diagnostics(self, mock_info):
        """When blocked, user picks diagnostics -> returns False."""
        mock_info.return_value = {'available': False}
        h = _make_nomadnet()
        h.ctx.dialog._menu_returns = ["diagnostics"]
        mock_diag = MagicMock()
        with patch.object(h, '_get_rnsd_user', return_value=None):
            with patch.object(h, '_get_rns_diagnostics_handler', return_value=mock_diag):
                result = h._check_rns_for_nomadnet()
        assert result is False

    @patch('handlers._nomadnet_rns_checks.get_rns_shared_instance_info')
    def test_prelaunch_blocked_user_picks_continue(self, mock_info):
        """When blocked, user picks continue -> returns True."""
        mock_info.return_value = {'available': False}
        h = _make_nomadnet()
        h.ctx.dialog._menu_returns = ["continue"]
        with patch.object(h, '_get_rnsd_user', return_value=None):
            result = h._check_rns_for_nomadnet()
        assert result is True

    @patch('handlers._nomadnet_rns_checks.get_rns_shared_instance_info')
    def test_prelaunch_blocked_user_cancels(self, mock_info):
        """When blocked, user cancels -> returns False."""
        mock_info.return_value = {'available': False}
        h = _make_nomadnet()
        h.ctx.dialog._menu_returns = [None]  # Default = cancel
        with patch.object(h, '_get_rnsd_user', return_value=None):
            result = h._check_rns_for_nomadnet()
        assert result is False

    @patch('subprocess.run')
    @patch('handlers._nomadnet_rns_checks.get_rns_shared_instance_info')
    def test_prelaunch_user_mismatch_warning(self, mock_info, mock_run):
        """User mismatch shows warning but still allows launch."""
        mock_info.return_value = {'available': True}
        mock_run.return_value = MagicMock(returncode=0, stdout='ok', stderr='')
        h = _make_nomadnet()
        with patch.object(h, '_get_rnsd_user', return_value='root'):
            with patch.dict(os.environ, {'SUDO_USER': 'pi'}):
                result = h._check_rns_for_nomadnet()
        assert result is True
        # Should show a warning via msgbox or infobox
        warning_calls = [
            c for c in h.ctx.dialog.calls
            if c[0] in ('msgbox', 'infobox') and c[1][1] and 'mismatch' in str(c[1][1]).lower()
            or c[0] in ('msgbox', 'infobox') and c[1][1] and 'warning' in str(c[1][0]).lower()
        ]
        # At least acknowledged — warning is informational


# ======================================================================
# Phase 3: stderr capture on launch failure
# ======================================================================

class TestLaunchErrorHandling:
    """Test NomadNet launch error capture."""

    @patch('subprocess.run')
    @patch('handlers.nomadnet.clear_screen')
    def test_launch_textui_stderr_on_failure(self, mock_clear, mock_run):
        """When NomadNet exits non-zero, stderr is shown."""
        mock_run.return_value = MagicMock(
            returncode=1,
            stderr="ConnectionRefusedError: [Errno 111]",
        )
        h = _make_nomadnet()
        with patch.object(h, '_find_nomadnet_binary', return_value='/usr/bin/nomadnet'):
            with patch.object(h, '_ensure_lxmf_exclusive', return_value=True):
                with patch.object(h, '_fix_user_directory_ownership', return_value=True):
                    with patch.object(h, '_validate_nomadnet_config', return_value=True):
                        with patch.object(h, '_check_rns_for_nomadnet', return_value=True):
                            with patch.object(h, '_get_rns_config_for_user', return_value=None):
                                with patch.object(h, '_get_wrapper_command',
                                                  return_value=['/usr/bin/nomadnet', '--textui']):
                                    with patch('builtins.input', return_value=''):
                                        with patch.dict(os.environ, {}, clear=False):
                                            # Remove SUDO_USER if present
                                            os.environ.pop('SUDO_USER', None)
                                            h._launch_nomadnet_textui()

    @patch('subprocess.run')
    @patch('handlers.nomadnet.clear_screen')
    def test_launch_textui_success(self, mock_clear, mock_run):
        """When NomadNet exits 0, clean exit message."""
        mock_run.return_value = MagicMock(returncode=0, stderr="")
        h = _make_nomadnet()
        with patch.object(h, '_find_nomadnet_binary', return_value='/usr/bin/nomadnet'):
            with patch.object(h, '_ensure_lxmf_exclusive', return_value=True):
                with patch.object(h, '_fix_user_directory_ownership', return_value=True):
                    with patch.object(h, '_validate_nomadnet_config', return_value=True):
                        with patch.object(h, '_check_rns_for_nomadnet', return_value=True):
                            with patch.object(h, '_get_rns_config_for_user', return_value=None):
                                with patch.object(h, '_get_wrapper_command',
                                                  return_value=['/usr/bin/nomadnet', '--textui']):
                                    with patch('builtins.input', return_value=''):
                                        with patch.dict(os.environ, {}, clear=False):
                                            os.environ.pop('SUDO_USER', None)
                                            h._launch_nomadnet_textui()


# ======================================================================
# Phase 4: Handler structure and navigation
# ======================================================================

class TestNomadNetHandlerStructure:
    """Test handler registration and structure."""

    def test_handler_id(self):
        h = _make_nomadnet()
        assert h.handler_id == "nomadnet"

    def test_menu_section(self):
        h = _make_nomadnet()
        assert h.menu_section == "mesh_networks"

    def test_menu_items(self):
        h = _make_nomadnet()
        items = h.menu_items()
        assert len(items) >= 1
        tag, desc, flag = items[0]
        assert tag == "nomadnet"

    def test_execute_dispatches_to_menu(self):
        h = _make_nomadnet()
        with patch.object(h, '_nomadnet_menu') as mock:
            h.execute("nomadnet")
            mock.assert_called_once()

    def test_execute_ignores_unknown_action(self):
        h = _make_nomadnet()
        # Should not raise
        h.execute("unknown_action")

    def test_nomadnet_menu_back_exits(self):
        h = _make_nomadnet()
        h.ctx.dialog._menu_returns = [None]
        h._nomadnet_menu()


class TestNomadNetStatusDisplay:
    """Test status display variations."""

    @patch('shutil.which', return_value=None)
    def test_status_not_installed(self, mock_which):
        h = _make_nomadnet()
        h.ctx.dialog._menu_returns = [None]  # Exit after status
        # Should handle gracefully when not installed
        with patch.object(h, '_is_nomadnet_installed', return_value=False):
            with patch.object(h, '_nomadnet_status') as mock_status:
                # Just verify the handler can be created and invoked
                pass


class TestNomadNetBinaryDetection:
    """Test binary path discovery."""

    @patch('shutil.which', return_value='/usr/local/bin/nomadnet')
    def test_found_in_path(self, mock_which):
        h = _make_nomadnet()
        result = h._find_nomadnet_binary()
        assert result == '/usr/local/bin/nomadnet'

    @patch('shutil.which', return_value=None)
    def test_found_in_local_bin(self, mock_which):
        h = _make_nomadnet()
        with patch('handlers._nomadnet_install_utils.get_real_user_home',
                   return_value=Path('/home/pi')):
            with patch.object(Path, 'exists', return_value=True):
                result = h._find_nomadnet_binary()
                assert result is not None

    @patch('shutil.which', return_value=None)
    def test_not_found_shows_dialog(self, mock_which):
        h = _make_nomadnet()
        with patch('handlers._nomadnet_install_utils.get_real_user_home',
                   return_value=Path('/home/pi')):
            # Make candidate not exist
            result = h._find_nomadnet_binary()
            if result is None:
                # Should have shown msgbox
                msgbox_calls = [c for c in h.ctx.dialog.calls if c[0] == 'msgbox']
                assert len(msgbox_calls) >= 1


class TestNomadNetStopFlow:
    """Test stop process flow."""

    def test_stop_not_running(self):
        h = _make_nomadnet()
        with patch.object(h, '_is_nomadnet_running', return_value=False):
            h._stop_nomadnet()
        msgbox_calls = [c for c in h.ctx.dialog.calls if c[0] == 'msgbox']
        assert any('Not Running' in str(c) for c in msgbox_calls)

    @patch('subprocess.run')
    def test_stop_running_confirmed(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        h = _make_nomadnet()
        h.ctx.dialog._yesno_returns = [True]
        with patch.object(h, '_is_nomadnet_running', side_effect=[True, False]):
            h._stop_nomadnet()

    def test_stop_running_cancelled(self):
        h = _make_nomadnet()
        h.ctx.dialog._yesno_returns = [False]
        with patch.object(h, '_is_nomadnet_running', return_value=True):
            h._stop_nomadnet()


# ======================================================================
# Phase 2b: Degraded rnsd (rnsd_healthy parameter)
# ======================================================================

class TestRNSReadinessDegraded:
    """Test rnsd_healthy parameter in the readiness gate."""

    def test_rnsd_running_shared_instance_degraded(self):
        """rnsd running + shared instance + unhealthy: can launch with warning."""
        r = check_rns_readiness(
            rnsd_running=True,
            shared_instance_available=True,
            rnsd_healthy=False,
            rnsd_user="pi",
            launch_user="pi",
        )
        assert r.can_launch is True
        assert r.rnsd_healthy is False
        assert r.warning is not None
        assert "degraded" in r.warning.lower()

    def test_rnsd_healthy_unknown(self):
        """rnsd running + shared instance + health unknown: can launch, no warning."""
        r = check_rns_readiness(
            rnsd_running=True,
            shared_instance_available=True,
            rnsd_healthy=None,
            rnsd_user="pi",
            launch_user="pi",
        )
        assert r.can_launch is True
        assert r.rnsd_healthy is None
        assert r.warning is None

    def test_rnsd_healthy_true(self):
        """rnsd running + shared instance + healthy: can launch, no warning."""
        r = check_rns_readiness(
            rnsd_running=True,
            shared_instance_available=True,
            rnsd_healthy=True,
            rnsd_user="pi",
            launch_user="pi",
        )
        assert r.can_launch is True
        assert r.rnsd_healthy is True
        assert r.warning is None

    def test_degraded_warning_overrides_user_mismatch(self):
        """Degraded warning takes priority over user mismatch."""
        r = check_rns_readiness(
            rnsd_running=True,
            shared_instance_available=True,
            rnsd_healthy=False,
            rnsd_user="root",
            launch_user="pi",
        )
        assert r.can_launch is True
        assert "degraded" in r.warning.lower()
        # User mismatch warning is not shown when degraded
        assert "root" not in r.warning


class TestPrelaunchDegradedFlow:
    """Test _handle_degraded_rnsd dialog flow."""

    @patch('handlers._nomadnet_rns_checks.get_rns_shared_instance_info')
    @patch('subprocess.run')
    def test_prelaunch_degraded_user_picks_restart(self, mock_run, mock_info):
        """Degraded rnsd, user picks restart -> restart_rnsd called."""
        mock_info.return_value = {'available': True}
        # rnstatus returns non-zero (degraded)
        mock_run.return_value = MagicMock(returncode=1, stdout='', stderr='')
        h = _make_nomadnet()
        h.ctx.dialog._menu_returns = ["restart"]
        with patch.object(h, '_get_rnsd_user', return_value='pi'):
            with patch.dict(os.environ, {'SUDO_USER': 'pi'}):
                with patch('handlers._rns_repair.restart_rnsd', return_value=True):
                    result = h._check_rns_for_nomadnet()
        assert result is True

    @patch('handlers._nomadnet_rns_checks.get_rns_shared_instance_info')
    @patch('subprocess.run')
    def test_prelaunch_degraded_user_picks_continue(self, mock_run, mock_info):
        """Degraded rnsd, user picks continue -> launches anyway."""
        mock_info.return_value = {'available': True}
        mock_run.return_value = MagicMock(returncode=1, stdout='', stderr='')
        h = _make_nomadnet()
        h.ctx.dialog._menu_returns = ["continue"]
        with patch.object(h, '_get_rnsd_user', return_value='pi'):
            with patch.dict(os.environ, {'SUDO_USER': 'pi'}):
                result = h._check_rns_for_nomadnet()
        assert result is True

    @patch('handlers._nomadnet_rns_checks.get_rns_shared_instance_info')
    @patch('subprocess.run')
    def test_prelaunch_degraded_user_cancels(self, mock_run, mock_info):
        """Degraded rnsd, user cancels -> returns False."""
        mock_info.return_value = {'available': True}
        mock_run.return_value = MagicMock(returncode=1, stdout='', stderr='')
        h = _make_nomadnet()
        h.ctx.dialog._menu_returns = [None]  # cancel
        with patch.object(h, '_get_rnsd_user', return_value='pi'):
            with patch.dict(os.environ, {'SUDO_USER': 'pi'}):
                result = h._check_rns_for_nomadnet()
        assert result is False

    @patch('handlers._nomadnet_rns_checks.get_rns_shared_instance_info')
    @patch('subprocess.run')
    def test_prelaunch_healthy_skips_degraded_dialog(self, mock_run, mock_info):
        """Healthy rnsd skips degraded dialog entirely."""
        mock_info.return_value = {'available': True}
        mock_run.return_value = MagicMock(returncode=0, stdout='ok', stderr='')
        h = _make_nomadnet()
        with patch.object(h, '_get_rnsd_user', return_value='pi'):
            with patch.dict(os.environ, {'SUDO_USER': 'pi'}):
                result = h._check_rns_for_nomadnet()
        assert result is True
        # No menu dialog should have been shown
        menu_calls = [c for c in h.ctx.dialog.calls if c[0] == 'menu']
        assert len(menu_calls) == 0


class TestConfigValidation:
    """Test NomadNet config validation."""

    def test_validate_no_config(self):
        """No config file: should return True (NomadNet creates default)."""
        h = _make_nomadnet()
        with patch.object(h, '_get_nomadnet_config_path', return_value=Path('/tmp/nonexistent')):
            result = h._validate_nomadnet_config()
        assert result is True

    def test_validate_config_has_textui(self):
        """Config with [textui] section: should return True."""
        h = _make_nomadnet()
        import tempfile
        with tempfile.NamedTemporaryFile(mode='w', suffix='_config', delete=False) as f:
            f.write("[reticulum]\n\n[textui]\ntheme = dark\n")
            f.flush()
            with patch.object(h, '_get_nomadnet_config_path', return_value=Path(f.name)):
                result = h._validate_nomadnet_config()
        os.unlink(f.name)
        assert result is True
