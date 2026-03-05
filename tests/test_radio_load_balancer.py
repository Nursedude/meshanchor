"""
Tests for dual-radio TX load balancer.

Tests the RadioLoadBalancer's weight calculation, port selection distribution,
state transitions, and edge cases without requiring actual hardware.
"""

import time
import threading
from collections import Counter
from unittest.mock import MagicMock, patch

import pytest

# Ensure src is on path
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))

from gateway.radio_failover import (
    RadioLoadBalancer,
    LoadBalancerConfig,
    LoadBalancerState,
    RadioHealth,
)


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def config():
    """Default load balancer config for testing."""
    return LoadBalancerConfig(
        enabled=True,
        primary_port=4403,
        secondary_port=4404,
        primary_http_port=9443,
        secondary_http_port=9444,
        tx_threshold=10.0,
        tx_max=20.0,
        health_poll_interval=0.1,
        weight_change_rate=100.0,  # Instant for testing
        min_primary_weight=10.0,
    )


@pytest.fixture
def lb(config):
    """RadioLoadBalancer with testing config (not started)."""
    balancer = RadioLoadBalancer(config)
    yield balancer
    balancer.stop()


@pytest.fixture
def config_gradual():
    """Config with slow weight changes for testing gradual adjustment."""
    return LoadBalancerConfig(
        enabled=True,
        tx_threshold=10.0,
        tx_max=20.0,
        weight_change_rate=10.0,  # Max 10% shift per cycle
        min_primary_weight=10.0,
    )


@pytest.fixture
def lb_gradual(config_gradual):
    """Load balancer with gradual weight changes."""
    balancer = RadioLoadBalancer(config_gradual)
    yield balancer
    balancer.stop()


# ── State Machine Tests ──────────────────────────────────────────────


class TestLoadBalancerStates:
    """Test state transitions."""

    def test_initial_state_idle_when_enabled(self, lb):
        assert lb.state == LoadBalancerState.IDLE

    def test_initial_state_disabled_when_not_enabled(self):
        config = LoadBalancerConfig(enabled=False)
        balancer = RadioLoadBalancer(config)
        assert balancer.state == LoadBalancerState.DISABLED

    def test_initial_weights_100_0(self, lb):
        assert lb.primary_weight == 100.0
        assert lb.secondary_weight == 0.0

    def test_idle_when_primary_tx_below_threshold(self, lb):
        """Primary TX below threshold → stay IDLE, 100/0 weights."""
        lb._primary.reachable = True
        lb._secondary.reachable = True
        lb._primary.tx_utilization = 5.0
        lb._secondary.tx_utilization = 0.0

        lb._recalculate_weights()

        assert lb.state == LoadBalancerState.IDLE
        assert lb.primary_weight == 100.0

    def test_balancing_when_primary_tx_above_threshold(self, lb):
        """Primary TX above threshold → BALANCING with split weights."""
        lb._primary.reachable = True
        lb._secondary.reachable = True
        lb._primary.tx_utilization = 15.0  # Between threshold(10) and max(20)
        lb._secondary.tx_utilization = 0.0

        lb._recalculate_weights()

        assert lb.state == LoadBalancerState.BALANCING
        assert lb.primary_weight < 100.0
        assert lb.primary_weight > 10.0  # min_primary_weight

    def test_saturated_when_both_radios_high(self, lb):
        """Both radios at tx_max → SATURATED."""
        lb._primary.reachable = True
        lb._secondary.reachable = True
        lb._primary.tx_utilization = 25.0
        lb._secondary.tx_utilization = 25.0

        lb._recalculate_weights()

        assert lb.state == LoadBalancerState.SATURATED

    def test_recovery_from_balancing_to_idle(self, lb):
        """Primary TX drops below threshold → back to IDLE."""
        lb._primary.reachable = True
        lb._secondary.reachable = True

        # First: go to BALANCING
        lb._primary.tx_utilization = 15.0
        lb._recalculate_weights()
        assert lb.state == LoadBalancerState.BALANCING

        # Then: primary TX drops
        lb._primary.tx_utilization = 5.0
        lb._recalculate_weights()
        assert lb.state == LoadBalancerState.IDLE
        assert lb.primary_weight == 100.0


# ── Weight Calculation Tests ─────────────────────────────────────────


class TestWeightCalculation:
    """Test the weight calculation logic."""

    def test_at_threshold_weights_near_100(self, lb):
        """At exactly tx_threshold, weights should be near 100/0."""
        lb._primary.reachable = True
        lb._secondary.reachable = True
        lb._primary.tx_utilization = 10.0  # Exactly at threshold
        lb._secondary.tx_utilization = 0.0

        lb._recalculate_weights()

        # At threshold boundary, ratio = 0, so target = 100
        assert lb.primary_weight == 100.0

    def test_at_tx_max_weights_at_minimum(self, lb):
        """At tx_max, primary weight should be at min_primary_weight."""
        lb._primary.reachable = True
        lb._secondary.reachable = True
        lb._primary.tx_utilization = 20.0  # At tx_max
        lb._secondary.tx_utilization = 5.0  # Below tx_max

        lb._recalculate_weights()

        assert lb.primary_weight == pytest.approx(10.0, abs=1.0)  # min_primary_weight

    def test_midpoint_weights_proportional(self, lb):
        """At midpoint between threshold and max, weights should be ~55/45."""
        lb._primary.reachable = True
        lb._secondary.reachable = True
        lb._primary.tx_utilization = 15.0  # Midpoint of 10-20 range
        lb._secondary.tx_utilization = 0.0

        lb._recalculate_weights()

        # ratio = 0.5, target = max(10, 100 - 0.5 * 90) = 55
        assert lb.primary_weight == pytest.approx(55.0, abs=2.0)

    def test_above_tx_max_clamps_to_minimum(self, lb):
        """TX above tx_max still clamps to min_primary_weight."""
        lb._primary.reachable = True
        lb._secondary.reachable = True
        lb._primary.tx_utilization = 30.0  # Way above tx_max
        lb._secondary.tx_utilization = 5.0  # Below tx_max

        lb._recalculate_weights()

        assert lb.primary_weight == pytest.approx(10.0, abs=1.0)

    def test_secondary_unreachable_100_primary(self, lb):
        """If secondary is unreachable, all traffic to primary."""
        lb._primary.reachable = True
        lb._secondary.reachable = False
        lb._primary.tx_utilization = 15.0

        lb._recalculate_weights()

        assert lb.primary_weight == 100.0
        assert lb.state == LoadBalancerState.IDLE

    def test_primary_unreachable_offloads_to_secondary(self, lb):
        """If primary is unreachable, most traffic to secondary."""
        lb._primary.reachable = False
        lb._secondary.reachable = True

        lb._recalculate_weights()

        assert lb.primary_weight == lb._config.min_primary_weight
        assert lb.state == LoadBalancerState.BALANCING


# ── Gradual Weight Adjustment Tests ──────────────────────────────────


class TestGradualAdjustment:
    """Test that weights change gradually, not instantly."""

    def test_weights_shift_by_rate_per_cycle(self, lb_gradual):
        """Weights should move by at most weight_change_rate per cycle."""
        lb_gradual._primary.reachable = True
        lb_gradual._secondary.reachable = True
        lb_gradual._primary.tx_utilization = 20.0  # Wants min weight (10)
        lb_gradual._secondary.tx_utilization = 0.0

        # Starting at 100, should drop by 10 per cycle (rate=10)
        lb_gradual._recalculate_weights()
        assert lb_gradual.primary_weight == pytest.approx(90.0, abs=1.0)

        lb_gradual._recalculate_weights()
        assert lb_gradual.primary_weight == pytest.approx(80.0, abs=1.0)

        lb_gradual._recalculate_weights()
        assert lb_gradual.primary_weight == pytest.approx(70.0, abs=1.0)

    def test_weights_recover_gradually(self, lb_gradual):
        """Weights should increase gradually when load drops."""
        lb_gradual._primary.reachable = True
        lb_gradual._secondary.reachable = True

        # Force weights low
        lb_gradual._set_weights(30.0, "test")

        # Now primary TX drops below threshold
        lb_gradual._primary.tx_utilization = 5.0
        lb_gradual._secondary.tx_utilization = 0.0

        # Should climb by 10 per cycle toward 100
        lb_gradual._recalculate_weights()
        assert lb_gradual.primary_weight == pytest.approx(40.0, abs=1.0)

        lb_gradual._recalculate_weights()
        assert lb_gradual.primary_weight == pytest.approx(50.0, abs=1.0)


# ── Port Selection Tests ─────────────────────────────────────────────


class TestPortSelection:
    """Test that get_tx_port() distributes according to weights."""

    def test_100_0_always_primary(self, lb):
        """At 100/0 weights, always returns primary port."""
        ports = [lb.get_tx_port() for _ in range(100)]
        assert all(p == 9443 for p in ports)

    def test_disabled_returns_primary(self):
        """Disabled balancer always returns primary port."""
        config = LoadBalancerConfig(enabled=False)
        balancer = RadioLoadBalancer(config)

        ports = [balancer.get_tx_port() for _ in range(100)]
        assert all(p == 9443 for p in ports)

    def test_50_50_distribution(self, lb):
        """At 50/50 weights, roughly equal distribution."""
        lb._set_weights(50.0, "test")

        counts = Counter(lb.get_tx_port() for _ in range(1000))

        # Allow 10% tolerance
        assert 400 < counts[9443] < 600
        assert 400 < counts[9444] < 600

    def test_70_30_distribution(self, lb):
        """At 70/30 weights, roughly 70% primary."""
        lb._set_weights(70.0, "test")

        counts = Counter(lb.get_tx_port() for _ in range(1000))

        assert 600 < counts[9443] < 800
        assert 200 < counts[9444] < 400

    def test_tx_counters_increment(self, lb):
        """TX counters should track sends per radio."""
        lb._set_weights(50.0, "test")

        for _ in range(100):
            lb.get_tx_port()

        total = lb._tx_count_primary + lb._tx_count_secondary
        assert total == 100


# ── Status Tests ─────────────────────────────────────────────────────


class TestGetStatus:
    """Test the status output for dashboard display."""

    def test_status_structure(self, lb):
        """Status should contain all expected keys."""
        status = lb.get_status()

        assert 'state' in status
        assert 'enabled' in status
        assert 'primary_weight' in status
        assert 'secondary_weight' in status
        assert 'primary' in status
        assert 'secondary' in status
        assert 'tx_counts' in status
        assert 'congested_nodes' in status
        assert 'thresholds' in status

    def test_status_weights_sum_to_100(self, lb):
        """Primary + secondary weights should always sum to 100."""
        lb._set_weights(63.0, "test")
        status = lb.get_status()

        total = status['primary_weight'] + status['secondary_weight']
        assert total == pytest.approx(100.0, abs=0.1)

    def test_status_with_congested_node_provider(self):
        """Congested node provider should be called in get_status()."""
        mock_nodes = [
            {'id': '!aabb', 'name': 'Node1', 'channel_util': 30.0, 'tx_airtime': 8.0},
        ]
        config = LoadBalancerConfig(enabled=True)
        lb = RadioLoadBalancer(config, congested_node_provider=lambda: mock_nodes)

        status = lb.get_status()
        assert len(status['congested_nodes']) == 1
        assert status['congested_nodes'][0]['name'] == 'Node1'

    def test_status_without_congested_node_provider(self, lb):
        """Without provider, congested_nodes should be empty list."""
        status = lb.get_status()
        assert status['congested_nodes'] == []


# ── Event History Tests ──────────────────────────────────────────────


class TestEventHistory:
    """Test state change event recording."""

    def test_state_change_records_event(self, lb):
        """State transitions should be recorded in events."""
        lb._primary.reachable = True
        lb._secondary.reachable = True
        lb._primary.tx_utilization = 15.0

        lb._recalculate_weights()

        assert len(lb._events) >= 1
        assert lb._events[-1].to_state == LoadBalancerState.BALANCING

    def test_callback_called_on_state_change(self):
        """on_state_change callback should fire on transitions."""
        callback = MagicMock()
        config = LoadBalancerConfig(enabled=True, weight_change_rate=100.0)
        lb = RadioLoadBalancer(config, on_state_change=callback)

        lb._primary.reachable = True
        lb._secondary.reachable = True
        lb._primary.tx_utilization = 15.0

        lb._recalculate_weights()

        callback.assert_called_once()
        args = callback.call_args[0]
        assert args[0] == LoadBalancerState.IDLE
        assert args[1] == LoadBalancerState.BALANCING


# ── Edge Case Tests ──────────────────────────────────────────────────


class TestEdgeCases:
    """Test edge cases and boundary conditions."""

    def test_both_unreachable_stays_idle(self, lb):
        """Both radios unreachable — stay at 100/0 (best effort)."""
        lb._primary.reachable = False
        lb._secondary.reachable = False

        lb._recalculate_weights()

        # Secondary unreachable check comes first → 100% primary
        assert lb.primary_weight == 100.0

    def test_saturated_holds_weights(self, lb):
        """SATURATED state should not change weights."""
        lb._primary.reachable = True
        lb._secondary.reachable = True

        # Set some non-default weights
        lb._set_weights(60.0, "test")

        lb._primary.tx_utilization = 25.0
        lb._secondary.tx_utilization = 25.0

        lb._recalculate_weights()

        assert lb.state == LoadBalancerState.SATURATED
        assert lb.primary_weight == 60.0  # Unchanged

    def test_zero_range_threshold_equals_max(self):
        """If tx_threshold == tx_max, should handle gracefully."""
        config = LoadBalancerConfig(
            enabled=True,
            tx_threshold=10.0,
            tx_max=10.0,  # Same as threshold
            weight_change_rate=100.0,
            min_primary_weight=10.0,
        )
        lb = RadioLoadBalancer(config)
        lb._primary.reachable = True
        lb._secondary.reachable = True
        lb._primary.tx_utilization = 15.0
        lb._secondary.tx_utilization = 0.0

        # Should not crash — ratio clamps to 1.0
        lb._recalculate_weights()

        assert lb.primary_weight == pytest.approx(10.0, abs=1.0)

    def test_start_without_http_module(self):
        """Start should gracefully disable if HTTP module unavailable."""
        config = LoadBalancerConfig(enabled=True)
        lb = RadioLoadBalancer(config)

        with patch('gateway.radio_failover._HAS_HTTP', False):
            lb.start()

        assert lb.state == LoadBalancerState.DISABLED

    def test_stop_idempotent(self, lb):
        """Calling stop multiple times should not error."""
        lb.stop()
        lb.stop()  # Second call should be fine
