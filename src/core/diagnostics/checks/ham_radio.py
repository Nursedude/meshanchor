"""
HAM Radio diagnostic checks.

Checks for callsign configuration.
"""

import os
import time
import logging
from pathlib import Path

from ..models import CheckResult, CheckStatus, CheckCategory
from utils.safe_import import safe_import

# Module-level safe imports
_get_real_user_home, _HAS_PATHS = safe_import('utils.paths', 'get_real_user_home')

logger = logging.getLogger(__name__)


def _resolve_user_home() -> Path:
    """Resolve user home directory with sudo compatibility."""
    if _HAS_PATHS:
        return _get_real_user_home()
    # Fallback for when utils.paths is not in Python path
    sudo_user = os.environ.get('SUDO_USER', '')
    if sudo_user and sudo_user != 'root' and '/' not in sudo_user and '..' not in sudo_user:
        return Path(f'/home/{sudo_user}')
    return Path.home()


def check_callsign() -> CheckResult:
    """Check if HAM callsign is configured."""
    start = time.time()
    callsign = os.environ.get('CALLSIGN', os.environ.get('HAM_CALLSIGN', ''))
    duration = (time.time() - start) * 1000

    if callsign:
        return CheckResult(
            name="Callsign",
            category=CheckCategory.HAM_RADIO,
            status=CheckStatus.PASS,
            message=callsign,
            details={"callsign": callsign},
            duration_ms=duration
        )
    else:
        # Check NomadNet config
        nomadnet_config = _resolve_user_home() / '.nomadnetwork' / 'config'
        if nomadnet_config.exists():
            try:
                content = nomadnet_config.read_text()
                if 'display_name' in content:
                    return CheckResult(
                        name="Callsign",
                        category=CheckCategory.HAM_RADIO,
                        status=CheckStatus.PASS,
                        message="Set in NomadNet config",
                        duration_ms=duration
                    )
            except Exception:
                pass

        return CheckResult(
            name="Callsign",
            category=CheckCategory.HAM_RADIO,
            status=CheckStatus.SKIP,
            message="Not configured (optional)",
            fix_hint="Set CALLSIGN environment variable",
            duration_ms=duration
        )
