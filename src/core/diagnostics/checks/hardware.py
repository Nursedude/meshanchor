"""
Hardware diagnostic checks.

Checks for SPI, I2C, temperature, and SDR devices.
"""

import shutil
import subprocess
import time
import logging
from pathlib import Path

from ..models import CheckResult, CheckStatus, CheckCategory

logger = logging.getLogger(__name__)


def check_spi() -> CheckResult:
    """Check if SPI is enabled."""
    start = time.time()
    spi_devices = list(Path('/dev').glob('spidev*'))
    duration = (time.time() - start) * 1000

    if spi_devices:
        return CheckResult(
            name="SPI interface",
            category=CheckCategory.HARDWARE,
            status=CheckStatus.PASS,
            message=f"Enabled ({len(spi_devices)} device(s))",
            details={"devices": [str(d) for d in spi_devices]},
            duration_ms=duration
        )
    else:
        return CheckResult(
            name="SPI interface",
            category=CheckCategory.HARDWARE,
            status=CheckStatus.SKIP,
            message="Not enabled or not a Pi",
            fix_hint="Enable SPI in raspi-config if needed",
            duration_ms=duration
        )


def check_i2c() -> CheckResult:
    """Check if I2C is enabled."""
    start = time.time()
    i2c_devices = list(Path('/dev').glob('i2c-*'))
    duration = (time.time() - start) * 1000

    if i2c_devices:
        return CheckResult(
            name="I2C interface",
            category=CheckCategory.HARDWARE,
            status=CheckStatus.PASS,
            message=f"Enabled ({len(i2c_devices)} bus(es))",
            details={"devices": [str(d) for d in i2c_devices]},
            duration_ms=duration
        )
    else:
        return CheckResult(
            name="I2C interface",
            category=CheckCategory.HARDWARE,
            status=CheckStatus.SKIP,
            message="Not enabled or not a Pi",
            fix_hint="Enable I2C in raspi-config if needed",
            duration_ms=duration
        )


def check_temperature() -> CheckResult:
    """Check CPU temperature (Raspberry Pi)."""
    start = time.time()
    temp_file = Path('/sys/class/thermal/thermal_zone0/temp')

    if temp_file.exists():
        try:
            temp_raw = temp_file.read_text().strip()
            temp_c = int(temp_raw) / 1000
            duration = (time.time() - start) * 1000

            if temp_c >= 80:
                return CheckResult(
                    name="CPU temperature",
                    category=CheckCategory.HARDWARE,
                    status=CheckStatus.FAIL,
                    message=f"{temp_c:.1f}°C (CRITICAL)",
                    fix_hint="Add cooling or reduce load",
                    details={"temp_c": temp_c},
                    duration_ms=duration
                )
            elif temp_c >= 70:
                return CheckResult(
                    name="CPU temperature",
                    category=CheckCategory.HARDWARE,
                    status=CheckStatus.WARN,
                    message=f"{temp_c:.1f}°C (warm)",
                    details={"temp_c": temp_c},
                    duration_ms=duration
                )
            else:
                return CheckResult(
                    name="CPU temperature",
                    category=CheckCategory.HARDWARE,
                    status=CheckStatus.PASS,
                    message=f"{temp_c:.1f}°C",
                    details={"temp_c": temp_c},
                    duration_ms=duration
                )
        except Exception as e:
            return CheckResult(
                name="CPU temperature",
                category=CheckCategory.HARDWARE,
                status=CheckStatus.FAIL,
                message=str(e),
                duration_ms=(time.time() - start) * 1000
            )
    else:
        return CheckResult(
            name="CPU temperature",
            category=CheckCategory.HARDWARE,
            status=CheckStatus.SKIP,
            message="Not a Raspberry Pi",
            duration_ms=(time.time() - start) * 1000
        )


def check_sdr() -> CheckResult:
    """Check for SDR devices."""
    start = time.time()
    rtl_path = shutil.which('rtl_test')

    if rtl_path:
        try:
            result = subprocess.run(
                ['rtl_test', '-t'],
                capture_output=True, text=True, timeout=5
            )
            duration = (time.time() - start) * 1000

            if 'Found' in result.stderr or 'Found' in result.stdout:
                return CheckResult(
                    name="RTL-SDR",
                    category=CheckCategory.HARDWARE,
                    status=CheckStatus.PASS,
                    message="Device found",
                    duration_ms=duration
                )
        except subprocess.TimeoutExpired:
            pass
        except Exception:
            pass

    return CheckResult(
        name="RTL-SDR",
        category=CheckCategory.HARDWARE,
        status=CheckStatus.SKIP,
        message="Not installed or no device",
        duration_ms=(time.time() - start) * 1000
    )
