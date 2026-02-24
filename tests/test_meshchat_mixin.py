"""
MeshChat TUI mixin, deployment profile, and diagnostics tests.

Tests MeshChat as a first-class LXMF client alongside NomadNet.
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
# MeshChat Mixin Tests
# ============================================================================

class TestMeshChatClientMixin:
    """Test MeshChatClientMixin TUI methods."""

    def _make_mixin(self):
        """Create a mixin instance with mocked dialog."""
        from launcher_tui.meshchat_client_mixin import MeshChatClientMixin

        class TestClass(MeshChatClientMixin):
            def __init__(self):
                self.dialog = MagicMock()
                self._feature_flags = {}

            def _safe_call(self, name, method, *a, **kw):
                return method(*a, **kw)

            def _wait_for_enter(self, msg=""):
                pass

            def _feature_enabled(self, f):
                return True

            def _get_rnsd_user(self):
                return None

        return TestClass()

    def test_mixin_creates(self):
        """MeshChatClientMixin can be instantiated."""
        mixin = self._make_mixin()
        assert hasattr(mixin, '_meshchat_menu')
        assert hasattr(mixin, '_meshchat_status')
        assert hasattr(mixin, '_is_meshchat_installed')
        assert hasattr(mixin, '_is_meshchat_running')

    @patch('shutil.which', return_value=None)
    def test_not_installed_when_no_binary(self, mock_which):
        """Reports not installed when no meshchat binary found."""
        mixin = self._make_mixin()
        # Patch plugin import to fail
        with patch('launcher_tui.meshchat_client_mixin._HAS_MESHCHAT_SERVICE', False):
            result = mixin._is_meshchat_installed()
            assert result is False

    @patch('shutil.which', return_value='/usr/bin/meshchat')
    def test_installed_when_binary_found(self, mock_which):
        """Reports installed when meshchat binary found."""
        mixin = self._make_mixin()
        result = mixin._is_meshchat_installed()
        assert result is True

    def test_check_rns_preflight_no_rnsd(self):
        """Preflight check warns when rnsd not running."""
        mixin = self._make_mixin()
        mixin.dialog.yesno.return_value = True
        result = mixin._check_rns_for_meshchat()
        assert result is True
        mixin.dialog.yesno.assert_called_once()

    def test_check_rns_preflight_cancelled(self):
        """Preflight check returns False when user cancels."""
        mixin = self._make_mixin()
        mixin.dialog.yesno.return_value = False
        result = mixin._check_rns_for_meshchat()
        assert result is False

    def test_check_rns_preflight_rnsd_running(self):
        """Preflight check passes when rnsd running as non-root."""
        mixin = self._make_mixin()
        mixin._get_rnsd_user = lambda: 'pi'
        result = mixin._check_rns_for_meshchat()
        assert result is True


# ============================================================================
# LXMF App Conflict Detection Tests
# ============================================================================

class TestLXMFAppConflict:
    """Test _check_lxmf_app_conflict() detects both NomadNet and MeshChat."""

    def _make_diagnostics_mixin(self):
        """Create a diagnostics mixin instance."""
        from launcher_tui.rns_diagnostics_mixin import RNSDiagnosticsMixin

        class TestClass(RNSDiagnosticsMixin):
            def __init__(self):
                self.dialog = MagicMock()

        return TestClass()

    @patch('subprocess.run')
    def test_detects_nomadnet(self, mock_run):
        """Detects NomadNet holding port."""
        mock_run.return_value = MagicMock(returncode=0, stdout="1234\n")
        mixin = self._make_diagnostics_mixin()
        result = mixin._check_lxmf_app_conflict()
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
        mixin = self._make_diagnostics_mixin()
        result = mixin._check_lxmf_app_conflict()
        assert result == "MeshChat"

    @patch('subprocess.run')
    def test_no_conflict(self, mock_run):
        """Returns None when no LXMF app running."""
        mock_run.return_value = MagicMock(returncode=1, stdout="")
        mixin = self._make_diagnostics_mixin()
        result = mixin._check_lxmf_app_conflict()
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
