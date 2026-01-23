"""
Tests for signal strength trending module.

Tests cover:
- SignalSample creation and properties
- WindowStats calculation (SNR/RSSI averages, min, max, stddev)
- Trend detection (improving, stable, degrading)
- Stability scoring (0-100 scale)
- Event detection (drops, spikes, recoveries)
- Hourly pattern detection (time-of-day interference)
- NodeSignalReport generation
- SignalTrendingManager multi-node tracking
- Bulk ingestion from NodeHistoryDB
- Edge cases (empty data, single sample, all None values)
"""

import sys
import os
import time
import math

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import pytest

from utils.signal_trending import (
    SignalSample,
    WindowStats,
    SignalEvent,
    HourlyPattern,
    NodeSignalReport,
    SignalTrend,
    SignalTrendingManager,
    EVENT_THRESHOLD_DB,
    TREND_THRESHOLD_DB_HR,
    MIN_SAMPLES_FOR_TREND,
)


# =============================================================================
# SignalSample Tests
# =============================================================================

class TestSignalSample:
    def test_basic_creation(self):
        s = SignalSample(timestamp=1000.0, snr=-5.0, rssi=-95)
        assert s.timestamp == 1000.0
        assert s.snr == -5.0
        assert s.rssi == -95

    def test_has_snr(self):
        s = SignalSample(timestamp=1000.0, snr=-5.0)
        assert s.has_snr is True
        assert s.has_rssi is False

    def test_has_rssi(self):
        s = SignalSample(timestamp=1000.0, rssi=-100)
        assert s.has_snr is False
        assert s.has_rssi is True

    def test_both_none(self):
        s = SignalSample(timestamp=1000.0)
        assert s.has_snr is False
        assert s.has_rssi is False


# =============================================================================
# WindowStats Tests
# =============================================================================

class TestWindowStats:
    def test_to_dict_with_data(self):
        ws = WindowStats(
            window_name='1h', window_seconds=3600,
            sample_count=10,
            snr_avg=-5.0, snr_min=-10.0, snr_max=0.0, snr_stddev=2.5,
            rssi_avg=-95.0, rssi_min=-110.0, rssi_max=-80.0, rssi_stddev=8.0,
        )
        d = ws.to_dict()
        assert d['window'] == '1h'
        assert d['sample_count'] == 10
        assert d['snr']['avg'] == -5.0
        assert d['rssi']['min'] == -110.0

    def test_to_dict_no_data(self):
        ws = WindowStats(window_name='empty', window_seconds=3600, sample_count=0)
        d = ws.to_dict()
        assert d['snr'] is None
        assert d['rssi'] is None


# =============================================================================
# SignalTrend - Basic Operations
# =============================================================================

class TestSignalTrendBasic:
    def test_empty_trend(self):
        t = SignalTrend(node_id="!test1")
        assert t.sample_count == 0
        assert t.node_id == "!test1"

    def test_add_sample(self):
        t = SignalTrend()
        t.add_sample(1000.0, snr=-5.0)
        assert t.sample_count == 1

    def test_add_sample_ignores_all_none(self):
        t = SignalTrend()
        t.add_sample(1000.0, snr=None, rssi=None)
        assert t.sample_count == 0

    def test_add_samples_bulk(self):
        t = SignalTrend()
        samples = [
            (1000.0, -5.0, -95),
            (1060.0, -6.0, -97),
            (1120.0, -4.0, -93),
        ]
        added = t.add_samples_bulk(samples)
        assert added == 3
        assert t.sample_count == 3

    def test_bulk_skips_none_pairs(self):
        t = SignalTrend()
        samples = [
            (1000.0, -5.0, None),
            (1060.0, None, None),  # Should be skipped
            (1120.0, None, -93),
        ]
        added = t.add_samples_bulk(samples)
        assert added == 2

    def test_max_samples_eviction(self):
        t = SignalTrend(max_samples=5)
        for i in range(10):
            t.add_sample(float(i * 60), snr=-5.0 + i)
        assert t.sample_count == 5
        # Should have the last 5 samples
        report = t.get_report(now=600.0)
        assert report.current_snr == 4.0  # -5 + 9

    def test_max_samples_bulk_eviction(self):
        t = SignalTrend(max_samples=3)
        samples = [(float(i), -10.0 + i, None) for i in range(10)]
        t.add_samples_bulk(samples)
        assert t.sample_count == 3


# =============================================================================
# WindowStats Calculation
# =============================================================================

class TestWindowStatsCalculation:
    def setup_method(self):
        self.now = 10000.0
        self.trend = SignalTrend()
        # Add samples spread across 2 hours
        for i in range(120):
            ts = self.now - 7200 + i * 60  # 2 hours of data, 1/min
            snr = -5.0 + math.sin(i * 0.1) * 2  # Oscillating around -5
            rssi = -95.0 + math.sin(i * 0.1) * 5
            self.trend.add_sample(ts, snr=snr, rssi=rssi)

    def test_1h_window(self):
        stats = self.trend.get_window_stats(3600, window_name='1h', now=self.now)
        assert stats.window_name == '1h'
        assert stats.sample_count == 60  # Last 60 minutes
        assert stats.snr_avg is not None
        assert -7.0 < stats.snr_avg < -3.0  # Around -5

    def test_snr_min_max(self):
        stats = self.trend.get_window_stats(7200, now=self.now)
        assert stats.snr_min is not None
        assert stats.snr_max is not None
        assert stats.snr_min <= stats.snr_avg <= stats.snr_max

    def test_stddev_positive(self):
        stats = self.trend.get_window_stats(7200, now=self.now)
        assert stats.snr_stddev is not None
        assert stats.snr_stddev > 0  # Oscillating signal has variance

    def test_rssi_stats(self):
        stats = self.trend.get_window_stats(7200, now=self.now)
        assert stats.rssi_avg is not None
        assert -100.0 < stats.rssi_avg < -90.0

    def test_empty_window(self):
        stats = self.trend.get_window_stats(60, now=self.now + 10000)  # Far future
        assert stats.sample_count == 0
        assert stats.snr_avg is None

    def test_single_sample_stddev(self):
        t = SignalTrend()
        t.add_sample(100.0, snr=-5.0)
        stats = t.get_window_stats(200, now=100.0)
        assert stats.snr_stddev == 0.0


# =============================================================================
# Trend Detection
# =============================================================================

class TestTrendDetection:
    def test_insufficient_data(self):
        t = SignalTrend()
        t.add_sample(100.0, snr=-5.0)
        direction, rate = t.get_trend()
        assert direction == 'insufficient_data'
        assert rate == 0.0

    def test_stable_signal(self):
        t = SignalTrend()
        now = time.time()
        for i in range(20):
            t.add_sample(now + i * 60, snr=-5.0)  # Constant -5 dB
        direction, rate = t.get_trend()
        assert direction == 'stable'
        assert abs(rate) < TREND_THRESHOLD_DB_HR

    def test_degrading_signal(self):
        t = SignalTrend()
        now = time.time()
        for i in range(60):
            # Drop 0.5 dB per sample (1 sample/min = 30 dB/hour)
            t.add_sample(now + i * 60, snr=-5.0 - i * 0.5)
        direction, rate = t.get_trend()
        assert direction == 'degrading'
        assert rate < -TREND_THRESHOLD_DB_HR

    def test_improving_signal(self):
        t = SignalTrend()
        now = time.time()
        for i in range(60):
            # Improve 0.2 dB per sample
            t.add_sample(now + i * 60, snr=-20.0 + i * 0.2)
        direction, rate = t.get_trend()
        assert direction == 'improving'
        assert rate > TREND_THRESHOLD_DB_HR

    def test_rssi_fallback(self):
        """When no SNR data, uses RSSI for trend."""
        t = SignalTrend()
        now = time.time()
        for i in range(20):
            t.add_sample(now + i * 60, rssi=-100.0 - i * 0.5)  # Degrading RSSI
        direction, rate = t.get_trend()
        assert direction == 'degrading'

    def test_trend_rate_units(self):
        """Verify rate is in dB/hour."""
        t = SignalTrend()
        now = time.time()
        # 1 dB drop per hour (1/60 dB per minute)
        for i in range(120):
            t.add_sample(now + i * 60, snr=-5.0 - i / 60.0)
        direction, rate = t.get_trend()
        assert direction == 'degrading'
        # Rate should be approximately -1.0 dB/hour
        assert -1.5 < rate < -0.5


# =============================================================================
# Stability Score
# =============================================================================

class TestStabilityScore:
    def test_perfect_stability(self):
        """Constant signal = high stability."""
        t = SignalTrend()
        now = time.time()
        for i in range(30):
            t.add_sample(now - 1800 + i * 60, snr=-5.0)
        score = t.get_stability_score(window_seconds=3600, now=now)
        assert score >= 90  # Rock solid

    def test_noisy_signal(self):
        """Highly variable signal = low stability."""
        t = SignalTrend()
        now = time.time()
        import random
        rng = random.Random(42)
        for i in range(30):
            # Random SNR between -20 and +5 (stddev ~7)
            snr = -7.5 + rng.uniform(-12.5, 12.5)
            t.add_sample(now - 1800 + i * 60, snr=snr)
        score = t.get_stability_score(window_seconds=3600, now=now)
        assert score < 50  # Unstable

    def test_moderate_variance(self):
        """Moderate variance = middle score."""
        t = SignalTrend()
        now = time.time()
        for i in range(30):
            # +/- 2 dB oscillation (stddev ~1.4)
            snr = -5.0 + (2.0 if i % 2 == 0 else -2.0)
            t.add_sample(now - 1800 + i * 60, snr=snr)
        score = t.get_stability_score(window_seconds=3600, now=now)
        assert 60 < score < 95

    def test_insufficient_data(self):
        """Single sample = neutral 50."""
        t = SignalTrend()
        t.add_sample(time.time(), snr=-5.0)
        score = t.get_stability_score()
        assert score == 50

    def test_score_bounds(self):
        """Score always 0-100."""
        t = SignalTrend()
        now = time.time()
        # Extreme variance
        for i in range(30):
            t.add_sample(now - 1800 + i * 60, snr=-50.0 + i * 5)
        score = t.get_stability_score(window_seconds=3600, now=now)
        assert 0 <= score <= 100


# =============================================================================
# Event Detection
# =============================================================================

class TestEventDetection:
    def test_no_events_stable(self):
        """Stable signal has no events."""
        t = SignalTrend()
        now = time.time()
        for i in range(20):
            t.add_sample(now + i * 60, snr=-5.0)
        events = t.detect_events()
        assert len(events) == 0

    def test_detect_drop(self):
        """Sudden SNR drop detected."""
        t = SignalTrend()
        now = time.time()
        t.add_sample(now, snr=-5.0)
        t.add_sample(now + 60, snr=-5.0)
        t.add_sample(now + 120, snr=-15.0)  # 10 dB drop
        events = t.detect_events()
        assert len(events) >= 1
        drop = [e for e in events if e.event_type == 'drop']
        assert len(drop) == 1
        assert drop[0].magnitude_db == 10.0
        assert drop[0].metric == 'snr'

    def test_detect_spike(self):
        """Sudden SNR improvement detected."""
        t = SignalTrend()
        now = time.time()
        t.add_sample(now, snr=-15.0)
        t.add_sample(now + 60, snr=-15.0)
        t.add_sample(now + 120, snr=-5.0)  # 10 dB spike
        events = t.detect_events()
        spikes = [e for e in events if e.event_type in ('spike', 'recovery')]
        assert len(spikes) >= 1
        assert spikes[0].magnitude_db == 10.0

    def test_detect_recovery(self):
        """Drop followed by recovery detected."""
        t = SignalTrend()
        now = time.time()
        t.add_sample(now, snr=-5.0)
        t.add_sample(now + 60, snr=-15.0)  # Drop
        t.add_sample(now + 120, snr=-5.0)  # Recovery
        events = t.detect_events()
        recoveries = [e for e in events if e.event_type == 'recovery']
        assert len(recoveries) >= 1

    def test_custom_threshold(self):
        """Custom threshold filters smaller changes."""
        t = SignalTrend()
        now = time.time()
        t.add_sample(now, snr=-5.0)
        t.add_sample(now + 60, snr=-8.0)  # 3 dB drop
        # Default threshold is 5 dB, should not detect
        events = t.detect_events(threshold_db=5.0)
        assert len(events) == 0
        # Lower threshold should detect
        events = t.detect_events(threshold_db=2.0)
        assert len(events) >= 1

    def test_rssi_events(self):
        """RSSI events also detected."""
        t = SignalTrend()
        now = time.time()
        t.add_sample(now, rssi=-90.0)
        t.add_sample(now + 60, rssi=-105.0)  # 15 dBm drop
        events = t.detect_events()
        rssi_events = [e for e in events if e.metric == 'rssi']
        assert len(rssi_events) >= 1

    def test_event_to_dict(self):
        e = SignalEvent(
            timestamp=1000.0, event_type='drop',
            magnitude_db=7.5, metric='snr',
            before_value=-5.0, after_value=-12.5,
        )
        d = e.to_dict()
        assert d['event_type'] == 'drop'
        assert d['magnitude_db'] == 7.5
        assert d['before'] == -5.0


# =============================================================================
# Hourly Pattern Detection
# =============================================================================

class TestHourlyPattern:
    def test_no_pattern_uniform(self):
        """Uniform signal across hours shows no pattern."""
        t = SignalTrend()
        base = time.time()
        # 24 hours of constant signal
        for i in range(24 * 60):  # 1 sample/min for 24h
            t.add_sample(base + i * 60, snr=-5.0)
        patterns, detected = t.get_hourly_pattern()
        assert len(patterns) == 24
        assert detected is False

    def test_pattern_detected(self):
        """Time-of-day variation detected as pattern."""
        t = SignalTrend()
        import time as time_mod

        # Create samples with strong time-of-day pattern
        # Daytime (6-18): good signal. Nighttime (18-6): bad signal.
        base = time.time()
        # Need to align to known hour boundaries
        for day in range(3):  # 3 days of data
            for hour in range(24):
                for minute in range(0, 60, 5):  # Every 5 min
                    ts = base + day * 86400 + hour * 3600 + minute * 60
                    if 6 <= hour < 18:
                        snr = -3.0  # Good during day
                    else:
                        snr = -15.0  # Bad at night
                    t.add_sample(ts, snr=snr)

        patterns, detected = t.get_hourly_pattern()
        # Should detect pattern (12 dB difference between day/night)
        assert detected is True

    def test_best_worst_hour_in_report(self):
        """Report identifies best and worst hours."""
        t = SignalTrend(node_id="!pattern_node")
        base = time.time()

        for day in range(3):
            for hour in range(24):
                for minute in range(0, 60, 10):
                    ts = base + day * 86400 + hour * 3600 + minute * 60
                    # Hour-dependent signal
                    snr = -5.0 - abs(hour - 12) * 1.5  # Best at noon
                    t.add_sample(ts, snr=snr)

        report = t.get_report(now=base + 3 * 86400)
        if report.pattern_detected:
            assert report.best_hour is not None
            assert report.worst_hour is not None


# =============================================================================
# NodeSignalReport
# =============================================================================

class TestNodeSignalReport:
    def test_empty_report(self):
        t = SignalTrend(node_id="!empty")
        report = t.get_report()
        assert report.node_id == "!empty"
        assert report.total_samples == 0
        assert report.trend_direction == 'insufficient_data'
        assert report.stability_score == 50

    def test_report_with_data(self):
        t = SignalTrend(node_id="!node1")
        now = time.time()
        for i in range(60):
            t.add_sample(now - 3600 + i * 60, snr=-5.0 - i * 0.01, rssi=-95)
        report = t.get_report(now=now)
        assert report.total_samples == 60
        assert report.time_span_hours > 0
        assert report.current_snr is not None
        assert report.current_rssi == -95
        assert len(report.windows) > 0

    def test_report_to_dict(self):
        t = SignalTrend(node_id="!dict_test")
        now = time.time()
        for i in range(10):
            t.add_sample(now - 600 + i * 60, snr=-5.0)
        report = t.get_report(now=now)
        d = report.to_dict()
        assert d['node_id'] == '!dict_test'
        assert 'trend' in d
        assert 'stability_score' in d
        assert 'windows' in d
        assert 'pattern' in d

    def test_report_includes_events(self):
        t = SignalTrend(node_id="!events")
        now = time.time()
        t.add_sample(now - 120, snr=-5.0)
        t.add_sample(now - 60, snr=-5.0)
        t.add_sample(now, snr=-20.0)  # Big drop
        report = t.get_report(now=now)
        assert len(report.events) >= 1


# =============================================================================
# SignalTrendingManager
# =============================================================================

class TestSignalTrendingManager:
    def test_empty_manager(self):
        mgr = SignalTrendingManager()
        assert mgr.get_tracked_nodes() == []

    def test_add_samples(self):
        mgr = SignalTrendingManager()
        now = time.time()
        mgr.add_sample("!node1", now, snr=-5.0)
        mgr.add_sample("!node2", now, snr=-10.0)
        assert len(mgr.get_tracked_nodes()) == 2

    def test_get_report(self):
        mgr = SignalTrendingManager()
        now = time.time()
        for i in range(10):
            mgr.add_sample("!node1", now + i * 60, snr=-5.0)
        report = mgr.get_report("!node1")
        assert report is not None
        assert report.node_id == "!node1"

    def test_get_report_unknown_node(self):
        mgr = SignalTrendingManager()
        assert mgr.get_report("!unknown") is None

    def test_get_all_reports(self):
        mgr = SignalTrendingManager()
        now = time.time()
        for node in ["!a", "!b", "!c"]:
            for i in range(5):
                mgr.add_sample(node, now + i * 60, snr=-5.0)
        reports = mgr.get_all_reports()
        assert len(reports) == 3

    def test_get_degrading_nodes(self):
        mgr = SignalTrendingManager()
        now = time.time()
        # Node with degrading signal
        for i in range(30):
            mgr.add_sample("!bad", now + i * 60, snr=-5.0 - i * 1.0)
        # Node with stable signal
        for i in range(30):
            mgr.add_sample("!good", now + i * 60, snr=-5.0)

        degrading = mgr.get_degrading_nodes()
        assert any(r.node_id == "!bad" for r in degrading)
        assert not any(r.node_id == "!good" for r in degrading)

    def test_get_unstable_nodes(self):
        mgr = SignalTrendingManager()
        now = time.time()
        import random
        rng = random.Random(99)
        # Unstable node
        for i in range(30):
            mgr.add_sample("!jitter", now - 1800 + i * 60,
                           snr=-5.0 + rng.uniform(-15, 15))
        # Stable node
        for i in range(30):
            mgr.add_sample("!rock", now - 1800 + i * 60, snr=-5.0)

        unstable = mgr.get_unstable_nodes(threshold=40)
        node_ids = [r.node_id for r in unstable]
        assert "!jitter" in node_ids
        assert "!rock" not in node_ids

    def test_get_summary(self):
        mgr = SignalTrendingManager()
        now = time.time()
        for i in range(20):
            mgr.add_sample("!node1", now + i * 60, snr=-5.0)
            mgr.add_sample("!node2", now + i * 60, snr=-5.0 - i)
        summary = mgr.get_summary()
        assert summary['total_nodes'] == 2
        assert summary['total_samples'] == 40
        assert 'health' in summary
        assert 'alerts' in summary

    def test_empty_summary(self):
        mgr = SignalTrendingManager()
        summary = mgr.get_summary()
        assert summary['total_nodes'] == 0


# =============================================================================
# Integration with NodeHistoryDB (mock)
# =============================================================================

class TestHistoryIngestion:
    def test_ingest_from_mock_history(self):
        """Test ingestion from a mock NodeHistoryDB."""
        from dataclasses import dataclass
        from typing import List

        @dataclass
        class MockObs:
            node_id: str
            timestamp: float
            snr: float = None
            latitude: float = 0.0
            longitude: float = 0.0

        class MockHistoryDB:
            def __init__(self, nodes_data):
                self._data = nodes_data

            def get_unique_nodes(self, hours=24):
                return [{'node_id': nid} for nid in self._data.keys()]

            def get_trajectory(self, node_id, hours=24):
                return self._data.get(node_id, [])

        now = time.time()
        mock_data = {
            "!node_a": [MockObs("!node_a", now - 3600 + i * 60, snr=-5.0 + i * 0.1)
                        for i in range(60)],
            "!node_b": [MockObs("!node_b", now - 3600 + i * 60, snr=-10.0)
                        for i in range(60)],
        }

        mgr = SignalTrendingManager()
        total = mgr.ingest_from_history(MockHistoryDB(mock_data), hours=24)
        assert total == 120
        assert len(mgr.get_tracked_nodes()) == 2

        # node_a should be improving
        report_a = mgr.get_report("!node_a")
        assert report_a is not None
        assert report_a.trend_direction == 'improving'

        # node_b should be stable
        report_b = mgr.get_report("!node_b")
        assert report_b is not None
        assert report_b.trend_direction == 'stable'

    def test_ingest_skips_none_snr(self):
        """Observations with None SNR are skipped."""
        from dataclasses import dataclass

        @dataclass
        class MockObs:
            node_id: str
            timestamp: float
            snr: float = None

        class MockHistoryDB:
            def get_unique_nodes(self, hours=24):
                return [{'node_id': '!x'}]
            def get_trajectory(self, node_id, hours=24):
                return [MockObs('!x', 1000.0, snr=None),
                        MockObs('!x', 1060.0, snr=-5.0)]

        mgr = SignalTrendingManager()
        total = mgr.ingest_from_history(MockHistoryDB(), hours=24)
        assert total == 1  # Only the non-None sample


# =============================================================================
# Edge Cases
# =============================================================================

class TestEdgeCases:
    def test_all_same_timestamp(self):
        """Multiple samples at exact same time."""
        t = SignalTrend()
        for i in range(10):
            t.add_sample(1000.0, snr=-5.0 + i)
        direction, rate = t.get_trend()
        # Slope undefined with same x values, should not crash
        assert direction in ('stable', 'insufficient_data', 'improving', 'degrading')

    def test_single_sample_report(self):
        t = SignalTrend(node_id="!one")
        t.add_sample(time.time(), snr=-5.0, rssi=-95)
        report = t.get_report()
        assert report.total_samples == 1
        assert report.trend_direction == 'insufficient_data'
        assert report.current_snr == -5.0

    def test_very_large_values(self):
        """Extreme signal values don't crash."""
        t = SignalTrend()
        now = time.time()
        t.add_sample(now, snr=50.0, rssi=-30.0)  # Extremely strong
        t.add_sample(now + 60, snr=-50.0, rssi=-140.0)  # Extremely weak
        events = t.detect_events()
        assert len(events) >= 1  # Should detect the 100 dB swing

    def test_report_time_span(self):
        """Time span correctly calculated."""
        t = SignalTrend()
        t.add_sample(0.0, snr=-5.0)
        t.add_sample(7200.0, snr=-5.0)  # 2 hours later
        report = t.get_report(now=7200.0)
        assert abs(report.time_span_hours - 2.0) < 0.01

    def test_get_or_create(self):
        """Manager creates trend on first access."""
        mgr = SignalTrendingManager()
        trend = mgr.get_or_create("!new_node")
        assert trend.node_id == "!new_node"
        # Second call returns same object
        trend2 = mgr.get_or_create("!new_node")
        assert trend is trend2
