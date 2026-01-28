"""
Tests for Emergency Mode Mixin.

Tests cover:
- Menu structure and dispatch
- Broadcast message flow
- Direct message flow
- Status display
- SOS beacon confirmation
- Graceful error handling
- EMCOMM prefix application

Run with: pytest tests/test_emergency_mode.py -v
"""

import pytest
import sys
import os
from unittest.mock import patch, MagicMock, call

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src', 'launcher_tui'))

from emergency_mode_mixin import EmergencyModeMixin, EMCOMM_PREFIX


class MockDialog:
    """Mock dialog backend."""

    def __init__(self):
        self.menu_calls = []
        self.inputbox_calls = []
        self.yesno_calls = []
        self._menu_responses = []
        self._inputbox_responses = []
        self._yesno_responses = []

    def set_menu_responses(self, responses):
        self._menu_responses = list(responses)

    def set_inputbox_responses(self, responses):
        self._inputbox_responses = list(responses)

    def set_yesno_responses(self, responses):
        self._yesno_responses = list(responses)

    def menu(self, title, text, choices):
        self.menu_calls.append((title, text, choices))
        if self._menu_responses:
            return self._menu_responses.pop(0)
        return None

    def inputbox(self, title, text, init=""):
        self.inputbox_calls.append((title, text, init))
        if self._inputbox_responses:
            return self._inputbox_responses.pop(0)
        return None

    def yesno(self, title, text, default_no=False):
        self.yesno_calls.append((title, text, default_no))
        if self._yesno_responses:
            return self._yesno_responses.pop(0)
        return False


class MockLauncher(EmergencyModeMixin):
    """Test launcher with emergency mode."""

    def __init__(self):
        self.dialog = MockDialog()

    @staticmethod
    def _wait_for_enter(msg: str = "\nPress Enter to continue...") -> None:
        pass


class TestEmergencyModeMenu:
    """Test emergency mode menu structure."""

    def test_menu_title(self):
        launcher = MockLauncher()
        launcher.dialog.set_menu_responses([None])
        launcher._emergency_mode()
        title = launcher.dialog.menu_calls[0][0]
        assert "EMERGENCY" in title

    def test_menu_has_all_actions(self):
        launcher = MockLauncher()
        launcher.dialog.set_menu_responses([None])
        launcher._emergency_mode()
        _, _, choices = launcher.dialog.menu_calls[0]
        tags = [c[0] for c in choices]
        assert "send" in tags
        assert "direct" in tags
        assert "status" in tags
        assert "msgs" in tags
        assert "pos" in tags
        assert "sos" in tags
        assert "exit" in tags

    def test_exit_breaks_loop(self):
        launcher = MockLauncher()
        launcher.dialog.set_menu_responses(["exit"])
        launcher._emergency_mode()
        assert len(launcher.dialog.menu_calls) == 1

    def test_none_breaks_loop(self):
        launcher = MockLauncher()
        launcher.dialog.set_menu_responses([None])
        launcher._emergency_mode()
        assert len(launcher.dialog.menu_calls) == 1


class TestBroadcastMessage:
    """Test broadcast message flow."""

    @patch('subprocess.run')
    @patch('builtins.input', return_value='')
    def test_broadcast_sends_message(self, mock_input, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        launcher = MockLauncher()
        launcher.dialog.set_inputbox_responses(["Help needed at grid ref"])
        launcher.dialog.set_yesno_responses([True])  # Confirm send

        launcher._emcomm_broadcast()

        # Should call meshtastic --sendtext with EMCOMM prefix
        send_calls = [
            c for c in mock_run.call_args_list
            if 'meshtastic' in str(c) and '--sendtext' in str(c)
        ]
        assert len(send_calls) == 1
        # Verify message has EMCOMM prefix
        cmd_args = send_calls[0][0][0]
        assert EMCOMM_PREFIX in str(send_calls[0])

    def test_broadcast_cancelled_if_no_message(self):
        launcher = MockLauncher()
        launcher.dialog.set_inputbox_responses([None])
        launcher._emcomm_broadcast()
        # No yesno call should happen
        assert len(launcher.dialog.yesno_calls) == 0

    def test_broadcast_cancelled_if_empty_message(self):
        launcher = MockLauncher()
        launcher.dialog.set_inputbox_responses([""])
        launcher._emcomm_broadcast()
        assert len(launcher.dialog.yesno_calls) == 0

    @patch('subprocess.run')
    def test_broadcast_cancelled_if_not_confirmed(self, mock_run):
        launcher = MockLauncher()
        launcher.dialog.set_inputbox_responses(["Test message"])
        launcher.dialog.set_yesno_responses([False])  # Deny send

        launcher._emcomm_broadcast()

        # Should NOT call meshtastic
        meshtastic_calls = [
            c for c in mock_run.call_args_list
            if 'meshtastic' in str(c) and '--sendtext' in str(c)
        ]
        assert len(meshtastic_calls) == 0

    @patch('subprocess.run')
    @patch('builtins.input', return_value='')
    def test_broadcast_handles_missing_cli(self, mock_input, mock_run):
        def side_effect(*args, **kwargs):
            if 'meshtastic' in str(args):
                raise FileNotFoundError("meshtastic not found")
            return MagicMock(returncode=0)

        mock_run.side_effect = side_effect
        launcher = MockLauncher()
        launcher.dialog.set_inputbox_responses(["Help"])
        launcher.dialog.set_yesno_responses([True])

        # Should not raise
        launcher._emcomm_broadcast()


class TestDirectMessage:
    """Test direct message flow."""

    @patch('subprocess.run')
    @patch('builtins.input', return_value='')
    def test_direct_sends_to_destination(self, mock_input, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        launcher = MockLauncher()
        launcher.dialog.set_inputbox_responses([
            "!abc12345",  # Destination
            "Urgent update"  # Message
        ])
        launcher.dialog.set_yesno_responses([True])  # Confirm

        launcher._emcomm_direct()

        send_calls = [
            c for c in mock_run.call_args_list
            if 'meshtastic' in str(c) and '--dest' in str(c)
        ]
        assert len(send_calls) == 1
        assert '!abc12345' in str(send_calls[0])

    def test_direct_cancelled_if_no_dest(self):
        launcher = MockLauncher()
        launcher.dialog.set_inputbox_responses([None])
        launcher._emcomm_direct()
        # Should only have 1 inputbox call (the dest), no message prompt
        assert len(launcher.dialog.inputbox_calls) == 1

    def test_direct_cancelled_if_no_message(self):
        launcher = MockLauncher()
        launcher.dialog.set_inputbox_responses(["!abc", None])
        launcher._emcomm_direct()
        assert len(launcher.dialog.yesno_calls) == 0


class TestEmcommStatus:
    """Test status display."""

    @patch('subprocess.run')
    @patch('builtins.input', return_value='')
    def test_status_calls_meshtastic_nodes(self, mock_input, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        launcher = MockLauncher()
        launcher._emcomm_status()

        node_calls = [
            c for c in mock_run.call_args_list
            if '--nodes' in str(c)
        ]
        assert len(node_calls) == 1

    @patch('subprocess.run')
    @patch('builtins.input', return_value='')
    def test_status_handles_timeout(self, mock_input, mock_run):
        import subprocess as sp

        def side_effect(*args, **kwargs):
            if 'meshtastic' in str(args):
                raise sp.TimeoutExpired(cmd='meshtastic', timeout=30)
            return MagicMock(returncode=0)

        mock_run.side_effect = side_effect
        launcher = MockLauncher()
        # Should not raise
        launcher._emcomm_status()


class TestEmcommMessages:
    """Test message display."""

    @patch('subprocess.run')
    @patch('builtins.input', return_value='')
    def test_messages_reads_journal(self, mock_input, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="received message from !abc: Hello\ntext: World\n"
        )
        launcher = MockLauncher()
        launcher._emcomm_messages()

        journal_calls = [
            c for c in mock_run.call_args_list
            if 'journalctl' in str(c)
        ]
        assert len(journal_calls) >= 1


class TestEmcommPosition:
    """Test position display."""

    @patch('subprocess.run')
    @patch('builtins.input', return_value='')
    def test_position_calls_meshtastic(self, mock_input, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        launcher = MockLauncher()
        launcher._emcomm_position()

        pos_calls = [
            c for c in mock_run.call_args_list
            if 'position' in str(c)
        ]
        assert len(pos_calls) == 1


class TestSOSBeacon:
    """Test SOS beacon functionality."""

    def test_sos_requires_confirmation(self):
        launcher = MockLauncher()
        launcher.dialog.set_yesno_responses([False])  # Deny
        launcher._emcomm_sos_beacon()
        # Should have asked for confirmation
        assert len(launcher.dialog.yesno_calls) == 1
        assert "SOS" in launcher.dialog.yesno_calls[0][0]

    def test_sos_denied_does_not_send(self):
        launcher = MockLauncher()
        launcher.dialog.set_yesno_responses([False])
        launcher._emcomm_sos_beacon()
        # No inputbox for info should appear
        assert len(launcher.dialog.inputbox_calls) == 0

    @patch('subprocess.run')
    @patch('builtins.input', return_value='')
    @patch('time.sleep', side_effect=KeyboardInterrupt)
    def test_sos_sends_beacon_until_interrupt(self, mock_sleep, mock_input,
                                              mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        launcher = MockLauncher()
        launcher.dialog.set_yesno_responses([True])  # Confirm SOS
        launcher.dialog.set_inputbox_responses(["WH6GXZ trapped"])

        launcher._emcomm_sos_beacon()

        # Should have sent at least one beacon
        send_calls = [
            c for c in mock_run.call_args_list
            if 'meshtastic' in str(c) and '--sendtext' in str(c)
        ]
        assert len(send_calls) >= 1

        # Message should contain SOS and operator info
        msg = str(send_calls[0])
        assert "SOS" in msg
        assert "WH6GXZ" in msg

    @patch('subprocess.run')
    @patch('builtins.input', return_value='')
    @patch('time.sleep', side_effect=KeyboardInterrupt)
    def test_sos_generic_message(self, mock_sleep, mock_input, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        launcher = MockLauncher()
        launcher.dialog.set_yesno_responses([True])
        launcher.dialog.set_inputbox_responses([""])  # No custom info

        launcher._emcomm_sos_beacon()

        send_calls = [
            c for c in mock_run.call_args_list
            if '--sendtext' in str(c)
        ]
        assert len(send_calls) >= 1
        msg = str(send_calls[0])
        assert "SOS" in msg
        assert EMCOMM_PREFIX in msg


class TestEmcommPrefix:
    """Test EMCOMM message prefix."""

    def test_prefix_defined(self):
        assert EMCOMM_PREFIX == "[EMCOMM] "

    def test_prefix_in_broadcast(self):
        launcher = MockLauncher()
        launcher.dialog.set_inputbox_responses(["test"])
        launcher.dialog.set_yesno_responses([False])  # Don't actually send
        launcher._emcomm_broadcast()
        # The yesno confirmation should show the prefixed message
        assert len(launcher.dialog.yesno_calls) == 1
        confirm_text = launcher.dialog.yesno_calls[0][1]
        assert EMCOMM_PREFIX in confirm_text


class TestGracefulErrors:
    """Test error handling in emergency mode."""

    @patch('subprocess.run')
    @patch('builtins.input', return_value='')
    def test_meshtastic_not_found(self, mock_input, mock_run):
        def side_effect(*args, **kwargs):
            if 'meshtastic' in str(args):
                raise FileNotFoundError("meshtastic not found")
            return MagicMock(returncode=0)

        mock_run.side_effect = side_effect
        launcher = MockLauncher()
        launcher.dialog.set_inputbox_responses(["Help"])
        launcher.dialog.set_yesno_responses([True])
        # Should not raise
        launcher._emcomm_broadcast()

    @patch('subprocess.run')
    @patch('builtins.input', return_value='')
    def test_send_timeout(self, mock_input, mock_run):
        import subprocess as sp

        def side_effect(*args, **kwargs):
            if 'meshtastic' in str(args) and '--sendtext' in str(args):
                raise sp.TimeoutExpired(cmd=args, timeout=30)
            return MagicMock(returncode=0)

        mock_run.side_effect = side_effect
        launcher = MockLauncher()
        launcher.dialog.set_inputbox_responses(["Help"])
        launcher.dialog.set_yesno_responses([True])
        # Should not raise
        launcher._emcomm_broadcast()
