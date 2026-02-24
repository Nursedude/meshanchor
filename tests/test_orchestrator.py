"""
Tests for ServiceOrchestrator startup logic.

Covers:
- Polling retry with crash detection
- Journalctl diagnostics on failure
- Port readiness waiting
- Service already running

Run: python3 -m pytest tests/test_orchestrator.py -v
"""

import subprocess
import pytest
from unittest.mock import patch, MagicMock, call
from dataclasses import dataclass
from pathlib import Path

from utils.service_check import (
    ServiceState as _CheckState,
    ServiceStatus as _CheckStatus,
)


def _make_status(available, state, name='meshtasticd', message=''):
    """Helper to build a service_check.ServiceStatus."""
    return _CheckStatus(
        name=name,
        available=available,
        state=state,
        message=message,
    )


def _available():
    return _make_status(True, _CheckState.AVAILABLE, message='running')


def _not_running():
    return _make_status(False, _CheckState.NOT_RUNNING, message='not running')


def _failed():
    return _make_status(False, _CheckState.FAILED, message='has failed')


@pytest.fixture
def orchestrator():
    """Create a ServiceOrchestrator with mocked config loading."""
    with patch('core.orchestrator.Path.exists', return_value=False), \
         patch('core.orchestrator._detect_radio_hardware', return_value={
             'has_spi': False, 'has_usb': True,
             'spi_devices': [], 'usb_devices': ['/dev/ttyUSB0'],
             'usb_device': '/dev/ttyUSB0', 'hardware_type': 'usb',
         }):
        from core.orchestrator import ServiceOrchestrator
        orch = ServiceOrchestrator(config_path=Path('/nonexistent'))
        return orch


class TestStartServiceCrashDetection:
    """Test crash detection and restart logic in start_service()."""

    def test_crash_detected_and_restarted_successfully(self, orchestrator):
        """When service crashes, orchestrator restarts it once and succeeds."""
        # check_service returns: FAILED on first poll, AVAILABLE after restart
        check_responses = [_failed(), _available()]
        check_iter = iter(check_responses)

        with patch.object(orchestrator, 'is_installed', return_value=True), \
             patch.object(orchestrator, 'is_running', return_value=False), \
             patch.object(orchestrator, '_check_meshtasticd_config', return_value=True), \
             patch('core.orchestrator.check_service', side_effect=lambda _: next(check_iter)), \
             patch('subprocess.run', return_value=MagicMock(returncode=0, stdout='', stderr='')), \
             patch('time.sleep'), \
             patch.object(orchestrator, '_check_port', return_value=True), \
             patch.object(orchestrator, '_log_journal_tail') as mock_journal, \
             patch.object(orchestrator, '_emit'):

            result = orchestrator.start_service('meshtasticd')

            assert result is True
            # Journal should have been logged on crash detection
            mock_journal.assert_called()

    def test_crash_permanent_failure(self, orchestrator):
        """When service crashes and restart also fails, return False with diagnostics."""
        with patch.object(orchestrator, 'is_installed', return_value=True), \
             patch.object(orchestrator, 'is_running', return_value=False), \
             patch.object(orchestrator, '_check_meshtasticd_config', return_value=True), \
             patch('core.orchestrator.check_service', return_value=_failed()), \
             patch('subprocess.run', return_value=MagicMock(returncode=0, stdout='', stderr='')), \
             patch('time.sleep'), \
             patch.object(orchestrator, '_log_journal_tail') as mock_journal, \
             patch.object(orchestrator, '_emit') as mock_emit:

            result = orchestrator.start_service('meshtasticd')

            assert result is False
            # Journal diagnostics should be logged (at least once for crash, once for final failure)
            assert mock_journal.call_count >= 2
            mock_emit.assert_called_with('service_failed', 'meshtasticd')


class TestStartServiceSlowStartup:
    """Test polling handles slow service startups."""

    def test_service_available_after_several_polls(self, orchestrator):
        """Service takes a few seconds to start — polling waits for it."""
        # NOT_RUNNING for 3 polls, then AVAILABLE
        responses = [_not_running(), _not_running(), _not_running(), _available()]
        response_iter = iter(responses)

        with patch.object(orchestrator, 'is_installed', return_value=True), \
             patch.object(orchestrator, 'is_running', return_value=False), \
             patch.object(orchestrator, '_check_meshtasticd_config', return_value=True), \
             patch('core.orchestrator.check_service', side_effect=lambda _: next(response_iter)), \
             patch('subprocess.run', return_value=MagicMock(returncode=0, stdout='', stderr='')), \
             patch('time.sleep'), \
             patch.object(orchestrator, '_check_port', return_value=True), \
             patch.object(orchestrator, '_emit'):

            result = orchestrator.start_service('meshtasticd')

            assert result is True

    def test_service_timeout(self, orchestrator):
        """Service never starts within max_wait — timeout with diagnostics."""
        with patch.object(orchestrator, 'is_installed', return_value=True), \
             patch.object(orchestrator, 'is_running', return_value=False), \
             patch.object(orchestrator, '_check_meshtasticd_config', return_value=True), \
             patch('core.orchestrator.check_service', return_value=_not_running()), \
             patch('subprocess.run', return_value=MagicMock(returncode=0, stdout='', stderr='')), \
             patch('time.sleep'), \
             patch.object(orchestrator, '_log_journal_tail') as mock_journal, \
             patch.object(orchestrator, '_emit') as mock_emit:

            result = orchestrator.start_service('meshtasticd')

            assert result is False
            mock_journal.assert_called()
            mock_emit.assert_called_with('service_failed', 'meshtasticd')


class TestStartServicePortReadiness:
    """Test port readiness retry logic."""

    def test_port_ready_after_retries(self, orchestrator):
        """Port comes up after a few retries — logged as ready."""
        port_responses = [False, False, True]
        port_iter = iter(port_responses)

        with patch.object(orchestrator, 'is_installed', return_value=True), \
             patch.object(orchestrator, 'is_running', return_value=False), \
             patch.object(orchestrator, '_check_meshtasticd_config', return_value=True), \
             patch('core.orchestrator.check_service', return_value=_available()), \
             patch('subprocess.run', return_value=MagicMock(returncode=0, stdout='', stderr='')), \
             patch('time.sleep'), \
             patch.object(orchestrator, '_check_port', side_effect=lambda _: next(port_iter)), \
             patch.object(orchestrator, '_emit'):

            result = orchestrator.start_service('meshtasticd')

            assert result is True

    def test_port_never_ready_still_succeeds(self, orchestrator):
        """Port never binds — service still reports success (warning only)."""
        with patch.object(orchestrator, 'is_installed', return_value=True), \
             patch.object(orchestrator, 'is_running', return_value=False), \
             patch.object(orchestrator, '_check_meshtasticd_config', return_value=True), \
             patch('core.orchestrator.check_service', return_value=_available()), \
             patch('subprocess.run', return_value=MagicMock(returncode=0, stdout='', stderr='')), \
             patch('time.sleep'), \
             patch.object(orchestrator, '_check_port', return_value=False), \
             patch.object(orchestrator, '_emit'):

            result = orchestrator.start_service('meshtasticd')

            # Service is up, port not bound is just a warning
            assert result is True


class TestStartServiceAlreadyRunning:
    """Test early return when service is already running."""

    def test_already_running(self, orchestrator):
        """Service already running — return True immediately."""
        with patch.object(orchestrator, 'is_installed', return_value=True), \
             patch.object(orchestrator, 'is_running', return_value=True), \
             patch.object(orchestrator, '_check_meshtasticd_config', return_value=True):

            result = orchestrator.start_service('meshtasticd')

            assert result is True


class TestLogJournalTail:
    """Test _log_journal_tail helper."""

    def test_logs_journal_output(self, orchestrator):
        """Journal tail is logged for known service."""
        mock_result = MagicMock()
        mock_result.stdout = "2026-02-24 line1\n2026-02-24 line2"
        mock_result.returncode = 0

        with patch('subprocess.run', return_value=mock_result) as mock_run:
            orchestrator._log_journal_tail('meshtasticd', lines=5)

            mock_run.assert_called_once()
            args = mock_run.call_args[0][0]
            assert 'journalctl' in args
            assert '-n' in args
            assert '5' in args

    def test_handles_missing_journalctl(self, orchestrator):
        """No crash when journalctl not available."""
        with patch('subprocess.run', side_effect=FileNotFoundError):
            # Should not raise
            orchestrator._log_journal_tail('meshtasticd')

    def test_handles_timeout(self, orchestrator):
        """No crash when journalctl times out."""
        with patch('subprocess.run', side_effect=subprocess.TimeoutExpired(cmd='', timeout=5)):
            # Should not raise
            orchestrator._log_journal_tail('meshtasticd')

    def test_unknown_service(self, orchestrator):
        """No crash for unknown service name."""
        orchestrator._log_journal_tail('nonexistent')
