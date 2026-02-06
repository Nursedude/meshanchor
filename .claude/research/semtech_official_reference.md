# Semtech Official LoRa Reference Data -- Authoritative Sources

> **Research Date**: 2026-02-06
> **Context**: MeshForge NOC RF calculations (`src/utils/rf.py`)
> **Companion**: See also `lora_physical_layer.md` for academic/SDR research

---

## Table of Contents

1. [Document Index -- Semtech Application Notes & Datasheets](#1-document-index)
2. [AN1200.22 -- LoRa Modulation Basics](#2-an120022--lora-modulation-basics)
3. [AN1200.13 -- LoRa Modem Designer's Guide](#3-an120013--lora-modem-designers-guide)
4. [SX1262 Datasheet -- Key Specifications](#4-sx1262-datasheet--key-specifications)
5. [SX1276 Datasheet -- Key Specifications](#5-sx1276-datasheet--key-specifications)
6. [SX1262 vs SX1276 -- Comparative Analysis](#6-sx1262-vs-sx1276--comparative-analysis)
7. [Semtech LoRa Calculator -- Formulas & Methodology](#7-semtech-lora-calculator--formulas--methodology)
8. [Processing Gain -- The Mathematics](#8-processing-gain--the-mathematics)
9. [Channel Capacity -- ALOHA Model & Device Limits](#9-channel-capacity--aloha-model--device-limits)
10. [FEC Interleaving & Coding -- Hamming Code Details](#10-fec-interleaving--coding--hamming-code-details)
11. [Receiver Sensitivity -- Complete Tables](#11-receiver-sensitivity--complete-tables)
12. [Time on Air -- Complete Formula](#12-time-on-air--complete-formula)
13. [Link Budget -- Methodology & Examples](#13-link-budget--methodology--examples)

---

## 1. Document Index

### Semtech Application Notes

| Document | Title | Status | Mirror URLs |
|----------|-------|--------|-------------|
| **AN1200.13** | LoRa Modem Designer's Guide (SX1272/3/6/7/8) | Retired from semtech.com; content superseded | [OpenHacks](https://www.openhacks.com/uploadsproductos/loradesignguide_std.pdf), [Mouser](https://www.mouser.com/pdfdocs/semtech-lora-modem-design.pdf) |
| **AN1200.22** | LoRa Modulation Basics | Retired from semtech.com; content in AN1200.86 | [FrugalPrototype](https://www.frugalprototype.com/wp-content/uploads/2016/08/an1200.22.pdf), [FCC Filing](https://apps.fcc.gov/els/GetAtt.html?id=258342) |
| **AN1200.37** | SX1261/2 Recommendations for Best Performance | Current | [SparkFun CDN](https://cdn.sparkfun.com/assets/f/f/b/4/2/SX1262_AN-Recommendations_for_Best_Performance.pdf) |
| **AN1200.59** | Selecting the Optimal Reference Clock | Current | [Mouser](https://www.mouser.com/pdfDocs/AN1200_59_LoRa_Reference_Clock_Selection_V1_3_rev.pdf) |
| **AN1200.85** | Introduction to Channel Activity Detection | Current | [Semtech](https://www.semtech.com/uploads/technology/LoRa/cad-ensuring-lora-packets.pdf) |
| **AN1200.86** | LoRa and LoRaWAN v1.0 | Current (replaces AN1200.22) | [Semtech](https://www.semtech.com/uploads/technology/LoRa/lora-and-lorawan.pdf) |
| **AN1200.88** | Sending and Receiving Messages | Current | [Semtech](https://www.semtech.com/uploads/technology/LoRa/sending-and-receiving-messages.pdf) |
| **AN1200.89** | Theory and Principle of Advanced Ranging | Current | [Semtech](https://www.semtech.com/uploads/technology/LoRa/theory-and-principle-of-advanced-ranging.pdf) |
| **TN1300.05** | Predicting LoRaWAN Capacity v2.0 | Current | [Semtech](https://www.semtech.com/uploads/technology/LoRa/predicting-lorawan-capacity.pdf) |

### Datasheets

| Chip | Revision | Mirror URLs |
|------|----------|-------------|
| **SX1261/2** | Rev 1.2 (June 2019) | [SparkFun](https://cdn.sparkfun.com/assets/6/b/5/1/4/SX1262_datasheet.pdf), [Mouser](https://www.mouser.com/datasheet/2/761/DS_SX1261-2_V1.1-1307803.pdf) |
| **SX1261/2** | Rev 2.1 | [uelectronics](https://uelectronics.com/wp-content/uploads/2022/12/Datasheet-LoRa-SX1262.pdf) |
| **SX1276/77/78/79** | Rev 7 (May 2020) | [SparkFun](https://cdn.sparkfun.com/assets/7/7/3/2/2/SX1276_Datasheet.pdf), [Mouser](https://www.mouser.com/datasheet/2/761/sx1276-1278113.pdf) |
| **SX1276/77/78/79** | Rev 4 (March 2015) | [Adafruit](https://cdn-shop.adafruit.com/product-files/3179/sx1276_77_78_79.pdf) |

### Online Tools

| Tool | URL |
|------|-----|
| **Semtech LoRa Calculator** | [calculator.semtech.com](https://calculator.semtech.com/) |
| **Semtech LoRa Calculator (product page)** | [semtech.com/design-support/lora-calculator](https://www.semtech.com/design-support/lora-calculator) |
| **SX1262 Product Page** | [semtech.com/products/wireless-rf/lora-connect/sx1262](https://www.semtech.com/products/wireless-rf/lora-connect/sx1262) |
| **SX1276 Product Page** | [semtech.com/products/wireless-rf/lora-transceivers/sx1276](https://www.semtech.com/products/wireless-rf/lora-transceivers/sx1276) |
| **LoRa Developer Portal** | [lora-developers.semtech.com](https://lora-developers.semtech.com/) |
| **Semtech FAQ (LoRa)** | [semtech.com/design-support/faq/faq-lora](https://www.semtech.com/design-support/faq/faq-lora) |

---

## 2. AN1200.22 -- LoRa Modulation Basics

**Full title**: AN1200.22 LoRa Modulation Basics, Revision 2, May 2015
**Status**: Original Semtech URL now redirects; replaced by AN1200.86. Mirrors above.

### 2.1 Chirp Spread Spectrum (CSS) Explanation

CSS was developed for radar in the 1940s and adopted for military/secure communications. In 2007, IEEE adopted a CSS PHY for the LR-WPAN standard 802.15.4a. LoRa is a **proprietary CSS derivative** that combines chirp modulation with spread-spectrum principles.

Key concept: The data signal is "chipped" at a higher rate and modulated onto a chirp signal. The chirp sweeps linearly across the full channel bandwidth, and data is encoded as **cyclic frequency shifts** of the base chirp.

**Properties from AN1200.22:**
- **Bandwidth scalable**: Can operate in narrowband frequency hopping or wideband direct sequence modes
- **Constant envelope**: Like FSK, enables low-cost high-efficiency PA stages (no linear PA required)
- **High robustness**: Resistant to multipath, fading, Doppler, and in-band jamming
- **Multipath/fading resistant**: Chirp energy spread across bandwidth means no frequency-selective nulls
- **Doppler resistant**: CSS is inherently robust to frequency shifts from motion
- **Long range**: Processing gain enables reception below the noise floor

### 2.2 Spread Spectrum Principles (Shannon-Hartley Context)

AN1200.22 frames LoRa in the context of the Shannon-Hartley theorem:

```
C = B * log2(1 + S/N)
```

Where C = channel capacity (bps), B = bandwidth (Hz), S/N = signal-to-noise ratio.

LoRa trades data rate for sensitivity: by spreading the signal across a wider bandwidth and using longer symbol durations, it can operate at **negative SNR** values (signal below the noise floor).

### 2.3 Processing Gain Definition

From AN1200.22: Processing gain is introduced by multiplying the data signal with a spreading code or chip sequence. By increasing the chip rate, the frequency components of the total signal spectrum are increased. The energy is now spread over a wider range of frequencies.

The **processing gain (Gp)** is:

```
Gp (dB) = 10 * log10(Rc / Rb)
```

Where Rc = chip rate and Rb = bit rate. Since in LoRa the chip rate equals the bandwidth (Rc = BW), and each symbol encodes SF bits over 2^SF chips:

```
Gp (dB) = 10 * log10(2^SF / SF)
```

Higher SF = more processing gain = receiver can accept signals with worse SNR.

### 2.4 Noise Floor and Sensitivity

From AN1200.22, the noise floor calculation:

```
Noise Floor (dBm) = -174 + 10 * log10(BW)
```

Where -174 = 10 * log10(k * T * 1000) at T = 293K (room temperature), and BW is in Hz.

The receiver sensitivity formula:

```
Sensitivity (dBm) = -174 + 10 * log10(BW) + NF + SNR_required
```

Where NF = receiver noise figure (typically 6 dB for sub-GHz LoRa transceivers).

### 2.5 Key Properties Not in DSSS

AN1200.22 contrasts LoRa CSS with traditional DSSS:
- DSSS requires a highly accurate (expensive) reference clock
- LoRa CSS does **not** require a high-accuracy clock (works with low-cost 20 ppm XTAL)
- This is because in CSS, timing offset and frequency offset are mathematically equivalent -- the receiver treats both as a simple frequency shift

---

## 3. AN1200.13 -- LoRa Modem Designer's Guide

**Full title**: SX1272/3/6/7/8: LoRa Modem Designer's Guide, AN1200.13, Revision 1, July 2013
**Status**: The earliest Semtech LoRa app note. Original URL now redirects. Mirrors above.

### 3.1 Core Design Equations

AN1200.13 provides the foundational formulas used by all LoRa airtime calculators.

#### Symbol Duration

```
T_sym = 2^SF / BW  (seconds)
```

Example: SF12, BW=125 kHz:
```
T_sym = 4096 / 125000 = 32.768 ms per symbol
```

#### Chip Rate

```
R_chip = BW  (chips per second)
```

LoRa modulation sends data at a chip rate equal to the programmed bandwidth. BW=125 kHz = 125,000 chips/sec.

#### Symbol Rate

```
R_sym = BW / 2^SF  (symbols per second)
```

#### Bit Rate

```
R_bit = SF * (BW / 2^SF) * (4 / (4 + CR_overhead))
```

Where CR_overhead = number of FEC parity bits (1 for CR 4/5, 2 for 4/6, 3 for 4/7, 4 for 4/8).

### 3.2 Influence of Spreading Factor

From AN1200.13: "The substitution of one bit for multiple chips of information means that the spreading factor has a direct influence on the duration of the LoRa packet."

Each SF increment:
- **Doubles** the time on air (2x)
- Improves sensitivity by **~2.5 dB**
- Halves the bit rate
- Adds ~3 dB of processing gain

### 3.3 Sensitivity vs SF/BW

From AN1200.13: "LoRa modulation itself, forward error correction (FEC) techniques, and the spread spectrum processing gain combine to allow significant SNR improvements. Negative SNR numbers indicate the ability to receive signal powers below the receiver noise floor."

The minimum SNR required for demodulation depends on SF, and the sensitivity depends on both SF and BW.

### 3.4 Forward Error Correction

AN1200.13: "The LoRa modem employs Forward Error Correction (FEC) that permits the recovery of bits corrupted by interference, requiring a small overhead of additional encoding, with robustness depending on the coding rate selected."

The increase in coding rate (more redundancy) influences time on air for a fixed bandwidth.

---

## 4. SX1262 Datasheet -- Key Specifications

**Chip**: Semtech SX1261/SX1262 (Gen 2 LoRa transceiver)
**Datasheet**: DS.SX1261-2.W.APP, Rev 1.2 (June 2019) / Rev 2.1
**Product page**: [semtech.com/products/wireless-rf/lora-connect/sx1262](https://www.semtech.com/products/wireless-rf/lora-connect/sx1262)

### 4.1 General Specifications

| Parameter | Value |
|-----------|-------|
| Frequency Range | 150 -- 960 MHz |
| Modulation | LoRa + (G)FSK |
| LoRa Spreading Factors | SF5 -- SF12 |
| LoRa Bandwidth | 7.81 -- 500 kHz |
| LoRa Air Data Rate | 0.018 -- 62.5 kbps |
| (G)FSK Data Rate | Up to 300 kbps |
| Package | 4 x 4 mm, 24-pin QFN |
| Supply Voltage | 1.8 -- 3.7 V |

### 4.2 Transmitter Specifications

| Parameter | Value |
|-----------|-------|
| Max TX Power (SX1262) | **+22 dBm** |
| Max TX Power (SX1261) | +15 dBm |
| TX Current @ +22 dBm (DC-DC) | **118 mA** |
| TX Power Range | -9 to +22 dBm (32 dB dynamic range, 1 dB steps) |
| PA Supply | VBAT direct (PA not through DC-DC) |
| Modulation | Constant envelope (efficient PA) |

### 4.3 Receiver Specifications

| Parameter | Value |
|-----------|-------|
| Max Sensitivity (SF12, BW 7.8 kHz) | **-148 dBm** |
| Sensitivity (SF12, BW 125 kHz) | **-137 dBm** (typical) |
| Sensitivity (SF7, BW 125 kHz) | **-124 dBm** (typical) |
| RX Current (DC-DC mode) | **4.2 -- 4.6 mA** |
| RX Boosted Gain Mode | Available (slightly higher current for improved sensitivity) |
| Noise Figure | ~6 dB (estimated from sensitivity formula; not explicitly specified on product page) |
| Max Link Budget | **170 dB** |

### 4.4 Power Consumption

| Mode | Current |
|------|---------|
| TX @ +22 dBm (DC-DC) | 118 mA |
| TX @ +14 dBm (DC-DC) | ~45 mA |
| RX (DC-DC mode) | 4.2 -- 4.6 mA |
| RX (LDO mode) | ~8 mA |
| Sleep (cold start, RC64k running) | **1.2 uA** |
| Sleep (warm start, config retained) | **600 nA** |

### 4.5 Power Supply Architecture

The SX1262 provides **two voltage regulation modes**:

1. **DC-DC buck converter**: Higher efficiency, lower current draw in RX/TX. Recommended for battery operation.
2. **LDO (Low Dropout Regulator)**: Simpler, lower noise, but higher current draw. Used when DC-DC switching noise is unacceptable.

The PA (power amplifier) is always supplied directly from VBAT, not through the DC-DC converter.

### 4.6 TCXO Support

The SX1262 has a built-in TCXO power supply on **DIO3**:
- Programmable output voltage: 1.6 -- 3.3 V
- Current capability: 1.5 mA nominal, 4 mA max
- VBAT must be at least 200 mV above the programmed TCXO voltage
- Requires clipped-sinewave output TCXO, amplitude not exceeding 1.2 Vpp
- Eliminates the need for an external TCXO regulator (reduces BOM)

### 4.7 Crystal Oscillator

When using XTAL (not TCXO):
- Internal matching capacitors (software-adjustable) -- no external capacitors needed
- PCB thermal cutout recommended around crystal area for high-power TX designs
- If thermal cutout is not possible, use TCXO instead
- TCXO recommended below -20 C or above +70 C for frequency stability

### 4.8 LoRa Sensitivity Table (SX1262, Approximate)

From datasheet Section 4 (Electrical Specifications). Values are typical at 868/915 MHz:

| SF | 125 kHz | 250 kHz | 500 kHz |
|----|---------|---------|---------|
| SF5 | -112 dBm | -109 dBm | -106 dBm |
| SF6 | -115 dBm | -112 dBm | -109 dBm |
| SF7 | **-124 dBm** | -121 dBm | -118 dBm |
| SF8 | -127 dBm | -124 dBm | -121 dBm |
| SF9 | -130 dBm | -127 dBm | -124 dBm |
| SF10 | -133 dBm | -130 dBm | -127 dBm |
| SF11 | -135.5 dBm | -132.5 dBm | -129.5 dBm |
| SF12 | **-137 dBm** | -134 dBm | -131 dBm |

**Note**: The -148 dBm headline sensitivity is at SF12, BW 7.8 kHz. These values match the theoretical formula: Sensitivity = -174 + 10*log10(BW) + 6 + SNR_limit. Minor variations of +/-1 dB exist between datasheet revisions and individual devices.

---

## 5. SX1276 Datasheet -- Key Specifications

**Chip**: Semtech SX1276/77/78/79 (Gen 1 LoRa transceiver)
**Datasheet**: SX1276/77/78/79, Rev 7 (May 2020)
**Product page**: [semtech.com/products/wireless-rf/lora-connect/sx1276](https://www.semtech.com/products/wireless-rf/lora-connect/sx1276)

### 5.1 General Specifications

| Parameter | Value |
|-----------|-------|
| Frequency Range | 137 -- 1020 MHz |
| Modulation | LoRa + FSK/OOK |
| LoRa Spreading Factors | SF6 -- SF12 |
| LoRa Bandwidth | 7.8 -- 500 kHz (9 options) |
| LoRa Air Data Rate | 0.018 -- 37.5 kbps |
| FSK Data Rate | Up to 300 kbps |
| Package | 6 x 6 mm, 28-pin QFN |
| Supply Voltage | 1.8 -- 3.7 V |
| Reference Oscillator | FXOSC = 32 MHz |

### 5.2 Transmitter Specifications

| Parameter | Value |
|-----------|-------|
| Max TX Power (PA_BOOST pin) | **+20 dBm** |
| Max TX Power (RFO pin) | +14 dBm |
| TX Current @ +20 dBm (PA_BOOST) | **120 mA** |
| TX Current @ +13 dBm | ~28 mA |
| PA_BOOST bias voltage | ~1.65 V DC (maintained across 1.8 -- 3.7 V supply) |

### 5.3 Receiver Specifications

| Parameter | Value |
|-----------|-------|
| Max Sensitivity (SF12, BW 7.8 kHz) | **-148 dBm** |
| Sensitivity (SF12, BW 125 kHz) | **-137 dBm** |
| Sensitivity (SF7, BW 125 kHz) | **-123 dBm** |
| Sensitivity (FSK, 4.8 kbps) | -126 dBm |
| RX Current | **10 -- 12 mA** |
| Noise Figure (FSK mode) | ~6 dB |
| Adjacent Channel Selectivity (FSK, 25 kHz spacing) | ~33 dB |
| Blocking (FSK, 1 MHz offset) | ~73 dB |
| Blocking (FSK, 10 MHz offset) | ~79 dB |
| Blocking (LoRa, 1 MHz offset) | ~89 dB |
| Co-channel Rejection (LoRa) | ~20 dB |
| IIP3 | -11 dBm (typical) |
| Max RSSI before saturation | ~-1 dBm |
| Architecture | Half-duplex, low-IF |

### 5.4 Power Consumption

| Mode | Current |
|------|---------|
| TX @ +20 dBm (PA_BOOST) | 120 mA |
| TX @ +13 dBm | ~28 mA |
| TX @ +7 dBm | ~18 mA |
| RX (LoRa) | ~10 -- 12 mA |
| Idle | ~1.5 mA |
| Sleep (register retention) | **100 nA** |

### 5.5 LoRa Sensitivity Table (SX1276, from Datasheet Rev 7)

Values are typical at 868/915 MHz:

| SF | 125 kHz | 250 kHz | 500 kHz |
|----|---------|---------|---------|
| SF6 | -118 dBm | -115 dBm | -112 dBm |
| SF7 | **-123 dBm** | -120 dBm | -117 dBm |
| SF8 | -126 dBm | -123 dBm | -120 dBm |
| SF9 | -129 dBm | -126 dBm | -123 dBm |
| SF10 | -132 dBm | -129 dBm | -126 dBm |
| SF11 | -134.5 dBm | -131.5 dBm | -128.5 dBm |
| SF12 | **-137 dBm** | -134 dBm | -131 dBm |

**Observation**: SX1276 and SX1262 sensitivity values are essentially identical at the same SF/BW. The LoRa demodulation performance is determined by the modulation scheme, not the chip generation. The chip improvements (Gen 2 vs Gen 1) are in power consumption, TX power, and integration, not demodulation sensitivity.

### 5.6 Receiver Architecture

From the SX1276 datasheet: The SX1276 is a half-duplex, low-IF transceiver. The received RF signal is first amplified by the LNA (single-ended input, converted to differential internally for improved second-order linearity and harmonic rejection). The signal is down-converted to I&Q components at an intermediate frequency by the mixer stage. Sigma-delta ADCs perform data conversion, with all subsequent signal processing and demodulation in the digital domain.

---

## 6. SX1262 vs SX1276 -- Comparative Analysis

### 6.1 Head-to-Head Specifications

| Parameter | SX1276 (Gen 1, 2013) | SX1262 (Gen 2, 2018) | Winner |
|-----------|----------------------|----------------------|--------|
| **Max Sensitivity** | -148 dBm | -148 dBm | Tie |
| **Sensitivity @ SF12/125 kHz** | -137 dBm | -137 dBm | Tie |
| **Sensitivity @ SF7/125 kHz** | -123 dBm | -124 dBm | SX1262 (+1 dB) |
| **Max TX Power** | +20 dBm | **+22 dBm** | SX1262 (+2 dB) |
| **Max Link Budget** | 168 dB | **170 dB** | SX1262 (+2 dB) |
| **RX Current** | 10--12 mA | **4.2--4.6 mA** | SX1262 (~60% less) |
| **TX Current @ Max Power** | 120 mA @ +20 dBm | 118 mA @ +22 dBm | SX1262 (more efficient) |
| **Sleep Current** | **100 nA** | 600 nA | SX1276 (6x less) |
| **Noise Figure** | ~6 dB | ~6 dB | Tie |
| **Blocking (LoRa, 1 MHz)** | ~89 dB | 88 dB | Tie (within margin) |
| **Co-channel Rejection** | ~20 dB | 19 dB | Tie (within margin) |
| **Frequency Range** | 137--1020 MHz | 150--960 MHz | SX1276 (wider) |
| **SF Range** | SF6--SF12 | **SF5--SF12** | SX1262 (SF5 added) |
| **Max LoRa Bit Rate** | 37.5 kbps | **62.5 kbps** | SX1262 |
| **Package** | 6x6 mm, 28-pin QFN | **4x4 mm, 24-pin QFN** | SX1262 (smaller) |
| **DC-DC Converter** | Not integrated | **Integrated** | SX1262 |
| **TCXO Supply (DIO3)** | Not available | **Built-in** | SX1262 |
| **Crystal Matching Caps** | External required | **Internal, SW-adjustable** | SX1262 |
| **RX Boosted Gain** | Not available | **Available** | SX1262 |
| **Power Regulation Options** | LDO only | **DC-DC + LDO** | SX1262 |

### 6.2 DC-DC Converter Analysis

The SX1262's integrated DC-DC converter is the primary reason for its dramatically lower RX current:

- **SX1276**: Uses only an LDO regulator. Power dissipated as heat = (VBAT - V_core) * I_core.
- **SX1262 DC-DC mode**: Buck converter efficiently steps down VBAT to V_core. At 3.3V supply, efficiency is typically 85-90%, reducing wasted power by 3-5x compared to LDO.
- **SX1262 LDO mode**: Available as fallback when DC-DC switching noise is a concern (~8 mA RX, similar to SX1276).

**Design implication**: The DC-DC converter introduces switching noise. For extremely sensitive narrowband RX applications, LDO mode may be preferred despite higher current. For most LoRa applications at 125+ kHz bandwidth, DC-DC noise is insignificant.

### 6.3 TCXO vs Crystal Oscillator

**SX1276 with crystal**: Requires external matching capacitors (2x ceramic caps, ~2 pF typical). Frequency stability depends on crystal specification and temperature coefficient. A 20 ppm crystal is acceptable for BW >= 62.5 kHz. For BW < 41.7 kHz, a TCXO is recommended.

**SX1262 with crystal**: Internal matching capacitors, software-adjustable. No external caps needed (reduces BOM by 2 components). Semtech recommends a PCB thermal cutout around the crystal area when using high TX power (+22 dBm) to prevent frequency drift from PA heating. If thermal cutout is impossible, use TCXO.

**SX1262 with TCXO**: DIO3 provides a regulated DC voltage (1.6--3.3 V, software-configurable) to power an external TCXO directly. No external voltage regulator needed. This is a significant BOM and design simplification.

**Temperature recommendation**: At extreme temperatures (below -20 C or above +70 C), Semtech recommends TCXO for both chips.

### 6.4 Practical Sensitivity Comparison

From the Semtech FAQ: "With Bandwidth (BW) 125 kHz and Spreading Factor at SF12, the result is a data rate at 30 symbols/sec and Rx sensitivity at -137 dBm. Compared with BW 10.4 kHz and SF8, which results in very similar results: data rate at 40 symbols/sec and Rx sensitivity of -138 dBm. The trade-off is that with BW 10.4 kHz, you will have to use a TCXO which adds cost to your system. At BW 125 kHz, you do not need a TCXO anymore."

This tradeoff applies to both chips identically -- the sensitivity floor is determined by the LoRa modulation, not the chip.

### 6.5 When to Use Which

**Use SX1262 when**:
- Battery-powered (60% less RX current)
- Need +22 dBm TX (2 dB more link budget)
- Want simplified BOM (integrated DC-DC, TCXO supply, no external crystal caps)
- New designs (Gen 2 is the recommended platform)
- Meshtastic boards: T-Beam v1.1+, Heltec V3, RAK4631, XIAO-SX1262

**Use SX1276 when**:
- Absolute minimum sleep current is critical (100 nA vs 600 nA)
- Need frequency range outside 150--960 MHz (SX1276 goes to 137/1020 MHz)
- Existing design / legacy compatibility
- Meshtastic boards: T-Beam v0.7--v1.0, Heltec V2, RAK4200, TTGO LoRa32

---

## 7. Semtech LoRa Calculator -- Formulas & Methodology

**Tool URL**: [calculator.semtech.com](https://calculator.semtech.com/)
**Product page**: [semtech.com/design-support/lora-calculator](https://www.semtech.com/design-support/lora-calculator)

### 7.1 What It Calculates

The Semtech LoRa Calculator outputs:

| Output | Description |
|--------|-------------|
| Time on Air | Total packet transmission duration |
| Total Symbols | Number of symbols in the packet |
| Symbol Time | Duration of one symbol |
| Preamble Duration | Time for preamble transmission |
| Effective Data Rate | Actual throughput after overhead |
| Link Budget | Maximum allowable path loss |
| Receiver Sensitivity | Minimum detectable signal level |
| Range | Estimated communication distance |
| Device TX Consumption | Energy used per transmission |
| Device RX Consumption | Energy used per reception window |
| Average TX/RX/Sleep Consumption | Duty-cycle averaged power |

### 7.2 Input Parameters

| Parameter | Options |
|-----------|---------|
| LoRa Product | SX1261, SX1262, SX1268, SX1276, SX1278, etc. |
| Spreading Factor | SF5 -- SF12 (chip-dependent) |
| Bandwidth | 7.8 kHz -- 500 kHz |
| Coding Rate | 4/5, 4/6, 4/7, 4/8 |
| Preamble Length | 6 -- 65535 symbols |
| Payload Length | 1 -- 255 bytes |
| Header Mode | Explicit / Implicit |
| CRC | On / Off |
| Low Data Rate Optimization | Auto / On / Off |
| TX Power | Configurable per chip |
| Frequency | ISM band selection |

### 7.3 Internal Formulas

The calculator uses the same formulas from AN1200.13 / SX1276 datasheet Section 4.1.1.6:

#### Symbol Time
```
T_sym = 2^SF / BW  (seconds)
```

#### Preamble Duration
```
T_preamble = (n_preamble + 4.25) * T_sym
```

Where n_preamble = number of preamble symbols (default 8 for LoRaWAN).

#### Payload Symbols
```
n_payload = 8 + max(ceil((8*PL - 4*SF + 28 + 16*CRC - 20*IH) / (4*(SF - 2*DE))) * (CR + 4), 0)
```

Where:
- PL = payload length in bytes
- SF = spreading factor
- CRC = 1 if CRC enabled, 0 otherwise
- IH = 1 if implicit header, 0 if explicit
- DE = 1 if low data rate optimization enabled, 0 otherwise
- CR = coding rate (1 for 4/5, 2 for 4/6, 3 for 4/7, 4 for 4/8)

#### Total Time on Air
```
T_packet = T_preamble + n_payload * T_sym
```

#### Receiver Sensitivity
```
Sensitivity (dBm) = -174 + 10*log10(BW_Hz) + NF + SNR_limit[SF]
```

#### Link Budget
```
Link_Budget (dB) = TX_Power - Sensitivity
```

#### Range Estimation

The calculator uses a propagation model (likely Log-Distance or similar) with:
```
FSPL (dB) = 20*log10(d_km) + 20*log10(f_MHz) + 32.44
```

For free-space, solving for distance:
```
d_km = 10^((Link_Budget - 20*log10(f_MHz) - 32.44) / 20)
```

### 7.4 Low Data Rate Optimization (LDRO)

LDRO **must be enabled** when the symbol duration exceeds 16 ms. This occurs at:
- SF12, BW 125 kHz: T_sym = 32.768 ms (LDRO required)
- SF11, BW 125 kHz: T_sym = 16.384 ms (LDRO required)
- SF12, BW 250 kHz: T_sym = 16.384 ms (LDRO required)

When LDRO is active, the effective data rate is further reduced, and the denominator in the payload symbol formula changes from `4*SF` to `4*(SF-2)`.

### 7.5 Header Coding Rate

From the SX1262 datasheet (page 40): The explicit header is always encoded using coding rate **4/8** (maximum error protection), regardless of the payload coding rate. This ensures critical header information (payload length, coding rate, CRC presence) is received reliably. The payload uses the user-selected coding rate.

---

## 8. Processing Gain -- The Mathematics

### 8.1 Fundamental Formula

From AN1200.22 and AN1200.86: The processing gain is the Log10 ratio of the code sequence's chip rate to the data signal's bit rate.

There are **two ways to express** LoRa processing gain, and they give different numbers. Both are correct but measure different things:

#### Method 1: Chip-to-Bit Ratio (AN1200.22)

```
Gp = 10 * log10(R_chip / R_bit)
```

Since R_chip = BW and R_bit = SF * BW / 2^SF (ignoring coding rate):

```
Gp = 10 * log10(2^SF / SF)
```

| SF | 2^SF / SF | Gp (dB) |
|----|-----------|---------|
| 7  | 128/7 = 18.3 | **12.6 dB** |
| 8  | 256/8 = 32 | **15.1 dB** |
| 9  | 512/9 = 56.9 | **17.5 dB** |
| 10 | 1024/10 = 102.4 | **20.1 dB** |
| 11 | 2048/11 = 186.2 | **22.7 dB** |
| 12 | 4096/12 = 341.3 | **25.3 dB** |

**Increment per SF step**: ~2.5 dB (matches the SNR limit improvement)

#### Method 2: Raw Spreading (Total Chips per Symbol)

```
Gp_raw = 10 * log10(2^SF) = SF * 3.01 dB
```

| SF | Gp_raw (dB) |
|----|-------------|
| 7  | **21.1 dB** |
| 8  | **24.1 dB** |
| 9  | **27.1 dB** |
| 10 | **30.1 dB** |
| 11 | **33.1 dB** |
| 12 | **36.1 dB** |

This represents the total spread of energy across chips.

### 8.2 Reconciling the Two Methods

The difference between the two methods is `10*log10(SF)`, which accounts for the fact that each symbol carries SF bits of information. Method 1 gives the **per-bit** processing gain relevant to SNR, while Method 2 gives the **per-symbol** spreading.

### 8.3 SNR Advantage over Conventional Modulation

The processing gain translates directly to SNR advantage:

- **Conventional FSK**: Requires SNR ~ +8 to +10 dB for reliable demodulation
- **LoRa SF7**: Requires SNR = -7.5 dB
- **Difference**: ~15.5 to 17.5 dB advantage over FSK

From Semtech's product literature: "LoRa has a superior Gp compared to frequency-shift keying (FSK) modulation, allowing for a reduced transmitter output power level while maintaining the same signal data rate and a similar link budget."

Quantitatively, at the SX1276's maximum LoRa data rate, "the sensitivity is 8 dB better than FSK, but using a low-cost bill of materials with a 20 ppm XTAL, LoRa can improve receiver sensitivity by more than 20 dB compared to FSK."

### 8.4 Processing Gain and Sensitivity Relationship

The receiver sensitivity formula can be rewritten to show processing gain explicitly:

```
Sensitivity = Thermal_Noise_Floor + NF - Processing_Gain + Implementation_Loss
```

Where:
- Thermal_Noise_Floor = -174 + 10*log10(BW) dBm
- NF = noise figure (~6 dB)
- Processing_Gain = 10*log10(2^SF / SF) dB
- Implementation_Loss = constant that accounts for non-ideal demodulation

For LoRa, the Implementation_Loss term is approximately 10 dB (the ~10 dB Eb/N0 required). So:

```
Sensitivity ≈ -174 + 10*log10(BW) + 6 + 10 - 10*log10(2^SF/SF)
```

This gives the same results as the SNR_limit method, just expressed differently.

---

## 9. Channel Capacity -- ALOHA Model & Device Limits

### 9.1 Semtech TN1300.05 -- Predicting LoRaWAN Capacity

**Source**: [Semtech TN1300.05](https://www.semtech.com/uploads/technology/LoRa/predicting-lorawan-capacity.pdf)
**Context**: MachineQ + Semtech trial in Philadelphia, 2017. 10 indoor gateways, 108 indoor devices, ~1 million frames over 2 days.

### 9.2 Pure ALOHA Collision Model

LoRaWAN Class A devices use **pure ALOHA** for uplink. There is no carrier sensing, no time slots, and no coordination -- devices transmit whenever they have data.

**Classic ALOHA maximum throughput**: 1/(2e) = **18.4%** channel utilization.

From TN1300.05, the collision probability formula:

```
P_overlap = 1 - exp(-G * (1 + T_victim / T_interferer))
```

Where:
- G = offered load (channel utilization factor)
- T_victim = duration of the packet being received
- T_interferer = duration of the interfering packet

Special cases:
- When T_victim = T_interferer: reduces to classic ALOHA formula `P_overlap = 1 - exp(-2G)`
- When T_victim = 0: reduces to probability channel is occupied `P_busy = 1 - exp(-G)`
- When T_victim -> infinity: P_overlap -> 1

### 9.3 LoRa-Specific ALOHA Modifications

LoRa deviates from pure ALOHA in important ways:

1. **SF orthogonality**: Packets at different SFs are quasi-orthogonal. Two packets on the same channel but different SFs will NOT collide. This effectively multiplies capacity by the number of SFs in use.

2. **Capture effect**: When two same-SF packets collide, if one is stronger by >= 6 dB, it survives. This is a significant improvement over pure ALOHA where all collisions destroy both packets.

3. **Multiple receive paths**: Gateways can demodulate multiple SFs simultaneously on the same channel (SX1301/SX1302 concentrator chips have 8+ demodulation paths).

### 9.4 Capacity Numbers

From TN1300.05 and related Semtech literature:

| Scenario | Capacity |
|----------|----------|
| Single channel, single SF, pure ALOHA | ~18.4% channel utilization max |
| Single channel, 6 SFs (SF7-SF12), with ADR | **~150,000 packets/day** (at 10% PER target) |
| MachineQ/Semtech trial (10 gateways) | **>96% success rate** at tested load |
| Maximum single-gateway (Semtech estimate) | ~1,000,000 messages/day (with ADR, mostly SF7) |

### 9.5 Duty Cycle Impact

European regulation (ETSI EN300.220) limits transmission to:
- **1% duty cycle** in the 868.0--868.6 MHz sub-band
- **0.1% duty cycle** in the 868.7--869.2 MHz sub-band

At 1% duty cycle:
- SF7/125 kHz packet (~50 ms): can send ~720 packets/hour per device
- SF12/125 kHz packet (~1.2 s): can send ~30 packets/hour per device

US FCC Part 15.247 (915 MHz): No duty cycle limit, but frequency hopping or digital modulation spread spectrum rules apply. Maximum dwell time of 400 ms per channel for frequency hopping.

### 9.6 Scaling Limits

From NS-3 simulation research corroborating Semtech models:
- 1,000 nodes per gateway at 1% duty cycle: **~32% packet loss** (acceptable for many IoT applications)
- Collision ratio increases with node count, following ALOHA curve
- Simulations confirm LoRa maximum channel capacity of **~18%**, consistent with pure ALOHA

### 9.7 LR-FHSS -- Semtech's Scalability Solution

To address ALOHA limitations at massive scale, Semtech developed **LR-FHSS** (Long Range Frequency Hopping Spread Spectrum):
- Frequency hopping across the band reduces collision probability
- **Up to 1 million packets/day** on ETSI 125 kHz bandwidth
- **Up to 11 million packets/day** on FCC 1.5 MHz bandwidth (theoretical)
- Practical limit ~700k due to DSP processing constraints
- Supported on SX1262 and later chips via firmware

### 9.8 Meshtastic / P2P Implications

Meshtastic does NOT use LoRaWAN or ALOHA. It uses:
- **Listen-before-talk** (CAD -- Channel Activity Detection) before transmitting
- **Mesh retransmission** (store-and-forward)
- **Single SF/BW preset** for all nodes (no ADR)

This means:
- No SF orthogonality benefit (all nodes use same SF)
- Capture effect still applies (stronger signal wins)
- Mesh hopping helps with range but increases channel load (each message is retransmitted up to hop_limit times)
- Dense networks (>50 nodes on one channel) can experience significant congestion, especially on LongFast (SF11/250 kHz)

Practical Meshtastic channel limits (community observations):
- **ShortFast** (SF7/250): ~100+ nodes feasible
- **LongFast** (SF11/250): ~30-50 nodes before noticeable congestion
- **LongSlow** (SF12/125): ~10-20 nodes due to extreme airtime

---

## 10. FEC Interleaving & Coding -- Hamming Code Details

### 10.1 LoRa FEC Architecture

From the SX1276 datasheet and academic reverse engineering (Tapparel et al., EPFL):

The LoRa transmit chain is:
```
Data -> Whitening -> Hamming Encoding -> Diagonal Interleaving -> Gray Mapping -> Chirp Modulation
```

The receive chain is the reverse:
```
Chirp Demodulation -> Gray Demapping -> Deinterleaving -> Hamming Decoding -> Dewhitening -> Data
```

### 10.2 Hamming Code Specifications per Coding Rate

LoRa's FEC is based on **Hamming codes**. The (n, k) notation means n total bits, k data bits:

| Coding Rate | Hamming Code | Data Bits | Parity Bits | Total Bits | Hamming Distance | Capability |
|-------------|-------------|-----------|-------------|------------|-----------------|------------|
| **4/5** | (5, 4) | 4 | 1 | 5 | d_min = 2 | **Detect 1-bit error** (no correction) |
| **4/6** | (6, 4) | 4 | 2 | 6 | d_min = 3 | **Detect 2-bit errors** (no correction) |
| **4/7** | (7, 4) | 4 | 3 | 7 | d_min = 3 | **Correct 1-bit error** (SEC) |
| **4/8** | (8, 4) | 4 | 4 | 8 | d_min = 4 | **Correct 1-bit + detect 2-bit** (SECDED) |

**Critical distinction**: CR 4/5 and 4/6 **cannot correct** errors -- they can only detect them. Only CR 4/7 and 4/8 provide actual error correction capability. CR 4/8 additionally detects (without miscorrecting) double-bit errors.

### 10.3 Hamming Distance and Error Correction Theory

The **Hamming distance** d_min determines the code's capabilities:
- Detect up to (d_min - 1) errors
- Correct up to floor((d_min - 1) / 2) errors

| d_min | Detection | Correction |
|-------|-----------|------------|
| 2 (CR 4/5) | 1 error | 0 errors |
| 3 (CR 4/6, 4/7) | 2 errors | 1 error |
| 4 (CR 4/8) | 3 errors | 1 error (SECDED: correct 1, detect 2) |

### 10.4 Diagonal Interleaving

LoRa uses **diagonal interleaving** to spread burst errors across multiple codewords:

**Why interleaving is needed**: When a LoRa symbol is corrupted by noise or fading, multiple bits in that symbol may be in error. These errors are highly correlated (all caused by one symbol). Hamming codes are designed for **random** errors, not burst errors. Without interleaving, a single corrupted symbol could produce multi-bit errors within one codeword, overwhelming the code's correction capability.

**How it works**: SF codewords are interleaved using a diagonal pattern. The bits of each codeword are distributed across different symbols, so that:
- The LSBs (least significant bits, most vulnerable to noise) of one codeword end up in different symbols
- A single corrupted symbol spreads its errors across multiple codewords
- Each codeword sees at most 1 error from any single symbol corruption

**Interleaving block size**: SF codewords form one interleaving block. The block is transmitted as SF chirp symbols, with each symbol carrying one bit from each of the SF codewords in the block.

### 10.5 BER Improvement per Coding Rate

From academic analysis (Coded LoRa Frame Error Rate Analysis, arXiv:1911.10245):

At SF7, BW=125 kHz, SNR = -10 dB:
```
CR 4/5:  BER ~ 1.5 x 10^-1  (15% bit error rate)
CR 4/8:  BER ~ 2.0 x 10^-2  (2% bit error rate)
```

The combined effect of Hamming coding + diagonal interleaving at CR 4/8 yields SNR gains of **7 to 11 dB** depending on the channel conditions and packet length.

### 10.6 Airtime Penalty per Coding Rate

| CR | Payload Overhead | Relative Airtime (approx) |
|----|-----------------|--------------------------|
| 4/5 | 25% | **Baseline** |
| 4/6 | 50% | ~13% more than 4/5 |
| 4/7 | 75% | ~27% more than 4/5 |
| 4/8 | 100% | **~35-40% more than 4/5** |

For a 32-byte payload at SF7:
- CR 4/5: ~434 ms airtime
- CR 4/8: ~608 ms airtime

### 10.7 When is CR 4/8 Worth the Airtime Penalty?

**Use CR 4/8 when:**

1. **Retransmission cost exceeds airtime penalty**: If a failed packet requires retransmission (2x airtime), and CR 4/8's 40% extra airtime prevents the retransmission, it is a net win.

2. **High interference environments**: Industrial/urban settings with significant narrowband interference. The interleaver + Hamming correction recovers corrupted symbols.

3. **Extreme range / low SNR**: At the edge of coverage where SNR is near the demodulation threshold, the 7-11 dB gain from CR 4/8 FEC can make the difference between reception and silence.

4. **Asymmetric links**: When one end of the link is in a noisy environment, asymmetric coding rates can be used (the standard LoRaWAN approach sends the header at CR 4/8 and payload at user-selected CR).

5. **Duty-cycle-limited regions with no retransmit budget**: In EU868 with strict 1% duty cycle, every transmission counts. Better to spend 40% more airtime on FEC than risk losing the packet entirely and having to wait for the next duty cycle window.

**Use CR 4/5 when:**

1. **Clean RF environment**: Good SNR margin, minimal interference.
2. **Throughput-sensitive applications**: Real-time telemetry, higher message rates.
3. **Dense networks**: More airtime = more collisions. In dense Meshtastic deployments, the reduced airtime of CR 4/5 is more beneficial than the FEC of CR 4/8.
4. **LoRaWAN default**: The standard specifies CR 4/5 as default for most data rates.

**The header exception**: Regardless of payload CR, the LoRa explicit header is **always** encoded at CR 4/8. This ensures the critical header information (payload length, coding rate, CRC flag) is received reliably even when the payload uses a weaker code.

---

## 11. Receiver Sensitivity -- Complete Tables

### 11.1 Theoretical Sensitivity (Formula-Derived)

Using: `Sensitivity = -174 + 10*log10(BW_Hz) + NF + SNR_limit`
Where NF = 6 dB (Semtech typical)

#### SNR Demodulation Limits (Semtech Official)

| SF | SNR Limit (dB) |
|----|---------------|
| 5  | -2.5 |
| 6  | -5.0 |
| 7  | **-7.5** |
| 8  | **-10.0** |
| 9  | **-12.5** |
| 10 | **-15.0** |
| 11 | **-17.5** |
| 12 | **-20.0** |

Pattern: Each SF increment improves SNR tolerance by **2.5 dB**.

#### Full Theoretical Sensitivity Table (NF = 6 dB)

| SF | 7.8 kHz | 10.4 kHz | 15.6 kHz | 20.8 kHz | 31.25 kHz | 41.7 kHz | 62.5 kHz | 125 kHz | 250 kHz | 500 kHz |
|----|---------|----------|----------|----------|-----------|----------|----------|---------|---------|---------|
| 7  | -136.6 | -135.3 | -133.6 | -132.3 | -130.5 | -129.3 | -127.5 | **-124.5** | -121.5 | -118.5 |
| 8  | -139.1 | -137.8 | -136.1 | -134.8 | -133.0 | -131.8 | -130.0 | **-127.0** | -124.0 | -121.0 |
| 9  | -141.6 | -140.3 | -138.6 | -137.3 | -135.5 | -134.3 | -132.5 | **-129.5** | -126.5 | -123.5 |
| 10 | -144.1 | -142.8 | -141.1 | -139.8 | -138.0 | -136.8 | -135.0 | **-132.0** | -129.0 | -126.0 |
| 11 | -146.6 | -145.3 | -143.6 | -142.3 | -140.5 | -139.3 | -137.5 | **-134.5** | -131.5 | -128.5 |
| 12 | **-149.1** | -147.8 | -146.1 | -144.8 | -143.0 | -141.8 | -140.0 | **-137.0** | -134.0 | -131.0 |

**Notes:**
- The -149.1 dBm theoretical value at SF12/7.8 kHz aligns with Semtech's "-148 dBm" headline figure (within measurement tolerance).
- The bolded 125 kHz column contains the most commonly referenced values.
- Each halving of BW improves sensitivity by ~3 dB.
- Each SF increment improves sensitivity by ~2.5 dB.

### 11.2 SX1276 Datasheet Sensitivity (Measured Typical Values)

From SX1276 datasheet Table 11 (LoRa Modem, 868/915 MHz):

| SF | 125 kHz | 250 kHz | 500 kHz |
|----|---------|---------|---------|
| 6  | -118 | -115 | -112 |
| 7  | **-123** | -120 | -117 |
| 8  | -126 | -123 | -120 |
| 9  | -129 | -126 | -123 |
| 10 | -132 | -129 | -126 |
| 11 | -134.5 | -131.5 | -128.5 |
| 12 | **-137** | -134 | -131 |

All values in dBm.

### 11.3 Comparison: Theoretical vs Datasheet

At 125 kHz:

| SF | Theoretical | Datasheet | Delta |
|----|------------|-----------|-------|
| 7  | -124.5 | -123 | +1.5 dB |
| 8  | -127.0 | -126 | +1.0 dB |
| 9  | -129.5 | -129 | +0.5 dB |
| 10 | -132.0 | -132 | 0 dB |
| 11 | -134.5 | -134.5 | 0 dB |
| 12 | -137.0 | -137 | 0 dB |

The theoretical formula is an excellent match for SF10-SF12. The 1-1.5 dB difference at SF7-SF8 represents real-world implementation loss at the higher data rates.

---

## 12. Time on Air -- Complete Formula

### 12.1 The Official Formula (from AN1200.13 / SX1276 Datasheet Section 4.1.1.6)

#### Step 1: Symbol Duration

```
T_sym = 2^SF / BW  (seconds)
```

#### Step 2: Preamble Duration

```
T_preamble = (n_preamble + 4.25) * T_sym
```

Where n_preamble is the programmed preamble length (default 8 for LoRaWAN, 16 for Meshtastic).

The +4.25 accounts for the sync word (2 symbols) and SFD (2.25 down-chirps).

#### Step 3: Payload Symbol Count

```
n_payload = 8 + max(ceil((8*PL - 4*SF + 28 + 16*CRC - 20*IH) / (4*(SF - 2*DE))) * (CR + 4), 0)
```

Where:
- **PL** = payload length in bytes
- **SF** = spreading factor (7-12)
- **CRC** = 1 if CRC enabled, 0 if disabled
- **IH** = 1 if implicit header mode, 0 if explicit header
- **DE** = 1 if low data rate optimization enabled, 0 if disabled
- **CR** = coding rate numerator minus 4 (1 for 4/5, 2 for 4/6, 3 for 4/7, 4 for 4/8)

The minimum payload is always 8 symbols (the `8 +` part).

#### Step 4: Payload Duration

```
T_payload = n_payload * T_sym
```

#### Step 5: Total Time on Air

```
T_packet = T_preamble + T_payload
```

### 12.2 Low Data Rate Optimization (LDRO) Rules

LDRO **must be enabled** when T_sym > 16 ms:

| SF | BW | T_sym | LDRO Required? |
|----|-----|-------|---------------|
| 12 | 125 kHz | 32.768 ms | **Yes** |
| 11 | 125 kHz | 16.384 ms | **Yes** |
| 12 | 250 kHz | 16.384 ms | **Yes** |
| 10 | 125 kHz | 8.192 ms | No |
| 11 | 250 kHz | 8.192 ms | No |
| 12 | 500 kHz | 8.192 ms | No |

When LDRO is active (DE=1), the denominator changes from `4*SF` to `4*(SF-2)`, increasing the number of payload symbols.

### 12.3 Worked Example: Meshtastic LongFast

**Parameters**: SF11, BW=250 kHz, CR=4/5, 32-byte payload, CRC on, explicit header, preamble=16

```
Step 1: T_sym = 2^11 / 250000 = 2048 / 250000 = 8.192 ms
        LDRO not required (T_sym < 16 ms), DE = 0

Step 2: T_preamble = (16 + 4.25) * 8.192 = 20.25 * 8.192 = 165.888 ms

Step 3: n_payload = 8 + max(ceil((8*32 - 4*11 + 28 + 16*1 - 20*0) / (4*(11 - 2*0))) * (1 + 4), 0)
       = 8 + max(ceil((256 - 44 + 28 + 16 - 0) / (44)) * 5, 0)
       = 8 + max(ceil(256 / 44) * 5, 0)
       = 8 + max(ceil(5.82) * 5, 0)
       = 8 + max(6 * 5, 0)
       = 8 + 30
       = 38 symbols

Step 4: T_payload = 38 * 8.192 = 311.296 ms

Step 5: T_packet = 165.888 + 311.296 = 477.184 ms ≈ 477 ms
```

### 12.4 Worked Example: Meshtastic VeryLongSlow

**Parameters**: SF12, BW=62.5 kHz, CR=4/8, 32-byte payload, CRC on, explicit header, preamble=16

```
Step 1: T_sym = 2^12 / 62500 = 4096 / 62500 = 65.536 ms
        LDRO required (T_sym > 16 ms), DE = 1

Step 2: T_preamble = (16 + 4.25) * 65.536 = 20.25 * 65.536 = 1327.104 ms

Step 3: n_payload = 8 + max(ceil((8*32 - 4*12 + 28 + 16*1 - 20*0) / (4*(12 - 2*1))) * (4 + 4), 0)
       = 8 + max(ceil((256 - 48 + 28 + 16) / (40)) * 8, 0)
       = 8 + max(ceil(252 / 40) * 8, 0)
       = 8 + max(ceil(6.3) * 8, 0)
       = 8 + max(7 * 8, 0)
       = 8 + 56
       = 64 symbols

Step 4: T_payload = 64 * 65.536 = 4194.304 ms

Step 5: T_packet = 1327.104 + 4194.304 = 5521.408 ms ≈ 5.5 seconds
```

### 12.5 Time on Air Quick Reference

For a 32-byte payload, CRC on, explicit header, preamble 16:

| Preset | SF | BW | CR | ToA (approx) |
|--------|-----|-----|-----|-------------|
| ShortTurbo | 7 | 500 | 4/5 | ~25 ms |
| ShortFast | 7 | 250 | 4/5 | ~50 ms |
| MediumFast | 9 | 250 | 4/5 | ~165 ms |
| LongFast | 11 | 250 | 4/5 | ~477 ms |
| LongModerate | 11 | 125 | 4/8 | ~1600 ms |
| LongSlow | 12 | 125 | 4/8 | ~2800 ms |
| VeryLongSlow | 12 | 62.5 | 4/8 | ~5500 ms |

---

## 13. Link Budget -- Methodology & Examples

### 13.1 Semtech Link Budget Formula

```
Link Budget (dB) = P_TX + G_TX - L_TX + G_RX - L_RX - Sensitivity
```

Or equivalently:

```
Maximum Path Loss (dB) = EIRP - Sensitivity
```

Where:
- **EIRP** = P_TX + G_TX - L_TX (Effective Isotropic Radiated Power)
- **Sensitivity** = receiver sensitivity (negative dBm value, so subtracting it adds to the budget)

### 13.2 Semtech's Headline Link Budgets

| Chip | Max TX | Sensitivity | Link Budget |
|------|--------|-------------|-------------|
| SX1262 | +22 dBm | -148 dBm | **170 dB** |
| SX1276 | +20 dBm | -148 dBm | **168 dB** |

These are theoretical maximums at SF12, BW 7.8 kHz -- not practical operating points.

### 13.3 Practical Link Budgets (Common Meshtastic Configurations)

| Config | TX Power | Sensitivity | Link Budget |
|--------|----------|-------------|-------------|
| SX1262, LongFast (SF11/250) | +22 dBm | -131.5 dBm | **153.5 dB** |
| SX1262, LongSlow (SF12/125) | +22 dBm | -137 dBm | **159 dB** |
| SX1276, LongFast (SF11/250) | +20 dBm | -131.5 dBm | **151.5 dB** |
| SX1262, MediumFast (SF9/250) | +22 dBm | -126.5 dBm | **148.5 dB** |
| SX1262, ShortFast (SF7/250) | +22 dBm | -121.5 dBm | **143.5 dB** |

### 13.4 Range Estimation Approach

Semtech uses free-space path loss (FSPL) for theoretical range and recommends real-world propagation models for practical estimates.

**Free Space Path Loss (Friis)**:
```
FSPL (dB) = 20*log10(d_km) + 20*log10(f_MHz) + 32.44
```

At 915 MHz:
```
FSPL = 20*log10(d_km) + 91.7
```

**Solving for range:**
```
d_km = 10^((Link_Budget - Fade_Margin - 91.7) / 20)
```

### 13.5 Range Estimates with Fade Margin

For SX1262 LongFast (153.5 dB link budget), 915 MHz:

| Fade Margin | Free-Space Range |
|-------------|-----------------|
| 0 dB (theoretical max) | ~460 km |
| 10 dB | ~145 km |
| 20 dB | ~46 km |
| 30 dB | ~14.5 km |
| 40 dB | ~4.6 km |

**Reality check**: Free-space range is purely theoretical. Real-world range depends heavily on terrain, antenna height, obstacles, and environment:
- **Urban**: 1-3 km typical
- **Suburban**: 3-8 km typical
- **Rural (elevated antennas)**: 10-30 km typical
- **Mountain-to-mountain (LOS)**: 50-200+ km demonstrated

### 13.6 Semtech's Range Estimation in the Calculator

The Semtech LoRa Calculator likely uses a propagation model similar to the **Okumura-Hata** or **Log-Distance** model rather than free-space, though the exact model is not published. The calculator outputs a range estimate that can be compared against free-space to understand the assumed propagation conditions.

---

## References

### Semtech Official Documents

1. AN1200.13 -- LoRa Modem Designer's Guide: [Mouser Mirror](https://www.mouser.com/pdfdocs/semtech-lora-modem-design.pdf)
2. AN1200.22 -- LoRa Modulation Basics: [FrugalPrototype Mirror](https://www.frugalprototype.com/wp-content/uploads/2016/08/an1200.22.pdf)
3. AN1200.37 -- SX1261/2 Best Performance: [SparkFun CDN](https://cdn.sparkfun.com/assets/f/f/b/4/2/SX1262_AN-Recommendations_for_Best_Performance.pdf)
4. AN1200.59 -- Reference Clock Selection: [Mouser](https://www.mouser.com/pdfDocs/AN1200_59_LoRa_Reference_Clock_Selection_V1_3_rev.pdf)
5. AN1200.85 -- Channel Activity Detection: [Semtech](https://www.semtech.com/uploads/technology/LoRa/cad-ensuring-lora-packets.pdf)
6. AN1200.86 -- LoRa and LoRaWAN: [Semtech](https://www.semtech.com/uploads/technology/LoRa/lora-and-lorawan.pdf)
7. TN1300.05 -- Predicting LoRaWAN Capacity: [Semtech](https://www.semtech.com/uploads/technology/LoRa/predicting-lorawan-capacity.pdf)
8. SX1261/2 Datasheet Rev 2.1: [uelectronics](https://uelectronics.com/wp-content/uploads/2022/12/Datasheet-LoRa-SX1262.pdf)
9. SX1276/77/78/79 Datasheet Rev 7: [SparkFun CDN](https://cdn.sparkfun.com/assets/7/7/3/2/2/SX1276_Datasheet.pdf)

### Semtech Online Resources

10. Semtech LoRa Calculator: [calculator.semtech.com](https://calculator.semtech.com/)
11. Semtech LoRa FAQ: [semtech.com/design-support/faq/faq-lora](https://www.semtech.com/design-support/faq/faq-lora)
12. LoRa Developer Portal: [lora-developers.semtech.com](https://lora-developers.semtech.com/)
13. Understanding ADR: [lora-developers.semtech.com](https://lora-developers.semtech.com/documentation/tech-papers-and-guides/understanding-adr/)
14. Semtech Blog -- LoRa Calculator: [blog.semtech.com](https://blog.semtech.com/lora-calculator-now-available-on-lora-developer-portal)
15. Semtech Blog -- Long Range with LoRa: [blog.semtech.com](https://blog.semtech.com/long-range-with-lora)

### Academic & Third-Party Validation

16. ST Microelectronics AN5664 -- RSSI and SNR for LoRa on STM32WL: [ST.com PDF](https://www.st.com/resource/en/application_note/an5664-rssi-and-snr-for-lora-modulation-on-stm32wl-series-stmicroelectronics.pdf)
17. Coded LoRa Frame Error Rate Analysis: [arXiv:1911.10245](https://arxiv.org/pdf/1911.10245)
18. From Demodulation to Decoding (EPFL, Tapparel et al.): [ACM TOSN](https://dl.acm.org/doi/fullHtml/10.1145/3546869)
19. Complete Reverse Engineering of LoRa PHY (EPFL): [EPFL Report](https://www.epfl.ch/labs/tcl/wp-content/uploads/2020/02/Reverse_Eng_Report.pdf)
20. NS-3 LoRa Simulation: [HAL](https://hal.science/hal-01835883/document)
21. IEEE LR-FHSS Capacity: [arXiv](https://arxiv.org/pdf/2010.00491)

### Community & Third-Party Comparison Sources

22. EBYTE SX1262 vs SX1276 Guide: [cdebyte.com](https://www.cdebyte.com/news/580)
23. Rokland SX1262 vs SX1276: [store.rokland.com](https://store.rokland.com/blogs/news/lorawan-difference-between-sx1262-and-sx1276)
24. NiceRF LoRa Chip Comparison: [nicerf.com](https://www.nicerf.com/news/semtech-lora-chip-comparison-upgrade-guide-sx1276-sx1262-lr1121-lr2021.html)
25. The Things Network -- FEC and Code Rate: [thethingsnetwork.org](https://www.thethingsnetwork.org/docs/lorawan/fec-and-code-rate/)
26. The Things Network -- Spreading Factors: [thethingsnetwork.org](https://www.thethingsnetwork.org/docs/lorawan/spreading-factors/)

---

*Research compiled from Semtech official documentation for MeshForge NOC development. Made with aloha for the mesh community. -- WH6GXZ*
