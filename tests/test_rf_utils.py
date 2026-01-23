"""
RF utility function tests for MeshForge.

Run with: python3 -m pytest tests/test_rf_utils.py -v
Or: python3 tests/test_rf_utils.py
"""

import sys
import os
import math

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from utils.rf import haversine_distance, fresnel_radius, free_space_path_loss, earth_bulge


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


def run_tests():
    """Run all tests without pytest."""
    import traceback

    test_classes = [
        TestHaversineDistance,
        TestFresnelRadius,
        TestFreeSpacePathLoss,
        TestEarthBulge,
        TestDetailedLinkBudget,
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
