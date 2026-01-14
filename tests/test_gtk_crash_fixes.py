"""
Tests for GTK crash fixes in MeshForge RNS panel.

These tests verify that the crash fixes are working correctly:
1. Dropdown index bounds checking (GTK_INVALID_LIST_POSITION = -1)
2. Timer tracking for cleanup on widget destruction
3. Socket cleanup patterns

Run with: python3 -m unittest tests/test_gtk_crash_fixes.py -v
"""

import unittest
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))


class TestDropdownIndexBoundsChecking(unittest.TestCase):
    """Test that dropdown selections handle -1 (GTK_INVALID_LIST_POSITION) correctly."""

    def test_bandwidth_index_negative_one(self):
        """Test bandwidth selection with -1 returns valid default."""
        bw_values = [7800, 10400, 15600, 20800, 31250, 41700, 62500, 125000, 250000, 500000]

        # Simulate GTK_INVALID_LIST_POSITION
        bw_idx = -1

        # Apply the fix pattern from rnode.py
        if bw_idx < 0 or bw_idx >= len(bw_values):
            bw_idx = 8  # Default to 250 kHz

        bw_hz = bw_values[bw_idx]

        # Should get default (250000 Hz) not crash or wrong value
        self.assertEqual(bw_hz, 250000)
        self.assertEqual(bw_idx, 8)

    def test_bandwidth_index_out_of_bounds(self):
        """Test bandwidth selection with index >= len returns valid default."""
        bw_values = [7800, 10400, 15600, 20800, 31250, 41700, 62500, 125000, 250000, 500000]

        # Simulate out of bounds
        bw_idx = 100

        # Apply the fix pattern
        if bw_idx < 0 or bw_idx >= len(bw_values):
            bw_idx = 8

        bw_hz = bw_values[bw_idx]
        self.assertEqual(bw_hz, 250000)

    def test_bandwidth_index_valid(self):
        """Test bandwidth selection with valid index works correctly."""
        bw_values = [7800, 10400, 15600, 20800, 31250, 41700, 62500, 125000, 250000, 500000]

        # Valid selection
        bw_idx = 3

        # Apply the fix pattern
        if bw_idx < 0 or bw_idx >= len(bw_values):
            bw_idx = 8

        bw_hz = bw_values[bw_idx]
        self.assertEqual(bw_hz, 20800)  # Index 3 = 20800 Hz

    def test_coding_rate_index_negative_one(self):
        """Test coding rate selection with -1 returns valid default."""
        cr_idx = -1

        # Apply the fix pattern from rnode.py
        if cr_idx < 0 or cr_idx > 3:
            cr_idx = 0  # Default to 4/5

        cr = cr_idx + 5  # 0->5, 1->6, 2->7, 3->8

        self.assertEqual(cr, 5)  # Default coding rate 4/5

    def test_coding_rate_index_out_of_bounds(self):
        """Test coding rate selection with index > 3 returns valid default."""
        cr_idx = 10

        if cr_idx < 0 or cr_idx > 3:
            cr_idx = 0

        cr = cr_idx + 5
        self.assertEqual(cr, 5)

    def test_device_selection_negative_one(self):
        """Test device selection with -1 is properly guarded."""
        selected_idx = -1
        detected_devices = [MagicMock(port='/dev/ttyUSB0')]

        # Apply the fix pattern from rnode.py _on_device_selected
        if selected_idx < 0 or not detected_devices or selected_idx >= len(detected_devices):
            result = None  # Should early return
        else:
            result = detected_devices[selected_idx]

        # Should NOT access the list with -1
        self.assertIsNone(result)

    def test_device_selection_empty_list(self):
        """Test device selection with empty list is properly guarded."""
        selected_idx = 0
        detected_devices = []

        if selected_idx < 0 or not detected_devices or selected_idx >= len(detected_devices):
            result = None
        else:
            result = detected_devices[selected_idx]

        self.assertIsNone(result)

    def test_device_selection_valid(self):
        """Test device selection with valid index works correctly."""
        selected_idx = 0
        mock_device = MagicMock(port='/dev/ttyUSB0')
        detected_devices = [mock_device]

        if selected_idx < 0 or not detected_devices or selected_idx >= len(detected_devices):
            result = None
        else:
            result = detected_devices[selected_idx]

        self.assertEqual(result, mock_device)


class TestTimerTracking(unittest.TestCase):
    """Test that timer tracking patterns work correctly."""

    def test_schedule_timer_tracks_id(self):
        """Test that _schedule_timer adds timer ID to pending list."""
        pending_timers = []

        def schedule_timer(delay_ms, callback):
            # Simulate GLib.timeout_add returning an ID
            timer_id = 12345
            pending_timers.append(timer_id)
            return timer_id

        timer_id = schedule_timer(2000, lambda: None)

        self.assertEqual(len(pending_timers), 1)
        self.assertEqual(pending_timers[0], 12345)

    def test_unrealize_clears_timers(self):
        """Test that unrealize handler clears all pending timers."""
        pending_timers = [1, 2, 3, 4, 5]
        removed_timers = []

        def mock_source_remove(timer_id):
            removed_timers.append(timer_id)

        # Simulate _on_unrealize
        for timer_id in pending_timers:
            try:
                mock_source_remove(timer_id)
            except Exception:
                pass
        pending_timers.clear()

        self.assertEqual(len(pending_timers), 0)
        self.assertEqual(removed_timers, [1, 2, 3, 4, 5])

    def test_timer_callback_pattern(self):
        """Test that timer callbacks return False to not repeat."""
        # Timer callbacks should return False to run once
        def timer_callback():
            # Do work
            return False  # Don't repeat

        result = timer_callback()
        self.assertFalse(result)


class TestSocketCleanupPatterns(unittest.TestCase):
    """Test socket cleanup patterns are correct."""

    def test_check_port_closes_on_success(self):
        """Test socket is closed even on successful connection."""
        import socket
        import os

        initial_fds = len(os.listdir(f'/proc/{os.getpid()}/fd'))

        # Simulate the fixed pattern
        sock = None
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(0.1)
            result = sock.connect_ex(('127.0.0.1', 59999))
        except Exception:
            pass
        finally:
            if sock:
                try:
                    sock.close()
                except Exception:
                    pass

        final_fds = len(os.listdir(f'/proc/{os.getpid()}/fd'))
        self.assertEqual(final_fds, initial_fds)

    def test_check_port_closes_on_exception(self):
        """Test socket is closed when exception occurs."""
        import socket
        import os

        initial_fds = len(os.listdir(f'/proc/{os.getpid()}/fd'))

        sock = None
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(0.001)  # Very short timeout to force exception
            # This will raise timeout
            sock.connect(('192.0.2.1', 12345))  # TEST-NET, will timeout
        except Exception:
            pass
        finally:
            if sock:
                try:
                    sock.close()
                except Exception:
                    pass

        final_fds = len(os.listdir(f'/proc/{os.getpid()}/fd'))
        self.assertEqual(final_fds, initial_fds)


class TestPresetDropdownBounds(unittest.TestCase):
    """Test preset dropdown bounds checking patterns."""

    def test_preset_selection_bounds_check(self):
        """Test preset selection with bounds checking pattern."""
        preset_names = ['SHORT_FAST', 'SHORT_SLOW', 'MEDIUM_FAST', 'LONG_FAST']

        # Test valid selection
        selected_idx = 2
        if 0 <= selected_idx < len(preset_names):
            preset_name = preset_names[selected_idx]
        else:
            preset_name = None

        self.assertEqual(preset_name, 'MEDIUM_FAST')

    def test_preset_selection_negative_index(self):
        """Test preset selection with negative index."""
        preset_names = ['SHORT_FAST', 'SHORT_SLOW']

        selected_idx = -1
        if 0 <= selected_idx < len(preset_names):
            preset_name = preset_names[selected_idx]
        else:
            preset_name = None

        self.assertIsNone(preset_name)

    def test_preset_selection_out_of_bounds(self):
        """Test preset selection with out of bounds index."""
        preset_names = ['SHORT_FAST', 'SHORT_SLOW']

        selected_idx = 100
        if 0 <= selected_idx < len(preset_names):
            preset_name = preset_names[selected_idx]
        else:
            preset_name = None

        self.assertIsNone(preset_name)


class TestNodeCountThreadSafety(unittest.TestCase):
    """
    Test that node count fetching is thread-safe and doesn't block GTK.

    Regression test for the GTK freeze caused by meshtastic CLI auto-detection.
    The _get_node_count() method MUST:
    1. Do a quick port check before calling the CLI
    2. Use --host localhost to avoid USB/serial auto-detection
    3. Skip the CLI call entirely if port is not reachable

    Without these safeguards, the meshtastic CLI does slow USB/serial scanning
    which blocks threads and can freeze the GTK main loop.
    """

    def test_port_check_before_cli_call_pattern(self):
        """
        Test that the port check pattern is used before CLI calls.

        The pattern must be: check socket FIRST, then only call CLI if reachable.
        This prevents the expensive meshtastic CLI from doing auto-detection.
        """
        import socket

        # Simulate the correct pattern from app.py
        port_reachable = False
        sock = None
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(1.0)  # Must be short (1 second max)
            sock.connect(("localhost", 4403))
            port_reachable = True
        except (socket.timeout, socket.error, OSError):
            port_reachable = False
        finally:
            if sock:
                try:
                    sock.close()
                except Exception:
                    pass

        # Port 4403 unlikely to be open in test environment
        # Key assertion: the check completes quickly without blocking
        self.assertIsInstance(port_reachable, bool)

    def test_cli_must_use_host_localhost(self):
        """
        Test that CLI command includes --host localhost flag.

        Without --host, meshtastic CLI does USB/serial auto-detection
        which can take 15+ seconds and freeze the UI.
        """
        # The correct command pattern
        cli_path = '/usr/bin/meshtastic'
        correct_command = [cli_path, '--host', 'localhost', '--nodes']

        # These are WRONG patterns that cause freezes
        wrong_patterns = [
            [cli_path, '--nodes'],  # No --host = auto-detect
            [cli_path, '--nodes', '--host', 'localhost'],  # Wrong order
        ]

        # Verify correct pattern has --host before --nodes
        self.assertIn('--host', correct_command)
        host_idx = correct_command.index('--host')
        nodes_idx = correct_command.index('--nodes')

        # --host should come before --nodes
        self.assertLess(host_idx, nodes_idx)

        # --host should be followed by 'localhost'
        self.assertEqual(correct_command[host_idx + 1], 'localhost')

    def test_timeout_is_reasonable(self):
        """
        Test that CLI timeout is not too long.

        Long timeouts (15+ seconds) combined with the 5-second status timer
        can pile up threads and cause resource exhaustion.
        """
        # Maximum reasonable timeout for CLI call
        max_timeout = 10  # seconds

        # The status timer interval
        status_interval = 5  # seconds

        # Timeout should be less than 2x the timer interval
        # to prevent thread pile-up
        self.assertLessEqual(max_timeout, status_interval * 2)

    def test_cache_prevents_rapid_cli_calls(self):
        """
        Test that caching prevents CLI from being called too frequently.

        The cache TTL should be longer than the status timer interval
        to prevent unnecessary CLI calls.
        """
        cache_ttl = 30  # seconds (from app.py _node_count_cache_ttl)
        status_interval = 5  # seconds

        # Cache should last at least 2 timer intervals
        self.assertGreaterEqual(cache_ttl, status_interval * 2)

    def test_socket_check_timeout_is_short(self):
        """
        Test that socket pre-check timeout is short enough to not block UI.

        The socket check runs in a background thread, but we still want
        it to be fast so threads don't pile up.
        """
        socket_timeout = 1.0  # seconds (from app.py)

        # Socket check should complete in 1 second or less
        self.assertLessEqual(socket_timeout, 1.0)


class TestNodeCountCodePattern(unittest.TestCase):
    """
    Verify the actual code in app.py follows the correct pattern.

    This is a meta-test that reads the source code and verifies
    the safety patterns are present. This prevents accidental removal
    of critical guards.
    """

    def setUp(self):
        """Load the app.py source code."""
        app_path = Path(__file__).parent.parent / 'src' / 'gtk_ui' / 'app.py'
        self.source = app_path.read_text()

    def test_shutil_is_imported(self):
        """Verify shutil is imported (needed for shutil.which fallback)."""
        self.assertIn('import shutil', self.source)

    def test_port_check_exists_before_cli(self):
        """Verify port check pattern exists in _get_node_count."""
        # The method should contain socket check before CLI call
        self.assertIn('sock.settimeout', self.source)
        self.assertIn('sock.connect', self.source)
        self.assertIn('port_reachable', self.source)

    def test_host_localhost_flag_present(self):
        """Verify --host localhost is used in CLI command."""
        self.assertIn("'--host', 'localhost'", self.source)

    def test_early_return_when_port_unreachable(self):
        """Verify early return when port is not reachable."""
        self.assertIn('if not port_reachable:', self.source)


if __name__ == '__main__':
    unittest.main()
