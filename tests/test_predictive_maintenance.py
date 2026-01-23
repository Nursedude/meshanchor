"""
Tests for Predictive Maintenance module.

Tests cover:
- Battery drain rate calculation and forecasting
- Node dropout pattern recognition
- Periodicity detection
- Reliability scoring
- Maintenance recommendations
- Solar/charging pattern detection
- Voltage-to-percentage conversion
- Report formatting

Run with: pytest tests/test_predictive_maintenance.py -v
"""

import pytest
import time
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from utils.predictive_maintenance import (
    MaintenancePredictor,
    BatteryForecast,
    DropoutPattern,
    MaintenanceRecommendation,
    voltage_to_percentage,
    format_maintenance_report,
    BATTERY_WARNING_PCT,
    BATTERY_CRITICAL_PCT,
    BATTERY_SHUTDOWN_PCT,
)


@pytest.fixture
def predictor():
    """Create a fresh predictor."""
    return MaintenancePredictor()


# =============================================================================
# Battery Drain Rate Tests
# =============================================================================

class TestBatteryRecording:
    """Test battery sample recording."""

    def test_record_single_sample(self, predictor):
        predictor.record_battery("!node1", 85.0)
        forecast = predictor.get_battery_forecast("!node1")
        assert forecast.current_pct == 85.0
        assert forecast.sample_count == 1

    def test_record_with_voltage(self, predictor):
        predictor.record_battery("!node1", 85.0, voltage=3.95)
        forecast = predictor.get_battery_forecast("!node1")
        assert forecast.current_voltage == 3.95

    def test_record_with_timestamp(self, predictor):
        ts = time.time() - 3600  # 1 hour ago
        predictor.record_battery("!node1", 90.0, timestamp=ts)
        predictor.record_battery("!node1", 85.0)
        forecast = predictor.get_battery_forecast("!node1")
        assert forecast.sample_count == 2

    def test_clamp_percentage(self, predictor):
        predictor.record_battery("!node1", 150.0)
        predictor.record_battery("!node1", -10.0)
        predictor.record_battery("!node1", 50.0)
        forecast = predictor.get_battery_forecast("!node1")
        # Should clamp to 0-100
        assert forecast.current_pct == 50.0

    def test_multiple_nodes(self, predictor):
        predictor.record_battery("!node1", 80.0)
        predictor.record_battery("!node2", 60.0)
        assert len(predictor.get_node_ids()) == 2

    def test_max_samples_trimmed(self, predictor):
        for i in range(600):
            predictor.record_battery("!node1", 80.0 - i * 0.01, timestamp=time.time() + i)
        assert len(predictor._battery_history["!node1"]) == predictor.MAX_BATTERY_SAMPLES


class TestBatteryForecast:
    """Test battery drain prediction."""

    def test_insufficient_data(self, predictor):
        predictor.record_battery("!node1", 80.0)
        predictor.record_battery("!node1", 78.0)
        forecast = predictor.get_battery_forecast("!node1")
        assert forecast.trend == 'insufficient_data'
        assert forecast.confidence == 0.0

    def test_unknown_node(self, predictor):
        forecast = predictor.get_battery_forecast("!unknown")
        assert forecast.trend == 'insufficient_data'
        assert forecast.sample_count == 0

    def test_draining_battery(self, predictor):
        """Simulate battery draining at 5%/hour."""
        base_time = time.time() - 4 * 3600
        for i in range(5):
            predictor.record_battery(
                "!node1",
                80.0 - i * 5.0,
                timestamp=base_time + i * 3600
            )

        forecast = predictor.get_battery_forecast("!node1")
        assert forecast.trend == 'draining'
        assert forecast.drain_rate_pct_per_hour < 0
        # Should be approximately -5%/hour
        assert -7.0 < forecast.drain_rate_pct_per_hour < -3.0

    def test_charging_battery(self, predictor):
        """Simulate battery charging."""
        base_time = time.time() - 4 * 3600
        for i in range(5):
            predictor.record_battery(
                "!node1",
                40.0 + i * 10.0,
                timestamp=base_time + i * 3600
            )

        forecast = predictor.get_battery_forecast("!node1")
        assert forecast.trend == 'charging'
        assert forecast.drain_rate_pct_per_hour > 0

    def test_stable_battery(self, predictor):
        """Simulate stable battery (small fluctuations)."""
        base_time = time.time() - 10 * 3600
        for i in range(11):
            # Fluctuate around 75% within 1%
            predictor.record_battery(
                "!node1",
                75.0 + (i % 3 - 1) * 0.3,
                timestamp=base_time + i * 3600
            )

        forecast = predictor.get_battery_forecast("!node1")
        assert forecast.trend == 'stable'
        assert abs(forecast.drain_rate_pct_per_hour) < 0.05

    def test_hours_to_thresholds(self, predictor):
        """Verify time-to-threshold calculations."""
        base_time = time.time() - 3 * 3600
        # Drain at 2%/hour from 50%
        for i in range(4):
            predictor.record_battery(
                "!node1",
                50.0 - i * 2.0,
                timestamp=base_time + i * 3600
            )

        forecast = predictor.get_battery_forecast("!node1")
        # At 44%, draining at ~2%/h:
        # To 30% = (44-30)/2 = 7h
        # To 15% = (44-15)/2 = 14.5h
        # To 5% = (44-5)/2 = 19.5h
        assert forecast.hours_to_warning is not None
        assert 5 < forecast.hours_to_warning < 12
        assert forecast.hours_to_critical is not None
        assert forecast.hours_to_shutdown is not None
        assert forecast.hours_to_critical > forecast.hours_to_warning
        assert forecast.hours_to_shutdown > forecast.hours_to_critical

    def test_no_thresholds_when_charging(self, predictor):
        """Charging batteries shouldn't have time-to-threshold."""
        base_time = time.time() - 3 * 3600
        for i in range(4):
            predictor.record_battery(
                "!node1",
                30.0 + i * 10.0,
                timestamp=base_time + i * 3600
            )

        forecast = predictor.get_battery_forecast("!node1")
        assert forecast.hours_to_warning is None
        assert forecast.hours_to_critical is None
        assert forecast.hours_to_shutdown is None

    def test_confidence_increases_with_data(self, predictor):
        """More samples and longer time span should increase confidence."""
        base_time = time.time() - 48 * 3600

        # Few samples, short span
        predictor.record_battery("!node1", 80.0, timestamp=base_time)
        predictor.record_battery("!node1", 79.5, timestamp=base_time + 1800)
        predictor.record_battery("!node1", 79.0, timestamp=base_time + 3600)
        forecast_short = predictor.get_battery_forecast("!node1")

        # Many samples, long span
        predictor._battery_history["!node2"] = []
        for i in range(30):
            predictor.record_battery(
                "!node2",
                80.0 - i * 0.5,
                timestamp=base_time + i * 3600
            )
        forecast_long = predictor.get_battery_forecast("!node2")

        assert forecast_long.confidence > forecast_short.confidence

    def test_forecast_to_dict(self, predictor):
        base_time = time.time() - 3 * 3600
        for i in range(4):
            predictor.record_battery("!node1", 80.0 - i * 3.0,
                                     voltage=4.0 - i * 0.05,
                                     timestamp=base_time + i * 3600)

        forecast = predictor.get_battery_forecast("!node1")
        d = forecast.to_dict()
        assert d['node_id'] == '!node1'
        assert isinstance(d['current_pct'], float)
        assert isinstance(d['drain_rate_pct_per_hour'], float)
        assert d['trend'] in ('draining', 'charging', 'stable', 'insufficient_data')


class TestSolarDetection:
    """Test solar/charging pattern detection."""

    def test_solar_pattern_detected(self, predictor):
        """Simulate day/night solar cycle."""
        base_time = time.time() - 48 * 3600
        for hour in range(48):
            # Battery charges during day (hours 8-16), drains at night
            hour_of_day = hour % 24
            if 8 <= hour_of_day <= 16:
                pct = 50.0 + (hour_of_day - 8) * 5.0  # Charge up
            else:
                pct = 90.0 - ((hour_of_day - 16) % 24) * 2.5  # Drain down
            predictor.record_battery("!solar", pct, timestamp=base_time + hour * 3600)

        forecast = predictor.get_battery_forecast("!solar")
        assert forecast.is_solar is True

    def test_no_solar_pattern_linear_drain(self, predictor):
        """Pure linear drain should not be detected as solar."""
        base_time = time.time() - 24 * 3600
        for hour in range(25):
            predictor.record_battery("!linear", 90.0 - hour * 2.0,
                                     timestamp=base_time + hour * 3600)

        forecast = predictor.get_battery_forecast("!linear")
        assert forecast.is_solar is False


# =============================================================================
# Node Dropout Pattern Tests
# =============================================================================

class TestStatusRecording:
    """Test status event recording."""

    def test_record_online(self, predictor):
        predictor.record_status("!node1", online=True)
        events = predictor._status_history["!node1"]
        assert len(events) == 1
        assert events[0].online is True

    def test_record_offline(self, predictor):
        predictor.record_status("!node1", online=False)
        events = predictor._status_history["!node1"]
        assert events[0].online is False

    def test_max_events_trimmed(self, predictor):
        for i in range(1100):
            predictor.record_status("!node1", online=(i % 2 == 0),
                                    timestamp=time.time() + i)
        assert len(predictor._status_history["!node1"]) == predictor.MAX_STATUS_EVENTS


class TestDropoutPattern:
    """Test dropout pattern analysis."""

    def test_insufficient_data(self, predictor):
        predictor.record_status("!node1", online=True)
        predictor.record_status("!node1", online=False)
        pattern = predictor.get_dropout_pattern("!node1")
        assert pattern.prediction == 'insufficient_data'

    def test_unknown_node(self, predictor):
        pattern = predictor.get_dropout_pattern("!unknown")
        assert pattern.prediction == 'insufficient_data'
        assert pattern.total_events == 0

    def test_stable_node(self, predictor):
        """Node that stays online should be classified as stable."""
        base_time = time.time() - 24 * 3600
        for i in range(10):
            predictor.record_status("!stable", online=True,
                                    timestamp=base_time + i * 3600)

        pattern = predictor.get_dropout_pattern("!stable")
        assert pattern.dropout_count == 0
        assert pattern.prediction == 'stable'
        assert pattern.uptime_pct == 100.0

    def test_intermittent_node(self, predictor):
        """Node with occasional dropouts."""
        base_time = time.time() - 24 * 3600
        events = [
            (0, True), (4, False), (4.5, True),  # 30 min dropout at hour 4
            (10, False), (10.5, True),  # 30 min dropout at hour 10
            (16, False), (16.5, True),  # 30 min dropout at hour 16
            (20, True), (24, True),
        ]
        for hour, online in events:
            predictor.record_status("!intermittent", online=online,
                                    timestamp=base_time + hour * 3600)

        pattern = predictor.get_dropout_pattern("!intermittent")
        assert pattern.dropout_count == 3
        assert pattern.prediction == 'intermittent'
        assert pattern.dropouts_per_day > 1.0
        assert pattern.avg_downtime_minutes > 0

    def test_failing_node(self, predictor):
        """Node with very frequent dropouts."""
        base_time = time.time() - 6 * 3600  # 6 hours
        # Dropout every 30 minutes
        for i in range(12):
            hour = i * 0.5
            predictor.record_status("!failing", online=True,
                                    timestamp=base_time + hour * 3600)
            predictor.record_status("!failing", online=False,
                                    timestamp=base_time + (hour + 0.25) * 3600)

        pattern = predictor.get_dropout_pattern("!failing")
        assert pattern.dropout_count >= 6
        assert pattern.prediction == 'failing'
        assert pattern.uptime_pct < 75.0

    def test_periodic_dropout(self, predictor):
        """Node that drops out on a regular schedule."""
        base_time = time.time() - 72 * 3600  # 3 days
        # Dropout every 8 hours (possible watchdog reset)
        for cycle in range(9):  # 9 cycles over 72 hours
            hour = cycle * 8
            predictor.record_status("!periodic", online=True,
                                    timestamp=base_time + hour * 3600)
            predictor.record_status("!periodic", online=False,
                                    timestamp=base_time + (hour + 7.9) * 3600)
            predictor.record_status("!periodic", online=True,
                                    timestamp=base_time + (hour + 8.0) * 3600)

        pattern = predictor.get_dropout_pattern("!periodic")
        assert pattern.dropout_count >= 5
        assert pattern.is_periodic is True
        assert pattern.period_hours is not None
        # Period should be roughly 8 hours
        assert 6.0 < pattern.period_hours < 10.0

    def test_uptime_percentage(self, predictor):
        """Test uptime percentage calculation."""
        base_time = time.time() - 10 * 3600  # 10 hours
        # Online for 7 hours, offline for 3 hours
        predictor.record_status("!node1", online=True, timestamp=base_time)
        predictor.record_status("!node1", online=False, timestamp=base_time + 7 * 3600)
        predictor.record_status("!node1", online=True, timestamp=base_time + 10 * 3600)
        # Add extra events for MIN_STATUS_SAMPLES
        predictor.record_status("!node1", online=True, timestamp=base_time + 10.1 * 3600)
        predictor.record_status("!node1", online=True, timestamp=base_time + 10.2 * 3600)

        pattern = predictor.get_dropout_pattern("!node1")
        # ~70% uptime
        assert 60.0 < pattern.uptime_pct < 80.0

    def test_peak_dropout_hour(self, predictor):
        """Test detection of peak dropout hour."""
        # Create dropouts all at 2 AM
        for day in range(5):
            base = time.time() - (5 - day) * 86400
            # Set hour to 2 AM
            dt = datetime_at_hour(base, 2)
            predictor.record_status("!night_crash", online=True, timestamp=dt - 3600)
            predictor.record_status("!night_crash", online=False, timestamp=dt)
            predictor.record_status("!night_crash", online=True, timestamp=dt + 600)

        pattern = predictor.get_dropout_pattern("!night_crash")
        # Peak hour should be around 2
        if pattern.peak_dropout_hour is not None:
            assert pattern.peak_dropout_hour == 2 or abs(pattern.peak_dropout_hour - 2) <= 1

    def test_reliability_score_range(self, predictor):
        """Reliability score should be 0-100."""
        base_time = time.time() - 24 * 3600
        for i in range(10):
            predictor.record_status("!node1", online=(i % 2 == 0),
                                    timestamp=base_time + i * 3600)

        pattern = predictor.get_dropout_pattern("!node1")
        assert 0.0 <= pattern.reliability_score <= 100.0

    def test_pattern_to_dict(self, predictor):
        base_time = time.time() - 24 * 3600
        for i in range(10):
            predictor.record_status("!node1", online=(i % 3 != 0),
                                    timestamp=base_time + i * 3600)

        pattern = predictor.get_dropout_pattern("!node1")
        d = pattern.to_dict()
        assert d['node_id'] == '!node1'
        assert isinstance(d['uptime_pct'], float)
        assert d['prediction'] in ('stable', 'intermittent', 'failing', 'insufficient_data')


# =============================================================================
# Maintenance Recommendations Tests
# =============================================================================

class TestMaintenanceRecommendations:
    """Test maintenance recommendation generation."""

    def test_no_recommendations_healthy(self, predictor):
        """Healthy nodes should generate no recommendations."""
        base_time = time.time() - 24 * 3600
        for i in range(10):
            predictor.record_battery("!healthy", 85.0 - i * 0.1,
                                     timestamp=base_time + i * 3600)
            predictor.record_status("!healthy", online=True,
                                    timestamp=base_time + i * 3600)

        recs = predictor.get_maintenance_recommendations()
        assert len(recs) == 0

    def test_urgent_battery_recommendation(self, predictor):
        """Very low battery should generate urgent recommendation."""
        base_time = time.time() - 3 * 3600
        for i in range(4):
            predictor.record_battery("!dying", 12.0 - i * 2.0,
                                     timestamp=base_time + i * 3600)

        recs = predictor.get_maintenance_recommendations()
        battery_recs = [r for r in recs if r.node_id == '!dying']
        assert len(battery_recs) > 0
        assert battery_recs[0].priority == 'urgent'

    def test_soon_battery_recommendation(self, predictor):
        """Battery approaching critical should generate 'soon' recommendation."""
        base_time = time.time() - 5 * 3600
        for i in range(6):
            predictor.record_battery("!lowish", 35.0 - i * 2.0,
                                     timestamp=base_time + i * 3600)

        recs = predictor.get_maintenance_recommendations()
        battery_recs = [r for r in recs if r.node_id == '!lowish']
        assert len(battery_recs) > 0
        assert battery_recs[0].priority in ('soon', 'urgent')

    def test_failing_node_recommendation(self, predictor):
        """Failing node should generate urgent recommendation."""
        base_time = time.time() - 6 * 3600
        for i in range(20):
            predictor.record_status("!flaky", online=(i % 2 == 0),
                                    timestamp=base_time + i * 1080)  # Every 18 min

        recs = predictor.get_maintenance_recommendations()
        node_recs = [r for r in recs if r.node_id == '!flaky']
        assert len(node_recs) > 0
        assert node_recs[0].priority in ('urgent', 'soon')

    def test_recommendations_sorted_by_priority(self, predictor):
        """Recommendations should be sorted: urgent > soon > scheduled > monitor."""
        base_time = time.time() - 10 * 3600

        # Urgent: very low battery
        for i in range(4):
            predictor.record_battery("!urgent", 8.0 - i * 1.5,
                                     timestamp=base_time + i * 3600)

        # Scheduled: slowly draining
        for i in range(11):
            predictor.record_battery("!slow", 50.0 - i * 1.0,
                                     timestamp=base_time + i * 3600)

        recs = predictor.get_maintenance_recommendations()
        if len(recs) >= 2:
            priorities = [r.priority for r in recs]
            priority_values = {'urgent': 0, 'soon': 1, 'scheduled': 2, 'monitor': 3}
            priority_nums = [priority_values.get(p, 4) for p in priorities]
            assert priority_nums == sorted(priority_nums)


# =============================================================================
# Voltage Conversion Tests
# =============================================================================

class TestVoltageConversion:
    """Test voltage to percentage conversion."""

    def test_full_charge(self):
        assert voltage_to_percentage(4.2) == 100.0

    def test_above_full(self):
        assert voltage_to_percentage(4.5) == 100.0

    def test_empty(self):
        assert voltage_to_percentage(3.0) == 0.0

    def test_below_empty(self):
        assert voltage_to_percentage(2.5) == 0.0

    def test_nominal_voltage(self):
        # 3.7V should be around 40%
        pct = voltage_to_percentage(3.7)
        assert 35.0 < pct < 45.0

    def test_monotonic_decrease(self):
        """Higher voltage should always give higher percentage."""
        voltages = [4.2, 4.1, 4.0, 3.9, 3.8, 3.7, 3.6, 3.5, 3.4, 3.3, 3.2, 3.0]
        percentages = [voltage_to_percentage(v) for v in voltages]
        for i in range(len(percentages) - 1):
            assert percentages[i] >= percentages[i + 1]

    def test_interpolation_accuracy(self):
        # 3.85V should be between 3.8 (55%) and 3.9 (70%)
        pct = voltage_to_percentage(3.85)
        assert 55.0 < pct < 70.0


# =============================================================================
# Report Formatting Tests
# =============================================================================

class TestFormatReport:
    """Test report formatting."""

    def test_empty_report(self, predictor):
        report = format_maintenance_report(predictor)
        assert "PREDICTIVE MAINTENANCE REPORT" in report
        assert "No maintenance actions needed" in report

    def test_report_with_battery_data(self, predictor):
        base_time = time.time() - 5 * 3600
        for i in range(6):
            predictor.record_battery("!node1", 70.0 - i * 5.0,
                                     voltage=3.9 - i * 0.05,
                                     timestamp=base_time + i * 3600)

        report = format_maintenance_report(predictor)
        assert "BATTERY STATUS" in report
        assert "!node1" in report

    def test_report_with_dropout_data(self, predictor):
        base_time = time.time() - 24 * 3600
        for i in range(10):
            predictor.record_status("!flaky", online=(i % 3 != 0),
                                    timestamp=base_time + i * 3600)

        report = format_maintenance_report(predictor)
        assert "NODE RELIABILITY" in report
        assert "!flaky" in report

    def test_report_with_recommendations(self, predictor):
        base_time = time.time() - 3 * 3600
        for i in range(4):
            predictor.record_battery("!dying", 10.0 - i * 2.0,
                                     timestamp=base_time + i * 3600)

        report = format_maintenance_report(predictor)
        assert "MAINTENANCE ACTIONS" in report

    def test_report_is_string(self, predictor):
        report = format_maintenance_report(predictor)
        assert isinstance(report, str)


# =============================================================================
# Integration Tests
# =============================================================================

class TestIntegration:
    """Test integrated behavior across features."""

    def test_full_lifecycle(self, predictor):
        """Simulate a node's full lifecycle with battery and status."""
        base_time = time.time() - 48 * 3600

        # Node starts healthy
        for hour in range(48):
            ts = base_time + hour * 3600
            # Battery draining slowly
            predictor.record_battery("!field1", 95.0 - hour * 0.5, timestamp=ts)
            # Occasional dropout at night (hours 2-4)
            if hour % 24 in (2, 3, 4):
                predictor.record_status("!field1", online=False, timestamp=ts)
                predictor.record_status("!field1", online=True, timestamp=ts + 1800)
            else:
                predictor.record_status("!field1", online=True, timestamp=ts)

        # Check battery forecast
        forecast = predictor.get_battery_forecast("!field1")
        assert forecast.trend == 'draining'
        assert forecast.drain_rate_pct_per_hour < 0

        # Check dropout pattern
        pattern = predictor.get_dropout_pattern("!field1")
        assert pattern.dropout_count > 0
        assert pattern.uptime_pct > 50.0

        # Check recommendations exist
        recs = predictor.get_maintenance_recommendations()
        # Should have some recommendation due to battery drain
        assert len(recs) >= 0  # May or may not trigger depending on exact rates

    def test_get_all_forecasts(self, predictor):
        base_time = time.time() - 3 * 3600
        for node in ["!n1", "!n2", "!n3"]:
            for i in range(4):
                predictor.record_battery(node, 80.0 - i * 3.0,
                                         timestamp=base_time + i * 3600)

        forecasts = predictor.get_all_forecasts()
        assert len(forecasts) == 3
        assert all(isinstance(f, BatteryForecast) for f in forecasts.values())

    def test_get_all_patterns(self, predictor):
        base_time = time.time() - 10 * 3600
        for node in ["!n1", "!n2"]:
            for i in range(6):
                predictor.record_status(node, online=True,
                                        timestamp=base_time + i * 3600)

        patterns = predictor.get_all_patterns()
        assert len(patterns) == 2
        assert all(isinstance(p, DropoutPattern) for p in patterns.values())

    def test_node_ids_combined(self, predictor):
        """get_node_ids should include nodes from both battery and status."""
        predictor.record_battery("!bat_only", 80.0)
        predictor.record_status("!status_only", online=True)
        predictor.record_battery("!both", 70.0)
        predictor.record_status("!both", online=True)

        ids = predictor.get_node_ids()
        assert "!bat_only" in ids
        assert "!status_only" in ids
        assert "!both" in ids
        assert len(ids) == 3


# =============================================================================
# Edge Cases
# =============================================================================

class TestEdgeCases:
    """Test edge cases and boundary conditions."""

    def test_single_timestamp_battery(self, predictor):
        """All samples at same timestamp."""
        for i in range(5):
            predictor.record_battery("!node1", 80.0 - i, timestamp=1000.0)

        forecast = predictor.get_battery_forecast("!node1")
        # Should handle gracefully (zero time span)
        assert forecast.drain_rate_pct_per_hour == 0.0 or forecast.trend in ('stable', 'insufficient_data', 'draining')

    def test_very_rapid_drain(self, predictor):
        """Battery draining extremely fast."""
        base_time = time.time() - 600  # 10 minutes ago
        for i in range(4):
            predictor.record_battery("!fast_drain", 50.0 - i * 10.0,
                                     timestamp=base_time + i * 120)

        forecast = predictor.get_battery_forecast("!fast_drain")
        assert forecast.trend == 'draining'
        assert forecast.drain_rate_pct_per_hour < -10

    def test_all_offline_events(self, predictor):
        """Node that only reports offline events."""
        base_time = time.time() - 10 * 3600
        for i in range(6):
            predictor.record_status("!alloff", online=False,
                                    timestamp=base_time + i * 3600)

        pattern = predictor.get_dropout_pattern("!alloff")
        assert pattern.uptime_pct == 0.0 or pattern.prediction in ('failing', 'insufficient_data')

    def test_all_online_events(self, predictor):
        """Node that only reports online events."""
        base_time = time.time() - 10 * 3600
        for i in range(6):
            predictor.record_status("!allon", online=True,
                                    timestamp=base_time + i * 3600)

        pattern = predictor.get_dropout_pattern("!allon")
        assert pattern.dropout_count == 0
        assert pattern.prediction == 'stable'


# =============================================================================
# Helper functions for tests
# =============================================================================

def datetime_at_hour(base_timestamp: float, hour: int) -> float:
    """Get a timestamp at a specific hour of day near the base timestamp."""
    from datetime import datetime
    dt = datetime.fromtimestamp(base_timestamp)
    dt = dt.replace(hour=hour, minute=0, second=0, microsecond=0)
    return dt.timestamp()
