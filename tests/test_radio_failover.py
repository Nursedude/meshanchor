"""
Tests for dual-radio failover state machine.

Tests the FailoverManager's state transitions, health polling,
and edge cases without requiring actual hardware.
"""

import time
import threading
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

# Ensure src is on path
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))

from gateway.radio_failover import (
    FailoverManager,
    FailoverConfig,
    FailoverState,
    RadioHealth,
    FailoverEvent,
)


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def config():
    """Default failover config for testing."""
    return FailoverConfig(
        enabled=True,
        primary_port=4403,
        secondary_port=4404,
        utilization_threshold=25.0,
        utilization_duration=2,   # Short for testing
        recovery_threshold=15.0,
        recovery_duration=2,      # Short for testing
        health_poll_interval=0.1,
        cooldown_after_failover=0,  # No cooldown for testing
        max_failovers_per_hour=100,
    )


@pytest.fixture
def manager(config):
    """FailoverManager with testing config (not started)."""
    mgr = FailoverManager(config)
    yield mgr
    mgr.stop()


# ── State Machine Tests ──────────────────────────────────────────────


class TestFailoverStateTransitions:
    """Test the core state machine logic."""

    def test_initial_state_enabled(self, manager):
        """Enabled manager starts in PRIMARY_ACTIVE."""
        assert manager.state == FailoverState.PRIMARY_ACTIVE

    def test_initial_state_disabled(self):
        """Disabled manager starts in DISABLED."""
        config = FailoverConfig(enabled=False)
        mgr = FailoverManager(config)
        assert mgr.state == FailoverState.DISABLED

    def test_active_port_primary(self, manager):
        """Active port defaults to primary."""
        assert manager.active_port == 4403

    def test_transition_to_failover_pending(self, manager):
        """Primary overload triggers FAILOVER_PENDING after duration."""
        # Simulate primary overloaded
        manager._primary.reachable = True
        manager._primary.channel_utilization = 30.0
        manager._secondary.reachable = True
        manager._secondary.channel_utilization = 5.0

        # First evaluation starts the timer
        manager._evaluate_state()
        assert manager.state == FailoverState.PRIMARY_ACTIVE

        # Wait for duration
        time.sleep(0.1)
        manager._overload_start = time.time() - 3  # Simulate past threshold

        manager._evaluate_state()
        assert manager.state == FailoverState.FAILOVER_PENDING

    def test_failover_to_secondary(self, manager):
        """FAILOVER_PENDING transitions to SECONDARY_ACTIVE when secondary healthy."""
        manager._state = FailoverState.FAILOVER_PENDING
        manager._primary.reachable = True
        manager._primary.channel_utilization = 30.0
        manager._secondary.reachable = True
        manager._secondary.channel_utilization = 5.0

        manager._evaluate_state()
        assert manager.state == FailoverState.SECONDARY_ACTIVE
        assert manager.active_port == 4404

    def test_failover_aborted_secondary_unreachable(self, manager):
        """FAILOVER_PENDING reverts to PRIMARY if secondary unreachable."""
        manager._state = FailoverState.FAILOVER_PENDING
        manager._secondary.reachable = False

        manager._evaluate_state()
        assert manager.state == FailoverState.PRIMARY_ACTIVE

    def test_failover_aborted_secondary_overloaded(self, manager):
        """FAILOVER_PENDING reverts if secondary also overloaded."""
        manager._state = FailoverState.FAILOVER_PENDING
        manager._secondary.reachable = True
        manager._secondary.channel_utilization = 30.0

        manager._evaluate_state()
        assert manager.state == FailoverState.PRIMARY_ACTIVE

    def test_recovery_from_secondary(self, manager):
        """SECONDARY_ACTIVE transitions to RECOVERY_PENDING when primary recovers."""
        manager._state = FailoverState.SECONDARY_ACTIVE
        manager._primary.reachable = True
        manager._primary.channel_utilization = 10.0

        # First eval starts recovery timer
        manager._evaluate_state()
        assert manager.state == FailoverState.SECONDARY_ACTIVE

        # Simulate time passing
        manager._recovery_start = time.time() - 3

        manager._evaluate_state()
        assert manager.state == FailoverState.RECOVERY_PENDING

    def test_recovery_to_primary(self, manager):
        """RECOVERY_PENDING transitions back to PRIMARY_ACTIVE."""
        manager._state = FailoverState.RECOVERY_PENDING
        manager._primary.reachable = True
        manager._primary.channel_utilization = 10.0

        manager._evaluate_state()
        assert manager.state == FailoverState.PRIMARY_ACTIVE
        assert manager.active_port == 4403

    def test_recovery_aborted_if_primary_overloaded(self, manager):
        """RECOVERY_PENDING goes back to SECONDARY if primary spikes."""
        manager._state = FailoverState.RECOVERY_PENDING
        manager._primary.reachable = True
        manager._primary.channel_utilization = 30.0

        manager._evaluate_state()
        assert manager.state == FailoverState.SECONDARY_ACTIVE

    def test_primary_unreachable_immediate_failover(self, manager):
        """Primary unreachable triggers immediate failover."""
        manager._primary.reachable = False
        manager._secondary.reachable = True
        manager._secondary.channel_utilization = 5.0

        manager._evaluate_state()
        assert manager.state == FailoverState.SECONDARY_ACTIVE

    def test_disabled_state_no_transitions(self, manager):
        """DISABLED state never transitions."""
        manager._state = FailoverState.DISABLED
        manager._primary.reachable = False

        manager._evaluate_state()
        assert manager.state == FailoverState.DISABLED


class TestFailoverEvents:
    """Test event recording."""

    def test_events_recorded(self, manager):
        """State transitions record events."""
        manager._state = FailoverState.FAILOVER_PENDING
        manager._secondary.reachable = True
        manager._secondary.channel_utilization = 5.0

        manager._evaluate_state()

        assert len(manager.events) == 1
        event = manager.events[0]
        assert event.from_state == FailoverState.FAILOVER_PENDING
        assert event.to_state == FailoverState.SECONDARY_ACTIVE

    def test_event_history_capped(self, manager):
        """Event history doesn't grow unbounded."""
        for i in range(150):
            manager._transition(
                FailoverState.SECONDARY_ACTIVE if i % 2 else FailoverState.PRIMARY_ACTIVE,
                f"test event {i}"
            )
        assert len(manager._events) <= 100

    def test_callback_invoked(self):
        """on_state_change callback fires on transition."""
        callback = MagicMock()
        config = FailoverConfig(enabled=True, cooldown_after_failover=0)
        mgr = FailoverManager(config, on_state_change=callback)

        mgr._state = FailoverState.FAILOVER_PENDING
        mgr._secondary.reachable = True
        mgr._secondary.channel_utilization = 5.0

        mgr._evaluate_state()

        callback.assert_called_once()
        args = callback.call_args[0]
        assert args[0] == FailoverState.FAILOVER_PENDING
        assert args[1] == FailoverState.SECONDARY_ACTIVE


class TestFailoverStatus:
    """Test status reporting."""

    def test_get_status(self, manager):
        """get_status returns comprehensive dict."""
        manager._primary.reachable = True
        manager._primary.channel_utilization = 15.5
        manager._secondary.reachable = True
        manager._secondary.channel_utilization = 3.2

        status = manager.get_status()

        assert status['state'] == 'primary_active'
        assert status['active_port'] == 4403
        assert status['enabled'] is True
        assert status['primary']['channel_utilization'] == 15.5
        assert status['secondary']['channel_utilization'] == 3.2

    def test_get_status_disabled(self):
        """Disabled manager reports disabled state."""
        config = FailoverConfig(enabled=False)
        mgr = FailoverManager(config)

        status = mgr.get_status()
        assert status['state'] == 'disabled'
        assert status['enabled'] is False


class TestRadioHealth:
    """Test RadioHealth dataclass properties."""

    def test_is_overloaded(self):
        """Overloaded when channel utilization >= 25%."""
        health = RadioHealth(channel_utilization=25.0)
        assert health.is_overloaded is True

        health.channel_utilization = 24.9
        assert health.is_overloaded is False

    def test_is_healthy(self):
        """Healthy when reachable and below recovery threshold."""
        health = RadioHealth(reachable=True, channel_utilization=10.0)
        assert health.is_healthy is True

        health.reachable = False
        assert health.is_healthy is False

        health.reachable = True
        health.channel_utilization = 20.0
        assert health.is_healthy is False


class TestFailoverRateLimiting:
    """Test failover rate limiting to prevent flapping."""

    def test_rate_limit_blocks_failover(self):
        """Max failovers per hour prevents excessive switching."""
        config = FailoverConfig(
            enabled=True,
            max_failovers_per_hour=2,
            cooldown_after_failover=0,
        )
        mgr = FailoverManager(config)

        # Fill up the rate limit window
        mgr._failover_count_window = [time.time(), time.time()]

        mgr._state = FailoverState.FAILOVER_PENDING
        mgr._secondary.reachable = True
        mgr._secondary.channel_utilization = 5.0

        mgr._evaluate_state()
        # Should revert to PRIMARY_ACTIVE instead of going to SECONDARY
        assert mgr.state == FailoverState.PRIMARY_ACTIVE

    def test_cooldown_prevents_rapid_transitions(self):
        """Cooldown period prevents rapid state changes."""
        config = FailoverConfig(
            enabled=True,
            cooldown_after_failover=60,
        )
        mgr = FailoverManager(config)
        mgr._last_state_change = time.time()  # Just changed

        mgr._primary.reachable = False
        mgr._secondary.reachable = True

        mgr._evaluate_state()
        # Should NOT transition due to cooldown
        assert mgr.state == FailoverState.PRIMARY_ACTIVE
