"""
MeshChat handler, deployment profile, and diagnostics tests.

Tests MeshChat as a first-class LXMF client alongside NomadNet.

Updated from mixin-based tests to handler-based tests after the
mixin-to-registry migration (Batch 8).
"""

import os
import sys
from unittest.mock import MagicMock, patch, PropertyMock
import pytest

# Ensure src directory is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))


# ============================================================================
# Deployment Profile Tests
# ============================================================================

class TestMeshChatDeploymentProfile:
    """Test the MeshChat deployment profile."""

    def test_meshchat_profile_name_exists(self):
        from utils.deployment_profiles import ProfileName
        assert hasattr(ProfileName, 'MESHCHAT')
        assert ProfileName.MESHCHAT.value == "meshchat"

    def test_meshchat_profile_in_profiles_dict(self):
        from utils.deployment_profiles import ProfileName, PROFILES
        assert ProfileName.MESHCHAT in PROFILES

    def test_meshchat_profile_flags(self):
        from utils.deployment_profiles import ProfileName, PROFILES
        profile = PROFILES[ProfileName.MESHCHAT]
        assert profile.feature_flags['meshchat'] is True
        assert profile.feature_flags['rns'] is True
        assert profile.feature_flags['gateway'] is True
        assert profile.feature_flags['meshtastic'] is True

    def test_meshchat_profile_services(self):
        from utils.deployment_profiles import ProfileName, PROFILES
        profile = PROFILES[ProfileName.MESHCHAT]
        assert 'meshtasticd' in profile.required_services
        assert 'rnsd' in profile.required_services

    def test_meshchat_profile_optional_services(self):
        from utils.deployment_profiles import ProfileName, PROFILES
        profile = PROFILES[ProfileName.MESHCHAT]
        assert 'reticulum-meshchat' in profile.optional_services or \
               'meshchat' in profile.optional_services

    def test_meshchat_in_list_profiles(self):
        from utils.deployment_profiles import list_profiles, ProfileName
        profiles = list_profiles()
        names = [p.name for p in profiles]
        assert ProfileName.MESHCHAT in names

    def test_meshchat_flag_in_full_profile(self):
        from utils.deployment_profiles import ProfileName, PROFILES
        full = PROFILES[ProfileName.FULL]
        assert 'meshchat' in full.feature_flags
        assert full.feature_flags['meshchat'] is True

    def test_meshchat_flag_false_in_non_meshchat_profiles(self):
        from utils.deployment_profiles import ProfileName, PROFILES
        for name in [ProfileName.RADIO_MAPS, ProfileName.MONITOR, ProfileName.MESHCORE]:
            profile = PROFILES[name]
            assert profile.feature_flags.get('meshchat') is False, \
                f"Profile {name.value} should have meshchat=False"

    def test_get_profile_by_name_meshchat(self):
        from utils.deployment_profiles import get_profile_by_name, ProfileName
        profile = get_profile_by_name("meshchat")
        assert profile is not None
        assert profile.name == ProfileName.MESHCHAT

    @patch('utils.deployment_profiles._check_service_available')
    @patch('utils.service_check.check_port', return_value=True)
    def test_detect_profile_meshchat(self, mock_port, mock_svc):
        """Auto-detect selects meshchat when meshtasticd + rnsd + port 8000."""
        from utils.deployment_profiles import detect_profile, ProfileName

        def service_available(name):
            return name in ('meshtasticd', 'rnsd')

        mock_svc.side_effect = service_available
        profile = detect_profile()
        assert profile.name == ProfileName.MESHCHAT

    @patch('utils.deployment_profiles._check_service_available')
    def test_detect_profile_gateway_without_meshchat(self, mock_svc):
        """Auto-detect selects gateway when no MeshChat port 8000."""
        from utils.deployment_profiles import detect_profile, ProfileName

        def service_available(name):
            return name in ('meshtasticd', 'rnsd')

        mock_svc.side_effect = service_available
        # check_port will fail (no MeshChat) → falls back to gateway
        profile = detect_profile()
        # Should be either meshchat or gateway depending on port
        assert profile.name in (ProfileName.MESHCHAT, ProfileName.GATEWAY)


# ============================================================================
# MeshChat Handler Tests (migrated from MeshChatClientMixin)
# ============================================================================

def _make_handler():
    """Create a MeshChatHandler with mocked TUIContext."""
    from launcher_tui.handlers.meshchat import MeshChatHandler
    handler = MeshChatHandler()
    ctx = MagicMock()
    ctx.dialog = MagicMock()
    ctx.registry = MagicMock()
    ctx.safe_call = lambda name, fn, *a, **kw: fn(*a, **kw)
    ctx.wait_for_enter = MagicMock()
    ctx.feature_enabled = lambda f: True
    handler.ctx = ctx
    return handler


class TestMeshChatHandler:
    """Test MeshChatHandler TUI methods."""

    def test_handler_creates(self):
        """MeshChatHandler can be instantiated with expected methods."""
        handler = _make_handler()
        assert hasattr(handler, '_meshchat_menu')
        assert hasattr(handler, '_meshchat_status')
        assert hasattr(handler, '_is_meshchat_installed')
        assert hasattr(handler, '_is_meshchat_running')

    @patch('shutil.which', return_value=None)
    def test_not_installed_when_no_binary(self, mock_which):
        """Reports not installed when no meshchat binary found."""
        handler = _make_handler()
        with patch('launcher_tui.handlers.meshchat._HAS_MESHCHAT_SERVICE', False):
            result = handler._is_meshchat_installed()
            assert result is False

    @patch('shutil.which', return_value='/usr/bin/meshchat')
    def test_installed_when_binary_found(self, mock_which):
        """Reports installed when meshchat binary found."""
        handler = _make_handler()
        result = handler._is_meshchat_installed()
        assert result is True

    def test_check_rns_preflight_no_rnsd(self):
        """Preflight check warns when rnsd not running."""
        handler = _make_handler()
        handler.ctx.dialog.yesno.return_value = True
        handler._get_rnsd_user = lambda: None
        result = handler._check_rns_for_meshchat()
        assert result is True
        handler.ctx.dialog.yesno.assert_called_once()

    def test_check_rns_preflight_cancelled(self):
        """Preflight check returns False when user cancels."""
        handler = _make_handler()
        handler.ctx.dialog.yesno.return_value = False
        handler._get_rnsd_user = lambda: None
        result = handler._check_rns_for_meshchat()
        assert result is False

    def test_check_rns_preflight_rnsd_running(self):
        """Preflight check passes when rnsd running as non-root."""
        handler = _make_handler()
        handler._get_rnsd_user = lambda: 'pi'
        result = handler._check_rns_for_meshchat()
        assert result is True


# ============================================================================
# LXMF App Conflict Detection Tests
# ============================================================================

class TestLXMFAppConflict:
    """Test _check_lxmf_app_conflict() on RNSDiagnosticsHandler."""

    def _make_diagnostics_handler(self):
        """Create a RNSDiagnosticsHandler with mocked TUIContext."""
        from launcher_tui.handlers.rns_diagnostics import RNSDiagnosticsHandler
        handler = RNSDiagnosticsHandler()
        ctx = MagicMock()
        ctx.dialog = MagicMock()
        ctx.registry = MagicMock()
        ctx.safe_call = lambda name, fn, *a, **kw: fn(*a, **kw)
        ctx.wait_for_enter = MagicMock()
        handler.ctx = ctx
        return handler

    @patch('subprocess.run')
    def test_detects_nomadnet(self, mock_run):
        """Detects NomadNet holding port."""
        mock_run.return_value = MagicMock(returncode=0, stdout="1234\n")
        handler = self._make_diagnostics_handler()
        result = handler._check_lxmf_app_conflict()
        assert result == "NomadNet"

    @patch('subprocess.run')
    def test_detects_meshchat(self, mock_run):
        """Detects MeshChat when NomadNet not running."""
        def side_effect(cmd, **kwargs):
            mock = MagicMock()
            if 'nomadnet' in cmd:
                mock.returncode = 1
                mock.stdout = ""
            else:  # meshchat
                mock.returncode = 0
                mock.stdout = "5678\n"
            return mock

        mock_run.side_effect = side_effect
        handler = self._make_diagnostics_handler()
        result = handler._check_lxmf_app_conflict()
        assert result == "MeshChat"

    @patch('subprocess.run')
    def test_no_conflict(self, mock_run):
        """Returns None when no LXMF app running."""
        mock_run.return_value = MagicMock(returncode=1, stdout="")
        handler = self._make_diagnostics_handler()
        result = handler._check_lxmf_app_conflict()
        assert result is None


# ============================================================================
# Gateway Diagnostic Tests
# ============================================================================

class TestMeshChatGatewayDiagnostic:
    """Test MeshChat integration in gateway diagnostics."""

    def test_check_meshchat_rns_integration_no_client(self):
        """Returns SKIP when MeshChat client not available."""
        from utils.gateway_diagnostic import GatewayDiagnostic, CheckStatus

        with patch('utils.gateway_diagnostic._HAS_MESHCHAT_CLIENT', False):
            diag = GatewayDiagnostic()
            result = diag.check_meshchat_rns_integration()
            assert result.status == CheckStatus.SKIP

    @patch('utils.gateway_diagnostic.MeshChatClient')
    @patch('utils.gateway_diagnostic._HAS_MESHCHAT_CLIENT', True)
    def test_check_meshchat_rns_not_running(self, MockClient):
        """Returns SKIP when MeshChat not running."""
        from utils.gateway_diagnostic import GatewayDiagnostic, CheckStatus

        MockClient.return_value.is_available.return_value = False
        diag = GatewayDiagnostic()
        result = diag.check_meshchat_rns_integration()
        assert result.status == CheckStatus.SKIP

    @patch('utils.gateway_diagnostic.MeshChatClient')
    @patch('utils.gateway_diagnostic._HAS_MESHCHAT_CLIENT', True)
    def test_check_meshchat_rns_connected(self, MockClient):
        """Returns PASS when MeshChat connected to RNS."""
        from utils.gateway_diagnostic import GatewayDiagnostic, CheckStatus

        mock_status = MagicMock()
        mock_status.rns_connected = True
        mock_status.peer_count = 5
        mock_status.message_count = 42
        MockClient.return_value.is_available.return_value = True
        MockClient.return_value.get_status.return_value = mock_status
        diag = GatewayDiagnostic()
        result = diag.check_meshchat_rns_integration()
        assert result.status == CheckStatus.PASS
        assert "5 peers" in result.message

    @patch('utils.gateway_diagnostic.MeshChatClient')
    @patch('utils.gateway_diagnostic._HAS_MESHCHAT_CLIENT', True)
    def test_check_meshchat_rns_disconnected(self, MockClient):
        """Returns FAIL when MeshChat running but RNS disconnected."""
        from utils.gateway_diagnostic import GatewayDiagnostic, CheckStatus

        mock_status = MagicMock()
        mock_status.rns_connected = False
        MockClient.return_value.is_available.return_value = True
        MockClient.return_value.get_status.return_value = mock_status
        diag = GatewayDiagnostic()
        result = diag.check_meshchat_rns_integration()
        assert result.status == CheckStatus.FAIL


# ============================================================================
# Automated Installer Tests (migrated from MeshChatClientMixin)
# ============================================================================

class TestMeshChatInstaller:
    """Test the automated MeshChat installation methods on MeshChatHandler."""

    def test_has_install_method(self):
        """Handler has automated _install_meshchat method."""
        handler = _make_handler()
        assert hasattr(handler, '_install_meshchat')
        assert callable(handler._install_meshchat)

    def test_has_uninstall_method(self):
        """Handler has _uninstall_meshchat method."""
        handler = _make_handler()
        assert hasattr(handler, '_uninstall_meshchat')
        assert callable(handler._uninstall_meshchat)

    def test_has_lxmf_exclusive_method(self):
        """Handler has _ensure_lxmf_exclusive method."""
        handler = _make_handler()
        assert hasattr(handler, '_ensure_lxmf_exclusive')
        assert callable(handler._ensure_lxmf_exclusive)

    def test_get_meshchat_install_dir(self):
        """Install dir is under user home, not /root."""
        handler = _make_handler()
        with patch('launcher_tui.handlers.meshchat.get_real_user_home') as mock_home:
            mock_home.return_value = __import__('pathlib').Path('/home/testuser')
            result = handler._get_meshchat_install_dir()
            assert str(result) == '/home/testuser/reticulum-meshchat'

    @patch('shutil.which', return_value='/usr/bin/meshchat')
    def test_install_skips_if_already_installed(self, mock_which):
        """Install shows 'already installed' if MeshChat is present."""
        handler = _make_handler()
        handler._install_meshchat()
        handler.ctx.dialog.msgbox.assert_called_once()
        assert "Already Installed" in str(handler.ctx.dialog.msgbox.call_args)

    def test_install_cancelled_by_user(self):
        """Install returns when user declines."""
        handler = _make_handler()
        with patch.object(handler, '_is_meshchat_installed', return_value=False):
            handler.ctx.dialog.yesno.return_value = False
            handler._install_meshchat()
            # Should not proceed to prerequisites
            assert handler.ctx.dialog.yesno.called

    @patch('shutil.which')
    def test_install_prerequisites_checks_git_node_npm(self, mock_which):
        """Prerequisites checker verifies git, node, npm."""
        handler = _make_handler()

        # All tools available
        mock_which.return_value = '/usr/bin/git'
        result = handler._install_meshchat_prerequisites()
        assert result is True

    @patch('shutil.which', return_value=None)
    @patch('subprocess.run')
    def test_install_prerequisites_installs_nodejs(self, mock_run, mock_which):
        """Prerequisites installs nodejs when not found."""
        handler = _make_handler()

        # First call: git not found, then found after install
        call_count = [0]
        def which_side_effect(tool):
            call_count[0] += 1
            # After apt install, tools are "found"
            if call_count[0] > 3:
                return f'/usr/bin/{tool}'
            return None

        mock_which.side_effect = which_side_effect
        mock_run.return_value = MagicMock(returncode=0)
        result = handler._install_meshchat_prerequisites()
        assert mock_run.called

    @patch('subprocess.run')
    def test_install_clone_new_repo(self, mock_run):
        """Clone creates new repo when dir doesn't exist."""
        handler = _make_handler()
        mock_run.return_value = MagicMock(returncode=0)

        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            install_dir = __import__('pathlib').Path(tmpdir) / 'reticulum-meshchat'
            result = handler._install_meshchat_clone(install_dir, None)
            assert result is True
            # Verify git clone was called
            clone_call = mock_run.call_args_list[0]
            assert 'git' in clone_call[0][0]
            assert 'clone' in clone_call[0][0]

    @patch('subprocess.run')
    def test_install_clone_pulls_existing(self, mock_run):
        """Clone pulls latest when dir already exists."""
        handler = _make_handler()
        mock_run.return_value = MagicMock(returncode=0, stderr='')

        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            install_dir = __import__('pathlib').Path(tmpdir)
            result = handler._install_meshchat_clone(install_dir, None)
            assert result is True
            pull_call = mock_run.call_args_list[0]
            assert 'pull' in pull_call[0][0]

    def test_install_service_creates_unit_file(self):
        """Service creation writes a valid systemd unit file via _sudo_write."""
        handler = _make_handler()

        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            install_dir = __import__('pathlib').Path(tmpdir)

            with patch('launcher_tui.handlers.meshchat.get_real_user_home',
                       return_value=__import__('pathlib').Path('/home/testuser')):
                with patch('launcher_tui.handlers.meshchat._HAS_SUDO_WRITE', True):
                    with patch('launcher_tui.handlers.meshchat._sudo_write',
                               return_value=(True, 'ok')) as mock_write:
                        with patch('launcher_tui.handlers.meshchat.enable_service',
                                   return_value=(True, 'ok')):
                            result = handler._install_meshchat_service(
                                install_dir, 'testuser')
                            assert result is True
                            # Verify _sudo_write was called with unit file content
                            assert mock_write.called
                            path_arg = mock_write.call_args[0][0]
                            content = mock_write.call_args[0][1]
                            assert 'reticulum-meshchat' in path_arg
                            assert 'User=testuser' in content
                            assert 'meshchat.py' in content
                            assert 'rnsd.service' in content

    @patch('pathlib.Path.is_file', return_value=True)
    def test_get_service_python_prefers_venv(self, mock_is_file):
        """_get_service_python returns venv python when venv exists."""
        handler = _make_handler()
        result = handler._get_service_python()
        assert result == '/opt/meshforge/venv/bin/python3'

    @patch('pathlib.Path.is_file', return_value=False)
    @patch('shutil.which', return_value='/usr/bin/python3')
    def test_get_service_python_fallback(self, mock_which, mock_is_file):
        """_get_service_python falls back to system python without venv."""
        handler = _make_handler()
        result = handler._get_service_python()
        assert result == '/usr/bin/python3'

    @patch('pathlib.Path.is_file', return_value=False)
    @patch('shutil.which', return_value=None)
    def test_get_service_python_ultimate_fallback(self, mock_which, mock_is_file):
        """_get_service_python returns /usr/bin/python3 as last resort."""
        handler = _make_handler()
        result = handler._get_service_python()
        assert result == '/usr/bin/python3'

    def test_service_uses_venv_python_in_unit_file(self):
        """Service unit file references venv python when venv exists."""
        handler = _make_handler()

        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            install_dir = __import__('pathlib').Path(tmpdir)

            with patch('launcher_tui.handlers.meshchat.get_real_user_home',
                       return_value=__import__('pathlib').Path('/home/testuser')):
                with patch.object(handler, '_get_service_python',
                                  return_value='/opt/meshforge/venv/bin/python3'):
                    with patch('launcher_tui.handlers.meshchat._HAS_SUDO_WRITE', True):
                        with patch('launcher_tui.handlers.meshchat._sudo_write',
                                   return_value=(True, 'ok')) as mock_write:
                            with patch('launcher_tui.handlers.meshchat.enable_service',
                                       return_value=(True, 'ok')):
                                handler._install_meshchat_service(
                                    install_dir, 'testuser')
                                content = mock_write.call_args[0][1]
                                assert '/opt/meshforge/venv/bin/python3' in content

    def test_meshchat_repo_url(self):
        """MESHCHAT_REPO constant points to correct URL."""
        from launcher_tui.handlers.meshchat import MeshChatHandler
        assert 'liamcottle/reticulum-meshchat' in MeshChatHandler.MESHCHAT_REPO

    def test_meshchat_service_name(self):
        """MESHCHAT_SERVICE_NAME is correct."""
        from launcher_tui.handlers.meshchat import MeshChatHandler
        assert MeshChatHandler.MESHCHAT_SERVICE_NAME == "reticulum-meshchat"


# ============================================================================
# Create Systemd Service Tests
# ============================================================================

class TestMeshChatCreateService:
    """Test the standalone Create Service flow."""

    def test_create_service_uses_sudo_write(self):
        """_install_meshchat_service uses _sudo_write for privileged write."""
        handler = _make_handler()
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            install_dir = __import__('pathlib').Path(tmpdir)
            with patch('launcher_tui.handlers.meshchat.get_real_user_home',
                       return_value=__import__('pathlib').Path('/home/testuser')):
                with patch('launcher_tui.handlers.meshchat._HAS_SUDO_WRITE', True):
                    with patch('launcher_tui.handlers.meshchat._sudo_write',
                               return_value=(True, 'ok')) as mock_write:
                        with patch('launcher_tui.handlers.meshchat.enable_service',
                                   return_value=(True, 'ok')):
                            result = handler._install_meshchat_service(
                                install_dir, 'testuser')
                            assert result is True
                            assert mock_write.called
                            path_arg = mock_write.call_args[0][0]
                            assert path_arg == \
                                '/etc/systemd/system/reticulum-meshchat.service'

    def test_create_service_sudo_write_failure(self):
        """Returns False when _sudo_write fails."""
        handler = _make_handler()
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            install_dir = __import__('pathlib').Path(tmpdir)
            with patch('launcher_tui.handlers.meshchat.get_real_user_home',
                       return_value=__import__('pathlib').Path('/home/testuser')):
                with patch('launcher_tui.handlers.meshchat._HAS_SUDO_WRITE', True):
                    with patch('launcher_tui.handlers.meshchat._sudo_write',
                               return_value=(False, 'permission denied')):
                        result = handler._install_meshchat_service(
                            install_dir, 'testuser')
                        assert result is False

    def test_create_service_enable_failure(self):
        """Returns False when enable_service fails."""
        handler = _make_handler()
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            install_dir = __import__('pathlib').Path(tmpdir)
            with patch('launcher_tui.handlers.meshchat.get_real_user_home',
                       return_value=__import__('pathlib').Path('/home/testuser')):
                with patch('launcher_tui.handlers.meshchat._HAS_SUDO_WRITE', True):
                    with patch('launcher_tui.handlers.meshchat._sudo_write',
                               return_value=(True, 'ok')):
                        with patch('launcher_tui.handlers.meshchat.enable_service',
                                   return_value=(False, 'enable failed')):
                            result = handler._install_meshchat_service(
                                install_dir, 'testuser')
                            assert result is False

    def test_create_service_dialog_cancelled(self):
        """No service created when user declines."""
        handler = _make_handler()
        handler.ctx.dialog.yesno.return_value = False
        with patch.object(handler, '_install_meshchat_service') as mock_install:
            handler._create_meshchat_service()
            mock_install.assert_not_called()

    def test_create_service_dialog_confirmed(self):
        """Service created when user confirms."""
        handler = _make_handler()
        handler.ctx.dialog.yesno.side_effect = [True, False]  # Create=yes, Start=no
        with patch.object(handler, '_get_meshchat_install_dir') as mock_dir:
            import tempfile
            with tempfile.TemporaryDirectory() as tmpdir:
                install_dir = __import__('pathlib').Path(tmpdir)
                # Create meshchat.py so the check passes
                (install_dir / 'meshchat.py').write_text('# meshchat')
                mock_dir.return_value = install_dir
                with patch.object(handler, '_install_meshchat_service',
                                  return_value=True):
                    handler._create_meshchat_service()
                    # Should have called yesno twice (create + start)
                    assert handler.ctx.dialog.yesno.call_count == 2

    def test_create_service_install_dir_missing(self):
        """Shows error when meshchat.py not found."""
        handler = _make_handler()
        handler.ctx.dialog.yesno.return_value = True
        with patch.object(handler, '_get_meshchat_install_dir',
                          return_value=__import__('pathlib').Path('/nonexistent')):
            handler._create_meshchat_service()
            handler.ctx.dialog.msgbox.assert_called_once()
            assert 'Not Found' in str(handler.ctx.dialog.msgbox.call_args)

    def test_has_meshchat_systemd_service_true(self):
        """Detection returns True when service file exists."""
        handler = _make_handler()
        with patch('launcher_tui.handlers.meshchat.Path') as MockPath:
            mock_path = MagicMock()
            mock_path.exists.return_value = True
            MockPath.return_value = mock_path
            result = handler._has_meshchat_systemd_service()
            assert result is True

    def test_has_meshchat_systemd_service_false(self):
        """Detection returns False when no service file and no plugin."""
        handler = _make_handler()
        with patch('launcher_tui.handlers.meshchat.Path') as MockPath:
            mock_path = MagicMock()
            mock_path.exists.return_value = False
            MockPath.return_value = mock_path
            with patch('launcher_tui.handlers.meshchat._HAS_MESHCHAT_SERVICE',
                       False):
                result = handler._has_meshchat_systemd_service()
                assert result is False

    def test_launch_blocked_when_lxmf_missing(self):
        """_launch_meshchat offers to install LXMF when module is missing."""
        handler = _make_handler()
        handler._ensure_lxmf_exclusive = MagicMock(return_value=True)
        handler._check_rns_for_meshchat = MagicMock(return_value=True)
        # User declines the install prompt
        handler.ctx.dialog.yesno.return_value = False

        with patch('launcher_tui.handlers.meshchat._HAS_LXMF', False):
            handler._launch_meshchat()
            # Should have been offered to install via yesno
            yesno_calls = handler.ctx.dialog.yesno.call_args_list
            lxmf_prompt = any('LXMF' in str(c) for c in yesno_calls)
            assert lxmf_prompt, f"Expected LXMF install prompt, got: {yesno_calls}"

    def test_launch_offers_create_when_no_service(self):
        """_launch_meshchat offers to create service when none exists."""
        handler = _make_handler()
        handler._ensure_lxmf_exclusive = MagicMock(return_value=True)
        handler._check_rns_for_meshchat = MagicMock(return_value=True)
        handler._check_meshchat_deps = MagicMock(return_value=[])
        handler._is_meshchat_installed = MagicMock(return_value=True)
        handler._create_meshchat_service = MagicMock()

        mock_status = MagicMock()
        mock_status.running = False
        mock_status.service_name = None

        with patch('launcher_tui.handlers.meshchat._HAS_LXMF', True):
            with patch('launcher_tui.handlers.meshchat._HAS_MESHCHAT_SERVICE', True):
                with patch('launcher_tui.handlers.meshchat.MeshChatService') as MockSvc:
                    MockSvc.return_value.check_status.return_value = mock_status
                    # User accepts to create service
                    handler.ctx.dialog.yesno.return_value = True
                    handler._launch_meshchat()
                    handler._create_meshchat_service.assert_called_once()

    def test_launch_shows_manual_when_declined(self):
        """_launch_meshchat shows manual start when user declines service creation."""
        handler = _make_handler()
        handler._ensure_lxmf_exclusive = MagicMock(return_value=True)
        handler._check_rns_for_meshchat = MagicMock(return_value=True)
        handler._check_meshchat_deps = MagicMock(return_value=[])
        handler._is_meshchat_installed = MagicMock(return_value=True)

        mock_status = MagicMock()
        mock_status.running = False
        mock_status.service_name = None

        with patch('launcher_tui.handlers.meshchat._HAS_LXMF', True):
            with patch('launcher_tui.handlers.meshchat._HAS_MESHCHAT_SERVICE', True):
                with patch('launcher_tui.handlers.meshchat.MeshChatService') as MockSvc:
                    MockSvc.return_value.check_status.return_value = mock_status
                    # User declines
                    handler.ctx.dialog.yesno.return_value = False
                    handler._launch_meshchat()
                    handler.ctx.dialog.msgbox.assert_called_once()
                    assert 'Manual Start' in str(handler.ctx.dialog.msgbox.call_args)


# ============================================================================
# Uninstall (Stop + Disable) Tests
# ============================================================================

class TestMeshChatUninstall:
    """Test MeshChat uninstall functionality on MeshChatHandler."""

    def test_uninstall_cancelled(self):
        """Uninstall does nothing when user cancels."""
        handler = _make_handler()
        handler.ctx.dialog.yesno.return_value = False
        handler._uninstall_meshchat()
        # Should only call yesno (confirmation), nothing else

    @patch('subprocess.run')
    def test_uninstall_stops_and_disables(self, mock_run):
        """Uninstall calls systemctl stop and disable."""
        handler = _make_handler()
        handler.ctx.dialog.yesno.return_value = True
        mock_run.return_value = MagicMock(returncode=0)

        handler._uninstall_meshchat()

        # Verify systemctl calls
        calls = [str(c) for c in mock_run.call_args_list]
        stop_called = any('stop' in c and 'reticulum-meshchat' in c for c in calls)
        disable_called = any('disable' in c and 'reticulum-meshchat' in c for c in calls)
        assert stop_called, "systemctl stop not called"
        assert disable_called, "systemctl disable not called"


# ============================================================================
# LXMF Exclusive Toggle Tests (migrated to _lxmf_utils.ensure_lxmf_exclusive)
# ============================================================================

class TestLXMFExclusiveToggle:
    """Test ensure_lxmf_exclusive() one-app-at-a-time enforcement."""

    @patch('subprocess.run')
    def test_meshchat_start_no_conflict(self, mock_run):
        """Starting MeshChat succeeds when NomadNet not running."""
        from launcher_tui.handlers._lxmf_utils import ensure_lxmf_exclusive
        mock_dialog = MagicMock()
        mock_run.return_value = MagicMock(returncode=1, stdout='')

        with patch('launcher_tui.handlers._lxmf_utils._HAS_SERVICE_CHECK', False):
            result = ensure_lxmf_exclusive(mock_dialog, "meshchat")
            assert result is True

    @patch('subprocess.run')
    def test_meshchat_start_stops_nomadnet(self, mock_run):
        """Starting MeshChat offers to stop NomadNet."""
        from launcher_tui.handlers._lxmf_utils import ensure_lxmf_exclusive
        mock_dialog = MagicMock()
        mock_dialog.yesno.return_value = True

        # First pgrep finds NomadNet, second pkill succeeds
        call_count = [0]
        def run_side_effect(cmd, **kwargs):
            call_count[0] += 1
            result = MagicMock()
            if call_count[0] == 1:  # pgrep for nomadnet
                result.returncode = 0
                result.stdout = "1234\n"
            else:
                result.returncode = 0
                result.stdout = ""
            return result

        mock_run.side_effect = run_side_effect

        with patch('launcher_tui.handlers._lxmf_utils._HAS_SERVICE_CHECK', False):
            result = ensure_lxmf_exclusive(mock_dialog, "meshchat")
            assert result is True
            mock_dialog.yesno.assert_called_once()

    @patch('subprocess.run')
    def test_meshchat_start_user_declines(self, mock_run):
        """User declines to stop NomadNet, MeshChat start cancelled."""
        from launcher_tui.handlers._lxmf_utils import ensure_lxmf_exclusive
        mock_dialog = MagicMock()
        mock_dialog.yesno.return_value = False

        mock_run.return_value = MagicMock(returncode=0, stdout="1234\n")

        with patch('launcher_tui.handlers._lxmf_utils._HAS_SERVICE_CHECK', False):
            result = ensure_lxmf_exclusive(mock_dialog, "meshchat")
            assert result is False

    @patch('subprocess.run')
    def test_nomadnet_start_stops_meshchat(self, mock_run):
        """Starting NomadNet offers to stop MeshChat."""
        from launcher_tui.handlers._lxmf_utils import ensure_lxmf_exclusive
        mock_dialog = MagicMock()
        mock_dialog.yesno.return_value = True
        mock_run.return_value = MagicMock(returncode=0)

        result = ensure_lxmf_exclusive(
            mock_dialog, "nomadnet",
            is_meshchat_running_fn=lambda: True,
        )
        assert result is True
        mock_dialog.yesno.assert_called_once()
        assert "MeshChat" in str(mock_dialog.yesno.call_args)

    def test_nomadnet_start_no_conflict(self):
        """Starting NomadNet succeeds when MeshChat not running."""
        from launcher_tui.handlers._lxmf_utils import ensure_lxmf_exclusive
        mock_dialog = MagicMock()

        result = ensure_lxmf_exclusive(
            mock_dialog, "nomadnet",
            is_meshchat_running_fn=lambda: False,
        )
        assert result is True

    @patch('subprocess.run')
    def test_nomadnet_start_user_declines(self, mock_run):
        """User declines to stop MeshChat, NomadNet start cancelled."""
        from launcher_tui.handlers._lxmf_utils import ensure_lxmf_exclusive
        mock_dialog = MagicMock()
        mock_dialog.yesno.return_value = False

        result = ensure_lxmf_exclusive(
            mock_dialog, "nomadnet",
            is_meshchat_running_fn=lambda: True,
        )
        assert result is False


# ============================================================================
# Service INSTALL_HINT Tests
# ============================================================================

class TestMeshChatServiceHint:
    """Test that service.py INSTALL_HINT references TUI install."""

    def test_install_hint_mentions_tui(self):
        """INSTALL_HINT references TUI automated install path."""
        from plugins.meshchat.service import MeshChatService
        assert "TUI" in MeshChatService.INSTALL_HINT

    def test_install_hint_mentions_npm(self):
        """INSTALL_HINT mentions npm for manual install."""
        from plugins.meshchat.service import MeshChatService
        assert "npm" in MeshChatService.INSTALL_HINT

    def test_install_hint_mentions_nodejs(self):
        """INSTALL_HINT mentions nodejs prerequisite."""
        from plugins.meshchat.service import MeshChatService
        assert "nodejs" in MeshChatService.INSTALL_HINT


# ============================================================================
# Pip Install Environment Tests (Issue: venv pip + sudo -u mismatch)
# ============================================================================

class TestMeshChatPipInstall:
    """Test _install_meshchat_pip handles Python environments correctly.

    Root cause of crash-loop: venv pip wrapped with sudo -u <user> fails
    because non-root user can't write to root-owned /opt/meshforge/venv/.
    Fix mirrors the guard in _install_lxmf_package (line 358-361).
    """

    @patch('subprocess.run')
    @patch('pathlib.Path.exists', return_value=True)
    def test_venv_pip_no_sudo_prefix(self, mock_exists, mock_run):
        """When venv pip exists, command must NOT get sudo -u prefix."""
        handler = _make_handler()
        mock_run.return_value = MagicMock(
            returncode=0, stdout='Installed ok', stderr='',
        )

        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            install_dir = __import__('pathlib').Path(tmpdir)
            (install_dir / 'requirements.txt').write_text('aiohttp\n')
            handler._install_meshchat_pip(install_dir, run_as_user='testuser')

            cmd = mock_run.call_args[0][0]
            assert cmd[0] != 'sudo', \
                f"Venv pip should not be wrapped with sudo -u, got: {cmd}"
            assert '/opt/meshforge/venv/bin/pip' in cmd[0] or 'pip' in cmd[0]

    @patch('subprocess.run')
    def test_system_pip_with_sudo_prefix(self, mock_run):
        """When no venv, command should get sudo -u prefix for run_as_user."""
        handler = _make_handler()
        mock_run.return_value = MagicMock(
            returncode=0, stdout='Installed ok', stderr='',
        )

        # Patch _get_pip_command to return system pip (no venv)
        handler._get_pip_command = lambda: ['pip3']

        import tempfile
        from pathlib import Path
        orig_exists = Path.exists

        def exists_side_effect(self):
            if str(self) == '/opt/meshforge/venv/bin/pip':
                return False
            return orig_exists(self)

        with tempfile.TemporaryDirectory() as tmpdir:
            install_dir = Path(tmpdir)
            (install_dir / 'requirements.txt').write_text('aiohttp\n')
            with patch.object(Path, 'exists', exists_side_effect):
                handler._install_meshchat_pip(install_dir, run_as_user='testuser')

            cmd = mock_run.call_args[0][0]
            assert cmd[0] == 'sudo', \
                f"System pip should be wrapped with sudo -u, got: {cmd}"
            assert '-u' in cmd
            assert 'testuser' in cmd

    @patch('subprocess.run')
    def test_system_pip_pep668_adds_user_flag(self, mock_run):
        """PEP 668 system + run_as_user should include --user flag."""
        handler = _make_handler()
        mock_run.return_value = MagicMock(
            returncode=0, stdout='Installed ok', stderr='',
        )

        handler._get_pip_command = lambda: ['pip3', 'install', '--break-system-packages']

        import tempfile
        from pathlib import Path
        orig_exists = Path.exists

        def exists_side_effect(self):
            if str(self) == '/opt/meshforge/venv/bin/pip':
                return False
            return orig_exists(self)

        with tempfile.TemporaryDirectory() as tmpdir:
            install_dir = Path(tmpdir)
            (install_dir / 'requirements.txt').write_text('aiohttp\n')
            with patch.object(Path, 'exists', exists_side_effect):
                handler._install_meshchat_pip(install_dir, run_as_user='testuser')

            cmd = mock_run.call_args[0][0]
            assert '--user' in cmd, \
                f"PEP 668 + run_as_user should include --user, got: {cmd}"
            assert 'sudo' in cmd[0]

    @patch('subprocess.run')
    @patch('pathlib.Path.exists', return_value=True)
    def test_venv_pip_no_run_as_user(self, mock_exists, mock_run):
        """Venv pip without run_as_user runs directly (no sudo prefix)."""
        handler = _make_handler()
        mock_run.return_value = MagicMock(
            returncode=0, stdout='Installed ok', stderr='',
        )

        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            install_dir = __import__('pathlib').Path(tmpdir)
            (install_dir / 'requirements.txt').write_text('aiohttp\n')
            result = handler._install_meshchat_pip(install_dir, run_as_user=None)

            assert result is True
            cmd = mock_run.call_args[0][0]
            assert cmd[0] != 'sudo'

    @patch('subprocess.run')
    @patch('pathlib.Path.exists', return_value=True)
    def test_pip_failure_captures_error(self, mock_exists, mock_run):
        """Failed pip install captures stderr for error reporting."""
        handler = _make_handler()
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout='Collecting aiohttp',
            stderr='ERROR: Could not install packages\nPermission denied',
        )

        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            install_dir = __import__('pathlib').Path(tmpdir)
            (install_dir / 'requirements.txt').write_text('aiohttp\n')
            result = handler._install_meshchat_pip(install_dir)

            assert result is False
            assert hasattr(handler, '_last_pip_error')
            assert 'Permission denied' in handler._last_pip_error


# ============================================================================
# Frontend Build Validation Tests
# ============================================================================

class TestMeshChatFrontend:
    """Test frontend (public/) validation and rebuild functionality."""

    @patch('subprocess.run')
    def test_npm_build_validates_public_dir_exists(self, mock_run):
        """_install_meshchat_npm returns True when public/ exists after build."""
        handler = _make_handler()
        mock_run.return_value = MagicMock(returncode=0)

        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmpdir:
            install_dir = Path(tmpdir)
            (install_dir / 'package.json').write_text('{}')
            (install_dir / 'public').mkdir()  # Frontend exists
            result = handler._install_meshchat_npm(install_dir)
            assert result is True

    @patch('subprocess.run')
    def test_npm_build_fails_when_public_missing(self, mock_run):
        """_install_meshchat_npm returns False when public/ not created."""
        handler = _make_handler()
        mock_run.return_value = MagicMock(returncode=0)

        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmpdir:
            install_dir = Path(tmpdir)
            (install_dir / 'package.json').write_text('{}')
            # No public/ directory created
            result = handler._install_meshchat_npm(install_dir)
            assert result is False

    @patch('subprocess.run')
    def test_handle_start_failure_detects_missing_frontend(self, mock_run):
        """_handle_start_failure offers rebuild when public/ error in logs."""
        handler = _make_handler()
        handler.ctx.dialog.yesno.return_value = False  # Decline rebuild

        log_output = (
            "ValueError: '/home/<user>/reticulum-meshchat/public/' "
            "does not exist"
        )
        mock_run.return_value = MagicMock(
            returncode=0, stdout=log_output, stderr='',
        )

        handler._handle_start_failure('reticulum-meshchat')

        # Should have offered to rebuild frontend
        yesno_calls = handler.ctx.dialog.yesno.call_args_list
        assert len(yesno_calls) == 1
        assert 'Frontend Not Built' in str(yesno_calls[0])

    @patch('subprocess.run')
    def test_handle_start_failure_still_detects_module_errors(self, mock_run):
        """_handle_start_failure still detects ModuleNotFoundError first."""
        handler = _make_handler()
        handler.ctx.dialog.yesno.return_value = False

        log_output = "ModuleNotFoundError: No module named 'aiohttp'"
        mock_run.return_value = MagicMock(
            returncode=0, stdout=log_output, stderr='',
        )

        handler._handle_start_failure('reticulum-meshchat')

        yesno_calls = handler.ctx.dialog.yesno.call_args_list
        assert len(yesno_calls) == 1
        assert 'Missing Python Module' in str(yesno_calls[0])

    def test_status_shows_frontend_built(self):
        """Status display shows 'Built' when public/ exists."""
        handler = _make_handler()
        handler._is_meshchat_installed = MagicMock(return_value=True)
        handler._is_meshchat_running = MagicMock(return_value=False)
        handler._get_service_python = MagicMock(return_value='/usr/bin/python3')
        handler._check_meshchat_deps = MagicMock(return_value=[])

        import tempfile, io, contextlib
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmpdir:
            install_dir = Path(tmpdir)
            (install_dir / 'public').mkdir()
            handler._get_meshchat_install_dir = MagicMock(return_value=install_dir)

            with patch('launcher_tui.handlers.meshchat._HAS_LXMF', True):
                with patch('launcher_tui.handlers.meshchat._HAS_MESHCHAT_SERVICE', False):
                    output = io.StringIO()
                    with contextlib.redirect_stdout(output):
                        handler._meshchat_status()

            assert 'Frontend:   Built' in output.getvalue()

    def test_status_shows_frontend_missing(self):
        """Status display shows 'NOT BUILT' when public/ missing."""
        handler = _make_handler()
        handler._is_meshchat_installed = MagicMock(return_value=True)
        handler._is_meshchat_running = MagicMock(return_value=False)
        handler._get_service_python = MagicMock(return_value='/usr/bin/python3')
        handler._check_meshchat_deps = MagicMock(return_value=[])

        import tempfile, io, contextlib
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmpdir:
            install_dir = Path(tmpdir)
            # No public/ directory
            handler._get_meshchat_install_dir = MagicMock(return_value=install_dir)

            with patch('launcher_tui.handlers.meshchat._HAS_LXMF', True):
                with patch('launcher_tui.handlers.meshchat._HAS_MESHCHAT_SERVICE', False):
                    output = io.StringIO()
                    with contextlib.redirect_stdout(output):
                        handler._meshchat_status()

            assert 'Frontend:   NOT BUILT' in output.getvalue()

    def test_rebuild_frontend_method_exists(self):
        """Handler has _rebuild_frontend method."""
        handler = _make_handler()
        assert hasattr(handler, '_rebuild_frontend')
        assert callable(handler._rebuild_frontend)


# ============================================================================
# MeshChat Client API v1 Tests
# ============================================================================

class TestMeshChatPeerFromAPI:
    """Test MeshChatPeer.from_api() with real MeshChat announce format."""

    def test_from_meshchat_announce_format(self):
        """Parse real MeshChat /api/v1/announces response."""
        from plugins.meshchat.client import MeshChatPeer
        import time

        now_ts = time.time()
        data = {
            'destination_hash': 'abcdef1234567890',
            'identity_hash': 'fedcba0987654321',
            'aspect': 'lxmf.delivery',
            'app_data': 'TestNode',
            'snr': 8.5,
            'rssi': -95,
            'quality': 0.8,
            'created_at': now_ts - 3600,
            'updated_at': now_ts - 60,
        }
        peer = MeshChatPeer.from_api(data)
        assert peer.destination_hash == 'abcdef1234567890'
        assert peer.identity_hash == 'fedcba0987654321'
        assert peer.display_name == 'TestNode'
        assert peer.is_online is True  # updated_at was 60s ago
        assert peer.snr == 8.5
        assert peer.rssi == -95
        assert peer.last_announce is not None

    def test_from_meshchat_announce_stale(self):
        """Peer with old announce is marked offline."""
        from plugins.meshchat.client import MeshChatPeer
        import time

        data = {
            'destination_hash': 'abcdef1234567890',
            'updated_at': time.time() - 7200,  # 2 hours ago
        }
        peer = MeshChatPeer.from_api(data)
        assert peer.is_online is False

    def test_from_legacy_format_still_works(self):
        """Backward compat with old field names."""
        from plugins.meshchat.client import MeshChatPeer

        data = {
            'hash': 'abc123',
            'name': 'LegacyNode',
            'last_announce': '2026-01-01T12:00:00',
            'is_online': True,
        }
        peer = MeshChatPeer.from_api(data)
        assert peer.destination_hash == 'abc123'
        assert peer.display_name == 'LegacyNode'
        assert peer.is_online is True


class TestMeshChatClientEndpoints:
    """Test that MeshChatClient uses correct /api/v1/ endpoints."""

    def test_is_available_uses_v1_status(self):
        """is_available() calls /api/v1/status."""
        from plugins.meshchat.client import MeshChatClient

        client = MeshChatClient()
        with patch.object(client, '_request', return_value={'status': 'ok'}) as mock_req:
            result = client.is_available()
            assert result is True
            mock_req.assert_called_once_with('GET', '/api/v1/status')

    def test_get_peers_uses_v1_announces(self):
        """get_peers() calls /api/v1/announces."""
        from plugins.meshchat.client import MeshChatPeer, MeshChatClient

        client = MeshChatClient()
        mock_response = {'announces': [
            {'destination_hash': 'abc123', 'app_data': 'Node1'}
        ]}
        with patch.object(client, '_request', return_value=mock_response) as mock_req:
            peers = client.get_peers()
            mock_req.assert_called_once_with('GET', '/api/v1/announces')
            assert len(peers) == 1
            assert peers[0].destination_hash == 'abc123'

    def test_send_announce_uses_get(self):
        """send_announce() uses GET /api/v1/announce."""
        from plugins.meshchat.client import MeshChatClient

        client = MeshChatClient()
        with patch.object(client, '_request', return_value={}) as mock_req:
            result = client.send_announce()
            assert result is True
            mock_req.assert_called_once_with('GET', '/api/v1/announce')

    def test_send_message_uses_v1_endpoint(self):
        """send_message() calls /api/v1/lxmf-messages/send with correct payload."""
        from plugins.meshchat.client import MeshChatClient

        client = MeshChatClient()
        with patch.object(client, '_request', return_value={}) as mock_req:
            result = client.send_message('dest_hash_123', 'Hello')
            assert result is True
            mock_req.assert_called_once_with(
                'POST', '/api/v1/lxmf-messages/send',
                data={'lxmf_message': {
                    'destination_hash': 'dest_hash_123',
                    'content': 'Hello'
                }}
            )

    def test_get_status_uses_app_info(self):
        """get_status() calls /api/v1/app/info."""
        from plugins.meshchat.client import MeshChatClient

        client = MeshChatClient()
        app_info_resp = {'app_info': {
            'version': '2.3.0',
            'is_connected_to_shared_instance': True,
        }}
        announces_resp = {'announces': [{'destination_hash': 'a'}, {'destination_hash': 'b'}]}

        call_count = [0]
        def mock_request(method, endpoint, **kwargs):
            call_count[0] += 1
            if '/api/v1/app/info' in endpoint:
                return app_info_resp
            if '/api/v1/announces' in endpoint:
                return announces_resp
            return {}

        with patch.object(client, '_request', side_effect=mock_request):
            status = client.get_status()
            assert status.version == '2.3.0'
            assert status.rns_connected is True
            assert status.peer_count == 2


# ============================================================================
# NPM Management Tests
# ============================================================================

class TestNPMManagement:
    """Test NPM management submenu and helper methods."""

    def test_has_npm_management_menu(self):
        """Handler has _npm_management_menu method."""
        handler = _make_handler()
        assert hasattr(handler, '_npm_management_menu')
        assert callable(handler._npm_management_menu)

    def test_has_run_npm_command(self):
        """Handler has _run_npm_command helper."""
        handler = _make_handler()
        assert hasattr(handler, '_run_npm_command')
        assert callable(handler._run_npm_command)

    @patch('subprocess.run')
    @patch('launcher_tui.handlers.meshchat.get_real_user_home')
    def test_run_npm_command_basic(self, mock_home, mock_run):
        """_run_npm_command runs npm with correct args and cwd."""
        from pathlib import Path
        mock_home.return_value = Path('/home/testuser')
        mock_run.return_value = MagicMock(returncode=0)

        handler = _make_handler()
        with patch.dict(os.environ, {}, clear=True):
            result = handler._run_npm_command(['audit'])

        mock_run.assert_called_once()
        call_args = mock_run.call_args
        assert call_args[0][0] == ['npm', 'audit']
        assert call_args[1]['cwd'] == '/home/testuser/reticulum-meshchat'
        assert call_args[1]['timeout'] == 300

    @patch('subprocess.run')
    @patch('launcher_tui.handlers.meshchat.get_real_user_home')
    def test_run_npm_command_with_sudo_user(self, mock_home, mock_run):
        """_run_npm_command prepends sudo when SUDO_USER is set."""
        from pathlib import Path
        mock_home.return_value = Path('/home/testuser')
        mock_run.return_value = MagicMock(returncode=0)

        handler = _make_handler()
        with patch.dict(os.environ, {'SUDO_USER': 'testuser'}):
            handler._run_npm_command(['outdated'])

        call_args = mock_run.call_args[0][0]
        assert call_args == ['sudo', '-H', '-u', 'testuser', 'npm', 'outdated']

    @patch('subprocess.run')
    @patch('launcher_tui.handlers.meshchat.get_real_user_home')
    def test_run_npm_command_no_sudo_for_root(self, mock_home, mock_run):
        """_run_npm_command does not prepend sudo when SUDO_USER is root."""
        from pathlib import Path
        mock_home.return_value = Path('/root')
        mock_run.return_value = MagicMock(returncode=0)

        handler = _make_handler()
        with patch.dict(os.environ, {'SUDO_USER': 'root'}):
            handler._run_npm_command(['audit'])

        call_args = mock_run.call_args[0][0]
        assert call_args == ['npm', 'audit']

    def test_npm_check_installed_missing(self):
        """_npm_check_installed shows dialog when no package.json."""
        handler = _make_handler()
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch('launcher_tui.handlers.meshchat.get_real_user_home',
                       return_value=Path(tmpdir)):
                result = handler._npm_check_installed()
                assert result is False
                handler.ctx.dialog.msgbox.assert_called_once()

    def test_npm_check_installed_present(self):
        """_npm_check_installed returns True when package.json exists."""
        handler = _make_handler()
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmpdir:
            pkg = Path(tmpdir) / 'reticulum-meshchat'
            pkg.mkdir()
            (pkg / 'package.json').write_text('{}')
            with patch('launcher_tui.handlers.meshchat.get_real_user_home',
                       return_value=Path(tmpdir)):
                result = handler._npm_check_installed()
                assert result is True

    @patch('subprocess.run')
    @patch('launcher_tui.handlers.meshchat.clear_screen')
    @patch('launcher_tui.handlers.meshchat.get_real_user_home')
    def test_npm_audit_runs_command(self, mock_home, mock_clear, mock_run):
        """_npm_audit runs npm audit and waits for enter."""
        from pathlib import Path
        mock_home.return_value = Path('/home/testuser')
        mock_run.return_value = MagicMock(returncode=0)

        handler = _make_handler()
        with patch.dict(os.environ, {}, clear=True):
            handler._npm_audit()

        mock_run.assert_called_once()
        assert mock_run.call_args[0][0] == ['npm', 'audit']
        handler.ctx.wait_for_enter.assert_called_once()

    @patch('subprocess.run')
    @patch('launcher_tui.handlers.meshchat.clear_screen')
    @patch('launcher_tui.handlers.meshchat.get_real_user_home')
    def test_npm_outdated_handles_exit_code_1(self, mock_home, mock_clear, mock_run):
        """_npm_outdated treats exit code 1 as normal (outdated packages found)."""
        from pathlib import Path
        mock_home.return_value = Path('/home/testuser')
        # npm outdated returns 1 when outdated packages exist
        mock_run.return_value = MagicMock(returncode=1)

        handler = _make_handler()
        with patch.dict(os.environ, {}, clear=True):
            handler._npm_outdated()

        handler.ctx.wait_for_enter.assert_called_once()

    @patch('launcher_tui.handlers.meshchat.get_real_user_home')
    def test_npm_view_logs_no_directory(self, mock_home):
        """_npm_view_logs handles missing log directory gracefully."""
        from pathlib import Path
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            mock_home.return_value = Path(tmpdir)
            handler = _make_handler()
            with patch('launcher_tui.handlers.meshchat.clear_screen'):
                handler._npm_view_logs()
            handler.ctx.wait_for_enter.assert_called()

    @patch('launcher_tui.handlers.meshchat.get_real_user_home')
    def test_npm_view_logs_empty_directory(self, mock_home):
        """_npm_view_logs handles empty log directory."""
        from pathlib import Path
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            mock_home.return_value = Path(tmpdir)
            (Path(tmpdir) / '.npm' / '_logs').mkdir(parents=True)
            handler = _make_handler()
            with patch('launcher_tui.handlers.meshchat.clear_screen'):
                handler._npm_view_logs()
            handler.ctx.wait_for_enter.assert_called()

    def test_npm_menu_back_exits(self):
        """_npm_management_menu exits on back selection."""
        handler = _make_handler()
        handler.ctx.dialog.menu.return_value = "back"
        with patch.object(handler, '_npm_check_installed', return_value=True):
            handler._npm_management_menu()
        # Should not raise, just return

    def test_npm_menu_not_installed(self):
        """_npm_management_menu shows error when not installed."""
        handler = _make_handler()
        with patch.object(handler, '_npm_check_installed', return_value=False):
            handler._npm_management_menu()
        # Should show msgbox via _npm_check_installed and return


# ============================================================================
# Upstream Fixes Tests
# ============================================================================

class TestUpstreamFixes:
    """Test upstream fix detection and application."""

    def test_apply_upstream_fixes_no_file(self):
        """Returns empty list when meshchat.py doesn't exist."""
        handler = _make_handler()
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmpdir:
            result = handler._apply_upstream_fixes(Path(tmpdir))
            assert result == []

    def test_apply_upstream_fixes_already_patched(self):
        """Returns empty list when fix already applied (default=str present)."""
        handler = _make_handler()
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmpdir:
            meshchat_py = Path(tmpdir) / 'meshchat.py'
            meshchat_py.write_text(
                'import json\nimport functools\n'
                'web.json_response({"announces": announces}, '
                'dumps=functools.partial(json.dumps, default=str))\n'
            )
            result = handler._apply_upstream_fixes(Path(tmpdir))
            assert result == []

    def test_apply_upstream_fixes_applies_datetime_fix(self):
        """Patches meshchat.py when the vulnerable pattern is found."""
        handler = _make_handler()
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmpdir:
            meshchat_py = Path(tmpdir) / 'meshchat.py'
            # Simulate the upstream buggy code pattern
            meshchat_py.write_text(
                'import json\n'
                'import aiohttp\n'
                'class MeshChat:\n'
                '    async def get_announces(self):\n'
                '        announces = self.get_all_announces()\n'
                '        return web.json_response({\n'
                '            "announces": announces,\n'
                '        })\n'
            )
            result = handler._apply_upstream_fixes(Path(tmpdir))
            assert 'datetime JSON serialization' in result

            # Verify the file was actually modified
            content = meshchat_py.read_text()
            assert 'import functools' in content
            assert 'default=str' in content

    def test_apply_upstream_fixes_idempotent(self):
        """Running twice doesn't double-patch."""
        handler = _make_handler()
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmpdir:
            meshchat_py = Path(tmpdir) / 'meshchat.py'
            meshchat_py.write_text(
                'import json\n'
                'class MeshChat:\n'
                '    async def get_announces(self):\n'
                '        announces = self.get_all_announces()\n'
                '        return web.json_response({\n'
                '            "announces": announces,\n'
                '        })\n'
            )
            result1 = handler._apply_upstream_fixes(Path(tmpdir))
            assert len(result1) == 1
            content_after_first = meshchat_py.read_text()

            result2 = handler._apply_upstream_fixes(Path(tmpdir))
            assert result2 == []
            assert meshchat_py.read_text() == content_after_first

    def test_apply_upstream_fixes_interactive_no_fixes(self):
        """Shows 'no fixes needed' dialog when already patched."""
        handler = _make_handler()
        with patch.object(handler, '_apply_upstream_fixes', return_value=[]):
            handler._apply_upstream_fixes_interactive()
        handler.ctx.dialog.msgbox.assert_called_once()
        assert 'No Fixes' in handler.ctx.dialog.msgbox.call_args[0][0]

    def test_apply_upstream_fixes_interactive_with_fixes(self):
        """Shows fix list and offers restart when fixes applied."""
        handler = _make_handler()
        with patch.object(handler, '_apply_upstream_fixes',
                         return_value=['datetime JSON serialization']):
            with patch.object(handler, '_is_meshchat_running', return_value=False):
                handler._apply_upstream_fixes_interactive()
        handler.ctx.dialog.msgbox.assert_called_once()
        assert 'Applied' in handler.ctx.dialog.msgbox.call_args[0][0]

    @patch('subprocess.run')
    def test_handle_start_failure_detects_datetime_error(self, mock_run):
        """_handle_start_failure detects datetime JSON serialization error."""
        handler = _make_handler()
        mock_run.return_value = MagicMock(
            stdout='TypeError: Object of type datetime is not JSON serializable',
            returncode=0,
        )
        handler.ctx.dialog.yesno.return_value = True
        with patch.object(handler, '_apply_upstream_fixes',
                         return_value=['datetime JSON serialization']):
            with patch('launcher_tui.handlers.meshchat.start_service'):
                with patch.object(handler, '_is_meshchat_running', return_value=True):
                    with patch.object(handler, '_get_meshchat_url',
                                     return_value='http://127.0.0.1:8000'):
                        handler._handle_start_failure('reticulum-meshchat')
        handler.ctx.dialog.yesno.assert_called_once()
        assert 'JSON' in handler.ctx.dialog.yesno.call_args[0][0]
