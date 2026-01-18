"""
uConsole AIO V2 Hardware Profile - Detection and Auto-Configuration

Supports the HackerGadgets uConsole AIO V2 expansion board:
- SX1262 LoRa (860-960MHz, 22dBm, SPI) - Meshtastic native
- RTL-SDR (RTL2832U + R860, 100KHz-1.74GHz)
- GPS (Multi-GNSS: GPS/BDS/GLONASS)
- RTC (PCF85063A with battery backup)
- USB 3.0 Hub, RJ45 Gigabit Ethernet

Reference: https://hackergadgets.com/products/uconsole-aio-v2

GPIO Power Control (active low):
- GPIO17: LoRa power
- GPIO27: SDR power
- GPIO22: GPS power
- GPIO23: USB hub power

Note: Hardware not available until April 2026 - this is foundational code.
"""

import logging
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from enum import Enum

logger = logging.getLogger(__name__)


class HardwareComponent(Enum):
    """uConsole AIO V2 hardware components"""
    LORA_SX1262 = "sx1262"
    RTL_SDR = "rtlsdr"
    GPS_GNSS = "gps"
    RTC = "rtc"
    USB_HUB = "usb_hub"
    ETHERNET = "ethernet"


@dataclass
class ComponentStatus:
    """Status of a hardware component"""
    component: HardwareComponent
    detected: bool
    enabled: bool = False
    device_path: str = ""
    details: str = ""


@dataclass
class UConsoleProfile:
    """Complete uConsole AIO V2 hardware profile"""
    detected: bool = False
    board_version: str = ""
    compute_module: str = ""  # CM4 or CM5
    components: Dict[HardwareComponent, ComponentStatus] = field(default_factory=dict)

    def is_complete(self) -> bool:
        """Check if all expected components are present"""
        expected = {HardwareComponent.LORA_SX1262, HardwareComponent.RTL_SDR,
                   HardwareComponent.GPS_GNSS}
        detected = {c for c, s in self.components.items() if s.detected}
        return expected.issubset(detected)

    def summary(self) -> str:
        """Human-readable summary"""
        if not self.detected:
            return "uConsole AIO V2 not detected"

        lines = [f"uConsole AIO V2 ({self.compute_module})"]
        for comp, status in self.components.items():
            icon = "✓" if status.detected else "✗"
            state = "ON" if status.enabled else "OFF"
            lines.append(f"  {icon} {comp.value}: {state} {status.details}")
        return "\n".join(lines)


class UConsoleDetector:
    """
    Detect and configure uConsole AIO V2 hardware.

    The AIO V2 board includes:
    - SX1262 LoRa on SPI0 (CE1)
    - RTL2832U + R860 SDR on USB
    - Multi-GNSS GPS on UART
    - PCF85063A RTC on I2C

    GPIO pins control power to each subsystem for power saving.
    """

    # SX1262 SPI configuration
    SX1262_SPI_BUS = 0
    SX1262_SPI_CS = 1  # CE1
    SX1262_FREQ_MIN = 860_000_000
    SX1262_FREQ_MAX = 960_000_000
    SX1262_POWER_MAX = 22  # dBm

    # RTL-SDR USB identifiers
    RTLSDR_USB_IDS = [
        ("0bda", "2832"),  # RTL2832U
        ("0bda", "2838"),  # RTL2838
    ]

    # GPS UART
    GPS_UART = "/dev/ttyAMA0"  # Primary UART on Pi
    GPS_BAUD = 9600

    # RTC I2C
    RTC_I2C_BUS = 1
    RTC_I2C_ADDR = 0x51  # PCF85063A

    # GPIO power control pins (active low on AIO V2)
    GPIO_LORA = 17
    GPIO_SDR = 27
    GPIO_GPS = 22
    GPIO_USB = 23

    def __init__(self):
        self._profile: Optional[UConsoleProfile] = None
        self._gpio_available = Path("/sys/class/gpio").exists()

    def detect(self) -> UConsoleProfile:
        """
        Perform full hardware detection.

        Returns UConsoleProfile with detected components.
        """
        profile = UConsoleProfile()

        # Detect compute module
        profile.compute_module = self._detect_compute_module()

        # Detect each component
        profile.components[HardwareComponent.LORA_SX1262] = self._detect_sx1262()
        profile.components[HardwareComponent.RTL_SDR] = self._detect_rtlsdr()
        profile.components[HardwareComponent.GPS_GNSS] = self._detect_gps()
        profile.components[HardwareComponent.RTC] = self._detect_rtc()
        profile.components[HardwareComponent.USB_HUB] = self._detect_usb_hub()
        profile.components[HardwareComponent.ETHERNET] = self._detect_ethernet()

        # Mark as detected if we have the key components
        profile.detected = (
            profile.components[HardwareComponent.LORA_SX1262].detected or
            profile.components[HardwareComponent.RTL_SDR].detected
        )

        if profile.detected:
            profile.board_version = "AIO V2"

        self._profile = profile
        return profile

    def _detect_compute_module(self) -> str:
        """Detect CM4 vs CM5"""
        try:
            model_path = Path("/proc/device-tree/model")
            if model_path.exists():
                model = model_path.read_text().strip('\x00')
                if "CM5" in model or "Compute Module 5" in model:
                    return "CM5"
                elif "CM4" in model or "Compute Module 4" in model:
                    return "CM4"
                elif "Raspberry Pi" in model:
                    return model.split("Raspberry Pi")[-1].strip()[:10]
        except Exception as e:
            logger.debug(f"Could not detect compute module: {e}")
        return "Unknown"

    def _detect_sx1262(self) -> ComponentStatus:
        """Detect SX1262 LoRa module on SPI"""
        status = ComponentStatus(
            component=HardwareComponent.LORA_SX1262,
            detected=False
        )

        # Check if SPI is enabled
        spi_dev = Path(f"/dev/spidev{self.SX1262_SPI_BUS}.{self.SX1262_SPI_CS}")
        if not spi_dev.exists():
            status.details = "SPI not enabled"
            return status

        status.device_path = str(spi_dev)

        # Check for sx126x kernel module or meshtasticd using it
        try:
            # Check if meshtasticd is configured for SPI
            config_paths = [
                Path("/etc/meshtasticd/config.yaml"),
                Path("/etc/meshtasticd/config.d/hardware.yaml"),
            ]

            for cfg_path in config_paths:
                if cfg_path.exists():
                    content = cfg_path.read_text()
                    if "SX1262" in content or "sx126" in content.lower():
                        status.detected = True
                        status.details = "SX1262 configured in meshtasticd"
                        break

            # Also check for raw SPI device access
            if not status.detected and spi_dev.exists():
                # SPI device exists - assume SX1262 present
                # (Can't probe without potentially interfering)
                status.detected = True
                status.details = "SPI device available"

        except Exception as e:
            logger.debug(f"SX1262 detection error: {e}")
            status.details = f"Detection error: {e}"

        return status

    def _detect_rtlsdr(self) -> ComponentStatus:
        """Detect RTL-SDR on USB"""
        status = ComponentStatus(
            component=HardwareComponent.RTL_SDR,
            detected=False
        )

        try:
            # Check lsusb for RTL-SDR devices
            result = subprocess.run(
                ['lsusb'],
                capture_output=True, text=True, timeout=5
            )

            for vid, pid in self.RTLSDR_USB_IDS:
                if f"{vid}:{pid}" in result.stdout.lower():
                    status.detected = True
                    status.device_path = f"USB {vid}:{pid}"
                    status.details = "RTL2832U detected"
                    break

            if not status.detected:
                status.details = "No RTL-SDR USB device found"

        except FileNotFoundError:
            status.details = "lsusb not available"
        except Exception as e:
            status.details = f"Detection error: {e}"

        return status

    def _detect_gps(self) -> ComponentStatus:
        """Detect GPS module on UART"""
        status = ComponentStatus(
            component=HardwareComponent.GPS_GNSS,
            detected=False
        )

        uart_path = Path(self.GPS_UART)
        if not uart_path.exists():
            status.details = "UART not available"
            return status

        status.device_path = self.GPS_UART

        try:
            # Check if gpsd is running and has a fix
            result = subprocess.run(
                ['gpspipe', '-w', '-n', '1'],
                capture_output=True, text=True, timeout=3
            )

            if result.returncode == 0 and 'TPV' in result.stdout:
                status.detected = True
                status.enabled = True
                status.details = "GPS active via gpsd"
            else:
                # Check if UART shows NMEA data
                # (Would need serial access - mark as potentially available)
                status.detected = True
                status.details = "UART available (gpsd not running)"

        except FileNotFoundError:
            # gpspipe not available, assume GPS present if UART exists
            status.detected = True
            status.details = "UART available"
        except subprocess.TimeoutExpired:
            status.detected = True
            status.details = "GPS timeout (no fix?)"
        except Exception as e:
            status.details = f"Detection error: {e}"

        return status

    def _detect_rtc(self) -> ComponentStatus:
        """Detect PCF85063A RTC on I2C"""
        status = ComponentStatus(
            component=HardwareComponent.RTC,
            detected=False
        )

        i2c_dev = Path(f"/dev/i2c-{self.RTC_I2C_BUS}")
        if not i2c_dev.exists():
            status.details = "I2C not enabled"
            return status

        try:
            # Check for RTC device in sysfs
            rtc_path = Path("/dev/rtc0")
            if rtc_path.exists():
                status.detected = True
                status.device_path = str(rtc_path)
                status.details = "RTC available"

            # Also check i2cdetect for PCF85063A
            result = subprocess.run(
                ['i2cdetect', '-y', str(self.RTC_I2C_BUS)],
                capture_output=True, text=True, timeout=5
            )

            if f"{self.RTC_I2C_ADDR:02x}" in result.stdout or "51" in result.stdout:
                status.detected = True
                status.details = "PCF85063A at 0x51"

        except FileNotFoundError:
            status.details = "i2cdetect not available"
        except Exception as e:
            status.details = f"Detection error: {e}"

        return status

    def _detect_usb_hub(self) -> ComponentStatus:
        """Detect internal USB hub"""
        status = ComponentStatus(
            component=HardwareComponent.USB_HUB,
            detected=False
        )

        try:
            result = subprocess.run(
                ['lsusb', '-t'],
                capture_output=True, text=True, timeout=5
            )

            # Look for hub entries
            if 'Hub' in result.stdout:
                status.detected = True
                status.details = "USB hub detected"

        except Exception as e:
            status.details = f"Detection error: {e}"

        return status

    def _detect_ethernet(self) -> ComponentStatus:
        """Detect RJ45 Gigabit Ethernet"""
        status = ComponentStatus(
            component=HardwareComponent.ETHERNET,
            detected=False
        )

        # Check for eth0 or end0 (newer naming)
        for iface in ['eth0', 'end0', 'enp0s3']:
            iface_path = Path(f"/sys/class/net/{iface}")
            if iface_path.exists():
                status.detected = True
                status.device_path = iface

                # Check link status
                try:
                    carrier = (iface_path / "carrier").read_text().strip()
                    speed = (iface_path / "speed").read_text().strip()
                    status.enabled = carrier == "1"
                    status.details = f"{speed}Mbps" if status.enabled else "No link"
                except Exception:
                    status.details = "Interface available"
                break

        if not status.detected:
            status.details = "No Ethernet interface"

        return status

    def set_power_state(self, lora: bool = True, sdr: bool = True,
                        gps: bool = True, usb: bool = True) -> bool:
        """
        Control power to hardware subsystems via GPIO.

        Note: GPIO pins are active LOW on AIO V2.

        Args:
            lora: Enable LoRa module
            sdr: Enable RTL-SDR
            gps: Enable GPS
            usb: Enable USB hub

        Returns:
            True if successful
        """
        if not self._gpio_available:
            logger.warning("GPIO not available for power control")
            return False

        try:
            gpio_states = [
                (self.GPIO_LORA, not lora),  # Active low
                (self.GPIO_SDR, not sdr),
                (self.GPIO_GPS, not gps),
                (self.GPIO_USB, not usb),
            ]

            for gpio, state in gpio_states:
                self._set_gpio(gpio, state)

            logger.info(f"Power state: LoRa={lora}, SDR={sdr}, GPS={gps}, USB={usb}")
            return True

        except Exception as e:
            logger.error(f"Failed to set power state: {e}")
            return False

    def _set_gpio(self, pin: int, value: bool):
        """Set GPIO pin value via sysfs"""
        gpio_path = Path(f"/sys/class/gpio/gpio{pin}")

        # Export if needed
        if not gpio_path.exists():
            Path("/sys/class/gpio/export").write_text(str(pin))

        # Set direction
        (gpio_path / "direction").write_text("out")

        # Set value
        (gpio_path / "value").write_text("1" if value else "0")

    def generate_meshtasticd_config(self) -> str:
        """
        Generate meshtasticd config.yaml for SX1262 on SPI.

        Returns YAML configuration string.
        """
        config = f"""# MeshForge Auto-Generated Config for uConsole AIO V2
# SX1262 LoRa Module on SPI

Lora:
  Module: sx1262
  CS: {self.SX1262_SPI_CS}
  IRQ: 22
  Busy: 23
  Reset: 24
  # DIO2 controls RF switch
  DIO2_AS_RF_SWITCH: true

  # SX1262 on AIO V2 supports 860-960MHz
  # Use appropriate frequency for your region
  # Region: US  # 902-928MHz
  # Region: EU  # 863-870MHz

Webserver:
  Port: 4403

GPS:
  # GPS via UART if gpsd not running
  # SerialPath: {self.GPS_UART}
  # If gpsd is running, meshtasticd will use it automatically
"""
        return config

    def get_intercept_config(self) -> Dict:
        """
        Generate Intercept SIGINT configuration for the RTL-SDR.

        Returns dict suitable for Intercept config.
        """
        return {
            'rtl_sdr': {
                'device_index': 0,
                'sample_rate': 2_400_000,
                'gain': 'auto',
                'bias_tee': False,  # Can enable for active antennas
            },
            'frequencies': {
                'pager': [152.480, 152.840, 157.740],  # POCSAG
                'aircraft': [1090.0],  # ADS-B
                'ism_433': [433.92],  # 433MHz sensors
                'meshtastic': [906.875],  # US default
            }
        }


def detect_uconsole() -> Optional[UConsoleProfile]:
    """
    Convenience function to detect uConsole hardware.

    Returns UConsoleProfile if detected, None otherwise.
    """
    detector = UConsoleDetector()
    profile = detector.detect()

    if profile.detected:
        logger.info(f"uConsole AIO V2 detected: {profile.compute_module}")
        return profile

    return None


def get_hardware_summary() -> str:
    """Get human-readable hardware summary"""
    detector = UConsoleDetector()
    profile = detector.detect()
    return profile.summary()
