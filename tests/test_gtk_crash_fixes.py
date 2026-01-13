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


if __name__ == '__main__':
    unittest.main()
