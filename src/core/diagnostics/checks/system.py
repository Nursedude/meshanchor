"""
System diagnostic checks.

Checks for Python version, packages, memory, disk, and CPU.
"""

import os
import sys
import time
import logging

from ..models import CheckResult, CheckStatus, CheckCategory
from utils.safe_import import safe_import

logger = logging.getLogger(__name__)


def check_python_version() -> CheckResult:
    """Check Python version."""
    start = time.time()
    version = sys.version_info
    version_str = f"{version.major}.{version.minor}.{version.micro}"
    duration = (time.time() - start) * 1000

    if version >= (3, 9):
        return CheckResult(
            name="Python version",
            category=CheckCategory.SYSTEM,
            status=CheckStatus.PASS,
            message=version_str,
            duration_ms=duration
        )
    elif version >= (3, 8):
        return CheckResult(
            name="Python version",
            category=CheckCategory.SYSTEM,
            status=CheckStatus.WARN,
            message=f"{version_str} (3.9+ recommended)",
            duration_ms=duration
        )
    else:
        return CheckResult(
            name="Python version",
            category=CheckCategory.SYSTEM,
            status=CheckStatus.FAIL,
            message=f"{version_str} (requires 3.8+)",
            fix_hint="Upgrade Python to 3.9+",
            duration_ms=duration
        )


def check_pip_packages() -> CheckResult:
    """Check required pip packages."""
    start = time.time()
    required = {
        'meshtastic': 'meshtastic',
        'rns': 'RNS',
        'lxmf': 'LXMF',
    }
    installed = []
    missing = []

    for display_name, module_name in required.items():
        _mod, _available = safe_import(module_name)
        if _available:
            installed.append(display_name)
        else:
            missing.append(display_name)

    duration = (time.time() - start) * 1000

    if not missing:
        return CheckResult(
            name="Required packages",
            category=CheckCategory.SYSTEM,
            status=CheckStatus.PASS,
            message=f"All installed ({len(installed)})",
            details={"installed": installed},
            duration_ms=duration
        )
    else:
        return CheckResult(
            name="Required packages",
            category=CheckCategory.SYSTEM,
            status=CheckStatus.WARN,
            message=f"Missing: {', '.join(missing)}",
            fix_hint=f"pip3 install {' '.join(missing)}",
            details={"installed": installed, "missing": missing},
            duration_ms=duration
        )


def check_memory() -> CheckResult:
    """Check available memory."""
    start = time.time()
    try:
        with open('/proc/meminfo') as f:
            lines = f.readlines()

        mem_info = {}
        for line in lines:
            parts = line.split()
            if len(parts) >= 2:
                key = parts[0].rstrip(':')
                value = int(parts[1])
                mem_info[key] = value

        total_mb = mem_info.get('MemTotal', 0) / 1024
        available_mb = mem_info.get('MemAvailable', 0) / 1024
        percent_free = (available_mb / total_mb * 100) if total_mb > 0 else 0
        duration = (time.time() - start) * 1000

        if percent_free < 10:
            return CheckResult(
                name="Memory",
                category=CheckCategory.SYSTEM,
                status=CheckStatus.FAIL,
                message=f"{available_mb:.0f}MB free ({percent_free:.0f}%)",
                fix_hint="Free up memory or add swap",
                details={"total_mb": total_mb, "available_mb": available_mb},
                duration_ms=duration
            )
        elif percent_free < 25:
            return CheckResult(
                name="Memory",
                category=CheckCategory.SYSTEM,
                status=CheckStatus.WARN,
                message=f"{available_mb:.0f}MB free ({percent_free:.0f}%)",
                details={"total_mb": total_mb, "available_mb": available_mb},
                duration_ms=duration
            )
        else:
            return CheckResult(
                name="Memory",
                category=CheckCategory.SYSTEM,
                status=CheckStatus.PASS,
                message=f"{available_mb:.0f}MB free ({percent_free:.0f}%)",
                details={"total_mb": total_mb, "available_mb": available_mb},
                duration_ms=duration
            )
    except Exception as e:
        return CheckResult(
            name="Memory",
            category=CheckCategory.SYSTEM,
            status=CheckStatus.FAIL,
            message=str(e),
            duration_ms=(time.time() - start) * 1000
        )


def check_disk_space() -> CheckResult:
    """Check available disk space."""
    start = time.time()
    try:
        stat = os.statvfs('/')
        total_gb = (stat.f_blocks * stat.f_frsize) / (1024**3)
        free_gb = (stat.f_bavail * stat.f_frsize) / (1024**3)
        duration = (time.time() - start) * 1000

        if free_gb < 1:
            return CheckResult(
                name="Disk space",
                category=CheckCategory.SYSTEM,
                status=CheckStatus.FAIL,
                message=f"{free_gb:.1f}GB free (CRITICAL)",
                fix_hint="Free up disk space",
                details={"total_gb": total_gb, "free_gb": free_gb},
                duration_ms=duration
            )
        elif free_gb < 5:
            return CheckResult(
                name="Disk space",
                category=CheckCategory.SYSTEM,
                status=CheckStatus.WARN,
                message=f"{free_gb:.1f}GB free",
                details={"total_gb": total_gb, "free_gb": free_gb},
                duration_ms=duration
            )
        else:
            return CheckResult(
                name="Disk space",
                category=CheckCategory.SYSTEM,
                status=CheckStatus.PASS,
                message=f"{free_gb:.1f}GB free",
                details={"total_gb": total_gb, "free_gb": free_gb},
                duration_ms=duration
            )
    except Exception as e:
        return CheckResult(
            name="Disk space",
            category=CheckCategory.SYSTEM,
            status=CheckStatus.FAIL,
            message=str(e),
            duration_ms=(time.time() - start) * 1000
        )


def check_cpu_load() -> CheckResult:
    """Check CPU load average."""
    start = time.time()
    try:
        load_1, load_5, load_15 = os.getloadavg()
        cpu_count = os.cpu_count() or 1
        load_percent = (load_1 / cpu_count) * 100
        duration = (time.time() - start) * 1000

        if load_percent > 100:
            return CheckResult(
                name="CPU load",
                category=CheckCategory.SYSTEM,
                status=CheckStatus.WARN,
                message=f"{load_1:.2f} ({load_percent:.0f}% of {cpu_count} cores)",
                details={"load_1": load_1, "load_5": load_5, "load_15": load_15},
                duration_ms=duration
            )
        else:
            return CheckResult(
                name="CPU load",
                category=CheckCategory.SYSTEM,
                status=CheckStatus.PASS,
                message=f"{load_1:.2f} ({load_percent:.0f}%)",
                details={"load_1": load_1, "load_5": load_5, "load_15": load_15},
                duration_ms=duration
            )
    except Exception as e:
        return CheckResult(
            name="CPU load",
            category=CheckCategory.SYSTEM,
            status=CheckStatus.SKIP,
            message=str(e),
            duration_ms=(time.time() - start) * 1000
        )
