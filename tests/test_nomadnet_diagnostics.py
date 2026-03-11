"""
Tests for NomadNet startup diagnostics — error pattern detection,
share_instance pre-flight check, and rnsd crash-loop detection.
"""

import os
import subprocess
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
        handler._check_rnsd_rpc = MagicMock(return_value=True)

        with patch('launcher_tui.handlers._nomadnet_rns_checks.time.sleep'):
            result = handler._check_rns_for_nomadnet()

        self.assertTrue(result)


class TestConnectionRefusedDiagnosis(unittest.TestCase):
    """Test ConnectionRefusedError pattern in _diagnose_nomadnet_error."""

    def _make_handler(self):
        from launcher_tui.handlers.nomadnet import NomadNetHandler

        ctx = MagicMock()
        ctx.dialog = MagicMock()
        handler = NomadNetHandler.__new__(NomadNetHandler)
        handler.ctx = ctx
        return handler

    @patch('launcher_tui.handlers.nomadnet.get_real_user_home')
    def test_connection_refused_detected(self, mock_home):
        """ConnectionRefusedError pattern produces RPC hint."""
        handler = self._make_handler()

        with tempfile.TemporaryDirectory() as tmpdir:
            mock_home.return_value = Path(tmpdir)
            nn_dir = Path(tmpdir) / '.nomadnetwork'
            nn_dir.mkdir()
            logfile = nn_dir / 'logfile'
            logfile.write_text(
                "[2026-03-11 08:43:50] [Error] Type  : "
                "<class 'ConnectionRefusedError'>\n"
                "[2026-03-11 08:43:50] [Error] Value : "
                "[Errno 111] Connection refused\n"
            )

            with patch('builtins.print') as mock_print:
                handler._diagnose_nomadnet_error(1, 'testuser')

            output = ' '.join(
                str(call.args[0]) for call in mock_print.call_args_list
                if call.args
            )
            self.assertIn('RPC', output)
            self.assertIn('rnstatus', output)

    @patch('launcher_tui.handlers.nomadnet.get_real_user_home')
    def test_errno_111_detected(self, mock_home):
        """Errno 111 + Connection refused pattern detected."""
        handler = self._make_handler()

        with tempfile.TemporaryDirectory() as tmpdir:
            mock_home.return_value = Path(tmpdir)
            nn_dir = Path(tmpdir) / '.nomadnetwork'
            nn_dir.mkdir()
            logfile = nn_dir / 'logfile'
            logfile.write_text(
                "[2026-03-11 08:43:50] s.connect(address)\n"
                "[2026-03-11 08:43:50] [Errno 111] Connection refused\n"
            )

            with patch('builtins.print') as mock_print:
                handler._diagnose_nomadnet_error(1, 'testuser')

            output = ' '.join(
                str(call.args[0]) for call in mock_print.call_args_list
                if call.args
            )
            self.assertIn('RPC', output)


    @patch('launcher_tui.handlers.nomadnet.get_real_user_home')
    def test_connection_refused_in_long_traceback(self, mock_home):
        """ConnectionRefusedError detected even with long traceback + teardown."""
        handler = self._make_handler()

        with tempfile.TemporaryDirectory() as tmpdir:
            mock_home.return_value = Path(tmpdir)
            nn_dir = Path(tmpdir) / '.nomadnetwork'
            nn_dir.mkdir()
            logfile = nn_dir / 'logfile'
            # Simulate the real log: Error lines, then ~20 traceback frames,
            # then teardown notices — old maxlen=20 would miss the Error lines
            lines = [
                "[2026-03-11 08:43:50] [Info]     Starting user interface...\n",
                "[2026-03-11 08:43:50] [Error]    An unhandled exception occurred\n",
                "[2026-03-11 08:43:50] [Error]    Type  : "
                "<class 'ConnectionRefusedError'>\n",
                "[2026-03-11 08:43:50] [Error]    Value : "
                "[Errno 111] Connection refused\n",
                "[2026-03-11 08:43:50] [Error]    Trace :\n",
            ]
            # Add 20 traceback frame lines
            for i in range(20):
                lines.append(
                    f'  File "nomadnet/module{i}.py", line {i}, in func{i}\n'
                )
            # Add teardown notices
            lines.extend([
                "[2026-03-11 08:43:50] [Notice]   Tearing down...\n",
                "[2026-03-11 08:43:50] [Notice]   Persisting LXMF state...\n",
                "[2026-03-11 08:43:50] [Notice]   Saving 0 peers...\n",
                "[2026-03-11 08:43:50] [Notice]   Saved 0 peers in 400µs\n",
            ])
            logfile.write_text(''.join(lines))

            with patch('builtins.print') as mock_print:
                handler._diagnose_nomadnet_error(1, 'testuser')

            output = ' '.join(
                str(call.args[0]) for call in mock_print.call_args_list
                if call.args
            )
            self.assertIn('RPC', output)
            # Should NOT fall through to "No known error pattern"
            self.assertNotIn('No known error pattern', output)


class TestRnsdRpcCheck(unittest.TestCase):
    """Test _check_rnsd_rpc pre-launch check."""

    def _make_handler(self):
        from launcher_tui.handlers._nomadnet_rns_checks import NomadNetRNSChecksMixin

        class MockHandler(NomadNetRNSChecksMixin):
            pass

        handler = MockHandler()
        handler.ctx = MagicMock()
        handler.ctx.dialog = MagicMock()
        return handler

    @patch('launcher_tui.handlers._nomadnet_rns_checks.subprocess.run')
    def test_rpc_ok(self, mock_run):
        """rnstatus success means RPC is available."""
        handler = self._make_handler()
        mock_run.return_value = MagicMock(returncode=0, stderr='')
        self.assertTrue(handler._check_rnsd_rpc('testuser'))

    @patch('launcher_tui.handlers._nomadnet_rns_checks.subprocess.run')
    def test_rpc_connection_refused(self, mock_run):
        """rnstatus failing with connection refused returns False."""
        handler = self._make_handler()
        mock_run.return_value = MagicMock(
            returncode=1, stderr='Connection refused'
        )
        self.assertFalse(handler._check_rnsd_rpc('testuser'))

    @patch('launcher_tui.handlers._nomadnet_rns_checks.subprocess.run')
    def test_rnstatus_not_installed(self, mock_run):
        """Missing rnstatus doesn't block launch."""
        handler = self._make_handler()
        mock_run.side_effect = FileNotFoundError("rnstatus")
        self.assertTrue(handler._check_rnsd_rpc('testuser'))

    @patch('launcher_tui.handlers._nomadnet_rns_checks.subprocess.run')
    def test_rpc_timeout(self, mock_run):
        """Timeout returns False (RPC is stuck)."""
        handler = self._make_handler()
        mock_run.side_effect = subprocess.TimeoutExpired(cmd='rnstatus', timeout=10)
        self.assertFalse(handler._check_rnsd_rpc('testuser'))

    @patch.dict(os.environ, {'SUDO_USER': 'testuser'}, clear=False)
    @patch('launcher_tui.handlers._nomadnet_rns_checks.subprocess.run')
    def test_rpc_failure_shows_menu(self, mock_run):
        """RPC failure in _check_rns_for_nomadnet shows restart menu."""
        handler = self._make_handler()
        handler._get_rnsd_user = MagicMock(return_value='testuser')
        handler._wait_for_rns_port = MagicMock(return_value=True)
        handler._get_rnsd_uptime = MagicMock(return_value=60)
        # rnstatus returns connection refused
        mock_run.return_value = MagicMock(
            returncode=1, stderr='[Errno 111] Connection refused'
        )
        # User cancels from the menu
        handler.ctx.dialog.menu = MagicMock(return_value='cancel')

        with patch('launcher_tui.handlers._nomadnet_rns_checks.time.sleep'):
            result = handler._check_rns_for_nomadnet()

        self.assertFalse(result)
        # Verify menu was called with RPC options
        menu_calls = handler.ctx.dialog.menu.call_args_list
        rpc_call = [c for c in menu_calls if 'RPC' in str(c)]
        self.assertTrue(len(rpc_call) > 0, "Expected RPC menu dialog")


class TestRpcAutoRestart(unittest.TestCase):
    """Test RPC failure auto-restart flow."""

    def _make_handler(self):
        from launcher_tui.handlers._nomadnet_rns_checks import NomadNetRNSChecksMixin

        class MockHandler(NomadNetRNSChecksMixin):
            pass

        handler = MockHandler()
        handler.ctx = MagicMock()
        handler.ctx.dialog = MagicMock()
        return handler

    @patch('launcher_tui.handlers._nomadnet_rns_checks.subprocess.run')
    def test_young_rnsd_waits_and_retries(self, mock_run):
        """RPC failure with young rnsd triggers wait+retry."""
        handler = self._make_handler()
        handler._get_rnsd_uptime = MagicMock(return_value=3)
        # First check fails, second succeeds (after wait)
        handler._check_rnsd_rpc = MagicMock(side_effect=[True])

        with patch('launcher_tui.handlers._nomadnet_rns_checks.time.sleep'):
            result = handler._handle_rpc_failure('testuser')

        self.assertTrue(result)
        # Should have shown "Waiting" infobox
        infobox_calls = handler.ctx.dialog.infobox.call_args_list
        waiting_call = [c for c in infobox_calls if 'Waiting' in str(c)]
        self.assertTrue(len(waiting_call) > 0)

    @patch('launcher_tui.handlers._nomadnet_rns_checks.subprocess.run')
    def test_mature_rnsd_offers_restart(self, mock_run):
        """RPC failure with mature rnsd shows restart menu."""
        handler = self._make_handler()
        handler._get_rnsd_uptime = MagicMock(return_value=120)
        handler._check_rnsd_rpc = MagicMock(return_value=False)
        handler.ctx.dialog.menu = MagicMock(return_value='cancel')

        with patch('launcher_tui.handlers._nomadnet_rns_checks.time.sleep'):
            result = handler._handle_rpc_failure('testuser')

        self.assertFalse(result)
        handler.ctx.dialog.menu.assert_called_once()

    @patch('launcher_tui.handlers._nomadnet_rns_checks.restart_service')
    @patch('launcher_tui.handlers._nomadnet_rns_checks.subprocess.run')
    def test_restart_resolves_rpc(self, mock_run, mock_restart):
        """Successful restart makes RPC work."""
        handler = self._make_handler()
        handler._wait_for_rns_port = MagicMock(return_value=True)
        handler._check_rnsd_rpc = MagicMock(return_value=True)
        mock_restart.return_value = (True, 'ok')

        with patch('launcher_tui.handlers._nomadnet_rns_checks.time.sleep'):
            result = handler._restart_rnsd_and_verify_rpc('testuser')

        self.assertTrue(result)
        handler.ctx.dialog.msgbox.assert_called()
        # Verify "RPC Ready" shown
        msg_calls = handler.ctx.dialog.msgbox.call_args_list
        rpc_ready = [c for c in msg_calls if 'RPC Ready' in str(c)]
        self.assertTrue(len(rpc_ready) > 0)

    @patch('launcher_tui.handlers._nomadnet_rns_checks.restart_service')
    @patch('launcher_tui.handlers._nomadnet_rns_checks.subprocess.run')
    def test_restart_fails(self, mock_run, mock_restart):
        """Failed restart shows error."""
        handler = self._make_handler()
        mock_restart.return_value = (False, 'unit not found')

        with patch('launcher_tui.handlers._nomadnet_rns_checks.time.sleep'):
            result = handler._restart_rnsd_and_verify_rpc('testuser')

        self.assertFalse(result)
        handler.ctx.dialog.msgbox.assert_called()

    @patch('launcher_tui.handlers._nomadnet_rns_checks.subprocess.run')
    def test_user_mismatch_checked_before_rpc(self, mock_run):
        """User mismatch is detected before RPC check runs."""
        handler = self._make_handler()
        # rnsd as root, user is testuser
        handler._get_rnsd_user = MagicMock(return_value='root')
        handler._wait_for_rns_port = MagicMock(return_value=True)
        # User cancels from mismatch menu
        handler.ctx.dialog.menu = MagicMock(return_value='cancel')

        with patch.dict(os.environ, {'SUDO_USER': 'testuser'}, clear=False), \
             patch('launcher_tui.handlers._nomadnet_rns_checks.time.sleep'):
            result = handler._check_rns_for_nomadnet()

        self.assertFalse(result)
        # Menu should show "Running as Root" (user mismatch), not "RPC"
        menu_call = handler.ctx.dialog.menu.call_args
        self.assertIn('Root', str(menu_call))

    @patch('launcher_tui.handlers._nomadnet_rns_checks.subprocess.run')
    def test_get_rnsd_uptime(self, mock_run):
        """_get_rnsd_uptime parses ps output."""
        handler = self._make_handler()
        mock_run.return_value = MagicMock(
            returncode=0, stdout='   42\n'
        )
        self.assertEqual(handler._get_rnsd_uptime(), 42)

    @patch('launcher_tui.handlers._nomadnet_rns_checks.subprocess.run')
    def test_get_rnsd_uptime_not_running(self, mock_run):
        """_get_rnsd_uptime returns None when rnsd not running."""
        handler = self._make_handler()
        mock_run.return_value = MagicMock(
            returncode=1, stdout=''
        )
        self.assertIsNone(handler._get_rnsd_uptime())

    @patch('launcher_tui.handlers._nomadnet_rns_checks.subprocess.run')
    def test_continue_anyway_from_rpc_menu(self, mock_run):
        """User can choose 'continue' from RPC failure menu."""
        handler = self._make_handler()
        handler._get_rnsd_uptime = MagicMock(return_value=120)
        handler._check_rnsd_rpc = MagicMock(return_value=False)
        handler.ctx.dialog.menu = MagicMock(return_value='continue')

        with patch('launcher_tui.handlers._nomadnet_rns_checks.time.sleep'):
            result = handler._handle_rpc_failure('testuser')

        self.assertTrue(result)


if __name__ == '__main__':
    unittest.main()
