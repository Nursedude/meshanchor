"""
Tests for FailoverManager service watchdog and crash-based failover.

Covers:
- Service watchdog detects crashed meshtasticd and attempts restart
- Restart rate limiting (max restarts per hour)
- Restart cooldown between attempts
- Crash-based failover: primary unreachable → secondary promoted
- Crash recovery: primary comes back → recovery pending → primary active
- EventBus emission on state changes
- Persistent events via SharedHealthState
"""

import threading
import time
from datetime import datetime
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from gateway.radio_failover import (
    FailoverManager,
    FailoverConfig,
    FailoverState,
    RadioHealth,
)


@pytest.fixture
def watchdog_config():
    """Config with watchdog enabled and short intervals for testing."""
    return FailoverConfig(
        enabled=True,
        primary_port=4403,
        secondary_port=4404,
        primary_http_port=9443,
        secondary_http_port=9444,
        health_poll_interval=0.1,
        cooldown_after_failover=0,
        watchdog_enabled=True,
        restart_after_failures=3,
        max_restarts_per_hour=3,
        restart_cooldown=0,
        primary_service="meshtasticd",
        secondary_service="meshtasticd-alt",
    )


@pytest.fixture
def nowatchdog_config():
    """Config with watchdog disabled."""
    return FailoverConfig(
        enabled=True,
        watchdog_enabled=False,
        health_poll_interval=0.1,
        cooldown_after_failover=0,
    )


class TestServiceWatchdog:
    """Tests for the service watchdog feature."""

    @patch('gateway.radio_failover._HAS_SERVICE_CHECK', True)
    @patch('gateway.radio_failover.restart_service')
    def test_watchdog_restarts_crashed_primary(self, mock_restart, watchdog_config):
        """Watchdog should attempt restart when primary has 5+ consecutive failures."""
        mock_restart.return_value = (True, "Service started")
        fm = FailoverManager(config=watchdog_config)

        # Simulate primary failure
        fm._primary.consecutive_failures = 5
        fm._primary.reachable = False

        fm._run_watchdog()

        mock_restart.assert_called_once_with("meshtasticd", timeout=30)

    @patch('gateway.radio_failover._HAS_SERVICE_CHECK', True)
    @patch('gateway.radio_failover.restart_service')
    def test_watchdog_restarts_crashed_secondary(self, mock_restart, watchdog_config):
        """Watchdog should attempt restart when secondary has 5+ consecutive failures."""
        mock_restart.return_value = (True, "Service started")
        fm = FailoverManager(config=watchdog_config)

        fm._secondary.consecutive_failures = 5
        fm._secondary.reachable = False

        fm._run_watchdog()

        mock_restart.assert_called_once_with("meshtasticd-alt", timeout=30)

    @patch('gateway.radio_failover._HAS_SERVICE_CHECK', True)
    @patch('gateway.radio_failover.restart_service')
    def test_watchdog_skips_when_below_failure_threshold(self, mock_restart, watchdog_config):
        """Watchdog should not restart if failures below threshold."""
        fm = FailoverManager(config=watchdog_config)

        fm._primary.consecutive_failures = 2  # Below threshold of 3
        fm._primary.reachable = False

        fm._run_watchdog()

        mock_restart.assert_not_called()

    @patch('gateway.radio_failover._HAS_SERVICE_CHECK', True)
    @patch('gateway.radio_failover.restart_service')
    def test_watchdog_rate_limits_restarts(self, mock_restart, watchdog_config):
        """Watchdog should respect max_restarts_per_hour limit."""
        mock_restart.return_value = (True, "Service started")
        fm = FailoverManager(config=watchdog_config)

        # Fill up restart window
        now = time.time()
        fm._restart_timestamps['primary'] = [now - 100, now - 50, now - 10]

        fm._primary.consecutive_failures = 10
        fm._primary.reachable = False

        fm._run_watchdog()

        # Should NOT restart — already at max (3)
        mock_restart.assert_not_called()

    @patch('gateway.radio_failover._HAS_SERVICE_CHECK', True)
    @patch('gateway.radio_failover.restart_service')
    def test_watchdog_respects_cooldown(self, mock_restart):
        """Watchdog should wait restart_cooldown between attempts."""
        config = FailoverConfig(
            enabled=True,
            watchdog_enabled=True,
            restart_after_failures=3,
            restart_cooldown=60,
        )
        fm = FailoverManager(config=config)

        # Recent restart attempt
        fm._last_restart_attempt['primary'] = time.time() - 10  # 10s ago, cooldown is 60s

        fm._primary.consecutive_failures = 10
        fm._primary.reachable = False

        fm._run_watchdog()

        mock_restart.assert_not_called()

    def test_watchdog_disabled_skips_restart(self, nowatchdog_config):
        """Watchdog should do nothing when disabled."""
        fm = FailoverManager(config=nowatchdog_config)

        fm._primary.consecutive_failures = 100
        fm._primary.reachable = False

        # Should not raise or attempt restart
        fm._run_watchdog()

    @patch('gateway.radio_failover._HAS_SERVICE_CHECK', True)
    @patch('gateway.radio_failover.restart_service')
    def test_watchdog_handles_restart_failure(self, mock_restart, watchdog_config):
        """Watchdog should handle restart failure gracefully."""
        mock_restart.return_value = (False, "Permission denied")
        fm = FailoverManager(config=watchdog_config)

        fm._primary.consecutive_failures = 5
        fm._primary.reachable = False

        fm._run_watchdog()

        mock_restart.assert_called_once()
        # Should still record the attempt
        assert len(fm._restart_timestamps['primary']) == 1


class TestCrashBasedFailover:
    """Tests for crash-based failover and recovery."""

    def test_primary_unreachable_triggers_failover(self, watchdog_config):
        """Primary unreachable should trigger immediate failover to secondary."""
        fm = FailoverManager(config=watchdog_config)
        assert fm.state == FailoverState.PRIMARY_ACTIVE

        fm._primary.reachable = False
        fm._secondary.reachable = True

        fm._evaluate_state()

        assert fm.state == FailoverState.SECONDARY_ACTIVE

    def test_primary_recovery_triggers_recovery_pending(self, watchdog_config):
        """Primary coming back after crash should trigger recovery."""
        fm = FailoverManager(config=watchdog_config)

        # Simulate crash failover
        fm._primary.reachable = False
        fm._secondary.reachable = True
        fm._evaluate_state()
        assert fm.state == FailoverState.SECONDARY_ACTIVE

        # Track that primary was down
        fm._primary_was_down = True
        fm._primary_down_since = time.time() - 30

        # Primary comes back
        fm._primary.reachable = True
        fm._primary.channel_utilization = 5.0

        fm._evaluate_state()

        assert fm.state == FailoverState.RECOVERY_PENDING

    def test_recovery_pending_to_primary_active(self, watchdog_config):
        """Recovery should complete when primary is stable."""
        fm = FailoverManager(config=watchdog_config)

        # Set state to RECOVERY_PENDING
        fm._state = FailoverState.RECOVERY_PENDING
        fm._primary.reachable = True
        fm._primary.channel_utilization = 5.0  # Below overload

        fm._evaluate_state()

        assert fm.state == FailoverState.PRIMARY_ACTIVE

    def test_recovery_aborted_if_primary_unstable(self, watchdog_config):
        """Recovery should abort if primary becomes unstable again."""
        fm = FailoverManager(config=watchdog_config)

        fm._state = FailoverState.RECOVERY_PENDING
        fm._primary.reachable = True
        fm._primary.channel_utilization = 30.0  # Overloaded

        fm._evaluate_state()

        assert fm.state == FailoverState.SECONDARY_ACTIVE

    def test_both_radios_down_stays_on_primary(self, watchdog_config):
        """When both radios are down, stay on primary (best effort)."""
        fm = FailoverManager(config=watchdog_config)

        fm._primary.reachable = False
        fm._secondary.reachable = False

        fm._evaluate_state()

        # Should stay PRIMARY_ACTIVE (no good secondary to fail to)
        assert fm.state == FailoverState.PRIMARY_ACTIVE


class TestReachabilityTracking:
    """Tests for crash tracking via _track_reachability."""

    def test_tracks_primary_going_down(self, watchdog_config):
        """Should detect when primary goes from reachable to unreachable."""
        fm = FailoverManager(config=watchdog_config)

        fm._primary.reachable = False
        fm._track_reachability()

        assert fm._primary_was_down is True
        assert fm._primary_down_since is not None

    def test_tracks_primary_coming_back(self, watchdog_config):
        """Should detect when primary recovers from crash."""
        fm = FailoverManager(config=watchdog_config)

        # Mark primary as having been down
        fm._primary_was_down = True
        fm._primary_down_since = time.time() - 60

        # Primary comes back
        fm._primary.reachable = True
        fm._track_reachability()

        # _primary_was_down should NOT be cleared here (used by state machine)
        assert fm._primary_was_down is True

    def test_tracks_secondary_going_down(self, watchdog_config):
        """Should detect when secondary goes down."""
        fm = FailoverManager(config=watchdog_config)

        fm._secondary.reachable = False
        fm._track_reachability()

        assert fm._secondary_was_down is True
        assert fm._secondary_down_since is not None

    def test_tracks_secondary_recovering(self, watchdog_config):
        """Should detect secondary recovery and clear tracking."""
        fm = FailoverManager(config=watchdog_config)

        fm._secondary_was_down = True
        fm._secondary_down_since = time.time() - 30
        fm._secondary.reachable = True

        fm._track_reachability()

        assert fm._secondary_was_down is False
        assert fm._secondary_down_since is None


class TestEventBusEmission:
    """Tests for EventBus integration on state changes."""

    @patch('gateway.radio_failover._HAS_EVENT_BUS', True)
    @patch('gateway.radio_failover.emit_service_status')
    def test_transition_emits_event(self, mock_emit, watchdog_config):
        """State transitions should emit EventBus service status events."""
        fm = FailoverManager(config=watchdog_config)

        fm._transition(FailoverState.SECONDARY_ACTIVE, "Test failover")

        mock_emit.assert_called_once_with(
            "radio_failover",
            True,  # SECONDARY_ACTIVE is not DISABLED
            "primary_active -> secondary_active: Test failover",
        )

    @patch('gateway.radio_failover._HAS_EVENT_BUS', True)
    @patch('gateway.radio_failover.emit_service_status')
    def test_transition_handles_emit_error(self, mock_emit, watchdog_config):
        """EventBus errors should not crash the failover manager."""
        mock_emit.side_effect = RuntimeError("EventBus broken")
        fm = FailoverManager(config=watchdog_config)

        # Should not raise
        fm._transition(FailoverState.SECONDARY_ACTIVE, "Test failover")
        assert fm.state == FailoverState.SECONDARY_ACTIVE


class TestPersistentEvents:
    """Tests for SharedHealthState persistence."""

    def test_transition_persists_to_sqlite(self, watchdog_config):
        """State transitions should persist to SharedHealthState."""
        import gateway.radio_failover as rf_mod

        mock_state = MagicMock()
        mock_shs_func = MagicMock(return_value=mock_state)

        orig_has = rf_mod._HAS_SHARED_STATE
        orig_func = getattr(rf_mod, 'get_shared_health_state', None)
        rf_mod._HAS_SHARED_STATE = True
        rf_mod.get_shared_health_state = mock_shs_func

        try:
            fm = FailoverManager(config=watchdog_config)
            fm._transition(FailoverState.SECONDARY_ACTIVE, "Test persist")

            mock_state.update_service.assert_called_once_with(
                "radio_failover",
                state="secondary_active",
                reason="Test persist",
            )
        finally:
            rf_mod._HAS_SHARED_STATE = orig_has
            if orig_func is None and hasattr(rf_mod, 'get_shared_health_state'):
                delattr(rf_mod, 'get_shared_health_state')
            elif orig_func is not None:
                rf_mod.get_shared_health_state = orig_func

    def test_persist_handles_error(self, watchdog_config):
        """Persistence errors should not crash the failover manager."""
        import gateway.radio_failover as rf_mod

        mock_shs_func = MagicMock(side_effect=RuntimeError("DB locked"))
        orig_has = rf_mod._HAS_SHARED_STATE
        orig_func = getattr(rf_mod, 'get_shared_health_state', None)
        rf_mod._HAS_SHARED_STATE = True
        rf_mod.get_shared_health_state = mock_shs_func

        try:
            fm = FailoverManager(config=watchdog_config)
            # Should not raise
            fm._transition(FailoverState.SECONDARY_ACTIVE, "Test")
            assert fm.state == FailoverState.SECONDARY_ACTIVE
        finally:
            rf_mod._HAS_SHARED_STATE = orig_has
            if orig_func is None and hasattr(rf_mod, 'get_shared_health_state'):
                delattr(rf_mod, 'get_shared_health_state')
            elif orig_func is not None:
                rf_mod.get_shared_health_state = orig_func


class TestStatusReport:
    """Tests for get_status() with watchdog information."""

    def test_status_includes_watchdog_info(self, watchdog_config):
        """Status should include watchdog section."""
        fm = FailoverManager(config=watchdog_config)
        status = fm.get_status()

        assert 'watchdog' in status
        assert status['watchdog']['enabled'] is True
        assert status['watchdog']['primary_restarts_1h'] == 0
        assert status['watchdog']['secondary_restarts_1h'] == 0
        assert status['watchdog']['primary_down'] is False

    def test_status_shows_restart_counts(self, watchdog_config):
        """Status should reflect restart attempt counts."""
        fm = FailoverManager(config=watchdog_config)
        fm._restart_timestamps['primary'] = [time.time() - 100, time.time() - 50]

        status = fm.get_status()
        assert status['watchdog']['primary_restarts_1h'] == 2

    def test_active_http_port_property(self, watchdog_config):
        """active_http_port should return the correct HTTP port."""
        fm = FailoverManager(config=watchdog_config)

        assert fm.active_http_port == 9443  # Primary

        fm._state = FailoverState.SECONDARY_ACTIVE
        assert fm.active_http_port == 9444  # Secondary
