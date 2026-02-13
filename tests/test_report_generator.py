"""
Tests for Network Status Report Generator.

Tests cover:
- Report generation with default config
- Report generation with custom config
- Individual section generation
- Report saving to file
- Graceful handling of missing modules
- Markdown format correctness
- Report with populated data

Run with: pytest tests/test_report_generator.py -v
"""

import pytest
import sys
import os
import tempfile
import time
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from utils.report_generator import (
    ReportGenerator, ReportConfig, ReportSection,
    generate_report, save_report, generate_and_save,
    _score_status, _get_hostname, _get_python_version,
)


@pytest.fixture
def config():
    """Default report config."""
    return ReportConfig()


@pytest.fixture
def generator(config):
    """Report generator with default config."""
    return ReportGenerator(config)


class TestReportConfig:
    """Test report configuration."""

    def test_default_config(self):
        config = ReportConfig()
        assert config.include_health is True
        assert config.include_signals is True
        assert config.include_diagnostics is True
        assert config.include_maintenance is True
        assert config.include_rf_analysis is True
        assert config.include_recommendations is True
        assert config.include_metadata is True

    def test_custom_title(self):
        config = ReportConfig(title="My Custom Report")
        assert config.title == "My Custom Report"

    def test_selective_sections(self):
        config = ReportConfig(
            include_health=True,
            include_signals=False,
            include_diagnostics=False,
            include_maintenance=False,
            include_rf_analysis=False,
            include_recommendations=False,
            include_metadata=False,
        )
        gen = ReportGenerator(config)
        report = gen.generate()
        assert "Network Health" in report
        assert "Signal Quality" not in report
        assert "Diagnostics" not in report


class TestReportGeneration:
    """Test basic report generation."""

    def test_generate_produces_string(self, generator):
        report = generator.generate()
        assert isinstance(report, str)
        assert len(report) > 0

    def test_report_has_title(self, generator):
        report = generator.generate()
        assert "MeshForge Network Status Report" in report

    def test_report_has_timestamp(self, generator):
        report = generator.generate()
        assert "Generated:" in report

    def test_report_has_health_section(self, generator):
        report = generator.generate()
        assert "## Network Health" in report

    def test_report_has_signal_section(self, generator):
        report = generator.generate()
        assert "## Signal Quality" in report

    def test_report_has_maintenance_section(self, generator):
        report = generator.generate()
        assert "## Predictive Maintenance" in report

    def test_report_has_diagnostics_section(self, generator):
        report = generator.generate()
        assert "## Diagnostics" in report

    def test_report_has_rf_section(self, generator):
        report = generator.generate()
        assert "## RF Analysis" in report

    def test_report_has_recommendations_section(self, generator):
        report = generator.generate()
        assert "## Recommendations" in report

    def test_report_has_metadata_section(self, generator):
        report = generator.generate()
        assert "## Report Metadata" in report
        assert "MeshForge Version" in report
        assert "Python" in report

    def test_report_is_valid_markdown(self, generator):
        """Report should have proper markdown headings."""
        report = generator.generate()
        lines = report.split("\n")
        heading_lines = [l for l in lines if l.startswith("#")]
        assert len(heading_lines) >= 7  # Title + 6 sections
        assert any(l.startswith("# ") for l in heading_lines)  # H1 title
        assert any(l.startswith("## ") for l in heading_lines)  # H2 sections


class TestReportWithData:
    """Test report generation with populated data sources."""

    def test_report_with_health_scorer(self):
        """Report should include health data when scorer has data."""
        # Populate health scorer via the singleton
        import utils.health_score as hs

        scorer = hs.HealthScorer()
        scorer.report_node_metrics("!test1", snr=-5.0, rssi=-90)
        scorer.report_node_metrics("!test2", snr=-8.0, rssi=-100)
        scorer.report_service_status("meshtasticd", running=True)

        # Inject into health_score module singleton
        old_scorer = hs._health_scorer
        hs._health_scorer = scorer
        try:
            report = generate_report()
            assert "Overall Score" in report
            assert "/100" in report
        finally:
            hs._health_scorer = old_scorer

    def test_report_with_signal_data(self):
        """Report should include signal data when manager has data."""
        from utils.signal_trending import SignalTrendingManager
        import utils.report_generator as rg

        manager = SignalTrendingManager()
        base_time = time.time() - 3600
        for i in range(5):
            manager.add_sample("!node1", base_time + i * 600,
                               snr=-5.0 + i * 0.1, rssi=-90)

        old_manager = rg._signal_manager
        rg._signal_manager = manager
        try:
            report = generate_report()
            assert "Tracking" in report
            assert "!node1" in report
        finally:
            rg._signal_manager = old_manager

    def test_report_with_maintenance_data(self):
        """Report should include maintenance data."""
        from utils.predictive_maintenance import MaintenancePredictor
        import utils.report_generator as rg

        predictor = MaintenancePredictor()
        base_time = time.time() - 5 * 3600
        for i in range(6):
            predictor.record_battery("!solar1", 70.0 - i * 5.0,
                                     timestamp=base_time + i * 3600)

        old_predictor = rg._maintenance_predictor
        rg._maintenance_predictor = predictor
        try:
            report = generate_report()
            assert "Battery Status" in report
            assert "!solar1" in report
        finally:
            rg._maintenance_predictor = old_predictor

    def test_report_with_rf_analysis(self):
        """Report should include RF preset data."""
        report = generate_report(ReportConfig(
            include_health=False,
            include_signals=False,
            include_diagnostics=False,
            include_maintenance=False,
            include_rf_analysis=True,
            include_recommendations=False,
            include_metadata=False,
        ))
        assert "LONG_FAST" in report
        assert "Sensitivity" in report


class TestReportSaving:
    """Test report file saving."""

    def test_save_report_to_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "test_report.md")
            report = "# Test Report\n\nContent here."
            result = save_report(report, path)
            assert os.path.exists(result)
            with open(result) as f:
                assert f.read() == report

    def test_save_creates_directories(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "subdir", "nested", "report.md")
            save_report("test", path)
            assert os.path.exists(path)

    def test_generate_and_save(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "full_report.md")
            result = generate_and_save(path=path)
            assert os.path.exists(result)
            content = Path(result).read_text()
            assert "MeshForge Network Status Report" in content

    def test_generate_and_save_default_path(self):
        """Default path uses get_real_user_home and timestamps."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mock_home = Path(tmpdir)
            with patch('utils.paths.get_real_user_home',
                       return_value=mock_home):
                result = generate_and_save()  # path=None triggers default
                assert os.path.exists(result)
                assert "status_report_" in result
                assert result.endswith(".md")
                # Should be under .config/meshforge/reports/
                assert "meshforge" in result
                assert "reports" in result


class TestConvenienceFunctions:
    """Test module-level convenience functions."""

    def test_generate_report_no_args(self):
        report = generate_report()
        assert isinstance(report, str)
        assert "MeshForge" in report

    def test_generate_report_with_config(self):
        config = ReportConfig(title="Custom Title")
        report = generate_report(config)
        assert "Custom Title" in report

    def test_score_status_good(self):
        assert _score_status(90) == "Good"
        assert _score_status(80) == "Good"

    def test_score_status_fair(self):
        assert _score_status(70) == "Fair"
        assert _score_status(60) == "Fair"

    def test_score_status_degraded(self):
        assert _score_status(50) == "Degraded"
        assert _score_status(40) == "Degraded"

    def test_score_status_critical(self):
        assert _score_status(30) == "Critical"
        assert _score_status(0) == "Critical"

    def test_get_hostname(self):
        hostname = _get_hostname()
        assert isinstance(hostname, str)
        assert len(hostname) > 0

    def test_get_python_version(self):
        version = _get_python_version()
        assert "." in version
        parts = version.split(".")
        assert len(parts) == 3


class TestGracefulDegradation:
    """Test that report handles missing modules gracefully."""

    def test_report_without_any_data(self):
        """Report should generate even with no data populated."""
        import utils.report_generator as rg
        import utils.health_score as hs
        old_health = hs._health_scorer
        old_signal = rg._signal_manager
        old_maint = rg._maintenance_predictor

        hs._health_scorer = None
        rg._signal_manager = None
        rg._maintenance_predictor = None
        try:
            report = generate_report()
            assert isinstance(report, str)
            assert "MeshForge" in report
            # Should have placeholder text for empty sections
            assert "not initialized" in report or "not available" in report or "No" in report
        finally:
            hs._health_scorer = old_health
            rg._signal_manager = old_signal
            rg._maintenance_predictor = old_maint

    def test_report_section_ordering(self):
        """Sections should appear in correct order."""
        report = generate_report()
        lines = report.split("\n")
        heading_positions = {}
        for i, line in enumerate(lines):
            if line.startswith("## "):
                heading_positions[line.strip("# ").strip()] = i

        # Health before Signal before Maintenance before Diagnostics
        if "Network Health" in heading_positions and "Signal Quality" in heading_positions:
            assert heading_positions["Network Health"] < heading_positions["Signal Quality"]
        if "Signal Quality" in heading_positions and "Predictive Maintenance" in heading_positions:
            assert heading_positions["Signal Quality"] < heading_positions["Predictive Maintenance"]


class TestReportSection:
    """Test ReportSection dataclass."""

    def test_section_creation(self):
        section = ReportSection(
            heading="Test",
            level=2,
            content="Some content",
            order=10,
        )
        assert section.heading == "Test"
        assert section.level == 2
        assert section.content == "Some content"
        assert section.order == 10

    def test_section_default_order(self):
        section = ReportSection(heading="X", level=2, content="Y")
        assert section.order == 0
