"""
Tests for antenna pattern modeling module.

Tests cover:
- Angle normalization utilities
- Isotropic pattern (uniform gain)
- Dipole pattern (omnidirectional, elevation-dependent)
- Ground plane pattern (higher gain omni)
- Yagi pattern (directional, beamwidth)
- Patch pattern (wide-beam directional)
- Antenna presets
- Coverage calculations with antenna gain
- Range profiles and comparisons
- Physics validation
"""

import sys
import os
import math

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import pytest

from utils.antenna_patterns import (
    AntennaPattern,
    IsotropicPattern,
    DipolePattern,
    GroundPlanePattern,
    YagiPattern,
    PatchPattern,
    AntennaSpec,
    ANTENNA_PRESETS,
    normalize_angle,
    angle_difference,
    get_antenna_preset,
    effective_gain,
    coverage_with_antenna,
    azimuth_range_profile,
    format_antenna_comparison,
)


# =============================================================================
# Angle Utilities
# =============================================================================

class TestNormalizeAngle:
    def test_zero(self):
        assert normalize_angle(0.0) == 0.0

    def test_positive(self):
        assert normalize_angle(90.0) == 90.0

    def test_full_circle(self):
        assert normalize_angle(360.0) == 0.0

    def test_negative(self):
        assert normalize_angle(-90.0) == 270.0

    def test_large_positive(self):
        assert normalize_angle(720.0) == 0.0

    def test_large_negative(self):
        assert normalize_angle(-450.0) == 270.0


class TestAngleDifference:
    def test_same_angle(self):
        assert angle_difference(45.0, 45.0) == 0.0

    def test_opposite(self):
        assert angle_difference(0.0, 180.0) == 180.0

    def test_90_degrees(self):
        assert abs(angle_difference(0.0, 90.0) - 90.0) < 0.001

    def test_wrap_around(self):
        """350° to 10° is 20°, not 340°."""
        assert abs(angle_difference(350.0, 10.0) - 20.0) < 0.001

    def test_symmetry(self):
        assert angle_difference(30.0, 60.0) == angle_difference(60.0, 30.0)

    def test_negative_angles(self):
        assert abs(angle_difference(-10.0, 10.0) - 20.0) < 0.001


# =============================================================================
# Isotropic Pattern
# =============================================================================

class TestIsotropicPattern:
    def test_uniform_gain(self):
        """Isotropic has 0 dBi everywhere."""
        iso = IsotropicPattern()
        for az in range(0, 360, 30):
            for el in range(-90, 91, 30):
                assert iso.gain_at(float(az), float(el)) == 0.0

    def test_peak_gain(self):
        iso = IsotropicPattern()
        assert iso.peak_gain_dbi == 0.0

    def test_spec(self):
        iso = IsotropicPattern()
        s = iso.spec()
        assert s.h_beamwidth_deg == 360.0
        assert s.v_beamwidth_deg == 360.0
        assert s.peak_gain_dbi == 0.0


# =============================================================================
# Dipole Pattern
# =============================================================================

class TestDipolePattern:
    def test_peak_at_horizon(self):
        """Dipole peak gain at horizon."""
        dip = DipolePattern()
        gain = dip.gain_at(0.0, 0.0)
        assert abs(gain - 2.15) < 0.01

    def test_omnidirectional(self):
        """Same gain at all azimuths (at horizon)."""
        dip = DipolePattern()
        gains = [dip.gain_at(float(az), 0.0) for az in range(0, 360, 30)]
        assert max(gains) - min(gains) < 0.001

    def test_null_at_zenith(self):
        """Gain drops significantly at zenith."""
        dip = DipolePattern()
        gain_zenith = dip.gain_at(0.0, 90.0)
        assert gain_zenith < -30.0  # Practical null

    def test_gain_decreases_with_elevation(self):
        """Gain decreases as elevation increases."""
        dip = DipolePattern()
        gain_0 = dip.gain_at(0.0, 0.0)
        gain_30 = dip.gain_at(0.0, 30.0)
        gain_60 = dip.gain_at(0.0, 60.0)
        assert gain_0 > gain_30 > gain_60

    def test_default_peak_gain(self):
        """Default peak is 2.15 dBi."""
        dip = DipolePattern()
        assert dip.peak_gain_dbi == 2.15

    def test_custom_gain(self):
        """Custom peak gain works."""
        dip = DipolePattern(peak_gain_dbi=3.0)
        assert dip.gain_at(0.0, 0.0) == 3.0

    def test_negative_elevation_symmetric(self):
        """Negative elevation same as positive (symmetric pattern)."""
        dip = DipolePattern()
        assert abs(dip.gain_at(0.0, 30.0) - dip.gain_at(0.0, -30.0)) < 0.001

    def test_spec_beamwidth(self):
        """Spec reports reasonable vertical beamwidth."""
        dip = DipolePattern()
        s = dip.spec()
        assert s.h_beamwidth_deg == 360.0
        assert 60 < s.v_beamwidth_deg < 90


# =============================================================================
# Ground Plane Pattern
# =============================================================================

class TestGroundPlanePattern:
    def test_higher_gain_than_dipole(self):
        """Ground plane should have higher peak gain than dipole."""
        gp = GroundPlanePattern(peak_gain_dbi=5.5)
        dip = DipolePattern()
        assert gp.gain_at(0.0, 0.0) > dip.gain_at(0.0, 0.0)

    def test_omnidirectional(self):
        """Omnidirectional in azimuth."""
        gp = GroundPlanePattern()
        gains = [gp.gain_at(float(az), 0.0) for az in range(0, 360, 30)]
        assert max(gains) - min(gains) < 0.001

    def test_narrower_vertical_than_dipole(self):
        """Higher gain means narrower vertical beam."""
        gp = GroundPlanePattern(peak_gain_dbi=8.0)
        dip = DipolePattern()
        # At 45° elevation, GP should have less gain relative to peak
        gp_drop = gp.gain_at(0.0, 0.0) - gp.gain_at(0.0, 45.0)
        dip_drop = dip.gain_at(0.0, 0.0) - dip.gain_at(0.0, 45.0)
        assert gp_drop > dip_drop

    def test_peak_at_horizon(self):
        """Peak gain at horizon."""
        gp = GroundPlanePattern(peak_gain_dbi=5.5)
        assert abs(gp.gain_at(0.0, 0.0) - 5.5) < 0.01

    def test_null_at_zenith(self):
        """Gain drops at zenith."""
        gp = GroundPlanePattern()
        assert gp.gain_at(0.0, 90.0) < -20.0

    def test_spec_vertical_beamwidth(self):
        """Higher gain GP has narrower vertical beam in spec."""
        gp5 = GroundPlanePattern(peak_gain_dbi=5.5)
        gp8 = GroundPlanePattern(peak_gain_dbi=8.0)
        assert gp8.spec().v_beamwidth_deg < gp5.spec().v_beamwidth_deg


# =============================================================================
# Yagi Pattern
# =============================================================================

class TestYagiPattern:
    def test_peak_on_axis(self):
        """Peak gain on the aim axis."""
        yagi = YagiPattern(peak_gain_dbi=12.0, aim_azimuth=45.0)
        gain = yagi.gain_at(45.0, 0.0)
        assert abs(gain - 12.0) < 0.01

    def test_gain_drops_off_axis(self):
        """Gain drops away from aim direction."""
        yagi = YagiPattern(peak_gain_dbi=12.0, h_beamwidth=30.0, aim_azimuth=0.0)
        gain_on = yagi.gain_at(0.0, 0.0)
        gain_off = yagi.gain_at(45.0, 0.0)
        assert gain_on > gain_off

    def test_3db_at_beamwidth_edge(self):
        """Gain should be ~peak-3dB at half-beamwidth."""
        yagi = YagiPattern(peak_gain_dbi=12.0, h_beamwidth=30.0, aim_azimuth=0.0)
        gain_edge = yagi.gain_at(15.0, 0.0)  # Half of 30° beamwidth
        # Should be approximately peak - 3 dB
        assert abs(gain_edge - (12.0 - 3.0)) < 0.5

    def test_front_to_back(self):
        """Rear gain = peak - F/B ratio."""
        yagi = YagiPattern(peak_gain_dbi=12.0, front_to_back_db=20.0, aim_azimuth=0.0)
        rear_gain = yagi.gain_at(180.0, 0.0)
        assert abs(rear_gain - (12.0 - 20.0)) < 0.01

    def test_custom_aim_direction(self):
        """Aim direction shifts the pattern."""
        yagi = YagiPattern(aim_azimuth=90.0)
        gain_aim = yagi.gain_at(90.0, 0.0)
        gain_opposite = yagi.gain_at(270.0, 0.0)
        assert gain_aim > gain_opposite

    def test_vertical_pattern(self):
        """Vertical pattern also narrows."""
        yagi = YagiPattern(peak_gain_dbi=12.0, v_beamwidth=35.0, aim_azimuth=0.0)
        gain_0 = yagi.gain_at(0.0, 0.0)
        gain_45 = yagi.gain_at(0.0, 45.0)
        assert gain_0 > gain_45

    def test_directional_not_omni(self):
        """Yagi is NOT omnidirectional."""
        yagi = YagiPattern(peak_gain_dbi=12.0, h_beamwidth=30.0)
        gains = [yagi.gain_at(float(az), 0.0) for az in range(0, 360, 30)]
        assert max(gains) - min(gains) > 10.0  # Significant directivity

    def test_spec_fields(self):
        """Spec returns correct fields."""
        yagi = YagiPattern(peak_gain_dbi=12.0, h_beamwidth=30.0,
                          aim_azimuth=45.0, front_to_back_db=20.0)
        s = yagi.spec()
        assert s.type_name == "Yagi"
        assert s.peak_gain_dbi == 12.0
        assert s.h_beamwidth_deg == 30.0
        assert s.aim_azimuth_deg == 45.0
        assert s.front_to_back_db == 20.0

    def test_aim_elevation(self):
        """Tilted antenna shifts vertical peak."""
        yagi = YagiPattern(aim_elevation=10.0, aim_azimuth=0.0)
        gain_10 = yagi.gain_at(0.0, 10.0)
        gain_0 = yagi.gain_at(0.0, 0.0)
        assert gain_10 > gain_0


# =============================================================================
# Patch Pattern
# =============================================================================

class TestPatchPattern:
    def test_peak_on_axis(self):
        """Peak gain on aim axis."""
        patch = PatchPattern(peak_gain_dbi=8.0, aim_azimuth=0.0)
        assert abs(patch.gain_at(0.0, 0.0) - 8.0) < 0.01

    def test_wider_beam_than_yagi(self):
        """Patch has wider beamwidth than Yagi at similar gain."""
        patch = PatchPattern(peak_gain_dbi=8.0, h_beamwidth=70.0, aim_azimuth=0.0)
        yagi = YagiPattern(peak_gain_dbi=12.0, h_beamwidth=30.0, aim_azimuth=0.0)
        # At 30° off-axis, patch should retain more gain (relative to peak)
        patch_drop = patch.peak_gain_dbi - patch.gain_at(30.0, 0.0)
        yagi_drop = yagi.peak_gain_dbi - yagi.gain_at(30.0, 0.0)
        assert patch_drop < yagi_drop

    def test_front_to_back(self):
        """Rear gain is reduced by F/B ratio."""
        patch = PatchPattern(peak_gain_dbi=8.0, front_to_back_db=15.0, aim_azimuth=0.0)
        rear = patch.gain_at(180.0, 0.0)
        assert abs(rear - (8.0 - 15.0)) < 0.01

    def test_directional(self):
        """Patch is directional."""
        patch = PatchPattern(aim_azimuth=90.0)
        gain_front = patch.gain_at(90.0, 0.0)
        gain_back = patch.gain_at(270.0, 0.0)
        assert gain_front > gain_back

    def test_spec_fields(self):
        """Spec returns correct type."""
        patch = PatchPattern()
        s = patch.spec()
        assert s.type_name == "Patch"


# =============================================================================
# Antenna Presets
# =============================================================================

class TestAntennaPresets:
    def test_all_presets_instantiate(self):
        """All preset factories produce valid patterns."""
        for name in ANTENNA_PRESETS:
            antenna = ANTENNA_PRESETS[name]()
            assert isinstance(antenna, AntennaPattern)

    def test_get_preset_valid(self):
        """get_antenna_preset works for known names."""
        ant = get_antenna_preset('stock_whip')
        assert ant.peak_gain_dbi == 2.15

    def test_get_preset_invalid(self):
        """Unknown preset raises ValueError."""
        with pytest.raises(ValueError):
            get_antenna_preset('nonexistent')

    def test_stock_whip_is_dipole(self):
        """Stock whip is a DipolePattern."""
        ant = get_antenna_preset('stock_whip')
        assert isinstance(ant, DipolePattern)

    def test_yagi_preset(self):
        """Yagi presets are YagiPattern."""
        ant = get_antenna_preset('yagi_5el')
        assert isinstance(ant, YagiPattern)

    def test_preset_gain_ordering(self):
        """Presets should have increasing gain: whip < collinear < yagi."""
        whip = get_antenna_preset('stock_whip')
        col = get_antenna_preset('collinear_5dbi')
        yagi = get_antenna_preset('yagi_5el')
        assert whip.peak_gain_dbi < col.peak_gain_dbi < yagi.peak_gain_dbi


# =============================================================================
# Effective Range
# =============================================================================

class TestEffectiveRange:
    def test_same_as_reference_unity(self):
        """Same gain as reference = 1x range factor."""
        dip = DipolePattern(peak_gain_dbi=2.15)
        factor = dip.effective_range_factor(0.0, 0.0, reference_gain_dbi=2.15)
        assert abs(factor - 1.0) < 0.01

    def test_higher_gain_extends_range(self):
        """Higher gain = range factor > 1."""
        yagi = YagiPattern(peak_gain_dbi=12.0, aim_azimuth=0.0)
        factor = yagi.effective_range_factor(0.0, 0.0, reference_gain_dbi=2.15)
        assert factor > 1.0

    def test_lower_gain_reduces_range(self):
        """Lower gain (off-axis) = range factor < 1."""
        yagi = YagiPattern(peak_gain_dbi=12.0, h_beamwidth=30.0,
                          aim_azimuth=0.0, front_to_back_db=25.0)
        factor = yagi.effective_range_factor(180.0, 0.0, reference_gain_dbi=2.15)
        assert factor < 1.0

    def test_6db_doubles_range(self):
        """6 dB gain over reference should ~double range."""
        # 6 dB over dipole = 8.15 dBi
        gp = GroundPlanePattern(peak_gain_dbi=8.15)
        factor = gp.effective_range_factor(0.0, 0.0, reference_gain_dbi=2.15)
        # 10^(6/20) = 1.995 ≈ 2x
        assert abs(factor - 2.0) < 0.05

    def test_coverage_with_antenna_function(self):
        """coverage_with_antenna applies range factor correctly."""
        yagi = YagiPattern(peak_gain_dbi=12.0, aim_azimuth=0.0)
        base_range = 10.0  # km
        enhanced = coverage_with_antenna(base_range, yagi, azimuth=0.0)
        assert enhanced > base_range

    def test_coverage_off_axis_reduced(self):
        """Off-axis coverage is less than on-axis."""
        yagi = YagiPattern(peak_gain_dbi=12.0, h_beamwidth=30.0, aim_azimuth=0.0)
        on_axis = coverage_with_antenna(10.0, yagi, azimuth=0.0)
        off_axis = coverage_with_antenna(10.0, yagi, azimuth=60.0)
        assert on_axis > off_axis


# =============================================================================
# Range Profile
# =============================================================================

class TestRangeProfile:
    def test_profile_length(self):
        """Profile returns correct number of points."""
        dip = DipolePattern()
        profile = azimuth_range_profile(dip, 10.0, step_deg=10.0)
        assert len(profile) == 36  # 360/10

    def test_profile_uniform_for_omni(self):
        """Omnidirectional antenna has uniform range profile."""
        dip = DipolePattern()
        profile = azimuth_range_profile(dip, 10.0)
        ranges = [r for _, r in profile]
        assert max(ranges) - min(ranges) < 0.01

    def test_profile_directional_peak(self):
        """Directional antenna shows peak in aim direction."""
        yagi = YagiPattern(peak_gain_dbi=12.0, aim_azimuth=90.0)
        profile = azimuth_range_profile(yagi, 10.0, step_deg=10.0)
        # Find the max range — should be near 90°
        max_entry = max(profile, key=lambda x: x[1])
        assert abs(max_entry[0] - 90.0) <= 10.0

    def test_profile_azimuths_correct(self):
        """Azimuths start at 0 and increase."""
        dip = DipolePattern()
        profile = azimuth_range_profile(dip, 10.0, step_deg=45.0)
        azimuths = [az for az, _ in profile]
        assert azimuths == [0, 45, 90, 135, 180, 225, 270, 315]


# =============================================================================
# Format Comparison
# =============================================================================

class TestFormatComparison:
    def test_output_nonempty(self):
        """Comparison table is non-empty."""
        antennas = [DipolePattern(), GroundPlanePattern(), YagiPattern()]
        table = format_antenna_comparison(antennas)
        assert len(table) > 50

    def test_contains_antenna_names(self):
        """Table includes antenna names."""
        antennas = [DipolePattern(name="MyDipole"),
                   YagiPattern(name="MyYagi")]
        table = format_antenna_comparison(antennas)
        assert "MyDipole" in table
        assert "MyYagi" in table

    def test_contains_gain_values(self):
        """Table includes gain numbers."""
        antennas = [DipolePattern()]
        table = format_antenna_comparison(antennas)
        assert "2.1" in table  # 2.15 dBi rounded

    def test_factor_column(self):
        """Table includes range factor."""
        antennas = [DipolePattern()]
        table = format_antenna_comparison(antennas, target_azimuth=0.0)
        assert "1.00x" in table  # Dipole vs dipole reference = 1x


# =============================================================================
# Gain Pattern Generation
# =============================================================================

class TestGainPattern:
    def test_pattern_length(self):
        """gain_pattern returns correct count."""
        dip = DipolePattern()
        pattern = dip.gain_pattern(azimuth_step=10.0)
        assert len(pattern) == 36

    def test_pattern_default_step(self):
        """Default step is 5 degrees."""
        dip = DipolePattern()
        pattern = dip.gain_pattern()
        assert len(pattern) == 72  # 360/5

    def test_pattern_at_elevation(self):
        """Pattern at non-zero elevation works."""
        dip = DipolePattern()
        pattern = dip.gain_pattern(elevation=30.0)
        assert all(g < dip.peak_gain_dbi for _, g in pattern)


# =============================================================================
# AntennaSpec
# =============================================================================

class TestAntennaSpec:
    def test_to_dict(self):
        """AntennaSpec.to_dict returns complete structure."""
        s = AntennaSpec(
            name="Test", type_name="Yagi", peak_gain_dbi=12.0,
            h_beamwidth_deg=30.0, v_beamwidth_deg=35.0,
            front_to_back_db=20.0, aim_azimuth_deg=45.0,
        )
        d = s.to_dict()
        assert d['name'] == "Test"
        assert d['type'] == "Yagi"
        assert d['peak_gain_dbi'] == 12.0

    def test_default_efficiency(self):
        """Default efficiency is 0.85."""
        s = AntennaSpec(name="X", type_name="Y", peak_gain_dbi=5.0,
                       h_beamwidth_deg=360.0, v_beamwidth_deg=78.0)
        assert s.efficiency == 0.85


# =============================================================================
# Physics Validation
# =============================================================================

class TestPhysicsValidation:
    def test_yagi_gain_vs_beamwidth_inverse(self):
        """Narrower beam = higher gain (directivity)."""
        narrow = YagiPattern(peak_gain_dbi=13.0, h_beamwidth=25.0)
        wide = YagiPattern(peak_gain_dbi=8.0, h_beamwidth=60.0)
        assert narrow.peak_gain_dbi > wide.peak_gain_dbi

    def test_omni_uniform_in_azimuth(self):
        """All omni types are uniform in azimuth."""
        for OmniType in [IsotropicPattern, DipolePattern, GroundPlanePattern]:
            if OmniType == GroundPlanePattern:
                ant = OmniType(peak_gain_dbi=5.5)
            else:
                ant = OmniType()
            gains = [ant.gain_at(float(az), 0.0) for az in range(0, 360, 10)]
            assert max(gains) - min(gains) < 0.001

    def test_reciprocity(self):
        """Antenna gain pattern is the same for TX and RX (reciprocity theorem)."""
        # This is inherent in the model — same gain_at() used for both.
        # Test that the pattern is deterministic.
        yagi = YagiPattern(peak_gain_dbi=12.0, aim_azimuth=45.0)
        g1 = yagi.gain_at(30.0, 5.0)
        g2 = yagi.gain_at(30.0, 5.0)
        assert g1 == g2

    def test_gain_never_exceeds_peak(self):
        """No direction should exceed peak gain."""
        for AntennaType in [DipolePattern, GroundPlanePattern]:
            if AntennaType == GroundPlanePattern:
                ant = AntennaType(peak_gain_dbi=5.5)
            else:
                ant = AntennaType()
            for az in range(0, 360, 10):
                for el in range(-90, 91, 10):
                    g = ant.gain_at(float(az), float(el))
                    assert g <= ant.peak_gain_dbi + 0.01

    def test_yagi_gain_never_exceeds_peak(self):
        """Yagi gain never exceeds peak in any direction."""
        yagi = YagiPattern(peak_gain_dbi=12.0, h_beamwidth=30.0, aim_azimuth=0.0)
        for az in range(0, 360, 5):
            for el in range(-90, 91, 10):
                g = yagi.gain_at(float(az), float(el))
                assert g <= yagi.peak_gain_dbi + 0.01

    def test_dipole_pattern_symmetry(self):
        """Dipole is symmetric around the axis."""
        dip = DipolePattern()
        # Symmetric in azimuth
        assert abs(dip.gain_at(0.0, 30.0) - dip.gain_at(180.0, 30.0)) < 0.001
        # Symmetric in elevation (positive/negative)
        assert abs(dip.gain_at(0.0, 45.0) - dip.gain_at(0.0, -45.0)) < 0.001

    def test_range_factor_physics(self):
        """10 dB gain over reference should give ~3.16x range (10^(10/20))."""
        # Antenna with exactly 12.15 dBi = 10 dB over 2.15 reference
        yagi = YagiPattern(peak_gain_dbi=12.15, aim_azimuth=0.0)
        factor = yagi.effective_range_factor(0.0, 0.0, reference_gain_dbi=2.15)
        expected = 10 ** (10.0 / 20.0)  # ~3.162
        assert abs(factor - expected) < 0.01
