"""iptables port isolation for meshtasticd.

MeshForge owns the browser: blocks external access to meshtasticd's
web server (port 9443) so users go through MeshForge's multiplexed
proxy at port 5000.

Extracted from service_check.py for file size compliance (CLAUDE.md #6).
"""

import logging
import shutil
import subprocess
from pathlib import Path
from typing import Tuple

from utils.service_check import _sudo_cmd

logger = logging.getLogger(__name__)


def lock_port_external(port: int = 9443, timeout: int = 10) -> Tuple[bool, str]:
    """Block external access to a port, allowing only localhost.

    Used to prevent users from accessing meshtasticd's web server directly
    at port 9443.  MeshForge serves the web client at port 5000/mesh/
    with multiplexed API proxying and phantom node filtering.

    This adds an iptables INPUT rule that rejects non-localhost traffic
    to the specified port.  The rule is idempotent — calling multiple
    times won't create duplicate rules.

    Args:
        port: TCP port to lock down (default: 9443 for meshtasticd)
        timeout: subprocess timeout in seconds

    Returns:
        Tuple of (success, message)
    """
    rule_args = ['-p', 'tcp', '--dport', str(port),
                 '!', '-s', '127.0.0.1', '-j', 'REJECT']

    try:
        # Check if rule already exists (idempotent)
        check = subprocess.run(
            _sudo_cmd(['iptables', '-C', 'INPUT'] + rule_args),
            capture_output=True, text=True, timeout=timeout
        )
        if check.returncode == 0:
            logger.info("iptables rule for port %d already in place", port)
            return True, f"Port {port} already locked to localhost"

        # Add the rule
        result = subprocess.run(
            _sudo_cmd(['iptables', '-A', 'INPUT'] + rule_args),
            capture_output=True, text=True, timeout=timeout
        )
        if result.returncode == 0:
            logger.info("Locked external access to port %d (localhost only)", port)
            return True, f"Port {port} locked — external access blocked"
        else:
            error = result.stderr.strip() or "iptables command failed"
            logger.error("Failed to lock port %d: %s", port, error)
            return False, f"iptables error: {error}"

    except FileNotFoundError:
        logger.warning("iptables not found — port lockdown unavailable")
        return False, "iptables not found (install iptables package)"
    except subprocess.TimeoutExpired:
        return False, "iptables command timed out"
    except Exception as e:
        logger.error("Port lockdown error: %s", e)
        return False, f"Error: {e}"


def unlock_port_external(port: int = 9443, timeout: int = 10) -> Tuple[bool, str]:
    """Remove the iptables rule blocking external access to a port.

    Args:
        port: TCP port to unlock (default: 9443)
        timeout: subprocess timeout in seconds

    Returns:
        Tuple of (success, message)
    """
    rule_args = ['-p', 'tcp', '--dport', str(port),
                 '!', '-s', '127.0.0.1', '-j', 'REJECT']

    try:
        result = subprocess.run(
            _sudo_cmd(['iptables', '-D', 'INPUT'] + rule_args),
            capture_output=True, text=True, timeout=timeout
        )
        if result.returncode == 0:
            logger.info("Unlocked external access to port %d", port)
            return True, f"Port {port} unlocked — external access restored"
        else:
            # Rule may not exist — that's fine
            return True, f"Port {port} was already unlocked"

    except FileNotFoundError:
        return False, "iptables not found"
    except subprocess.TimeoutExpired:
        return False, "iptables command timed out"
    except Exception as e:
        return False, f"Error: {e}"


def check_port_locked(port: int = 9443, timeout: int = 10) -> bool:
    """Check if the iptables rule blocking external access exists.

    Args:
        port: TCP port to check (default: 9443)
        timeout: subprocess timeout in seconds

    Returns:
        True if the port is locked to localhost, False otherwise.
    """
    rule_args = ['-p', 'tcp', '--dport', str(port),
                 '!', '-s', '127.0.0.1', '-j', 'REJECT']
    try:
        result = subprocess.run(
            _sudo_cmd(['iptables', '-C', 'INPUT'] + rule_args),
            capture_output=True, text=True, timeout=timeout
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired, Exception):
        return False


def persist_iptables(timeout: int = 30) -> Tuple[bool, str]:
    """Save current iptables rules so they survive reboot.

    Tries netfilter-persistent first, then falls back to iptables-save
    to /etc/iptables/rules.v4.

    Returns:
        Tuple of (success, message)
    """
    # Method 1: netfilter-persistent (Debian/Ubuntu with iptables-persistent)
    try:
        result = subprocess.run(
            _sudo_cmd(['netfilter-persistent', 'save']),
            capture_output=True, text=True, timeout=timeout
        )
        if result.returncode == 0:
            logger.info("iptables rules saved via netfilter-persistent")
            return True, "Rules saved (netfilter-persistent)"
    except FileNotFoundError:
        pass  # Not installed, try fallback
    except subprocess.TimeoutExpired:
        return False, "netfilter-persistent save timed out"

    # Method 2: Manual iptables-save to rules.v4
    if not shutil.which('iptables-save'):
        return False, (
            "No persistence tool found.\n"
            "Install: sudo apt install iptables-persistent"
        )

    try:
        rules_dir = Path('/etc/iptables')
        rules_dir.mkdir(parents=True, exist_ok=True)
        rules_file = rules_dir / 'rules.v4'

        save_result = subprocess.run(
            _sudo_cmd(['iptables-save']),
            capture_output=True, text=True, timeout=timeout
        )
        if save_result.returncode != 0:
            return False, f"iptables-save failed: {save_result.stderr.strip()}"

        rules_file.write_text(save_result.stdout)
        logger.info("iptables rules saved to %s", rules_file)
        return True, f"Rules saved to {rules_file}"

    except subprocess.TimeoutExpired:
        return False, "iptables-save timed out"
    except OSError as e:
        return False, f"Failed to write rules file: {e}"
    except Exception as e:
        logger.error("persist_iptables error: %s", e)
        return False, f"Error: {e}"
