"""
Service Availability Utilities for MeshAnchor

Provides standardized service checking before connecting to external services.
Use these instead of assuming services are running.

ARCHITECTURE (Issue #17 redesign, Issue #20 completion):
    - For systemd services: Trust systemctl ONLY (single source of truth)
    - Port/process checks kept for utilities but NOT used in check_service()
    - "Unknown" state is better than wrong state from conflicting methods
    - Active services always trusted (no port fallback for transitional states)

Usage:
    from utils.service_check import check_port, check_service, ServiceStatus
    from utils.ports import MESHTASTICD_PORT

    # Quick port check (utility function)
    if check_port(MESHTASTICD_PORT):
        connect_to_meshtasticd()

    # Full service check - trusts systemctl for systemd services
    status = check_service('meshtasticd')
    if not status.available:
        show_error(status.message)
        show_fix(status.fix_hint)
"""

import contextlib
import os
import tempfile
import re
import socket
import subprocess
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple
from enum import Enum

from utils.boundary_timing import timed_boundary
from utils.ports import MESHTASTICD_PORT, MESHTASTICD_ALT_PORT, MQTT_PORT, RNS_SHARED_INSTANCE_PORT
from utils import tx_guard

logger = logging.getLogger(__name__)


def _sudo_cmd(cmd: List[str]) -> List[str]:
    """Prefix a command with 'sudo' when MeshAnchor is not running as root.

    Allows MeshAnchor to run as a normal user and only elevate for
    specific operations (systemctl, iptables, etc.).  When already root,
    returns the command unchanged.

    Args:
        cmd: Command and arguments, e.g. ['systemctl', 'restart', 'rnsd']

    Returns:
        The command, possibly prefixed with ['sudo'].
    """
    if os.geteuid() != 0:
        return ['sudo'] + cmd
    return cmd

# Public API - these are the functions/classes intended for external use
__all__ = [
    # Main entry points
    'check_service',        # Primary status checker (SINGLE SOURCE OF TRUTH)
    'clear_service_cache',  # Drop the TTL cache (post-mutation, tests)
    'require_service',      # Check with exception on failure
    'check_port',           # TCP port check (utility)
    'check_udp_port',       # UDP port check (utility)
    'check_rns_shared_instance',  # RNS shared instance check (domain socket + TCP + UDP)
    'get_rns_shared_instance_info',  # RNS shared instance diagnostics
    'get_udp_port_owner',   # UDP port owner lookup (process name + PID)
    'check_process_running', # Process check via pgrep (utility)
    'check_systemd_service', # Systemd status check
    # Service management
    'daemon_reload',             # Reload systemd daemon
    'enable_service',            # Enable service at boot
    'disable_service',           # Disable service at boot
    'start_service',             # Start a systemd service
    'stop_service',              # Stop a systemd service
    'restart_service',           # Restart a systemd service
    'apply_config_and_restart',  # Reload daemon + restart service
    # Systemd unit-state query primitives (role-engine port, 2026-07-18)
    'check_systemd_service',     # (is_running, is_enabled) tuple
    'is_service_unit_installed', # unit FILE exists on this box
    'is_service_masked',         # unit is masked
    'mask_service',              # mask a unit (one-rnsd-per-box invariant)
    # Privilege elevation & file I/O
    '_sudo_cmd',            # Prefix command with sudo when not root
    '_sudo_write',          # Write file content with privilege elevation
    # Data classes
    'ServiceStatus',        # Return type from check_service
    'ServiceState',         # Status enum (AVAILABLE, DEGRADED, FAILED, etc.)
    # Configuration
    'KNOWN_SERVICES',       # Service configuration dict
]


class ServiceState(Enum):
    """Service availability states."""
    AVAILABLE = "available"
    DEGRADED = "degraded"       # Running but with issues
    FAILED = "failed"           # Service crashed or failed to start
    NOT_RUNNING = "not_running"
    NOT_INSTALLED = "not_installed"
    UNKNOWN = "unknown"         # Cannot determine state


@dataclass
class ServiceStatus:
    """Result of a service availability check."""
    name: str
    available: bool
    state: ServiceState
    message: str
    fix_hint: str = ""
    port: Optional[int] = None
    # Additional context (Phase 2: separate service state from detection)
    detection_method: str = ""  # How was this determined

    def __bool__(self) -> bool:
        return self.available


# Known services and their configurations
# Port numbers imported from utils.ports for centralization
# NOTE: is_systemd=True means we ONLY trust systemctl for status
# `optional: True` marks services that are NOT load-bearing on a
# MeshAnchor (MeshCore-primary) NOC. They show on the dashboard for
# visibility but don't count toward the SLO denominator and don't
# degrade `overall_status` when down. The split:
#   - rnsd, mosquitto       → required (federation transport + MQTT broker)
#   - meshtasticd*, nomadnet, meshcore-radio → optional
# Rationale: MeshAnchor sister-projects MeshForge as a Meshtastic-primary
# fork; here Meshtastic-side daemons exist for compatibility but aren't
# required. `meshcore-radio` is the opt-in supervisor (PR #70 playbook);
# the bridge runs in-process by default.
KNOWN_SERVICES = {
    'meshtasticd': {
        'port': MESHTASTICD_PORT,
        'systemd_name': 'meshtasticd',
        'is_systemd': True,  # Trust systemctl only
        'optional': True,
        'description': 'Meshtastic daemon',
        'fix_hint': 'Start with: sudo systemctl start meshtasticd',
    },
    'rnsd': {
        'port': RNS_SHARED_INSTANCE_PORT,
        'port_type': 'unix_socket',  # RNS uses abstract domain sockets on Linux
        'systemd_name': 'rnsd',
        'is_systemd': True,  # rnsd runs as systemd service (install_noc.sh creates unit)
        'description': 'Reticulum Network Stack daemon',
        'fix_hint': 'Start with: sudo systemctl start rnsd',
    },
    'mosquitto': {
        'port': MQTT_PORT,
        'systemd_name': 'mosquitto',
        'is_systemd': True,
        'description': 'MQTT broker',
        'fix_hint': 'Start with: sudo systemctl start mosquitto',
    },
    'nomadnet': {
        'port': None,  # NomadNet uses RNS shared instance, no dedicated port
        'systemd_name': 'nomadnet',
        'is_systemd': False,  # NomadNet is a user-space app, NOT a systemd service
        'optional': True,
        'description': 'NomadNet mesh messaging client',
        'fix_hint': 'Start with: nomadnetwork (run as user, not root)',
    },
    'meshtasticd-alt': {
        'port': MESHTASTICD_ALT_PORT,
        'systemd_name': 'meshtasticd-alt',
        'is_systemd': True,
        'optional': True,
        'description': 'Meshtastic daemon (secondary/failover)',
        'fix_hint': 'Start with: sudo systemctl start meshtasticd-alt',
    },
    'meshcore-radio': {
        # Unix socket, not TCP/UDP. Set port=None so check_port skips it
        # and check_service falls through to systemd state.
        'port': None,
        'systemd_name': 'meshcore-radio',
        'is_systemd': True,
        'optional': True,
        'description': 'MeshCore radio supervisor (owns /dev/ttyMeshCore)',
        'fix_hint': 'Start with: sudo systemctl start meshcore-radio',
    },
}


# Port/process/socket detection extracted to _port_detection.py — re-exported for backward compat
from utils._port_detection import (  # noqa: F401, E402
    _detect_radio_hardware, check_port, check_udp_port, get_udp_port_owner,
    check_rns_shared_instance, _check_proc_net_unix, get_rns_shared_instance_info,
    check_process_running, check_process_with_pid, check_systemd_service,
)


# In-process TTL cache for `check_service` results — cuts the
# systemd shell-out load from dashboard polling.
#
# The fleet dashboard polls /fleet/slo every 5s, and each call iterates
# every KNOWN_SERVICES entry; a single browser drives ~144 systemctl
# shell-outs per minute. The 73-min soak captured ~16k boundary calls
# with zero anomalies, but the load is wasted work — service state
# rarely changes mid-second. A 6s TTL means each entry serves at most
# one cache hit between systemd reads (5s poll < 6s TTL ≤ 10s next
# poll), cutting the load roughly in half while keeping state changes
# visible within ~6s of the operator's actual systemctl restart.
#
# `_CACHE_TTL_S` is module-level so tests + callers wanting fresh data
# can override or call `clear_service_cache()`. The cache is global
# (single dict) — same `(name, port, host)` key produces the same
# answer regardless of caller.
_CACHE_TTL_S: float = 6.0
_service_cache: dict = {}
_service_cache_lock = __import__("threading").Lock()


def clear_service_cache() -> None:
    """Drop every cached result. Used by tests and after explicit
    service-mutation operations (start/stop/restart) so the next
    `check_service` call sees the fresh state immediately."""
    with _service_cache_lock:
        _service_cache.clear()


def check_service(name: str, port: Optional[int] = None, host: str = 'localhost',
                  *, use_cache: bool = True) -> ServiceStatus:
    """
    Check if a service is available and provide actionable feedback.

    SIMPLIFIED ARCHITECTURE (Issue #17):
        - For systemd services: ONLY trust systemctl (single source of truth)
        - No conflicting fallback methods (port check, pgrep)
        - "Unknown" is better than wrong state

    Args:
        name: Service name (e.g., 'meshtasticd', 'rnsd', 'mosquitto')
        port: Override port to check (uses known default if not specified)
        host: Host to check (default localhost)
        use_cache: When True (default), return a cached ServiceStatus
            up to ``_CACHE_TTL_S`` seconds old. The fleet dashboard
            relies on this; explicit operator actions (e.g. service
            menu start/stop) should pass ``use_cache=False`` so the
            UI sees the post-mutation state immediately.

    Returns:
        ServiceStatus with availability info and fix hints

    API Contract:
        - ALWAYS returns a ServiceStatus (never None)
        - ServiceStatus.available: bool indicating if service is ready
        - ServiceStatus.state: ServiceState enum (AVAILABLE, NOT_RUNNING, etc.)
        - ServiceStatus.detection_method: How status was determined
        - Known services: meshtasticd, rnsd, mosquitto, nomadnet
    """
    if use_cache:
        cache_key = (name, port, host)
        now = time.monotonic()
        with _service_cache_lock:
            entry = _service_cache.get(cache_key)
            if entry is not None and (now - entry[0]) < _CACHE_TTL_S:
                return entry[1]
    status = _check_service_uncached(name, port, host)
    if use_cache:
        with _service_cache_lock:
            _service_cache[cache_key] = (time.monotonic(), status)
    return status


def _check_service_uncached(name: str, port: Optional[int] = None,
                             host: str = 'localhost') -> ServiceStatus:
    """Cache-bypass implementation — the original `check_service` body.
    Always shells out to systemctl for systemd services."""
    config = KNOWN_SERVICES.get(name, {})
    check_port_num = port or config.get('port')
    systemd_name = config.get('systemd_name', name)
    description = config.get('description', name)
    fix_hint = config.get('fix_hint', f'Start {name} service')
    is_systemd = config.get('is_systemd', True)  # Default to systemd

    # =========================================================================
    # SYSTEMD SERVICES: Trust systemctl ONLY
    # =========================================================================
    if is_systemd:
        try:
            # Single source of truth: systemctl is-active
            with timed_boundary("systemd.is_active", target=systemd_name):
                result = subprocess.run(
                    ['systemctl', 'is-active', systemd_name],
                    capture_output=True,
                    text=True,
                    timeout=5
                )
            is_active = result.returncode == 0
            status_text = result.stdout.strip()  # "active", "inactive", "failed"

            # For daemon services, also check the actual state (running vs exited)
            # "active (exited)" means it ran once and exited - NOT a running daemon
            sub_state = ""
            if is_active:
                with timed_boundary("systemd.show", target=systemd_name):
                    state_result = subprocess.run(
                        ['systemctl', 'show', systemd_name, '--property=SubState'],
                        capture_output=True,
                        text=True,
                        timeout=5
                    )
                # Output is like "SubState=running" or "SubState=exited"
                if '=' in state_result.stdout:
                    sub_state = state_result.stdout.strip().split('=')[1]

            # Check for placeholder services (active but exited = not a real daemon)
            if is_active and sub_state == "exited":
                # This is a placeholder or oneshot that ran and exited
                hardware = _detect_radio_hardware()

                # Check if the real binary exists — stale placeholder if so
                has_binary = False
                try:
                    bin_result = subprocess.run(
                        ['which', 'meshtasticd'],
                        capture_output=True,
                        text=True,
                        timeout=5
                    )
                    has_binary = bin_result.returncode == 0
                except (subprocess.TimeoutExpired, FileNotFoundError):
                    pass

                if has_binary:
                    # Real binary exists but service is a placeholder — stale
                    return ServiceStatus(
                        name=name,
                        available=False,
                        state=ServiceState.DEGRADED,
                        message=f"{description}: stale placeholder — meshtasticd binary available",
                        fix_hint="Restart NOC to auto-fix, or run: sudo bash scripts/install_noc.sh",
                        port=check_port_num,
                        detection_method="systemctl (exited) + binary exists"
                    )
                elif hardware['has_spi'] and not hardware['has_usb']:
                    # SPI HAT detected but placeholder service - MISMATCH!
                    return ServiceStatus(
                        name=name,
                        available=False,
                        state=ServiceState.DEGRADED,
                        message=f"{description}: WRONG CONFIG - SPI HAT needs native daemon",
                        fix_hint="Run: sudo bash scripts/install_noc.sh (or install meshtasticd)",
                        port=check_port_num,
                        detection_method="systemctl (exited) + hardware mismatch"
                    )
                elif hardware['has_usb']:
                    # USB radio, no native binary — placeholder is expected
                    return ServiceStatus(
                        name=name,
                        available=False,
                        state=ServiceState.NOT_RUNNING,
                        message=f"{description}: USB mode (no daemon needed)",
                        fix_hint=f"Use: meshtastic --port {hardware.get('usb_device', '/dev/ttyUSB0')} --info",
                        port=check_port_num,
                        detection_method="systemctl (exited)"
                    )
                else:
                    # No hardware detected
                    return ServiceStatus(
                        name=name,
                        available=False,
                        state=ServiceState.NOT_RUNNING,
                        message=f"{description}: placeholder (no hardware detected)",
                        fix_hint="Connect a Meshtastic device via USB or configure SPI HAT",
                        port=check_port_num,
                        detection_method="systemctl (exited)"
                    )

            if is_active and sub_state == "running":
                return ServiceStatus(
                    name=name,
                    available=True,
                    state=ServiceState.AVAILABLE,
                    message=f"{description} is running",
                    port=check_port_num,
                    detection_method="systemctl"
                )

            if is_active:
                # Active but sub-state not "running" or "exited"
                # (e.g., "start", "auto-restart", "reload", or empty)
                # Trust systemctl — port fallback here caused flakiness (Issue #20)
                return ServiceStatus(
                    name=name,
                    available=True,
                    state=ServiceState.AVAILABLE,
                    message=f"{description} is active ({sub_state or 'transitioning'})",
                    port=check_port_num,
                    detection_method="systemctl"
                )

            # Not active - check if it exists
            if status_text == "inactive":
                # Service exists but not running
                return ServiceStatus(
                    name=name,
                    available=False,
                    state=ServiceState.NOT_RUNNING,
                    message=f"{description} is not running",
                    fix_hint=fix_hint,
                    port=check_port_num,
                    detection_method="systemctl"
                )

            if status_text == "failed":
                return ServiceStatus(
                    name=name,
                    available=False,
                    state=ServiceState.FAILED,
                    message=f"{description} has failed",
                    fix_hint=f"Check logs: journalctl -u {systemd_name}",
                    port=check_port_num,
                    detection_method="systemctl"
                )

            # Check if service unit exists
            with timed_boundary("systemd.list_unit_files", target=systemd_name):
                check_result = subprocess.run(
                    ['systemctl', 'list-unit-files', f'{systemd_name}.service'],
                    capture_output=True,
                    text=True,
                    timeout=5
                )
            if systemd_name not in check_result.stdout:
                return ServiceStatus(
                    name=name,
                    available=False,
                    state=ServiceState.NOT_INSTALLED,
                    message=f"{description} is not installed",
                    fix_hint=f"Install {name} first",
                    port=check_port_num,
                    detection_method="systemctl"
                )

            # Generic not running
            return ServiceStatus(
                name=name,
                available=False,
                state=ServiceState.NOT_RUNNING,
                message=f"{description} is not running",
                fix_hint=fix_hint,
                port=check_port_num,
                detection_method="systemctl"
            )

        except FileNotFoundError:
            # systemctl not available (non-systemd system)
            logger.warning(f"systemctl not found - cannot check {name}")
            return ServiceStatus(
                name=name,
                available=False,
                state=ServiceState.UNKNOWN,
                message=f"{description}: cannot determine status (no systemctl)",
                fix_hint="Check manually or use port check",
                port=check_port_num,
                detection_method="none"
            )
        except subprocess.TimeoutExpired:
            return ServiceStatus(
                name=name,
                available=False,
                state=ServiceState.UNKNOWN,
                message=f"{description}: status check timed out",
                fix_hint="System may be overloaded",
                port=check_port_num,
                detection_method="systemctl-timeout"
            )
        except Exception as e:
            logger.error(f"Service check failed for {name}: {e}")
            return ServiceStatus(
                name=name,
                available=False,
                state=ServiceState.UNKNOWN,
                message=f"{description}: check failed ({e})",
                port=check_port_num,
                detection_method="error"
            )

    # =========================================================================
    # NON-SYSTEMD SERVICES: Fall back to port/process check
    # Race condition fix: Process may start before binding port, so check
    # process FIRST, then port. Also add retry for startup race condition.
    # =========================================================================
    port_type = config.get('port_type', 'tcp')

    # Check process FIRST (more reliable during startup)
    # This helps with the race condition where process starts but hasn't
    # bound to port yet (e.g., rnsd shows PID but port check fails)
    if check_process_running(systemd_name):
        return ServiceStatus(
            name=name,
            available=True,
            state=ServiceState.AVAILABLE,
            message=f"{description} is running (process detected)",
            port=check_port_num,
            detection_method="process"
        )

    # Fall back to port check
    if check_port_num:
        if port_type == 'udp':
            port_open = check_udp_port(check_port_num, host)
        else:
            port_open = check_port(check_port_num, host)

        if port_open:
            return ServiceStatus(
                name=name,
                available=True,
                state=ServiceState.AVAILABLE,
                message=f"{description} is running (port {check_port_num})",
                port=check_port_num,
                detection_method="port"
            )

    return ServiceStatus(
        name=name,
        available=False,
        state=ServiceState.NOT_RUNNING,
        message=f"{description} is not running",
        fix_hint=fix_hint,
        port=check_port_num,
        detection_method="port+process"
    )


def require_service(name: str, port: Optional[int] = None) -> ServiceStatus:
    """
    Check service and log warning if not available.

    Convenience wrapper around check_service that logs warnings.

    Args:
        name: Service name
        port: Optional port override

    Returns:
        ServiceStatus
    """
    status = check_service(name, port)
    if not status.available:
        logger.warning(f"{status.message}. {status.fix_hint}")
    return status


def apply_config_and_restart(service_name: str = 'meshtasticd', timeout: int = 30) -> Tuple[bool, str]:
    """
    Reload systemd daemon and restart a service.

    This is the standard pattern after modifying service configuration files.
    Always runs daemon-reload before restart to pick up changes.

    Args:
        service_name: Name of the systemd service to restart (default: meshtasticd)
        timeout: Timeout in seconds for each command (default: 30)

    Returns:
        Tuple of (success: bool, message: str)

    Example:
        from utils.service_check import apply_config_and_restart

        # After modifying /etc/meshtasticd/config.yaml:
        success, msg = apply_config_and_restart('meshtasticd')
        if not success:
            show_error(msg)
    """
    try:
        # Step 1: Reload systemd daemon to pick up any service file changes
        with timed_boundary("systemd.daemon_reload", threshold_s=5.0):
            reload_cmd = subprocess.run(
                _sudo_cmd(['systemctl', 'daemon-reload']),
                capture_output=True,
                text=True,
                timeout=timeout
            )
        if reload_cmd.returncode != 0:
            error_msg = reload_cmd.stderr.strip() or "daemon-reload failed"
            logger.error(f"daemon-reload failed: {error_msg}")
            return False, f"daemon-reload failed: {error_msg}"

        # Step 2: Restart the service. systemd waits for the unit to reach
        # the target state — 30s threshold matches the 'systemctl restart'
        # default wait window.
        with timed_boundary("systemd.restart", target=service_name,
                            threshold_s=30.0):
            restart = subprocess.run(
                _sudo_cmd(['systemctl', 'restart', service_name]),
                capture_output=True,
                text=True,
                timeout=timeout
            )
        if restart.returncode != 0:
            error_msg = restart.stderr.strip() or f"restart {service_name} failed"
            logger.error(f"restart {service_name} failed: {error_msg}")
            return False, f"restart {service_name} failed: {error_msg}"

        logger.info(f"Successfully restarted {service_name}")

        # Wait for TCP port readiness (meshtasticd binds 4403 on startup)
        if service_name == 'meshtasticd':
            tcp_ready = _wait_for_tcp_ready(4403, max_wait=15)
            if tcp_ready:
                return True, f"{service_name} restarted and accepting connections"
            else:
                logger.warning("meshtasticd restarted but TCP:4403 not ready within 15s")
                return True, f"{service_name} restarted (TCP port not yet ready)"

        return True, f"{service_name} restarted successfully"

    except subprocess.TimeoutExpired:
        logger.error(f"Timeout while restarting {service_name}")
        return False, f"Timeout while restarting {service_name}"
    except FileNotFoundError:
        logger.error("systemctl not found")
        return False, "systemctl not found - is this a systemd system?"
    except Exception as e:
        logger.error(f"Error restarting {service_name}: {e}")
        return False, f"Error: {e}"


def _wait_for_tcp_ready(port: int, host: str = 'localhost', max_wait: int = 15) -> bool:
    """Poll a TCP port until it accepts connections.

    Used after service restart to ensure the daemon is fully initialized
    and accepting client connections before returning.

    Args:
        port: TCP port number to check
        host: Host to connect to (default: localhost)
        max_wait: Maximum seconds to wait (default: 15)

    Returns:
        True if port became ready, False if timeout
    """
    for _attempt in range(max_wait):
        try:
            with tx_guard.probe_connect(), \
                    socket.create_connection((host, port), timeout=1):
                logger.debug("TCP port %d ready", port)
                return True
        except (ConnectionRefusedError, OSError):
            time.sleep(1)
    return False


def is_service_enabled(
    service_name: str, timeout: int = 5, user: bool = False
) -> bool:
    """Return True iff the unit is enabled to start at boot.

    Read-only (``systemctl is-enabled``). Answers "is this unit the
    *designated* owner of its job on this box?" — distinct from
    ``check_service`` (live state right now). Primary use case: the RNS-init
    boot-race guard (Issue #69) must know whether rnsd WILL host the
    ``@rns/<instance>`` shared instance even when it hasn't started yet,
    so client daemons that win the boot race wait instead of boot-claiming
    the instance out from under it.

    Accepts ``enabled`` and ``enabled-runtime``. Returns False for
    disabled/static/masked/not-found and on any error.
    """
    argv = (
        ['systemctl', '--user', 'is-enabled', service_name]
        if user
        else ['systemctl', 'is-enabled', service_name]
    )
    try:
        result = subprocess.run(
            argv, capture_output=True, text=True, timeout=timeout
        )
        return result.stdout.strip() in ('enabled', 'enabled-runtime')
    except (subprocess.SubprocessError, FileNotFoundError, OSError) as e:
        logger.debug("is_service_enabled(%s) failed: %s", service_name, e)
        return False


def check_systemd_service(
    service_name: str, user: bool = False
) -> Tuple[bool, bool]:
    """Return ``(is_running, is_enabled)`` for a systemd unit.

    Read-only; ``is-active``/``is-enabled`` return code == 0 is the signal.
    Ported from the MeshForge role engine (2026-07-18) so ``provision_role``
    can observe live unit state with the same interface on both apps.

    Args:
        service_name: Service unit name (with or without .service suffix).
        user: When True, query the user-scope manager (``systemctl --user``).

    Returns:
        Tuple of (is_running, is_enabled).
    """
    is_running = False
    is_enabled = False
    base = ['systemctl', '--user'] if user else ['systemctl']
    try:
        result = subprocess.run(
            base + ['is-active', service_name],
            capture_output=True, text=True, timeout=5,
        )
        is_running = result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass
    try:
        result = subprocess.run(
            base + ['is-enabled', service_name],
            capture_output=True, text=True, timeout=5,
        )
        is_enabled = result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass
    return is_running, is_enabled


def is_service_unit_installed(
    service_name: str, timeout: int = 5, user: bool = False
) -> bool:
    """Return True iff the systemd unit FILE exists on this box.

    Orthogonal to active/enabled state — an installed unit might be disabled,
    masked, or failed; this reports True for all of those and False only when
    there is no unit file to load. Uses ``systemctl cat`` (read-only), which
    exits 0 whenever it can resolve a unit file. Ported from MeshForge
    (2026-07-18) for the role engine's absent/present distinction.
    """
    argv = (
        ['systemctl', '--user', 'cat', service_name]
        if user else ['systemctl', 'cat', service_name]
    )
    try:
        result = subprocess.run(
            argv, capture_output=True, text=True, timeout=timeout,
        )
        return result.returncode == 0
    except (subprocess.SubprocessError, FileNotFoundError, OSError) as e:
        logger.debug("is_service_unit_installed(%s) failed: %s", service_name, e)
        return False


def is_service_masked(
    service_name: str, timeout: int = 5, user: bool = False
) -> bool:
    """Return True iff the unit is currently masked.

    Read-only (``systemctl is-enabled`` prints ``masked`` for a masked unit);
    lets callers make masking idempotent. Returns False on any error or
    non-masked state. Ported from MeshForge (2026-07-18).
    """
    argv = (
        ['systemctl', '--user', 'is-enabled', service_name]
        if user else ['systemctl', 'is-enabled', service_name]
    )
    try:
        result = subprocess.run(
            argv, capture_output=True, text=True, timeout=timeout,
        )
        return result.stdout.strip() == 'masked'
    except (subprocess.SubprocessError, FileNotFoundError, OSError) as e:
        logger.debug("is_service_masked(%s) failed: %s", service_name, e)
        return False


def mask_service(
    service_name: str, timeout: int = 30, user: bool = False
) -> Tuple[bool, str]:
    """Mask a systemd unit so it cannot be started by anyone.

    Stronger than ``disable_service``: a masked unit (symlinked to /dev/null)
    refuses ``start``/``restart`` from every source (timer, dependency, manual).
    The durable fix for a rival RNS host on a box where it must never own the
    listener (one rnsd per box). Uses MeshAnchor's ``_sudo_cmd`` elevation.
    Ported from MeshForge (2026-07-18).
    """
    argv = ['systemctl', '--user', 'mask', service_name] if user \
        else _sudo_cmd(['systemctl', 'mask', service_name])
    try:
        result = subprocess.run(
            argv, capture_output=True, text=True, timeout=timeout,
        )
        if result.returncode != 0:
            error_msg = result.stderr.strip() or f"mask {service_name} failed"
            logger.error("mask %s failed: %s", service_name, error_msg)
            return False, f"mask {service_name} failed: {error_msg}"
        logger.info("Successfully masked %s", service_name)
        return True, f"{service_name} masked"
    except subprocess.TimeoutExpired:
        logger.error("Timeout while masking %s", service_name)
        return False, f"Timeout while masking {service_name}"
    except FileNotFoundError:
        return False, "systemctl not found - is this a systemd system?"
    except Exception as e:  # noqa: BLE001 — report, never raise into converge
        logger.error("Error masking %s: %s", service_name, e)
        return False, f"Error: {e}"


def daemon_reload(timeout: int = 30) -> Tuple[bool, str]:
    """
    Reload the systemd daemon to pick up service file changes.

    Use this after creating or modifying service unit files.
    For most cases, prefer enable_service() or apply_config_and_restart()
    which include daemon-reload automatically.

    Args:
        timeout: Timeout in seconds (default: 30)

    Returns:
        Tuple of (success: bool, message: str)

    Example:
        from utils.service_check import daemon_reload

        # After creating a new service file:
        success, msg = daemon_reload()
        if not success:
            show_error(msg)
    """
    try:
        with timed_boundary("systemd.daemon_reload", threshold_s=5.0):
            result = subprocess.run(
                _sudo_cmd(['systemctl', 'daemon-reload']),
                capture_output=True,
                text=True,
                timeout=timeout
            )
        if result.returncode != 0:
            error_msg = result.stderr.strip() or "daemon-reload failed"
            logger.error(f"daemon-reload failed: {error_msg}")
            return False, f"daemon-reload failed: {error_msg}"

        logger.debug("systemctl daemon-reload succeeded")
        return True, "daemon-reload succeeded"

    except subprocess.TimeoutExpired:
        logger.error("Timeout during daemon-reload")
        return False, "Timeout during daemon-reload"
    except FileNotFoundError:
        logger.error("systemctl not found")
        return False, "systemctl not found - is this a systemd system?"
    except Exception as e:
        logger.error(f"Error during daemon-reload: {e}")
        return False, f"Error: {e}"


def enable_service(service_name: str, start: bool = False, timeout: int = 30) -> Tuple[bool, str]:
    """
    Enable a systemd service to start at boot.

    Automatically runs daemon-reload before enabling to ensure service
    file changes are picked up.

    Args:
        service_name: Name of the systemd service to enable
        start: If True, also start the service immediately (default: False)
        timeout: Timeout in seconds for each command (default: 30)

    Returns:
        Tuple of (success: bool, message: str)

    Example:
        from utils.service_check import enable_service

        # After creating a service file:
        success, msg = enable_service('rnsd')
        if not success:
            show_error(msg)

        # Enable and start immediately:
        success, msg = enable_service('meshtasticd', start=True)
    """
    try:
        # Step 1: Reload systemd daemon to pick up service file changes
        with timed_boundary("systemd.daemon_reload", threshold_s=5.0):
            reload_result = subprocess.run(
                _sudo_cmd(['systemctl', 'daemon-reload']),
                capture_output=True,
                text=True,
                timeout=timeout
            )
        if reload_result.returncode != 0:
            error_msg = reload_result.stderr.strip() or "daemon-reload failed"
            logger.error(f"daemon-reload failed: {error_msg}")
            return False, f"daemon-reload failed: {error_msg}"

        # Step 2: Enable the service
        with timed_boundary("systemd.enable", target=service_name,
                            threshold_s=5.0):
            enable_result = subprocess.run(
                _sudo_cmd(['systemctl', 'enable', service_name]),
                capture_output=True,
                text=True,
                timeout=timeout
            )
        if enable_result.returncode != 0:
            error_msg = enable_result.stderr.strip() or f"enable {service_name} failed"
            logger.error(f"enable {service_name} failed: {error_msg}")
            return False, f"enable {service_name} failed: {error_msg}"

        # Step 3: Optionally start the service
        if start:
            with timed_boundary("systemd.start", target=service_name,
                                threshold_s=30.0):
                start_result = subprocess.run(
                    _sudo_cmd(['systemctl', 'start', service_name]),
                    capture_output=True,
                    text=True,
                    timeout=timeout
                )
            if start_result.returncode != 0:
                error_msg = start_result.stderr.strip() or f"start {service_name} failed"
                logger.error(f"start {service_name} failed: {error_msg}")
                return False, f"Enabled but start failed: {error_msg}"

            logger.info(f"Successfully enabled and started {service_name}")
            return True, f"{service_name} enabled and started"

        logger.info(f"Successfully enabled {service_name}")
        return True, f"{service_name} enabled"

    except subprocess.TimeoutExpired:
        logger.error(f"Timeout while enabling {service_name}")
        return False, f"Timeout while enabling {service_name}"
    except FileNotFoundError:
        logger.error("systemctl not found")
        return False, "systemctl not found - is this a systemd system?"
    except Exception as e:
        logger.error(f"Error enabling {service_name}: {e}")
        return False, f"Error: {e}"


def disable_service(service_name: str, timeout: int = 30) -> Tuple[bool, str]:
    """
    Disable a systemd service from starting at boot.

    Args:
        service_name: Name of the systemd service to disable
        timeout: Timeout in seconds (default: 30)

    Returns:
        Tuple of (success: bool, message: str)

    Example:
        from utils.service_check import disable_service

        success, msg = disable_service('meshtasticd')
        if not success:
            show_error(msg)
    """
    try:
        with timed_boundary("systemd.disable", target=service_name,
                            threshold_s=5.0):
            result = subprocess.run(
                _sudo_cmd(['systemctl', 'disable', service_name]),
                capture_output=True,
                text=True,
                timeout=timeout
            )
        if result.returncode != 0:
            error_msg = result.stderr.strip() or f"disable {service_name} failed"
            logger.error(f"disable {service_name} failed: {error_msg}")
            return False, f"disable {service_name} failed: {error_msg}"

        logger.info(f"Successfully disabled {service_name}")
        return True, f"{service_name} disabled"

    except subprocess.TimeoutExpired:
        logger.error(f"Timeout while disabling {service_name}")
        return False, f"Timeout while disabling {service_name}"
    except FileNotFoundError:
        logger.error("systemctl not found")
        return False, "systemctl not found - is this a systemd system?"
    except Exception as e:
        logger.error(f"Error disabling {service_name}: {e}")
        return False, f"Error: {e}"


def start_service(service_name: str, timeout: int = 30) -> Tuple[bool, str]:
    """
    Start a systemd service.

    Args:
        service_name: Name of the systemd service to start
        timeout: Timeout in seconds (default: 30)

    Returns:
        Tuple of (success: bool, message: str)

    Example:
        from utils.service_check import start_service

        success, msg = start_service('meshtasticd')
        if not success:
            show_error(msg)
    """
    try:
        with timed_boundary("systemd.start", target=service_name,
                            threshold_s=30.0):
            result = subprocess.run(
                _sudo_cmd(['systemctl', 'start', service_name]),
                capture_output=True,
                text=True,
                timeout=timeout
            )
        if result.returncode != 0:
            error_msg = result.stderr.strip() or f"start {service_name} failed"
            logger.error(f"start {service_name} failed: {error_msg}")
            return False, f"start {service_name} failed: {error_msg}"

        clear_service_cache()  # post-mutation: next check_service hits systemd
        logger.info(f"Successfully started {service_name}")
        return True, f"{service_name} started"

    except subprocess.TimeoutExpired:
        logger.error(f"Timeout while starting {service_name}")
        return False, f"Timeout while starting {service_name}"
    except FileNotFoundError:
        logger.error("systemctl not found")
        return False, "systemctl not found - is this a systemd system?"
    except Exception as e:
        logger.error(f"Error starting {service_name}: {e}")
        return False, f"Error: {e}"


def stop_service(service_name: str, timeout: int = 30) -> Tuple[bool, str]:
    """
    Stop a systemd service.

    Args:
        service_name: Name of the systemd service to stop
        timeout: Timeout in seconds (default: 30)

    Returns:
        Tuple of (success: bool, message: str)

    Example:
        from utils.service_check import stop_service

        success, msg = stop_service('meshtasticd')
        if not success:
            show_error(msg)
    """
    try:
        with timed_boundary("systemd.stop", target=service_name,
                            threshold_s=30.0):
            result = subprocess.run(
                _sudo_cmd(['systemctl', 'stop', service_name]),
                capture_output=True,
                text=True,
                timeout=timeout
            )
        if result.returncode != 0:
            error_msg = result.stderr.strip() or f"stop {service_name} failed"
            logger.error(f"stop {service_name} failed: {error_msg}")
            return False, f"stop {service_name} failed: {error_msg}"

        clear_service_cache()  # post-mutation: next check_service hits systemd
        logger.info(f"Successfully stopped {service_name}")
        return True, f"{service_name} stopped"

    except subprocess.TimeoutExpired:
        logger.error(f"Timeout while stopping {service_name}")
        return False, f"Timeout while stopping {service_name}"
    except FileNotFoundError:
        logger.error("systemctl not found")
        return False, "systemctl not found - is this a systemd system?"
    except Exception as e:
        logger.error(f"Error stopping {service_name}: {e}")
        return False, f"Error: {e}"


def restart_service(service_name: str, timeout: int = 30) -> Tuple[bool, str]:
    """
    Restart a systemd service.

    For a simple restart without daemon-reload. If you've modified service
    unit files or config that requires a reload, use apply_config_and_restart()
    instead.

    Args:
        service_name: Name of the systemd service to restart
        timeout: Timeout in seconds (default: 30)

    Returns:
        Tuple of (success: bool, message: str)

    Example:
        from utils.service_check import restart_service

        success, msg = restart_service('meshtasticd')
        if not success:
            show_error(msg)
    """
    try:
        with timed_boundary("systemd.restart", target=service_name,
                            threshold_s=30.0):
            result = subprocess.run(
                _sudo_cmd(['systemctl', 'restart', service_name]),
                capture_output=True,
                text=True,
                timeout=timeout
            )
        if result.returncode != 0:
            error_msg = result.stderr.strip() or f"restart {service_name} failed"
            logger.error(f"restart {service_name} failed: {error_msg}")
            return False, f"restart {service_name} failed: {error_msg}"

        clear_service_cache()  # post-mutation: next check_service hits systemd
        logger.info(f"Successfully restarted {service_name}")
        return True, f"{service_name} restarted"

    except subprocess.TimeoutExpired:
        logger.error(f"Timeout while restarting {service_name}")
        return False, f"Timeout while restarting {service_name}"
    except FileNotFoundError:
        logger.error("systemctl not found")
        return False, "systemctl not found - is this a systemd system?"
    except Exception as e:
        logger.error(f"Error restarting {service_name}: {e}")
        return False, f"Error: {e}"


def _sudo_write(file_path: str, content: str, timeout: int = 10) -> Tuple[bool, str]:
    """
    Write content to a file, using sudo tee for privilege elevation when needed.

    Use this for writing to system paths (/etc/, /boot/, /etc/systemd/system/)
    where the current user may not have write access.

    When already running as root, writes directly. When running as a normal user,
    uses 'sudo tee' to elevate privileges for the write.

    Args:
        file_path: Absolute path to the file to write
        content: String content to write
        timeout: Timeout in seconds for the sudo tee command (default: 10)

    Returns:
        Tuple of (success: bool, message: str)

    Example:
        from utils.service_check import _sudo_write

        service_content = '''[Unit]
        Description=My Service
        ...
        '''
        success, msg = _sudo_write('/etc/systemd/system/my.service', service_content)
        if not success:
            show_error(msg)
    """
    try:
        if os.geteuid() == 0:
            # Already root — write atomically (temp in the same dir + rename).
            # A direct open('w') truncates first: a crash or ENOSPC mid-write
            # leaves a half-written systemd unit / /etc config that fails to
            # parse on the next daemon-reload — a silent brick of the very
            # service this call provisions (2026-07-09 Pri-4, ported from
            # MeshForge). os.replace is atomic within one filesystem.
            dest = Path(file_path)
            dest.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp_path = tempfile.mkstemp(
                dir=str(dest.parent), prefix=f".{dest.name}.", suffix=".tmp")
            try:
                with os.fdopen(fd, 'w') as f:
                    f.write(content)
                    f.flush()
                    os.fsync(f.fileno())
                os.chmod(tmp_path, 0o644)
                os.replace(tmp_path, file_path)
            except BaseException:
                with contextlib.suppress(OSError):
                    os.unlink(tmp_path)
                raise
            logger.debug(f"Wrote {file_path} (as root, atomic)")
            return True, f"Wrote {file_path}"

        # Not root — use sudo tee to write with elevation
        result = subprocess.run(
            ['sudo', 'tee', file_path],
            input=content,
            capture_output=True,
            text=True,
            timeout=timeout
        )
        if result.returncode != 0:
            error_msg = result.stderr.strip() or f"Failed to write {file_path}"
            logger.error(f"sudo tee failed for {file_path}: {error_msg}")
            return False, f"Failed to write {file_path}: {error_msg}"

        logger.debug(f"Wrote {file_path} (via sudo tee)")
        return True, f"Wrote {file_path}"

    except subprocess.TimeoutExpired:
        logger.error(f"Timeout writing {file_path}")
        return False, f"Timeout writing {file_path}"
    except PermissionError:
        logger.error(f"Permission denied writing {file_path}")
        return False, f"Permission denied: {file_path}"
    except OSError as e:
        logger.error(f"OS error writing {file_path}: {e}")
        return False, f"OS error: {e}"
    except Exception as e:
        logger.error(f"Error writing {file_path}: {e}")
        return False, f"Error: {e}"

