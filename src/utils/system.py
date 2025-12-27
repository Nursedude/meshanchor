"""System utilities for OS detection and information"""

import os
import sys
import platform
import subprocess
import distro


def check_root():
    """Check if running with root privileges"""
    return os.geteuid() == 0


def get_system_info():
    """Get comprehensive system information"""
    info = {}

    # OS information
    info['os'] = distro.name()
    info['os_version'] = distro.version()
    info['os_codename'] = distro.codename()

    # Architecture
    info['arch'] = platform.machine()
    info['platform'] = platform.system()

    # Python version
    info['python'] = platform.python_version()

    # Kernel
    info['kernel'] = platform.release()

    # Check if Raspberry Pi
    info['is_pi'] = is_raspberry_pi()

    # Determine if 32-bit or 64-bit
    info['bits'] = get_architecture_bits()

    return info


def is_raspberry_pi():
    """Check if system is a Raspberry Pi"""
    try:
        with open('/proc/cpuinfo', 'r') as f:
            cpuinfo = f.read()
            return 'Raspberry Pi' in cpuinfo or 'BCM' in cpuinfo
    except FileNotFoundError:
        return False


def get_architecture_bits():
    """Determine if system is 32-bit or 64-bit"""
    arch = platform.machine().lower()

    if 'aarch64' in arch or 'arm64' in arch:
        return 64
    elif 'armv7' in arch or 'armhf' in arch or 'armv6' in arch:
        return 32
    elif 'x86_64' in arch or 'amd64' in arch:
        return 64
    elif 'i386' in arch or 'i686' in arch:
        return 32
    else:
        # Default to checking architecture
        return 64 if sys.maxsize > 2**32 else 32


def get_os_type():
    """Get OS type for installation (armhf, arm64, or other)"""
    info = get_system_info()

    if not info['is_pi']:
        return 'unknown'

    if info['bits'] == 64:
        return 'arm64'
    elif info['bits'] == 32:
        return 'armhf'
    else:
        return 'unknown'


def run_command(command, shell=False, capture_output=True):
    """Run a system command and return the result"""
    try:
        if isinstance(command, str) and not shell:
            command = command.split()

        result = subprocess.run(
            command,
            shell=shell,
            capture_output=capture_output,
            text=True,
            timeout=300
        )

        return {
            'returncode': result.returncode,
            'stdout': result.stdout,
            'stderr': result.stderr,
            'success': result.returncode == 0
        }
    except subprocess.TimeoutExpired:
        return {
            'returncode': -1,
            'stdout': '',
            'stderr': 'Command timed out',
            'success': False
        }
    except Exception as e:
        return {
            'returncode': -1,
            'stdout': '',
            'stderr': str(e),
            'success': False
        }


def check_internet_connection():
    """Check if internet connection is available"""
    try:
        result = run_command('ping -c 1 8.8.8.8')
        return result['success']
    except Exception:
        return False


def get_service_status(service_name):
    """Get systemd service status"""
    result = run_command(f'systemctl is-active {service_name}')
    return result['stdout'].strip() if result['success'] else 'unknown'


def is_service_running(service_name):
    """Check if a systemd service is running"""
    return get_service_status(service_name) == 'active'


def enable_service(service_name):
    """Enable and start a systemd service"""
    enable_result = run_command(f'systemctl enable {service_name}')
    start_result = run_command(f'systemctl start {service_name}')
    return enable_result['success'] and start_result['success']


def restart_service(service_name):
    """Restart a systemd service"""
    result = run_command(f'systemctl restart {service_name}')
    return result['success']


def check_package_installed(package_name):
    """Check if a Debian package is installed"""
    result = run_command(f'dpkg -l {package_name}')
    return result['success']


def get_available_memory():
    """Get available system memory in MB"""
    try:
        import psutil
        mem = psutil.virtual_memory()
        return mem.available // (1024 * 1024)
    except ImportError:
        # Fallback to reading /proc/meminfo
        try:
            with open('/proc/meminfo', 'r') as f:
                for line in f:
                    if 'MemAvailable' in line:
                        return int(line.split()[1]) // 1024
        except Exception:
            return 0


def get_disk_space(path='/'):
    """Get available disk space in MB"""
    try:
        import psutil
        disk = psutil.disk_usage(path)
        return disk.free // (1024 * 1024)
    except ImportError:
        # Fallback to df command
        result = run_command(f'df -m {path}')
        if result['success']:
            lines = result['stdout'].strip().split('\n')
            if len(lines) > 1:
                parts = lines[1].split()
                if len(parts) >= 4:
                    return int(parts[3])
        return 0
