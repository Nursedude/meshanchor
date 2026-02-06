"""
RF utility function tests for MeshForge.

Run with: python3 -m pytest tests/test_rf_utils.py -v
Or: python3 tests/test_rf_utils.py
"""

import sys
import os
import math

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from utils.rf import (
    haversine_distance, fresnel_radius, free_space_path_loss, earth_bulge,
    DeployEnvironment, BuildingType, ENVIRONMENT_PARAMS,
    BUILDING_PENETRATION_DB, SNR_THRESHOLD_DB,
    rx_sensitivity, log_distance_path_loss, realistic_max_range,
    radio_horizon_km, processing_gain_db, capture_effect,
)


class TestHaversineDistance:
    """Test haversine distance calculations."""

    def test_hilo_to_honolulu(self):
        """Hilo to Honolulu should be ~337 km."""
        dist = haversine_distance(19.7297, -155.09, 21.3069, -157.8583)
        assert 335_000 < dist < 340_000  # meters

    def test_same_point(self):
        """Same point should return 0."""
        dist = haversine_distance(37.7749, -122.4194, 37.7749, -122.4194)
        assert dist == 0

    def test_short_distance(self):
        """Short distance ~1km accuracy."""
        # ~1km apart
        dist = haversine_distance(37.7749, -122.4194, 37.7839, -122.4094)
        assert 1000 < dist < 1500

    def test_antipodal_points(self):
        """Opposite sides of Earth ~20,000 km."""
        dist = haversine_distance(0, 0, 0, 180)
        assert 20_000_000 < dist < 20_100_000


class TestFresnelRadius:
    """Test Fresnel zone radius calculations."""

    def test_915mhz_10km(self):
        """915 MHz at 10km should be ~29m radius."""
        radius = fresnel_radius(10, 0.915)
        assert 27 < radius < 30

    def test_433mhz_10km(self):
        """Lower frequency = larger Fresnel zone."""
        radius_433 = fresnel_radius(10, 0.433)
        radius_915 = fresnel_radius(10, 0.915)
        assert radius_433 > radius_915

    def test_longer_distance(self):
        """Longer distance = larger Fresnel zone."""
        radius_10km = fresnel_radius(10, 0.915)
        radius_50km = fresnel_radius(50, 0.915)
        assert radius_50km > radius_10km


class TestFreeSpacePathLoss:
    """Test FSPL calculations."""

    def test_1km_915mhz(self):
        """1km at 915 MHz should be ~92 dB."""
        fspl = free_space_path_loss(1000, 915)
        assert 90 < fspl < 94

    def test_10km_915mhz(self):
        """10km at 915 MHz should be ~112 dB."""
        fspl = free_space_path_loss(10000, 915)
        assert 110 < fspl < 114

    def test_distance_doubles_adds_6db(self):
        """Doubling distance adds ~6 dB."""
        fspl_1km = free_space_path_loss(1000, 915)
        fspl_2km = free_space_path_loss(2000, 915)
        diff = fspl_2km - fspl_1km
        assert 5.5 < diff < 6.5


class TestEarthBulge:
    """Test Earth bulge calculations."""

    def test_10km(self):
        """10km path should have ~1.5m bulge."""
        bulge = earth_bulge(10000)
        assert 1.4 < bulge < 1.6

    def test_50km(self):
        """50km path should have ~37m bulge (scales with d^2)."""
        bulge = earth_bulge(50000)
        assert 35 < bulge < 40

    def test_short_distance(self):
        """1km should have negligible bulge."""
        bulge = earth_bulge(1000)
        assert bulge < 0.1


class TestKnifeEdgeDiffraction:
    """Test knife_edge_diffraction() terrain model."""

    def test_no_blockage(self):
        """Obstacle below LOS should cause no loss."""
        from src.utils.rf import knife_edge_diffraction
        loss = knife_edge_diffraction(5000, -5.0)
        assert loss == 0.0

    def test_moderate_blockage(self):
        """10m obstacle at 5km midpoint should cause 10-20 dB loss."""
        from src.utils.rf import knife_edge_diffraction
        loss = knife_edge_diffraction(5000, 10.0)
        assert 10 < loss < 25

    def test_heavy_blockage(self):
        """50m obstacle should cause more loss than 10m."""
        from src.utils.rf import knife_edge_diffraction
        loss_10m = knife_edge_diffraction(5000, 10.0)
        loss_50m = knife_edge_diffraction(5000, 50.0)
        assert loss_50m > loss_10m

    def test_higher_freq_more_loss(self):
        """Higher frequency should diffract less (more loss)."""
        from src.utils.rf import knife_edge_diffraction
        loss_900 = knife_edge_diffraction(5000, 10.0, freq_mhz=906.875)
        loss_2400 = knife_edge_diffraction(5000, 10.0, freq_mhz=2400.0)
        assert loss_2400 > loss_900

    def test_multi_obstacle(self):
        """Multiple obstacles should sum losses."""
        from src.utils.rf import multi_obstacle_loss
        single = multi_obstacle_loss(10000, [(0.5, 10.0)])
        double = multi_obstacle_loss(10000, [(0.3, 10.0), (0.7, 10.0)])
        assert double > single


class TestDetailedLinkBudget:
    """Test detailed_link_budget() component breakdown."""

    def test_short_range_strong_signal(self):
        """1km link with default settings should have good margin."""
        from src.utils.rf import detailed_link_budget
        result = detailed_link_budget(distance_m=1000, tx_power_dbm=20)
        # 1km at 20dBm should be well above sensitivity
        assert result.link_margin_db > 20
        assert result.signal_quality == "EXCELLENT"

    def test_long_range_weaker_than_short(self):
        """50km link should have less margin than 1km link."""
        from src.utils.rf import detailed_link_budget
        short = detailed_link_budget(distance_m=1000, tx_power_dbm=20)
        long = detailed_link_budget(distance_m=50000, tx_power_dbm=20)
        # 50km has much more path loss
        assert long.link_margin_db < short.link_margin_db
        assert long.path_loss_db > 120  # High path loss at 50km
        assert long.path_loss_db > short.path_loss_db + 30  # ~34 dB more

    def test_cable_loss_reduces_margin(self):
        """Longer/lossier cables should reduce link margin."""
        from src.utils.rf import detailed_link_budget
        short_cable = detailed_link_budget(
            tx_cable_type='lmr400', tx_cable_length_m=1.0,
            rx_cable_type='lmr400', rx_cable_length_m=1.0,
        )
        long_cable = detailed_link_budget(
            tx_cable_type='rg58', tx_cable_length_m=10.0,
            rx_cable_type='rg58', rx_cable_length_m=10.0,
        )
        # Worse cables = lower received power
        assert long_cable.received_power_dbm < short_cable.received_power_dbm
        assert long_cable.tx_cable_loss_db > short_cable.tx_cable_loss_db

    def test_antenna_gain_improves_margin(self):
        """Higher antenna gain should improve link margin."""
        from src.utils.rf import detailed_link_budget
        stock = detailed_link_budget(tx_antenna_gain_dbi=2.0, rx_antenna_gain_dbi=2.0)
        yagi = detailed_link_budget(tx_antenna_gain_dbi=10.0, rx_antenna_gain_dbi=10.0)
        # 16 dB more gain total
        assert yagi.link_margin_db > stock.link_margin_db + 15

    def test_eirp_calculation(self):
        """EIRP should be TX power - cable loss + antenna gain."""
        from src.utils.rf import detailed_link_budget
        result = detailed_link_budget(
            tx_power_dbm=30.0,
            tx_cable_type='lmr400', tx_cable_length_m=1.0,
            tx_antenna_gain_dbi=6.0,
        )
        # EIRP = 30 - cable_loss + 6
        expected_eirp = 30.0 - result.tx_cable_loss_db + 6.0
        assert abs(result.eirp_dbm - expected_eirp) < 0.01

    def test_summary_output(self):
        """summary() should return readable lines."""
        from src.utils.rf import detailed_link_budget
        result = detailed_link_budget(distance_m=5000)
        lines = result.summary()
        assert len(lines) > 10
        assert any("TX Power" in line for line in lines)
        assert any("Link Margin" in line for line in lines)
        assert any("Signal Quality" in line for line in lines)

    def test_spreading_factor_affects_sensitivity(self):
        """Higher SF should have better sensitivity (more negative)."""
        from src.utils.rf import detailed_link_budget
        sf7 = detailed_link_budget(spreading_factor=7, distance_m=20000)
        sf12 = detailed_link_budget(spreading_factor=12, distance_m=20000)
        # SF12 has ~14dB better sensitivity than SF7
        assert sf12.rx_sensitivity_dbm < sf7.rx_sensitivity_dbm
        assert sf12.link_margin_db > sf7.link_margin_db


class TestRxSensitivity:
    """Test bandwidth-aware receiver sensitivity calculation."""

    def test_sf12_bw125_standard(self):
        """SF12/125kHz should be ~-137 dBm (matches datasheet)."""
        sens = rx_sensitivity(12, 125000)
        assert -138.0 < sens < -136.0

    def test_sf7_bw125(self):
        """SF7/125kHz should be ~-124.5 dBm."""
        sens = rx_sensitivity(7, 125000)
        assert -125.5 < sens < -123.5

    def test_sf11_bw250_longfast(self):
        """LongFast (SF11/250kHz) should be ~-131.5 dBm."""
        sens = rx_sensitivity(11, 250000)
        assert -132.5 < sens < -130.5

    def test_sf12_bw62500_verylongslow(self):
        """VeryLongSlow (SF12/62.5kHz) should be ~-140 dBm."""
        sens = rx_sensitivity(12, 62500)
        assert -141.0 < sens < -139.0

    def test_wider_bw_less_sensitive(self):
        """Wider bandwidth = worse sensitivity (more noise)."""
        sens_125 = rx_sensitivity(11, 125000)
        sens_250 = rx_sensitivity(11, 250000)
        assert sens_250 > sens_125  # Less negative = worse

    def test_higher_sf_more_sensitive(self):
        """Higher SF = better sensitivity (lower SNR threshold)."""
        sens_7 = rx_sensitivity(7, 125000)
        sens_12 = rx_sensitivity(12, 125000)
        assert sens_12 < sens_7  # More negative = better

    def test_halving_bw_gains_3db(self):
        """Halving BW should improve sensitivity by ~3 dB."""
        sens_250 = rx_sensitivity(11, 250000)
        sens_125 = rx_sensitivity(11, 125000)
        diff = sens_250 - sens_125
        assert 2.5 < diff < 3.5


class TestLogDistancePathLoss:
    """Test environment-aware log-distance path loss model."""

    def test_free_space_matches_fspl(self):
        """Free space environment should closely match FSPL."""
        ld_pl = log_distance_path_loss(1000, 915, DeployEnvironment.FREE_SPACE)
        fspl = free_space_path_loss(1000, 915)
        assert abs(ld_pl - fspl) < 1.0  # Within 1 dB

    def test_suburban_worse_than_free_space(self):
        """Suburban PLE 2.7 should give more loss than free space PLE 2.0."""
        fs_pl = log_distance_path_loss(5000, 915, DeployEnvironment.FREE_SPACE)
        sub_pl = log_distance_path_loss(5000, 915, DeployEnvironment.SUBURBAN)
        assert sub_pl > fs_pl + 5  # At least 5 dB worse at 5 km

    def test_forest_much_worse(self):
        """Forest PLE 5.0 should give dramatically more loss."""
        sub_pl = log_distance_path_loss(1000, 915, DeployEnvironment.SUBURBAN)
        forest_pl = log_distance_path_loss(1000, 915, DeployEnvironment.FOREST)
        assert forest_pl > sub_pl + 15

    def test_urban_elevated_below_free_space(self):
        """Urban elevated GW (PLE 1.8) can be better than free space at distance."""
        ue_pl = log_distance_path_loss(5000, 915, DeployEnvironment.URBAN_ELEVATED)
        fs_pl = log_distance_path_loss(5000, 915, DeployEnvironment.FREE_SPACE)
        assert ue_pl < fs_pl  # Waveguide effect

    def test_short_distance_uses_fspl(self):
        """Very short distance should fall back to FSPL."""
        ld_pl = log_distance_path_loss(0.5, 915, DeployEnvironment.SUBURBAN, d0_m=1.0)
        fspl = free_space_path_loss(0.5, 915)
        assert abs(ld_pl - fspl) < 0.1

    def test_zero_distance_returns_zero(self):
        """Zero distance should return 0."""
        assert log_distance_path_loss(0, 915) == 0.0

    def test_negative_distance_returns_zero(self):
        """Negative distance should return 0."""
        assert log_distance_path_loss(-100, 915) == 0.0


class TestRealisticMaxRange:
    """Test max range with environment model and fade margins."""

    def test_free_space_much_further_than_suburban(self):
        """Free space should yield much longer range than suburban."""
        fs_range = realistic_max_range(155, 915, DeployEnvironment.FREE_SPACE)
        sub_range = realistic_max_range(155, 915, DeployEnvironment.SUBURBAN)
        assert fs_range > sub_range * 10  # Order of magnitude difference

    def test_building_reduces_range(self):
        """Indoor reception should reduce range."""
        outdoor = realistic_max_range(155, 915, DeployEnvironment.SUBURBAN,
                                      BuildingType.NONE)
        indoor = realistic_max_range(155, 915, DeployEnvironment.SUBURBAN,
                                     BuildingType.CONCRETE)
        assert outdoor > indoor * 2

    def test_suburban_longfast_reasonable(self):
        """LongFast suburban range should be roughly 2-10 km."""
        # Typical LongFast link budget ~156 dB
        range_m = realistic_max_range(156, 915, DeployEnvironment.SUBURBAN)
        range_km = range_m / 1000
        assert 1.0 < range_km < 15.0  # Realistic suburban range

    def test_zero_budget_returns_zero(self):
        """Zero link budget should return 0 range."""
        assert realistic_max_range(0, 915) == 0.0

    def test_forest_severely_limited(self):
        """Forest range should be very short."""
        range_m = realistic_max_range(156, 915, DeployEnvironment.FOREST)
        range_km = range_m / 1000
        assert range_km < 2.0  # Dense forest severely limits range


class TestRadioHorizon:
    """Test radio horizon calculation."""

    def test_10m_antennas(self):
        """Two 10m antennas should have ~26 km horizon."""
        horizon = radio_horizon_km(10, 10)
        assert 24 < horizon < 28

    def test_ground_level_short(self):
        """1.5m handheld should have ~5 km horizon."""
        horizon = radio_horizon_km(1.5, 1.5)
        assert 4 < horizon < 12

    def test_mountain_to_mast(self):
        """1000m mountain to 10m mast should have ~140 km horizon."""
        horizon = radio_horizon_km(10, 1000)
        assert 130 < horizon < 150

    def test_higher_goes_further(self):
        """Taller antennas = further horizon."""
        low = radio_horizon_km(5, 5)
        high = radio_horizon_km(30, 30)
        assert high > low * 2

    def test_zero_height(self):
        """Zero height should still work (earth's surface)."""
        horizon = radio_horizon_km(0, 10)
        assert horizon > 0


class TestProcessingGain:
    """Test LoRa processing gain calculation."""

    def test_sf7(self):
        """SF7 should give ~21.1 dB processing gain."""
        pg = processing_gain_db(7)
        assert 20.5 < pg < 21.5

    def test_sf12(self):
        """SF12 should give ~36.1 dB processing gain."""
        pg = processing_gain_db(12)
        assert 35.5 < pg < 36.5

    def test_each_sf_adds_3db(self):
        """Each SF increment should add ~3 dB."""
        for sf in range(7, 12):
            low = processing_gain_db(sf)
            high = processing_gain_db(sf + 1)
            diff = high - low
            assert 2.5 < diff < 3.5


class TestCaptureEffect:
    """Test LoRa capture effect analysis."""

    def test_strong_signal_captures(self):
        """10 dB stronger signal should capture with same SF."""
        captured, margin, desc = capture_effect(-90, -100, same_sf=True)
        assert captured is True
        assert margin > 0
        assert "Captured" in desc

    def test_equal_power_blocked(self):
        """Equal power signals should fail capture (need 6 dB)."""
        captured, margin, desc = capture_effect(-90, -90, same_sf=True)
        assert captured is False
        assert "Blocked" in desc

    def test_weaker_signal_blocked(self):
        """Weaker signal should be blocked."""
        captured, margin, desc = capture_effect(-100, -90, same_sf=True)
        assert captured is False

    def test_cross_sf_more_tolerant(self):
        """Cross-SF should tolerate weaker wanted signal (16 dB rejection)."""
        # Same scenario that fails same-SF should pass cross-SF
        captured_same, _, _ = capture_effect(-95, -90, same_sf=True)
        captured_cross, _, _ = capture_effect(-95, -90, same_sf=False)
        assert captured_same is False  # 5 dB < 6 dB threshold
        assert captured_cross is True  # 5 dB > -16 dB threshold

    def test_exactly_at_threshold(self):
        """At exactly 6 dB delta, should capture."""
        captured, margin, _ = capture_effect(-90, -96, same_sf=True)
        assert captured is True
        assert abs(margin) < 0.1  # Right at threshold


class TestEnvironmentParams:
    """Test environment and building type constants."""

    def test_all_environments_have_params(self):
        """Every DeployEnvironment should have parameters."""
        for env in DeployEnvironment:
            assert env in ENVIRONMENT_PARAMS
            n, sigma, margin = ENVIRONMENT_PARAMS[env]
            assert n > 0
            assert sigma >= 0
            assert margin >= 0

    def test_all_buildings_have_loss(self):
        """Every BuildingType should have penetration loss."""
        for bldg in BuildingType:
            assert bldg in BUILDING_PENETRATION_DB
            loss = BUILDING_PENETRATION_DB[bldg]
            assert loss >= 0

    def test_no_building_zero_loss(self):
        """No building should have 0 dB loss."""
        assert BUILDING_PENETRATION_DB[BuildingType.NONE] == 0.0

    def test_building_loss_ordering(self):
        """Heavier materials should have more loss."""
        assert BUILDING_PENETRATION_DB[BuildingType.WOOD_FRAME] < \
               BUILDING_PENETRATION_DB[BuildingType.CONCRETE] < \
               BUILDING_PENETRATION_DB[BuildingType.METAL_CLAD]

    def test_snr_thresholds_complete(self):
        """SNR thresholds should cover SF 7-12."""
        for sf in range(7, 13):
            assert sf in SNR_THRESHOLD_DB
            assert SNR_THRESHOLD_DB[sf] < 0  # All negative (below noise)


def run_tests():
    """Run all tests without pytest."""
    import traceback

    test_classes = [
        TestHaversineDistance,
        TestFresnelRadius,
        TestFreeSpacePathLoss,
        TestEarthBulge,
        TestKnifeEdgeDiffraction,
        TestDetailedLinkBudget,
        TestRxSensitivity,
        TestLogDistancePathLoss,
        TestRealisticMaxRange,
        TestRadioHorizon,
        TestProcessingGain,
        TestCaptureEffect,
        TestEnvironmentParams,
    ]

    total = 0
    passed = 0
    failed = 0

    for test_class in test_classes:
        print(f"\n{test_class.__name__}")
        print("-" * 40)

        instance = test_class()
        for name in dir(instance):
            if name.startswith("test_"):
                total += 1
                try:
                    getattr(instance, name)()
                    print(f"  PASS: {name}")
                    passed += 1
                except AssertionError as e:
                    print(f"  FAIL: {name}")
                    print(f"        {e}")
                    failed += 1
                except Exception as e:
                    print(f"  ERROR: {name}")
                    traceback.print_exc()
                    failed += 1

    print("\n" + "=" * 40)
    print(f"Total: {total} | Passed: {passed} | Failed: {failed}")

    return failed == 0


if __name__ == "__main__":
    success = run_tests()
    sys.exit(0 if success else 1)
