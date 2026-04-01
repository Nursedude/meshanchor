"""Hardware diagnostic rules for MeshAnchor Diagnostic Engine."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from utils.diagnostic_engine import DiagnosticEngine

from utils.diagnostic_engine import (
    Category,
    DiagnosticRule,
    check_no_serial_device,
    check_serial_device_exists,
    make_process_check,
)


def load_hardware_rules(engine: "DiagnosticEngine") -> None:
    """Load hardware diagnostic rules."""

    engine.add_rule(DiagnosticRule(
        name="serial_port_busy",
        pattern=r"(?i)(serial|tty|usb).*(busy|in use|locked|permission)",
        category=Category.HARDWARE,
        cause_template="Serial port is in use by another process or has permission issues",
        evidence_checks=[
            lambda ctx: check_serial_device_exists(),  # Verify device exists
            make_process_check("meshtasticd"),  # meshtasticd might be using it
        ],
        suggestions=[
            "Find process using port: sudo lsof /dev/ttyUSB0",
            "Kill blocking process or use different port",
            "Check permissions: ls -la /dev/ttyUSB*",
            "Add user to dialout group: sudo usermod -aG dialout $USER",
        ],
        confidence_base=0.85,
    ))

    engine.add_rule(DiagnosticRule(
        name="device_disconnected",
        pattern=r"(?i)(device|radio|hardware).*(disconnect|removed|not found|missing)",
        category=Category.HARDWARE,
        cause_template="Hardware device was disconnected or not detected",
        evidence_checks=[
            lambda ctx: check_no_serial_device(),  # Verify no serial devices
        ],
        suggestions=[
            "Check USB connection: lsusb",
            "Check dmesg for device events: dmesg | tail -20",
            "Try different USB port or cable",
            "Device may need power cycle",
        ],
        confidence_base=0.9,
    ))

    # ── Extended hardware rules ──

    engine.add_rule(DiagnosticRule(
        name="usb_power_insufficient",
        pattern=r"(?i)(usb|power).*(insufficient|undervolt|brownout|over.?current)",
        category=Category.HARDWARE,
        cause_template="USB port is not providing sufficient power to the device",
        suggestions=[
            "Use a powered USB hub",
            "Try a different USB port (rear ports often have more power)",
            "Use a shorter, higher-quality USB cable",
            "Check dmesg for USB power warnings: dmesg | grep -i 'over-current'",
        ],
        confidence_base=0.8,
    ))

    engine.add_rule(DiagnosticRule(
        name="gps_lock_lost",
        pattern=r"(?i)(gps|gnss|position|location).*(lost|no fix|no lock|timeout|unavailable)",
        category=Category.HARDWARE,
        cause_template="GPS receiver has lost satellite lock",
        suggestions=[
            "Ensure clear view of sky (GPS needs satellite visibility)",
            "Check GPS antenna connection",
            "Cold start may take 2-15 minutes for first fix",
            "Verify GPS is enabled: meshtastic --get position.gps_enabled",
        ],
        confidence_base=0.75,
    ))

    engine.add_rule(DiagnosticRule(
        name="radio_reset_detected",
        pattern=r"(?i)(radio|lora|sx127|sx126|chip).*(reset|reboot|reinit|watchdog)",
        category=Category.HARDWARE,
        cause_template="Radio chip has reset unexpectedly — possible power or hardware issue",
        suggestions=[
            "Check power supply stability",
            "Verify SPI/I2C connections to radio chip",
            "Check for overheating (feel the device)",
            "Update firmware — may be a known chip driver bug",
        ],
        confidence_base=0.8,
    ))

    engine.add_rule(DiagnosticRule(
        name="battery_low",
        pattern=r"(?i)(battery|batt|power).*(low|critical|<\s*2[0-9]%|dying|shutdown)",
        category=Category.HARDWARE,
        cause_template="Device battery is critically low",
        suggestions=[
            "Connect to power source immediately",
            "Enable power-saving mode to extend life",
            "Check charging circuit if connected but not charging",
            "Consider solar panel for remote deployments",
        ],
        confidence_base=0.9,
    ))

    engine.add_rule(DiagnosticRule(
        name="overheating",
        pattern=r"(?i)(temperature|thermal|heat|overheat).*(high|warning|critical|throttl)",
        category=Category.HARDWARE,
        cause_template="Device is overheating — may throttle or shut down",
        suggestions=[
            "Move device to shaded/ventilated location",
            "Reduce TX power to lower heat generation",
            "Check for direct sunlight exposure",
            "Add heatsink or ventilation to enclosure",
        ],
        confidence_base=0.85,
    ))

    engine.add_rule(DiagnosticRule(
        name="spi_bus_error",
        pattern=r"(?i)(spi|i2c|bus).*(error|timeout|nak|collision|stuck)",
        category=Category.HARDWARE,
        cause_template="Communication bus error between processor and peripheral",
        suggestions=[
            "Check wiring/connections to peripheral",
            "Verify bus clock speed is within spec",
            "Check for bus contention (multiple devices on same bus)",
            "Power cycle the device",
        ],
        confidence_base=0.8,
    ))

    engine.add_rule(DiagnosticRule(
        name="firmware_flash_failed",
        pattern=r"(?i)(firmware|flash|update|ota).*(failed|error|abort|corrupt|verify)",
        category=Category.HARDWARE,
        cause_template="Firmware update/flash operation failed",
        suggestions=[
            "Do NOT power off device — retry the flash",
            "Use wired connection (USB) instead of OTA for reliability",
            "Verify firmware file integrity (checksum)",
            "Try recovery/DFU mode if device is bricked",
        ],
        confidence_base=0.9,
    ))
