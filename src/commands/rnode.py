"""
RNode Device Detection and Management

Provides device discovery for RNode LoRa interfaces.
RNodes are Reticulum-compatible LoRa transceivers that provide
long-range mesh networking capability.

Usage:
    from commands.rnode import detect_devices, get_device_info

    devices = detect_devices()
    for device in devices:
        print(f"Found: {device['port']} - {device['model']}")
"""

import os
import logging
import re
from pathlib import Path
from typing import List, Dict, Optional, Any
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Try to import CommandResult
try:
    from commands.base import CommandResult
except ImportError:
    from dataclasses import dataclass as _dataclass

    @_dataclass
    class CommandResult:
        success: bool
        message: str
        data: Any = None

        @classmethod
        def ok(cls, message: str, data: Any = None):
            return cls(success=True, message=message, data=data)

        @classmethod
        def fail(cls, message: str, data: Any = None):
            return cls(success=False, message=message, data=data)


# ============================================================================
# Constants
# ============================================================================

# Common USB vendor/product IDs for RNode-compatible devices
RNODE_USB_IDS = [
    # Official RNode devices
    {'vid': '1a86', 'pid': '55d4', 'name': 'RNode (CH340)'},
    {'vid': '10c4', 'pid': 'ea60', 'name': 'RNode (CP210x)'},
    {'vid': '0403', 'pid': '6001', 'name': 'RNode (FTDI)'},

    # Lilygo T-Beam (common RNode platform)
    {'vid': '1a86', 'pid': '7523', 'name': 'T-Beam (CH340)'},
    {'vid': '303a', 'pid': '1001', 'name': 'T-Beam (ESP32-S3)'},

    # Heltec LoRa32 (also used for RNode)
    {'vid': '10c4', 'pid': 'ea60', 'name': 'Heltec LoRa32'},

    # Generic ESP32 with LoRa
    {'vid': '303a', 'pid': '0002', 'name': 'ESP32-S2'},
    {'vid': '303a', 'pid': '1001', 'name': 'ESP32-S3'},
]

# Serial port patterns to scan
SERIAL_PATTERNS = [
    '/dev/ttyUSB*',
    '/dev/ttyACM*',
    '/dev/tty.usb*',  # macOS
    '/dev/cu.usb*',   # macOS
]

# RNode identification strings
RNODE_ID_STRINGS = [
    b'RNode',
    b'Reticulum',
    b'rns_fw',
    b'T-Beam',
]


# ============================================================================
# Device Detection
# ============================================================================

@dataclass
class RNodeDevice:
    """Represents a detected RNode device."""
    port: str
    model: str = "Unknown"
    vid: str = ""
    pid: str = ""
    serial: str = ""
    firmware_version: str = ""
    is_rnode: bool = False
    is_configured: bool = False
    details: Dict[str, Any] = None

    def __post_init__(self):
        if self.details is None:
            self.details = {}

    def to_dict(self) -> Dict[str, Any]:
        return {
            'port': self.port,
            'model': self.model,
            'vid': self.vid,
            'pid': self.pid,
            'serial': self.serial,
            'firmware_version': self.firmware_version,
            'is_rnode': self.is_rnode,
            'is_configured': self.is_configured,
            'details': self.details,
        }


def get_serial_ports() -> List[str]:
    """
    Get list of available serial ports.

    Returns:
        List of serial port paths
    """
    ports = []

    # Method 1: Glob /dev/tty*
    for pattern in SERIAL_PATTERNS:
        base_dir = Path(pattern).parent
        glob_pattern = Path(pattern).name
        if base_dir.exists():
            ports.extend([str(p) for p in base_dir.glob(glob_pattern)])

    # Method 2: Use pyserial if available
    try:
        import serial.tools.list_ports
        for port in serial.tools.list_ports.comports():
            if port.device not in ports:
                ports.append(port.device)
    except ImportError:
        pass

    # Filter out non-existent
    ports = [p for p in ports if Path(p).exists()]

    return sorted(set(ports))


def get_usb_info(port: str) -> Dict[str, str]:
    """
    Get USB vendor/product info for a serial port.

    Args:
        port: Serial port path (e.g., /dev/ttyUSB0)

    Returns:
        Dict with vid, pid, serial, manufacturer, product
    """
    info = {'vid': '', 'pid': '', 'serial': '', 'manufacturer': '', 'product': ''}

    try:
        # Get device name from port
        dev_name = Path(port).name

        # Read from sysfs
        sysfs_paths = [
            f'/sys/class/tty/{dev_name}/device',
            f'/sys/class/tty/{dev_name}/device/..',
        ]

        for base in sysfs_paths:
            base_path = Path(base)
            if not base_path.exists():
                continue

            # Walk up to find USB device info
            for _ in range(5):  # Max depth
                vid_path = base_path / 'idVendor'
                if vid_path.exists():
                    info['vid'] = vid_path.read_text().strip()
                    info['pid'] = (base_path / 'idProduct').read_text().strip()

                    serial_path = base_path / 'serial'
                    if serial_path.exists():
                        info['serial'] = serial_path.read_text().strip()

                    mfr_path = base_path / 'manufacturer'
                    if mfr_path.exists():
                        info['manufacturer'] = mfr_path.read_text().strip()

                    prod_path = base_path / 'product'
                    if prod_path.exists():
                        info['product'] = prod_path.read_text().strip()

                    break

                base_path = base_path.parent
                if str(base_path) == '/sys':
                    break

    except (OSError, PermissionError) as e:
        logger.debug(f"Could not read USB info for {port}: {e}")

    # Fallback: use pyserial
    if not info['vid']:
        try:
            import serial.tools.list_ports
            for p in serial.tools.list_ports.comports():
                if p.device == port:
                    info['vid'] = f'{p.vid:04x}' if p.vid else ''
                    info['pid'] = f'{p.pid:04x}' if p.pid else ''
                    info['serial'] = p.serial_number or ''
                    info['manufacturer'] = p.manufacturer or ''
                    info['product'] = p.product or ''
                    break
        except ImportError:
            pass

    return info


def identify_device_model(vid: str, pid: str, product: str = '') -> str:
    """
    Identify device model from USB IDs.

    Args:
        vid: USB vendor ID (hex string)
        pid: USB product ID (hex string)
        product: USB product string

    Returns:
        Device model name
    """
    vid_lower = vid.lower()
    pid_lower = pid.lower()

    # Check known IDs
    for known in RNODE_USB_IDS:
        if known['vid'] == vid_lower and known['pid'] == pid_lower:
            return known['name']

    # Fallback to product string
    if product:
        return product

    return "Unknown USB Serial"


def probe_rnode(port: str, timeout: float = 2.0) -> Optional[Dict[str, Any]]:
    """
    Probe a serial port for RNode firmware.

    Args:
        port: Serial port path
        timeout: Connection timeout in seconds

    Returns:
        Dict with firmware info or None if not an RNode
    """
    try:
        import serial
    except ImportError:
        logger.debug("pyserial not installed, skipping probe")
        return None

    result = {'is_rnode': False, 'firmware_version': '', 'details': {}}

    try:
        # Open port briefly
        with serial.Serial(port, 115200, timeout=timeout) as ser:
            # Send RNode identification command
            # RNode firmware responds to specific commands
            ser.write(b'\x00')  # Null byte often triggers response
            ser.flush()

            # Read response
            import time
            time.sleep(0.5)
            response = ser.read(256)

            # Check for RNode identification strings
            for id_str in RNODE_ID_STRINGS:
                if id_str in response:
                    result['is_rnode'] = True
                    break

            # Try to parse firmware version
            if b'RNode' in response:
                result['is_rnode'] = True
                # Look for version string like "v1.2.3"
                version_match = re.search(rb'v?\d+\.\d+(\.\d+)?', response)
                if version_match:
                    result['firmware_version'] = version_match.group(0).decode('utf-8', errors='ignore')

            result['details']['raw_response'] = response[:64].hex() if response else ''

    except serial.SerialException as e:
        logger.debug(f"Could not probe {port}: {e}")
        result['details']['error'] = str(e)
    except Exception as e:
        logger.debug(f"Probe error for {port}: {e}")

    return result


def check_rns_config(port: str) -> bool:
    """
    Check if a port is configured in RNS config.

    Args:
        port: Serial port path

    Returns:
        True if port is in RNS config
    """
    try:
        from utils.paths import get_real_user_home
        config_path = get_real_user_home() / '.reticulum' / 'config'
    except ImportError:
        # Fallback: use SUDO_USER to avoid Path.home() returning /root
        sudo_user = os.environ.get('SUDO_USER')
        if sudo_user and sudo_user != 'root':
            config_path = Path(f'/home/{sudo_user}') / '.reticulum' / 'config'
        else:
            config_path = Path.home() / '.reticulum' / 'config'

    if not config_path.exists():
        return False

    try:
        content = config_path.read_text()
        return port in content
    except Exception as e:
        logger.debug(f"Could not read RNS config at {config_path}: {e}")
        return False


def detect_devices(probe: bool = False) -> List[RNodeDevice]:
    """
    Detect RNode-compatible devices.

    Args:
        probe: If True, probe each device to identify RNode firmware

    Returns:
        List of detected RNodeDevice objects
    """
    devices = []
    seen_ports = set()

    for port in get_serial_ports():
        if port in seen_ports:
            continue
        seen_ports.add(port)

        # Get USB info
        usb_info = get_usb_info(port)

        # Identify model
        model = identify_device_model(
            usb_info['vid'],
            usb_info['pid'],
            usb_info['product']
        )

        device = RNodeDevice(
            port=port,
            model=model,
            vid=usb_info['vid'],
            pid=usb_info['pid'],
            serial=usb_info['serial'],
        )

        # Check if configured in RNS
        device.is_configured = check_rns_config(port)

        # Probe for RNode firmware if requested
        if probe:
            probe_result = probe_rnode(port)
            if probe_result:
                device.is_rnode = probe_result['is_rnode']
                device.firmware_version = probe_result['firmware_version']
                device.details = probe_result['details']

        devices.append(device)

    return devices


# ============================================================================
# CLI Commands
# ============================================================================

def detect_rnode_devices(probe: bool = False) -> CommandResult:
    """
    Detect RNode devices connected to the system.

    Args:
        probe: If True, probe devices for RNode firmware

    Returns:
        CommandResult with list of detected devices
    """
    try:
        devices = detect_devices(probe=probe)

        if not devices:
            return CommandResult.fail(
                "No serial devices found",
                data={'devices': [], 'count': 0}
            )

        rnode_count = sum(1 for d in devices if d.is_rnode)
        configured_count = sum(1 for d in devices if d.is_configured)

        message = f"Found {len(devices)} serial device(s)"
        if probe and rnode_count > 0:
            message += f", {rnode_count} confirmed RNode(s)"
        if configured_count > 0:
            message += f", {configured_count} configured in RNS"

        return CommandResult.ok(
            message,
            data={
                'devices': [d.to_dict() for d in devices],
                'count': len(devices),
                'rnode_count': rnode_count,
                'configured_count': configured_count,
            }
        )

    except Exception as e:
        return CommandResult.fail(f"Device detection failed: {e}")


def get_device_info(port: str) -> CommandResult:
    """
    Get detailed info for a specific device.

    Args:
        port: Serial port path

    Returns:
        CommandResult with device info
    """
    if not Path(port).exists():
        return CommandResult.fail(f"Port not found: {port}")

    try:
        usb_info = get_usb_info(port)
        model = identify_device_model(usb_info['vid'], usb_info['pid'], usb_info['product'])

        device = RNodeDevice(
            port=port,
            model=model,
            vid=usb_info['vid'],
            pid=usb_info['pid'],
            serial=usb_info['serial'],
            is_configured=check_rns_config(port),
        )

        # Always probe for single device query
        probe_result = probe_rnode(port)
        if probe_result:
            device.is_rnode = probe_result['is_rnode']
            device.firmware_version = probe_result['firmware_version']
            device.details = probe_result['details']

        status = "RNode" if device.is_rnode else "Unknown device"
        if device.is_configured:
            status += " (configured)"

        return CommandResult.ok(
            f"{status} on {port}",
            data=device.to_dict()
        )

    except Exception as e:
        return CommandResult.fail(f"Failed to get device info: {e}")


def get_recommended_config(port: str, region: str = 'US') -> CommandResult:
    """
    Get recommended RNode configuration for a port.

    Args:
        port: Serial port path
        region: Regulatory region (US, EU, AU, etc.)

    Returns:
        CommandResult with recommended configuration
    """
    # Region-specific defaults
    REGION_DEFAULTS = {
        'US': {
            'frequency': 903625000,  # 903.625 MHz
            'bandwidth': 250000,
            'spreading_factor': 7,
            'coding_rate': 5,
            'tx_power': 22,
        },
        'EU': {
            'frequency': 867500000,  # 867.5 MHz
            'bandwidth': 125000,
            'spreading_factor': 8,
            'coding_rate': 5,
            'tx_power': 14,  # EU limit
        },
        'AU': {
            'frequency': 917000000,  # 917 MHz
            'bandwidth': 250000,
            'spreading_factor': 7,
            'coding_rate': 5,
            'tx_power': 22,
        },
    }

    region = region.upper()
    if region not in REGION_DEFAULTS:
        return CommandResult.fail(
            f"Unknown region: {region}. Use US, EU, or AU.",
            data={'available_regions': list(REGION_DEFAULTS.keys())}
        )

    config = REGION_DEFAULTS[region].copy()
    config['port'] = port
    config['region'] = region

    # Generate config snippet
    config_snippet = f"""[[RNode LoRa Interface]]
  type = RNodeInterface
  interface_enabled = True
  port = {port}
  frequency = {config['frequency']}
  bandwidth = {config['bandwidth']}
  txpower = {config['tx_power']}
  spreadingfactor = {config['spreading_factor']}
  codingrate = {config['coding_rate']}
"""

    return CommandResult.ok(
        f"Recommended config for {region} region",
        data={
            'config': config,
            'snippet': config_snippet,
        }
    )


def is_available() -> bool:
    """Check if RNode functionality is available."""
    return True
