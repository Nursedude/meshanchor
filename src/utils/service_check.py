"""
Service Availability Utilities for MeshForge

Provides standardized service checking before connecting to external services.
Use these instead of assuming services are running.

Usage:
    from utils.service_check import check_port, check_service, ServiceStatus
    from utils.ports import MESHTASTICD_PORT

    # Quick port check
    if check_port(MESHTASTICD_PORT):
        connect_to_meshtasticd()
    else:
        show_error(f"meshtasticd not running on port {MESHTASTICD_PORT}")

    # Full service check with actionable feedback
    status = check_service('meshtasticd')
    if not status.available:
        show_error(status.message)
        show_fix(status.fix_hint)
"""

import socket
import subprocess
import logging
from dataclasses import dataclass
from typing import Optional, Tuple
from enum import Enum

from utils.ports import MESHTASTICD_PORT, HAMCLOCK_PORT, MQTT_PORT, RNS_SHARED_INSTANCE_PORT

logger = logging.getLogger(__name__)

# Public API - these are the functions/classes intended for external use
__all__ = [
    # Main entry points
    'check_service',        # Primary status checker (SINGLE SOURCE OF TRUTH)
    'require_service',      # Check with exception on failure
    'check_port',           # TCP port check
    'check_udp_port',       # UDP port check
    'check_process_running', # Process check via pgrep
    'check_systemd_service', # Systemd status check
    'check_meshtasticd_responsive',  # Meshtasticd-specific verification
    # Data classes
    'ServiceStatus',        # Return type from check_service
    'ServiceState',         # Status enum (AVAILABLE, DEGRADED, etc.)
    # Configuration
    'KNOWN_SERVICES',       # Service configuration dict
]


class ServiceState(Enum):
    """Service availability states."""
    AVAILABLE = "available"
    DEGRADED = "degraded"       # Port open but not responsive
    PORT_CLOSED = "port_closed"
    NOT_RUNNING = "not_running"
    NOT_INSTALLED = "not_installed"
    UNKNOWN = "unknown"


@dataclass
class ServiceStatus:
    """Result of a service availability check."""
    name: str
    available: bool
    state: ServiceState
    message: str
    fix_hint: str = ""
    port: Optional[int] = None

    def __bool__(self) -> bool:
        return self.available


# Known services and their configurations
# Port numbers imported from utils.ports for centralization
KNOWN_SERVICES = {
    'meshtasticd': {
        'port': MESHTASTICD_PORT,
        'systemd_name': 'meshtasticd',
        'description': 'Meshtastic daemon',
        'fix_hint': 'Start with: sudo systemctl start meshtasticd',
        'verify_func': 'check_meshtasticd_responsive',  # Extra verification
    },
    'rnsd': {
        'port': RNS_SHARED_INSTANCE_PORT,  # UDP 37428 - shared instance port
        'port_type': 'udp',  # rnsd uses UDP, not TCP
        'systemd_name': 'rnsd',
        'description': 'Reticulum Network Stack daemon',
        'fix_hint': 'Start with: rnsd or sudo systemctl start rnsd',
    },
    'hamclock': {
        'port': HAMCLOCK_PORT,
        'systemd_name': 'hamclock',
        'description': 'HamClock space weather display',
        'fix_hint': 'Start with: sudo systemctl start hamclock',
    },
    'mosquitto': {
        'port': MQTT_PORT,
        'systemd_name': 'mosquitto',
        'description': 'MQTT broker',
        'fix_hint': 'Start with: sudo systemctl start mosquitto',
    },
}


def check_meshtasticd_responsive(timeout: float = 5.0) -> tuple:
    """
    Verify meshtasticd is actually responsive, not just that port is open.

    Sometimes meshtasticd hangs with port open but not responding.

    Args:
        timeout: How long to wait for response

    Returns:
        Tuple of (is_responsive: bool, message: str)
    """
    import subprocess

    # First check if port is even open
    if not check_port(MESHTASTICD_PORT, timeout=2.0):
        return False, "Port 4403 not open"

    # Try a quick meshtastic CLI command to verify responsiveness
    try:
        result = subprocess.run(
            ['meshtastic', '--host', 'localhost', '--info'],
            capture_output=True,
            text=True,
            timeout=timeout
        )

        if result.returncode == 0:
            # Check for actual device info in output
            if 'Owner:' in result.stdout or 'Nodes' in result.stdout:
                return True, "Responsive (device info received)"
            elif 'Connected to' in result.stdout:
                return True, "Responsive (connected)"
            else:
                return True, "Responsive"

        # Non-zero return but process completed
        stderr = result.stderr.lower()
        if 'timed out' in stderr or 'timeout' in stderr:
            return False, "Port open but not responding (try: sudo systemctl restart meshtasticd)"
        elif 'connection refused' in stderr:
            return False, "Connection refused"
        elif 'error' in stderr:
            return False, f"Error: {result.stderr[:100]}"
        else:
            return False, "Command failed"

    except subprocess.TimeoutExpired:
        return False, "Port open but unresponsive (hung?) - try: sudo systemctl restart meshtasticd"
    except FileNotFoundError:
        # meshtastic CLI not installed, fall back to port check only
        return True, "Port open (CLI not available for verification)"
    except Exception as e:
        return False, f"Check failed: {e}"


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
    Check if a UDP port is in use by trying to bind to it.

    For services like rnsd that use UDP, we can check if the port is already
    bound by attempting to bind ourselves - if it fails with EADDRINUSE, the
    service is running.

    Args:
        port: UDP port number
        host: Host address to check (default 127.0.0.1)
        timeout: Socket timeout in seconds

    Returns:
        True if port appears to be in use (service running), False otherwise
    """
    sock = None
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(timeout)
        # Try to bind to the port - if it fails, port is in use
        sock.bind((host, port))
        # If we successfully bound, port was NOT in use
        return False
    except OSError as e:
        # EADDRINUSE (98 on Linux) means the port is already bound
        # This indicates the service IS running
        if e.errno in (98, 48, 10048):  # Linux, macOS, Windows EADDRINUSE
            return True
        logger.debug(f"UDP port check error for {host}:{port}: {e}")
        return False
    finally:
        if sock:
            try:
                sock.close()
            except Exception:
                pass  # Socket close errors are non-critical


def check_process_running(process_name: str) -> bool:
    """
    Check if a process is running by name.

    Args:
        process_name: Name of the process to check (e.g., 'rnsd')

    Returns:
        True if process is running, False otherwise
    """
    try:
        # Use pgrep with multiple patterns to catch different invocation methods
        # -f matches the full command line
        result = subprocess.run(
            ['pgrep', '-f', process_name],
            capture_output=True,
            text=True,
            timeout=5
        )
        if result.returncode == 0 and result.stdout.strip():
            return True

        # Also check with just the process name (no -f)
        result = subprocess.run(
            ['pgrep', process_name],
            capture_output=True,
            text=True,
            timeout=5
        )
        return result.returncode == 0 and result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return False


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

    Uses multiple detection methods for reliability:
    1. Port check (TCP or UDP based on service config)
    2. Process check (pgrep)
    3. Systemd status

    Args:
        name: Service name (e.g., 'meshtasticd', 'hamclock', 'rnsd')
        port: Override port to check (uses known default if not specified)
        host: Host to check (default localhost)

    Returns:
        ServiceStatus with availability info and fix hints

    API Contract:
        - ALWAYS returns a ServiceStatus (never None)
        - ServiceStatus.available: bool indicating if service is ready
        - ServiceStatus.state: ServiceState enum (AVAILABLE, UNAVAILABLE, DEGRADED, UNKNOWN)
        - ServiceStatus.fix_hint: Actionable command to fix the issue
        - Known services: meshtasticd, rnsd, hamclock, mosquitto
        - Tests: tests/test_service_check.py::TestCheckService
    """
    # Get known service config
    config = KNOWN_SERVICES.get(name, {})
    check_port_num = port or config.get('port')
    port_type = config.get('port_type', 'tcp')  # Default to TCP
    systemd_name = config.get('systemd_name', name)
    description = config.get('description', name)
    fix_hint = config.get('fix_hint', f'Start {name} service')
    verify_func_name = config.get('verify_func')

    # Check port if applicable (TCP or UDP)
    port_is_open = False
    if check_port_num:
        if port_type == 'udp':
            port_is_open = check_udp_port(check_port_num, host)
        else:
            port_is_open = check_port(check_port_num, host)

        if port_is_open:
            # For services with verification function, verify actual responsiveness
            if verify_func_name and verify_func_name == 'check_meshtasticd_responsive':
                is_responsive, verify_msg = check_meshtasticd_responsive()
                if is_responsive:
                    return ServiceStatus(
                        name=name,
                        available=True,
                        state=ServiceState.AVAILABLE,
                        message=f"{description}: {verify_msg}",
                        port=check_port_num
                    )
                else:
                    # Port open but service not responding properly
                    return ServiceStatus(
                        name=name,
                        available=False,
                        state=ServiceState.DEGRADED,
                        message=f"{description}: {verify_msg}",
                        fix_hint="Try: sudo systemctl restart meshtasticd",
                        port=check_port_num
                    )

            # Standard port check without verification
            proto = 'UDP' if port_type == 'udp' else 'TCP'
            return ServiceStatus(
                name=name,
                available=True,
                state=ServiceState.AVAILABLE,
                message=f"{description} is running ({proto} {check_port_num})",
                port=check_port_num
            )

    # Check if process is running (catches non-systemd starts)
    if check_process_running(systemd_name):
        # Process is running - if we have a port and it's not open,
        # it might be starting up or using a different port
        if check_port_num and not port_is_open:
            # Service running but port not responding - might be starting
            return ServiceStatus(
                name=name,
                available=True,  # Mark as available since process IS running
                state=ServiceState.AVAILABLE,
                message=f"{description} is running (process detected)",
                port=check_port_num
            )
        return ServiceStatus(
            name=name,
            available=True,
            state=ServiceState.AVAILABLE,
            message=f"{description} is running (process detected)"
        )

    # Check systemd service
    is_running, is_enabled = check_systemd_service(systemd_name)

    if is_running:
        # Systemd says running but port not open and process not found
        # Trust systemd in this case
        return ServiceStatus(
            name=name,
            available=True,
            state=ServiceState.AVAILABLE,
            message=f"{description} is running (systemd)"
        )

    if is_enabled:
        return ServiceStatus(
            name=name,
            available=False,
            state=ServiceState.NOT_RUNNING,
            message=f"{description} is enabled but not running",
            fix_hint=fix_hint,
            port=check_port_num
        )

    # Check if service exists at all
    try:
        result = subprocess.run(
            ['systemctl', 'status', systemd_name],
            capture_output=True,
            text=True,
            timeout=5
        )
        if 'could not be found' in result.stderr.lower():
            return ServiceStatus(
                name=name,
                available=False,
                state=ServiceState.NOT_INSTALLED,
                message=f"{description} is not installed (no systemd service)",
                fix_hint=f"Install {name} first or start manually",
                port=check_port_num
            )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass

    return ServiceStatus(
        name=name,
        available=False,
        state=ServiceState.NOT_RUNNING,
        message=f"{description} is not running",
        fix_hint=fix_hint,
        port=check_port_num
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
