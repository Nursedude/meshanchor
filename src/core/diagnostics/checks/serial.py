"""
Serial port diagnostic checks.

Checks for serial devices and user permissions.
"""

import os
import time
import logging
from pathlib import Path
from typing import List

from ..models import CheckResult, CheckStatus, CheckCategory

logger = logging.getLogger(__name__)


def find_serial_devices() -> List[str]:
    """Find Meshtastic-compatible serial devices."""
    devices = []
    dev_path = Path('/dev')

    # Common patterns
    patterns = ['ttyACM*', 'ttyUSB*']

    for pattern in patterns:
        devices.extend([str(d) for d in dev_path.glob(pattern)])

    return devices


def check_serial_ports() -> CheckResult:
    """Check for available serial ports."""
    start = time.time()
    devices = find_serial_devices()
    duration = (time.time() - start) * 1000

    if devices:
        return CheckResult(
            name="Serial ports",
            category=CheckCategory.SERIAL,
            status=CheckStatus.PASS,
            message=f"Found: {', '.join(devices)}",
            details={"devices": devices},
            duration_ms=duration
        )
    else:
        return CheckResult(
            name="Serial ports",
            category=CheckCategory.SERIAL,
            status=CheckStatus.WARN,
            message="No Meshtastic devices found",
            fix_hint="Connect device via USB",
            duration_ms=duration
        )


def check_dialout_group() -> CheckResult:
    """Check if user is in dialout group."""
    start = time.time()
    try:
        import grp
        username = os.environ.get('SUDO_USER', os.environ.get('USER', 'root'))
        groups = [g.gr_name for g in grp.getgrall() if username in g.gr_mem]

        # Also check primary group
        try:
            import pwd
            user_info = pwd.getpwnam(username)
            primary_group = grp.getgrgid(user_info.pw_gid).gr_name
            groups.append(primary_group)
        except Exception:
            pass

        duration = (time.time() - start) * 1000

        if 'dialout' in groups:
            return CheckResult(
                name="Dialout group",
                category=CheckCategory.SERIAL,
                status=CheckStatus.PASS,
                message=f"User {username} in dialout group",
                duration_ms=duration
            )
        else:
            return CheckResult(
                name="Dialout group",
                category=CheckCategory.SERIAL,
                status=CheckStatus.WARN,
                message=f"User {username} not in dialout group",
                fix_hint=f"sudo usermod -a -G dialout {username} (then logout/login)",
                duration_ms=duration
            )
    except Exception as e:
        return CheckResult(
            name="Dialout group",
            category=CheckCategory.SERIAL,
            status=CheckStatus.SKIP,
            message=str(e),
            duration_ms=(time.time() - start) * 1000
        )
