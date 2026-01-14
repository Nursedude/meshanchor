#!/usr/bin/env python3
"""
Meshtasticd Manager - Web UI

Browser-based interface for managing meshtasticd.
Access via http://your-pi-ip:8080/

Usage:
    sudo python3 src/main_web.py              # Run on port 8080
    sudo python3 src/main_web.py --port 8888  # Custom port
    sudo python3 src/main_web.py --host 0.0.0.0  # Listen on all interfaces
"""

import os
import sys
import re
import json
import socket
import signal
import subprocess
import threading
import argparse
import secrets
import atexit
from pathlib import Path
from datetime import datetime
from functools import wraps

# Track running subprocesses for cleanup
_running_processes = []
_shutdown_flag = False

# Add src to path
sys.path.insert(0, str(Path(__file__).parent))

try:
    from flask import Flask, render_template, render_template_string, jsonify, request, redirect, url_for, session
except ImportError:
    print("Flask not installed. Installing...")
    subprocess.run([sys.executable, '-m', 'pip', 'install', '--break-system-packages', 'flask'],
                   capture_output=True, timeout=120)
    from flask import Flask, render_template, render_template_string, jsonify, request, redirect, url_for, session

# Import centralized service checker
try:
    from utils.service_check import check_service as _check_service, check_port
except ImportError:
    _check_service = None
    check_port = None

# Import meshtastic connection manager for resilient TCP handling
try:
    from utils.meshtastic_connection import get_connection_manager, MeshtasticConnectionManager
    _meshtastic_mgr = None
except ImportError:
    get_connection_manager = None
    MeshtasticConnectionManager = None
    _meshtastic_mgr = None

# Configure Flask with external templates
_template_folder = Path(__file__).parent / 'web' / 'templates'
app = Flask(__name__, template_folder=str(_template_folder))
app.secret_key = secrets.token_hex(32)

# Register modular blueprints (new architecture)
try:
    from web.blueprints import register_blueprints
    register_blueprints(app)
except ImportError:
    pass  # Blueprints not yet installed, use legacy routes

# Configuration - SECURITY: Default to localhost only
# Use --host 0.0.0.0 explicitly to expose to network (requires --password)
CONFIG = {
    'auth_enabled': False,
    'password': None,  # Set via --password or environment
    'host': '127.0.0.1',  # SECURE DEFAULT: localhost only
    'port': 8080,
}


# Security headers middleware
@app.after_request
def add_security_headers(response):
    """Add security headers to all responses"""
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'SAMEORIGIN'
    response.headers['X-XSS-Protection'] = '1; mode=block'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    # CSP - allow Leaflet maps and external resources needed for the app
    response.headers['Content-Security-Policy'] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' https://unpkg.com; "
        "style-src 'self' 'unsafe-inline' https://unpkg.com; "
        "img-src 'self' data: https://*.basemaps.cartocdn.com https://*.tile.openstreetmap.org; "
        "connect-src 'self' https://*.basemaps.cartocdn.com https://*.tile.openstreetmap.org; "
        "font-src 'self' data:"
    )
    return response

# CPU stats for delta calculation
_last_cpu = None

# PID file for tracking
WEB_PID_FILE = Path('/tmp/meshtasticd-web.pid')


def cleanup_processes():
    """Kill any lingering subprocesses and close connections gracefully"""
    global _shutdown_flag, _meshtastic_mgr, _node_monitor
    _shutdown_flag = True

    # Close meshtastic connection manager gracefully
    try:
        if _meshtastic_mgr is not None:
            _meshtastic_mgr.close()
            _meshtastic_mgr = None
    except Exception:
        pass

    # Close node monitor gracefully
    try:
        if _node_monitor is not None:
            _node_monitor.disconnect()
            _node_monitor = None
    except Exception:
        pass

    # Terminate subprocesses
    for proc in _running_processes[:]:
        try:
            if proc.poll() is None:  # Still running
                proc.terminate()
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    proc.kill()
        except Exception:
            pass
    _running_processes.clear()

    # Clean up PID file
    try:
        if WEB_PID_FILE.exists():
            WEB_PID_FILE.unlink()
    except Exception:
        pass

    print("MeshForge Web UI shutdown complete.")


def signal_handler(signum, frame):
    """Handle shutdown signals"""
    print(f"\nReceived signal {signum}, shutting down...")
    cleanup_processes()
    sys.exit(0)


def check_port_available(host: str, port: int) -> tuple:
    """
    Check if a port is available for binding.

    Returns:
        (is_available, process_info) - process_info is populated if port is in use
    """
    # First check if we can bind to the port
    test_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    test_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        test_socket.bind((host, port))
        test_socket.close()
        return (True, None)
    except OSError as e:
        test_socket.close()
        # Port is in use - try to identify what's using it
        process_info = None
        try:
            # Try lsof to identify the process
            result = subprocess.run(
                ['lsof', '-i', f':{port}', '-t'],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0 and result.stdout.strip():
                pids = result.stdout.strip().split('\n')
                # Get process name for first PID
                pid = pids[0]
                ps_result = subprocess.run(
                    ['ps', '-p', pid, '-o', 'comm='],
                    capture_output=True, text=True, timeout=5
                )
                if ps_result.returncode == 0:
                    proc_name = ps_result.stdout.strip()
                    process_info = f"{proc_name} (PID: {pid})"
        except Exception:
            pass

        return (False, process_info)


def find_available_port(host: str, preferred_port: int, max_tries: int = 10) -> int:
    """
    Find an available port, starting with the preferred port.

    Returns:
        Available port number, or 0 if none found
    """
    for offset in range(max_tries):
        port = preferred_port + offset
        is_available, _ = check_port_available(host, port)
        if is_available:
            return port
    return 0


def run_subprocess(cmd, **kwargs):
    """Run a subprocess and track it for cleanup"""
    global _shutdown_flag
    if _shutdown_flag:
        return None

    # Set defaults for safety
    kwargs.setdefault('capture_output', True)
    kwargs.setdefault('text', True)

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE if kwargs.get('capture_output') else None,
            stderr=subprocess.PIPE if kwargs.get('capture_output') else None,
            text=kwargs.get('text', True)
        )
        _running_processes.append(proc)

        timeout = kwargs.get('timeout', 30)
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
            result = subprocess.CompletedProcess(
                cmd, proc.returncode, stdout or '', stderr or ''
            )
        except subprocess.TimeoutExpired:
            proc.kill()
            stdout, stderr = proc.communicate()
            raise
        finally:
            if proc in _running_processes:
                _running_processes.remove(proc)

        return result
    except Exception as e:
        # Clean up on error
        try:
            if proc in _running_processes:
                _running_processes.remove(proc)
        except (NameError, ValueError):
            pass
        raise


# Register cleanup handlers
atexit.register(cleanup_processes)

# Only register signal handlers in main thread (avoids error when imported from blueprints)
try:
    if threading.current_thread() is threading.main_thread():
        signal.signal(signal.SIGTERM, signal_handler)
        signal.signal(signal.SIGINT, signal_handler)
except (ValueError, RuntimeError):
    pass  # Signal handlers already set or not in main thread


# ============================================================================
# Authentication
# ============================================================================

def login_required(f):
    """Decorator for routes that require authentication"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if CONFIG['auth_enabled'] and not session.get('authenticated'):
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        # Use constant-time comparison to prevent timing attacks
        user_password = request.form.get('password', '')
        stored_password = CONFIG['password'] or ''
        # Both must be strings for compare_digest
        if secrets.compare_digest(user_password, stored_password):
            session['authenticated'] = True
            return redirect(url_for('index'))
        return render_template('login.html', error="Invalid password")
    return render_template('login.html', error=None)


@app.route('/logout')
def logout():
    session.pop('authenticated', None)
    return redirect(url_for('login'))


# ============================================================================
# Utility Functions
# ============================================================================

def validate_config_name(config_name):
    """
    Validate config filename to prevent path traversal attacks.
    Returns (is_valid, error_message).
    Only allows alphanumeric, hyphen, underscore, and dot characters.
    Must end with .yaml or .yml extension.
    """
    if not config_name:
        return False, "Config name is required"

    # Block any path separators or parent directory references
    if '/' in config_name or '\\' in config_name or '..' in config_name:
        return False, "Invalid config name: path separators not allowed"

    # Only allow safe characters: alphanumeric, hyphen, underscore, dot
    import re
    if not re.match(r'^[a-zA-Z0-9_.-]+$', config_name):
        return False, "Invalid config name: only alphanumeric, hyphen, underscore, and dot allowed"

    # Must have valid extension
    if not (config_name.endswith('.yaml') or config_name.endswith('.yml')):
        return False, "Invalid config name: must end with .yaml or .yml"

    # Prevent hidden files
    if config_name.startswith('.'):
        return False, "Invalid config name: hidden files not allowed"

    return True, None


def find_meshtastic_cli():
    """Find meshtastic CLI path - uses centralized utils.cli"""
    try:
        from utils.cli import find_meshtastic_cli as _find_cli
        return _find_cli()
    except ImportError:
        # Fallback if utils not available
        import shutil
        return shutil.which('meshtastic')


def check_service_status():
    """Check meshtasticd service status using centralized checker"""
    # Use centralized service checker if available
    if _check_service:
        status = _check_service('meshtasticd')
        return status.available, status.message

    # Fallback to manual checks if centralized checker not available
    is_running = False
    status_detail = "Stopped"

    # Method 1: systemctl
    try:
        result = subprocess.run(['systemctl', 'is-active', 'meshtasticd'],
                               capture_output=True, text=True, timeout=5)
        if result.stdout.strip() == 'active':
            is_running = True
            status_detail = "Running (systemd)"
    except Exception:
        pass

    # Method 2: pgrep
    if not is_running:
        try:
            result = subprocess.run(['pgrep', '-f', 'meshtasticd'],
                                   capture_output=True, text=True, timeout=5)
            if result.returncode == 0 and result.stdout.strip():
                is_running = True
                status_detail = "Running (process)"
        except Exception:
            pass

    # Method 3: TCP port
    if not is_running:
        sock = None
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(1.0)
            if sock.connect_ex(('localhost', 4403)) == 0:
                is_running = True
                status_detail = "Running (TCP 4403)"
        except Exception:
            pass
        finally:
            if sock:
                try:
                    sock.close()
                except Exception:
                    pass

    return is_running, status_detail


def get_system_stats():
    """Get system statistics"""
    global _last_cpu
    stats = {}

    # CPU usage
    try:
        with open('/proc/stat', 'r') as f:
            line = f.readline()
        cpu_vals = [int(x) for x in line.split()[1:8]]
        idle = cpu_vals[3]
        total = sum(cpu_vals)
        if _last_cpu:
            diff_idle = idle - _last_cpu[0]
            diff_total = total - _last_cpu[1]
            stats['cpu_percent'] = round(100 * (1 - diff_idle / diff_total), 1) if diff_total > 0 else 0
        else:
            stats['cpu_percent'] = 0
        _last_cpu = (idle, total)
    except Exception:
        stats['cpu_percent'] = 0

    # Memory usage
    try:
        with open('/proc/meminfo', 'r') as f:
            lines = f.readlines()
        mem_info = {}
        for line in lines:
            parts = line.split(':')
            if len(parts) == 2:
                key = parts[0].strip()
                val = int(parts[1].split()[0])
                mem_info[key] = val
        total = mem_info.get('MemTotal', 1)
        avail = mem_info.get('MemAvailable', mem_info.get('MemFree', 0))
        used = total - avail
        stats['mem_percent'] = round(100 * used / total, 1) if total > 0 else 0
        stats['mem_used_mb'] = round(used / 1024)
        stats['mem_total_mb'] = round(total / 1024)
    except Exception:
        stats['mem_percent'] = 0
        stats['mem_used_mb'] = 0
        stats['mem_total_mb'] = 0

    # Disk usage
    try:
        stat = os.statvfs('/')
        total = stat.f_blocks * stat.f_frsize
        free = stat.f_bfree * stat.f_frsize
        used = total - free
        stats['disk_percent'] = round(100 * used / total, 1) if total > 0 else 0
        stats['disk_used_gb'] = round(used / (1024**3), 1)
        stats['disk_total_gb'] = round(total / (1024**3), 1)
    except Exception:
        stats['disk_percent'] = 0
        stats['disk_used_gb'] = 0
        stats['disk_total_gb'] = 0

    # Temperature
    try:
        temp = None
        temp_file = Path('/sys/class/thermal/thermal_zone0/temp')
        if temp_file.exists():
            temp = int(temp_file.read_text().strip()) / 1000
        if temp is None:
            result = subprocess.run(['vcgencmd', 'measure_temp'],
                                   capture_output=True, text=True, timeout=5)
            if result.returncode == 0 and 'temp=' in result.stdout:
                temp_parts = result.stdout.split('=')
                if len(temp_parts) >= 2:
                    temp_str = temp_parts[1].replace("'C", "").strip()
                    try:
                        temp = float(temp_str)
                    except ValueError:
                        temp = None
        stats['temperature'] = round(temp, 1) if temp else None
    except Exception:
        stats['temperature'] = None

    # Uptime
    try:
        with open('/proc/uptime', 'r') as f:
            uptime_sec = float(f.read().split()[0])
        days = int(uptime_sec // 86400)
        hours = int((uptime_sec % 86400) // 3600)
        mins = int((uptime_sec % 3600) // 60)
        if days > 0:
            stats['uptime'] = f"{days}d {hours}h {mins}m"
        elif hours > 0:
            stats['uptime'] = f"{hours}h {mins}m"
        else:
            stats['uptime'] = f"{mins}m"
    except Exception:
        stats['uptime'] = "--"

    return stats


def get_service_logs(lines=50):
    """Get recent service logs"""
    try:
        result = subprocess.run(
            ['journalctl', '-u', 'meshtasticd', '-n', str(lines), '--no-pager'],
            capture_output=True, text=True, timeout=10
        )
        return result.stdout
    except Exception as e:
        return f"Error fetching logs: {e}"


# Radio info cache
_radio_cache = {'data': None, 'timestamp': 0}
_RADIO_CACHE_TTL = 30  # seconds


def get_radio_info(use_cache=True):
    """Get radio info from meshtastic CLI with caching"""
    import time

    # Return cached data if fresh
    if use_cache and _radio_cache['data']:
        age = time.time() - _radio_cache['timestamp']
        if age < _RADIO_CACHE_TTL:
            return _radio_cache['data']

    cli = find_meshtastic_cli()
    if not cli:
        return {'error': 'Meshtastic CLI not found. Install with: pipx install meshtastic'}

    # Check if port is reachable first (quick check)
    sock = None
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(3.0)
        result = sock.connect_ex(('localhost', 4403))
        if result != 0:
            return {'error': 'meshtasticd not running (port 4403 closed)'}
    except Exception:
        return {'error': 'Cannot check meshtasticd port 4403'}
    finally:
        if sock:
            try:
                sock.close()
            except Exception:
                pass

    try:
        # Increased timeout to 30 seconds - CLI can be slow
        result = run_subprocess(
            [cli, '--host', 'localhost', '--info'],
            timeout=30
        )
        if result is None:  # Shutdown in progress
            return {'error': 'Server shutting down'}
        if result.returncode == 0:
            output = result.stdout
            info = {}

            # Parse JSON fields
            hw_match = re.search(r'"hwModel":\s*"([^"]+)"', output)
            if hw_match:
                info['hardware'] = hw_match.group(1)

            fw_match = re.search(r'"firmwareVersion":\s*"([^"]+)"', output)
            if fw_match:
                info['firmware'] = fw_match.group(1)

            region_match = re.search(r'"region":\s*"([^"]+)"', output)
            if region_match:
                info['region'] = region_match.group(1)

            name_match = re.search(r'"longName":\s*"([^"]+)"', output)
            if name_match:
                info['name'] = name_match.group(1)

            id_match = re.search(r'"id":\s*"([^"]+)"', output)
            if id_match:
                info['node_id'] = id_match.group(1)

            # Cache successful result
            _radio_cache['data'] = info
            _radio_cache['timestamp'] = time.time()

            return info if info else {'error': 'No radio info found in response'}

        # Check for common errors
        stderr = result.stderr or ''
        if 'Connection refused' in stderr:
            return {'error': 'meshtasticd refused connection'}
        if 'timed out' in stderr.lower():
            return {'error': 'Radio not responding (check connection)'}

        return {'error': result.stderr or 'Failed to get radio info'}

    except subprocess.TimeoutExpired:
        return {'error': 'Radio info timeout (30s) - radio may be busy or disconnected'}
    except Exception as e:
        return {'error': f'Error: {str(e)}'}


def get_configs():
    """Get available and active configurations"""
    configs = {'available': [], 'active': [], 'main_config': None}

    meshtasticd_dir = Path('/etc/meshtasticd')
    available_d = meshtasticd_dir / 'available.d'
    config_d = meshtasticd_dir / 'config.d'
    main_config = meshtasticd_dir / 'config.yaml'

    # Check main config.yaml
    if main_config.exists():
        try:
            size = main_config.stat().st_size
            configs['main_config'] = f"config.yaml ({size} bytes)"
        except Exception:
            configs['main_config'] = "config.yaml (exists)"

    # Check available.d
    if available_d.exists():
        for f in sorted(available_d.glob('*.yaml')) + sorted(available_d.glob('*.yml')):
            configs['available'].append(f.name)

    # Check config.d
    if config_d.exists():
        for f in sorted(config_d.glob('*.yaml')) + sorted(config_d.glob('*.yml')):
            configs['active'].append(f.name)

    # If no directories exist, note that
    if not meshtasticd_dir.exists():
        configs['error'] = 'meshtasticd not installed (/etc/meshtasticd missing)'

    return configs


def detect_hardware():
    """Detect hardware and service status"""
    detected = []

    # Check meshtasticd service status with error handling
    try:
        is_running, status = check_service_status()
        if is_running:
            try:
                info = get_radio_info()
                if 'error' not in info:
                    hw = info.get('hardware', 'Connected')
                    fw = info.get('firmware', '')
                    detected.append({
                        'type': 'Active',
                        'device': 'meshtasticd',
                        'description': f"Running - {hw}" + (f" (v{fw})" if fw else "")
                    })
                else:
                    detected.append({
                        'type': 'Active',
                        'device': 'meshtasticd',
                        'description': f"Running - {status}"
                    })
            except Exception as e:
                detected.append({
                    'type': 'Active',
                    'device': 'meshtasticd',
                    'description': f"Running - {status}"
                })
        else:
            detected.append({
                'type': 'Info',
                'device': 'meshtasticd',
                'description': 'Service not running'
            })
    except Exception as e:
        detected.append({
            'type': 'Warning',
            'device': 'meshtasticd',
            'description': f'Status check failed: {str(e)}'
        })

    # Check SPI devices
    spi_devices = list(Path('/dev').glob('spidev*'))
    for dev in spi_devices:
        detected.append({
            'type': 'SPI',
            'device': dev.name,
            'description': 'SPI device available'
        })

    # Check I2C devices
    i2c_devices = list(Path('/dev').glob('i2c-*'))
    for dev in i2c_devices:
        detected.append({
            'type': 'I2C',
            'device': dev.name,
            'description': 'I2C bus available'
        })

    return detected


def get_nodes():
    """Get mesh nodes - tries connection manager first, falls back to CLI"""
    global _meshtastic_mgr

    # Try using the connection manager first (more resilient)
    if get_connection_manager is not None:
        try:
            if _meshtastic_mgr is None:
                _meshtastic_mgr = get_connection_manager()

            # Check availability first
            if not _meshtastic_mgr.is_available():
                return {'error': 'meshtasticd not running (port 4403)', 'nodes': []}

            nodes = _meshtastic_mgr.get_nodes()
            if nodes:
                return {'nodes': nodes}
            # Fall through to CLI if connection manager returns empty
        except Exception as e:
            # Log and fall through to CLI method
            pass

    # Fallback to CLI method
    cli = find_meshtastic_cli()
    if not cli:
        return {'error': 'Meshtastic CLI not found', 'nodes': []}

    # Check if port is reachable
    sock = None
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(3.0)
        if sock.connect_ex(('localhost', 4403)) != 0:
            return {'error': 'meshtasticd not running (port 4403)', 'nodes': []}
    except Exception:
        return {'error': 'Cannot connect to meshtasticd', 'nodes': []}
    finally:
        if sock:
            try:
                sock.close()
            except Exception:
                pass

    try:
        result = run_subprocess(
            [cli, '--host', 'localhost', '--nodes'],
            timeout=30
        )
        if result is None:
            return {'error': 'Server shutting down', 'nodes': []}
        if result.returncode == 0:
            output = result.stdout
            nodes = []

            # Parse node entries - look for node info patterns
            # Format: !abcd1234: User Name (SHORT)
            import re
            node_pattern = re.compile(
                r'(!?[a-fA-F0-9]{8}):\s*([^\(]+)\s*\(([^\)]+)\)'
            )

            for match in node_pattern.finditer(output):
                node_id, name, short_name = match.groups()
                nodes.append({
                    'id': node_id.strip(),
                    'name': name.strip(),
                    'short': short_name.strip()
                })

            # Also try to parse the table format if present
            # Look for lines with | separators
            lines = output.strip().split('\n')
            for line in lines:
                if '│' in line and '!' in line:
                    parts = [p.strip() for p in line.split('│')]
                    if len(parts) >= 4:
                        # Try to extract node info from table row
                        for part in parts:
                            if part.startswith('!'):
                                node_id = part
                                break
                        else:
                            continue

                        # Get other fields
                        node_data = {
                            'id': node_id,
                            'name': parts[1] if len(parts) > 1 else '',
                            'short': parts[2] if len(parts) > 2 else ''
                        }
                        # Avoid duplicates
                        if not any(n['id'] == node_id for n in nodes):
                            nodes.append(node_data)

            return {'nodes': nodes, 'raw': output}

        return {'error': result.stderr or 'Failed to get nodes', 'nodes': [], 'raw': result.stdout}

    except subprocess.TimeoutExpired:
        return {'error': 'Timeout getting nodes (30s)', 'nodes': []}
    except Exception as e:
        return {'error': str(e), 'nodes': []}


# Node monitor for full node data with positions
_node_monitor = None
_node_monitor_lock = threading.Lock()


def get_nodes_full():
    """Get detailed node info including positions using NodeMonitor"""
    global _node_monitor

    # Check if port is reachable first
    sock = None
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(3.0)
        if sock.connect_ex(('localhost', 4403)) != 0:
            return {'error': 'meshtasticd not running (port 4403)'}
    except Exception:
        return {'error': 'Cannot connect to meshtasticd'}
    finally:
        if sock:
            try:
                sock.close()
            except Exception:
                pass

    try:
        with _node_monitor_lock:
            # Import NodeMonitor
            try:
                from monitoring.node_monitor import NodeMonitor
            except ImportError:
                try:
                    from src.monitoring.node_monitor import NodeMonitor
                except ImportError:
                    return {'error': 'NodeMonitor not available'}

            # Create or reuse monitor
            if _node_monitor is None or not _node_monitor.is_connected:
                if _node_monitor:
                    try:
                        _node_monitor.disconnect()
                    except Exception:
                        pass
                _node_monitor = NodeMonitor(host='localhost', port=4403)
                if not _node_monitor.connect(timeout=10.0):
                    return {'error': 'Failed to connect to meshtasticd'}

            # Get nodes
            nodes = []
            my_node = _node_monitor.get_my_node()

            for node in _node_monitor.get_nodes():
                node_data = {
                    'id': node.node_id,
                    'name': node.long_name or node.short_name or node.node_id,
                    'short': node.short_name,
                    'hardware': node.hardware_model,
                    'role': node.role,
                    'snr': node.snr,
                    'hops': node.hops_away,
                    'via_mqtt': node.via_mqtt,
                    'is_me': node.node_id == _node_monitor.my_node_id,
                }

                # Position
                if node.position and (node.position.latitude or node.position.longitude):
                    node_data['position'] = {
                        'latitude': node.position.latitude,
                        'longitude': node.position.longitude,
                        'altitude': node.position.altitude,
                    }

                # Metrics
                if node.metrics:
                    node_data['battery'] = node.metrics.battery_level
                    node_data['voltage'] = node.metrics.voltage
                    if node.metrics.temperature:
                        node_data['temperature'] = node.metrics.temperature
                    if node.metrics.humidity:
                        node_data['humidity'] = node.metrics.humidity

                # Last heard
                if node.last_heard:
                    node_data['last_heard'] = node.last_heard.isoformat()
                    # Calculate how long ago
                    delta = datetime.now() - node.last_heard
                    if delta.total_seconds() < 60:
                        node_data['last_heard_ago'] = f"{int(delta.total_seconds())}s ago"
                    elif delta.total_seconds() < 3600:
                        node_data['last_heard_ago'] = f"{int(delta.total_seconds() / 60)}m ago"
                    elif delta.total_seconds() < 86400:
                        node_data['last_heard_ago'] = f"{int(delta.total_seconds() / 3600)}h ago"
                    else:
                        node_data['last_heard_ago'] = f"{int(delta.total_seconds() / 86400)}d ago"

                nodes.append(node_data)

            # Try to add RNS nodes from gateway
            rns_nodes_added = 0
            try:
                from gateway.node_tracker import UnifiedNodeTracker
                # Check if there's a running tracker instance we can query
                # This is a singleton-ish pattern - try to get cached instance
                tracker_file = '/tmp/meshforge_rns_nodes.json'
                import json
                import os
                if os.path.exists(tracker_file):
                    with open(tracker_file) as f:
                        rns_data = json.load(f)
                        for rnode in rns_data.get('nodes', []):
                            # Only add if not already in list (by matching name or RNS hash)
                            existing_ids = {n.get('id') for n in nodes}
                            if rnode.get('rns_hash') and rnode.get('rns_hash') not in existing_ids:
                                node_data = {
                                    'id': rnode.get('rns_hash', '')[:16],
                                    'name': rnode.get('name', 'RNS Node'),
                                    'short': rnode.get('short_name', 'RNS'),
                                    'hardware': 'RNS',
                                    'network': 'rns',
                                    'is_me': False,
                                }
                                if rnode.get('position'):
                                    pos = rnode['position']
                                    if pos.get('latitude') and pos.get('longitude'):
                                        node_data['position'] = {
                                            'latitude': pos['latitude'],
                                            'longitude': pos['longitude'],
                                            'altitude': pos.get('altitude', 0),
                                        }
                                if rnode.get('last_seen'):
                                    node_data['last_heard'] = rnode['last_seen']
                                nodes.append(node_data)
                                rns_nodes_added += 1
            except Exception as e:
                logger.debug(f"Could not load RNS nodes: {e}")

            # Mark meshtastic nodes with network type
            for node in nodes:
                if 'network' not in node:
                    node['network'] = 'meshtastic'

            # Count nodes with positions
            nodes_with_position = sum(1 for n in nodes if 'position' in n)

            return {
                'nodes': nodes,
                'my_node_id': _node_monitor.my_node_id,
                'total_nodes': len(nodes),
                'nodes_with_position': nodes_with_position,
                'rns_nodes': rns_nodes_added,
            }

    except Exception as e:
        return {'error': f'Error getting nodes: {str(e)}'}


def send_mesh_message(text, destination=None):
    """Send a message to the mesh"""
    cli = find_meshtastic_cli()
    if not cli:
        return {'error': 'Meshtastic CLI not found'}

    if not text or not text.strip():
        return {'error': 'Message cannot be empty'}

    # Validate message length (Meshtastic limit ~230 bytes)
    # UTF-8 can use multiple bytes per char, so check both
    text = text.strip()
    if len(text) > 230:
        return {'error': 'Message too long (max 230 characters)'}
    if len(text.encode('utf-8')) > 230:
        return {'error': 'Message too long (max 230 bytes, unicode chars count as more)'}

    # Validate destination if provided (Meshtastic node IDs are hex with optional ! prefix)
    if destination:
        destination = str(destination).strip()
        # Node IDs: !abc123def or abc123def (hex)
        if not re.match(r'^!?[0-9a-fA-F]{1,16}$', destination):
            return {'error': 'Invalid destination: must be hex node ID (e.g., !abc123 or abc123)'}

    # Check if port is reachable
    sock = None
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(3.0)
        if sock.connect_ex(('localhost', 4403)) != 0:
            return {'error': 'meshtasticd not running'}
    except Exception:
        return {'error': 'Cannot connect to meshtasticd'}
    finally:
        if sock:
            try:
                sock.close()
            except Exception:
                pass

    try:
        cmd = [cli, '--host', 'localhost', '--sendtext', text]
        if destination:
            cmd.extend(['--dest', destination])

        result = run_subprocess(cmd, timeout=30)

        if result is None:
            return {'error': 'Server shutting down'}
        if result.returncode == 0:
            return {'success': True, 'message': 'Message sent'}
        return {'error': result.stderr or 'Failed to send message'}

    except subprocess.TimeoutExpired:
        return {'error': 'Timeout sending message'}
    except Exception as e:
        return {'error': str(e)}





# ============================================================================
# Main Routes
# ============================================================================

@app.route('/favicon.ico')
def favicon():
    """Return empty favicon to avoid 404"""
    return '', 204


@app.route('/')
@login_required
def index():
    from __version__ import __version__
    return render_template(
        'main.html',
        version=__version__,
        auth_enabled=CONFIG['auth_enabled']
    )


# ============================================================================
# Main Entry Point
# ============================================================================

def get_web_pid():
    """Get running web UI PID if exists"""
    if WEB_PID_FILE.exists():
        try:
            pid = int(WEB_PID_FILE.read_text().strip())
            os.kill(pid, 0)  # Check if running
            return pid
        except (ValueError, ProcessLookupError):
            try:
                WEB_PID_FILE.unlink(missing_ok=True)
            except PermissionError:
                pass
        except PermissionError:
            return pid  # Can't signal, but exists
    return None


def stop_web_ui():
    """Stop running web UI"""
    pid = get_web_pid()
    if pid:
        try:
            os.kill(pid, signal.SIGTERM)
            print(f"Sent SIGTERM to Web UI (PID: {pid})")
            import time
            time.sleep(1)
            try:
                os.kill(pid, 0)
                os.kill(pid, signal.SIGKILL)
                print("Process killed with SIGKILL")
            except ProcessLookupError:
                pass
            try:
                WEB_PID_FILE.unlink(missing_ok=True)
            except PermissionError:
                pass
            print("Web UI stopped")
            return True
        except ProcessLookupError:
            print("Process already stopped")
            try:
                WEB_PID_FILE.unlink(missing_ok=True)
            except PermissionError:
                pass
            return True
        except PermissionError:
            print("Permission denied. Try with sudo")
            return False
    else:
        print("Web UI is not running")
        return True


def main():
    # Get defaults from environment variables
    default_port = int(os.environ.get('MESHTASTICD_WEB_PORT', 8880))
    # SECURITY: Default to localhost, require explicit --host 0.0.0.0 for network access
    default_host = os.environ.get('MESHTASTICD_WEB_HOST', '127.0.0.1')

    parser = argparse.ArgumentParser(
        description='Meshtasticd Manager - Web UI',
        epilog='''
Examples:
  sudo python3 src/main_web.py                              # Localhost only (secure)
  sudo python3 src/main_web.py --host 0.0.0.0 -P secret     # Network access with auth
  sudo python3 src/main_web.py --port 9000                  # Custom port
  sudo python3 src/main_web.py --stop                       # Stop running instance

SECURITY NOTE:
  By default, binds to localhost (127.0.0.1) only.
  To expose to network, use --host 0.0.0.0 WITH --password.

Environment variables:
  MESHTASTICD_WEB_PORT=9000      # Set default port
  MESHTASTICD_WEB_PASSWORD=xxx   # Enable authentication (required for network access)
  MESHTASTICD_WEB_HOST=0.0.0.0   # Set bind address
'''
    )
    parser.add_argument('--host', default=default_host,
                        help=f'Host to bind to (default: {default_host}, env: MESHTASTICD_WEB_HOST)')
    parser.add_argument('--port', '-p', type=int, default=default_port,
                        help=f'Port to listen on (default: {default_port}, env: MESHTASTICD_WEB_PORT)')
    parser.add_argument('--password', '-P',
                        help='Enable authentication with this password (env: MESHTASTICD_WEB_PASSWORD)')
    parser.add_argument('--debug', action='store_true',
                        help='Enable debug mode')
    parser.add_argument('--stop', action='store_true',
                        help='Stop running web UI instance')
    parser.add_argument('--status', action='store_true',
                        help='Check if web UI is running')
    parser.add_argument('--auto-port', action='store_true',
                        help='Automatically find an available port if default is in use')
    parser.add_argument('--list-ports', action='store_true',
                        help='List what processes are using common ports and exit')
    args = parser.parse_args()

    # Handle --list-ports
    if args.list_ports:
        print("Checking common ports...")
        ports_to_check = [8080, 8081, 4403, 8880, 5000, 9000]
        for p in ports_to_check:
            is_available, process_info = check_port_available(args.host, p)
            if is_available:
                print(f"  Port {p}: Available")
            else:
                print(f"  Port {p}: In use by {process_info or 'unknown process'}")
        sys.exit(0)

    # Handle --stop
    if args.stop:
        sys.exit(0 if stop_web_ui() else 1)

    # Handle --status
    if args.status:
        pid = get_web_pid()
        if pid:
            print(f"Web UI is running (PID: {pid})")
            sys.exit(0)
        else:
            print("Web UI is not running")
            sys.exit(1)

    # Check if already running
    existing_pid = get_web_pid()
    if existing_pid:
        print(f"Web UI already running (PID: {existing_pid})")
        print("Stop it first with: sudo python3 src/main_web.py --stop")
        sys.exit(1)

    # Check if port is available
    actual_port = args.port
    is_available, process_info = check_port_available(args.host, actual_port)
    if not is_available:
        if args.auto_port:
            # Try to find an available port
            print(f"Port {actual_port} is in use, searching for available port...")
            new_port = find_available_port(args.host, actual_port + 1, max_tries=20)
            if new_port:
                print(f"Found available port: {new_port}")
                actual_port = new_port
            else:
                print(f"ERROR: Could not find available port in range {actual_port+1}-{actual_port+20}")
                sys.exit(1)
        else:
            print()
            print("=" * 60)
            print(f"ERROR: Port {actual_port} is already in use")
            print("=" * 60)
            if process_info:
                print(f"Process using port: {process_info}")
            else:
                print("Could not identify process using the port.")
                print(f"Check with: sudo lsof -i :{actual_port}")
            print()

            # Known services that commonly use certain ports
            known_services = {
                8080: "AREDN web UI, HamClock API, or other web services",
                4403: "meshtasticd TCP interface",
                8081: "HamClock live port",
            }
            if actual_port in known_services:
                print(f"Note: Port {actual_port} is commonly used by: {known_services[actual_port]}")
                print()

            # Suggest alternatives
            print("Options:")
            print(f"  --port 9000      Use a specific port")
            print(f"  --auto-port      Auto-find an available port")
            print(f"  --list-ports     Show what's using common ports")
            alt_port = find_available_port(args.host, actual_port + 1, max_tries=10)
            if alt_port:
                print()
                print(f"Suggested: sudo python3 src/main_web.py --port {alt_port}")
            print("=" * 60)
            sys.exit(1)

    # Check root
    if os.geteuid() != 0:
        print("=" * 60)
        print("WARNING: Not running as root")
        print("=" * 60)
        print("Some features (service control) require root privileges.")
        print("Run with: sudo python3 src/main_web.py")
        print()

    # Configure authentication
    if args.password:
        CONFIG['auth_enabled'] = True
        CONFIG['password'] = args.password
        print("Authentication enabled")
    elif os.environ.get('MESHTASTICD_WEB_PASSWORD'):
        CONFIG['auth_enabled'] = True
        CONFIG['password'] = os.environ.get('MESHTASTICD_WEB_PASSWORD')
        print("Authentication enabled (from environment)")

    # SECURITY: Warn if exposing to network without authentication
    if args.host in ('0.0.0.0', '::') and not CONFIG['auth_enabled']:
        print()
        print("=" * 70)
        print("⚠️  SECURITY WARNING: Network exposure without authentication!")
        print("=" * 70)
        print("You are binding to all interfaces without a password.")
        print("Anyone on your network can access and control meshtasticd.")
        print()
        print("Recommended: Add authentication with --password <secret>")
        print("  Example: sudo python3 src/main_web.py --host 0.0.0.0 -P mysecret")
        print()
        print("Or use localhost only (default):")
        print("  Example: sudo python3 src/main_web.py")
        print("=" * 70)
        print()
        # Give user 5 seconds to cancel
        print("Starting in 5 seconds... (Ctrl+C to cancel)")
        import time
        try:
            time.sleep(5)
        except KeyboardInterrupt:
            print("\nAborted.")
            sys.exit(1)

    # Get local IP for display
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('8.8.8.8', 80))
        local_ip = s.getsockname()[0]
        s.close()
    except Exception:
        local_ip = '127.0.0.1'

    print("=" * 60)
    print("Meshtasticd Manager - Web UI")
    print("=" * 60)
    print()
    print(f"Access the web interface at:")
    print(f"  http://localhost:{actual_port}/")
    print(f"  http://{local_ip}:{actual_port}/")
    print()
    if CONFIG['auth_enabled']:
        print("Authentication: ENABLED")
    else:
        print("Authentication: DISABLED (use --password to enable)")
    print()
    print("Press Ctrl+C to stop")
    print("=" * 60)

    # Write PID file
    try:
        WEB_PID_FILE.write_text(str(os.getpid()))
    except Exception as e:
        print(f"Warning: Could not write PID file: {e}")

    try:
        app.run(
            host=args.host,
            port=actual_port,
            debug=args.debug,
            threaded=True,
            use_reloader=False  # Prevent duplicate processes
        )
    finally:
        # Clean up on exit
        cleanup_processes()


if __name__ == '__main__':
    main()
