"""
Tests for Predictive Analytics System (Sprint B: Predictive Network Health)

Tests the PredictiveAnalyzer class and its integration with the diagnostic engine.
"""

import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from utils.analytics import (
    AnalyticsStore,
    LinkBudgetSample,
    NetworkHealthMetrics,
    PredictiveAlert,
    PredictiveAnalyzer,
)


class TestPredictiveAlert:
    """Tests for PredictiveAlert dataclass."""

    def test_predictive_alert_creation(self):
        """Test creating a PredictiveAlert."""
        alert = PredictiveAlert(
            alert_type='snr_degradation',
            severity='warning',
            message='Test alert',
            predicted_time_hours=24.0,
            confidence=0.85,
            evidence=['Evidence 1'],
            suggestions=['Suggestion 1'],
            affected_nodes=['node1', 'node2'],
        )

        assert alert.alert_type == 'snr_degradation'
        assert alert.severity == 'warning'
        assert alert.confidence == 0.85
        assert len(alert.evidence) == 1
        assert len(alert.affected_nodes) == 2
        assert alert.timestamp  # Auto-generated

    def test_predictive_alert_auto_timestamp(self):
        """Test that timestamp is auto-generated if not provided."""
        alert = PredictiveAlert(
            alert_type='test',
            severity='info',
            message='Test',
            predicted_time_hours=None,
            confidence=0.5,
            evidence=[],
            suggestions=[],
            affected_nodes=[],
        )

        assert alert.timestamp != ""
        # Should be a valid ISO format timestamp
        datetime.fromisoformat(alert.timestamp)


class TestPredictiveAnalyzer:
    """Tests for PredictiveAnalyzer class."""

    @pytest.fixture
    def temp_db(self):
        """Create a temporary database for testing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test_analytics.db"
            yield db_path

    @pytest.fixture
    def analyzer_with_data(self, temp_db):
        """Create analyzer with sample degrading data."""
        store = AnalyticsStore(db_path=temp_db)

        # Add network health data showing SNR degradation
        base_time = datetime.now() - timedelta(hours=48)
        for i in range(20):
            # SNR decreasing from 10 to -5 over time
            snr = 10 - (i * 0.75)
            metrics = NetworkHealthMetrics(
                timestamp=(base_time + timedelta(hours=i * 2)).isoformat(),
                online_nodes=10 - (i // 4),  # Slight decline
                offline_nodes=i // 4,
                avg_rssi_dbm=-80 - (i * 0.5),
                avg_snr_db=snr,
                avg_link_quality_pct=90 - (i * 2),
                packet_success_rate=0.95 - (i * 0.02),
                uptime_hours=i * 2,
            )
            store.record_network_health(metrics)

        return PredictiveAnalyzer(store=store)

    @pytest.fixture
    def analyzer_with_stable_data(self, temp_db):
        """Create analyzer with stable (non-degrading) data."""
        store = AnalyticsStore(db_path=temp_db)

        # Add stable network health data
        base_time = datetime.now() - timedelta(hours=48)
        for i in range(20):
            metrics = NetworkHealthMetrics(
                timestamp=(base_time + timedelta(hours=i * 2)).isoformat(),
                online_nodes=10,
                offline_nodes=0,
                avg_rssi_dbm=-75,
                avg_snr_db=5.0,  # Stable SNR
                avg_link_quality_pct=95,
                packet_success_rate=0.98,
                uptime_hours=i * 2,
            )
            store.record_network_health(metrics)

        return PredictiveAnalyzer(store=store)

    @pytest.fixture
    def analyzer_empty(self, temp_db):
        """Create analyzer with no data."""
        store = AnalyticsStore(db_path=temp_db)
        return PredictiveAnalyzer(store=store)

    def test_analyze_all_returns_list(self, analyzer_empty):
        """Test that analyze_all always returns a list."""
        result = analyzer_empty.analyze_all()
        assert isinstance(result, list)

    def test_analyze_all_with_insufficient_data(self, analyzer_empty):
        """Test that no alerts are generated with insufficient data."""
        alerts = analyzer_empty.analyze_all()
        assert len(alerts) == 0

    def test_analyze_detects_snr_degradation(self, analyzer_with_data):
        """Test that analyzer detects SNR degradation."""
        alerts = analyzer_with_data.analyze_all()

        # Should detect degradation
        snr_alerts = [a for a in alerts if 'SNR' in a.message or 'snr' in a.alert_type]
        assert len(snr_alerts) > 0

    def test_analyze_stable_network_no_alerts(self, analyzer_with_stable_data):
        """Test that stable network produces no degradation alerts."""
        alerts = analyzer_with_stable_data.analyze_all()

        # No degradation alerts for stable data
        degradation_alerts = [a for a in alerts if a.alert_type == 'metric_degradation']
        assert len(degradation_alerts) == 0

    def test_alerts_sorted_by_severity(self, analyzer_with_data):
        """Test that alerts are sorted by severity (critical first)."""
        alerts = analyzer_with_data.analyze_all()

        if len(alerts) > 1:
            severity_order = {'critical': 0, 'warning': 1, 'info': 2}
            for i in range(len(alerts) - 1):
                current_order = severity_order.get(alerts[i].severity, 3)
                next_order = severity_order.get(alerts[i + 1].severity, 3)
                assert current_order <= next_order

    def test_get_network_forecast_insufficient_data(self, analyzer_empty):
        """Test forecast with insufficient data."""
        forecast = analyzer_empty.get_network_forecast()

        assert forecast['has_forecast'] is False
        assert 'reason' in forecast

    def test_get_network_forecast_with_data(self, analyzer_with_data):
        """Test forecast with sufficient data."""
        forecast = analyzer_with_data.get_network_forecast(hours_ahead=24)

        assert forecast['has_forecast'] is True
        assert 'current' in forecast
        assert 'forecast' in forecast
        assert 'trends' in forecast
        assert 'outlook' in forecast
        assert 'confidence' in forecast

    def test_calculate_trend_slope_stable(self):
        """Test trend calculation for stable values."""
        analyzer = PredictiveAnalyzer()
        values = [5.0, 5.0, 5.0, 5.0, 5.0]
        slope = analyzer._calculate_trend_slope(values)
        assert abs(slope) < 0.01  # Nearly zero

    def test_calculate_trend_slope_increasing(self):
        """Test trend calculation for increasing values."""
        analyzer = PredictiveAnalyzer()
        values = [1.0, 2.0, 3.0, 4.0, 5.0]
        slope = analyzer._calculate_trend_slope(values)
        assert slope > 0  # Positive slope

    def test_calculate_trend_slope_decreasing(self):
        """Test trend calculation for decreasing values."""
        analyzer = PredictiveAnalyzer()
        values = [5.0, 4.0, 3.0, 2.0, 1.0]
        slope = analyzer._calculate_trend_slope(values)
        assert slope < 0  # Negative slope

    def test_calculate_trend_slope_insufficient_data(self):
        """Test trend calculation with insufficient data."""
        analyzer = PredictiveAnalyzer()
        assert analyzer._calculate_trend_slope([]) == 0.0
        assert analyzer._calculate_trend_slope([5.0]) == 0.0


class TestLinkDegradationAnalysis:
    """Tests for link-specific degradation analysis."""

    @pytest.fixture
    def analyzer_with_link_data(self, temp_db):
        """Create analyzer with link budget data showing degradation."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test_analytics.db"
            store = AnalyticsStore(db_path=db_path)

            # Add link budget samples showing degradation
            base_time = datetime.now() - timedelta(hours=168)  # 1 week
            for i in range(20):
                sample = LinkBudgetSample(
                    timestamp=(base_time + timedelta(hours=i * 8)).isoformat(),
                    source_node='node_a',
                    dest_node='node_b',
                    rssi_dbm=-80 - (i * 1),  # Degrading RSSI
                    snr_db=10 - (i * 0.8),  # Degrading SNR
                    distance_km=5.0,
                    packet_loss_pct=i * 1.5,  # Increasing packet loss
                    link_quality='good' if i < 10 else 'fair',
                )
                store.record_link_budget(sample)

            yield PredictiveAnalyzer(store=store)

    def test_detects_link_degradation(self, analyzer_with_link_data):
        """Test that link degradation is detected."""
        alerts = analyzer_with_link_data.analyze_all()

        link_alerts = [a for a in alerts if 'link' in a.alert_type.lower()]
        assert len(link_alerts) > 0

    def test_link_alert_contains_node_info(self, analyzer_with_link_data):
        """Test that link alerts include affected nodes."""
        alerts = analyzer_with_link_data.analyze_all()

        link_alerts = [a for a in alerts if 'link' in a.alert_type.lower()]
        if link_alerts:
            for alert in link_alerts:
                assert len(alert.affected_nodes) >= 1


class TestDiagnosticEngineIntegration:
    """Tests for diagnostic engine integration with predictive analytics."""

    def test_category_predictive_exists(self):
        """Test that PREDICTIVE category exists in diagnostic engine."""
        from utils.diagnostic_engine import Category
        assert hasattr(Category, 'PREDICTIVE')
        assert Category.PREDICTIVE.value == 'predictive'

    @patch('utils.diagnostic_engine._get_predictive_analyzer')
    def test_check_predictive_alerts_returns_list(self, mock_get_analyzer):
        """Test that check_predictive_alerts returns a list."""
        from utils.diagnostic_engine import DiagnosticEngine

        mock_analyzer = MagicMock()
        mock_analyzer.analyze_all.return_value = []
        mock_get_analyzer.return_value = mock_analyzer

        engine = DiagnosticEngine(persist_history=False)
        result = engine.check_predictive_alerts()

        assert isinstance(result, list)

    @patch('utils.diagnostic_engine._get_predictive_analyzer')
    def test_check_predictive_alerts_converts_to_diagnosis(self, mock_get_analyzer):
        """Test that predictive alerts are converted to Diagnosis objects."""
        from utils.diagnostic_engine import DiagnosticEngine, Category, Severity

        mock_alert = PredictiveAlert(
            alert_type='link_snr_degradation',
            severity='warning',
            message='Test link degradation',
            predicted_time_hours=12.0,
            confidence=0.8,
            evidence=['Evidence 1'],
            suggestions=['Fix it'],
            affected_nodes=['node1'],
        )

        mock_analyzer = MagicMock()
        mock_analyzer.analyze_all.return_value = [mock_alert]
        mock_get_analyzer.return_value = mock_analyzer

        engine = DiagnosticEngine(persist_history=False)
        diagnoses = engine.check_predictive_alerts()

        assert len(diagnoses) == 1
        diagnosis = diagnoses[0]
        assert diagnosis.symptom.category == Category.PREDICTIVE
        assert diagnosis.symptom.severity == Severity.WARNING
        assert 'Test link degradation' in diagnosis.symptom.message
        assert diagnosis.confidence == 0.8

    @patch('utils.diagnostic_engine._get_predictive_analyzer')
    def test_check_predictive_alerts_handles_import_error(self, mock_get_analyzer):
        """Test graceful handling when analytics module unavailable."""
        from utils.diagnostic_engine import DiagnosticEngine

        mock_get_analyzer.side_effect = ImportError("Module not found")

        engine = DiagnosticEngine(persist_history=False)
        # Should not raise, returns empty list
        result = engine.check_predictive_alerts()
        assert result == []

    def test_get_network_forecast_from_engine(self):
        """Test get_network_forecast method on engine."""
        from utils.diagnostic_engine import DiagnosticEngine

        engine = DiagnosticEngine(persist_history=False)
        forecast = engine.get_network_forecast()

        # Should return a dict (either with forecast or reason for no forecast)
        assert isinstance(forecast, dict)
        assert 'has_forecast' in forecast or 'reason' in forecast


class TestPredictiveThresholds:
    """Tests for predictive analysis thresholds."""

    def test_snr_degradation_threshold(self):
        """Test SNR degradation threshold constant."""
        assert PredictiveAnalyzer.SNR_DEGRADATION_THRESHOLD == -3.0

    def test_snr_critical_threshold(self):
        """Test SNR critical threshold constant."""
        assert PredictiveAnalyzer.SNR_CRITICAL_THRESHOLD == -10.0

    def test_packet_loss_thresholds(self):
        """Test packet loss threshold constants."""
        assert PredictiveAnalyzer.PACKET_LOSS_WARNING == 10.0
        assert PredictiveAnalyzer.PACKET_LOSS_CRITICAL == 25.0

    def test_min_samples_threshold(self):
        """Test minimum samples threshold."""
        assert PredictiveAnalyzer.MIN_SAMPLES_FOR_PREDICTION >= 3


class TestNodeCountDecline:
    """Tests for node count decline detection."""

    def test_check_node_count_decline_significant(self):
        """Test detection of significant node count decline."""
        analyzer = PredictiveAnalyzer()

        # 50% decline (10 -> 5)
        counts = [5, 5, 5, 5, 5, 10, 10, 10, 10, 10]
        alert = analyzer._check_node_count_decline(counts)

        assert alert is not None
        assert alert.alert_type == 'node_count_decline'
        assert alert.severity in ['warning', 'critical']

    def test_check_node_count_decline_stable(self):
        """Test no alert for stable node count."""
        analyzer = PredictiveAnalyzer()

        # Stable count
        counts = [10, 10, 10, 10, 10, 10, 10, 10, 10, 10]
        alert = analyzer._check_node_count_decline(counts)

        assert alert is None

    def test_check_node_count_decline_insufficient_data(self):
        """Test with insufficient data points."""
        analyzer = PredictiveAnalyzer()

        counts = [10, 9]  # Too few samples
        alert = analyzer._check_node_count_decline(counts)

        assert alert is None


# Fixture for temp_db used by multiple test classes
@pytest.fixture
def temp_db():
    """Create a temporary database for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test_analytics.db"
        yield db_path
