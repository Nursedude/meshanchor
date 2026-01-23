"""
Tests for LoRa preset impact visualization module.

Tests cover:
- Sensitivity calculations per SF and BW
- Link budget calculation
- Max range from FSPL inversion
- Airtime calculation (LoRa PHY timing)
- Throughput calculation
- Single preset analysis (PresetImpact)
- All-preset comparison (PresetComparison)
- Coverage zones by signal quality
- Range-at-SNR calculations
- Format table output
- Edge cases and physics validation
"""

import sys
import os
import math

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import pytest

from utils.preset_impact import (
    PresetAnalyzer,
    PresetImpact,
    PresetComparison,
    PRESET_PARAMS,
    REQUIRED_SNR_DB,
    NOISE_FIGURE_DB,
    DEFAULT_TX_POWER_DBM,
    DEFAULT_FREQ_MHZ,
    compare_presets,
    format_comparison_table,
)


@pytest.fixture
def analyzer():
    """Create a default preset analyzer."""
    return PresetAnalyzer()


# =============================================================================
# Sensitivity Calculations
# =============================================================================

class TestSensitivity:
    def test_sf12_125khz_most_sensitive(self, analyzer):
        """SF12 with 125kHz BW should be the most sensitive."""
        sens_sf12 = analyzer.sensitivity(12, 125000)
        sens_sf7 = analyzer.sensitivity(7, 125000)
        assert sens_sf12 < sens_sf7  # More negative = more sensitive

    def test_wider_bw_less_sensitive(self, analyzer):
        """Wider bandwidth reduces sensitivity."""
        sens_125 = analyzer.sensitivity(10, 125000)
        sens_250 = analyzer.sensitivity(10, 250000)
        sens_500 = analyzer.sensitivity(10, 500000)
        assert sens_125 < sens_250 < sens_500

    def test_sensitivity_formula(self, analyzer):
        """Verify sensitivity formula: -174 + 10*log10(BW) + NF + req_SNR."""
        # SF10, 125kHz
        expected = -174 + 10 * math.log10(125000) + NOISE_FIGURE_DB + REQUIRED_SNR_DB[10]
        actual = analyzer.sensitivity(10, 125000)
        assert abs(actual - expected) < 0.01

    def test_sensitivity_range(self, analyzer):
        """All sensitivities should be in reasonable range (-110 to -145 dBm)."""
        for sf in range(7, 13):
            for bw in [62500, 125000, 250000, 500000]:
                sens = analyzer.sensitivity(sf, bw)
                assert -145 < sens < -110, f"SF{sf} BW{bw}: {sens} dBm out of range"

    def test_3db_per_bw_doubling(self, analyzer):
        """Doubling BW should reduce sensitivity by ~3 dB."""
        sens_125 = analyzer.sensitivity(10, 125000)
        sens_250 = analyzer.sensitivity(10, 250000)
        diff = sens_250 - sens_125
        assert abs(diff - 3.0) < 0.1  # ~3 dB for doubling BW


# =============================================================================
# Link Budget
# =============================================================================

class TestLinkBudget:
    def test_higher_sf_more_link_budget(self, analyzer):
        """Higher SF gives more link budget (more sensitive receiver)."""
        impact_sf7 = analyzer.analyze_preset('SHORT_FAST')  # SF7
        impact_sf12 = analyzer.analyze_preset('LONG_SLOW')  # SF12
        assert impact_sf12.link_budget_db > impact_sf7.link_budget_db

    def test_link_budget_increases_with_power(self):
        """Higher TX power increases link budget."""
        low = PresetAnalyzer(tx_power_dbm=14)
        high = PresetAnalyzer(tx_power_dbm=22)
        lb_low = low.analyze_preset('LONG_FAST').link_budget_db
        lb_high = high.analyze_preset('LONG_FAST').link_budget_db
        assert lb_high - lb_low == 8.0  # 8 dB more

    def test_link_budget_positive(self, analyzer):
        """Link budget should always be positive (signals travel some distance)."""
        for name in PRESET_PARAMS:
            impact = analyzer.analyze_preset(name)
            assert impact.link_budget_db > 0


# =============================================================================
# Max Range
# =============================================================================

class TestMaxRange:
    def test_range_increases_with_sf(self, analyzer):
        """Higher SF presets should have longer range."""
        range_sf7 = analyzer.analyze_preset('SHORT_FAST').max_range_los_km
        range_sf10 = analyzer.analyze_preset('MEDIUM_FAST').max_range_los_km
        range_sf12 = analyzer.analyze_preset('LONG_SLOW').max_range_los_km
        assert range_sf7 < range_sf10 < range_sf12

    def test_range_positive(self, analyzer):
        """All ranges should be positive."""
        for name in PRESET_PARAMS:
            impact = analyzer.analyze_preset(name)
            assert impact.max_range_km > 0

    def test_range_reasonable(self, analyzer):
        """Ranges should be within physically plausible bounds."""
        for name in PRESET_PARAMS:
            impact = analyzer.analyze_preset(name)
            # Even best LoRa rarely exceeds 200 km (FSPL only, no terrain)
            assert impact.max_range_km <= 200.0
            # Even worst preset should reach a few km
            assert impact.max_range_km > 1.0

    def test_fspl_inversion(self, analyzer):
        """max_range_fspl should be inverse of FSPL calculation."""
        # At 10 km, 907 MHz: FSPL = 20*log10(10000) + 20*log10(907) - 27.55
        fspl_10km = 20 * math.log10(10000) + 20 * math.log10(907) - 27.55
        # Inverse: given this FSPL as link budget, should get ~10 km
        range_m = analyzer.max_range_fspl(fspl_10km, 907)
        assert abs(range_m - 10000) < 1.0  # Within 1 meter

    def test_coverage_area(self, analyzer):
        """Coverage area should be pi*r^2."""
        impact = analyzer.analyze_preset('LONG_FAST')
        expected_area = math.pi * impact.max_range_km ** 2
        assert abs(impact.coverage_area_km2 - expected_area) < 0.1


# =============================================================================
# Airtime Calculation
# =============================================================================

class TestAirtime:
    def test_airtime_increases_with_sf(self, analyzer):
        """Higher SF = longer airtime."""
        at_sf7 = analyzer.airtime_ms(7, 125000, 8, 50)
        at_sf10 = analyzer.airtime_ms(10, 125000, 8, 50)
        at_sf12 = analyzer.airtime_ms(12, 125000, 8, 50)
        assert at_sf7 < at_sf10 < at_sf12

    def test_airtime_decreases_with_bw(self, analyzer):
        """Wider bandwidth = shorter airtime."""
        at_125 = analyzer.airtime_ms(10, 125000, 8, 50)
        at_250 = analyzer.airtime_ms(10, 250000, 8, 50)
        at_500 = analyzer.airtime_ms(10, 500000, 8, 50)
        assert at_500 < at_250 < at_125

    def test_airtime_increases_with_payload(self, analyzer):
        """Larger payload = longer airtime."""
        at_20 = analyzer.airtime_ms(10, 250000, 8, 20)
        at_100 = analyzer.airtime_ms(10, 250000, 8, 100)
        at_200 = analyzer.airtime_ms(10, 250000, 8, 200)
        assert at_20 < at_100 < at_200

    def test_airtime_positive(self, analyzer):
        """Airtime should always be positive."""
        for name in PRESET_PARAMS:
            impact = analyzer.analyze_preset(name)
            assert impact.airtime_ms > 0

    def test_airtime_short_turbo_fastest(self, analyzer):
        """SHORT_TURBO should have shortest airtime."""
        impacts = analyzer.analyze_all()
        short_turbo = next(p for p in impacts if p.preset_name == 'SHORT_TURBO')
        for p in impacts:
            if p.preset_name != 'SHORT_TURBO':
                assert short_turbo.airtime_ms <= p.airtime_ms

    def test_airtime_reasonable_range(self, analyzer):
        """Airtimes should be within LoRa physical bounds."""
        for name in PRESET_PARAMS:
            impact = analyzer.analyze_preset(name)
            # Even fastest SF7/500kHz: ~5ms minimum for 50 byte payload
            assert impact.airtime_ms > 1.0
            # Even slowest SF12/62.5kHz: shouldn't exceed 30 seconds for 50 bytes
            assert impact.airtime_ms < 30000.0


# =============================================================================
# Throughput
# =============================================================================

class TestThroughput:
    def test_throughput_decreases_with_sf(self, analyzer):
        """Higher SF = lower throughput."""
        tp_sf7 = analyzer.throughput_bps(7, 125000, 8)
        tp_sf10 = analyzer.throughput_bps(10, 125000, 8)
        tp_sf12 = analyzer.throughput_bps(12, 125000, 8)
        assert tp_sf7 > tp_sf10 > tp_sf12

    def test_throughput_increases_with_bw(self, analyzer):
        """Wider bandwidth = higher throughput."""
        tp_125 = analyzer.throughput_bps(10, 125000, 8)
        tp_250 = analyzer.throughput_bps(10, 250000, 8)
        assert tp_250 > tp_125

    def test_throughput_positive(self, analyzer):
        """All throughputs should be positive."""
        for name in PRESET_PARAMS:
            impact = analyzer.analyze_preset(name)
            assert impact.throughput_bps > 0

    def test_throughput_formula(self, analyzer):
        """Verify throughput formula: SF * (BW / 2^SF) * (4/CR)."""
        # SF10, BW250k, CR8
        expected = 10 * (250000 / (2**10)) * (4.0 / 8)
        actual = analyzer.throughput_bps(10, 250000, 8)
        assert abs(actual - expected) < 0.01


# =============================================================================
# Preset Analysis
# =============================================================================

class TestPresetAnalysis:
    def test_all_presets_analyzed(self, analyzer):
        """analyze_all returns results for every preset."""
        results = analyzer.analyze_all()
        assert len(results) == len(PRESET_PARAMS)

    def test_unknown_preset_raises(self, analyzer):
        """Unknown preset name raises ValueError."""
        with pytest.raises(ValueError):
            analyzer.analyze_preset('NONEXISTENT')

    def test_impact_to_dict(self, analyzer):
        """PresetImpact.to_dict() returns complete structure."""
        impact = analyzer.analyze_preset('LONG_FAST')
        d = impact.to_dict()
        assert 'preset_name' in d
        assert 'sensitivity_dbm' in d
        assert 'max_range_km' in d
        assert 'airtime_ms' in d
        assert 'throughput_bps' in d
        assert d['preset_name'] == 'LONG_FAST'

    def test_duty_cycle_calculated(self, analyzer):
        """Duty cycle should be calculated for each preset."""
        for name in PRESET_PARAMS:
            impact = analyzer.analyze_preset(name)
            assert impact.duty_cycle_pct >= 0
            assert impact.duty_cycle_pct < 100

    def test_packets_per_hour(self, analyzer):
        """Packets per hour should reflect duty cycle limit."""
        impact_fast = analyzer.analyze_preset('SHORT_TURBO')
        impact_slow = analyzer.analyze_preset('LONG_SLOW')
        # Fast preset can send more packets per hour
        assert impact_fast.packets_per_hour > impact_slow.packets_per_hour


# =============================================================================
# Preset Comparison
# =============================================================================

class TestPresetComparison:
    def test_comparison_has_rankings(self, analyzer):
        """Comparison identifies best in each category."""
        comp = analyzer.compare()
        assert comp.best_range != ""
        assert comp.best_throughput != ""
        assert comp.best_balance != ""

    def test_best_range_is_slow_preset(self, analyzer):
        """Best range should be a slow/long preset."""
        comp = analyzer.compare()
        assert 'LONG' in comp.best_range or 'VERY' in comp.best_range

    def test_best_throughput_is_fast_preset(self, analyzer):
        """Best throughput should be a fast/short preset."""
        comp = analyzer.compare()
        assert 'SHORT' in comp.best_throughput or 'TURBO' in comp.best_throughput

    def test_comparison_to_dict(self, analyzer):
        """Comparison to_dict includes all presets and rankings."""
        comp = analyzer.compare()
        d = comp.to_dict()
        assert len(d['presets']) == len(PRESET_PARAMS)
        assert 'rankings' in d

    def test_sorted_by_range(self, analyzer):
        """Presets sorted by LOS range (shortest first)."""
        comp = analyzer.compare()
        ranges = [p.max_range_los_km for p in comp.presets]
        assert ranges == sorted(ranges)


# =============================================================================
# Coverage Zones
# =============================================================================

class TestCoverageZones:
    def test_zones_ordered(self, analyzer):
        """Coverage zones should be ordered: excellent < good < fair < max."""
        zones = analyzer.coverage_zones('LONG_FAST')
        assert zones['excellent_km'] < zones['good_km']
        assert zones['good_km'] < zones['fair_km']
        assert zones['fair_km'] <= zones['max_km']

    def test_all_zones_positive(self, analyzer):
        """All zone ranges should be positive."""
        for name in PRESET_PARAMS:
            zones = analyzer.coverage_zones(name)
            for key, value in zones.items():
                assert value > 0, f"{name} {key} = {value}"

    def test_range_at_snr_unknown_preset(self, analyzer):
        """Unknown preset raises ValueError."""
        with pytest.raises(ValueError):
            analyzer.range_at_snr('FAKE_PRESET', -5.0)


# =============================================================================
# Format Table
# =============================================================================

class TestFormatTable:
    def test_format_produces_output(self, analyzer):
        """format_comparison_table produces non-empty string."""
        comp = analyzer.compare()
        table = format_comparison_table(comp)
        assert len(table) > 100
        assert 'LONG_FAST' in table

    def test_format_contains_all_presets(self, analyzer):
        """Table includes every preset name."""
        comp = analyzer.compare()
        table = format_comparison_table(comp)
        for name in PRESET_PARAMS:
            assert name in table

    def test_format_contains_markers(self, analyzer):
        """Table includes [R], [T], [B] markers."""
        comp = analyzer.compare()
        table = format_comparison_table(comp)
        assert '[R]' in table
        assert '[T]' in table
        assert '[B]' in table


# =============================================================================
# Convenience Function
# =============================================================================

class TestConvenienceFunction:
    def test_compare_presets_default(self):
        """compare_presets() works with defaults."""
        comp = compare_presets()
        assert len(comp.presets) == len(PRESET_PARAMS)

    def test_compare_presets_custom_power(self):
        """Custom TX power affects results."""
        low = compare_presets(tx_power_dbm=14)
        high = compare_presets(tx_power_dbm=30)
        # Higher power = longer range for same preset
        low_range = next(p for p in low.presets if p.preset_name == 'LONG_FAST')
        high_range = next(p for p in high.presets if p.preset_name == 'LONG_FAST')
        assert high_range.max_range_los_km > low_range.max_range_los_km


# =============================================================================
# Physics Validation
# =============================================================================

class TestPhysicsValidation:
    def test_range_throughput_inverse(self, analyzer):
        """Range and throughput should generally be inversely related."""
        impacts = analyzer.analyze_all()
        # The preset with best range should NOT have best throughput
        best_range_preset = max(impacts, key=lambda p: p.max_range_los_km)
        best_tp_preset = max(impacts, key=lambda p: p.throughput_bps)
        assert best_range_preset.preset_name != best_tp_preset.preset_name

    def test_sf12_beats_sf7_range(self, analyzer):
        """SF12 should always have more range than SF7 at same BW."""
        # Compare at 125kHz
        sens_sf12 = analyzer.sensitivity(12, 125000)
        sens_sf7 = analyzer.sensitivity(7, 125000)
        # SF12 is 12.5 dB more sensitive
        assert sens_sf12 < sens_sf7
        diff = sens_sf7 - sens_sf12
        assert diff > 10  # At least 10 dB difference

    def test_energy_conservation(self, analyzer):
        """Link budget + FSPL at max LOS range should equal sensitivity."""
        impact = analyzer.analyze_preset('MEDIUM_FAST')
        # At max LOS range (uncapped FSPL), received power = sensitivity
        fspl_at_max = (20 * math.log10(impact.max_range_los_km * 1000) +
                       20 * math.log10(impact.frequency_mhz) - 27.55)
        received = impact.tx_power_dbm + analyzer.tx_gain_dbi + analyzer.rx_gain_dbi - fspl_at_max
        # Should be approximately equal to sensitivity
        assert abs(received - impact.sensitivity_dbm) < 0.1

    def test_higher_cr_lower_throughput(self, analyzer):
        """Higher coding rate should reduce throughput."""
        tp_cr5 = analyzer.throughput_bps(10, 250000, 5)
        tp_cr8 = analyzer.throughput_bps(10, 250000, 8)
        assert tp_cr5 > tp_cr8  # CR 4/5 is less redundancy than 4/8
