"""
HAM Radio diagnostic checks.

Checks for callsign configuration.
"""

import os
import time
import logging
from pathlib import Path

from ..models import CheckResult, CheckStatus, CheckCategory

logger = logging.getLogger(__name__)

# Import centralized path utility for sudo compatibility
try:
    from utils.paths import get_real_user_home
    _get_real_user_home = get_real_user_home
except ImportError:
    def _get_real_user_home() -> Path:
        """Fallback for when utils.paths is not in Python path."""
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
        nomadnet_config = _get_real_user_home() / '.nomadnetwork' / 'config'
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
