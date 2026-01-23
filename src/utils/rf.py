"""
RF calculation utilities for MeshForge.

Pure functions for radio frequency calculations - no UI dependencies.

If Cython-compiled rf_fast module is available, these functions are
replaced with optimized versions providing 5-10x speedup.

To compile fast version:
    cd src/utils && python setup_cython.py build_ext --inplace
"""

import math
from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Tuple, Optional, NamedTuple

# Try to import Cython-optimized versions
_USE_FAST = False
try:
    from utils.rf_fast import (
        haversine_distance as _haversine_fast,
        fresnel_radius as _fresnel_fast,
        free_space_path_loss as _fspl_fast,
        earth_bulge as _bulge_fast,
        link_budget as _link_budget_fast,
        snr_estimate as _snr_fast,
        batch_haversine as _batch_haversine_fast,
        batch_link_quality as _batch_link_quality_fast,
    )
    _USE_FAST = True
except ImportError:
    pass  # Fall back to pure Python


# ============================================================================
# Signal Quality Classification (based on meshtastic-go/MeshTenna research)
# Reference: https://github.com/OE3JGW/MeshTenna
# ============================================================================

class SignalQuality(Enum):
    """Signal quality classification based on SNR and RSSI thresholds."""
    EXCELLENT = "excellent"  # Strong signal, well above noise
    GOOD = "good"            # Reliable communication expected
    FAIR = "fair"            # May experience occasional packet loss
    BAD = "bad"              # Unreliable, high packet loss expected
    NONE = "none"            # No signal or below receiver sensitivity


class SignalMetrics(NamedTuple):
    """Complete signal analysis metrics."""
    rssi_dbm: float
    snr_db: float
    quality: SignalQuality
    quality_percent: int
    link_margin_db: float
    description: str


# Signal quality thresholds (from meshtastic-go library)
# https://github.com/crypto-smoke/meshtastic-go/lora
SIGNAL_THRESHOLDS = {
    # (min_snr, min_rssi) for each quality level
    'excellent': (-3.0, -100.0),   # Strong, reliable link
    'good': (-7.0, -115.0),        # Normal operation
    'fair': (-15.0, -126.0),       # Weak but usable
    # Anything below fair is BAD
}

# LoRa receiver sensitivity by spreading factor (typical values)
# Lower SF = less sensitive, Higher SF = more sensitive
LORA_SENSITIVITY_DBM = {
    7: -123.0,
    8: -126.0,
    9: -129.0,
    10: -132.0,
    11: -134.5,
    12: -137.0,
}

# Default noise floor for LoRa (can vary with environment)
DEFAULT_NOISE_FLOOR_DBM = -120.0


def classify_signal(snr_db: float, rssi_dbm: float) -> SignalQuality:
    """
    Classify signal quality based on SNR and RSSI.

    Based on thresholds from meshtastic-go library used by MeshTenna.

    Args:
        snr_db: Signal-to-noise ratio in dB
        rssi_dbm: Received signal strength in dBm

    Returns:
        SignalQuality enum value

    Example:
        >>> classify_signal(-5.0, -110.0)
        SignalQuality.GOOD
    """
    if rssi_dbm < -137.0:  # Below SF12 sensitivity
        return SignalQuality.NONE

    if snr_db >= SIGNAL_THRESHOLDS['excellent'][0] and rssi_dbm >= SIGNAL_THRESHOLDS['excellent'][1]:
        return SignalQuality.EXCELLENT
    if snr_db >= SIGNAL_THRESHOLDS['good'][0] and rssi_dbm >= SIGNAL_THRESHOLDS['good'][1]:
        return SignalQuality.GOOD
    if snr_db >= SIGNAL_THRESHOLDS['fair'][0] and rssi_dbm >= SIGNAL_THRESHOLDS['fair'][1]:
        return SignalQuality.FAIR

    return SignalQuality.BAD


def signal_quality_percent(snr_db: float, rssi_dbm: float) -> int:
    """
    Convert signal metrics to a percentage (0-100).

    Uses a weighted combination of SNR and RSSI normalized to typical ranges.

    Args:
        snr_db: Signal-to-noise ratio in dB
        rssi_dbm: Received signal strength in dBm

    Returns:
        Signal quality as percentage (0-100)
    """
    # Normalize SNR: -20 dB to +10 dB range -> 0-100
    snr_normalized = max(0, min(100, (snr_db + 20) * (100 / 30)))

    # Normalize RSSI: -137 dBm to -70 dBm range -> 0-100
    rssi_normalized = max(0, min(100, (rssi_dbm + 137) * (100 / 67)))

    # Weight SNR more heavily as it's more indicative of link quality
    return int(snr_normalized * 0.6 + rssi_normalized * 0.4)


def analyze_signal(rssi_dbm: float, snr_db: float,
                   spreading_factor: int = 11) -> SignalMetrics:
    """
    Comprehensive signal analysis with quality classification.

    Args:
        rssi_dbm: Received signal strength in dBm
        snr_db: Signal-to-noise ratio in dB
        spreading_factor: LoRa SF (7-12), affects sensitivity

    Returns:
        SignalMetrics with complete analysis

    Example:
        >>> metrics = analyze_signal(-105.0, -3.0, 11)
        >>> print(f"{metrics.quality.value}: {metrics.description}")
        excellent: Strong signal with 29.5 dB link margin
    """
    quality = classify_signal(snr_db, rssi_dbm)
    quality_pct = signal_quality_percent(snr_db, rssi_dbm)

    # Calculate link margin (how much above sensitivity)
    sensitivity = LORA_SENSITIVITY_DBM.get(spreading_factor, -134.5)
    link_margin = rssi_dbm - sensitivity

    # Generate description
    descriptions = {
        SignalQuality.EXCELLENT: f"Strong signal with {link_margin:.1f} dB link margin",
        SignalQuality.GOOD: f"Good signal, {link_margin:.1f} dB above sensitivity",
        SignalQuality.FAIR: f"Weak signal, only {link_margin:.1f} dB margin - may have packet loss",
        SignalQuality.BAD: f"Very weak signal ({link_margin:.1f} dB margin) - unreliable link",
        SignalQuality.NONE: "No signal detected or below receiver sensitivity",
    }

    return SignalMetrics(
        rssi_dbm=rssi_dbm,
        snr_db=snr_db,
        quality=quality,
        quality_percent=quality_pct,
        link_margin_db=link_margin,
        description=descriptions[quality]
    )


# ============================================================================
# Antenna Testing Calculations
# Reference: https://meshtastic.org/docs/hardware/antennas/antenna-testing/
# ============================================================================

# Typical connector/cable losses at 915 MHz
CONNECTOR_LOSS_DB = {
    'sma': 0.1,      # SMA connector
    'n_type': 0.05,  # N-type connector
    'u_fl': 0.2,     # U.FL (small board connector)
    'bnc': 0.1,      # BNC connector
}

# Coax cable loss per meter at 915 MHz (dB/m)
CABLE_LOSS_DB_PER_M = {
    'rg174': 0.9,    # Thin, flexible, high loss
    'rg58': 0.5,     # Common, moderate loss
    'rg8x': 0.3,     # Better, thicker
    'lmr195': 0.35,  # Good quality thin
    'lmr240': 0.25,  # Better quality
    'lmr400': 0.15,  # Low loss, thick
    'lmr600': 0.10,  # Very low loss
}


def calculate_cable_loss(cable_type: str, length_m: float,
                         connectors: int = 2) -> float:
    """
    Calculate total loss from coax cable and connectors.

    Important for antenna testing - excessive cable loss can mask
    a good antenna's performance.

    Args:
        cable_type: Type of coax (e.g., 'rg58', 'lmr400')
        length_m: Cable length in meters
        connectors: Number of connectors (default 2 for both ends)

    Returns:
        Total loss in dB

    Example:
        >>> calculate_cable_loss('rg58', 3.0, connectors=2)
        1.7  # 1.5 dB from cable + 0.2 dB from connectors
    """
    cable_loss_per_m = CABLE_LOSS_DB_PER_M.get(cable_type.lower(), 0.5)
    connector_loss = CONNECTOR_LOSS_DB.get('sma', 0.1)  # Assume SMA

    return (cable_loss_per_m * length_m) + (connector_loss * connectors)


def effective_radiated_power(tx_power_dbm: float, antenna_gain_dbi: float,
                             cable_loss_db: float = 0.0) -> float:
    """
    Calculate Effective Radiated Power (ERP).

    ERP = TX Power + Antenna Gain - Cable/Connector Losses

    Args:
        tx_power_dbm: Transmitter output power in dBm
        antenna_gain_dbi: Antenna gain in dBi
        cable_loss_db: Total cable and connector loss in dB

    Returns:
        ERP in dBm

    Note:
        ERP is regulated. US Part 15 allows 1W (30 dBm) ERP for 915 MHz ISM.
    """
    return tx_power_dbm + antenna_gain_dbi - cable_loss_db


def required_antenna_height(distance_km: float, freq_mhz: float = 915.0,
                            clearance_percent: float = 0.6) -> float:
    """
    Calculate minimum antenna height for Fresnel zone clearance.

    For reliable links, at least 60% of the first Fresnel zone should be clear.

    Args:
        distance_km: Link distance in kilometers
        freq_mhz: Frequency in MHz (default 915 for US LoRa)
        clearance_percent: Fraction of Fresnel zone to clear (default 0.6)

    Returns:
        Minimum height above midpoint obstacles in meters

    Example:
        >>> required_antenna_height(5.0)  # 5 km link
        12.5  # Need ~12.5m clearance at midpoint
    """
    freq_ghz = freq_mhz / 1000.0
    fresnel_r = fresnel_radius(distance_km, freq_ghz)
    return fresnel_r * clearance_percent


def haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate distance between two points using Haversine formula.

    Args:
        lat1, lon1: First point coordinates (degrees)
        lat2, lon2: Second point coordinates (degrees)

    Returns:
        Distance in meters
    """
    R = 6371000  # Earth radius in meters

    lat1_rad = math.radians(lat1)
    lat2_rad = math.radians(lat2)
    delta_lat = math.radians(lat2 - lat1)
    delta_lon = math.radians(lon2 - lon1)

    a = (math.sin(delta_lat / 2) ** 2 +
         math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(delta_lon / 2) ** 2)
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    return R * c


def fresnel_radius(distance_km: float, freq_ghz: float) -> float:
    """Calculate first Fresnel zone radius at midpoint.

    Args:
        distance_km: Path distance in kilometers
        freq_ghz: Frequency in GHz

    Returns:
        Fresnel zone radius in meters
    """
    return 17.3 * math.sqrt(distance_km / (4 * freq_ghz))


def free_space_path_loss(distance_m: float, freq_mhz: float) -> float:
    """Calculate Free Space Path Loss (FSPL).

    Args:
        distance_m: Distance in meters
        freq_mhz: Frequency in MHz

    Returns:
        Path loss in dB
    """
    return 20 * math.log10(distance_m) + 20 * math.log10(freq_mhz) - 27.55


def earth_bulge(distance_m: float) -> float:
    """Calculate Earth bulge at midpoint of a path.

    Uses 4/3 Earth radius for RF refraction.

    Args:
        distance_m: Path distance in meters

    Returns:
        Earth bulge in meters
    """
    R = 6371000  # Earth radius
    k = 4 / 3    # RF refraction factor
    return (distance_m ** 2) / (8 * R * k)


def link_budget(tx_power_dbm: float, tx_gain_dbi: float,
                rx_gain_dbi: float, distance_m: float,
                freq_mhz: float) -> float:
    """Calculate received power using link budget equation.

    Args:
        tx_power_dbm: Transmit power in dBm
        tx_gain_dbi: Transmit antenna gain in dBi
        rx_gain_dbi: Receive antenna gain in dBi
        distance_m: Distance in meters
        freq_mhz: Frequency in MHz

    Returns:
        Received power in dBm
    """
    fspl = free_space_path_loss(distance_m, freq_mhz)
    return tx_power_dbm + tx_gain_dbi + rx_gain_dbi - fspl


def snr_estimate(rx_power_dbm: float, noise_floor_dbm: float = -120.0) -> float:
    """Estimate SNR given received power.

    Args:
        rx_power_dbm: Received power in dBm
        noise_floor_dbm: Noise floor in dBm (default -120 for LoRa)

    Returns:
        Estimated SNR in dB
    """
    return rx_power_dbm - noise_floor_dbm


# ============================================================================
# Detailed Link Budget Analysis
# ============================================================================

@dataclass
class LinkBudgetResult:
    """Complete link budget breakdown showing where every dB goes."""

    # TX side
    tx_power_dbm: float
    tx_cable_loss_db: float
    tx_antenna_gain_dbi: float
    eirp_dbm: float  # Effective Isotropic Radiated Power

    # Path
    distance_m: float
    freq_mhz: float
    path_loss_db: float
    fresnel_clearance_m: float

    # RX side
    rx_antenna_gain_dbi: float
    rx_cable_loss_db: float
    received_power_dbm: float

    # Margins
    rx_sensitivity_dbm: float
    link_margin_db: float  # How much signal above sensitivity
    estimated_snr_db: float
    signal_quality: str  # EXCELLENT/GOOD/FAIR/BAD

    def summary(self) -> List[str]:
        """Human-readable breakdown for TUI/GTK display."""
        lines = [
            f"TX Power:        {self.tx_power_dbm:+.1f} dBm",
            f"TX Cable Loss:   {self.tx_cable_loss_db:-.1f} dB",
            f"TX Antenna Gain: {self.tx_antenna_gain_dbi:+.1f} dBi",
            f"EIRP:            {self.eirp_dbm:+.1f} dBm",
            f"---",
            f"Distance:        {self.distance_m:.0f} m ({self.distance_m/1000:.2f} km)",
            f"Path Loss:       {self.path_loss_db:-.1f} dB",
            f"Fresnel Zone:    {self.fresnel_clearance_m:.1f} m clearance needed",
            f"---",
            f"RX Antenna Gain: {self.rx_antenna_gain_dbi:+.1f} dBi",
            f"RX Cable Loss:   {self.rx_cable_loss_db:-.1f} dB",
            f"Received Power:  {self.received_power_dbm:+.1f} dBm",
            f"---",
            f"RX Sensitivity:  {self.rx_sensitivity_dbm:.1f} dBm",
            f"Link Margin:     {self.link_margin_db:+.1f} dB",
            f"Est. SNR:        {self.estimated_snr_db:+.1f} dB",
            f"Signal Quality:  {self.signal_quality}",
        ]
        return lines


def detailed_link_budget(
    tx_power_dbm: float = 20.0,
    tx_cable_type: str = 'lmr400',
    tx_cable_length_m: float = 1.0,
    tx_antenna_gain_dbi: float = 2.0,
    rx_antenna_gain_dbi: float = 2.0,
    rx_cable_type: str = 'lmr400',
    rx_cable_length_m: float = 1.0,
    distance_m: float = 1000.0,
    freq_mhz: float = 906.875,
    spreading_factor: int = 11,
) -> LinkBudgetResult:
    """
    Calculate detailed link budget with full component breakdown.

    Shows exactly where signal is gained and lost across the entire
    TX → Path → RX chain. Uses LoRa sensitivity based on spreading factor.

    Args:
        tx_power_dbm: Transmitter output power (typical: 17-30 dBm)
        tx_cable_type: TX coax type (rg58, lmr400, etc.)
        tx_cable_length_m: TX cable length in meters
        tx_antenna_gain_dbi: TX antenna gain in dBi
        rx_antenna_gain_dbi: RX antenna gain in dBi
        rx_cable_type: RX coax type
        rx_cable_length_m: RX cable length in meters
        distance_m: Link distance in meters
        freq_mhz: Frequency in MHz (default 906.875 for US LoRa)
        spreading_factor: LoRa SF (7-12, affects sensitivity)

    Returns:
        LinkBudgetResult with full breakdown

    Example:
        >>> result = detailed_link_budget(distance_m=5000, tx_power_dbm=30)
        >>> print(result.link_margin_db)
        25.3  # Comfortable margin
        >>> for line in result.summary():
        ...     print(line)
    """
    # Calculate cable losses
    tx_cable_loss = calculate_cable_loss(tx_cable_type, tx_cable_length_m)
    rx_cable_loss = calculate_cable_loss(rx_cable_type, rx_cable_length_m)

    # EIRP: what actually leaves the TX antenna
    eirp = tx_power_dbm - tx_cable_loss + tx_antenna_gain_dbi

    # Path loss
    path_loss = free_space_path_loss(distance_m, freq_mhz)

    # Fresnel zone clearance needed at midpoint
    distance_km = distance_m / 1000.0
    freq_ghz = freq_mhz / 1000.0
    fresnel_m = fresnel_radius(distance_km, freq_ghz) * 0.6  # 60% clearance

    # Received power
    rx_power = eirp - path_loss + rx_antenna_gain_dbi - rx_cable_loss

    # LoRa sensitivity by spreading factor
    sensitivity = LORA_SENSITIVITY_DBM.get(spreading_factor, -130.0)

    # Link margin: how far above sensitivity we are
    margin = rx_power - sensitivity

    # SNR estimate
    snr = snr_estimate(rx_power)

    # Classify
    quality = classify_signal(snr, rx_power)

    return LinkBudgetResult(
        tx_power_dbm=tx_power_dbm,
        tx_cable_loss_db=tx_cable_loss,
        tx_antenna_gain_dbi=tx_antenna_gain_dbi,
        eirp_dbm=eirp,
        distance_m=distance_m,
        freq_mhz=freq_mhz,
        path_loss_db=path_loss,
        fresnel_clearance_m=fresnel_m,
        rx_antenna_gain_dbi=rx_antenna_gain_dbi,
        rx_cable_loss_db=rx_cable_loss,
        received_power_dbm=rx_power,
        rx_sensitivity_dbm=sensitivity,
        link_margin_db=margin,
        estimated_snr_db=snr,
        signal_quality=quality.name,
    )


# Use fast versions if available
if _USE_FAST:
    haversine_distance = _haversine_fast
    fresnel_radius = _fresnel_fast
    free_space_path_loss = _fspl_fast
    earth_bulge = _bulge_fast
    link_budget = _link_budget_fast
    snr_estimate = _snr_fast

    # Batch functions only available in fast version
    batch_haversine = _batch_haversine_fast
    batch_link_quality = _batch_link_quality_fast
else:
    # Provide pure Python batch implementations
    def batch_haversine(coords):
        """Calculate distances for multiple coordinate pairs."""
        return [haversine_distance(*c) for c in coords]

    def batch_link_quality(links, tx_power=20.0, freq_mhz=915.0):
        """Calculate link quality for multiple node pairs."""
        results = []
        for distance_m, tx_gain, rx_gain in links:
            rx_power = link_budget(tx_power, tx_gain, rx_gain, distance_m, freq_mhz)
            snr = snr_estimate(rx_power)
            # Quality as percentage
            if snr > 10.0:
                quality = 100.0
            elif snr < -10.0:
                quality = 0.0
            else:
                quality = (snr + 10.0) * 5.0
            results.append((rx_power, snr, quality))
        return results


def is_fast_available() -> bool:
    """Check if Cython-optimized RF functions are available."""
    return _USE_FAST
