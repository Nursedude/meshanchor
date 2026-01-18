"""
Site Planner Mixin for MeshForge Launcher TUI.

Handles RF coverage planning, range estimation, preset comparison,
antenna guidelines, and external tool references.
Extracted from main.py to reduce file size.
"""


class SitePlannerMixin:
    """Mixin providing site planner methods for the launcher."""

    def _site_planner_menu(self):
        """Site planner menu for RF coverage planning."""
        while True:
            choices = [
                ("link", "Link Budget Calculator"),
                ("range", "Range Estimator"),
                ("presets", "LoRa Preset Comparison"),
                ("fresnel", "Fresnel Zone Calculator"),
                ("antenna", "Antenna Guidelines"),
                ("freq", "Frequency Reference"),
                ("tools", "External Planning Tools"),
                ("back", "Back"),
            ]

            choice = self.dialog.menu(
                "Site Planner",
                "RF coverage and link planning:",
                choices
            )

            if choice is None or choice == "back":
                break

            if choice == "link":
                self._calc_link_budget()  # From RFToolsMixin
            elif choice == "range":
                self._estimate_range()
            elif choice == "presets":
                self._compare_presets()
            elif choice == "fresnel":
                self._calc_fresnel()  # From RFToolsMixin
            elif choice == "antenna":
                self._antenna_guidelines()
            elif choice == "freq":
                self._frequency_reference()
            elif choice == "tools":
                self._external_tools()

    def _estimate_range(self):
        """Estimate communication range based on parameters."""
        # Get TX power
        tx_pwr = self.dialog.inputbox("Range Estimator", "TX Power (dBm):", "20")
        if not tx_pwr:
            return

        # Get antenna gains
        ant_gain = self.dialog.inputbox("Range Estimator", "Total Antenna Gain (dBi):", "4")
        if not ant_gain:
            return

        # Get preset
        presets = [
            ("SHORT_TURBO", "-105 dBm sensitivity"),
            ("SHORT_FAST", "-110 dBm sensitivity"),
            ("MEDIUM_FAST", "-120 dBm sensitivity"),
            ("LONG_FAST", "-125 dBm sensitivity"),
            ("LONG_SLOW", "-132 dBm sensitivity"),
        ]

        preset = self.dialog.menu(
            "Select Preset",
            "Choose LoRa modem preset:",
            presets
        )

        if not preset:
            return

        # Sensitivity values
        sens_map = {
            "SHORT_TURBO": -105,
            "SHORT_FAST": -110,
            "MEDIUM_FAST": -120,
            "LONG_FAST": -125,
            "LONG_SLOW": -132,
        }

        try:
            import math
            tx_p = float(tx_pwr)
            ant_g = float(ant_gain)
            sens = sens_map.get(preset, -125)

            # Link budget
            link_budget = tx_p + ant_g - sens

            # Estimate range using FSPL formula (915 MHz)
            # FSPL = 20*log10(d) + 20*log10(f) + 32.45
            # d = 10^((FSPL - 20*log10(f) - 32.45) / 20)
            freq_mhz = 915
            max_fspl = link_budget
            range_km = 10 ** ((max_fspl - 20 * math.log10(freq_mhz) - 32.45) / 20)

            # Apply terrain factor (0.3-0.7 of theoretical)
            los_range = range_km
            urban_range = range_km * 0.3
            rural_range = range_km * 0.5

            text = f"""Range Estimation:

Preset: {preset}
TX Power: {tx_p} dBm
Antenna Gain: {ant_g} dBi
RX Sensitivity: {sens} dBm

Link Budget: {link_budget:.1f} dB

Estimated Range (915 MHz):
  Line of Sight: {los_range:.1f} km
  Rural/Suburban: {rural_range:.1f} km
  Urban/Dense: {urban_range:.1f} km

Note: Actual range depends on terrain,
vegetation, and antenna height."""

            self.dialog.msgbox("Range Estimation", text)

        except ValueError:
            self.dialog.msgbox("Error", "Invalid number entered")

    def _compare_presets(self):
        """Compare LoRa modem presets."""
        text = """LoRa Modem Preset Comparison:

Preset          BW    SF  Range     Speed
──────────────────────────────────────────
SHORT_TURBO    500   7   <1 km     Fastest
SHORT_FAST     250   7   1-5 km    Fast
SHORT_SLOW     125   7   1-5 km    Medium
MEDIUM_FAST    250   10  5-20 km   Medium
MEDIUM_SLOW    125   10  5-20 km   Slower
LONG_FAST      250   11  10-30 km  Default
LONG_MODERATE  125   11  15-40 km  Slower
LONG_SLOW      125   12  20-50 km  Slowest

Higher SF = Longer range, slower speed
Lower BW = Better sensitivity, slower speed

Recommended:
  Gateway: SHORT_TURBO or MEDIUM_FAST
  Rural: LONG_FAST or LONG_MODERATE
  Urban: SHORT_FAST or MEDIUM_FAST"""

        self.dialog.msgbox("Preset Comparison", text)

    def _antenna_guidelines(self):
        """Show antenna guidelines."""
        text = """Antenna Guidelines for 915 MHz:

Height:
  - Higher is better for range
  - 10m height doubles range vs 2m
  - Avoid below tree canopy

Antenna Types:
  - Dipole: 2.15 dBi, omnidirectional
  - 1/4 wave GP: 2-3 dBi, omnidirectional
  - Yagi: 6-12 dBi, directional
  - Colinear: 5-9 dBi, omnidirectional

Cable Loss (per 10m @ 915MHz):
  - RG58: ~2.5 dB (avoid)
  - RG8X: ~1.8 dB
  - LMR-240: ~1.3 dB
  - LMR-400: ~0.7 dB (recommended)

Best Practices:
  - Mount antenna clear of obstructions
  - Use quality coax, keep runs short
  - Ground antenna mast for lightning
  - Weatherproof all connections"""

        self.dialog.msgbox("Antenna Guidelines", text)

    def _frequency_reference(self):
        """Show frequency reference."""
        text = """LoRa Frequency Reference:

Region      Frequencies       TX Power
───────────────────────────────────────
US/FCC      902-928 MHz       30 dBm
EU 868      863-870 MHz       14-27 dBm
EU 433      433.05-434.79     10 dBm
UK          868 MHz           25 dBm
AU/NZ       915-928 MHz       30 dBm
AS          920-923 MHz       varies
CN          470-510 MHz       17 dBm
JP          920-923 MHz       13 dBm

Default Meshtastic Frequencies:
  US: 906.875 MHz (Ch 0)
  EU 868: 869.525 MHz
  EU 433: 433.175 MHz

ISM Band Limits (US):
  EIRP: 36 dBm (4W) max
  Duty Cycle: No limit (FHSS)"""

        self.dialog.msgbox("Frequency Reference", text)

    def _external_tools(self):
        """Show external planning tools."""
        text = """External RF Planning Tools:

Web-Based:
  meshtastic.org/docs/software/coverage/
    - Meshtastic Site Planner
    - Coverage prediction

  heywhatsthat.com
    - Line of sight analysis
    - Terrain profiles

  splat.ecso.org
    - Detailed RF coverage
    - Terrain analysis

Software:
  Radio Mobile (Windows/Wine)
    - Professional RF planning
    - Free for amateur use

  SPLAT! (Linux)
    - RF Signal Propagation
    - Terrain analysis

  CloudRF
    - Cloud-based planning
    - API available

Tip: Use these tools to plan
repeater locations and verify
line-of-sight paths."""

        self.dialog.msgbox("External Planning Tools", text)
