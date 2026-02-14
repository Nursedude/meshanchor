"""
Service diagnostic checks.

Checks for systemd services and running processes.
"""

import subprocess
import time
import logging
from typing import List

from ..models import CheckResult, CheckStatus, CheckCategory
from utils.safe_import import safe_import

# Module-level safe imports — SINGLE SOURCE OF TRUTH
_check_service_status, _SvcState, _HAS_SERVICE_CHECK = safe_import(
    'utils.service_check', 'check_service', 'ServiceState'
)

logger = logging.getLogger(__name__)


def check_service(service: str, display_name: str) -> CheckResult:
    """Check if a systemd service is running."""
    start = time.time()

    # Use centralized service checker if available (SINGLE SOURCE OF TRUTH)
    if _HAS_SERVICE_CHECK and _check_service_status is not None:
        try:
            status = _check_service_status(service)
            duration = (time.time() - start) * 1000

            if status.available:
                return CheckResult(
                    name=f"{display_name}",
                    category=CheckCategory.SERVICES,
                    status=CheckStatus.PASS,
                    message="Service running",
                    details={"detection_method": status.detection_method},
                    duration_ms=duration
                )
            elif status.state == _SvcState.NOT_INSTALLED:
                return CheckResult(
                    name=f"{display_name}",
                    category=CheckCategory.SERVICES,
                    status=CheckStatus.SKIP,
                    message="Service not installed",
                    fix_hint=status.fix_hint,
                    duration_ms=duration
                )
            else:
                return CheckResult(
                    name=f"{display_name}",
                    category=CheckCategory.SERVICES,
                    status=CheckStatus.FAIL,
                    message=status.message,
                    fix_hint=status.fix_hint,
                    details={"detection_method": status.detection_method},
                    duration_ms=duration
                )
        except Exception as e:
            logger.warning(f"Centralized service check failed, falling back: {e}")
            # Fall through to direct systemctl check

    # Fallback: direct systemctl check (for standalone use or import failure)
    try:
        result = subprocess.run(
            ['systemctl', 'is-active', service],
            capture_output=True, text=True, timeout=5
        )
        status_str = result.stdout.strip()
        duration = (time.time() - start) * 1000

        if status_str == 'active':
            return CheckResult(
                name=f"{display_name}",
                category=CheckCategory.SERVICES,
                status=CheckStatus.PASS,
                message="Service running",
                duration_ms=duration
            )
        else:
            return CheckResult(
                name=f"{display_name}",
                category=CheckCategory.SERVICES,
                status=CheckStatus.FAIL,
                message=f"Service {status_str}",
                fix_hint=f"sudo systemctl start {service}",
                duration_ms=duration
            )
    except FileNotFoundError:
        return CheckResult(
            name=f"{display_name}",
            category=CheckCategory.SERVICES,
            status=CheckStatus.SKIP,
            message="systemctl not found",
            duration_ms=(time.time() - start) * 1000
        )
    except Exception as e:
        return CheckResult(
            name=f"{display_name}",
            category=CheckCategory.SERVICES,
            status=CheckStatus.FAIL,
            message=str(e),
            duration_ms=(time.time() - start) * 1000
        )


def check_process(process: str, display_name: str) -> CheckResult:
    """Check if a process is running."""
    start = time.time()
    try:
        result = subprocess.run(
            ['pgrep', '-x', process],
            capture_output=True, text=True, timeout=5
        )
        duration = (time.time() - start) * 1000

        if result.returncode == 0:
            pids = result.stdout.strip().split('\n')
            return CheckResult(
                name=f"{display_name}",
                category=CheckCategory.SERVICES,
                status=CheckStatus.PASS,
                message=f"Running (PID: {pids[0]})",
                details={"pids": pids},
                duration_ms=duration
            )
        else:
            return CheckResult(
                name=f"{display_name}",
                category=CheckCategory.SERVICES,
                status=CheckStatus.WARN,
                message="Not running",
                fix_hint=f"Start {process} if needed",
                duration_ms=duration
            )
    except Exception as e:
        return CheckResult(
            name=f"{display_name}",
            category=CheckCategory.SERVICES,
            status=CheckStatus.FAIL,
            message=str(e),
            duration_ms=(time.time() - start) * 1000
        )


def check_service_logs(service: str) -> CheckResult:
    """Check recent service logs for errors."""
    start = time.time()
    try:
        result = subprocess.run(
            ['journalctl', '-u', service, '--since', '1 hour ago', '-p', 'err', '--no-pager', '-q'],
            capture_output=True, text=True, timeout=10
        )
        duration = (time.time() - start) * 1000

        lines = result.stdout.strip().split('\n') if result.stdout.strip() else []
        error_count = len(lines)

        if error_count == 0:
            return CheckResult(
                name=f"{service} logs",
                category=CheckCategory.LOGS,
                status=CheckStatus.PASS,
                message="No errors in last hour",
                duration_ms=duration
            )
        elif error_count < 5:
            return CheckResult(
                name=f"{service} logs",
                category=CheckCategory.LOGS,
                status=CheckStatus.WARN,
                message=f"{error_count} error(s) in last hour",
                details={"recent_errors": lines[:3]},
                duration_ms=duration
            )
        else:
            return CheckResult(
                name=f"{service} logs",
                category=CheckCategory.LOGS,
                status=CheckStatus.FAIL,
                message=f"{error_count} errors in last hour",
                fix_hint=f"Check: journalctl -u {service} -f",
                details={"recent_errors": lines[:5]},
                duration_ms=duration
            )
    except Exception as e:
        return CheckResult(
            name=f"{service} logs",
            category=CheckCategory.LOGS,
            status=CheckStatus.SKIP,
            message=str(e),
            duration_ms=(time.time() - start) * 1000
        )
