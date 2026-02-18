"""
Service Availability Utilities for MeshForge

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

import socket
import subprocess
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Tuple
from enum import Enum

from utils.ports import MESHTASTICD_PORT, MQTT_PORT, RNS_SHARED_INSTANCE_PORT

logger = logging.getLogger(__name__)

# Public API - these are the functions/classes intended for external use
__all__ = [
    # Main entry points
    'check_service',        # Primary status checker (SINGLE SOURCE OF TRUTH)
    'require_service',      # Check with exception on failure
    'check_port',           # TCP port check (utility)
    'check_udp_port',       # UDP port check (utility)
    'check_process_running', # Process check via pgrep (utility)
    'check_systemd_service', # Systemd status check
    # Service management
    'daemon_reload',             # Reload systemd daemon
    'enable_service',            # Enable service at boot
    'apply_config_and_restart',  # Reload daemon + restart service
    # Port lockdown (MeshForge owns the browser)
    'lock_port_external',        # Block external access to a port
    'unlock_port_external',      # Restore external access to a port
    'check_port_locked',         # Check if port is locked to localhost
    'persist_iptables',          # Save iptables rules to survive reboot
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
KNOWN_SERVICES = {
    'meshtasticd': {
        'port': MESHTASTICD_PORT,
        'systemd_name': 'meshtasticd',
        'is_systemd': True,  # Trust systemctl only
        'description': 'Meshtastic daemon',
        'fix_hint': 'Start with: sudo systemctl start meshtasticd',
    },
    'rnsd': {
        'port': RNS_SHARED_INSTANCE_PORT,
        'port_type': 'udp',
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
        'description': 'NomadNet mesh messaging client',
        'fix_hint': 'Start with: nomadnetwork (run as user, not root)',
    },
}


def _detect_radio_hardware() -> dict:
    """
    Detect what Meshtastic radio hardware is present.

    Returns:
        dict with:
            has_spi: bool - SPI devices present (/dev/spidev*)
            has_usb: bool - USB serial devices present (/dev/ttyUSB*, /dev/ttyACM*)
            spi_devices: list - SPI device paths
            usb_devices: list - USB device paths
            usb_device: str - First USB device (for fix hints)
            hardware_type: str - 'spi', 'usb', 'both', or 'none'
    """
    from pathlib import Path

    result = {
        'has_spi': False,
        'has_usb': False,
        'spi_devices': [],
        'usb_devices': [],
        'usb_device': '/dev/ttyUSB0',
        'hardware_type': 'none'
    }

    # Check SPI devices
    spi_devices = list(Path('/dev').glob('spidev*'))
    if spi_devices:
        result['has_spi'] = True
        result['spi_devices'] = [str(d) for d in spi_devices]

    # Check USB serial devices
    usb_devices = list(Path('/dev').glob('ttyUSB*')) + list(Path('/dev').glob('ttyACM*'))
    if usb_devices:
        result['has_usb'] = True
        result['usb_devices'] = [str(d) for d in usb_devices]
        result['usb_device'] = str(usb_devices[0])

    # Determine hardware type
    if result['has_spi'] and result['has_usb']:
        result['hardware_type'] = 'both'
    elif result['has_spi']:
        result['hardware_type'] = 'spi'
    elif result['has_usb']:
        result['hardware_type'] = 'usb'

    return result


# =============================================================================
# UTILITY FUNCTIONS
# These are kept for direct use but NOT used by check_service() for systemd
# services (Issue #17: avoid conflicting detection methods)
# =============================================================================


def check_port(port: int, host: str = 'localhost', timeout: float = 2.0) -> bool:
    """
    Check if a TCP port is accepting connections.

    Args:
        port: TCP port number
        host: Hostname to check (default localhost)
        timeout: Connection timeout in seconds

    Returns:
        True if port is open, False otherwise
    """
    sock = None
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        result = sock.connect_ex((host, port))
        return result == 0
    except (socket.error, OSError) as e:
        logger.debug(f"Port check failed for {host}:{port}: {e}")
        return False
    finally:
        if sock:
            try:
                sock.close()
            except Exception:
                pass  # Socket close errors are non-critical


def check_udp_port(port: int, host: str = '127.0.0.1', timeout: float = 2.0) -> bool:
    """
    Check if a UDP port is in use.

    Primary method: parse `ss -uln` output (kernel socket state).
    This is reliable even when the service sets SO_REUSEADDR/SO_REUSEPORT,
    which causes bind-test false negatives.

    Fallback: bind test (only if ss is unavailable).

    Args:
        port: UDP port number
        host: Host address to check (default 127.0.0.1)
        timeout: Socket timeout in seconds

    Returns:
        True if port appears to be in use (service running), False otherwise
    """
    # Primary: use ss to read kernel socket table directly.
    # The bind-test approach gives false negatives when the service
    # sets SO_REUSEADDR (e.g., rnsd), allowing our test bind to succeed
    # even though the port IS in use.
    try:
        result = subprocess.run(
            ['ss', '-uln'],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0 and result.stdout.strip():
            port_str = str(port)
            for line in result.stdout.split('\n'):
                # ss output format:
                #   UNCONN 0 0  127.0.0.1:37428  0.0.0.0:*
                #   UNCONN 0 0      [::1]:37428     [::]:*
                # Split on whitespace to get Local Address:Port column
                parts = line.split()
                if len(parts) >= 5:
                    local_addr = parts[4]  # e.g., "127.0.0.1:37428" or "[::1]:37428"
                    if local_addr.endswith(':' + port_str):
                        return True
            return False
    except (subprocess.SubprocessError, FileNotFoundError, OSError):
        pass  # ss not available, fall through to bind test

    # Fallback: bind test (unreliable with SO_REUSEADDR, but better than nothing)
    # Try multiple addresses since service might bind to different interfaces
    hosts_to_check = [host]
    if host == '127.0.0.1':
        hosts_to_check.append('0.0.0.0')  # Also check wildcard

    for check_host in hosts_to_check:
        sock = None
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.settimeout(timeout)
            # Try to bind to the port - if it fails, port is in use
            sock.bind((check_host, port))
            # If we successfully bound, port was NOT in use on this address
            sock.close()
            continue  # Try next address
        except OSError as e:
            # EADDRINUSE (98 on Linux) means the port is already bound
            # This indicates the service IS running
            if e.errno in (98, 48, 10048):  # Linux, macOS, Windows EADDRINUSE
                return True
            logger.debug(f"UDP port check error for {check_host}:{port}: {e}")
        finally:
            if sock:
                try:
                    sock.close()
                except Exception:
                    pass  # Socket close errors are non-critical

    return False


def check_process_running(process_name: str) -> bool:
    """
    Check if a process is running by name.

    Args:
        process_name: Name of the process to check (e.g., 'rnsd')

    Returns:
        True if process is running, False otherwise
    """
    try:
        # First try exact process name match (most reliable)
        result = subprocess.run(
            ['pgrep', '-x', process_name],  # -x = exact match
            capture_output=True,
            text=True,
            timeout=5
        )
        if result.returncode == 0 and result.stdout.strip():
            return True

        # Also check with -f but use word boundaries to avoid partial matches
        # e.g., match "rnsd" but not "myrnsd_wrapper"
        result = subprocess.run(
            ['pgrep', '-f', f'(^|/)({process_name})(\\s|$)'],
            capture_output=True,
            text=True,
            timeout=5
        )
        if result.returncode == 0 and result.stdout.strip():
            return True

        # Fallback: Check via ps for python-based services (e.g., python3 -m rnsd)
        if process_name in ('rnsd', 'nomadnet', 'meshchat'):
            result = subprocess.run(
                ['pgrep', '-f', f'python.*{process_name}'],
                capture_output=True,
                text=True,
                timeout=5
            )
            return result.returncode == 0 and result.stdout.strip()

        return False
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return False


def check_process_with_pid(process_name: str) -> Tuple[bool, Optional[str]]:
    """
    Check if a process is running and return its PID.

    Args:
        process_name: Name of the process to check (e.g., 'rnsd', 'meshtasticd')

    Returns:
        Tuple of (is_running, pid) where pid is the first matching PID or None

    Example:
        >>> running, pid = check_process_with_pid('rnsd')
        >>> if running:
        ...     print(f"rnsd is running (PID: {pid})")
    """
    try:
        # First try exact process name match (most reliable)
        result = subprocess.run(
            ['pgrep', '-x', process_name],
            capture_output=True,
            text=True,
            timeout=5
        )
        if result.returncode == 0 and result.stdout.strip():
            pid = result.stdout.strip().split('\n')[0]
            return True, pid

        # Also check with -f for processes run via interpreters
        result = subprocess.run(
            ['pgrep', '-f', process_name],
            capture_output=True,
            text=True,
            timeout=5
        )
        if result.returncode == 0 and result.stdout.strip():
            # Filter out pgrep itself and get first real PID
            pids = [p for p in result.stdout.strip().split('\n') if p]
            if pids:
                return True, pids[0]

        return False, None
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return False, None


def check_systemd_service(service_name: str) -> Tuple[bool, bool]:
    """
    Check if a systemd service is running and enabled.

    Args:
        service_name: Name of the systemd service

    Returns:
        Tuple of (is_running, is_enabled)
    """
    is_running = False
    is_enabled = False

    try:
        result = subprocess.run(
            ['systemctl', 'is-active', service_name],
            capture_output=True,
            text=True,
            timeout=5
        )
        is_running = result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass

    try:
        result = subprocess.run(
            ['systemctl', 'is-enabled', service_name],
            capture_output=True,
            text=True,
            timeout=5
        )
        is_enabled = result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass

    return is_running, is_enabled


def check_service(name: str, port: Optional[int] = None, host: str = 'localhost') -> ServiceStatus:
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

    Returns:
        ServiceStatus with availability info and fix hints

    API Contract:
        - ALWAYS returns a ServiceStatus (never None)
        - ServiceStatus.available: bool indicating if service is ready
        - ServiceStatus.state: ServiceState enum (AVAILABLE, NOT_RUNNING, etc.)
        - ServiceStatus.detection_method: How status was determined
        - Known services: meshtasticd, rnsd, mosquitto, nomadnet
    """
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
                # Check if this is a mismatch (SPI HAT but USB placeholder)
                hardware = _detect_radio_hardware()

                if hardware['has_spi'] and not hardware['has_usb']:
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
                    # USB radio - placeholder is correct
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
        daemon_reload = subprocess.run(
            ['systemctl', 'daemon-reload'],
            capture_output=True,
            text=True,
            timeout=timeout
        )
        if daemon_reload.returncode != 0:
            error_msg = daemon_reload.stderr.strip() or "daemon-reload failed"
            logger.error(f"daemon-reload failed: {error_msg}")
            return False, f"daemon-reload failed: {error_msg}"

        # Step 2: Restart the service
        restart = subprocess.run(
            ['systemctl', 'restart', service_name],
            capture_output=True,
            text=True,
            timeout=timeout
        )
        if restart.returncode != 0:
            error_msg = restart.stderr.strip() or f"restart {service_name} failed"
            logger.error(f"restart {service_name} failed: {error_msg}")
            return False, f"restart {service_name} failed: {error_msg}"

        logger.info(f"Successfully restarted {service_name}")
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
        result = subprocess.run(
            ['systemctl', 'daemon-reload'],
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
        reload_result = subprocess.run(
            ['systemctl', 'daemon-reload'],
            capture_output=True,
            text=True,
            timeout=timeout
        )
        if reload_result.returncode != 0:
            error_msg = reload_result.stderr.strip() or "daemon-reload failed"
            logger.error(f"daemon-reload failed: {error_msg}")
            return False, f"daemon-reload failed: {error_msg}"

        # Step 2: Enable the service
        enable_result = subprocess.run(
            ['systemctl', 'enable', service_name],
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
            start_result = subprocess.run(
                ['systemctl', 'start', service_name],
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


# ─────────────────────────────────────────────────────────────────────
# Port lockdown — MeshForge owns the browser
# ─────────────────────────────────────────────────────────────────────

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
            ['iptables', '-C', 'INPUT'] + rule_args,
            capture_output=True, text=True, timeout=timeout
        )
        if check.returncode == 0:
            logger.info("iptables rule for port %d already in place", port)
            return True, f"Port {port} already locked to localhost"

        # Add the rule
        result = subprocess.run(
            ['iptables', '-A', 'INPUT'] + rule_args,
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
            ['iptables', '-D', 'INPUT'] + rule_args,
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
            ['iptables', '-C', 'INPUT'] + rule_args,
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
            ['netfilter-persistent', 'save'],
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
    import shutil
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
            ['iptables-save'],
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
