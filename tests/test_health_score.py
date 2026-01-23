"""
Tests for network health scoring system.

Tests cover:
- Score-to-status conversion
- Connectivity scoring (services, node count)
- Performance scoring (SNR, RSSI, channel util)
- Reliability scoring (delivery rate, error frequency)
- Freshness scoring (node staleness)
- Overall composite score
- Individual node health
- Trend detection
- History tracking
- Edge cases and boundary conditions
- Format display output
"""

import sys
import os
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import pytest

from utils.health_score import (
    HealthScorer,
    HealthSnapshot,
    ServiceStatus,
    NodeMetrics,
    MessageStats,
    score_to_status,
    clamp,
    format_health_display,
    THRESHOLD_HEALTHY,
    THRESHOLD_FAIR,
    THRESHOLD_DEGRADED,
    FRESH_THRESHOLD,
    STALE_THRESHOLD,
    DEAD_THRESHOLD,
    SNR_EXCELLENT,
    SNR_GOOD,
    SNR_FAIR,
    RSSI_EXCELLENT,
    RSSI_GOOD,
    RSSI_FAIR,
)


@pytest.fixture
def scorer():
    """Create a fresh health scorer."""
    return HealthScorer()


@pytest.fixture
def healthy_scorer():
    """Create a scorer with healthy signals."""
    s = HealthScorer()
    s.report_service_status('meshtasticd', running=True)
    s.report_service_status('rnsd', running=True)
    s.report_node_metrics('!abc', snr=-3.0, rssi=-75)
    s.report_node_metrics('!def', snr=-4.0, rssi=-80)
    s.report_node_metrics('!ghi', snr=-2.0, rssi=-70)
    s.report_message_stats(sent=100, delivered=98, failed=2)
    return s


# =============================================================================
# Utility Functions
# =============================================================================

class TestScoreToStatus:
    def test_healthy(self):
        assert score_to_status(80.0) == 'healthy'
        assert score_to_status(100.0) == 'healthy'
        assert score_to_status(75.0) == 'healthy'

    def test_fair(self):
        assert score_to_status(60.0) == 'fair'
        assert score_to_status(50.0) == 'fair'
        assert score_to_status(74.9) == 'fair'

    def test_degraded(self):
        assert score_to_status(40.0) == 'degraded'
        assert score_to_status(25.0) == 'degraded'
        assert score_to_status(49.9) == 'degraded'

    def test_critical(self):
        assert score_to_status(10.0) == 'critical'
        assert score_to_status(0.0) == 'critical'
        assert score_to_status(24.9) == 'critical'


class TestClamp:
    def test_within_range(self):
        assert clamp(50.0) == 50.0

    def test_below_min(self):
        assert clamp(-10.0) == 0.0

    def test_above_max(self):
        assert clamp(110.0) == 100.0

    def test_at_boundaries(self):
        assert clamp(0.0) == 0.0
        assert clamp(100.0) == 100.0


# =============================================================================
# Message Stats
# =============================================================================

class TestMessageStats:
    def test_success_rate_all_delivered(self):
        stats = MessageStats(sent=100, delivered=100, failed=0)
        assert stats.success_rate == 1.0

    def test_success_rate_half(self):
        stats = MessageStats(sent=100, delivered=50, failed=50)
        assert stats.success_rate == 0.5

    def test_success_rate_no_messages(self):
        """No messages sent = 100% success (no failures)."""
        stats = MessageStats(sent=0, delivered=0, failed=0)
        assert stats.success_rate == 1.0

    def test_success_rate_all_failed(self):
        stats = MessageStats(sent=100, delivered=0, failed=100)
        assert stats.success_rate == 0.0


# =============================================================================
# Connectivity Scoring
# =============================================================================

class TestConnectivity:
    def test_all_services_up(self, scorer):
        """All critical services running = high score."""
        scorer.report_service_status('meshtasticd', running=True)
        scorer.report_service_status('rnsd', running=True)
        snapshot = scorer.get_snapshot()
        assert snapshot.connectivity_score >= 50

    def test_critical_service_down(self, scorer):
        """Critical service down = low connectivity."""
        scorer.report_service_status('meshtasticd', running=False, critical=True)
        snapshot = scorer.get_snapshot()
        assert snapshot.connectivity_score < 50

    def test_optional_service_down_less_impact(self, scorer):
        """Optional service down has less impact than critical."""
        scorer.report_service_status('meshtasticd', running=True, critical=True)
        scorer.report_service_status('mqtt', running=False, critical=False)
        snapshot = scorer.get_snapshot()
        # Score should be above neutral (50) since critical service is up
        assert snapshot.connectivity_score > 50

    def test_no_services_neutral(self, scorer):
        """No services reported = neutral score."""
        snapshot = scorer.get_snapshot()
        assert snapshot.connectivity_score == 50.0

    def test_nodes_boost_score(self, scorer):
        """Visible nodes boost connectivity."""
        scorer.report_service_status('meshtasticd', running=True)
        scorer.report_node_metrics('!abc', snr=-5.0)
        scorer.report_node_metrics('!def', snr=-5.0)
        scorer.report_node_metrics('!ghi', snr=-5.0)
        snapshot = scorer.get_snapshot()
        assert snapshot.connectivity_score > 80


# =============================================================================
# Performance Scoring
# =============================================================================

class TestPerformance:
    def test_excellent_signals(self, scorer):
        """Excellent SNR/RSSI = high performance."""
        scorer.report_node_metrics('!abc', snr=-2.0, rssi=-70)
        snapshot = scorer.get_snapshot()
        assert snapshot.performance_score >= 90

    def test_poor_signals(self, scorer):
        """Very poor signals = low performance."""
        scorer.report_node_metrics('!abc', snr=-20.0, rssi=-125)
        snapshot = scorer.get_snapshot()
        assert snapshot.performance_score < 40

    def test_mixed_signals(self, scorer):
        """Mixed signal quality = moderate score."""
        scorer.report_node_metrics('!good', snr=-3.0, rssi=-75)
        scorer.report_node_metrics('!bad', snr=-18.0, rssi=-120)
        snapshot = scorer.get_snapshot()
        assert 30 < snapshot.performance_score < 80

    def test_no_metrics_neutral(self, scorer):
        """No metrics = neutral performance."""
        snapshot = scorer.get_snapshot()
        assert snapshot.performance_score == 50.0

    def test_high_channel_util_penalty(self, scorer):
        """High channel utilization reduces score."""
        scorer.report_node_metrics('!abc', snr=-3.0, channel_util=80.0)
        snapshot = scorer.get_snapshot()
        # Good SNR but high util — should be penalized
        assert snapshot.performance_score < 90

    def test_snr_score_boundaries(self, scorer):
        """SNR score respects threshold boundaries."""
        # Excellent (report both SNR and RSSI for clean average)
        scorer.report_node_metrics('!a', snr=0.0, rssi=-70)
        snap = scorer.get_snapshot()
        assert snap.performance_score >= 90
        scorer.reset()

        # Fair
        scorer.report_node_metrics('!b', snr=-12.0, rssi=-105)
        snap = scorer.get_snapshot()
        assert 40 < snap.performance_score < 75


# =============================================================================
# Reliability Scoring
# =============================================================================

class TestReliability:
    def test_perfect_delivery(self, scorer):
        """100% delivery rate = high reliability."""
        scorer.report_message_stats(sent=100, delivered=100, failed=0)
        snapshot = scorer.get_snapshot()
        assert snapshot.reliability_score >= 95

    def test_poor_delivery(self, scorer):
        """50% delivery rate = low reliability."""
        scorer.report_message_stats(sent=100, delivered=50, failed=50)
        snapshot = scorer.get_snapshot()
        assert snapshot.reliability_score < 60

    def test_no_messages_full_score(self, scorer):
        """No messages = no failures = full score."""
        snapshot = scorer.get_snapshot()
        assert snapshot.reliability_score >= 95

    def test_errors_reduce_score(self, scorer):
        """Recent errors reduce reliability."""
        scorer.report_message_stats(sent=50, delivered=50, failed=0)
        # Report 5 errors
        for _ in range(5):
            scorer.report_error()
        snapshot = scorer.get_snapshot()
        assert snapshot.reliability_score < 90

    def test_many_errors_significant_penalty(self, scorer):
        """Many recent errors cause large penalty."""
        scorer.report_message_stats(sent=50, delivered=50, failed=0)
        for _ in range(10):
            scorer.report_error()
        snapshot = scorer.get_snapshot()
        assert snapshot.reliability_score < 60


# =============================================================================
# Freshness Scoring
# =============================================================================

class TestFreshness:
    def test_fresh_nodes(self, scorer):
        """Recently seen nodes = high freshness."""
        now = time.time()
        scorer.report_node_metrics('!abc', snr=-5.0, last_seen=now)
        scorer.report_node_metrics('!def', snr=-5.0, last_seen=now - 60)
        snapshot = scorer.get_snapshot()
        assert snapshot.freshness_score >= 80

    def test_stale_nodes(self, scorer):
        """Old nodes = low freshness."""
        now = time.time()
        scorer.report_node_metrics('!abc', snr=-5.0,
                                  last_seen=now - STALE_THRESHOLD)
        snapshot = scorer.get_snapshot()
        assert snapshot.freshness_score < 40

    def test_dead_nodes(self, scorer):
        """Very old nodes = near-zero freshness."""
        now = time.time()
        scorer.report_node_metrics('!abc', snr=-5.0,
                                  last_seen=now - DEAD_THRESHOLD * 2)
        snapshot = scorer.get_snapshot()
        assert snapshot.freshness_score < 10

    def test_no_nodes_neutral(self, scorer):
        """No nodes = neutral freshness."""
        snapshot = scorer.get_snapshot()
        assert snapshot.freshness_score == 50.0

    def test_mixed_freshness(self, scorer):
        """Mix of fresh and stale nodes."""
        now = time.time()
        scorer.report_node_metrics('!fresh', snr=-5.0, last_seen=now)
        scorer.report_node_metrics('!stale', snr=-5.0,
                                  last_seen=now - STALE_THRESHOLD)
        snapshot = scorer.get_snapshot()
        assert 30 < snapshot.freshness_score < 70


# =============================================================================
# Overall Score
# =============================================================================

class TestOverallScore:
    def test_healthy_system(self, healthy_scorer):
        """Healthy system gets high overall score."""
        snapshot = healthy_scorer.get_snapshot()
        assert snapshot.overall_score >= 70
        assert snapshot.status in ('healthy', 'fair')

    def test_unhealthy_system(self, scorer):
        """Unhealthy system gets low score."""
        scorer.report_service_status('meshtasticd', running=False, critical=True)
        scorer.report_node_metrics('!abc', snr=-22.0, rssi=-130,
                                  last_seen=time.time() - DEAD_THRESHOLD)
        scorer.report_message_stats(sent=100, delivered=20, failed=80)
        for _ in range(10):
            scorer.report_error()
        snapshot = scorer.get_snapshot()
        assert snapshot.overall_score < 40
        assert snapshot.status in ('degraded', 'critical')

    def test_score_bounded(self, scorer):
        """Score always in [0, 100] range."""
        # Edge case: all zeros
        snapshot = scorer.get_snapshot()
        assert 0 <= snapshot.overall_score <= 100

    def test_weights_sum_to_one(self, scorer):
        """Default weights sum to 1.0."""
        total = sum(scorer.weights.values())
        assert abs(total - 1.0) < 0.001

    def test_custom_weights(self):
        """Custom weights are used."""
        s = HealthScorer(weight_connectivity=0.5, weight_performance=0.5,
                        weight_reliability=0.0, weight_freshness=0.0)
        s.report_service_status('meshtasticd', running=True)
        s.report_node_metrics('!abc', snr=-3.0)
        snapshot = s.get_snapshot()
        # Score should reflect only connectivity and performance
        assert snapshot.overall_score > 0

    def test_snapshot_to_dict(self, healthy_scorer):
        """Snapshot to_dict has correct structure."""
        snapshot = healthy_scorer.get_snapshot()
        d = snapshot.to_dict()
        assert 'overall_score' in d
        assert 'status' in d
        assert 'categories' in d
        assert 'connectivity' in d['categories']
        assert 'performance' in d['categories']

    def test_snapshot_timestamp(self, scorer):
        """Snapshot has a timestamp."""
        now = time.time()
        snapshot = scorer.get_snapshot()
        assert abs(snapshot.timestamp - now) < 1.0


# =============================================================================
# Node Health
# =============================================================================

class TestNodeHealth:
    def test_healthy_node(self, scorer):
        """Good signals, fresh node = high health."""
        scorer.report_node_metrics('!abc', snr=-3.0, rssi=-75,
                                  battery_level=90.0)
        health = scorer.get_node_health('!abc')
        assert health is not None
        assert health >= 80

    def test_weak_signal_node(self, scorer):
        """Weak signals = lower health."""
        scorer.report_node_metrics('!abc', snr=-20.0, rssi=-125)
        health = scorer.get_node_health('!abc')
        assert health is not None
        assert health < 65

    def test_unknown_node(self, scorer):
        """Unknown node returns None."""
        health = scorer.get_node_health('!unknown')
        assert health is None

    def test_stale_node(self, scorer):
        """Stale node has lower health than fresh node."""
        now = time.time()
        scorer.report_node_metrics('!stale', snr=-3.0,
                                  last_seen=now - STALE_THRESHOLD)
        scorer.report_node_metrics('!fresh', snr=-3.0,
                                  last_seen=now)
        stale_health = scorer.get_node_health('!stale')
        fresh_health = scorer.get_node_health('!fresh')
        assert stale_health is not None
        assert fresh_health is not None
        assert stale_health < fresh_health

    def test_low_battery(self, scorer):
        """Low battery reduces node health."""
        scorer.report_node_metrics('!abc', snr=-3.0, rssi=-75,
                                  battery_level=10.0)
        scorer.report_node_metrics('!def', snr=-3.0, rssi=-75,
                                  battery_level=90.0)
        health_low = scorer.get_node_health('!abc')
        health_high = scorer.get_node_health('!def')
        assert health_low < health_high

    def test_node_health_bounded(self, scorer):
        """Node health always in [0, 100]."""
        scorer.report_node_metrics('!abc', snr=-30.0, rssi=-140,
                                  battery_level=0.0,
                                  last_seen=time.time() - 100000)
        health = scorer.get_node_health('!abc')
        assert 0 <= health <= 100


# =============================================================================
# Trend Detection
# =============================================================================

class TestTrend:
    def test_insufficient_data(self, scorer):
        """Too few snapshots = None trend."""
        scorer.get_snapshot()
        assert scorer.get_trend() is None

    def test_stable_trend(self, scorer):
        """Consistent scores = stable."""
        scorer.report_service_status('meshtasticd', running=True)
        scorer.report_node_metrics('!abc', snr=-5.0)
        for _ in range(10):
            scorer.get_snapshot()
        trend = scorer.get_trend()
        assert trend == 'stable'

    def test_improving_trend(self, scorer):
        """Improving signals = improving trend."""
        scorer.report_service_status('meshtasticd', running=True)
        # Start bad
        scorer.report_node_metrics('!abc', snr=-20.0, rssi=-125)
        for _ in range(5):
            scorer.get_snapshot()
        # Improve
        scorer.report_node_metrics('!abc', snr=-2.0, rssi=-70)
        for _ in range(5):
            scorer.get_snapshot()
        trend = scorer.get_trend()
        assert trend == 'improving'

    def test_declining_trend(self, scorer):
        """Worsening signals = declining trend."""
        scorer.report_service_status('meshtasticd', running=True)
        # Start good
        scorer.report_node_metrics('!abc', snr=-2.0, rssi=-70)
        for _ in range(5):
            scorer.get_snapshot()
        # Decline
        scorer.report_node_metrics('!abc', snr=-20.0, rssi=-125)
        for _ in range(5):
            scorer.get_snapshot()
        trend = scorer.get_trend()
        assert trend == 'declining'


# =============================================================================
# History
# =============================================================================

class TestHistory:
    def test_history_stored(self, scorer):
        """Snapshots are stored in history."""
        scorer.report_service_status('meshtasticd', running=True)
        scorer.get_snapshot()
        scorer.get_snapshot()
        scorer.get_snapshot()
        history = scorer.get_history()
        assert len(history) == 3

    def test_history_limit(self, scorer):
        """History respects count limit."""
        scorer.report_service_status('meshtasticd', running=True)
        for _ in range(20):
            scorer.get_snapshot()
        history = scorer.get_history(count=5)
        assert len(history) == 5

    def test_history_newest_last(self, scorer):
        """History is ordered oldest to newest."""
        scorer.report_service_status('meshtasticd', running=True)
        for _ in range(5):
            scorer.get_snapshot()
        history = scorer.get_history()
        timestamps = [s.timestamp for s in history]
        assert timestamps == sorted(timestamps)


# =============================================================================
# Reset
# =============================================================================

class TestReset:
    def test_reset_clears_state(self, healthy_scorer):
        """Reset clears all state."""
        healthy_scorer.get_snapshot()
        healthy_scorer.reset()
        snapshot = healthy_scorer.get_snapshot()
        # After reset, neutral scores
        assert snapshot.connectivity_score == 50.0
        assert snapshot.freshness_score == 50.0

    def test_reset_clears_history(self, healthy_scorer):
        """Reset clears history."""
        healthy_scorer.get_snapshot()
        healthy_scorer.get_snapshot()
        healthy_scorer.reset()
        assert len(healthy_scorer.get_history()) == 0


# =============================================================================
# Format Display
# =============================================================================

class TestFormatDisplay:
    def test_display_nonempty(self, healthy_scorer):
        """Display produces non-empty output."""
        snapshot = healthy_scorer.get_snapshot()
        output = format_health_display(snapshot)
        assert len(output) > 50

    def test_display_contains_score(self, healthy_scorer):
        """Display shows the overall score."""
        snapshot = healthy_scorer.get_snapshot()
        output = format_health_display(snapshot)
        assert '/100' in output

    def test_display_contains_categories(self, healthy_scorer):
        """Display shows all categories."""
        snapshot = healthy_scorer.get_snapshot()
        output = format_health_display(snapshot)
        assert 'Connectivity' in output
        assert 'Performance' in output
        assert 'Reliability' in output
        assert 'Freshness' in output

    def test_display_status_indicator(self, scorer):
        """Display shows status indicator."""
        snapshot = scorer.get_snapshot()
        output = format_health_display(snapshot)
        assert any(ind in output for ind in ['[OK]', '[--]', '[!!]', '[XX]'])

    def test_display_progress_bars(self, healthy_scorer):
        """Display includes progress bar characters."""
        snapshot = healthy_scorer.get_snapshot()
        output = format_health_display(snapshot)
        assert '#' in output or '.' in output


# =============================================================================
# Edge Cases
# =============================================================================

class TestEdgeCases:
    def test_single_node(self, scorer):
        """Works with single node."""
        scorer.report_node_metrics('!only', snr=-5.0)
        snapshot = scorer.get_snapshot()
        assert 0 <= snapshot.overall_score <= 100

    def test_many_nodes(self, scorer):
        """Works with many nodes."""
        for i in range(50):
            scorer.report_node_metrics(f'!node{i}', snr=-5.0 - i * 0.1)
        snapshot = scorer.get_snapshot()
        assert 0 <= snapshot.overall_score <= 100

    def test_only_snr_no_rssi(self, scorer):
        """Works with only SNR (no RSSI)."""
        scorer.report_node_metrics('!abc', snr=-8.0)
        snapshot = scorer.get_snapshot()
        assert snapshot.performance_score > 0

    def test_only_rssi_no_snr(self, scorer):
        """Works with only RSSI (no SNR)."""
        scorer.report_node_metrics('!abc', rssi=-90)
        snapshot = scorer.get_snapshot()
        assert snapshot.performance_score > 0

    def test_extreme_snr_values(self, scorer):
        """Extreme SNR values don't crash."""
        scorer.report_node_metrics('!good', snr=20.0)
        scorer.report_node_metrics('!bad', snr=-40.0)
        snapshot = scorer.get_snapshot()
        assert 0 <= snapshot.overall_score <= 100

    def test_extreme_rssi_values(self, scorer):
        """Extreme RSSI values don't crash."""
        scorer.report_node_metrics('!good', rssi=-30)
        scorer.report_node_metrics('!bad', rssi=-150)
        snapshot = scorer.get_snapshot()
        assert 0 <= snapshot.overall_score <= 100

    def test_node_update_replaces(self, scorer):
        """Updating a node replaces its metrics."""
        scorer.report_node_metrics('!abc', snr=-20.0)
        scorer.report_node_metrics('!abc', snr=-3.0)
        snapshot = scorer.get_snapshot()
        # Should reflect the better SNR
        assert snapshot.performance_score > 70
