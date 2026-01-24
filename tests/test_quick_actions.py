"""
Tests for Quick Actions Mixin.

Tests cover:
- Quick action definitions
- Menu dispatch logic
- Individual action methods exist and are callable
- Status bar invalidation on service restarts
- Graceful error handling

Run with: pytest tests/test_quick_actions.py -v
"""

import pytest
import sys
import os
from unittest.mock import patch, MagicMock, call

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src', 'launcher_tui'))

from quick_actions_mixin import QuickActionsMixin, QUICK_ACTIONS


class MockDialog:
    """Mock dialog backend for testing."""

    def __init__(self):
        self.menu_calls = []
        self._menu_responses = []

    def set_responses(self, responses):
        self._menu_responses = list(responses)

    def menu(self, title, text, choices):
        self.menu_calls.append((title, text, choices))
        if self._menu_responses:
            return self._menu_responses.pop(0)
        return None


class MockLauncher(QuickActionsMixin):
    """Test launcher with quick actions mixin."""

    def __init__(self):
        self.dialog = MockDialog()
        self._status_bar = None


class TestQuickActionDefinitions:
    """Test QUICK_ACTIONS list structure."""

    def test_actions_is_list(self):
        assert isinstance(QUICK_ACTIONS, list)

    def test_actions_not_empty(self):
        assert len(QUICK_ACTIONS) >= 5

    def test_actions_are_tuples(self):
        for action in QUICK_ACTIONS:
            assert isinstance(action, tuple)
            assert len(action) == 3

    def test_tags_are_single_char(self):
        for tag, _, _ in QUICK_ACTIONS:
            assert len(tag) == 1

    def test_tags_are_unique(self):
        tags = [tag for tag, _, _ in QUICK_ACTIONS]
        assert len(tags) == len(set(tags))

    def test_descriptions_non_empty(self):
        for _, desc, _ in QUICK_ACTIONS:
            assert len(desc) > 0

    def test_method_names_start_with_qa(self):
        for _, _, method_name in QUICK_ACTIONS:
            assert method_name.startswith('_qa_')


class TestQuickActionsMenu:
    """Test the quick actions menu dispatch."""

    def test_menu_shows_all_actions(self):
        launcher = MockLauncher()
        launcher.dialog.set_responses([None])  # Cancel immediately
        launcher._quick_actions_menu()

        assert len(launcher.dialog.menu_calls) == 1
        title, text, choices = launcher.dialog.menu_calls[0]
        assert title == "Quick Actions"
        # Should have all actions plus 'back'
        assert len(choices) == len(QUICK_ACTIONS) + 1

    def test_back_exits_menu(self):
        launcher = MockLauncher()
        launcher.dialog.set_responses(['b'])
        launcher._quick_actions_menu()
        assert len(launcher.dialog.menu_calls) == 1

    def test_none_exits_menu(self):
        launcher = MockLauncher()
        launcher.dialog.set_responses([None])
        launcher._quick_actions_menu()
        assert len(launcher.dialog.menu_calls) == 1

    @patch('subprocess.run')
    @patch('builtins.input', return_value='')
    def test_dispatches_to_correct_method(self, mock_input, mock_run):
        launcher = MockLauncher()
        # Select 's' (service status) then 'b' (back)
        launcher.dialog.set_responses(['s', 'b'])
        mock_run.return_value = MagicMock(stdout='active\n', returncode=0)
        launcher._quick_actions_menu()
        assert len(launcher.dialog.menu_calls) == 2


class TestQuickActionMethods:
    """Test that all action methods exist and work."""

    def test_all_methods_exist(self):
        launcher = MockLauncher()
        for _, _, method_name in QUICK_ACTIONS:
            assert hasattr(launcher, method_name), \
                f"Method {method_name} not found on launcher"
            assert callable(getattr(launcher, method_name))

    @patch('subprocess.run')
    @patch('builtins.input', return_value='')
    def test_service_status_calls_systemctl(self, mock_input, mock_run):
        mock_run.return_value = MagicMock(stdout='active\n', returncode=0)
        launcher = MockLauncher()
        launcher._qa_service_status()

        # Should call systemctl for each service
        systemctl_calls = [
            c for c in mock_run.call_args_list
            if 'systemctl' in str(c)
        ]
        assert len(systemctl_calls) >= 4  # 4 services

    @patch('subprocess.run')
    @patch('builtins.input', return_value='')
    def test_node_list_calls_meshtastic(self, mock_input, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        launcher = MockLauncher()
        launcher._qa_node_list()

        # Should call meshtastic --nodes
        meshtastic_calls = [
            c for c in mock_run.call_args_list
            if 'meshtastic' in str(c) and '--nodes' in str(c)
        ]
        assert len(meshtastic_calls) == 1

    @patch('subprocess.run')
    @patch('builtins.input', return_value='')
    def test_restart_meshtasticd(self, mock_input, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        launcher = MockLauncher()
        launcher._qa_restart_meshtasticd()

        # Should call systemctl restart meshtasticd
        restart_calls = [
            c for c in mock_run.call_args_list
            if 'restart' in str(c) and 'meshtasticd' in str(c)
        ]
        assert len(restart_calls) == 1

    @patch('subprocess.run')
    @patch('builtins.input', return_value='')
    def test_restart_rnsd(self, mock_input, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        launcher = MockLauncher()
        launcher._qa_restart_rnsd()

        restart_calls = [
            c for c in mock_run.call_args_list
            if 'restart' in str(c) and 'rnsd' in str(c)
        ]
        assert len(restart_calls) == 1

    @patch('subprocess.run')
    @patch('builtins.input', return_value='')
    def test_port_check_runs(self, mock_input, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        launcher = MockLauncher()
        # Port check uses socket, not subprocess for the actual checks
        launcher._qa_port_check()
        # Should at least call clear
        mock_run.assert_called()

    @patch('builtins.input', return_value='')
    def test_generate_report_runs(self, mock_input):
        launcher = MockLauncher()
        with patch('subprocess.run'):
            launcher._qa_generate_report()
        # Should complete without error

    @patch('builtins.input', return_value='')
    def test_run_diagnostics_runs(self, mock_input):
        launcher = MockLauncher()
        with patch('subprocess.run'):
            launcher._qa_run_diagnostics()
        # Should complete without error


    @patch('builtins.input', return_value='')
    def test_node_inventory_empty(self, mock_input):
        """Node inventory action works with empty inventory."""
        launcher = MockLauncher()
        with patch('subprocess.run'):
            launcher._qa_node_inventory()
        # Should complete without error

    @patch('builtins.input', return_value='')
    def test_node_inventory_with_nodes(self, mock_input):
        """Node inventory displays tracked nodes."""
        launcher = MockLauncher()
        import tempfile
        import json
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmpdir:
            inv_path = Path(tmpdir) / "node_inventory.json"
            # Pre-populate inventory file
            import time
            inv_data = {
                "!abc12345": {
                    "node_id": "!abc12345",
                    "short_name": "Node1",
                    "long_name": "Hilltop Node",
                    "hardware": "RAK4631",
                    "role": "router",
                    "last_snr": -5.0,
                    "last_rssi": -90,
                    "last_seen": time.time(),
                    "first_seen": time.time() - 3600,
                    "update_count": 10,
                }
            }
            inv_path.write_text(json.dumps(inv_data))

            with patch('subprocess.run'):
                with patch('utils.paths.get_real_user_home',
                           return_value=Path(tmpdir)):
                    # Patch .config/meshforge path
                    config_dir = Path(tmpdir) / ".config" / "meshforge"
                    config_dir.mkdir(parents=True)
                    real_inv = config_dir / "node_inventory.json"
                    real_inv.write_text(json.dumps(inv_data))

                    launcher._qa_node_inventory()

    @patch('builtins.input', return_value='')
    def test_node_inventory_import_error(self, mock_input):
        """Node inventory handles missing module gracefully."""
        launcher = MockLauncher()
        with patch('subprocess.run'):
            with patch.dict(sys.modules, {'utils.node_inventory': None}):
                launcher._qa_node_inventory()


class TestStatusBarInvalidation:
    """Test that service restarts invalidate the status bar cache."""

    @patch('subprocess.run')
    @patch('builtins.input', return_value='')
    def test_restart_meshtasticd_invalidates_cache(self, mock_input, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        launcher = MockLauncher()
        mock_bar = MagicMock()
        launcher._status_bar = mock_bar

        launcher._qa_restart_meshtasticd()
        mock_bar.invalidate.assert_called_once()

    @patch('subprocess.run')
    @patch('builtins.input', return_value='')
    def test_restart_rnsd_invalidates_cache(self, mock_input, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        launcher = MockLauncher()
        mock_bar = MagicMock()
        launcher._status_bar = mock_bar

        launcher._qa_restart_rnsd()
        mock_bar.invalidate.assert_called_once()

    @patch('subprocess.run')
    @patch('builtins.input', return_value='')
    def test_no_status_bar_doesnt_crash(self, mock_input, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        launcher = MockLauncher()
        launcher._status_bar = None

        # Should not raise
        launcher._qa_restart_meshtasticd()
        launcher._qa_restart_rnsd()


class TestGracefulErrors:
    """Test that actions handle errors gracefully."""

    @patch('subprocess.run')
    @patch('builtins.input', return_value='')
    def test_meshtastic_not_found(self, mock_input, mock_run):
        def side_effect(*args, **kwargs):
            if 'meshtastic' in str(args):
                raise FileNotFoundError("meshtastic not found")
            return MagicMock(returncode=0)

        mock_run.side_effect = side_effect
        launcher = MockLauncher()
        # Should not raise
        launcher._qa_node_list()

    @patch('subprocess.run')
    @patch('builtins.input', return_value='')
    def test_systemctl_timeout(self, mock_input, mock_run):
        import subprocess as sp

        def side_effect(*args, **kwargs):
            if 'systemctl' in str(args) and 'restart' in str(args):
                raise sp.TimeoutExpired(cmd=args, timeout=30)
            return MagicMock(returncode=0, stdout='inactive\n')

        mock_run.side_effect = side_effect
        launcher = MockLauncher()
        # Should not raise
        launcher._qa_restart_meshtasticd()

    @patch('builtins.input', return_value='')
    def test_diagnostics_import_error(self, mock_input):
        launcher = MockLauncher()
        with patch('subprocess.run'):
            with patch.dict(sys.modules, {'utils.diagnostic_engine': None}):
                # Should handle ImportError gracefully
                launcher._qa_run_diagnostics()
