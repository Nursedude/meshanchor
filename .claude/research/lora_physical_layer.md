# LoRa Physical Layer Signal Processing — Comprehensive Research

> **Research Date**: 2026-02-06
> **Context**: MeshForge NOC RF link budget calculations (`src/utils/rf.py`)
> **Key Paper**: IEEE 9555814 — "LoRa Signal Synchronization and Detection at Extremely Low Signal-to-Noise Ratios"

---

## Table of Contents

1. [LoRa CSS Modulation Fundamentals](#1-lora-css-modulation-fundamentals)
2. [LoRa Demodulation Algorithms](#2-lora-demodulation-algorithms)
3. [SNR Performance Limits by Spreading Factor](#3-snr-performance-limits-by-spreading-factor)
4. [Synchronization Techniques](#4-synchronization-techniques)
5. [SDR Implementations of LoRa](#5-sdr-implementations-of-lora)
6. [LoRa Capture Effect](#6-lora-capture-effect)
7. [Near-Far Problem in LoRa](#7-near-far-problem-in-lora)
8. [Coding Rate Impact on BER](#8-coding-rate-impact-on-ber)
9. [SX1262 vs SX1276 Receiver Performance](#9-sx1262-vs-sx1276-receiver-performance)
10. [LoRa Link Budget Best Practices](#10-lora-link-budget-best-practices)
11. [Meshtastic Preset Reference](#11-meshtastic-preset-reference)
12. [Application to MeshForge RF Calculations](#12-application-to-meshforge-rf-calculations)

---

## 1. LoRa CSS Modulation Fundamentals

### 1.1 What is Chirp Spread Spectrum (CSS)?

Chirp Spread Spectrum is a spread spectrum technique that uses wideband linear frequency modulated chirp pulses to encode information. A **chirp** is a sinusoidal signal whose frequency increases (up-chirp) or decreases (down-chirp) linearly over time across the entire allocated bandwidth.

LoRa is a **proprietary CSS derivative** patented by Semtech that combines chirp modulation with frequency-shift keying principles. It is fundamentally an **M-ary orthogonal modulation scheme** where M = 2^SF.

**Key advantages of CSS:**
- Robust to multipath fading (constant bandwidth usage)
- Resistant to narrowband interference and jamming
- Low RF power consumption (constant envelope signal)
- Computational simplicity for signal processing
- Timing offset and frequency offset equivalence simplifies receiver design

### 1.2 Signal Generation — The Mathematics

#### Core Parameters

| Parameter | Symbol | Description | LoRa Values |
|-----------|--------|-------------|--------------|
| Spreading Factor | SF | Bits per symbol | 6, 7, 8, 9, 10, 11, 12 |
| Bandwidth | B | Channel bandwidth | 7.8, 10.4, 15.6, 20.8, 31.25, 41.7, 62.5, 125, 250, 500 kHz |
| Modulation Order | M | Number of symbols | M = 2^SF |
| Symbol Duration | Ts | Time per symbol | Ts = 2^SF / B |
| Chip Rate | Rc | Chips per second | Rc = B (chips/s/Hz) |
| Symbol Rate | Rs | Symbols per second | Rs = B / 2^SF |

#### Chirp Signal Equation

The base up-chirp signal (symbol m = 0):

```
s_0(t) = A * exp(j * 2pi * [f_0 + (B / 2*Ts) * t] * t)
```

For a **symbol m** (where m is in {0, 1, ..., M-1}), the instantaneous frequency is a piecewise-linear function with a cyclic wrap:

```
f_inst(t, m) = { (m/Ts) + (B/Ts)*t          for 0 <= t < (M-m)/B
               { (m/Ts) + (B/Ts)*t - B       for (M-m)/B <= t < Ts
```

The key insight: **each symbol is a cyclic frequency shift** of the base up-chirp. Symbol m shifts the starting frequency by `m * (B/M)` Hz, and the chirp wraps around when it hits the upper band edge.

#### Normalized Signal Representation

```
s_m(t) = (1/sqrt(Ts)) * exp(j * 2pi * [(gamma(m) + beta*t/2) mod B - B/2] * t)
```

Where:
- `gamma(m) = m * B / M` is the frequency offset for symbol m
- `beta = B / Ts = B^2 / M` is the chirp rate (Hz/s)
- The `mod B` operation creates the cyclic frequency wrap

#### Bit Rate Formula

```
R_b = SF * (BW / 2^SF) * (4 / (4 + CR_overhead))
```

Where CR_overhead is the number of parity bits (1 for CR 4/5, 4 for CR 4/8).

**Example**: SF=11, BW=250 kHz, CR=4/5:
```
R_b = 11 * (250000 / 2048) * (4/5) = 11 * 122.07 * 0.8 = 1074 bps
```

### 1.3 Up-Chirp and Down-Chirp

| Type | Frequency Behavior | Use in LoRa |
|------|-------------------|-------------|
| **Up-chirp** | Frequency increases linearly from f_min to f_max | Data symbols, preamble |
| **Down-chirp** | Frequency decreases linearly from f_max to f_min | SFD (sync), dechirping reference |

- **Preamble**: p unmodulated up-chirps (typically 8, configurable 6-65535)
- **Sync word**: 2 modulated up-chirps (network identifier, e.g., 0x12 for LoRaWAN, 0x34 for private)
- **SFD**: 2.25 down-chirps (timing/frequency synchronization)
- **Payload**: Modulated up-chirps carrying encoded data

### 1.4 Gray Coding

LoRa uses **Gray coding** to map symbols to binary. Adjacent symbols differ by only one bit, so if noise causes a detection error to an adjacent frequency bin, only a single bit error occurs. This improves the effectiveness of forward error correction.

### 1.5 Processing Gain

The processing gain of LoRa is the ratio of the chip rate to the data rate:

```
Processing Gain (linear) = 2^SF
Processing Gain (dB) = SF * 10 * log10(2) = SF * 3.01 dB
```

| SF | M = 2^SF | Processing Gain (dB) |
|----|----------|---------------------|
| 7  | 128      | 21.1 |
| 8  | 256      | 24.1 |
| 9  | 512      | 27.1 |
| 10 | 1024     | 30.1 |
| 11 | 2048     | 33.1 |
| 12 | 4096     | 36.1 |

Each increment in SF adds ~3 dB of processing gain, which is why higher SFs can operate further below the noise floor.

---

## 2. LoRa Demodulation Algorithms

### 2.1 Standard Dechirp-and-FFT Pipeline

The standard LoRa demodulation is a two-step process:

**Step 1 — Dechirping:**
Multiply the received signal by a conjugate reference down-chirp (the base down-chirp with m=0):

```
r_dechirped(t) = r(t) * s_0*(t)
```

After dechirping, the cyclic frequency-shifted chirp becomes a **single-tone sinusoid** at frequency `m * delta_f`, where:
```
delta_f = B / M = 1 / Ts
```

This converts CSS modulation into standard M-ary FSK.

**Step 2 — DFT Peak Detection:**
Apply the N-point DFT (N = 2^SF) to the dechirped signal:

```
X[k] = sum_{n=0}^{N-1} r_dechirped[n] * exp(-j * 2pi * k * n / N)
```

The detected symbol is:
```
m_hat = argmax_k |X[k]|^2    (non-coherent detection)
m_hat = argmax_k Re{X[k]}    (coherent detection)
```

**Complexity**: O(N log N) using FFT, where N = 2^SF.

### 2.2 Non-Coherent vs Coherent vs Semi-Coherent Detection

| Detection Type | How It Works | BER Performance | Practical Notes |
|---------------|--------------|-----------------|-----------------|
| **Non-coherent** | Uses magnitude |X[k]| only | Baseline | Robust to phase offset; standard in SX1262/SX1276 |
| **Coherent** | Uses complex X[k] with CSI | ~1 dB better than non-coherent | Requires channel state info; sensitive to phase errors |
| **Semi-coherent** | Iterative blind CSI estimation | Between coherent and non-coherent | >1 dB gain over non-coherent; no overhead for CSI |

**Key finding**: Coherent detection is degraded by phase offset, while non-coherent detection is unaffected. In practice, LoRa hardware uses non-coherent detection.

### 2.3 Improved Demodulation Techniques

#### Zero-Padding for Better Resolution
Applying zero-padding to the time-domain signal before FFT improves frequency resolution (equivalent to interpolation in the frequency domain). **4x zero-padding** provides the optimal balance between computation overhead and sensitivity improvement.

#### Phase-Aligned Dechirping
In practice, phase misalignment occurs when the chirp frequency wraps from maximum to minimum. This distorts FFT peaks. Oversampling-based strategies separate the two chirp segments in the frequency domain, producing two distinct peaks at separate locations for correct detection.

#### Multi-Symbol Accumulation
For extremely low SNR, energy from multiple preamble up-chirps can be accumulated to improve detection. While one chirp's energy may be overwhelmed by noise, aggregating multiple chirps provides sufficient SNR for detection.

### 2.4 Symbol Error Rate Formula

LoRa's SER follows the classical non-coherent M-ary orthogonal signaling formula:

```
P_s = sum_{k=1}^{M-1} (-1)^(k+1) * C(M-1,k) * (1/(k+1)) * exp(-k/(k+1) * SF * Eb/N0)
```

Where:
- M = 2^SF is the modulation order
- SF is the spreading factor
- Eb/N0 is the energy per bit to noise spectral density ratio
- C(M-1,k) is the binomial coefficient

The BER is then:
```
P_b = (M/2) / (M-1) * P_s
```

LoRa waveforms are approximately orthogonal for large M, and the approximation improves with higher SF.

---

## 3. SNR Performance Limits by Spreading Factor

### 3.1 Demodulation SNR Thresholds

These are the **minimum SNR values at which the LoRa demodulator can still recover symbols**, as specified in Semtech datasheets (SX1276/77/78/79):

| SF | SNR Limit (dB) | Processing Gain (dB) | Min Eb/N0 (dB) |
|----|---------------|---------------------|-----------------|
| 7  | **-7.5**      | 21.1                | ~10.3           |
| 8  | **-10.0**     | 24.1                | ~10.3           |
| 9  | **-12.5**     | 27.1                | ~10.3           |
| 10 | **-15.0**     | 30.1                | ~10.3           |
| 11 | **-17.5**     | 33.1                | ~10.3           |
| 12 | **-20.0**     | 36.1                | ~10.3           |

**Pattern**: Each SF increment changes the SNR limit by **-2.5 dB**. The Eb/N0 stays roughly constant at ~10 dB — this is the fundamental demodulation threshold.

Negative SNR values mean the signal power can be **below the noise floor** and still be decoded. At SF12, the signal can be 100x weaker than the noise and still be recovered.

### 3.2 Receiver Sensitivity Formula

```
Sensitivity (dBm) = -174 + 10*log10(BW) + NF + SNR_limit
```

Where:
- **-174 dBm/Hz** = thermal noise floor at room temperature (kTB at 290K)
- **BW** = bandwidth in Hz
- **NF** = receiver noise figure (typically 6 dB for SX1276/SX1262)
- **SNR_limit** = spreading factor-dependent demodulation threshold

#### Worked Examples (NF = 6 dB)

| SF | BW (kHz) | 10*log10(BW) | SNR Limit | Sensitivity |
|----|----------|-------------|-----------|-------------|
| 7  | 125      | 51.0        | -7.5      | -124.5 dBm  |
| 7  | 250      | 54.0        | -7.5      | -121.5 dBm  |
| 9  | 125      | 51.0        | -12.5     | -129.5 dBm  |
| 11 | 125      | 51.0        | -17.5     | -134.5 dBm  |
| 11 | 250      | 54.0        | -17.5     | -131.5 dBm  |
| 12 | 125      | 51.0        | -20.0     | -137.0 dBm  |
| 12 | 62.5     | 48.0        | -20.0     | -140.0 dBm  |
| 12 | 7.8      | 38.9        | -20.0     | -149.1 dBm  |

The -148 dBm maximum sensitivity figure quoted for SX1276/SX1262 corresponds to SF12, BW=7.8 kHz.

### 3.3 Practical Field Observations

- SF7-SF8: Reliable only within ~500 m in urban environments
- SF10-SF11: Good packet delivery beyond 1000 m
- SF12: Best range up to 2000+ m in urban, 15+ km rural
- SNR values of -20 to -22 dB have been observed with successful decode in Meshtastic LongFast mode
- Anything above +5 dB SNR is considered "plenty of signal" (Semtech FAQ)

### 3.4 Values Used in MeshForge rf.py

The current MeshForge `LORA_SENSITIVITY_DBM` dictionary (at BW=125 kHz, NF=6 dB):

```python
LORA_SENSITIVITY_DBM = {
    7: -123.0, 8: -126.0, 9: -129.0,
    10: -132.0, 11: -134.5, 12: -137.0,
}
```

These values are consistent with the theoretical formula and match typical Semtech datasheet values for BW=125 kHz operation. For Meshtastic presets using BW=250 kHz, sensitivity is ~3 dB worse (e.g., SF11/250 kHz = -131.5 dBm).

---

## 4. Synchronization Techniques

### 4.1 LoRa Packet Structure

```
┌─────────────┬───────────┬──────────┬──────────────────┐
│  Preamble   │ Sync Word │   SFD    │     Payload      │
│ p up-chirps │ 2 symbols │ 2.25 DC  │ modulated chirps │
│ (detect +   │ (network  │ (timing  │ (data)           │
│  coarse     │  ID)      │  sync)   │                  │
│  sync)      │           │          │                  │
└─────────────┴───────────┴──────────┴──────────────────┘
```

- **Preamble**: Configurable length (6 to 65535 symbols). Default 8 for LoRaWAN. Longer preambles enable detection at lower SNR.
- **Sync Word**: 2 modulated symbols serving as network identifier (0x12 = LoRaWAN public, 0x34 = LoRaWAN private, 0x2B = Meshtastic)
- **SFD**: 2.25 down-chirps. The transition from up-chirps to down-chirps marks the end of the preamble and allows fine time/frequency synchronization.
- **Payload**: Header (optional) + encoded data + CRC (optional)

### 4.2 Synchronization Challenges

At long range (low SNR), synchronization is extremely difficult because:
1. The spread signal can be **multiple orders of magnitude below thermal noise**
2. The maximum-likelihood (ML) cost function for joint time/frequency estimation is **not concave** -- exhaustive search over all possible values is needed
3. Time offset (STO) and frequency offset (CFO) are **intertwined** and must be jointly estimated
4. Sampling frequency offset (SFO) causes progressive drift during packet reception

### 4.3 Preamble Detection and Coarse Synchronization

**Energy Detection**: Correlate incoming samples with a reference up-chirp. After dechirping, an unmodulated up-chirp produces a peak at bin 0 of the FFT. Multiple consecutive peaks at bin 0 indicate a preamble.

**Threshold**: Typically, 6 consecutive preamble symbols must be correctly detected before declaring "packet present."

### 4.4 Integer CFO and STO Resolution

The system of equations from up-chirp and down-chirp analysis:

```
k_up   = (STO + CFO) mod M      (from preamble up-chirps)
k_down = (STO - CFO) mod M      (from SFD down-chirps)
```

Solving:
```
STO = (k_up + k_down) / 2  mod M
CFO = (k_up - k_down) / 2  mod M
```

**Constraint**: This only works if CFO < B/4 (carrier frequency offset must be less than one quarter of the bandwidth).

### 4.5 Fractional Offset Estimation

Fractional STO (sub-sample timing error) causes energy leakage between adjacent FFT bins. If not corrected during the preamble, it increases the probability of incorrectly demodulating preamble symbols and thus incorrectly estimating integer offsets.

### 4.6 Key Algorithms in Literature

| Algorithm | Source | Key Contribution |
|-----------|--------|------------------|
| **ML exhaustive search** | Bernier et al. (2020) | Optimal but O(M^2) complexity |
| **Near-optimal concave recovery** | IEEE (2021) | Recovers ML cost function concavity; 2.8 Hz freq accuracy at SF9 |
| **Low-complexity robust** | arXiv:1912.11344 | Handles fractional STO in preamble; practical for IoT end nodes |
| **Two-pass SFO compensation** | arXiv:2502.08485 (2025) | Estimates SFO in preamble; 6 dB gain at SER=10^-3 |
| **Differential CSS (DCSS)** | MDPI Sensors (2022) | No full frequency sync needed; suited for LEO satellite |

### 4.7 IEEE 9555814 — Key Contributions

The paper "LoRa Signal Synchronization and Detection at Extremely Low SNRs" presents:
- An independently developed packet reception algorithm
- Advanced signal presence detection beyond standard threshold-based approaches
- Improved synchronization strategies for adverse noise conditions
- Multiple algorithm variations compared for BER performance vs computational cost
- SDR (software-defined radio) implementation providing insight into LoRa physical layer

---

## 5. SDR Implementations of LoRa

### 5.1 gr-lora_sdr (EPFL — Tapparel et al.)

**Repository**: https://github.com/tapparelj/gr-lora_sdr

- **Fully functional** GNU Radio LoRa transceiver (TX + RX)
- Operates correctly **even at very low SNRs**
- Tested with USRP, interoperable with SX1276, SX1262, RFM95
- Configurable: SF, CR, BW, sync word, header mode, CRC
- GNU Radio 3.10 compatible
- Related to IEEE 9555814 paper research

**Capabilities**:
- End-to-end USRP-to-USRP and USRP-to-commercial-transceiver communication
- All spreading factors supported
- Provides experimental BER performance data at low SNR

### 5.2 gr-lora (Pieter Robyns et al.)

**Repository**: https://github.com/rpp0/gr-lora

- **Receive-only** GNU Radio LoRa decoder
- SDR hardware tested: HackRF One, USRP B201, RTL-SDR, LimeSDR
- Transmitter hardware tested: Pycom LoPy, Dragino HAT, Adafruit Feather, SX1276 boards
- Near-100% decoding accuracy under clear signal conditions (v0.6+)
- All spreading factors supported (SF11/SF12 slower)

### 5.3 SDR vs Hardware Performance Comparison

| Aspect | SDR (gr-lora_sdr) | Hardware (SX1262/SX1276) |
|--------|-------------------|--------------------------|
| Sensitivity | Comparable at low SNR | -148 dBm (SF12, BW 7.8 kHz) |
| Power consumption | High (USRP: ~watts) | Low (SX1262: 4.6 mA RX) |
| Flexibility | Full parameter control | Limited to chip capabilities |
| Cost | $200-2000+ (SDR hardware) | $2-10 (LoRa module) |
| Size | Large (SDR + computer) | Tiny (4x4 mm QFN) |
| Research value | Excellent (modify algorithms) | Production deployment |
| Real-time capability | Depends on compute power | Always real-time |

### 5.4 Relevance to MeshForge

SDR implementations are primarily valuable for:
- **Validating RF calculations** in MeshForge against real-world measurements
- **Understanding demodulation algorithms** to improve signal quality metrics
- **Research** into improved detection at low SNR
- **Spectrum monitoring** without consuming the single TCP connection to meshtasticd

---

## 6. LoRa Capture Effect

### 6.1 Definition

The **capture effect** occurs when two LoRa signals using the same spreading factor collide at a receiver, but the stronger signal can still be decoded if its power exceeds the weaker signal by a sufficient threshold.

### 6.2 Power Ratio Threshold

The widely accepted capture effect threshold is **6 dB** (approximately 4x power ratio):

```
Capture Condition: P_wanted - P_interferer >= 6 dB  (same SF)
```

At this threshold, the wanted signal's FFT peak is sufficiently above the interferer's peak for correct detection.

### 6.3 Theoretical Analysis

After dechirping a collision between two same-SF signals:
- The wanted signal produces a **strong peak** at bin m_wanted
- The interferer produces **two smaller peaks** (because it is not synchronized with the receiver's dechirp window, so its energy splits across two FFT bins)

At 0 dB SIR (equal power), the wanted peak may still be identifiable if perfectly synchronized. However, practical deployments require the 6 dB margin for reliable operation.

### 6.4 Cross-SF Interference

Signals with **different spreading factors** are quasi-orthogonal:
- Cross-SF rejection is approximately **16-20 dB** (depending on SF pair)
- Different SFs can coexist on the same channel with minimal interference
- This orthogonality is not perfect, and degrades at high traffic loads

### 6.5 Capture Effect Limitations

- Does **not** scale well in dense networks (many simultaneous transmitters)
- Timing matters: if the interferer starts during the preamble of the wanted signal, synchronization may fail even with power advantage
- The 6 dB threshold assumes same-SF collision; cross-SF has different thresholds

### 6.6 Implications for Meshtastic Mesh Networks

In a Meshtastic mesh with multiple nodes transmitting simultaneously:
- **Nearby nodes dominate**: A node 100 m away will capture the receiver over a node 1 km away (path loss difference >> 6 dB)
- **Retransmission** (mesh hopping) mitigates some collisions
- **Channel activity detection (CAD)** before transmitting reduces collision probability
- Choosing different presets (SFs) can separate traffic, but all nodes in a Meshtastic mesh must use the same preset

---

## 7. Near-Far Problem in LoRa

### 7.1 The Problem

The near-far problem is the fundamental unfairness in LoRaWAN-style networks where transmissions from **nearby nodes overpower** signals from distant nodes. A node at 100 m distance might arrive at -60 dBm while a node at 10 km arrives at -130 dBm — a 70 dB difference that overwhelms the receiver's ability to decode the distant signal.

### 7.2 Receiver Dynamic Range Specifications

| Parameter | SX1276 | SX1262 |
|-----------|--------|--------|
| Blocking immunity (1 MHz offset) | ~89 dB | 88 dB |
| Co-channel rejection (LoRa) | ~20 dB | 19 dB |
| Adjacent channel selectivity | ~33 dB | Similar |
| Maximum RSSI before saturation | ~-1 dBm | ~-1 dBm |

Both chips have **very similar** blocking/co-channel rejection. Neither provides a dramatic advantage against the near-far problem at the chip level.

**Worst case**: An interferer on the same channel. Co-channel rejection of ~19-20 dB means the wanted signal must be within 19-20 dB of the interferer to be decoded. Combined with the capture effect (6 dB), the practical limit is that the wanted signal must be no more than about **6-20 dB weaker** than an interferer on the same channel and SF.

### 7.3 Out-of-Band Blocking

For strong signals from other services (e.g., cellular at 850 MHz, 50 MHz from the 915 MHz ISM band):
- 88 dB blocking immunity at 1 MHz offset
- External **cavity filters** (60 dB out-of-band rejection, <1 dB insertion loss) can mitigate interference from nearby cellular towers

### 7.4 Mitigations

| Strategy | Mechanism | Applicability |
|----------|-----------|---------------|
| **Adaptive TX power** | Equalize received power at gateway | LoRaWAN ADR; not standard in Meshtastic |
| **SF allocation** | Assign higher SF to distant nodes | LoRaWAN ADR; Meshtastic uses fixed preset |
| **Gateway density** | More gateways = shorter distances | Applies to both |
| **External filtering** | Reject out-of-band interference | Both; ~$30-50 per filter |
| **Antenna placement** | Higher antennas = better path to distant nodes | Both; most impactful |

### 7.5 Meshtastic-Specific Considerations

- Meshtastic uses a **single preset** for all nodes (no ADR)
- Mesh topology inherently mitigates some near-far issues (retransmission from mid-range nodes)
- The **hop limit** (default 3) prevents distant nodes from being entirely unreachable
- The switch from LongFast to MediumSlow/MediumFast in dense deployments helps by reducing airtime and collision probability

---

## 8. Coding Rate Impact on BER

### 8.1 LoRa Forward Error Correction

LoRa uses **Hamming codes** for FEC. Four data bits are extended to 5, 6, 7, or 8 coded bits:

| Coding Rate | Notation | Parity Bits | Overhead | Error Correction |
|-------------|----------|-------------|----------|------------------|
| 4/5 | CR1 | 1 | 25% | Detection only (no correction) |
| 4/6 | CR2 | 2 | 50% | Detection only (despite overhead) |
| 4/7 | CR3 | 3 | 75% | Can correct 1 bit error per codeword |
| 4/8 | CR4 | 4 | 100% | Can correct 1 bit error per codeword |

**Important**: CR 4/6 adds overhead but **cannot correct** erroneous bits — it only detects them. CR 4/7 and CR 4/8 can correct one erroneous bit per codeword.

### 8.2 BER Performance: CR 4/5 vs CR 4/8

From Mroue et al. ("Analytical and Simulation Study for LoRa Modulation"):

At SF7, BW=125 kHz, SNR = -10 dB:
```
CR 4/5:  BER ~ 1.5 x 10^-1  (15% bit error rate)
CR 4/8:  BER ~ 2.0 x 10^-2  (2% bit error rate)
```

**CR 4/8 provides roughly an order of magnitude (7-8x) lower BER** at the same SNR.

### 8.3 Packet Error Rate Comparison

At SF7, BW=125 kHz, 32-byte packets:

| SNR | PER (CR 4/5) | PER (CR 4/8) |
|-----|-------------|-------------|
| 0 dB | 0.0013 | 0.0004 |
| -9 dB | 0.7077 | 0.5380 |

The difference is most pronounced **under strong interference** (PER > 0.5). At good SNR, both CRs perform similarly.

### 8.4 Throughput vs Reliability Tradeoff

```
Effective bit rate = SF * (BW / 2^SF) * (4 / (4 + n_parity))
```

| CR | Fraction of useful data | Relative throughput |
|----|------------------------|---------------------|
| 4/5 | 80% | 100% (baseline) |
| 4/6 | 67% | 83% |
| 4/7 | 57% | 71% |
| 4/8 | 50% | 63% |

CR 4/8 halves the effective throughput compared to CR 4/5.

### 8.5 Future: LDPC Codes for LoRa

Research (LLDPC — "Low-Density Parity-Check Coding Scheme for LoRa Networks") shows that replacing Hamming codes with LDPC codes significantly reduces BER and extends battery lifetime at CR 4/5, offering potentially the best of both worlds (throughput + reliability). This is not yet implemented in commercial hardware.

### 8.6 Practical Guidance for Meshtastic

- **CR 4/5** (Meshtastic LongFast, MediumFast, ShortFast): Good for normal conditions, maximizes throughput
- **CR 4/8** (Meshtastic LongModerate, LongSlow, VeryLongSlow): Use in harsh RF environments, interference-heavy areas, or when maximum range is needed
- The **BER improvement from CR 4/8 matters most at the edge of coverage** where SNR is near the demodulation threshold

---

## 9. SX1262 vs SX1276 Receiver Performance

### 9.1 Comprehensive Comparison

| Parameter | SX1276 (Gen 1) | SX1262 (Gen 2) | Delta |
|-----------|----------------|----------------|-------|
| **Max Sensitivity** | -148 dBm | -148 dBm | 0 dB |
| **Max TX Power** | +20 dBm | +22 dBm | +2 dB |
| **Max Link Budget** | 168 dB | 170 dB | +2 dB |
| **RX Current** | ~10-12 mA | ~4.6 mA | -58% |
| **TX Current** | 120 mA @ 20 dBm | 118 mA @ 22 dBm | Similar |
| **Sleep Current** | ~0.2 uA | ~0.6 uA | Higher |
| **Noise Figure** | ~6 dB | ~6 dB | Same |
| **Blocking (1 MHz)** | ~89 dB | 88 dB | Similar |
| **Co-channel Rejection** | ~20 dB | 19 dB | Similar |
| **Architecture** | Low-IF | Newer (undisclosed details) | Different |
| **Package** | 6x6 mm, 28-pin QFN | 4x4 mm, 24-pin QFN | Smaller |
| **Image Rejection** | Specified after calibration | Specified in datasheet | Similar |
| **RX Boosted Gain Mode** | Not available | Available (improves sensitivity ~3 dB for FSK) | SX1262 only |
| **Supply Voltage** | 1.8-3.7V | 1.8-3.7V | Same |

### 9.2 Key Differences That Matter

1. **TX Power (+2 dB)**: SX1262's +22 dBm vs SX1276's +20 dBm directly adds 2 dB to link budget
2. **RX Power Consumption**: SX1262 uses ~60% less current in receive mode — critical for battery-powered Meshtastic nodes
3. **RX Boosted Gain**: SX1262 has a power-for-sensitivity tradeoff mode not available on SX1276
4. **Same sensitivity floor**: Both achieve -148 dBm at SF12/BW7.8kHz

### 9.3 Practical Observations from Meshtastic Community

- SX1262-based boards (T-Beam 1.1, Heltec V3) sometimes report **different RSSI/SNR values** than SX1276 boards for the same signal — this is due to different internal signal strength calculation methods, not actual sensitivity differences
- Some users report SX1262 showing "lower SNR" which is actually a measurement reporting difference, not a performance degradation
- Both chips perform equivalently for LoRa demodulation; the SX1262 wins on power efficiency

### 9.4 Recommendation for MeshForge

MeshForge's `rf.py` sensitivity values are chip-agnostic (they represent the LoRa modulation sensitivity, not chip-specific values). This is correct since both SX1276 and SX1262 achieve the same sensitivity floors. For link budget calculations, the **TX power difference** (+20 vs +22 dBm) should be a configurable parameter.

---

## 10. LoRa Link Budget Best Practices

### 10.1 Complete Link Budget Equation

```
Link Budget (dB) = P_TX + G_TX - L_TX - FSPL - L_misc + G_RX - L_RX - Fade_Margin
```

Where:
| Parameter | Symbol | Typical Values |
|-----------|--------|---------------|
| TX Power | P_TX | +14 to +30 dBm (regulatory) |
| TX Antenna Gain | G_TX | 0 to 6 dBi |
| TX Cable/Connector Loss | L_TX | 0.5 to 3 dB |
| Free Space Path Loss | FSPL | Distance dependent |
| Miscellaneous Loss | L_misc | Foliage, building penetration |
| RX Antenna Gain | G_RX | 0 to 12 dBi |
| RX Cable/Connector Loss | L_RX | 0.5 to 3 dB |
| Fade Margin | FM | 10 to 30 dB |

**EIRP** (Effective Isotropic Radiated Power):
```
EIRP = P_TX + G_TX - L_TX
```

### 10.2 Free Space Path Loss (Friis)

```
FSPL (dB) = 20*log10(d) + 20*log10(f) + 32.44
```

Where d = distance in km, f = frequency in MHz.

At **915 MHz**:
```
FSPL = 20*log10(d_km) + 20*log10(915) + 32.44
     = 20*log10(d_km) + 91.7
```

| Distance | FSPL at 915 MHz |
|----------|----------------|
| 100 m    | 71.7 dB |
| 500 m    | 85.6 dB |
| 1 km     | 91.7 dB |
| 5 km     | 105.6 dB |
| 10 km    | 111.7 dB |
| 50 km    | 125.6 dB |
| 100 km   | 131.7 dB |

**Note**: Each doubling of distance adds 6 dB of path loss.

### 10.3 Okumura-Hata Model (Urban/Suburban)

For realistic urban deployments:

```
PL = 69.55 + 26.16*log10(f_MHz) - 13.82*log10(h_BS) - C_H + [44.9 - 6.55*log10(h_BS)] * log10(d_km)
```

Where:
- f_MHz = frequency in MHz
- h_BS = base station (gateway) antenna height in meters
- C_H = mobile antenna height correction factor
- d_km = distance in km

At 915 MHz with a 30 m gateway antenna:
- ~5.7 km usable range in urban environment
- ~10-15 km in suburban
- ~50+ km in rural/open terrain

### 10.4 Fade Margin Guidelines

| Environment | Recommended Fade Margin |
|-------------|------------------------|
| Line-of-sight (open field) | 10 dB |
| Suburban (partial obstruction) | 15-20 dB |
| Urban (dense buildings) | 20-25 dB |
| Indoor penetration | 25-30 dB |
| Heavy foliage | 15-20 dB |

### 10.5 Additional Real-World Loss Factors

| Factor | Typical Loss |
|--------|-------------|
| Fresnel zone obstruction (50%) | 6 dB |
| Building penetration (concrete) | 10-20 dB |
| Building penetration (wood) | 5-10 dB |
| Dense foliage (per 10 m) | 1-2 dB |
| Rain fade (915 MHz) | Negligible |
| Atmospheric absorption (915 MHz) | ~0.01 dB/km (negligible) |
| Earth curvature (beyond ~15 km) | Variable, depends on antenna height |

### 10.6 Complete Link Budget Example — Meshtastic LongFast

**Scenario**: Two Meshtastic nodes, LongFast preset (SF11, BW=250 kHz), 915 MHz

```
TX side:
  TX Power (SX1262):          +22 dBm
  Antenna Gain (stock whip):   +2 dBi
  Cable Loss (U.FL pigtail):   -0.5 dB
  EIRP:                        +23.5 dBm

RX side:
  RX Sensitivity (SF11/250):   -131.5 dBm
  Antenna Gain (stock whip):   +2 dBi
  Cable Loss (U.FL pigtail):   -0.5 dB
  Effective Sensitivity:       -133.0 dBm

Maximum Allowable Path Loss:
  MAPL = 23.5 + 133.0 = 156.5 dB

With 15 dB fade margin:
  Usable MAPL = 156.5 - 15 = 141.5 dB

Free-space range at 141.5 dB:
  d = 10^((141.5 - 91.7) / 20) = 10^(2.49) = ~309 km (free space, theoretical)

Realistic range (Okumura-Hata urban, 10m antenna):
  ~3-5 km

Realistic range (Okumura-Hata suburban, 30m antenna):
  ~8-15 km
```

### 10.7 Industry Best Practices

1. **Always include fade margin** — 10 dB minimum, 20+ dB for reliability
2. **Account for cable losses** — Even short pigtails add 0.2-0.5 dB at 915 MHz
3. **Use realistic path loss models** — Free space is optimistic; use Okumura-Hata or measured data
4. **Consider Fresnel zone clearance** — First Fresnel zone at 915 MHz, 10 km: radius ~18 m at midpoint
5. **Verify with Semtech LoRa Calculator** — https://www.semtech.com/design-support/lora-calculator
6. **Test empirically** — Models are starting points; real-world propagation varies significantly
7. **Document antenna height** — Antenna height is the single most impactful variable for range
8. **Regulatory compliance** — US 915 MHz: max +30 dBm EIRP (FCC Part 15.247)

---

## 11. Meshtastic Preset Reference

### 11.1 Complete Preset Table

| Preset | SF | BW (kHz) | CR | Approx Sensitivity | Link Budget (@ +22 dBm) | Approx Bit Rate |
|--------|-----|---------|------|-------------------|-------------------------|-----------------|
| **ShortTurbo** | 7 | 500 | 4/5 | -126 dBm | ~148 dB | ~21.9 kbps |
| **ShortFast** | 7 | 250 | 4/5 | -129 dBm | ~151 dB | ~10.9 kbps |
| **ShortSlow** | 8 | 250 | 4/5 | -131.5 dBm | ~153.5 dB | ~6.25 kbps |
| **MediumFast** | 9 | 250 | 4/5 | -134 dBm | ~156 dB | ~3.52 kbps |
| **MediumSlow** | 10 | 250 | 4/5 | -136.5 dBm | ~158.5 dB | ~1.95 kbps |
| **LongFast** | 11 | 250 | 4/5 | -139 dBm | ~161 dB | ~1.07 kbps |
| **LongModerate** | 11 | 125 | 4/8 | -142 dBm | ~164 dB | ~0.34 kbps |
| **LongSlow** | 12 | 125 | 4/8 | -144.5 dBm | ~166.5 dB | ~0.18 kbps |
| **VeryLongSlow** | 12 | 62.5 | 4/8 | -147.5 dBm | ~169.5 dB | ~0.09 kbps |

### 11.2 Airtime and Channel Capacity

Each step up in SF roughly doubles the airtime. At LongFast (SF11/250):
- A typical 32-byte Meshtastic message takes ~2 seconds on air
- At VeryLongSlow (SF12/62.5): the same message takes ~30+ seconds

### 11.3 Deployment Guidance

- **Urban dense networks (60+ nodes)**: MediumFast or MediumSlow
- **Suburban general use**: LongFast (default)
- **Maximum range**: LongSlow or VeryLongSlow
- **Maximum reliability in noise**: LongModerate (CR 4/8 for error correction)
- **High throughput needs**: ShortFast or ShortTurbo

---

## 12. Application to MeshForge RF Calculations

### 12.1 Current Implementation Status

MeshForge's `src/utils/rf.py` currently implements:
- Signal quality classification (Excellent/Good/Fair/Bad/None)
- RSSI/SNR normalization and percentage scoring
- Link margin calculation
- Sensitivity lookup by spreading factor (BW=125 kHz assumed)
- Free space path loss calculation
- Fresnel zone radius calculation
- Cable and connector loss database

### 12.2 Enhancement Opportunities Based on This Research

1. **Bandwidth-aware sensitivity**: Current `LORA_SENSITIVITY_DBM` assumes BW=125 kHz. Should parameterize by bandwidth since Meshtastic presets use 62.5, 125, 250, and 500 kHz.

   Formula: `sensitivity = -174 + 10*log10(BW_Hz) + NF + SNR_limit[SF]`

2. **Meshtastic preset profiles**: Add a `MESHTASTIC_PRESETS` dictionary mapping preset names to (SF, BW, CR) tuples with pre-computed sensitivity and link budget values.

3. **Capture effect analysis**: Given two signals' RSSI values, determine if the stronger signal will be captured (>6 dB delta for same-SF).

4. **Coding rate in link budget**: Account for CR 4/8 vs 4/5 when estimating effective throughput and reliability.

5. **Okumura-Hata path loss model**: Add urban/suburban propagation model alongside free space path loss for more realistic range estimates.

6. **SX1262 vs SX1276 TX power**: Parameterize TX power based on chip generation (20 vs 22 dBm default).

### 12.3 Formulas Ready for Implementation

```python
# Receiver sensitivity (general)
def rx_sensitivity(sf: int, bw_hz: float, nf_db: float = 6.0) -> float:
    snr_limits = {7: -7.5, 8: -10.0, 9: -12.5, 10: -15.0, 11: -17.5, 12: -20.0}
    return -174.0 + 10 * math.log10(bw_hz) + nf_db + snr_limits[sf]

# LoRa bit rate
def lora_bit_rate(sf: int, bw_hz: float, cr_num: int = 5) -> float:
    return sf * (bw_hz / (2**sf)) * (4.0 / cr_num)

# Processing gain
def processing_gain_db(sf: int) -> float:
    return sf * 10 * math.log10(2)

# Free space path loss at 915 MHz
def fspl_915mhz(distance_km: float) -> float:
    return 20 * math.log10(distance_km) + 20 * math.log10(915) + 32.44

# Capture effect check
def will_capture(rssi_wanted: float, rssi_interferer: float, threshold_db: float = 6.0) -> bool:
    return (rssi_wanted - rssi_interferer) >= threshold_db
```

---

## References

### Key Papers

1. IEEE 9555814 — "LoRa Signal Synchronization and Detection at Extremely Low Signal-to-Noise Ratios" — [IEEE Xplore](https://ieeexplore.ieee.org/document/9555814/)
2. Vangelista (2017) — "Frequency Shift Chirp Modulation: The LoRa Modulation" — [IEEE Xplore](https://ieeexplore.ieee.org/document/8067462/)
3. Chiani & Elzanaty (2019) — "On the LoRa Modulation for IoT: Waveform Properties" — [arXiv:1906.04256](https://arxiv.org/pdf/1906.04256)
4. Elshabrawy & Robert (2019) — "On the Error Rate of the LoRa Modulation with Interference" — [arXiv:1905.11252](https://arxiv.org/pdf/1905.11252)
5. Tapparel et al. — "From Demodulation to Decoding: Toward Complete LoRa PHY Understanding" — [ACM TOSN](https://dl.acm.org/doi/10.1145/3546869)
6. Bernier et al. — "Low Complexity LoRa Frame Synchronization for Ultra-Low Power" — [CEA HAL](https://cea.hal.science/cea-02280910v2/document)
7. arXiv:1912.11344 — "A Low-Complexity LoRa Synchronization Algorithm Robust to Sampling Frequency Offset"
8. arXiv:2502.08485 — "LoRa Fine Synchronization with Two-Pass Time and Frequency Offset Estimation" (2025)
9. Mroue et al. — "Analytical and Simulation Study for LoRa Modulation" — [ResearchGate](https://www.researchgate.net/publication/324703847_Analytical_and_Simulation_study_for_LoRa_Modulation)
10. arXiv:1911.10245 — "Coded LoRa Frame Error Rate Analysis"

### Semtech Application Notes & Datasheets

11. AN1200.22 — [LoRa Modulation Basics](https://www.frugalprototype.com/wp-content/uploads/2016/08/an1200.22.pdf)
12. AN1200.13 — [LoRa Modem Designer's Guide](https://www.openhacks.com/uploadsproductos/loradesignguide_std.pdf)
13. AN1200.86 — [LoRa and LoRaWAN](https://www.semtech.com/uploads/technology/LoRa/lora-and-lorawan.pdf)
14. SX1262 Datasheet — [SparkFun CDN](https://cdn.sparkfun.com/assets/6/b/5/1/4/SX1262_datasheet.pdf)
15. SX1276 Datasheet — [Adafruit CDN](https://cdn-shop.adafruit.com/product-files/3179/sx1276_77_78_79.pdf)
16. [Semtech LoRa Calculator](https://www.semtech.com/design-support/lora-calculator)

### SDR Implementations

17. gr-lora_sdr (EPFL) — [GitHub](https://github.com/tapparelj/gr-lora_sdr)
18. gr-lora (Robyns) — [GitHub](https://github.com/rpp0/gr-lora)

### Tutorials & Community Resources

19. Gyujun Jeong — [LoRa/CSS Overview, Demodulation and Decoding](https://gyulab.github.io/lora/)
20. Wireless Pi — [Understanding LoRa PHY](https://wirelesspi.com/understanding-lora-phy-long-range-physical-layer/)
21. RF Wireless World — [LoRa Sensitivity Calculator](https://www.rfwireless-world.com/calculators/lora-sensitivity-calculator)
22. RF Wireless World — [LoRa Link Budget Calculator](https://rfwireless-world.com/calculators/LoRa-Link-Budget-Calculator.html)
23. The Things Network — [Spreading Factors](https://www.thethingsnetwork.org/docs/lorawan/spreading-factors/)
24. The Things Network — [RSSI and SNR](https://www.thethingsnetwork.org/docs/lorawan/rssi-and-snr/)
25. Meshtastic — [Radio Settings](https://meshtastic.org/docs/overview/radio-settings/)
26. Meshtastic — [LoRa Configuration](https://meshtastic.org/docs/configuration/radio/lora/)
27. Semtech — [Interference Immunity](https://lora-developers.semtech.com/documentation/tech-papers-and-guides/interference-immunity/)

### Collision & Capture Effect

28. CoRa — "A Collision-Resistant LoRa Symbol Detector" — [arXiv:2412.13930](https://arxiv.org/html/2412.13930v1)
29. "Impact of Spreading Factor Imperfect Orthogonality in LoRa Communications" — [ResearchGate](https://www.researchgate.net/publication/319486965_Impact_of_Spreading_Factor_Imperfect_Orthogonality_in_LoRa_Communications)
30. "Investigating and Experimenting Interference Mitigation by Capture Effect in LoRa Networks" — [ResearchGate](https://www.researchgate.net/publication/335673321_Investigating_and_Experimenting_Interference_Mitigation_by_Capture_Effect_in_LoRa_Networks)

---

*Research compiled for MeshForge NOC development. Made with aloha for the mesh community. -- WH6GXZ*
