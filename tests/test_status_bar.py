"""
Tests for TUI Status Bar.

Tests cover:
- Status line format and content
- Service status caching with TTL
- Node count and bridge status display
- Cache invalidation
- Graceful failure handling
- DialogBackend --backtitle integration

Run with: pytest tests/test_status_bar.py -v
"""

import pytest
import subprocess
import sys
import os
import time
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src', 'launcher_tui'))

from status_bar import (
    StatusBar, STATUS_CACHE_TTL,
    SYM_RUNNING, SYM_STOPPED, SYM_UNKNOWN,
    MONITORED_SERVICES,
)


class TestStatusBarFormat:
    """Test status line formatting."""

    def test_includes_version(self):
        bar = StatusBar(version="0.4.7-beta")
        with patch.object(bar, '_check_systemd_active', return_value=SYM_STOPPED):
            with patch.object(bar, '_check_bridge'):
                line = bar.get_status_line()
        assert "MeshForge v0.4.7-beta" in line

    def test_no_version(self):
        bar = StatusBar(version="")
        with patch.object(bar, '_check_systemd_active', return_value=SYM_STOPPED):
            with patch.object(bar, '_check_bridge'):
                line = bar.get_status_line()
        assert line.startswith("MeshForge |")

    def test_pipe_separated(self):
        bar = StatusBar(version="1.0")
        with patch.object(bar, '_check_systemd_active', return_value=SYM_RUNNING):
            with patch.object(bar, '_check_bridge'):
                line = bar.get_status_line()
        assert " | " in line

    def test_shows_all_monitored_services(self):
        bar = StatusBar(version="1.0")
        with patch.object(bar, '_check_systemd_active', return_value=SYM_RUNNING):
            with patch.object(bar, '_check_bridge'):
                line = bar.get_status_line()
        for _, short_name in MONITORED_SERVICES:
            assert f"{short_name}:" in line

    def test_running_symbol(self):
        bar = StatusBar(version="1.0")
        with patch.object(bar, '_check_systemd_active', return_value=SYM_RUNNING):
            with patch.object(bar, '_check_bridge'):
                line = bar.get_status_line()
        assert f"mesh:{SYM_RUNNING}" in line

    def test_stopped_symbol(self):
        bar = StatusBar(version="1.0")
        with patch.object(bar, '_check_systemd_active', return_value=SYM_STOPPED):
            with patch.object(bar, '_check_bridge'):
                line = bar.get_status_line()
        assert f"mesh:{SYM_STOPPED}" in line


class TestServiceChecks:
    """Test service status checking."""

    @patch('subprocess.run')
    def test_active_service(self, mock_run):
        mock_run.return_value = MagicMock(stdout='active\n', returncode=0)
        bar = StatusBar()
        result = bar._check_systemd_active('meshtasticd')
        assert result == SYM_RUNNING
        mock_run.assert_called_once_with(
            ['systemctl', 'is-active', 'meshtasticd'],
            capture_output=True, text=True, timeout=3
        )

    @patch('subprocess.run')
    def test_inactive_service(self, mock_run):
        mock_run.return_value = MagicMock(stdout='inactive\n', returncode=3)
        bar = StatusBar()
        result = bar._check_systemd_active('rnsd')
        assert result == SYM_STOPPED

    @patch('subprocess.run')
    def test_failed_service(self, mock_run):
        mock_run.return_value = MagicMock(stdout='failed\n', returncode=3)
        bar = StatusBar()
        result = bar._check_systemd_active('mosquitto')
        assert result == SYM_STOPPED

    @patch('subprocess.run')
    def test_timeout_returns_unknown(self, mock_run):
        mock_run.side_effect = subprocess.TimeoutExpired(cmd='systemctl', timeout=3)
        bar = StatusBar()
        result = bar._check_systemd_active('meshtasticd')
        assert result == SYM_UNKNOWN

    @patch('subprocess.run')
    def test_no_systemctl_returns_unknown(self, mock_run):
        mock_run.side_effect = FileNotFoundError()
        bar = StatusBar()
        result = bar._check_systemd_active('meshtasticd')
        assert result == SYM_UNKNOWN


class TestBridgeCheck:
    """Test bridge status checking."""

    @patch('subprocess.run')
    def test_bridge_running(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        bar = StatusBar()
        bar._check_bridge()
        assert bar._bridge_running is True

    @patch('subprocess.run')
    def test_bridge_not_running(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1)
        bar = StatusBar()
        bar._check_bridge()
        assert bar._bridge_running is False

    @patch('subprocess.run')
    def test_bridge_check_failure(self, mock_run):
        mock_run.side_effect = FileNotFoundError()
        bar = StatusBar()
        bar._check_bridge()
        assert bar._bridge_running is None

    def test_bridge_displayed_when_running(self):
        bar = StatusBar(version="1.0")
        bar._bridge_running = True
        bar._cache_time = time.time()  # Prevent refresh
        bar._cache = {s: SYM_STOPPED for s, _ in MONITORED_SERVICES}
        line = bar.get_status_line()
        assert f"bridge:{SYM_RUNNING}" in line

    def test_bridge_displayed_when_stopped(self):
        bar = StatusBar(version="1.0")
        bar._bridge_running = False
        bar._cache_time = time.time()
        bar._cache = {s: SYM_STOPPED for s, _ in MONITORED_SERVICES}
        line = bar.get_status_line()
        assert f"bridge:{SYM_STOPPED}" in line

    def test_bridge_not_displayed_when_none(self):
        bar = StatusBar(version="1.0")
        bar._bridge_running = None
        bar._cache_time = time.time()
        bar._cache = {s: SYM_STOPPED for s, _ in MONITORED_SERVICES}
        line = bar.get_status_line()
        assert "bridge" not in line


class TestNodeCount:
    """Test node count display."""

    def test_set_node_count(self):
        bar = StatusBar(version="1.0")
        bar._cache_time = time.time()
        bar._cache = {s: SYM_STOPPED for s, _ in MONITORED_SERVICES}
        bar.set_node_count(7)
        line = bar.get_status_line()
        assert "nodes:7" in line

    def test_no_node_count_by_default(self):
        bar = StatusBar(version="1.0")
        bar._cache_time = time.time()
        bar._cache = {s: SYM_STOPPED for s, _ in MONITORED_SERVICES}
        line = bar.get_status_line()
        assert "nodes" not in line

    def test_zero_nodes(self):
        bar = StatusBar(version="1.0")
        bar._cache_time = time.time()
        bar._cache = {s: SYM_STOPPED for s, _ in MONITORED_SERVICES}
        bar.set_node_count(0)
        line = bar.get_status_line()
        assert "nodes:0" in line


class TestCaching:
    """Test cache TTL behavior."""

    def test_fresh_cache_not_refreshed(self):
        bar = StatusBar(version="1.0")
        bar._cache_time = time.time()
        bar._cache = {s: SYM_RUNNING for s, _ in MONITORED_SERVICES}

        with patch.object(bar, '_check_services') as mock_check:
            bar._refresh_if_stale()
            mock_check.assert_not_called()

    def test_stale_cache_triggers_refresh(self):
        bar = StatusBar(version="1.0")
        bar._cache_time = time.time() - STATUS_CACHE_TTL - 1

        with patch.object(bar, '_check_services') as mock_services:
            with patch.object(bar, '_check_bridge') as mock_bridge:
                bar._refresh_if_stale()
                mock_services.assert_called_once()
                mock_bridge.assert_called_once()

    def test_invalidate_forces_refresh(self):
        bar = StatusBar(version="1.0")
        bar._cache_time = time.time()
        bar._cache = {s: SYM_RUNNING for s, _ in MONITORED_SERVICES}

        bar.invalidate()
        assert bar._cache_time == 0.0

        with patch.object(bar, '_check_services') as mock_services:
            with patch.object(bar, '_check_bridge'):
                bar._refresh_if_stale()
                mock_services.assert_called_once()

    def test_get_service_status_triggers_refresh(self):
        bar = StatusBar(version="1.0")
        bar._cache_time = 0.0  # Force stale

        with patch.object(bar, '_check_systemd_active', return_value=SYM_RUNNING):
            with patch.object(bar, '_check_bridge'):
                result = bar.get_service_status('meshtasticd')
        assert result == SYM_RUNNING


class TestDialogBackendIntegration:
    """Test StatusBar integration with DialogBackend."""

    def test_set_status_bar(self):
        from backend import DialogBackend
        backend = DialogBackend()
        bar = StatusBar(version="1.0")
        backend.set_status_bar(bar)
        assert backend._status_bar is bar

    def test_no_status_bar_by_default(self):
        from backend import DialogBackend
        backend = DialogBackend()
        assert backend._status_bar is None

    @patch('subprocess.run', return_value=MagicMock(returncode=0))
    def test_backtitle_injected(self, mock_run):
        """When status bar is set, --backtitle should be in the command."""
        from backend import DialogBackend
        backend = DialogBackend()
        backend.backend = 'whiptail'

        bar = StatusBar(version="1.0")
        bar._cache_time = time.time()
        bar._cache = {s: SYM_RUNNING for s, _ in MONITORED_SERVICES}
        backend.set_status_bar(bar)

        # Call a dialog method (msgbox for simplicity)
        backend.msgbox("Test", "Hello")

        # Verify subprocess.run was called with --backtitle in the args list
        call_args = mock_run.call_args[0][0]
        assert '--backtitle' in call_args

    @patch('subprocess.run', return_value=MagicMock(returncode=0))
    def test_no_backtitle_without_bar(self, mock_run):
        """Without status bar, no --backtitle in command."""
        from backend import DialogBackend
        backend = DialogBackend()
        backend.backend = 'whiptail'

        backend.msgbox("Test", "Hello")

        call_args = mock_run.call_args[0][0]
        assert '--backtitle' not in call_args

    @patch('subprocess.run', return_value=MagicMock(returncode=0))
    def test_status_bar_exception_doesnt_crash(self, mock_run):
        """Status bar failure must never block dialog display."""
        from backend import DialogBackend
        backend = DialogBackend()
        backend.backend = 'whiptail'

        # Create a broken status bar
        bar = MagicMock()
        bar.get_status_line.side_effect = RuntimeError("broken")
        backend.set_status_bar(bar)

        # Should still work without error
        backend.msgbox("Test", "Hello")
        mock_run.assert_called_once()

        # --backtitle should NOT be in the command (graceful fallback)
        call_args = mock_run.call_args[0][0]
        assert '--backtitle' not in call_args


class TestStatusBarSymbols:
    """Test that symbols are terminal-safe."""

    def test_running_symbol_is_ascii(self):
        assert SYM_RUNNING.isascii()

    def test_stopped_symbol_is_ascii(self):
        assert SYM_STOPPED.isascii()

    def test_unknown_symbol_is_ascii(self):
        assert SYM_UNKNOWN.isascii()

    def test_status_line_is_ascii(self):
        bar = StatusBar(version="1.0")
        bar._cache_time = time.time()
        bar._cache = {s: SYM_RUNNING for s, _ in MONITORED_SERVICES}
        bar._bridge_running = True
        bar.set_node_count(5)
        line = bar.get_status_line()
        assert line.isascii()


class TestStatusBarEdgeCases:
    """Test edge cases."""

    def test_empty_version_string(self):
        bar = StatusBar(version="")
        bar._cache_time = time.time()
        bar._cache = {s: SYM_STOPPED for s, _ in MONITORED_SERVICES}
        line = bar.get_status_line()
        assert "MeshForge" in line
        assert "v" not in line.split("|")[0] or "MeshForge |" in line

    def test_large_node_count(self):
        bar = StatusBar(version="1.0")
        bar._cache_time = time.time()
        bar._cache = {s: SYM_STOPPED for s, _ in MONITORED_SERVICES}
        bar.set_node_count(9999)
        line = bar.get_status_line()
        assert "nodes:9999" in line

    def test_concurrent_calls_safe(self):
        """Multiple rapid calls should not crash."""
        bar = StatusBar(version="1.0")
        with patch.object(bar, '_check_systemd_active', return_value=SYM_RUNNING):
            with patch.object(bar, '_check_bridge'):
                for _ in range(100):
                    line = bar.get_status_line()
                    assert isinstance(line, str)
