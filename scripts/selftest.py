#!/usr/bin/env python3
"""
MeshForge Self-Test Script

Quick diagnostic to verify installation and dependencies.

Usage:
    python3 scripts/selftest.py
    python3 scripts/selftest.py --verbose
"""

import sys
import os
import socket
from pathlib import Path

# Colors for output
GREEN = '\033[92m'
RED = '\033[91m'
YELLOW = '\033[93m'
CYAN = '\033[96m'
NC = '\033[0m'  # No Color


def ok(msg):
    print(f"  {GREEN}✓{NC} {msg}")


def fail(msg):
    print(f"  {RED}✗{NC} {msg}")


def warn(msg):
    print(f"  {YELLOW}!{NC} {msg}")


def info(msg):
    print(f"  {CYAN}i{NC} {msg}")


def check_python_version():
    """Check Python version is 3.9+"""
    version = sys.version_info
    if version.major >= 3 and version.minor >= 9:
        ok(f"Python {version.major}.{version.minor}.{version.micro}")
        return True
    else:
        fail(f"Python {version.major}.{version.minor} (need 3.9+)")
        return False


def check_import(module_name, package_name=None):
    """Check if a module can be imported."""
    try:
        __import__(module_name)
        ok(f"{package_name or module_name}")
        return True
    except ImportError:
        fail(f"{package_name or module_name} not installed")
        return False


def check_port(host, port, service_name):
    """Check if a port is listening."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(2)
        result = sock.connect_ex((host, port))
        sock.close()
        if result == 0:
            ok(f"{service_name} (port {port})")
            return True
        else:
            warn(f"{service_name} not running (port {port})")
            return False
    except Exception as e:
        warn(f"{service_name} check failed: {e}")
        return False


def check_file_exists(filepath, description):
    """Check if a file exists."""
    if Path(filepath).exists():
        ok(description)
        return True
    else:
        warn(f"{description} not found")
        return False


def check_directory_exists(dirpath, description):
    """Check if a directory exists."""
    if Path(dirpath).is_dir():
        ok(description)
        return True
    else:
        warn(f"{description} not found")
        return False


def main():
    verbose = '--verbose' in sys.argv or '-v' in sys.argv

    print(f"\n{CYAN}MeshForge Self-Test{NC}")
    print("=" * 40)

    results = {
        'passed': 0,
        'failed': 0,
        'warnings': 0
    }

    # 1. Python version
    print(f"\n{CYAN}Python Environment{NC}")
    if check_python_version():
        results['passed'] += 1
    else:
        results['failed'] += 1

    # 2. Core dependencies
    print(f"\n{CYAN}Core Dependencies{NC}")
    core_deps = [
        ('rich', 'rich'),
        ('textual', 'textual'),
        ('flask', 'flask'),
        ('yaml', 'pyyaml'),
        ('requests', 'requests'),
        ('psutil', 'psutil'),
    ]
    for module, package in core_deps:
        if check_import(module, package):
            results['passed'] += 1
        else:
            results['failed'] += 1

    # 3. Optional dependencies
    print(f"\n{CYAN}Optional Dependencies{NC}")
    optional_deps = [
        ('meshtastic', 'meshtastic'),
        ('folium', 'folium'),
        ('gi', 'PyGObject (GTK)'),
    ]
    for module, package in optional_deps:
        if check_import(module, package):
            results['passed'] += 1
        else:
            results['warnings'] += 1

    # 4. MeshForge modules
    print(f"\n{CYAN}MeshForge Modules{NC}")
    # Add src to path
    src_path = Path(__file__).parent.parent / 'src'
    sys.path.insert(0, str(src_path))

    meshforge_modules = [
        ('utils.diagnostic_engine', 'Diagnostic Engine'),
        ('utils.knowledge_base', 'Knowledge Base'),
        ('utils.coverage_map', 'Coverage Map'),
        ('utils.rf', 'RF Calculations'),
    ]
    for module, name in meshforge_modules:
        if check_import(module, name):
            results['passed'] += 1
        else:
            results['warnings'] += 1

    # 5. Services
    print(f"\n{CYAN}Services{NC}")
    services = [
        ('localhost', 4403, 'meshtasticd'),
        ('localhost', 37428, 'rnsd'),
        ('localhost', 8080, 'HamClock'),
        ('localhost', 1883, 'MQTT (mosquitto)'),
    ]
    for host, port, name in services:
        if check_port(host, port, name):
            results['passed'] += 1
        else:
            results['warnings'] += 1

    # 6. Configuration files
    print(f"\n{CYAN}Configuration{NC}")

    # Get real user home (handles sudo correctly)
    try:
        from utils.paths import get_real_user_home
        home = get_real_user_home()
    except ImportError:
        # Fallback with SUDO_USER handling
        sudo_user = os.environ.get('SUDO_USER')
        if sudo_user and sudo_user != 'root':
            home = Path(f'/home/{sudo_user}')
        else:
            home = Path.home()

    config_checks = [
        (home / '.config' / 'meshforge', 'User config directory'),
        (Path('/etc/meshtasticd'), 'meshtasticd config'),
    ]
    for path, desc in config_checks:
        if check_directory_exists(path, desc):
            results['passed'] += 1
        else:
            results['warnings'] += 1

    # Summary
    print("\n" + "=" * 40)
    total = results['passed'] + results['failed'] + results['warnings']
    print(f"{CYAN}Results:{NC}")
    print(f"  {GREEN}Passed:{NC}   {results['passed']}/{total}")
    if results['failed'] > 0:
        print(f"  {RED}Failed:{NC}   {results['failed']}/{total}")
    if results['warnings'] > 0:
        print(f"  {YELLOW}Warnings:{NC} {results['warnings']}/{total}")

    # Overall status
    if results['failed'] == 0:
        print(f"\n{GREEN}MeshForge is ready to use!{NC}")
        print(f"\nRun: {CYAN}sudo python3 src/launcher.py{NC}")
        return 0
    else:
        print(f"\n{RED}Some required dependencies are missing.{NC}")
        print(f"Run: {CYAN}pip3 install -r requirements.txt{NC}")
        return 1


if __name__ == '__main__':
    sys.exit(main())
