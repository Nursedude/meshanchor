"""
Tests for RadioLoadBalancer + FailoverManager coordination.

Covers:
- Load balancer defers to failover state when failover_manager provided
- LB routes 100% to secondary when failover is SECONDARY_ACTIVE
- LB does not interfere during RECOVERY_PENDING
- LB operates independently when failover_manager is None
- get_tx_port respects failover state when LB is disabled
- Reachability-based gradual recovery after radio comes back
"""

import time
from unittest.mock import MagicMock, patch

import pytest

from gateway.radio_failover import (
    FailoverManager,
    FailoverConfig,
    FailoverState,
    RadioLoadBalancer,
    LoadBalancerConfig,
    LoadBalancerState,
    RadioHealth,
)


@pytest.fixture
def fo_config():
    """Failover config for testing."""
    return FailoverConfig(
        enabled=True,
        cooldown_after_failover=0,
        health_poll_interval=0.1,
    )


@pytest.fixture
def lb_config():
    """Load balancer config for testing."""
    return LoadBalancerConfig(
        enabled=True,
        tx_threshold=10.0,
        tx_max=20.0,
        health_poll_interval=0.1,
    )


class TestLBFailoverCoordination:
    """Tests for load balancer deferring to failover state."""

    def test_lb_routes_to_secondary_when_failover_active(self, lb_config):
        """When failover is SECONDARY_ACTIVE, LB should route 100% to secondary."""
        fm = MagicMock(spec=FailoverManager)
        fm.state = FailoverState.SECONDARY_ACTIVE
        fm.active_http_port = 9444

        lb = RadioLoadBalancer(config=lb_config, failover_manager=fm)
        lb._primary.reachable = True
        lb._secondary.reachable = True
        lb._primary.tx_utilization = 5.0
        lb._secondary.tx_utilization = 5.0

        lb._recalculate_weights()

        assert lb.primary_weight == 0.0
        assert lb.state == LoadBalancerState.BALANCING

    def test_lb_does_not_interfere_during_recovery(self, lb_config):
        """When failover is RECOVERY_PENDING, LB should not change weights."""
        fm = MagicMock(spec=FailoverManager)
        fm.state = FailoverState.RECOVERY_PENDING

        lb = RadioLoadBalancer(config=lb_config, failover_manager=fm)
        lb._primary.reachable = True
        lb._secondary.reachable = True

        # Set initial weights
        lb._primary_weight = 30.0
        initial_weight = lb.primary_weight

        lb._recalculate_weights()

        # Weights should not change
        assert lb.primary_weight == initial_weight

    def test_lb_operates_normally_when_failover_primary_active(self, lb_config):
        """When failover is PRIMARY_ACTIVE, LB should use normal weight calc."""
        fm = MagicMock(spec=FailoverManager)
        fm.state = FailoverState.PRIMARY_ACTIVE

        lb = RadioLoadBalancer(config=lb_config, failover_manager=fm)
        lb._primary.reachable = True
        lb._secondary.reachable = True
        lb._primary.tx_utilization = 15.0  # Above threshold
        lb._secondary.tx_utilization = 5.0

        lb._recalculate_weights()

        # Should be in BALANCING with weights split
        assert lb.state == LoadBalancerState.BALANCING
        assert lb.primary_weight < 100.0

    def test_lb_operates_normally_without_failover_manager(self, lb_config):
        """Without failover_manager, LB should use normal weight calc."""
        lb = RadioLoadBalancer(config=lb_config, failover_manager=None)
        lb._primary.reachable = True
        lb._secondary.reachable = True
        lb._primary.tx_utilization = 15.0
        lb._secondary.tx_utilization = 5.0

        lb._recalculate_weights()

        assert lb.state == LoadBalancerState.BALANCING

    def test_get_tx_port_respects_failover_when_lb_disabled(self, lb_config):
        """get_tx_port should use failover's active_http_port when LB is disabled."""
        fm = MagicMock(spec=FailoverManager)
        fm.active_http_port = 9444  # Failover on secondary

        lb_config.enabled = True
        lb = RadioLoadBalancer(config=lb_config, failover_manager=fm)
        lb._state = LoadBalancerState.DISABLED

        port = lb.get_tx_port()
        assert port == 9444

    def test_get_tx_port_returns_primary_when_no_failover_and_disabled(self, lb_config):
        """get_tx_port should return primary when no failover and LB disabled."""
        lb = RadioLoadBalancer(config=lb_config, failover_manager=None)
        lb._state = LoadBalancerState.DISABLED

        port = lb.get_tx_port()
        assert port == lb_config.primary_http_port


class TestLBReachabilityRecovery:
    """Tests for gradual recovery when radio comes back online."""

    def test_primary_unreachable_routes_to_secondary(self, lb_config):
        """When primary unreachable, all traffic should go to secondary."""
        lb = RadioLoadBalancer(config=lb_config)
        lb._primary.reachable = False
        lb._secondary.reachable = True

        lb._recalculate_weights()

        assert lb.primary_weight == lb_config.min_primary_weight

    def test_secondary_unreachable_routes_to_primary(self, lb_config):
        """When secondary unreachable, all traffic should go to primary."""
        lb = RadioLoadBalancer(config=lb_config)
        lb._primary.reachable = True
        lb._secondary.reachable = False

        lb._recalculate_weights()

        assert lb.primary_weight == 100.0
        assert lb.state == LoadBalancerState.IDLE

    def test_tracks_primary_unreachable_state(self, lb_config):
        """Should track when primary was unreachable for gradual recovery."""
        lb = RadioLoadBalancer(config=lb_config)

        assert lb._primary_was_unreachable is False

        lb._primary.reachable = False
        lb._secondary.reachable = True
        lb._recalculate_weights()

        assert lb._primary_was_unreachable is True

    def test_clears_unreachable_flag_on_recovery(self, lb_config):
        """Should clear unreachable flag when primary recovers."""
        lb = RadioLoadBalancer(config=lb_config)
        lb._primary_was_unreachable = True

        lb._primary.reachable = True
        lb._secondary.reachable = True
        lb._primary.tx_utilization = 5.0
        lb._secondary.tx_utilization = 5.0

        lb._recalculate_weights()

        assert lb._primary_was_unreachable is False


class TestLBStatusWithFailover:
    """Tests for get_status() with failover awareness."""

    def test_status_includes_failover_info(self, lb_config):
        """Status should show failover awareness and state."""
        fm = MagicMock(spec=FailoverManager)
        fm.state = FailoverState.PRIMARY_ACTIVE

        lb = RadioLoadBalancer(config=lb_config, failover_manager=fm)
        status = lb.get_status()

        assert status['failover_aware'] is True
        assert status['failover_state'] == 'primary_active'

    def test_status_without_failover(self, lb_config):
        """Status should show no failover awareness when none configured."""
        lb = RadioLoadBalancer(config=lb_config, failover_manager=None)
        status = lb.get_status()

        assert status['failover_aware'] is False
        assert status['failover_state'] is None


class TestLBFailoverIntegration:
    """Integration tests with real FailoverManager instances."""

    def test_lb_and_failover_coordinate_on_primary_crash(self, fo_config, lb_config):
        """When failover switches to secondary, LB should follow."""
        fm = FailoverManager(config=fo_config)
        lb = RadioLoadBalancer(config=lb_config, failover_manager=fm)

        # Simulate primary crash
        fm._primary.reachable = False
        fm._secondary.reachable = True
        fm._evaluate_state()
        assert fm.state == FailoverState.SECONDARY_ACTIVE

        # LB should now route to secondary
        lb._primary.reachable = True
        lb._secondary.reachable = True
        lb._recalculate_weights()

        assert lb.primary_weight == 0.0

    def test_lb_returns_to_normal_after_recovery(self, fo_config, lb_config):
        """After failover recovery, LB should return to normal operation."""
        fm = FailoverManager(config=fo_config)
        lb = RadioLoadBalancer(config=lb_config, failover_manager=fm)

        # Failover is on primary (normal)
        fm._primary.reachable = True
        fm._secondary.reachable = True
        assert fm.state == FailoverState.PRIMARY_ACTIVE

        # LB with low utilization
        lb._primary.reachable = True
        lb._secondary.reachable = True
        lb._primary.tx_utilization = 5.0
        lb._secondary.tx_utilization = 2.0

        lb._recalculate_weights()

        assert lb.state == LoadBalancerState.IDLE
        assert lb.primary_weight == 100.0
