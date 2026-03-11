"""
Tests for NomadNet startup diagnostics — error pattern detection,
share_instance pre-flight check, and rnsd crash-loop detection.
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch, mock_open

sys.path.insert(0, 'src')


class TestDiagnoseNomadnetError(unittest.TestCase):
    """Test _diagnose_nomadnet_error pattern matching."""

    def _make_handler(self):
        """Create a minimal NomadNetHandler with mocked dependencies."""
        from launcher_tui.handlers.nomadnet import NomadNetHandler

        ctx = MagicMock()
        ctx.dialog = MagicMock()
        handler = NomadNetHandler.__new__(NomadNetHandler)
        handler.ctx = ctx
        return handler

    @patch('launcher_tui.handlers.nomadnet.get_real_user_home')
    def test_eaddrinuse_detected(self, mock_home):
        """EADDRINUSE pattern produces share_instance hint."""
        handler = self._make_handler()

        with tempfile.TemporaryDirectory() as tmpdir:
            mock_home.return_value = Path(tmpdir)
            nn_dir = Path(tmpdir) / '.nomadnetwork'
            nn_dir.mkdir()
            logfile = nn_dir / 'logfile'
            logfile.write_text(
                "[2026-03-11 10:00:00] Starting NomadNet\n"
                "[2026-03-11 10:00:01] [Error] [Errno 98] Address already in use\n"
            )

            with patch('builtins.print') as mock_print:
                handler._diagnose_nomadnet_error(1, 'testuser')

            output = ' '.join(
                str(call.args[0]) for call in mock_print.call_args_list
                if call.args
            )
            self.assertIn('EADDRINUSE', output)
            self.assertIn('share_instance', output)

    @patch('launcher_tui.handlers.nomadnet.get_real_user_home')
    def test_interface_creation_failure_detected(self, mock_home):
        """Interface creation failure pattern detected."""
        handler = self._make_handler()

        with tempfile.TemporaryDirectory() as tmpdir:
            mock_home.return_value = Path(tmpdir)
            nn_dir = Path(tmpdir) / '.nomadnetwork'
            nn_dir.mkdir()
            logfile = nn_dir / 'logfile'
            logfile.write_text(
                "[2026-03-11 10:00:00] Starting\n"
                "[2026-03-11 10:00:01] The interface could not be created\n"
            )

            with patch('builtins.print') as mock_print:
                handler._diagnose_nomadnet_error(1, 'testuser')

            output = ' '.join(
                str(call.args[0]) for call in mock_print.call_args_list
                if call.args
            )
            self.assertIn('interface', output.lower())
            self.assertIn('share_instance', output)

    @patch('launcher_tui.handlers.nomadnet.get_real_user_home')
    def test_config_parse_error_detected(self, mock_home):
        """Config parsing error pattern detected."""
        handler = self._make_handler()

        with tempfile.TemporaryDirectory() as tmpdir:
            mock_home.return_value = Path(tmpdir)
            nn_dir = Path(tmpdir) / '.nomadnetwork'
            nn_dir.mkdir()
            logfile = nn_dir / 'logfile'
            logfile.write_text(
                "[2026-03-11 10:00:00] Starting\n"
                "configparser.ParsingError: source contains parsing errors\n"
            )

            with patch('builtins.print') as mock_print:
                handler._diagnose_nomadnet_error(1, 'testuser')

            output = ' '.join(
                str(call.args[0]) for call in mock_print.call_args_list
                if call.args
            )
            self.assertIn('syntax', output.lower())

    @patch('launcher_tui.handlers.nomadnet.get_real_user_home')
    def test_traceback_fallback(self, mock_home):
        """Traceback in log is shown when no specific pattern matches."""
        handler = self._make_handler()

        with tempfile.TemporaryDirectory() as tmpdir:
            mock_home.return_value = Path(tmpdir)
            nn_dir = Path(tmpdir) / '.nomadnetwork'
            nn_dir.mkdir()
            logfile = nn_dir / 'logfile'
            logfile.write_text(
                "[2026-03-11 10:00:00] Starting\n"
                "Traceback (most recent call last):\n"
                '  File "nomadnet/main.py", line 42, in run\n'
                "SomeUnknownError: something broke\n"
            )

            # Mock _get_rnsd_user to return a user (rnsd is running)
            handler._get_rnsd_user = MagicMock(return_value='testuser')

            with patch('builtins.print') as mock_print, \
                 patch('subprocess.run') as mock_run:
                mock_run.return_value = MagicMock(stdout='', returncode=0)
                handler._diagnose_nomadnet_error(1, 'testuser')

            output = ' '.join(
                str(call.args[0]) for call in mock_print.call_args_list
                if call.args
            )
            self.assertIn('Traceback', output)
            self.assertIn('SomeUnknownError', output)

    @patch('launcher_tui.handlers.nomadnet.get_real_user_home')
    def test_rnsd_not_running_detected(self, mock_home):
        """rnsd not running is detected in post-failure diagnostics."""
        handler = self._make_handler()

        with tempfile.TemporaryDirectory() as tmpdir:
            mock_home.return_value = Path(tmpdir)
            nn_dir = Path(tmpdir) / '.nomadnetwork'
            nn_dir.mkdir()
            # Empty log — no patterns match
            logfile = nn_dir / 'logfile'
            logfile.write_text("[2026-03-11 10:00:00] Starting\n")

            handler._get_rnsd_user = MagicMock(return_value=None)

            with patch('builtins.print') as mock_print:
                handler._diagnose_nomadnet_error(1, 'testuser')

            output = ' '.join(
                str(call.args[0]) for call in mock_print.call_args_list
                if call.args
            )
            self.assertIn('not running', output.lower())

    @patch('launcher_tui.handlers.nomadnet.get_real_user_home')
    def test_fallback_shows_log_lines(self, mock_home):
        """When no pattern matches and rnsd is running, show log tail."""
        handler = self._make_handler()

        with tempfile.TemporaryDirectory() as tmpdir:
            mock_home.return_value = Path(tmpdir)
            nn_dir = Path(tmpdir) / '.nomadnetwork'
            nn_dir.mkdir()
            logfile = nn_dir / 'logfile'
            logfile.write_text(
                "[2026-03-11 10:00:00] Starting NomadNet v0.5.0\n"
                "[2026-03-11 10:00:01] Something unusual happened\n"
            )

            handler._get_rnsd_user = MagicMock(return_value='testuser')

            with patch('builtins.print') as mock_print, \
                 patch('subprocess.run') as mock_run:
                mock_run.return_value = MagicMock(stdout='all good', returncode=0)
                handler._diagnose_nomadnet_error(1, 'testuser')

            output = ' '.join(
                str(call.args[0]) for call in mock_print.call_args_list
                if call.args
            )
            self.assertIn('No known error pattern', output)
            self.assertIn('Something unusual happened', output)


class TestShareInstancePreFlight(unittest.TestCase):
    """Test _check_share_instance_for_nomadnet."""

    def _make_handler(self):
        from launcher_tui.handlers.nomadnet import NomadNetHandler

        ctx = MagicMock()
        ctx.dialog = MagicMock()
        handler = NomadNetHandler.__new__(NomadNetHandler)
        handler.ctx = ctx
        return handler

    def test_skipped_when_rnsd_not_running(self):
        """No check when rnsd is not running."""
        handler = self._make_handler()
        handler._get_rnsd_user = MagicMock(return_value=None)

        result = handler._check_share_instance_for_nomadnet('/etc/reticulum')
        self.assertTrue(result)

    def test_passes_when_share_instance_yes(self):
        """Returns True when share_instance = Yes."""
        handler = self._make_handler()
        handler._get_rnsd_user = MagicMock(return_value='testuser')

        with tempfile.TemporaryDirectory() as tmpdir:
            config_file = Path(tmpdir) / 'config'
            config_file.write_text(
                "[reticulum]\n"
                "  share_instance = Yes\n"
                "  shared_instance_port = 37428\n"
            )

            result = handler._check_share_instance_for_nomadnet(tmpdir)
            self.assertTrue(result)

    def test_warns_when_share_instance_missing(self):
        """Shows warning when share_instance is not Yes."""
        handler = self._make_handler()
        handler._get_rnsd_user = MagicMock(return_value='testuser')
        # User declines fix, then declines continue
        handler.ctx.dialog.yesno = MagicMock(return_value=False)

        with tempfile.TemporaryDirectory() as tmpdir:
            config_file = Path(tmpdir) / 'config'
            config_file.write_text(
                "[reticulum]\n"
                "  shared_instance_port = 37428\n"
            )

            result = handler._check_share_instance_for_nomadnet(tmpdir)
            # yesno called: first for "fix?", then for "continue anyway?"
            self.assertEqual(handler.ctx.dialog.yesno.call_count, 2)
            self.assertFalse(result)

    def test_skipped_when_no_config_file(self):
        """Returns True when config file doesn't exist."""
        handler = self._make_handler()
        handler._get_rnsd_user = MagicMock(return_value='testuser')

        with tempfile.TemporaryDirectory() as tmpdir:
            result = handler._check_share_instance_for_nomadnet(tmpdir)
            self.assertTrue(result)


class TestRnsdCrashLoopDetection(unittest.TestCase):
    """Test rnsd crash-loop detection in _check_rns_for_nomadnet."""

    def _make_handler(self):
        """Create handler with NomadNetRNSChecksMixin."""
        from launcher_tui.handlers._nomadnet_rns_checks import NomadNetRNSChecksMixin

        class MockHandler(NomadNetRNSChecksMixin):
            pass

        handler = MockHandler()
        handler.ctx = MagicMock()
        handler.ctx.dialog = MagicMock()
        return handler

    @patch.dict(os.environ, {'SUDO_USER': 'testuser'}, clear=False)
    def test_crash_loop_detected(self):
        """rnsd crash after initial check is caught."""
        handler = self._make_handler()
        # First call: rnsd running, second call (after sleep): rnsd gone
        handler._get_rnsd_user = MagicMock(side_effect=['testuser', None])
        handler._wait_for_rns_port = MagicMock(return_value=True)

        with patch('launcher_tui.handlers._nomadnet_rns_checks.time.sleep'):
            result = handler._check_rns_for_nomadnet()

        self.assertFalse(result)
        handler.ctx.dialog.msgbox.assert_called_once()
        call_args = handler.ctx.dialog.msgbox.call_args
        self.assertIn('crashed', call_args[0][0].lower())

    @patch.dict(os.environ, {'SUDO_USER': 'testuser'}, clear=False)
    def test_stable_rnsd_passes(self):
        """rnsd that stays running passes the check."""
        handler = self._make_handler()
        handler._get_rnsd_user = MagicMock(return_value='testuser')
        handler._wait_for_rns_port = MagicMock(return_value=True)

        with patch('launcher_tui.handlers._nomadnet_rns_checks.time.sleep'):
            result = handler._check_rns_for_nomadnet()

        self.assertTrue(result)


if __name__ == '__main__':
    unittest.main()
