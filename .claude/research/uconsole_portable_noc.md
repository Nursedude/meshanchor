# uConsole Portable NOC - Research Document

> **Target Hardware**: uConsole AIO V2 from HackerGadgets
> **Timeline**: April 2026 build
> **Architect**: WH6GXZ (Nursedude)

## Hardware Overview

### uConsole AIO V2 Specifications

| Component | Chip/Spec | MeshForge Integration |
|-----------|-----------|----------------------|
| **Compute** | CM5 8GB RAM | Full MeshForge stack |
| **LoRa** | SX1262 (SPI) | Native Meshtastic (meshtasticd) |
| **RTL-SDR** | RTL2832U + R860 | Spectrum analysis, SIGINT |
| **GPS** | Multi-GNSS | Position reporting, time sync |
| **RTC** | PCF85063A | Reliable timestamps offline |
| **Network** | RJ45 Gigabit | AREDN mesh connectivity |
| **USB** | USB 3.0 Hub | Peripherals, storage |

**Source**: https://hackergadgets.com/products/uconsole-aio-v2

### SX1262 LoRa Details
- Frequency: 860-960MHz (US: 902-928MHz, EU: 863-870MHz)
- Output Power: 22dBm max (158mW)
- Interface: SPI (CE1)
- TCXO for frequency stability
- Meshtastic compatible out of box

### RTL-SDR Details
- Tuner: R860 (improved R820T2)
- Frequency: 100kHz - 1.74GHz
- HF Direct Sampling: 100kHz - 28.8MHz
- Bias Tee: 5V for active antennas
- TCXO for frequency accuracy

### GPIO Power Control
The AIO V2 allows software control of subsystem power (active LOW):
- GPIO17: LoRa module
- GPIO27: RTL-SDR
- GPIO22: GPS
- GPIO23: USB Hub

This enables power-saving modes for extended field operation.

## MeshForge Integration Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    MESHFORGE PORTABLE NOC                    │
│                        (uConsole AIO V2)                     │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐      │
│  │   LAUNCHER   │  │     GTK      │  │     WEB      │      │
│  │  TUI (5" OK) │  │  (External)  │  │   MONITOR    │      │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘      │
│         │                 │                 │               │
│         └─────────────────┼─────────────────┘               │
│                           │                                 │
│  ┌────────────────────────┴────────────────────────┐       │
│  │              UNIFIED MAP (P2)                    │       │
│  │         Meshtastic + RNS + AREDN                │       │
│  └─────────────────────────────────────────────────┘       │
│                           │                                 │
│  ┌────────────────────────┼────────────────────────┐       │
│  │                        │                        │       │
│  ▼                        ▼                        ▼       │
│  ┌──────────┐     ┌──────────┐     ┌──────────┐           │
│  │MESHTASTIC│     │   RNS    │     │  AREDN   │           │
│  │ SX1262   │     │  (rnsd)  │     │ Ethernet │           │
│  │   SPI    │     │   TCP    │     │   10.x   │           │
│  └──────────┘     └──────────┘     └──────────┘           │
│                                                             │
│  ┌─────────────────────────────────────────────────┐       │
│  │                SIGINT (Intercept)                │       │
│  │  ┌────────┐ ┌────────┐ ┌────────┐ ┌────────┐   │       │
│  │  │ ADS-B  │ │ PAGER  │ │ 433MHz │ │ SCAN   │   │       │
│  │  │dump1090│ │multimon│ │rtl_433 │ │rtl_pwr │   │       │
│  │  └────────┘ └────────┘ └────────┘ └────────┘   │       │
│  └─────────────────────────────────────────────────┘       │
│                           │                                 │
│                    ┌──────┴──────┐                         │
│                    │   RTL-SDR   │                         │
│                    │  RTL2832U   │                         │
│                    │ 100k-1.74G  │                         │
│                    └─────────────┘                         │
│                                                             │
│  ┌─────────────────────────────────────────────────┐       │
│  │              HARDWARE SERVICES                   │       │
│  │  ┌────────┐ ┌────────┐ ┌────────┐ ┌────────┐   │       │
│  │  │  GPS   │ │  RTC   │ │ POWER  │ │ DETECT │   │       │
│  │  │ gpsd   │ │hwclock │ │  GPIO  │ │uconsole│   │       │
│  │  └────────┘ └────────┘ └────────┘ └────────┘   │       │
│  └─────────────────────────────────────────────────┘       │
└─────────────────────────────────────────────────────────────┘
```

## Implementation Status

### Completed (This Session)
- [x] `src/utils/uconsole.py` - Hardware detection and configuration
- [x] `src/utils/intercept.py` - SIGINT platform integration
- [x] GPIO power control support
- [x] meshtasticd config generation for SX1262

### Pending (April Build)
- [ ] Physical testing with actual hardware
- [ ] TUI optimization for 5" screen
- [ ] Power management profiles (field/stationary)
- [ ] Intercept web embedding or iframe
- [ ] Antenna configuration guide
- [ ] Field deployment documentation

## Use Cases

### 1. Emergency Communications (EMCOMM)
- Deploy as portable mesh gateway
- Bridge Meshtastic ↔ RNS ↔ AREDN
- Provide unified situational awareness
- Track responder positions via GPS

### 2. Search and Rescue (SAR)
- Map all mesh nodes in search area
- Track team positions
- Monitor aviation (ADS-B) for helicopter ops
- Log communications timeline

### 3. Ham Radio Field Day
- Portable station with multiple modes
- Spectrum monitoring
- Real-time propagation via HamClock
- Score tracking and logging

### 4. Infrastructure Assessment
- Survey spectrum utilization
- Map existing mesh coverage
- Identify interference sources
- Document network topology

### 5. Signal Intelligence (Authorized)
- Monitor ISM band sensors (weather, TPMS)
- Track aircraft in area
- Decode pager traffic (if legal in jurisdiction)
- Frequency coordination

## Related Projects

### Intercept
- **URL**: https://github.com/smittix/intercept
- **Purpose**: Web-based SDR signal intelligence dashboard
- **Tools**: rtl_433, dump1090, multimon-ng, acarsdec
- **Integration**: Launch from MeshForge, share RTL-SDR

### Similar Cyberdecks
- **DevTerm** (ClockworkPi) - Same form factor, less radio
- **CyberDeck builds** on r/cyberdeck
- **PINE64 PinePhone Pro** - Phone form factor
- **Framework Laptop 16** - Larger, modular

## Configuration Files

### meshtasticd for SX1262 (uConsole AIO V2)
```yaml
# /etc/meshtasticd/config.yaml
Lora:
  Module: sx1262
  CS: 1
  IRQ: 22
  Busy: 23
  Reset: 24
  DIO2_AS_RF_SWITCH: true

Webserver:
  Port: 4403
```

### gpsd for GPS
```bash
# /etc/default/gpsd
DEVICES="/dev/ttyAMA0"
GPSD_OPTIONS="-n"
```

### Power Saving Script
```bash
#!/bin/bash
# Disable SDR and GPS when on battery
echo 0 > /sys/class/gpio/gpio27/value  # SDR off
echo 0 > /sys/class/gpio/gpio22/value  # GPS off
```

## Testing Plan (April 2026)

1. **Initial Boot**
   - Verify CM5 boots with AIO V2
   - Check all GPIO exports
   - Verify SPI device /dev/spidev0.1

2. **LoRa Testing**
   - Configure meshtasticd
   - Join mesh network
   - Verify TX/RX with other nodes
   - Range test

3. **SDR Testing**
   - Run rtl_test for device check
   - Capture 433MHz with rtl_433
   - Run ADS-B with dump1090
   - Spectrum scan with rtl_power

4. **GPS Testing**
   - Configure gpsd
   - Verify fix acquisition
   - Test position reporting to mesh

5. **Integration Testing**
   - Full MeshForge stack
   - Unified map with all networks
   - Power management cycling
   - Field endurance test

## References

- [uConsole AIO V2 Product Page](https://hackergadgets.com/products/uconsole-aio-v2)
- [uConsole AIO Setup Guide](https://hackergadgets.com/pages/hackergadgets-uconsole-rtl-sdr-lora-gps-rtc-usb-hub-all-in-one-extension-board-setup-guide)
- [Intercept GitHub](https://github.com/smittix/intercept)
- [RTL-SDR Blog - uConsole](https://www.rtl-sdr.com/an-rtl-sdr-lora-gps-rtc-usb-hub-extension-board-for-the-uconsole/)
- [Meshtastic SX126x Config](https://meshtastic.org/docs/hardware/devices/)
- [SX1262 Datasheet](https://www.semtech.com/products/wireless-rf/lora-transceivers/sx1262)

---
*Research compiled by Claude Code for MeshForge project*
*Hardware arrival: April 2026*
