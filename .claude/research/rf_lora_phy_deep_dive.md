# RF Engine Deep Dive — Propagation Models, Environment Modeling & Gap Analysis

> **Research Date**: 2026-02-06
> **Context**: MeshForge NOC RF calculations (`src/utils/rf.py`, `preset_impact.py`, `multihop.py`)
> **Companion Document**: `lora_physical_layer.md` — LoRa CSS modulation, demodulation, SNR limits
> **Purpose**: Bridge academic RF research → concrete MeshForge code improvements

---

## Table of Contents

1. [Real-World LoRa Propagation Studies](#1-real-world-lora-propagation-studies)
2. [Path Loss Models for LoRa](#2-path-loss-models-for-lora)
3. [Environment-Specific Loss Factors](#3-environment-specific-loss-factors)
4. [Range Records & Meshtastic Field Data](#4-range-records--meshtastic-field-data)
5. [MeshForge RF Engine Gap Analysis](#5-meshforge-rf-engine-gap-analysis)
6. [Concrete Improvements — Ranked by Impact](#6-concrete-improvements--ranked-by-impact)
7. [Implementation Roadmap](#7-implementation-roadmap)
8. [References](#8-references)

---

## 1. Real-World LoRa Propagation Studies

### 1.1 Key Field Campaigns

| Study | Location | Freq | Environment | PLE (n) | σ (dB) | Max Range |
|-------|----------|------|-------------|---------|--------|-----------|
| Rademacher et al. (2021) | Bonn, Germany | 868 MHz | Urban, elevated GW | **1.58** | 9.9 | 2.1 km |
| Petäjäjärvi et al. (2015/2017) | Oulu, Finland | 868 MHz | Urban/water | 2.32 | 7.8 | 15 km (over water) |
| González-Palacio et al. (2023) | Medellín, Colombia | 915 MHz | Mountainous urban | 2.8-3.2 | 8.5 | 4.5 km |
| El Chall et al. (2021) | Beirut, Lebanon | 868 MHz | Dense urban | 3.1 | 10.2 | 1.8 km |
| Ferreira et al. (2020) | Brazil | 915 MHz | Tropical forest | **5.6** | 12.0 | 0.25 km |
| IISc study | India | 865 MHz | Campus + urban | 2.1 | 6.5 | 3.2 km |

### 1.2 Critical Insight: Low Urban PLE

The Rademacher Bonn study found n=1.58 — lower than free space (n=2). This is **not** a LoRa modulation property. It's an artifact of **elevated gateway antennas** creating a waveguide-like propagation environment where signals reflect off buildings and the ground, partially canceling path loss. This effect is strongest when:
- Gateway antenna is 15-30+ meters high (rooftop)
- Mobile node is at ground level (1-2 m)
- Urban canyon geometry channels RF energy

**Implication for MeshForge**: Cannot use a single PLE. Must parameterize by environment AND antenna height.

### 1.3 Over-Water Propagation

Water provides near-perfect RF reflection. The Oulu study achieved 15 km over lake/coastal water with stock LoRa hardware at 868 MHz. Effective PLE over water approaches 1.8-2.0 (near free space).

---

## 2. Path Loss Models for LoRa

### 2.1 Free Space Path Loss (Current MeshForge Model)

```
FSPL(d) = 20*log10(d) + 20*log10(f) - 27.55    [d in meters, f in MHz]
```

**When it's accurate**: Line-of-sight, no reflections, no obstructions. Satellite, mountain-to-mountain, elevated LOS links.

**When it fails**: Any terrestrial deployment with buildings, vegetation, terrain.

### 2.2 Log-Distance Path Loss Model

```
PL(d) = PL(d0) + 10 * n * log10(d/d0) + X_σ
```

Where:
- `PL(d0)` = reference path loss at distance d0 (usually 1 m or 100 m)
- `n` = path loss exponent (environment-dependent)
- `X_σ` = zero-mean Gaussian random variable with std dev σ (shadow fading)
- `d0` = reference distance

**Recommended PLE values for MeshForge (915 MHz):**

| Environment | PLE (n) | σ (dB) | Notes |
|-------------|---------|--------|-------|
| Free space / LOS | 2.0 | 0 | Theoretical baseline |
| Open rural flat | 2.0-2.2 | 4-6 | Minimal obstructions |
| Suburban | 2.5-3.0 | 6-8 | Residential, partial obstruction |
| Urban (elevated GW) | 1.5-2.0 | 8-10 | Rooftop gateway, waveguide effect |
| Urban (ground level) | 3.0-3.5 | 8-12 | Both antennas near ground |
| Dense urban | 3.5-4.5 | 10-14 | Downtown, canyon streets |
| Indoor (same floor) | 1.5-2.5 | 3-6 | Open plan office |
| Indoor (through floors) | 4.0-6.0 | 8-12 | Concrete floors |
| Dense forest / tropical | 5.0-6.0 | 10-14 | Heavy vegetation |
| Over water | 1.8-2.0 | 2-4 | Near free-space |

### 2.3 Okumura-Hata Model

Valid for 150-1500 MHz (includes 868/915 MHz). Best for macro-cell scenarios with elevated base stations.

```python
def okumura_hata_urban(freq_mhz, h_bs_m, h_ms_m, dist_km):
    """Okumura-Hata urban path loss (dB).

    Args:
        freq_mhz: 150-1500 MHz
        h_bs_m: Base station (gateway) antenna height, 30-200 m
        h_ms_m: Mobile station antenna height, 1-10 m
        dist_km: Distance, 1-20 km
    """
    # Mobile antenna correction (small/medium city)
    a_hm = (1.1 * math.log10(freq_mhz) - 0.7) * h_ms_m - \
           (1.56 * math.log10(freq_mhz) - 0.8)

    pl = (69.55 + 26.16 * math.log10(freq_mhz)
          - 13.82 * math.log10(h_bs_m) - a_hm
          + (44.9 - 6.55 * math.log10(h_bs_m)) * math.log10(dist_km))
    return pl
```

**Limitation**: Assumes base station height 30-200 m. Most Meshtastic gateways are 3-30 m. For lower heights, the log-distance model with measured PLE is more appropriate.

**Suburban correction**: Subtract 2 * [log10(freq_mhz/28)]^2 + 5.4 dB
**Open area correction**: Subtract 4.78 * [log10(freq_mhz)]^2 - 18.33 * log10(freq_mhz) + 40.94 dB

### 2.4 ITU-R Models

| Model | Use Case | Accuracy for LoRa |
|-------|----------|-------------------|
| **P.1411** | Short-range outdoor (< 1 km) | Good for urban LoRa |
| **P.1238** | Indoor propagation | Good for building penetration |
| **P.2001** | General terrestrial (all distances) | Complex but comprehensive |
| **P.526** | Diffraction over terrain | Good complement to MeshForge's knife-edge |
| **P.2109** | Building entry loss | Best for outdoor-to-indoor |

The Glasgow study (MacCartney et al.) found **Deygout 94** (multiple knife-edge diffraction) had the best accuracy for LoRa: MAE=0.83 dB, SD=4.17 dB.

### 2.5 Two-Ray Ground Reflection

Already partially implemented in `rf_fast.pyx`. The breakpoint distance where the model transitions from ~FSPL to 40*log10(d):

```
d_break = 4 * h_tx * h_rx / wavelength
```

At 915 MHz with 10 m antennas: d_break ≈ 1.2 km. Beyond this, path loss increases at 40 dB/decade instead of 20 dB/decade.

---

## 3. Environment-Specific Loss Factors

### 3.1 Building Penetration Loss

From ITU-R P.2109 and field measurements at 868/915 MHz:

| Material | Loss (dB) | Notes |
|----------|-----------|-------|
| Single-pane glass | 0.5-2 | Low loss, good indoor coverage |
| Drywall/plasterboard | 0.5-1.5 | Residential interior walls |
| Wood frame wall | 3-6 | Exterior residential wall |
| Brick (single layer) | 6-10 | Older construction |
| Concrete block | 10-15 | Commercial/industrial |
| Reinforced concrete | 13-22 | Modern commercial, parking structures |
| Metal-clad building | 20-35 | Industrial, metal roofing |
| Elevator shaft | 30-40+ | Steel + concrete composite |

**Whole-building penetration (outdoor-to-indoor):**

| Building Type | Loss (dB) |
|---------------|-----------|
| Wood-frame residential | 10-25 |
| Brick/concrete residential | 20-30 |
| Commercial office | 25-35 |
| Metal-clad industrial | 35-40 |
| Underground/basement | 40-50+ |

### 3.2 Foliage Loss

From ITU-R P.833-10 and field measurements:

```
Foliage excess loss ≈ 0.1-0.4 dB/m through woodland at 915 MHz
```

| Vegetation | Excess Loss | Notes |
|------------|-------------|-------|
| Light foliage (sparse trees) | 0.1-0.2 dB/m | Deciduous in leaf |
| Dense deciduous forest | 0.2-0.4 dB/m | Full canopy |
| Coniferous forest | 0.3-0.5 dB/m | Year-round |
| Tropical rainforest | 0.4-0.8 dB/m | Dense, wet |
| Single tree | 2-8 dB total | Depends on species, size |

**Seasonal variation**: ~2 dB between full leaf and bare branches (deciduous).

**Brazil study result**: Range dropped from 2 km (open) to 250 m (forest) — **8x reduction**, consistent with 5-6 dB PLE.

### 3.3 Rain Attenuation

**Negligible at 900 MHz.** Even extreme rain (100 mm/h) over 10 km produces only ~0.1 dB of attenuation. Rain becomes significant only above ~5 GHz.

MeshForge should **not** include rain attenuation for LoRa links. This is a non-factor.

### 3.4 Temperature Effects

- Thermal noise floor scales with temperature: N0 = kTB
- At 290K (17°C): -174 dBm/Hz (standard reference)
- At 330K (57°C, hot climate): -173.4 dBm/Hz (+0.6 dB)
- At 250K (-23°C, cold climate): -174.6 dBm/Hz (-0.6 dB)

Receiver sensitivity changes by approximately **0.015 dB/K**:
- Full industrial range (-40°C to +85°C): ~1.86 dB total variation
- Normal outdoor range (0°C to 40°C): ~0.6 dB variation

**TCXO recommendation**: For solar-powered installations (>70°C) or cold climate (<-20°C), TCXO-equipped modules (e.g., Heltec V3, RAK4631) are recommended due to crystal oscillator drift. Non-TCXO modules can drift enough to fail synchronization at extreme temperatures.

**MeshForge impact**: Temperature correction is a nice-to-have, not critical. Only relevant for extreme environment deployments.

### 3.5 Atmospheric Absorption

At 915 MHz: ~0.01 dB/km. Completely negligible for all practical LoRa distances.

---

## 4. Range Records & Meshtastic Field Data

### 4.1 LoRaWAN World Records

| Record | Distance | Configuration | Conditions |
|--------|----------|---------------|------------|
| **1,336 km** | Netherlands → Austria | SF12, BW 125 kHz | Atmospheric ducting, gateway at altitude |
| 766 km | Switzerland | SF12, BW 125 kHz | Mountain-to-mountain LOS |
| 702 km | Various | Multiple | Balloon/HAB flights |

### 4.2 Meshtastic Records

| Record | Distance | Configuration | Conditions |
|--------|----------|---------------|------------|
| **331 km** | Austria → Italy (Adriatic) | RAK4631, SF12, BW 62.5, CR 4/8 | Mountain-to-mountain |
| 254 km | Various mountain paths | SF12, BW 125 kHz | Elevated terrain |
| ~100 km | Multiple US locations | LongFast (SF11/250) | Mountain-to-valley |

### 4.3 Typical Meshtastic Range by Environment

| Environment | SHORT_FAST | LONG_FAST | LONG_SLOW | VERY_LONG_SLOW |
|-------------|-----------|-----------|-----------|----------------|
| Dense urban | 0.2-0.5 km | 0.5-2 km | 1-3 km | 2-5 km |
| Suburban | 0.5-1 km | 2-5 km | 5-10 km | 8-15 km |
| Rural open | 1-3 km | 5-15 km | 15-30 km | 25-50 km |
| Mountain LOS | 3-10 km | 20-60 km | 50-100 km | 100-330 km |
| Dense forest | 0.1-0.3 km | 0.2-0.5 km | 0.3-0.8 km | 0.4-1 km |
| Over water | 1-3 km | 5-20 km | 15-40 km | 30-60 km |

### 4.4 Antenna Height: The Single Most Impactful Variable

Radio horizon for 4/3-earth model:
```
d_horizon (km) = 4.12 * sqrt(h_m)
```

| Antenna Height | Radio Horizon | Comment |
|---------------|---------------|---------|
| 1.5 m (handheld) | 5.0 km | Walking around |
| 3 m (rooftop) | 7.1 km | Single-story house |
| 10 m (mast/tree) | 13.0 km | Typical mast install |
| 30 m (tower) | 22.5 km | Purpose-built tower |
| 100 m (hilltop) | 41.2 km | Elevated terrain |
| 1000 m (mountain) | 130.3 km | Serious elevation |

**Two antennas**: Combined horizon = `4.12 * (sqrt(h1) + sqrt(h2))`

Example: 10 m mast + 1000 m mountain = 13.0 + 130.3 = **143.3 km** radio horizon.

---

## 5. MeshForge RF Engine Gap Analysis

Cross-referencing the research above against the current RF engine source code:

### 5.1 What MeshForge Does Well

| Feature | File | Status |
|---------|------|--------|
| FSPL calculation | `rf.py:317` | Correct implementation |
| Haversine distance | `rf.py:278` | Correct |
| Fresnel zone radius | `rf.py:302` | Correct |
| Earth bulge (4/3 earth) | `rf.py:332` | Correct |
| Knife-edge diffraction | `rf.py:384` | Good Lee approximation |
| Multi-obstacle (Epstein-Peterson) | `rf.py:456` | Conservative but reasonable |
| Cable/connector loss | `rf.py:188-231` | Good catalog |
| Signal quality classification | `rf.py:85` | Based on meshtastic-go, practical |
| Detailed link budget | `rf.py:541` | Full TX→path→RX chain |
| All 9 Meshtastic presets | `preset_impact.py:130` | Correct SF/BW/CR values |
| Sensitivity formula | `preset_impact.py:182` | Textbook correct |
| Airtime calculation | `preset_impact.py:215` | Matches Semtech modem guide |
| Multi-hop analysis | `multihop.py:250` | Sound methodology |
| Coverage zones by quality | `preset_impact.py:425` | Good user-facing feature |
| Cython optimization | `rf_fast.pyx` | 5-10x speedup for batch ops |
| Antenna patterns | `antenna_patterns.py` | cos^n directional modeling |

### 5.2 Gaps Identified

#### GAP 1: FSPL-Only Propagation (HIGH IMPACT)
**Current**: `rf.py` and `multihop.py` use only FSPL for range/margin calculations.
**Problem**: FSPL predicts 300+ km for LongFast. Real urban range is 2-5 km. Off by 100x.
**Fix**: Add log-distance model with environment-specific PLE. Make environment a first-class parameter.

#### GAP 2: No Environment Selection (HIGH IMPACT)
**Current**: No concept of deployment environment anywhere in the RF engine.
**Problem**: A 5 km link in dense forest is completely different from 5 km mountain LOS.
**Fix**: Add `Environment` enum and environment-aware path loss.

#### GAP 3: No Fade Margin in Range Calculations (HIGH IMPACT)
**Current**: `preset_impact.py` max range = FSPL inversion with zero fade margin.
**Problem**: Theoretical max range ≠ reliable range. Users need the reliable number.
**Fix**: Default 10-20 dB fade margin in range estimates, configurable by environment.

#### GAP 4: Fixed BW=125kHz in rf.py Sensitivity Table (MEDIUM IMPACT)
**Current**: `LORA_SENSITIVITY_DBM` assumes BW=125 kHz for all SFs.
**Problem**: Meshtastic presets use 62.5, 125, 250, and 500 kHz. LongFast (SF11/250) sensitivity is -131.5 dBm, not -134.5 dBm.
**Fix**: Use `preset_impact.py`'s `sensitivity()` formula instead of the lookup table, or parameterize by BW.

#### GAP 5: No Coding Rate Impact on Link Budget (MEDIUM IMPACT)
**Current**: CR affects airtime/throughput in `preset_impact.py` but not link reliability.
**Problem**: CR 4/8 provides 7-8x BER improvement at edge-of-coverage. This means CR 4/8 presets have effectively 1-2 dB more margin than CR 4/5 at the same SNR.
**Fix**: Add coding gain factor to reliability calculations.

#### GAP 6: No Building Penetration Loss (MEDIUM IMPACT)
**Current**: No way to account for indoor/outdoor transitions.
**Problem**: A node inside a concrete building has 15-25 dB less signal than a node outside.
**Fix**: Add building penetration loss as an optional parameter.

#### GAP 7: No Foliage Loss (MEDIUM IMPACT)
**Current**: No vegetation attenuation modeling.
**Problem**: Dense forest can reduce range by 8x (PLE 5-6 vs 2).
**Fix**: Add foliage excess loss parameter, folded into environment PLE.

#### GAP 8: No Capture Effect Analysis (LOW IMPACT)
**Current**: No modeling of co-channel interference.
**Problem**: Can't predict mesh collision behavior in dense networks.
**Fix**: Add `will_capture(rssi_wanted, rssi_interferer)` function.

#### GAP 9: No Radio Horizon Limit (LOW IMPACT)
**Current**: `preset_impact.py:317` caps range at 200 km (hardcoded).
**Problem**: Should use actual radio horizon based on antenna height.
**Fix**: Compute radio horizon from antenna heights and limit FSPL-derived range.

#### GAP 10: No Battery Life Estimation (LOW IMPACT)
**Current**: No power consumption modeling.
**Problem**: Users choosing presets need to understand battery impact.
**Fix**: Add TX/RX current × duty cycle model per preset.

#### GAP 11: Sensitivity Table vs Formula Inconsistency (LOW IMPACT)
**Current**: `rf.py` uses lookup table, `preset_impact.py` uses formula. Both exist independently.
**Problem**: Two sources of truth for the same value.
**Fix**: Single source: formula in `preset_impact.py`, deprecate or remove table from `rf.py`.

---

## 6. Concrete Improvements — Ranked by Impact

### Improvement 1: Environment-Aware Path Loss (HIGH — addresses GAPs 1, 2, 3, 7)

**Add to `rf.py`:**

```python
class Environment(Enum):
    """Deployment environment for path loss modeling."""
    FREE_SPACE = "free_space"        # LOS, no reflections
    RURAL_OPEN = "rural_open"        # Open terrain, minimal obstruction
    SUBURBAN = "suburban"             # Residential, partial obstruction
    URBAN_ELEVATED = "urban_elevated" # Rooftop gateway, urban canyon
    URBAN_GROUND = "urban_ground"    # Both antennas near ground level
    DENSE_URBAN = "dense_urban"      # Downtown core, heavy obstruction
    FOREST = "forest"                # Dense vegetation
    OVER_WATER = "over_water"        # Lake, coastal, open ocean
    INDOOR = "indoor"                # Same building, through walls

# Measured/literature PLE and shadow fading values
ENVIRONMENT_PARAMS = {
    Environment.FREE_SPACE:     (2.0, 0.0, 0),
    Environment.RURAL_OPEN:     (2.1, 5.0, 10),
    Environment.SUBURBAN:       (2.7, 7.0, 15),
    Environment.URBAN_ELEVATED: (1.8, 9.0, 15),
    Environment.URBAN_GROUND:   (3.2, 10.0, 20),
    Environment.DENSE_URBAN:    (4.0, 12.0, 25),
    Environment.FOREST:         (5.0, 11.0, 20),
    Environment.OVER_WATER:     (1.9, 3.0, 8),
    Environment.INDOOR:         (2.0, 4.0, 10),
}  # (path_loss_exponent, shadow_fading_std_db, default_fade_margin_db)


def log_distance_path_loss(distance_m: float, freq_mhz: float,
                           environment: Environment = Environment.SUBURBAN,
                           d0_m: float = 1.0) -> float:
    """Calculate path loss using log-distance model with environment-specific PLE.

    PL(d) = FSPL(d0) + 10 * n * log10(d/d0)

    This replaces pure FSPL for terrestrial links. For LOS elevated links,
    FSPL remains appropriate (use Environment.FREE_SPACE).

    Args:
        distance_m: Distance in meters (must be > 0).
        freq_mhz: Frequency in MHz.
        environment: Deployment environment.
        d0_m: Reference distance in meters (default 1.0).

    Returns:
        Path loss in dB (deterministic component, no fading).
    """
    if distance_m <= 0 or freq_mhz <= 0:
        return 0.0

    n, _, _ = ENVIRONMENT_PARAMS[environment]

    # Reference path loss at d0 (use FSPL)
    pl_d0 = free_space_path_loss(d0_m, freq_mhz) if d0_m > 0 else 0.0

    if distance_m <= d0_m:
        return free_space_path_loss(distance_m, freq_mhz)

    return pl_d0 + 10.0 * n * math.log10(distance_m / d0_m)


def realistic_max_range(link_budget_db: float, freq_mhz: float,
                        environment: Environment = Environment.SUBURBAN,
                        d0_m: float = 1.0) -> float:
    """Invert log-distance model to find max range with fade margin.

    Args:
        link_budget_db: Total link budget in dB (EIRP - sensitivity + gains).
        freq_mhz: Frequency in MHz.
        environment: Deployment environment.
        d0_m: Reference distance in meters.

    Returns:
        Maximum reliable range in meters.
    """
    n, _, fade_margin = ENVIRONMENT_PARAMS[environment]

    effective_budget = link_budget_db - fade_margin
    pl_d0 = free_space_path_loss(d0_m, freq_mhz) if d0_m > 0 else 0.0

    exponent = (effective_budget - pl_d0) / (10.0 * n)
    return d0_m * (10.0 ** exponent)
```

**Impact**: Transforms range predictions from "300 km theoretical" to "3 km suburban realistic." Single highest-impact improvement.

### Improvement 2: Bandwidth-Aware Sensitivity (MEDIUM — addresses GAP 4)

**Add to `rf.py`:**

```python
# SNR demodulation thresholds (LoRa specification)
SNR_THRESHOLD_DB = {
    7: -7.5, 8: -10.0, 9: -12.5,
    10: -15.0, 11: -17.5, 12: -20.0,
}

def rx_sensitivity(spreading_factor: int, bandwidth_hz: float,
                   noise_figure_db: float = 6.0) -> float:
    """Calculate receiver sensitivity for any SF/BW combination.

    Formula: Sensitivity = -174 + 10*log10(BW) + NF + SNR_threshold

    Args:
        spreading_factor: LoRa SF (7-12).
        bandwidth_hz: Channel bandwidth in Hz (62500, 125000, 250000, 500000).
        noise_figure_db: Receiver noise figure in dB (default 6.0 for SX1262/SX1276).

    Returns:
        Sensitivity in dBm.
    """
    snr_limit = SNR_THRESHOLD_DB.get(spreading_factor, -15.0)
    return -174.0 + 10.0 * math.log10(bandwidth_hz) + noise_figure_db + snr_limit
```

**Impact**: Correct sensitivity for all 9 Meshtastic presets. Currently `rf.py:72` only covers BW=125 kHz.

### Improvement 3: Building Penetration Loss (MEDIUM — addresses GAP 6)

**Add to `rf.py`:**

```python
class BuildingType(Enum):
    """Building construction type for penetration loss."""
    NONE = "none"                    # Outdoor to outdoor
    WOOD_FRAME = "wood_frame"        # Residential wood construction
    BRICK = "brick"                  # Brick/masonry
    CONCRETE = "concrete"            # Commercial concrete
    REINFORCED_CONCRETE = "reinforced" # Modern commercial/parking
    METAL_CLAD = "metal_clad"        # Industrial/warehouse

# Building entry loss at 915 MHz (dB)
BUILDING_PENETRATION_DB = {
    BuildingType.NONE: 0.0,
    BuildingType.WOOD_FRAME: 8.0,
    BuildingType.BRICK: 12.0,
    BuildingType.CONCRETE: 18.0,
    BuildingType.REINFORCED_CONCRETE: 22.0,
    BuildingType.METAL_CLAD: 30.0,
}
```

**Impact**: Users can model outdoor-to-indoor links accurately.

### Improvement 4: Radio Horizon Limit (LOW — addresses GAP 9)

**Add to `rf.py`:**

```python
def radio_horizon_km(h1_m: float, h2_m: float, k: float = 4/3) -> float:
    """Calculate radio horizon between two antennas using k-factor Earth model.

    d = 4.12 * (sqrt(h1) + sqrt(h2))   [for standard k=4/3]

    Args:
        h1_m: First antenna height above ground (meters).
        h2_m: Second antenna height above ground (meters).
        k: Earth radius factor (4/3 for standard atmosphere).

    Returns:
        Maximum radio horizon in km.
    """
    factor = math.sqrt(2 * 6371.0 * k)  # ~130.3 for k=4/3
    return factor * (math.sqrt(max(h1_m, 0) / 1000) + math.sqrt(max(h2_m, 0) / 1000))
```

**Impact**: Caps unrealistic range predictions. 10 m + 10 m antennas = 26 km horizon.

### Improvement 5: Capture Effect Check (LOW — addresses GAP 8)

**Add to `rf.py`:**

```python
def capture_effect(rssi_wanted_dbm: float, rssi_interferer_dbm: float,
                   same_sf: bool = True) -> tuple:
    """Determine if the wanted signal will be captured over interference.

    Same-SF capture requires ~6 dB power advantage.
    Cross-SF signals are quasi-orthogonal with 16-20 dB rejection.

    Args:
        rssi_wanted_dbm: RSSI of desired signal.
        rssi_interferer_dbm: RSSI of interfering signal.
        same_sf: Whether both signals use the same spreading factor.

    Returns:
        (captured: bool, margin_db: float, description: str)
    """
    delta = rssi_wanted_dbm - rssi_interferer_dbm
    threshold = 6.0 if same_sf else -16.0  # Cross-SF has built-in rejection

    captured = delta >= threshold
    margin = delta - threshold

    if captured:
        desc = f"Captured: {delta:+.1f} dB SIR ({margin:+.1f} dB above threshold)"
    else:
        desc = f"Blocked: {delta:+.1f} dB SIR ({-margin:.1f} dB below threshold)"

    return (captured, margin, desc)
```

### Improvement 6: Processing Gain Display (LOW — informational)

**Add to `rf.py`:**

```python
def processing_gain_db(spreading_factor: int) -> float:
    """LoRa processing gain in dB.

    Processing gain = 2^SF (linear) = SF * 3.01 dB (logarithmic)
    This is why LoRa can decode signals below the noise floor.

    Args:
        spreading_factor: LoRa SF (7-12).

    Returns:
        Processing gain in dB.
    """
    return spreading_factor * 10.0 * math.log10(2)
```

---

## 7. Implementation Roadmap

### Phase 1: Core Model Upgrade (Highest Impact)
1. Add `Environment` enum and `ENVIRONMENT_PARAMS` to `rf.py`
2. Add `log_distance_path_loss()` function
3. Add `realistic_max_range()` function
4. Add `rx_sensitivity()` (bandwidth-aware) function
5. Update `multihop.py` to use environment-aware path loss
6. Update `preset_impact.py` max range to use log-distance model
7. Tests for all new functions

### Phase 2: Supplementary Features (Medium Impact)
8. Add `BuildingType` enum and penetration loss table
9. Add `radio_horizon_km()` function
10. Integrate building loss into `detailed_link_budget()`
11. Tests

### Phase 3: Analysis Features (Lower Impact, High Polish)
12. Add `capture_effect()` function
13. Add `processing_gain_db()` function
14. Add environment selection to TUI RF tools menu
15. Tests

### Estimated Scope
- Phase 1: ~200 lines new code + ~100 lines tests
- Phase 2: ~80 lines new code + ~40 lines tests
- Phase 3: ~60 lines new code + ~30 lines tests
- Total: ~510 lines, well within rf.py's budget (currently 668 lines → ~870 lines, under 1500 limit)

---

## 8. References

### Propagation Studies
1. Rademacher et al. (2021) — "Path loss and link budget for LoRa in urban environments" (Bonn)
2. Petäjäjärvi et al. (2015/2017) — "On the Coverage of LPWANs: Range Evaluation" (Oulu)
3. González-Palacio et al. (2023) — "LoRa propagation in mountainous terrain" (Medellín)
4. El Chall et al. (2021) — "LoRa channel measurements and modelling" (Beirut)
5. Ferreira et al. (2020) — "LoRa propagation in tropical forests" (Brazil)
6. MacCartney et al. — "LoRa propagation measurements" (Glasgow)

### ITU-R Recommendations
7. ITU-R P.1411 — Propagation data for short-range outdoor systems
8. ITU-R P.1238 — Propagation data for indoor planning
9. ITU-R P.2001 — General purpose terrestrial propagation model
10. ITU-R P.526 — Propagation by diffraction
11. ITU-R P.2109 — Building entry loss
12. ITU-R P.833-10 — Attenuation in vegetation

### Semtech & LoRa Alliance
13. AN1200.22 — LoRa Modulation Basics
14. AN1200.13 — LoRa Modem Designer's Guide
15. SX1262 Datasheet (Semtech)
16. SX1276/77/78/79 Datasheet (Semtech)

### Meshtastic
17. Meshtastic Range Records — Community Wiki
18. Meshtastic LoRa Configuration — https://meshtastic.org/docs/configuration/radio/lora/

---

*Research compiled for MeshForge NOC development. Made with aloha for the mesh community. — WH6GXZ*
