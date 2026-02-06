"""
Site Planner Mixin for MeshForge Launcher TUI.

Handles RF coverage planning, range estimation, preset comparison,
antenna guidelines, and external tool references.
Extracted from main.py to reduce file size.
"""

from utils.rf import DeployEnvironment, BuildingType


# Menu labels for environment selection
_ENV_CHOICES = [
    ("free_space", "Free Space / Clear LOS"),
    ("rural_open", "Rural Open Terrain"),
    ("suburban", "Suburban / Residential"),
    ("urban_elevated", "Urban w/ Elevated Gateway"),
    ("urban_ground", "Urban Ground Level"),
    ("dense_urban", "Dense Urban / Downtown"),
    ("forest", "Forest / Heavy Vegetation"),
    ("over_water", "Over Water / Coastal"),
    ("indoor", "Indoor (same building)"),
]

_ENV_MAP = {v.value: v for v in DeployEnvironment}


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

    def _select_environment(self):
        """Let the user pick a deployment environment.

        Returns:
            DeployEnvironment or None if cancelled.
        """
        choice = self.dialog.menu(
            "Environment",
            "Select deployment environment:",
            _ENV_CHOICES,
        )
        if choice is None:
            return None
        return _ENV_MAP.get(choice, DeployEnvironment.SUBURBAN)

    def _estimate_range(self):
        """Estimate communication range using environment-aware model."""
        from utils.preset_impact import PresetAnalyzer, PRESET_PARAMS

        # TX power
        tx_pwr = self.dialog.inputbox("Range Estimator", "TX Power (dBm):", "22")
        if not tx_pwr:
            return

        # Antenna gain
        ant_gain = self.dialog.inputbox("Range Estimator", "Total Antenna Gain (dBi):", "4.3")
        if not ant_gain:
            return

        # Antenna height
        ant_height = self.dialog.inputbox("Range Estimator", "Antenna Height (m):", "2")
        if not ant_height:
            return

        # Preset selection
        preset_choices = [
            (name, params['desc'])
            for name, params in PRESET_PARAMS.items()
        ]
        preset = self.dialog.menu(
            "Select Preset",
            "Choose LoRa modem preset:",
            preset_choices,
        )
        if not preset:
            return

        # Environment selection
        env = self._select_environment()
        if env is None:
            return

        try:
            tx_p = float(tx_pwr)
            ant_g = float(ant_gain)
            ant_h = float(ant_height)

            analyzer = PresetAnalyzer(
                tx_power_dbm=int(tx_p),
                tx_gain_dbi=ant_g / 2,
                rx_gain_dbi=ant_g / 2,
                environment=env,
                antenna_height_m=ant_h,
            )
            impact = analyzer.analyze_preset(preset)

            # Also show LOS reference
            los_analyzer = PresetAnalyzer(
                tx_power_dbm=int(tx_p),
                tx_gain_dbi=ant_g / 2,
                rx_gain_dbi=ant_g / 2,
                environment=DeployEnvironment.FREE_SPACE,
                antenna_height_m=ant_h,
            )
            los_impact = los_analyzer.analyze_preset(preset)

            env_label = dict(_ENV_CHOICES).get(env.value, env.value)

            text = f"""Range Estimation ({env_label}):

Preset: {preset}
TX Power: {tx_p:.0f} dBm
Antenna Gain: {ant_g} dBi
Antenna Height: {ant_h} m
Sensitivity: {impact.sensitivity_dbm:.1f} dBm
Link Budget: {impact.link_budget_db:.1f} dB

Estimated Max Range (915 MHz):
  {env_label}: {impact.max_range_km:.1f} km
  Free Space (LOS ref): {los_impact.max_range_km:.1f} km

Coverage Area: {impact.coverage_area_km2:.1f} km2
Airtime: {impact.airtime_ms:.0f} ms
Throughput: {impact.throughput_bps / 1000:.2f} kbps

Note: Uses log-distance propagation model
with environment-specific path loss exponent
and fade margin."""

            self.dialog.msgbox("Range Estimation", text)

        except (ValueError, TypeError):
            self.dialog.msgbox("Error", "Invalid number entered")

    def _compare_presets(self):
        """Compare LoRa modem presets with environment-aware range."""
        from utils.preset_impact import PresetAnalyzer, format_comparison_table

        # Let user pick environment
        env = self._select_environment()
        if env is None:
            env = DeployEnvironment.FREE_SPACE

        analyzer = PresetAnalyzer(environment=env)
        comp = analyzer.compare()
        table = format_comparison_table(comp)

        env_label = dict(_ENV_CHOICES).get(env.value, env.value)
        header = f"Preset Comparison ({env_label})"

        self.dialog.msgbox(header, table)

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
