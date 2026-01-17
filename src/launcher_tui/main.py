#!/usr/bin/env python3
"""
MeshForge Launcher - raspi-config Style TUI

A whiptail/dialog based launcher that works:
- Over SSH (no display required)
- With GTK when display available
- On any terminal

Uses whiptail (Debian/Ubuntu default) with dialog fallback.
Falls back to basic terminal menu if neither available.
"""

import os
import sys
import subprocess
from pathlib import Path
from typing import Optional, List

# Ensure src directory is in path for imports when run directly
_src_dir = Path(__file__).parent.parent
if str(_src_dir) not in sys.path:
    sys.path.insert(0, str(_src_dir))

# Ensure launcher_tui directory is in path for direct backend import
# This avoids the RuntimeWarning when run with python -m
_launcher_dir = Path(__file__).parent
if str(_launcher_dir) not in sys.path:
    sys.path.insert(0, str(_launcher_dir))

# Import version
try:
    from __version__ import __version__
except ImportError:
    __version__ = "0.4.6-beta"

# Import centralized path utility
try:
    from utils.paths import get_real_user_home
except ImportError:
    def get_real_user_home() -> Path:
        sudo_user = os.environ.get('SUDO_USER')
        if sudo_user and sudo_user != 'root':
            return Path(f'/home/{sudo_user}')
        return Path.home()

# Import dialog backend directly (not through package namespace)
from backend import DialogBackend

# Import mixins to reduce file size
from rf_tools_mixin import RFToolsMixin
from channel_config_mixin import ChannelConfigMixin
from ai_tools_mixin import AIToolsMixin
from meshtasticd_config_mixin import MeshtasticdConfigMixin
from site_planner_mixin import SitePlannerMixin


class MeshForgeLauncher(
    RFToolsMixin,
    ChannelConfigMixin,
    AIToolsMixin,
    MeshtasticdConfigMixin,
    SitePlannerMixin
):
    """MeshForge launcher with raspi-config style interface."""

    def __init__(self):
        self.dialog = DialogBackend()
        self.src_dir = Path(__file__).parent.parent  # src/ directory
        self.env = self._detect_environment()

    def _detect_environment(self) -> dict:
        """Detect the current environment."""
        env = {
            'has_display': False,
            'display_type': None,
            'is_ssh': False,
            'has_gtk': False,
            'is_root': os.geteuid() == 0,
        }

        # Check for display
        display = os.environ.get('DISPLAY')
        wayland = os.environ.get('WAYLAND_DISPLAY')
        if display or wayland:
            env['has_display'] = True
            env['display_type'] = 'Wayland' if wayland else 'X11'

        # Check for SSH
        if os.environ.get('SSH_CLIENT') or os.environ.get('SSH_TTY'):
            env['is_ssh'] = True

        # Check for GTK4
        try:
            import gi
            gi.require_version('Gtk', '4.0')
            gi.require_version('Adw', '1')
            from gi.repository import Gtk, Adw
            env['has_gtk'] = True
        except (ImportError, ValueError):
            pass

        return env

    def run(self):
        """Run the launcher."""
        if not self.env['is_root']:
            print("\nError: MeshForge requires root/sudo privileges")
            print("Please run: sudo python3 src/launcher_tui/main.py")
            sys.exit(1)

        if not self.dialog.available:
            # Fallback to basic launcher
            print("whiptail/dialog not available, using basic launcher...")
            self._run_basic_launcher()
            return

        self._run_main_menu()

    def _run_main_menu(self):
        """Display the main menu."""
        while True:
            # Build dynamic choices based on environment
            choices = []

            # Interfaces section
            if self.env['has_display'] and self.env['has_gtk']:
                choices.append(("gtk", "GTK4 Desktop Interface"))
            choices.append(("cli", "Rich CLI (Terminal Menu)"))
            choices.append(("web", "Web Monitor Dashboard"))

            # Tools section
            choices.append(("---", "──────────── Tools ────────────"))
            choices.append(("ai", "AI Tools"))
            choices.append(("diag", "System Diagnostics"))
            choices.append(("network", "Network Tools"))
            choices.append(("rf", "RF Tools"))
            choices.append(("site", "Site Planner"))
            choices.append(("bridge", "Start Gateway Bridge"))
            choices.append(("monitor", "Node Monitor"))
            choices.append(("nodes", "View Nodes"))
            choices.append(("messaging", "Messaging"))
            choices.append(("space", "Space Weather"))

            # Config section
            choices.append(("---", "──────────── Config ───────────"))
            choices.append(("meshtasticd", "Meshtasticd Config"))
            choices.append(("services", "Service Management"))
            choices.append(("hardware", "Hardware Detection"))
            choices.append(("settings", "Settings"))

            # System
            choices.append(("---", "──────────────────────────────"))
            choices.append(("about", "About MeshForge"))
            choices.append(("quit", "Exit"))

            # Filter out separators for whiptail
            filtered_choices = [(t, d) for t, d in choices if t != "---"]

            choice = self.dialog.menu(
                f"MeshForge v{__version__}",
                "Select an option:",
                filtered_choices
            )

            if choice is None or choice == "quit":
                break

            self._handle_choice(choice)

    def _handle_choice(self, choice: str):
        """Handle menu selection."""
        if choice == "gtk":
            self._launch_gtk()
        elif choice == "cli":
            self._launch_cli()
        elif choice == "web":
            self._launch_web()
        elif choice == "ai":
            self._ai_tools_menu()
        elif choice == "diag":
            self._diagnostics_menu()
        elif choice == "network":
            self._network_tools_menu()
        elif choice == "site":
            self._site_planner_menu()
        elif choice == "bridge":
            self._run_bridge()
        elif choice == "monitor":
            self._run_monitor()
        elif choice == "space":
            self._show_space_weather()
        elif choice == "nodes":
            self._show_nodes()
        elif choice == "messaging":
            self._messaging_menu()
        elif choice == "rf":
            self._rf_tools_menu()
        elif choice == "meshtasticd":
            self._meshtasticd_menu()
        elif choice == "services":
            self._service_menu()
        elif choice == "hardware":
            self._hardware_menu()
        elif choice == "settings":
            self._settings_menu()
        elif choice == "about":
            self._show_about()

    def _launch_gtk(self):
        """Launch GTK interface."""
        self.dialog.infobox("Launching", "Starting GTK4 Desktop Interface...")
        os.execv(sys.executable, [sys.executable, str(self.src_dir / 'main_gtk.py')])

    def _launch_cli(self):
        """Launch CLI interface."""
        self.dialog.infobox("Launching", "Starting Rich CLI...")
        os.execv(sys.executable, [sys.executable, str(self.src_dir / 'main.py')])

    def _launch_web(self):
        """Launch Web monitor."""
        self.dialog.msgbox(
            "Web Monitor",
            "Starting Web Monitor...\n\n"
            "Access at: http://localhost:5000\n\n"
            "Press Ctrl+C to stop."
        )
        os.execv(sys.executable, [sys.executable, str(self.src_dir / 'web_monitor.py')])

    # =========================================================================
    # System Diagnostics
    # =========================================================================

    def _diagnostics_menu(self):
        """System diagnostics menu."""
        while True:
            choices = [
                ("full", "Full System Diagnostic"),
                ("services", "Service Status Check"),
                ("network", "Network Connectivity"),
                ("hardware", "Hardware Interfaces"),
                ("logs", "Log Analysis"),
                ("system", "System Resources"),
                ("back", "Back"),
            ]

            choice = self.dialog.menu(
                "System Diagnostics",
                "Comprehensive system health checks:",
                choices
            )

            if choice is None or choice == "back":
                break

            if choice == "full":
                self._run_full_diagnostics()
            elif choice == "services":
                self._check_services()
            elif choice == "network":
                self._check_network()
            elif choice == "hardware":
                self._check_hardware_interfaces()
            elif choice == "logs":
                self._analyze_logs()
            elif choice == "system":
                self._check_system_resources()

    def _run_full_diagnostics(self):
        """Run full diagnostics script."""
        subprocess.run(['clear'], check=False, timeout=5)
        subprocess.run([sys.executable, str(self.src_dir / 'cli' / 'diagnose.py')], timeout=600)  # 10min max
        input("\nPress Enter to continue...")

    def _check_services(self):
        """Check service status."""
        self.dialog.infobox("Services", "Checking services...")

        services = ['meshtasticd', 'rnsd', 'lxmf.delivery']
        results = []

        for svc in services:
            try:
                result = subprocess.run(
                    ['systemctl', 'is-active', svc],
                    capture_output=True, text=True, timeout=5
                )
                status = result.stdout.strip()
                results.append(f"{svc}: {status.upper()}")
            except Exception:
                results.append(f"{svc}: UNKNOWN")

        self.dialog.msgbox("Service Status", "\n".join(results))

    def _check_network(self):
        """Check network connectivity."""
        self.dialog.infobox("Network", "Testing connectivity...")

        tests = []

        # Test meshtasticd TCP
        try:
            import socket
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(2)
            result = sock.connect_ex(('localhost', 4403))
            sock.close()
            tests.append(f"meshtasticd (4403): {'OK' if result == 0 else 'FAIL'}")
        except Exception:
            tests.append("meshtasticd (4403): ERROR")

        # Test RNS
        try:
            result = subprocess.run(
                ['rnstatus', '-j'],
                capture_output=True, text=True, timeout=5
            )
            tests.append(f"RNS Status: {'OK' if result.returncode == 0 else 'FAIL'}")
        except Exception:
            tests.append("RNS Status: NOT AVAILABLE")

        # Test internet
        try:
            import socket
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(3)
            result = sock.connect_ex(('8.8.8.8', 53))
            sock.close()
            tests.append(f"Internet (DNS): {'OK' if result == 0 else 'FAIL'}")
        except Exception:  # Error reported to user
            tests.append("Internet: ERROR")

        self.dialog.msgbox("Network Connectivity", "\n".join(tests))

    def _check_hardware_interfaces(self):
        """Check hardware interfaces."""
        self.dialog.infobox("Hardware", "Checking interfaces...")

        checks = []

        # SPI
        spi_enabled = Path('/dev/spidev0.0').exists()
        checks.append(f"SPI: {'ENABLED' if spi_enabled else 'DISABLED'}")

        # I2C
        i2c_enabled = Path('/dev/i2c-1').exists()
        checks.append(f"I2C: {'ENABLED' if i2c_enabled else 'DISABLED'}")

        # Serial
        serial_ports = list(Path('/dev').glob('ttyUSB*')) + list(Path('/dev').glob('ttyACM*'))
        checks.append(f"Serial Ports: {len(serial_ports)} found")
        for port in serial_ports[:3]:
            checks.append(f"  - {port.name}")

        # GPIO
        gpio_available = Path('/sys/class/gpio').exists()
        checks.append(f"GPIO: {'AVAILABLE' if gpio_available else 'NOT AVAILABLE'}")

        self.dialog.msgbox("Hardware Interfaces", "\n".join(checks))

    def _analyze_logs(self):
        """Analyze system logs for errors."""
        self.dialog.infobox("Logs", "Analyzing logs...")

        logs = []

        # Check meshtasticd logs
        try:
            result = subprocess.run(
                ['journalctl', '-u', 'meshtasticd', '-n', '20', '--no-pager'],
                capture_output=True, text=True, timeout=10
            )
            errors = [l for l in result.stdout.split('\n') if 'error' in l.lower()]
            logs.append(f"meshtasticd: {len(errors)} errors in last 20 lines")
        except Exception:
            logs.append("meshtasticd: Unable to read logs")

        # Check rnsd logs
        try:
            result = subprocess.run(
                ['journalctl', '-u', 'rnsd', '-n', '20', '--no-pager'],
                capture_output=True, text=True, timeout=10
            )
            errors = [l for l in result.stdout.split('\n') if 'error' in l.lower()]
            logs.append(f"rnsd: {len(errors)} errors in last 20 lines")
        except Exception:  # Error reported to user
            logs.append("rnsd: Unable to read logs")

        logs.append("")
        logs.append("For detailed logs, use:")
        logs.append("  journalctl -u meshtasticd -f")

        self.dialog.msgbox("Log Analysis", "\n".join(logs))

    def _check_system_resources(self):
        """Check system resources."""
        self.dialog.infobox("System", "Checking resources...")

        resources = []

        # CPU temperature
        try:
            with open('/sys/class/thermal/thermal_zone0/temp', 'r') as f:
                temp = int(f.read()) / 1000
                resources.append(f"CPU Temperature: {temp:.1f}°C")
        except Exception:
            resources.append("CPU Temperature: N/A")

        # Memory
        try:
            with open('/proc/meminfo', 'r') as f:
                lines = f.readlines()
                total = int([l for l in lines if 'MemTotal' in l][0].split()[1]) / 1024
                avail = int([l for l in lines if 'MemAvailable' in l][0].split()[1]) / 1024
                used_pct = (1 - avail/total) * 100
                resources.append(f"Memory: {used_pct:.0f}% used ({avail:.0f}/{total:.0f} MB)")
        except Exception:
            resources.append("Memory: N/A")

        # Disk
        try:
            result = subprocess.run(
                ['df', '-h', '/'],
                capture_output=True, text=True, timeout=5
            )
            lines = result.stdout.strip().split('\n')
            if len(lines) > 1:
                parts = lines[1].split()
                resources.append(f"Disk: {parts[4]} used ({parts[2]}/{parts[1]})")
        except Exception:
            resources.append("Disk: N/A")

        # Uptime
        try:
            with open('/proc/uptime', 'r') as f:
                uptime_secs = float(f.read().split()[0])
                days = int(uptime_secs // 86400)
                hours = int((uptime_secs % 86400) // 3600)
                resources.append(f"Uptime: {days}d {hours}h")
        except Exception:  # Error reported to user
            resources.append("Uptime: N/A")

        self.dialog.msgbox("System Resources", "\n".join(resources))

    # =========================================================================
    # Network Tools
    # =========================================================================

    def _network_tools_menu(self):
        """Network tools menu."""
        while True:
            choices = [
                ("ping", "Ping Test"),
                ("ports", "Port Scanner"),
                ("mesh", "Meshtastic Discovery"),
                ("ifaces", "Network Interfaces"),
                ("routes", "Routing Table"),
                ("conns", "Active Connections"),
                ("dns", "DNS Lookup"),
                ("back", "Back"),
            ]

            choice = self.dialog.menu(
                "Network Tools",
                "Network diagnostics and testing:",
                choices
            )

            if choice is None or choice == "back":
                break

            if choice == "ping":
                self._ping_test()
            elif choice == "ports":
                self._port_scan()
            elif choice == "mesh":
                self._meshtastic_discovery()
            elif choice == "ifaces":
                self._show_interfaces()
            elif choice == "routes":
                self._show_routes()
            elif choice == "conns":
                self._show_connections()
            elif choice == "dns":
                self._dns_lookup()

    def _ping_test(self):
        """Run ping test."""
        host = self.dialog.inputbox(
            "Ping Test",
            "Enter host to ping:",
            "8.8.8.8"
        )

        if not host:
            return

        self.dialog.infobox("Pinging", f"Pinging {host}...")

        try:
            result = subprocess.run(
                ['ping', '-c', '4', host],
                capture_output=True, text=True, timeout=15
            )

            # Parse results
            output = result.stdout
            if 'transmitted' in output:
                stats_line = [l for l in output.split('\n') if 'transmitted' in l]
                time_line = [l for l in output.split('\n') if 'rtt' in l or 'round-trip' in l]

                text = f"Ping {host}:\n\n"
                if stats_line:
                    text += stats_line[0] + "\n"
                if time_line:
                    text += time_line[0]

                self.dialog.msgbox("Ping Results", text)
            else:
                self.dialog.msgbox("Ping Failed", output[:500])

        except subprocess.TimeoutExpired:
            self.dialog.msgbox("Error", "Ping timed out")
        except Exception as e:
            self.dialog.msgbox("Error", str(e))

    def _port_scan(self):
        """Scan common ports."""
        host = self.dialog.inputbox(
            "Port Scanner",
            "Enter host to scan:",
            "localhost"
        )

        if not host:
            return

        self.dialog.infobox("Scanning", f"Scanning ports on {host}...")

        import socket
        common_ports = [
            (22, "SSH"),
            (80, "HTTP"),
            (443, "HTTPS"),
            (4403, "Meshtasticd"),
            (5000, "Flask/Web"),
            (8080, "HamClock"),
            (8082, "HamClock API"),
            (9443, "Meshtastic Web"),
        ]

        results = []
        for port, name in common_ports:
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(1)
                result = sock.connect_ex((host, port))
                sock.close()
                status = "OPEN" if result == 0 else "closed"
                results.append(f"{port:5d} {name:15s} {status}")
            except Exception:  # Error reported to user
                results.append(f"{port:5d} {name:15s} error")

        self.dialog.msgbox(f"Port Scan: {host}", "\n".join(results))

    def _meshtastic_discovery(self):
        """Discover Meshtastic devices."""
        self.dialog.infobox("Discovery", "Scanning for Meshtastic devices...")

        devices = []

        # Check TCP localhost
        import socket
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(2)
            if sock.connect_ex(('localhost', 4403)) == 0:
                devices.append("TCP: localhost:4403 (meshtasticd)")
            sock.close()
        except Exception:
            pass

        # Check serial ports
        serial_ports = list(Path('/dev').glob('ttyUSB*')) + list(Path('/dev').glob('ttyACM*'))
        for port in serial_ports:
            devices.append(f"Serial: {port}")

        # BLE hint
        devices.append("")
        devices.append("BLE devices require scanning:")
        devices.append("  meshtastic --ble-scan")

        if not devices:
            text = "No Meshtastic devices found.\n\nMake sure meshtasticd is running."
        else:
            text = "Found devices:\n\n" + "\n".join(devices)

        self.dialog.msgbox("Meshtastic Discovery", text)

    def _show_interfaces(self):
        """Show network interfaces."""
        try:
            result = subprocess.run(
                ['ip', '-br', 'addr'],
                capture_output=True, text=True, timeout=5
            )
            self.dialog.msgbox("Network Interfaces", result.stdout or "No interfaces found")
        except Exception as e:
            self.dialog.msgbox("Error", str(e))

    def _show_routes(self):
        """Show routing table."""
        try:
            result = subprocess.run(
                ['ip', 'route'],
                capture_output=True, text=True, timeout=5
            )
            self.dialog.msgbox("Routing Table", result.stdout or "No routes found")
        except Exception as e:
            self.dialog.msgbox("Error", str(e))

    def _show_connections(self):
        """Show active connections."""
        try:
            result = subprocess.run(
                ['ss', '-tuln'],
                capture_output=True, text=True, timeout=5
            )
            # Truncate for display
            output = result.stdout[:1500] if result.stdout else "No connections"
            self.dialog.msgbox("Active Connections", output)
        except Exception as e:
            self.dialog.msgbox("Error", str(e))

    def _dns_lookup(self):
        """Perform DNS lookup."""
        host = self.dialog.inputbox(
            "DNS Lookup",
            "Enter hostname to lookup:",
            "meshtastic.org"
        )

        if not host:
            return

        try:
            import socket
            results = []
            for info in socket.getaddrinfo(host, None):
                addr = info[4][0]
                if addr not in [r.split(': ')[1] for r in results if ': ' in r]:
                    family = "IPv4" if info[0] == socket.AF_INET else "IPv6"
                    results.append(f"{family}: {addr}")

            self.dialog.msgbox(f"DNS: {host}", "\n".join(results) or "No results")
        except socket.gaierror as e:
            self.dialog.msgbox("Error", f"DNS lookup failed:\n{e}")
        except Exception as e:
            self.dialog.msgbox("Error", str(e))

    def _run_bridge(self):
        """Start gateway bridge."""
        if self.dialog.yesno(
            "Gateway Bridge",
            "Start the RNS ↔ Meshtastic gateway bridge?\n\n"
            "This will bridge messages between Reticulum and Meshtastic networks.",
            default_no=True
        ):
            subprocess.run(['clear'], check=False, timeout=5)
            print("Starting Gateway Bridge...")
            print("Press Ctrl+C to stop\n")
            try:
                subprocess.run([sys.executable, str(self.src_dir / 'gateway' / 'bridge_cli.py')])  # Interactive
            except KeyboardInterrupt:
                print("\nBridge stopped.")
            input("\nPress Enter to continue...")

    def _run_monitor(self):
        """Run node monitor."""
        subprocess.run(['clear'], check=False, timeout=5)
        try:
            subprocess.run([sys.executable, str(self.src_dir / 'monitor.py')])  # Interactive - user Ctrl+C
        except KeyboardInterrupt:
            print("\nMonitor stopped.")
        input("\nPress Enter to continue...")

    def _show_space_weather(self):
        """Show space weather (uses HamClock if available, else NOAA)."""
        self.dialog.infobox("Space Weather", "Fetching space weather data...")

        try:
            # Use the commands layer with auto-fallback
            sys.path.insert(0, str(self.src_dir))
            from commands import hamclock

            # Auto-fallback: tries HamClock first, then NOAA
            result = hamclock.get_propagation_summary()

            if result.success:
                data = result.data
                source = data.get('source', 'Unknown')

                # Build display text
                lines = [
                    f"Solar Flux Index (SFI): {data.get('sfi', 'N/A')}",
                    f"Kp Index: {data.get('kp', 'N/A')}",
                    f"X-Ray Flux: {data.get('xray', 'N/A')}",
                    f"Sunspot Number: {data.get('ssn', 'N/A')}",
                    f"Geomagnetic: {data.get('geomagnetic', 'N/A')}",
                    "",
                    f"Overall Conditions: {data.get('overall', 'Unknown')}",
                ]

                # Add band conditions if available
                bands = data.get('hf_conditions', {})
                if bands:
                    lines.append("")
                    lines.append("HF Band Conditions:")
                    for band, cond in bands.items():
                        lines.append(f"  {band}: {cond}")

                # Add alerts if any
                alerts = data.get('alerts', [])
                if alerts:
                    lines.append("")
                    lines.append("Active Alerts:")
                    for alert in alerts[:2]:
                        msg = alert.get('message', '')[:60]
                        lines.append(f"  - {msg}...")

                lines.append("")
                lines.append(f"Source: {source}")

                text = "\n".join(lines)
            else:
                text = f"Could not retrieve space weather data.\n\nError: {result.message}"

            self.dialog.msgbox("Space Weather", text)

        except Exception as e:
            self.dialog.msgbox("Error", f"Failed to get space weather:\n{e}")

    def _service_menu(self):
        """Service management menu."""
        while True:
            choices = [
                ("status", "View Service Status"),
                ("meshtasticd", "Manage meshtasticd"),
                ("rnsd", "Manage rnsd"),
                ("hamclock", "Manage HamClock"),
                ("back", "Back to Main Menu"),
            ]

            choice = self.dialog.menu(
                "Service Management",
                "Manage system services:",
                choices
            )

            if choice is None or choice == "back":
                break

            if choice == "status":
                self._show_service_status()
            else:
                self._manage_service(choice)

    def _show_service_status(self):
        """Show status of all services."""
        self.dialog.infobox("Services", "Checking service status...")

        try:
            sys.path.insert(0, str(self.src_dir))
            from commands import service

            result = service.list_all()
            if result.success:
                services = result.data.get('services', {})
                lines = []
                for name, info in services.items():
                    status = "RUNNING" if info.get('running') else "STOPPED"
                    enabled = "enabled" if info.get('enabled') else "disabled"
                    lines.append(f"{name}: {status} ({enabled})")

                text = "\n".join(lines) if lines else "No services configured"
            else:
                text = f"Error: {result.message}"

            self.dialog.msgbox("Service Status", text)

        except Exception as e:
            self.dialog.msgbox("Error", f"Failed to get service status:\n{e}")

    def _manage_service(self, service_name: str):
        """Manage a specific service."""
        choices = [
            ("status", "Check Status"),
            ("start", "Start Service"),
            ("stop", "Stop Service"),
            ("restart", "Restart Service"),
            ("logs", "View Logs"),
            ("back", "Back"),
        ]

        while True:
            choice = self.dialog.menu(
                f"Manage {service_name}",
                f"Select action for {service_name}:",
                choices
            )

            if choice is None or choice == "back":
                break

            self._service_action(service_name, choice)

    def _service_action(self, service_name: str, action: str):
        """Perform service action."""
        try:
            sys.path.insert(0, str(self.src_dir))
            from commands import service

            if action == "status":
                result = service.check_status(service_name)
                text = f"Service: {service_name}\n"
                text += f"Running: {'Yes' if result.data.get('running') else 'No'}\n"
                text += f"Enabled: {'Yes' if result.data.get('enabled') else 'No'}\n"
                text += f"Status: {result.data.get('status', 'Unknown')}"
                self.dialog.msgbox(f"{service_name} Status", text)

            elif action == "start":
                self.dialog.infobox(service_name, f"Starting {service_name}...")
                result = service.start(service_name)
                self.dialog.msgbox("Result", result.message)

            elif action == "stop":
                if self.dialog.yesno("Confirm", f"Stop {service_name}?", default_no=True):
                    self.dialog.infobox(service_name, f"Stopping {service_name}...")
                    result = service.stop(service_name)
                    self.dialog.msgbox("Result", result.message)

            elif action == "restart":
                self.dialog.infobox(service_name, f"Restarting {service_name}...")
                result = service.restart(service_name)
                self.dialog.msgbox("Result", result.message)

            elif action == "logs":
                result = service.get_logs(service_name, lines=20)
                logs = result.data.get('logs', 'No logs available')
                # Truncate for display
                if len(logs) > 2000:
                    logs = logs[-2000:] + "\n...(truncated)"
                self.dialog.msgbox(f"{service_name} Logs", logs)

        except Exception as e:
            self.dialog.msgbox("Error", f"Action failed:\n{e}")

    def _hardware_menu(self):
        """Hardware detection menu."""
        self.dialog.infobox("Hardware", "Detecting hardware...")

        try:
            sys.path.insert(0, str(self.src_dir))
            from commands import hardware

            result = hardware.detect_devices()
            if result.success:
                data = result.data

                text = "=== Hardware Detection ===\n\n"

                # SPI
                spi = data.get('spi', {})
                text += f"SPI: {'Enabled' if spi.get('enabled') else 'Disabled'}\n"
                if spi.get('devices'):
                    text += f"  Devices: {', '.join(spi.get('devices', []))}\n"

                # I2C
                i2c = data.get('i2c', {})
                text += f"\nI2C: {'Enabled' if i2c.get('enabled') else 'Disabled'}\n"
                if i2c.get('devices'):
                    text += f"  Devices: {len(i2c.get('devices', []))} found\n"

                # Serial
                serial = data.get('serial', {})
                ports = serial.get('ports', [])
                text += f"\nSerial Ports: {len(ports)} found\n"
                for port in ports[:5]:  # Limit to 5
                    text += f"  - {port.get('device', 'Unknown')}\n"

                # Summary
                summary = data.get('summary', '')
                if summary:
                    text += f"\n{summary}"

                self.dialog.msgbox("Hardware Detection", text)
            else:
                self.dialog.msgbox("Error", f"Detection failed:\n{result.message}")

        except Exception as e:
            self.dialog.msgbox("Error", f"Hardware detection failed:\n{e}")

    def _settings_menu(self):
        """Settings menu."""
        choices = [
            ("connection", "Meshtastic Connection"),
            ("gateway", "Gateway Settings"),
            ("hamclock", "HamClock Settings"),
            ("back", "Back"),
        ]

        while True:
            choice = self.dialog.menu(
                "Settings",
                "Configure MeshForge:",
                choices
            )

            if choice is None or choice == "back":
                break

            if choice == "connection":
                self._configure_connection()
            elif choice == "gateway":
                self._configure_gateway()
            elif choice == "hamclock":
                self._configure_hamclock()

    def _configure_connection(self):
        """Configure Meshtastic connection."""
        choices = [
            ("localhost", "Local TCP (localhost:4403)"),
            ("serial", "Serial Port"),
            ("remote", "Remote Host"),
        ]

        choice = self.dialog.menu(
            "Meshtastic Connection",
            "Select connection type:",
            choices
        )

        if choice == "localhost":
            self.dialog.msgbox("Connection", "Connection set to localhost:4403")
        elif choice == "serial":
            port = self.dialog.inputbox("Serial Port", "Enter serial port:", "/dev/ttyUSB0")
            if port:
                self.dialog.msgbox("Connection", f"Connection set to {port}")
        elif choice == "remote":
            host = self.dialog.inputbox("Remote Host", "Enter host:port:", "192.168.1.100:4403")
            if host:
                self.dialog.msgbox("Connection", f"Connection set to {host}")

    def _configure_gateway(self):
        """Configure gateway settings."""
        self.dialog.msgbox(
            "Gateway Settings",
            "Gateway configuration is available in the full CLI.\n\n"
            "Run 'meshforge' and select Gateway from the menu."
        )

    def _configure_hamclock(self):
        """Configure HamClock settings."""
        host = self.dialog.inputbox(
            "HamClock Host",
            "Enter HamClock hostname or IP:",
            "localhost"
        )

        if host:
            port = self.dialog.inputbox(
                "HamClock API Port",
                "Enter API port (default 8082):",
                "8082"
            )

            if port:
                try:
                    sys.path.insert(0, str(self.src_dir))
                    from commands import hamclock
                    result = hamclock.configure(host, api_port=int(port))
                    self.dialog.msgbox("Result", result.message)
                except Exception as e:
                    self.dialog.msgbox("Error", f"Configuration failed:\n{e}")

    def _show_about(self):
        """Show about information."""
        text = f"""MeshForge v{__version__}
Network Operations Center

Bridges Meshtastic and Reticulum (RNS) mesh networks.

Features:
- Service management
- Hardware detection
- Space weather & propagation
- Gateway bridge (Mesh ↔ RNS)
- Node monitoring

GitHub: github.com/Nursedude/meshforge
License: MIT

Made with aloha for the mesh community
73 de WH6GXZ"""

        self.dialog.msgbox("About MeshForge", text)

    def _show_nodes(self):
        """Show connected nodes."""
        self.dialog.infobox("Nodes", "Fetching node list...")

        try:
            sys.path.insert(0, str(self.src_dir))
            from commands import meshtastic as mesh_cmd

            result = mesh_cmd.get_nodes()
            if result.success:
                nodes = result.data.get('nodes', [])
                if not nodes:
                    self.dialog.msgbox("Nodes", "No nodes found.\n\nMake sure meshtasticd is running.")
                    return

                text = f"Found {len(nodes)} nodes:\n\n"
                for node in nodes[:15]:  # Limit display
                    node_id = node.get('id', '?')
                    name = node.get('name', 'Unknown')
                    snr = node.get('snr', 'N/A')
                    last_heard = node.get('last_heard', 'N/A')
                    text += f"  {name} ({node_id})\n"
                    text += f"    SNR: {snr} | Last: {last_heard}\n"

                if len(nodes) > 15:
                    text += f"\n... and {len(nodes) - 15} more"

                self.dialog.msgbox("Mesh Nodes", text)
            else:
                self.dialog.msgbox("Error", f"Failed to get nodes:\n{result.message}")

        except Exception as e:
            self.dialog.msgbox("Error", f"Node fetch failed:\n{e}")

    def _messaging_menu(self):
        """Messaging menu."""
        choices = [
            ("view", "View Recent Messages"),
            ("send", "Send Message"),
            ("stats", "Message Statistics"),
            ("back", "Back"),
        ]

        while True:
            choice = self.dialog.menu(
                "Messaging",
                "Mesh messaging:",
                choices
            )

            if choice is None or choice == "back":
                break

            if choice == "view":
                self._view_messages()
            elif choice == "send":
                self._send_message()
            elif choice == "stats":
                self._message_stats()

    def _view_messages(self):
        """View recent messages."""
        try:
            sys.path.insert(0, str(self.src_dir))
            from commands import messaging

            result = messaging.get_messages(limit=20)
            if result.success:
                messages = result.data.get('messages', [])
                if not messages:
                    self.dialog.msgbox("Messages", "No messages yet.")
                    return

                text = ""
                for msg in messages[:10]:
                    ts = msg.get('timestamp', '')[:16]
                    from_id = msg.get('from_id', '?')
                    content = msg.get('content', '')[:40]
                    text += f"[{ts}] {from_id}\n  {content}\n\n"

                self.dialog.msgbox("Recent Messages", text)
            else:
                self.dialog.msgbox("Error", result.message)

        except Exception as e:
            self.dialog.msgbox("Error", f"Failed to load messages:\n{e}")

    def _send_message(self):
        """Send a message."""
        try:
            # Get destination
            dest = self.dialog.inputbox(
                "Send Message",
                "Destination (node ID or leave empty for broadcast):",
                ""
            )

            if dest is None:
                return

            # Get message content
            content = self.dialog.inputbox(
                "Send Message",
                "Message (max 160 chars):",
                ""
            )

            if not content:
                return

            sys.path.insert(0, str(self.src_dir))
            from commands import messaging

            self.dialog.infobox("Sending", "Sending message...")
            result = messaging.send_message(
                content=content,
                destination=dest if dest else None,
                network="auto"
            )

            self.dialog.msgbox("Result", result.message)

        except Exception as e:
            self.dialog.msgbox("Error", f"Send failed:\n{e}")

    def _message_stats(self):
        """Show message statistics."""
        try:
            sys.path.insert(0, str(self.src_dir))
            from commands import messaging

            result = messaging.get_stats()
            if result.success:
                data = result.data
                text = f"""Message Statistics:

Total Messages: {data.get('total', 0)}
Sent: {data.get('sent', 0)}
Received: {data.get('received', 0)}
Last 24h: {data.get('last_24h', 0)}

Storage: messages.db"""

                self.dialog.msgbox("Statistics", text)
            else:
                self.dialog.msgbox("Error", result.message)

        except Exception as e:
            self.dialog.msgbox("Error", f"Stats failed:\n{e}")

    def _run_basic_launcher(self):
        """Fallback basic terminal launcher."""
        # Import and run the original launcher
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "launcher",
            self.src_dir / "launcher.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.main()


def main():
    """Main entry point."""
    launcher = MeshForgeLauncher()
    launcher.run()


if __name__ == '__main__':
    main()
