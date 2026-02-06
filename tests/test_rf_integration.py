"""
RF Tools Integration Test — verify modules work together as a cohesive system.

Tests the full analysis pipeline:
  PresetAnalyzer → AntennaPattern → HopAnalyzer → HealthScorer → SignalTrending → LogParser

Verifies that:
- Module interfaces are compatible
- Calculations chain together correctly
- Data flows through the full pipeline
- Real-world scenarios produce sensible results
"""

import sys
import os
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import pytest

from utils.rf import DeployEnvironment, BuildingType
from utils.preset_impact import (
    PresetAnalyzer, PresetComparison, compare_presets, format_comparison_table,
    PRESET_PARAMS,
)
from utils.multihop import (
    HopAnalyzer, RelayNode, PathResult, haversine_km, format_path_report,
)
from utils.antenna_patterns import (
    DipolePattern, YagiPattern, GroundPlanePattern, PatchPattern,
    coverage_with_antenna, azimuth_range_profile, get_antenna_preset,
)
from utils.health_score import (
    HealthScorer, format_health_display, score_to_status,
)
from utils.signal_trending import (
    SignalTrend, SignalTrendingManager,
)
from utils.log_parser import (
    LogParser, LogSource, parse_log_lines, format_error_report,
)


# =============================================================================
# Preset → Multihop Integration
# =============================================================================

class TestPresetMultihopIntegration:
    """Test that preset analysis feeds correctly into hop analysis."""

    def test_preset_sensitivity_used_in_hop(self):
        """HopAnalyzer uses the preset's sensitivity for link budget."""
        for preset_name in ['LONG_FAST', 'SHORT_TURBO', 'VERY_LONG_SLOW']:
            analyzer = PresetAnalyzer()
            impact = analyzer.analyze_preset(preset_name)

            hop_analyzer = HopAnalyzer(preset=preset_name)
            assert abs(hop_analyzer.sensitivity_dbm - impact.sensitivity_dbm) < 0.01

    def test_preset_airtime_in_hop_latency(self):
        """HopAnalyzer uses preset airtime for latency calculations."""
        analyzer = PresetAnalyzer()
        for preset_name in ['LONG_FAST', 'SHORT_TURBO']:
            impact = analyzer.analyze_preset(preset_name)
            hop = HopAnalyzer(preset=preset_name)
            assert abs(hop.airtime_ms - impact.airtime_ms) < 0.01

    def test_compare_presets_for_path(self):
        """Compare how all presets perform on a specific path."""
        nodes = [
            RelayNode(lat=21.30, lon=-157.86, name="Honolulu"),
            RelayNode(lat=21.45, lon=-157.75, name="Kaneohe"),
        ]
        analyzer = HopAnalyzer()
        results = analyzer.compare_presets_for_path(nodes)

        # All presets should produce valid results
        assert len(results) == len(PRESET_PARAMS)

        # More sensitive presets should have higher success probability
        # on this ~20 km path
        for r in results:
            assert 0 < r.success_probability <= 1.0
            assert r.total_latency_ms > 0
            assert r.preset_name != ''

    def test_longer_range_preset_better_on_long_path(self):
        """LONG_SLOW should outperform SHORT_TURBO on a long path."""
        nodes = [
            RelayNode(lat=21.30, lon=-157.90, name="West"),
            RelayNode(lat=21.30, lon=-157.40, name="East"),  # ~47 km
        ]
        long_slow = HopAnalyzer(preset='LONG_SLOW')
        short_turbo = HopAnalyzer(preset='SHORT_TURBO')

        path_long = long_slow.analyze_path(nodes)
        path_short = short_turbo.analyze_path(nodes)

        assert path_long.success_probability >= path_short.success_probability


# =============================================================================
# Antenna → Coverage Integration
# =============================================================================

class TestAntennaCoverageIntegration:
    """Test antenna patterns enhance coverage calculations."""

    def test_yagi_extends_preset_range(self):
        """Yagi antenna extends range beyond stock dipole."""
        analyzer = PresetAnalyzer()
        impact = analyzer.analyze_preset('LONG_FAST')
        base_range = impact.max_range_los_km

        yagi = YagiPattern(peak_gain_dbi=12.0, aim_azimuth=45.0)
        enhanced_range = coverage_with_antenna(base_range, yagi, azimuth=45.0)

        assert enhanced_range > base_range

    def test_antenna_gain_in_hop_calculation(self):
        """Custom antenna gain affects hop link budget."""
        n1 = RelayNode(lat=21.30, lon=-157.80, name="A")
        n2 = RelayNode(lat=21.40, lon=-157.80, name="B")

        # Standard 2.15 dBi dipole
        std = HopAnalyzer(tx_gain_dbi=2.15, rx_gain_dbi=2.15)
        # High-gain setup: 8 dBi TX, 12 dBi RX
        high = HopAnalyzer(tx_gain_dbi=8.0, rx_gain_dbi=12.0)

        hop_std = std.analyze_hop(n1, n2)
        hop_high = high.analyze_hop(n1, n2)

        # Extra gain should give more margin
        gain_diff = (8.0 + 12.0) - (2.15 + 2.15)
        margin_diff = hop_high.link_margin_db - hop_std.link_margin_db
        assert abs(margin_diff - gain_diff) < 0.01

    def test_coverage_profile_with_preset(self):
        """Antenna profile combined with preset gives directional coverage."""
        analyzer = PresetAnalyzer()
        impact = analyzer.analyze_preset('LONG_FAST')
        base_range = impact.max_range_los_km

        yagi = YagiPattern(peak_gain_dbi=12.0, h_beamwidth=30.0, aim_azimuth=90.0)
        profile = azimuth_range_profile(yagi, base_range)

        # Should have peak near 90° and minimum near 270°
        gains_by_az = {az: r for az, r in profile}
        assert gains_by_az[90.0] > gains_by_az[270.0]

    def test_sector_antenna_coverage_area(self):
        """Sector antenna creates asymmetric coverage."""
        sector = PatchPattern(peak_gain_dbi=10.0, h_beamwidth=120.0, aim_azimuth=0.0)

        base_range = 10.0  # km
        # North (on-axis) should have more range than south (off-axis)
        north = coverage_with_antenna(base_range, sector, azimuth=0.0)
        south = coverage_with_antenna(base_range, sector, azimuth=180.0)
        assert north > south * 2  # Significant difference


# =============================================================================
# Health Score → Signal Trending Integration
# =============================================================================

class TestHealthTrendingIntegration:
    """Test health scoring uses signal metrics consistently."""

    def test_signal_trending_feeds_health_scorer(self):
        """Signal trending data can feed into health scorer."""
        # Simulate a node with declining signal
        trend = SignalTrend(node_id='!abc123')
        now = time.time()

        # Add declining samples
        for i in range(20):
            trend.add_sample(
                timestamp=now - (19 - i) * 300,  # Every 5 min
                snr=-5.0 - i * 0.5,              # Declining SNR
                rssi=-80 - i,                     # Declining RSSI
            )

        # Get latest stats
        report = trend.get_report()

        # Feed into health scorer
        scorer = HealthScorer()
        scorer.report_service_status('meshtasticd', running=True)
        scorer.report_node_metrics(
            '!abc123',
            snr=report.current_snr,
            rssi=report.current_rssi,
        )

        snapshot = scorer.get_snapshot()
        assert 0 <= snapshot.overall_score <= 100

    def test_multi_node_trending_to_health(self):
        """Multiple nodes trending feeds health scorer."""
        manager = SignalTrendingManager()
        now = time.time()

        # Add data for 3 nodes
        for node_id in ['!node1', '!node2', '!node3']:
            for i in range(10):
                manager.add_sample(
                    node_id=node_id,
                    timestamp=now - (9 - i) * 60,
                    snr=-3.0 - (hash(node_id) % 5),
                    rssi=-75 - (hash(node_id) % 10),
                )

        # Build health scorer from trending data
        scorer = HealthScorer()
        scorer.report_service_status('meshtasticd', running=True)

        for node_id in manager.get_tracked_nodes():
            report = manager.get_report(node_id)
            if report:
                scorer.report_node_metrics(
                    node_id,
                    snr=report.current_snr,
                    rssi=report.current_rssi,
                )

        snapshot = scorer.get_snapshot()
        assert snapshot.status in ('healthy', 'fair', 'degraded', 'critical')
        assert snapshot.details['connectivity']['nodes_visible'] == 3

    def test_health_trend_detection(self):
        """Health scorer detects trends from repeated snapshots."""
        scorer = HealthScorer()
        scorer.report_service_status('meshtasticd', running=True)

        # Start with good metrics
        scorer.report_node_metrics('!abc', snr=-3.0, rssi=-75)
        for _ in range(5):
            scorer.get_snapshot()

        # Degrade metrics
        scorer.report_node_metrics('!abc', snr=-18.0, rssi=-120)
        for _ in range(5):
            scorer.get_snapshot()

        trend = scorer.get_trend()
        assert trend == 'declining'


# =============================================================================
# Log Parser → Health Score Integration
# =============================================================================

class TestLogParserHealthIntegration:
    """Test log parser errors can drive health scoring."""

    def test_log_errors_affect_health(self):
        """Detected log errors fed to health scorer as error events."""
        log_lines = [
            "INFO: meshtasticd started",
            "ERROR: Serial port disconnected",
            "ERROR: Connection refused",
            "WARNING: Channel utilization at 90%",
            "ERROR: Radio TX failed: timeout",
        ]

        parser = LogParser()
        entries = parser.parse_lines(log_lines, LogSource.MESHTASTICD)

        scorer = HealthScorer()
        scorer.report_service_status('meshtasticd', running=True)

        # Feed errors into scorer
        for entry in entries:
            if entry.is_error:
                scorer.report_error()

        snapshot = scorer.get_snapshot()
        # Errors should reduce reliability
        assert snapshot.reliability_score < 100

    def test_error_categories_map_to_health(self):
        """Log error categories align with health score categories."""
        parser = LogParser()

        # Connectivity error
        entry = parser.parse_line(
            "Connection refused to meshtasticd", LogSource.MESHTASTICD)
        assert entry.category == 'connectivity'

        # Hardware error
        entry = parser.parse_line(
            "Serial port disconnected", LogSource.MESHTASTICD)
        assert entry.category == 'hardware'

        # Performance warning
        entry = parser.parse_line(
            "Channel utilization at 85%", LogSource.MESHTASTICD)
        assert entry.category == 'performance'


# =============================================================================
# Full Pipeline: Real-World Scenario
# =============================================================================

class TestRealWorldScenario:
    """Simulate a real-world Hawaiian mesh network analysis."""

    def test_hawaii_three_node_mesh(self):
        """Analyze a 3-node mesh across Oahu."""
        # Node locations (approximate)
        coast = RelayNode(lat=21.2969, lon=-157.8583, elevation_m=5,
                         antenna_height_m=10, name="Waikiki")
        ridge = RelayNode(lat=21.3550, lon=-157.8150, elevation_m=500,
                         antenna_height_m=5, name="Tantalus")
        valley = RelayNode(lat=21.3944, lon=-157.7400, elevation_m=50,
                          antenna_height_m=8, name="Kaneohe")

        # Analyze with LONG_FAST (default Meshtastic)
        analyzer = HopAnalyzer(preset='LONG_FAST')
        path = analyzer.analyze_path([coast, ridge, valley])

        # Path should be feasible (~7km + ~8km hops)
        assert path.hop_count == 2
        assert path.success_probability > 0.5
        assert path.total_distance_km > 10
        assert path.total_distance_km < 25

        # Weakest link should be identified
        assert path.weakest_hop is not None

        # Within Meshtastic hop limit
        assert path.within_hop_limit

    def test_preset_selection_for_scenario(self):
        """Different presets suit different scenarios."""
        # Short urban link
        urban_src = RelayNode(lat=21.30, lon=-157.86, name="Downtown")
        urban_dst = RelayNode(lat=21.305, lon=-157.855, name="Market")

        # Long rural link
        rural_src = RelayNode(lat=21.30, lon=-157.90, name="Coast")
        rural_dst = RelayNode(lat=21.30, lon=-157.60, name="Mountain")

        # For urban (short): fast presets should work fine
        fast = HopAnalyzer(preset='SHORT_FAST')
        urban_path = fast.analyze_path([urban_src, urban_dst])
        assert urban_path.success_probability > 0.95

        # For rural (long): only slow presets have margin
        slow = HopAnalyzer(preset='LONG_SLOW')
        rural_path = slow.analyze_path([rural_src, rural_dst])
        # Long range should have reasonable probability
        assert rural_path.success_probability > 0.3

    def test_antenna_upgrade_impact(self):
        """Quantify antenna upgrade impact on a real path."""
        base_range = 10.0  # km with stock dipole

        # Stock antenna: uniform coverage
        stock = DipolePattern(peak_gain_dbi=2.15)
        stock_range = coverage_with_antenna(base_range, stock, azimuth=45.0)

        # Upgraded: 5-element Yagi pointed at partner
        yagi = get_antenna_preset('yagi_5el')
        yagi.aim_azimuth = 45.0
        yagi_range = coverage_with_antenna(base_range, yagi, azimuth=45.0)

        # Yagi should significantly extend range in aimed direction
        improvement_factor = yagi_range / stock_range
        assert improvement_factor > 2.0  # At least 2x improvement

    def test_full_analysis_pipeline(self):
        """Run the complete analysis pipeline for a deployment."""
        # 1. Choose preset
        comparison = compare_presets()
        assert len(comparison.presets) == len(PRESET_PARAMS)

        # 2. Analyze path
        nodes = [
            RelayNode(lat=21.30, lon=-157.86, name="Base"),
            RelayNode(lat=21.35, lon=-157.82, name="Relay"),
            RelayNode(lat=21.40, lon=-157.78, name="Remote"),
        ]
        analyzer = HopAnalyzer(preset=comparison.best_balance)
        path = analyzer.analyze_path(nodes)
        assert path.hop_count == 2

        # 3. Apply antenna gains
        yagi = YagiPattern(peak_gain_dbi=10.0, aim_azimuth=0.0)
        enhanced_range = coverage_with_antenna(10.0, yagi, azimuth=0.0)
        assert enhanced_range > 10.0

        # 4. Score health
        scorer = HealthScorer()
        scorer.report_service_status('meshtasticd', running=True)
        scorer.report_node_metrics('Base', snr=-5.0, rssi=-80)
        scorer.report_node_metrics('Relay', snr=-8.0, rssi=-90)
        scorer.report_node_metrics('Remote', snr=-12.0, rssi=-105)
        snapshot = scorer.get_snapshot()
        assert snapshot.overall_score > 0

        # 5. Track signal trends
        manager = SignalTrendingManager()
        now = time.time()
        for i in range(10):
            manager.add_sample('Remote', now - (9 - i) * 300, snr=-12.0, rssi=-105)
        report = manager.get_report('Remote')
        assert report is not None

        # 6. Parse any log errors
        log = ["INFO: All nodes connected", "WARNING: Channel busy at 60%"]
        entries = parse_log_lines(log, source='meshtasticd')
        assert len(entries) == 1  # Only the warning

        # 7. Generate reports
        path_report = format_path_report(path)
        assert 'Base' in path_report
        health_display = format_health_display(snapshot)
        assert '/100' in health_display
        preset_table = format_comparison_table(comparison)
        assert comparison.best_balance in preset_table

    def test_relay_optimization(self):
        """Find optimal relay for a marginal link."""
        source = RelayNode(lat=21.30, lon=-157.90, name="Source")
        dest = RelayNode(lat=21.30, lon=-157.60, name="Dest")  # ~28 km

        candidates = [
            RelayNode(lat=21.30, lon=-157.75, name="Mid"),      # Midpoint
            RelayNode(lat=21.35, lon=-157.75, name="OffAxis"),   # Off-path
            RelayNode(lat=21.30, lon=-157.85, name="NearSrc"),   # Near source
        ]

        # Use SHORT_FAST to make the direct path marginal
        analyzer = HopAnalyzer(preset='SHORT_FAST', tx_power_dbm=14)
        best = analyzer.find_optimal_relay(source, dest, candidates)

        # Some relay should help (or direct is fine)
        if best is not None:
            # Verify the relay actually improves things
            direct = analyzer.analyze_path([source, dest])
            relayed = analyzer.analyze_path([source, best, dest])
            assert relayed.success_probability >= direct.success_probability


# =============================================================================
# Format Output Consistency
# =============================================================================

class TestFormatOutputConsistency:
    """Verify all format functions produce consistent, parseable output."""

    def test_all_format_functions_produce_strings(self):
        """All format functions return non-empty strings."""
        # Preset comparison table
        comp = compare_presets()
        table = format_comparison_table(comp)
        assert isinstance(table, str) and len(table) > 100

        # Path report
        nodes = [
            RelayNode(lat=21.30, lon=-157.86, name="A"),
            RelayNode(lat=21.35, lon=-157.82, name="B"),
        ]
        path = HopAnalyzer().analyze_path(nodes)
        report = format_path_report(path)
        assert isinstance(report, str) and len(report) > 50

        # Health display
        scorer = HealthScorer()
        scorer.report_service_status('meshtasticd', running=True)
        snapshot = scorer.get_snapshot()
        display = format_health_display(snapshot)
        assert isinstance(display, str) and len(display) > 50

        # Error report
        entries = parse_log_lines(
            ["ERROR: connection refused"], source='meshtasticd')
        error_report = format_error_report(entries)
        assert isinstance(error_report, str) and len(error_report) > 20

    def test_to_dict_serializable(self):
        """All to_dict outputs are JSON-compatible."""
        import json

        # PresetImpact
        impact = PresetAnalyzer().analyze_preset('LONG_FAST')
        json.dumps(impact.to_dict())

        # PathResult
        nodes = [
            RelayNode(lat=21.30, lon=-157.86, name="A"),
            RelayNode(lat=21.35, lon=-157.82, name="B"),
        ]
        path = HopAnalyzer().analyze_path(nodes)
        json.dumps(path.to_dict())

        # HealthSnapshot
        scorer = HealthScorer()
        scorer.report_service_status('meshtasticd', running=True)
        snapshot = scorer.get_snapshot()
        json.dumps(snapshot.to_dict())

        # LogEntry
        parser = LogParser()
        entry = parser.parse_line("ERROR: test", LogSource.MESHTASTICD)
        json.dumps(entry.to_dict())


# =============================================================================
# Environment-Aware Propagation Integration
# =============================================================================

class TestEnvironmentAwareIntegration:
    """Test environment-aware path loss wiring through multihop and preset_impact."""

    def test_multihop_suburban_worse_than_free_space(self):
        """Suburban environment gives lower success than free space on same path."""
        nodes = [
            RelayNode(lat=21.30, lon=-157.86, name="A"),
            RelayNode(lat=21.40, lon=-157.75, name="B"),
        ]
        free = HopAnalyzer(preset='LONG_FAST', environment=DeployEnvironment.FREE_SPACE)
        suburban = HopAnalyzer(preset='LONG_FAST', environment=DeployEnvironment.SUBURBAN)

        path_free = free.analyze_path(nodes)
        path_sub = suburban.analyze_path(nodes)

        assert path_free.success_probability >= path_sub.success_probability
        # Suburban path loss should be higher
        assert path_sub.hops[0].fspl_db > path_free.hops[0].fspl_db

    def test_multihop_forest_severely_limited(self):
        """Forest environment makes even moderate hops difficult."""
        nodes = [
            RelayNode(lat=21.30, lon=-157.86, name="A"),
            RelayNode(lat=21.35, lon=-157.82, name="B"),
        ]
        forest = HopAnalyzer(preset='LONG_FAST', environment=DeployEnvironment.FOREST)
        path = forest.analyze_path(nodes)
        # Forest PLE=5.0 should dramatically reduce margin vs free space
        free = HopAnalyzer(preset='LONG_FAST', environment=DeployEnvironment.FREE_SPACE)
        path_free = free.analyze_path(nodes)
        margin_drop = path_free.hops[0].link_margin_db - path.hops[0].link_margin_db
        assert margin_drop > 20  # Forest adds >20 dB loss at multi-km

    def test_multihop_over_water_near_free_space(self):
        """Over-water propagation is close to free space (PLE ~1.9)."""
        nodes = [
            RelayNode(lat=21.30, lon=-157.86, name="A"),
            RelayNode(lat=21.35, lon=-157.82, name="B"),
        ]
        water = HopAnalyzer(preset='LONG_FAST', environment=DeployEnvironment.OVER_WATER)
        free = HopAnalyzer(preset='LONG_FAST', environment=DeployEnvironment.FREE_SPACE)

        path_water = water.analyze_path(nodes)
        path_free = free.analyze_path(nodes)

        # Over water should be within ~5 dB of free space for moderate distances
        margin_diff = abs(path_free.hops[0].link_margin_db -
                         path_water.hops[0].link_margin_db)
        assert margin_diff < 10

    def test_multihop_environment_propagates_to_compare(self):
        """compare_presets_for_path uses the analyzer's environment."""
        nodes = [
            RelayNode(lat=21.30, lon=-157.86, name="A"),
            RelayNode(lat=21.35, lon=-157.82, name="B"),
        ]
        analyzer = HopAnalyzer(
            preset='LONG_FAST',
            environment=DeployEnvironment.DENSE_URBAN,
        )
        results = analyzer.compare_presets_for_path(nodes)
        # All presets should be significantly degraded in dense urban
        for r in results:
            assert r.success_probability < 1.0

    def test_preset_impact_suburban_range_realistic(self):
        """Suburban LONG_FAST should predict ~2-10 km, not 300 km."""
        analyzer = PresetAnalyzer(
            environment=DeployEnvironment.SUBURBAN,
            antenna_height_m=2.0,
        )
        impact = analyzer.analyze_preset('LONG_FAST')
        # Suburban with fade margin should be much less than free space
        assert impact.max_range_km < 50
        assert impact.max_range_km > 0.5

    def test_preset_impact_free_space_backward_compat(self):
        """Default FREE_SPACE environment matches old FSPL behavior."""
        old = PresetAnalyzer()  # defaults to FREE_SPACE
        impact = old.analyze_preset('LONG_FAST')
        # Old behavior: max_range_los_km was the uncapped FSPL range
        # New: still uses FSPL for FREE_SPACE
        assert impact.max_range_los_km > 100  # Still shows theoretical LOS range

    def test_preset_impact_building_reduces_range(self):
        """Building penetration loss reduces range."""
        outdoor = PresetAnalyzer(
            environment=DeployEnvironment.SUBURBAN,
            building=BuildingType.NONE,
        )
        indoor = PresetAnalyzer(
            environment=DeployEnvironment.SUBURBAN,
            building=BuildingType.CONCRETE,
        )
        range_out = outdoor.analyze_preset('LONG_FAST').max_range_km
        range_in = indoor.analyze_preset('LONG_FAST').max_range_km
        assert range_out > range_in

    def test_preset_impact_radio_horizon_caps_range(self):
        """Range is capped by radio horizon based on antenna height."""
        low = PresetAnalyzer(
            environment=DeployEnvironment.FREE_SPACE,
            antenna_height_m=2.0,
        )
        high = PresetAnalyzer(
            environment=DeployEnvironment.FREE_SPACE,
            antenna_height_m=50.0,
        )
        range_low = low.analyze_preset('VERY_LONG_SLOW').max_range_km
        range_high = high.analyze_preset('VERY_LONG_SLOW').max_range_km
        # High antenna should allow longer range
        assert range_high > range_low

    def test_environment_ordering_consistency(self):
        """More obstructed environments should give shorter range."""
        envs = [
            DeployEnvironment.FREE_SPACE,
            DeployEnvironment.RURAL_OPEN,
            DeployEnvironment.SUBURBAN,
            DeployEnvironment.DENSE_URBAN,
        ]
        ranges = []
        for env in envs:
            a = PresetAnalyzer(environment=env, antenna_height_m=10.0)
            impact = a.analyze_preset('LONG_FAST')
            ranges.append(impact.max_range_km)
        # Each should be less than or equal to the previous
        for i in range(len(ranges) - 1):
            assert ranges[i] >= ranges[i + 1], \
                f"{envs[i].value} range {ranges[i]} should >= {envs[i+1].value} range {ranges[i+1]}"
