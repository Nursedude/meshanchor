# Firmware Integration Viability Analysis

> **Created**: 2026-01-14
> **Purpose**: Research viability of integrating firmware tooling for RAK, Heltec, and Station G2

---

## Executive Summary

**Verdict: Viable as a PLUGIN, not core**

Firmware development (C++/PlatformIO) should remain separate from MeshForge (Python), but MeshForge can integrate:
1. Firmware flashing/updating (Python wrappers)
2. Device-specific configuration profiles
3. Build automation scripts
4. Hardware documentation

This approach won't break MeshForge's existing architecture.

---

## Target Devices

### 1. RAK WisBlock (nRF52840)

| Spec | Value |
|------|-------|
| MCU | nRF52840 (ARM Cortex-M4) |
| Flash Method | UF2 drag-and-drop, nrfjprog |
| Bootloader | Adafruit nRF52 UF2 |
| Build System | PlatformIO (Nordic framework) |
| MeshForge Support | Existing hardware detection |

**Variants**:
- RAK4631 (nRF52840 + SX1262)
- RAK3172 (STM32WLE5)
- WisMesh RAK3312 (ESP32-S3 + SX1262)

### 2. Heltec V3/V4 (ESP32-S3)

| Spec | Value |
|------|-------|
| MCU | ESP32-S3 |
| Flash Method | esptool.py, Web Flasher |
| Build System | PlatformIO (ESP-IDF/Arduino) |
| Output Power | V3: 20dBm, V4: 28dBm |
| MeshForge Support | Existing hardware detection |

**Key Difference**: V4 has higher TX power, same pinout as V3.

### 3. Station G2 (ESP32)

| Spec | Value |
|------|-------|
| MCU | ESP32 (original, not S3) |
| Flash Method | esptool.py |
| Build System | PlatformIO |
| Form Factor | Base station with ethernet |
| MeshForge Support | Not yet in hardware.py |

---

## Architecture Options

### Option A: Separate Firmware Repository (Recommended)

```
meshforge/              # Python NOC (existing)
├── src/
├── plugins/
│   └── firmware/       # Firmware plugin
│       ├── profiles/   # Device-specific configs
│       ├── flasher.py  # esptool/UF2 wrappers
│       └── __init__.py
└── ...

meshforge-firmware/     # Separate repo (C++/PlatformIO)
├── platformio.ini
├── src/
│   └── main.cpp
├── variants/
│   ├── rak4631_gateway/
│   ├── heltec_v4_gateway/
│   └── station_g2_gateway/
└── README.md
```

**Pros**:
- Clean separation of concerns
- Different build toolchains don't conflict
- MeshForge stays Python-only
- Firmware repo can be forked independently

**Cons**:
- Two repos to maintain
- Users must clone both

### Option B: Embedded Firmware Directory

```
meshforge/
├── src/
├── firmware/           # C++/PlatformIO embedded
│   ├── platformio.ini
│   ├── src/
│   └── variants/
└── ...
```

**Pros**:
- Single repo
- Unified versioning

**Cons**:
- Mixes Python and C++ toolchains
- Larger repo size
- Build confusion for contributors
- CI/CD complexity

### Recommendation: Option A

Firmware in separate repo with MeshForge plugin for flashing.

---

## MeshForge Integration Points

### 1. Firmware Flasher Plugin

```python
# plugins/firmware/flasher.py
class FirmwareFlasher:
    """Flash Meshtastic firmware to connected devices"""

    SUPPORTED_DEVICES = {
        'rak4631': {'method': 'uf2', 'firmware_prefix': 'firmware-rak4631'},
        'heltec_v3': {'method': 'esptool', 'firmware_prefix': 'firmware-heltec-v3'},
        'heltec_v4': {'method': 'esptool', 'firmware_prefix': 'firmware-heltec-v4'},
        'station_g2': {'method': 'esptool', 'firmware_prefix': 'firmware-station-g2'},
    }

    def flash_device(self, device_path: str, firmware_path: str) -> bool:
        """Flash firmware to device"""
        # Uses esptool.py for ESP32, UF2 copy for nRF52
        ...

    def download_firmware(self, version: str, device: str) -> Path:
        """Download official firmware from GitHub releases"""
        ...
```

### 2. Gateway Configuration Profiles

```json
// profiles/rak4631_gateway.json
{
  "device": "rak4631",
  "role": "ROUTER_CLIENT",
  "lora": {
    "region": "US",
    "modem_preset": "LONG_FAST",
    "hop_limit": 5
  },
  "bluetooth": {"enabled": false},
  "wifi": {"enabled": false},
  "power": {
    "ls_secs": 0,
    "wait_bluetooth_secs": 0
  },
  "display": {"enabled": false}
}
```

### 3. Device Scanner Enhancement

Add Station G2 to `src/config/hardware.py`:

```python
KNOWN_USB_MODULES = {
    # ... existing ...
    '303a:1001': {
        'name': 'Heltec Station G2',
        'common_devices': ['Station G2'],
        'meshtastic_compatible': True,
        'power_requirement': 'PoE or 5V',
        'notes': 'Base station with ethernet'
    },
}
```

---

## Custom Firmware Features

For gateway nodes, consider custom firmware with:

### Priority Features

1. **Longer packet queues** - Gateway handles more traffic
2. **MQTT optimizations** - Direct bridge to home automation
3. **JSON API enabled** - For MeshForge API integration
4. **Watchdog timers** - Auto-reboot on hang
5. **Remote OTA** - Update firmware via MeshForge

### Device-Specific

| Device | Gateway Optimization |
|--------|---------------------|
| RAK4631 | Low power disabled, max TX |
| Heltec V4 | Full 28dBm TX, display optional |
| Station G2 | Ethernet primary, WiFi backup |

---

## Build Requirements

### For Development

```bash
# PlatformIO Core
pip install platformio

# ESP32 tools
pip install esptool

# nRF52 tools (optional)
# Install nRF Command Line Tools from Nordic
```

### CI/CD

GitHub Actions workflow for firmware builds:

```yaml
# .github/workflows/firmware.yml
name: Build Firmware
on: [push, pull_request]
jobs:
  build:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        variant: [rak4631, heltec_v3, heltec_v4, station_g2]
    steps:
      - uses: actions/checkout@v4
      - uses: actions/cache@v4
        with:
          path: ~/.platformio
          key: ${{ runner.os }}-pio
      - run: pip install platformio
      - run: pio run -e ${{ matrix.variant }}
```

---

## Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| Firmware bricks device | Medium | High | Test builds, recovery docs |
| Build breaks MeshForge | Low | Medium | Separate repo (Option A) |
| Toolchain conflicts | Medium | Low | Virtual environments |
| Upstream Meshtastic changes | High | Medium | Track releases, test regularly |

---

## Implementation Roadmap

### Phase 1: Foundation (No firmware source)
- [ ] Add Station G2 to hardware detection
- [ ] Create firmware flashing plugin
- [ ] Add gateway config profiles
- [ ] Document device-specific setup

### Phase 2: Custom Builds (Separate repo)
- [ ] Fork Meshtastic firmware
- [ ] Create gateway-optimized variants
- [ ] Build automation (CI/CD)
- [ ] MeshForge integration for OTA

### Phase 3: Advanced
- [ ] Automated firmware testing
- [ ] Performance benchmarking
- [ ] Multi-device provisioning

---

## Resources

- [Meshtastic Firmware](https://github.com/meshtastic/firmware)
- [Building Meshtastic](https://meshtastic.org/docs/development/firmware/build/)
- [RAK WisBlock Devices](https://meshtastic.org/docs/hardware/devices/rak-wireless/wisblock/)
- [Heltec V4](https://heltec.org/project/wifi-lora-32-v4/)
- [PlatformIO](https://platformio.org/)

---

## Conclusion

**Recommended approach**:

1. **Don't embed firmware source in MeshForge** - Keep it Python-focused
2. **Create firmware plugin** - Handle flashing, config, OTA
3. **Separate firmware repo** - For custom gateway builds
4. **Start with profiles** - Configure official firmware optimally
5. **Custom builds later** - Only if needed for gateway features

This preserves MeshForge's stability while enabling firmware tooling.

---

*Research completed: 2026-01-14*
