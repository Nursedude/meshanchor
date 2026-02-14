"""
RNS (Reticulum Network Stack) diagnostic checks.

Checks for RNS installation, configuration, and Meshtastic interface.
"""

import os
import socket
import time
import logging
from pathlib import Path

from ..models import CheckResult, CheckStatus, CheckCategory
from utils.safe_import import safe_import

# Import centralized path utility - SINGLE SOURCE OF TRUTH for all paths
# See: utils/paths.py (ReticulumPaths)
# NO FALLBACK: the old fallback was WRONG - it skipped /etc/reticulum
# and XDG paths entirely, going directly to ~/.reticulum (Issue #25+)
from utils.paths import ReticulumPaths

# Module-level safe imports
_rns_mod, _HAS_RNS = safe_import('RNS')

logger = logging.getLogger(__name__)


def check_rns_installed() -> CheckResult:
    """Check if RNS is installed."""
    start = time.time()
    duration = (time.time() - start) * 1000

    if _HAS_RNS:
        return CheckResult(
            name="RNS library",
            category=CheckCategory.RNS,
            status=CheckStatus.PASS,
            message="Installed",
            duration_ms=duration
        )
    else:
        return CheckResult(
            name="RNS library",
            category=CheckCategory.RNS,
            status=CheckStatus.FAIL,
            message="Not installed",
            fix_hint="pipx install rns",
            duration_ms=duration
        )


def check_rns_config() -> CheckResult:
    """Check RNS configuration file."""
    start = time.time()
    config_path = ReticulumPaths.get_config_file()

    if config_path.exists():
        try:
            content = config_path.read_text()
            has_interface = '[interface' in content.lower() or '[[' in content
            duration = (time.time() - start) * 1000

            if has_interface:
                return CheckResult(
                    name="RNS config",
                    category=CheckCategory.RNS,
                    status=CheckStatus.PASS,
                    message=f"Found at {config_path}",
                    details={"path": str(config_path)},
                    duration_ms=duration
                )
            else:
                return CheckResult(
                    name="RNS config",
                    category=CheckCategory.RNS,
                    status=CheckStatus.WARN,
                    message="No interfaces configured",
                    fix_hint="Add interface to ~/.reticulum/config",
                    duration_ms=duration
                )
        except Exception as e:
            return CheckResult(
                name="RNS config",
                category=CheckCategory.RNS,
                status=CheckStatus.FAIL,
                message=f"Read error: {e}",
                duration_ms=(time.time() - start) * 1000
            )
    else:
        return CheckResult(
            name="RNS config",
            category=CheckCategory.RNS,
            status=CheckStatus.WARN,
            message="Not found (will be created on first run)",
            fix_hint="Run rnsd once to generate config",
            duration_ms=(time.time() - start) * 1000
        )


def check_rns_port() -> CheckResult:
    """Check if RNS AutoInterface port (29716) is available."""
    start = time.time()
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(('0.0.0.0', 29716))
        sock.close()
        duration = (time.time() - start) * 1000
        return CheckResult(
            name="RNS AutoInterface port",
            category=CheckCategory.RNS,
            status=CheckStatus.PASS,
            message="Port 29716 available",
            duration_ms=duration
        )
    except OSError as e:
        if e.errno == 98:  # Address already in use
            return CheckResult(
                name="RNS AutoInterface port",
                category=CheckCategory.RNS,
                status=CheckStatus.PASS,
                message="Port in use (rnsd running)",
                duration_ms=(time.time() - start) * 1000
            )
        return CheckResult(
            name="RNS AutoInterface port",
            category=CheckCategory.RNS,
            status=CheckStatus.FAIL,
            message=str(e),
            duration_ms=(time.time() - start) * 1000
        )


def check_rns_storage_permissions() -> CheckResult:
    """Check that RNS storage directories exist with correct permissions.

    RNS Identity.persist_job() requires the 'ratchets' subdirectory under
    the storage directory. Transport jobs require 'cache/announces/'.
    If missing or not writable, rnsd crashes with PermissionError in a
    background thread.
    """
    start = time.time()

    # Only relevant for system-wide config
    etc_storage = Path('/etc/reticulum/storage')
    if not etc_storage.parent.exists():
        return CheckResult(
            name="RNS storage permissions",
            category=CheckCategory.RNS,
            status=CheckStatus.PASS,
            message="System-wide config not used (OK)",
            duration_ms=(time.time() - start) * 1000
        )

    # All subdirectories RNS needs to write to
    required_dirs = [
        (etc_storage, "storage/"),
        (etc_storage / 'ratchets', "storage/ratchets/"),
        (etc_storage / 'resources', "storage/resources/"),
        (etc_storage / 'cache', "storage/cache/"),
        (etc_storage / 'cache' / 'announces', "storage/cache/announces/"),
    ]
    issues = []

    for dir_path, label in required_dirs:
        if not dir_path.exists():
            issues.append(f"{label} directory missing")
        elif not os.access(str(dir_path), os.W_OK):
            issues.append(f"{label} not writable")

    duration = (time.time() - start) * 1000

    if issues:
        return CheckResult(
            name="RNS storage permissions",
            category=CheckCategory.RNS,
            status=CheckStatus.FAIL,
            message="; ".join(issues),
            fix_hint=(
                "sudo mkdir -p /etc/reticulum/storage/{ratchets,resources,cache/announces} && "
                "sudo chown -R $(whoami) /etc/reticulum/storage/"
            ),
            duration_ms=duration
        )

    return CheckResult(
        name="RNS storage permissions",
        category=CheckCategory.RNS,
        status=CheckStatus.PASS,
        message="storage/, ratchets/, resources/, cache/announces/ directories OK",
        duration_ms=duration
    )


def check_meshtastic_interface_file() -> CheckResult:
    """Check for Meshtastic_Interface.py plugin in RNS interfaces directory.

    The plugin must be in the 'interfaces/' subdirectory of the RNS config dir
    (e.g., ~/.reticulum/interfaces/ or /etc/reticulum/interfaces/).
    Source: https://github.com/landandair/RNS_Over_Meshtastic
    """
    start = time.time()
    interface_file = ReticulumPaths.get_interfaces_dir() / 'Meshtastic_Interface.py'

    if interface_file.exists():
        return CheckResult(
            name="Meshtastic Interface Plugin",
            category=CheckCategory.RNS,
            status=CheckStatus.PASS,
            message=f"Installed at {interface_file}",
            details={"path": str(interface_file)},
            duration_ms=(time.time() - start) * 1000
        )
    else:
        return CheckResult(
            name="Meshtastic Interface Plugin",
            category=CheckCategory.RNS,
            status=CheckStatus.WARN,
            message="Not installed - required for RNS over Meshtastic bridging",
            fix_hint=(
                "Install from: https://github.com/landandair/RNS_Over_Meshtastic\n"
                f"Copy Meshtastic_Interface.py to: {ReticulumPaths.get_interfaces_dir()}/"
            ),
            duration_ms=(time.time() - start) * 1000
        )
