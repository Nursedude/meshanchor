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
import shutil
import subprocess
import logging
from pathlib import Path
from typing import Optional, List

logger = logging.getLogger(__name__)

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

# Import centralized service checker - SINGLE SOURCE OF TRUTH for service status
# See: utils/service_check.py and .claude/foundations/install_reliability_triage.md
try:
    from utils.service_check import check_service, check_port, ServiceState
except ImportError:
    # Fallback if running standalone - will use direct systemctl
    check_service = None
    check_port = None
    ServiceState = None

# Import dialog backend directly (not through package namespace)
from backend import DialogBackend

# Import mixins to reduce file size
from rf_tools_mixin import RFToolsMixin
from channel_config_mixin import ChannelConfigMixin
from ai_tools_mixin import AIToolsMixin
from meshtasticd_config_mixin import MeshtasticdConfigMixin
from site_planner_mixin import SitePlannerMixin
from service_discovery_mixin import ServiceDiscoveryMixin
from first_run_mixin import FirstRunMixin
from system_tools_mixin import SystemToolsMixin


class MeshForgeLauncher(
    RFToolsMixin,
    ChannelConfigMixin,
    AIToolsMixin,
    MeshtasticdConfigMixin,
    SitePlannerMixin,
    ServiceDiscoveryMixin,
    FirstRunMixin,
    SystemToolsMixin
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

        # Check for first run and offer setup wizard
        if self._check_first_run():
            self._run_first_run_wizard()

        # Check for service misconfiguration (SPI HAT with USB config)
        self._check_service_misconfig()

        self._run_main_menu()

    def _check_service_misconfig(self):
        """Check for service misconfiguration and offer to fix."""
        config_d = Path('/etc/meshtasticd/config.d')
        if not config_d.exists():
            return

        # Check what configs are active
        active_configs = list(config_d.glob('*.yaml'))
        usb_config = config_d / 'usb-serial.yaml'

        # Check for SPI configs
        spi_config_names = ['meshadv', 'waveshare', 'rak-hat', 'meshtoad', 'sx126', 'sx127', 'lora']
        has_spi_config = any(
            any(name in cfg.name.lower() for name in spi_config_names)
            for cfg in active_configs
        )

        # If SPI config exists AND usb-serial.yaml also exists, that's wrong
        if has_spi_config and usb_config.exists():
            spi_configs = [c.name for c in active_configs if any(n in c.name.lower() for n in spi_config_names)]

            msg = "CONFLICTING CONFIGURATIONS!\n\n"
            msg += "Both SPI HAT and USB configs are active:\n\n"
            msg += f"  SPI: {', '.join(spi_configs)}\n"
            msg += f"  USB: usb-serial.yaml (WRONG)\n\n"
            msg += "Remove the USB config?"

            if self.dialog.yesno("Config Conflict", msg):
                try:
                    usb_config.unlink()
                    subprocess.run(['systemctl', 'daemon-reload'], timeout=30, check=False)
                    subprocess.run(['systemctl', 'restart', 'meshtasticd'], timeout=30, check=False)
                    self.dialog.msgbox(
                        "Fixed",
                        "Removed usb-serial.yaml\n"
                        "Restarted meshtasticd\n\n"
                        "Check: systemctl status meshtasticd"
                    )
                except Exception as e:
                    self.dialog.msgbox("Error", f"Failed:\n{e}")
            return

        # Check: SPI hardware present but USB config active (wrong)
        spi_devices = list(Path('/dev').glob('spidev*'))

        has_spi = len(spi_devices) > 0

        # Only skip if no SPI hardware at all
        if not has_spi:
            return

        if not usb_config.exists():
            return

        result = subprocess.run(['which', 'meshtasticd'], capture_output=True, timeout=5)
        has_native = result.returncode == 0

        msg = "CONFIGURATION MISMATCH!\n\n"
        msg += "SPI HAT detected but USB config active.\n\n"
        msg += f"SPI: {', '.join(d.name for d in spi_devices)}\n"
        msg += "Config: usb-serial.yaml (WRONG)\n"
        if not has_native:
            msg += "Native meshtasticd: NOT INSTALLED\n"
        msg += "\nFix this now?"

        if self.dialog.yesno("Service Misconfiguration", msg):
            self._fix_spi_config(has_native)

    def _run_main_menu(self):
        """Display the main menu."""
        while True:
            # Build dynamic choices based on environment
            choices = []

            # Interfaces section
            if self.env['has_display'] and self.env['has_gtk']:
                choices.append(("gtk", "GTK4 Desktop Interface"))

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
            choices.append(("web", "Web Client (Radio Config)"))
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
        elif choice == "web":
            self._open_web_client()
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

    def _open_web_client(self):
        """Show/open meshtasticd web client for full radio configuration."""
        import socket
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            local_ip = s.getsockname()[0]
            s.close()
        except Exception:
            local_ip = "localhost"

        web_url = f"https://{local_ip}:9443"

        # Check if web server is responding
        port_ok = False
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(2)
            port_ok = sock.connect_ex((local_ip, 9443)) == 0
            sock.close()
        except Exception:
            pass

        if port_ok:
            msg = (
                f"Meshtastic Web Client is RUNNING\n\n"
                f"  URL: {web_url}\n\n"
                f"Open this in any browser on your network.\n\n"
                f"Configure your radio:\n"
                f"  Config → LoRa      Region, Preset, TX Power\n"
                f"  Config → Channels  PSK keys, channel names\n"
                f"  Config → Device    Node name, position\n\n"
                f"Also provides: messaging, node map, telemetry\n\n"
                f"Note: Accept the self-signed cert warning.\n\n"
                f"CLI shortcut: meshforge-web"
            )
        else:
            msg = (
                f"Web client NOT responding on port 9443\n\n"
                f"meshtasticd may not be running.\n\n"
                f"  Start: sudo systemctl start meshtasticd\n"
                f"  Check: sudo systemctl status meshtasticd\n"
                f"  Logs:  sudo journalctl -u meshtasticd -f"
            )

        self.dialog.msgbox("Web Client", msg)

    # =========================================================================
    # System Diagnostics
    # =========================================================================

    def _diagnostics_menu(self):
        """System diagnostics menu."""
        while True:
            choices = [
                ("full", "Full System Diagnostic"),
                ("tools", "System Tools (Full Linux CLI)"),
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
            elif choice == "tools":
                self._system_tools_menu()
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
        """Check service status using centralized service checker.

        Uses utils/service_check.py - SINGLE SOURCE OF TRUTH for service status.
        This ensures consistent status across all MeshForge UIs.
        """
        self.dialog.infobox("Services", "Checking services...")

        services = ['meshtasticd', 'rnsd', 'hamclock']
        results = []

        for svc in services:
            if check_service is not None:
                # Use centralized service checker (preferred)
                status = check_service(svc)
                state_str = status.state.value.upper() if status.state else "UNKNOWN"
                if status.available:
                    results.append(f"{svc}: {state_str} ✓")
                else:
                    results.append(f"{svc}: {state_str}")
                    if status.fix_hint:
                        results.append(f"  → {status.fix_hint}")
            else:
                # Fallback to direct systemctl (only if service_check unavailable)
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
        """Check network connectivity using centralized utilities.

        Uses utils/service_check.py for port checks - consistent with service status.
        """
        self.dialog.infobox("Network", "Testing connectivity...")

        tests = []

        # Test meshtasticd TCP using centralized port checker
        if check_port is not None:
            port_ok = check_port(4403)
            tests.append(f"meshtasticd (4403): {'OK ✓' if port_ok else 'FAIL'}")
        else:
            # Fallback to direct socket check
            try:
                import socket
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(2)
                result = sock.connect_ex(('localhost', 4403))
                sock.close()
                tests.append(f"meshtasticd (4403): {'OK' if result == 0 else 'FAIL'}")
            except Exception:
                tests.append("meshtasticd (4403): ERROR")

        # Test RNS using rnstatus command
        try:
            result = subprocess.run(
                ['rnstatus', '-j'],
                capture_output=True, text=True, timeout=5
            )
            tests.append(f"RNS Status: {'OK ✓' if result.returncode == 0 else 'FAIL'}")
        except FileNotFoundError:
            tests.append("RNS Status: NOT INSTALLED")
        except Exception:
            tests.append("RNS Status: NOT AVAILABLE")

        # Test web client (port 9443) using centralized port checker
        if check_port is not None:
            web_ok = check_port(9443)
            tests.append(f"Web Client (9443): {'OK ✓' if web_ok else 'NOT RUNNING'}")

        # Test internet connectivity
        if check_port is not None:
            inet_ok = check_port(53, host='8.8.8.8', timeout=3.0)
            tests.append(f"Internet (DNS): {'OK ✓' if inet_ok else 'FAIL'}")
        else:
            try:
                import socket
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(3)
                result = sock.connect_ex(('8.8.8.8', 53))
                sock.close()
                tests.append(f"Internet (DNS): {'OK' if result == 0 else 'FAIL'}")
            except Exception:
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
        """P4: Enhanced log viewer with service selection."""
        while True:
            choices = [
                ("summary", "Error Summary (All Services)"),
                ("meshtasticd", "View meshtasticd Logs"),
                ("rnsd", "View rnsd Logs"),
                ("syslog", "View System Log (journalctl)"),
                ("dmesg", "View Kernel Messages (dmesg)"),
                ("meshforge", "View MeshForge Logs"),
                ("live", "Live Log Follow (journalctl -f)"),
                ("boot", "Boot Messages (this boot)"),
                ("errors", "Errors Only (last hour)"),
                ("back", "Back"),
            ]

            choice = self.dialog.menu(
                "Log Viewer",
                "View and analyze system logs:",
                choices
            )

            if choice is None or choice == "back":
                break

            if choice == "summary":
                self._log_error_summary()
            elif choice == "meshtasticd":
                self._view_service_logs("meshtasticd")
            elif choice == "rnsd":
                self._view_service_logs("rnsd")
            elif choice == "syslog":
                self._view_syslog()
            elif choice == "dmesg":
                self._view_dmesg()
            elif choice == "meshforge":
                self._view_meshforge_logs()
            elif choice == "live":
                self._live_log_follow()
            elif choice == "boot":
                self._view_boot_logs()
            elif choice == "errors":
                self._view_errors_only()

    def _log_error_summary(self):
        """Show error summary for all services."""
        self.dialog.infobox("Logs", "Analyzing logs for errors...")

        services = ['meshtasticd', 'rnsd', 'lxmf.delivery']
        summary = ["Error Summary\n" + "=" * 40]

        for svc in services:
            try:
                result = subprocess.run(
                    ['journalctl', '-u', svc, '-n', '100', '--no-pager', '-p', 'err'],
                    capture_output=True, text=True, timeout=10
                )
                error_count = len([l for l in result.stdout.split('\n') if l.strip()])
                status = f"[ERRORS: {error_count}]" if error_count > 0 else "[OK]"
                summary.append(f"\n{svc}: {status}")
            except Exception:
                summary.append(f"\n{svc}: [Unable to read]")

        # Check recent errors
        summary.append("\n" + "-" * 40)
        summary.append("Recent errors (last hour):")

        try:
            result = subprocess.run(
                ['journalctl', '--since', '1 hour ago', '-p', 'err', '--no-pager', '-n', '10'],
                capture_output=True, text=True, timeout=10
            )
            if result.stdout.strip():
                for line in result.stdout.split('\n')[:8]:
                    if line.strip():
                        summary.append(f"  {line[:60]}...")
            else:
                summary.append("  No errors in last hour")
        except Exception as e:
            logger.debug("Failed to read journal errors: %s", e)
            summary.append("  Unable to read recent errors")

        self.dialog.msgbox("Log Analysis", "\n".join(summary))

    def _view_service_logs(self, service: str):
        """View logs for a specific service."""
        # Ask for number of lines
        lines = self.dialog.inputbox(
            f"{service} Logs",
            "Number of log lines to show:",
            "50"
        )

        if not lines:
            return

        try:
            lines = int(lines)
        except ValueError:
            lines = 50

        self.dialog.infobox("Loading", f"Loading {service} logs...")

        try:
            result = subprocess.run(
                ['journalctl', '-u', service, '-n', str(lines), '--no-pager'],
                capture_output=True, text=True, timeout=15
            )

            if result.stdout.strip():
                # Use scrollable textbox for long output
                subprocess.run(['clear'], check=False, timeout=5)
                print(f"=== {service} Logs (last {lines} lines) ===\n")
                print(result.stdout)
                print("\n" + "=" * 50)
                input("\nPress Enter to continue...")
            else:
                self.dialog.msgbox(f"{service} Logs", "No logs found for this service")
        except Exception as e:
            self.dialog.msgbox("Error", f"Failed to read logs: {e}")

    def _view_syslog(self):
        """View system log."""
        self.dialog.infobox("Loading", "Loading system log...")

        try:
            result = subprocess.run(
                ['journalctl', '-n', '50', '--no-pager'],
                capture_output=True, text=True, timeout=15
            )

            subprocess.run(['clear'], check=False, timeout=5)
            print("=== System Log (last 50 lines) ===\n")
            print(result.stdout)
            print("\n" + "=" * 50)
            input("\nPress Enter to continue...")
        except Exception as e:
            self.dialog.msgbox("Error", f"Failed to read syslog: {e}")

    def _view_dmesg(self):
        """View kernel messages."""
        self.dialog.infobox("Loading", "Loading kernel messages...")

        try:
            result = subprocess.run(
                ['dmesg', '--time-format=reltime'],
                capture_output=True, text=True, timeout=10
            )

            # Get last 50 lines
            lines = result.stdout.strip().split('\n')[-50:]

            subprocess.run(['clear'], check=False, timeout=5)
            print("=== Kernel Messages (dmesg, last 50 lines) ===\n")
            print('\n'.join(lines))
            print("\n" + "=" * 50)
            input("\nPress Enter to continue...")
        except Exception as e:
            self.dialog.msgbox("Error", f"Failed to read dmesg: {e}")

    def _view_meshforge_logs(self):
        """View MeshForge application logs."""
        log_dir = get_real_user_home() / ".config" / "meshforge" / "logs"

        if not log_dir.exists():
            self.dialog.msgbox("Logs", "No MeshForge logs found yet.\n\nLogs are created when you use MeshForge.")
            return

        log_files = list(log_dir.glob("*.log"))
        if not log_files:
            self.dialog.msgbox("Logs", "No log files found in:\n" + str(log_dir))
            return

        # Show most recent log
        latest_log = max(log_files, key=lambda f: f.stat().st_mtime)

        try:
            content = latest_log.read_text()
            lines = content.strip().split('\n')[-50:]  # Last 50 lines

            subprocess.run(['clear'], check=False, timeout=5)
            print(f"=== MeshForge Log: {latest_log.name} ===\n")
            print('\n'.join(lines))
            print("\n" + "=" * 50)
            input("\nPress Enter to continue...")
        except Exception as e:
            self.dialog.msgbox("Error", f"Failed to read log: {e}")

    def _live_log_follow(self):
        """Follow logs live (like tail -f)."""
        choices = [
            ("all", "All System Logs"),
            ("meshtasticd", "meshtasticd Only"),
            ("rnsd", "rnsd Only"),
            ("kernel", "Kernel Only (dmesg)"),
            ("back", "Back"),
        ]

        choice = self.dialog.menu(
            "Live Logs",
            "Select logs to follow (Ctrl+C to stop):",
            choices
        )

        if choice is None or choice == "back":
            return

        self.dialog.msgbox(
            "Live Log Follow",
            "Starting live log follow...\n\n"
            "Press Ctrl+C to stop and return to menu."
        )

        subprocess.run(['clear'], check=False, timeout=5)
        print("=== Live Log Follow (Ctrl+C to stop) ===\n")

        try:
            if choice == "all":
                subprocess.run(['journalctl', '-f', '-n', '20'], timeout=None)
            elif choice == "meshtasticd":
                subprocess.run(['journalctl', '-u', 'meshtasticd', '-f', '-n', '20'], timeout=None)
            elif choice == "rnsd":
                subprocess.run(['journalctl', '-u', 'rnsd', '-f', '-n', '20'], timeout=None)
            elif choice == "kernel":
                subprocess.run(['dmesg', '-w'], timeout=None)
        except KeyboardInterrupt:
            print("\n\nStopped.")
            input("\nPress Enter to continue...")
        except Exception as e:
            self.dialog.msgbox("Error", f"Failed: {e}")

    def _view_boot_logs(self):
        """View logs from current boot."""
        self.dialog.infobox("Loading", "Loading boot messages...")

        try:
            result = subprocess.run(
                ['journalctl', '-b', '-n', '100', '--no-pager'],
                capture_output=True, text=True, timeout=15
            )

            subprocess.run(['clear'], check=False, timeout=5)
            print("=== Boot Messages (this boot, last 100) ===\n")
            print(result.stdout)
            print("\n" + "=" * 50)
            input("\nPress Enter to continue...")
        except Exception as e:
            self.dialog.msgbox("Error", f"Failed to read boot logs: {e}")

    def _view_errors_only(self):
        """View only error-level messages."""
        self.dialog.infobox("Loading", "Scanning for errors...")

        try:
            result = subprocess.run(
                ['journalctl', '-p', 'err', '--since', '1 hour ago', '-n', '50', '--no-pager'],
                capture_output=True, text=True, timeout=15
            )

            subprocess.run(['clear'], check=False, timeout=5)
            print("=== Errors Only (last hour, priority err+) ===\n")

            if result.stdout.strip():
                print(result.stdout)
            else:
                print("No errors found in the last hour!")

            print("\n" + "=" * 50)
            print("\nLog Priority Levels:")
            print("  emerg(0) > alert(1) > crit(2) > err(3) > warning(4) > notice(5) > info(6) > debug(7)")
            input("\nPress Enter to continue...")
        except Exception as e:
            self.dialog.msgbox("Error", f"Failed to read error logs: {e}")

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
                ("discover", "Service Discovery (Auto-Scan)"),
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

            if choice == "discover":
                self._service_discovery_menu()
            elif choice == "ping":
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
                ("fix", "Fix Service Misconfiguration"),
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
            elif choice == "fix":
                self._fix_service_config()
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

    def _fix_service_config(self):
        """Detect and fix service misconfigurations."""
        self.dialog.infobox("Checking", "Detecting hardware and service configuration...")

        # Detect hardware
        spi_devices = list(Path('/dev').glob('spidev*'))
        usb_devices = list(Path('/dev').glob('ttyUSB*')) + list(Path('/dev').glob('ttyACM*'))

        has_spi = len(spi_devices) > 0
        has_usb = len(usb_devices) > 0

        # Check current service config
        try:
            result = subprocess.run(
                ['systemctl', 'cat', 'meshtasticd'],
                capture_output=True, text=True, timeout=10
            )
            service_content = result.stdout
        except Exception:
            service_content = ""

        is_placeholder = "No Daemon Needed" in service_content or "USB radios work directly" in service_content
        is_spi_pending = "Native daemon required for SPI" in service_content
        is_native = ("meshtasticd" in service_content and
                     ("/usr/bin/meshtasticd" in service_content or
                      "/usr/local/bin/meshtasticd" in service_content or
                      "ExecStart=" in service_content and "meshtasticd -c" in service_content))

        # Build status report
        lines = ["Hardware & Service Check\n" + "=" * 40]
        lines.append(f"\nHardware Detected:")
        lines.append(f"  SPI: {'Yes (' + ', '.join(d.name for d in spi_devices) + ')' if has_spi else 'No'}")
        lines.append(f"  USB: {'Yes (' + ', '.join(d.name for d in usb_devices) + ')' if has_usb else 'No'}")

        lines.append(f"\nService Configuration:")
        if is_placeholder:
            lines.append("  Type: USB Placeholder (no daemon)")
        elif is_spi_pending:
            lines.append("  Type: SPI pending (native daemon required)")
        elif is_native:
            lines.append("  Type: Native meshtasticd daemon")
        else:
            lines.append("  Type: Unknown or not configured")

        # Check for mismatch
        mismatch = False
        if has_spi and not has_usb and is_placeholder:
            mismatch = True
            lines.append("\n" + "!" * 40)
            lines.append("MISMATCH DETECTED!")
            lines.append("SPI HAT detected but USB placeholder service installed.")
            lines.append("Your SPI HAT needs the native meshtasticd daemon.")
            lines.append("!" * 40)

        self.dialog.msgbox("Configuration Check", "\n".join(lines))

        if mismatch:
            if self.dialog.yesno(
                "Fix Configuration?",
                "Would you like to install the correct service for your SPI HAT?\n\n"
                "This will:\n"
                "1. Install native meshtasticd (if not present)\n"
                "2. Create correct systemd service\n"
                "3. Configure for your SPI radio\n\n"
                "Proceed?"
            ):
                self._install_native_meshtasticd()

    def _fix_spi_config(self, has_native: bool = False):
        """Quick fix for SPI HAT with wrong USB config."""
        self.dialog.infobox("Fixing", "Removing wrong USB configuration...")

        try:
            config_dir = Path('/etc/meshtasticd')

            # Remove wrong USB config from config.d
            usb_config = config_dir / 'config.d' / 'usb-serial.yaml'
            if usb_config.exists():
                usb_config.unlink()
                self.dialog.infobox("Fixing", "Removed usb-serial.yaml from config.d/")

            # Check if config.yaml exists and is valid (has Webserver section)
            config_yaml = config_dir / 'config.yaml'
            needs_config = False
            if not config_yaml.exists():
                needs_config = True
            elif not config_yaml.read_text().strip():
                needs_config = True
            elif 'Webserver:' not in config_yaml.read_text():
                # Config exists but missing Webserver - probably corrupted
                self.dialog.msgbox(
                    "Config Warning",
                    f"Your config.yaml may be corrupted:\n{config_yaml}\n\n"
                    "It's missing the Webserver section.\n"
                    "Check: cat /etc/meshtasticd/config.yaml"
                )

            # Only create config.yaml if it doesn't exist or is empty
            if needs_config:
                config_yaml.write_text("""---
Lora:
  Module: auto

Logging:
  LogLevel: info

Webserver:
  Port: 9443
  RootPath: /usr/share/meshtasticd/web

General:
  MaxNodes: 200
  ConfigDirectory: /etc/meshtasticd/config.d/
""")
                self.dialog.infobox("Fixing", "Created minimal config.yaml")

            # NOTE: We do NOT create HAT templates - meshtasticd provides them
            # User should select from /etc/meshtasticd/available.d/

            if not has_native:
                # Offer to install native meshtasticd
                if self.dialog.yesno(
                    "Install Native Daemon?",
                    "SPI HATs require the native meshtasticd daemon.\n\n"
                    "Would you like to install it now?\n\n"
                    "(This requires internet connection)"
                ):
                    self._install_native_meshtasticd()
                else:
                    self.dialog.msgbox(
                        "Config Fixed",
                        "Wrong USB config removed.\n\n"
                        "To complete setup, install native meshtasticd:\n"
                        "  sudo apt install meshtasticd\n\n"
                        "Or run: sudo bash scripts/install_noc.sh --force-native"
                    )
            else:
                # Native daemon exists - restart service
                subprocess.run(['systemctl', 'daemon-reload'], timeout=30, check=False)
                subprocess.run(['systemctl', 'restart', 'meshtasticd'], timeout=30, check=False)

                self.dialog.msgbox(
                    "Config Fixed",
                    "Configuration corrected!\n\n"
                    "- Removed wrong USB config\n"
                    "- Restarted meshtasticd service\n\n"
                    "Check status: sudo systemctl status meshtasticd"
                )

        except Exception as e:
            self.dialog.msgbox("Error", f"Fix failed:\n{e}")

    def _install_native_meshtasticd(self):
        """Install native meshtasticd for SPI HAT."""
        self.dialog.infobox("Installing", "Installing native meshtasticd...")

        try:
            # Check if already installed
            result = subprocess.run(['which', 'meshtasticd'], capture_output=True, text=True, timeout=5)
            if result.returncode != 0:
                # Not installed - try to install
                self.dialog.infobox("Installing", "Adding Meshtastic repository...")

                # Detect OS for correct repo (matching install_noc.sh logic)
                os_repo = "Raspbian_12"  # Default for Pi
                if Path('/etc/os-release').exists():
                    os_info = {}
                    with open('/etc/os-release') as f:
                        for line in f:
                            if '=' in line:
                                key, val = line.strip().split('=', 1)
                                os_info[key] = val.strip('"')

                    os_id = os_info.get('ID', '')
                    version_id = os_info.get('VERSION_ID', '')

                    if os_id == 'raspbian':
                        os_repo = f"Raspbian_{version_id.split('.')[0]}" if version_id else "Raspbian_12"
                    elif os_id == 'debian':
                        os_repo = f"Debian_{version_id.split('.')[0]}" if version_id else "Debian_12"
                    elif os_id == 'ubuntu':
                        os_repo = f"xUbuntu_{version_id}" if version_id else "xUbuntu_24.04"

                repo_url = f"https://download.opensuse.org/repositories/network:/Meshtastic:/beta/{os_repo}/"

                # Add repo
                subprocess.run(
                    ['tee', '/etc/apt/sources.list.d/meshtastic.list'],
                    input=f"deb {repo_url} /\n",
                    text=True, timeout=30, check=False
                )

                subprocess.run([
                    'bash', '-c',
                    f'curl -fsSL {repo_url}Release.key | gpg --dearmor > /etc/apt/trusted.gpg.d/meshtastic.gpg'
                ], timeout=30, check=False)

                self.dialog.infobox("Installing", "Updating package list...")
                subprocess.run(['apt-get', 'update'], timeout=120, check=False)

                self.dialog.infobox("Installing", "Installing meshtasticd...")
                result = subprocess.run(['apt-get', 'install', '-y', 'meshtasticd'], timeout=300, capture_output=True, text=True)

                if result.returncode != 0:
                    self.dialog.msgbox("Error", f"Failed to install meshtasticd:\n{result.stderr[:500]}")
                    return

            # Find actual meshtasticd binary path
            result = subprocess.run(['which', 'meshtasticd'], capture_output=True, text=True, timeout=5)
            meshtasticd_bin = result.stdout.strip() if result.returncode == 0 else '/usr/bin/meshtasticd'

            # Ensure config directories exist (meshtasticd package should create these)
            config_dir = Path('/etc/meshtasticd')
            config_dir.mkdir(parents=True, exist_ok=True)
            (config_dir / 'available.d').mkdir(exist_ok=True)
            (config_dir / 'config.d').mkdir(exist_ok=True)
            (config_dir / 'ssl').mkdir(mode=0o700, exist_ok=True)

            # Check if meshtasticd installed a valid config.yaml
            # Only create one if missing or empty - NEVER overwrite
            config_yaml = config_dir / 'config.yaml'
            if config_yaml.exists() and 'Webserver:' in config_yaml.read_text():
                self.dialog.infobox("Installing", "Using existing config.yaml from meshtasticd package")
            elif not config_yaml.exists() or not config_yaml.read_text().strip():
                # No config or empty - create minimal one
                config_yaml.write_text("""---
Lora:
  Module: auto

Logging:
  LogLevel: info

Webserver:
  Port: 9443
  RootPath: /usr/share/meshtasticd/web

General:
  MaxNodes: 200
  ConfigDirectory: /etc/meshtasticd/config.d/
""")
                self.dialog.infobox("Installing", "Created minimal config.yaml")

            # NOTE: We do NOT create HAT templates - meshtasticd package provides them
            # User selects their HAT from /etc/meshtasticd/available.d/ via Hardware Config menu

            # Remove wrong USB config if present
            usb_config = config_dir / 'config.d' / 'usb-serial.yaml'
            if usb_config.exists():
                usb_config.unlink()
                self.dialog.infobox("Installing", "Removed incorrect USB config")

            # Create service file
            service_content = f"""[Unit]
Description=Meshtastic Daemon (Native SPI)
Documentation=https://meshtastic.org
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/etc/meshtasticd
ExecStart={meshtasticd_bin} -c /etc/meshtasticd/config.yaml
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
"""
            Path('/etc/systemd/system/meshtasticd.service').write_text(service_content)

            # Reload and enable
            subprocess.run(['systemctl', 'daemon-reload'], timeout=30, check=False)
            subprocess.run(['systemctl', 'enable', 'meshtasticd'], timeout=30, check=False)
            subprocess.run(['systemctl', 'restart', 'meshtasticd'], timeout=30, check=False)

            self.dialog.msgbox(
                "Success",
                "Native meshtasticd installed!\n\n"
                "NEXT STEP: Select your HAT config:\n"
                "  meshtasticd → Hardware Config\n\n"
                "Or manually:\n"
                "  ls /etc/meshtasticd/available.d/\n"
                "  sudo cp /etc/meshtasticd/available.d/<your-hat>.yaml \\\n"
                "         /etc/meshtasticd/config.d/\n"
                "  sudo systemctl restart meshtasticd"
            )

        except Exception as e:
            self.dialog.msgbox("Error", f"Installation failed:\n{e}")

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
        """Hardware detection and configuration menu."""
        while True:
            choices = [
                ("detect", "Detect Hardware"),
                ("spi", "Enable SPI (for HAT radios)"),
                ("back", "Back"),
            ]

            choice = self.dialog.menu(
                "Hardware",
                "Hardware detection and configuration:",
                choices
            )

            if choice is None or choice == "back":
                break

            if choice == "detect":
                self._detect_hardware()
            elif choice == "spi":
                self._enable_spi()

    def _detect_hardware(self):
        """Run hardware detection."""
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

    def _enable_spi(self):
        """Enable SPI interface for HAT-based radios."""
        # Check if SPI is already enabled
        spi_devices = list(Path('/dev').glob('spidev*'))
        if spi_devices:
            self.dialog.msgbox(
                "SPI Status",
                "SPI is already enabled!\n\n"
                f"Devices: {', '.join(d.name for d in spi_devices)}\n\n"
                "Your HAT radio should be detected."
            )
            return

        # Check if on Raspberry Pi
        is_pi = self._is_raspberry_pi()
        if not is_pi:
            self.dialog.msgbox(
                "Not Raspberry Pi",
                "SPI auto-enable is only available on Raspberry Pi.\n\n"
                "For other systems, consult your board's documentation\n"
                "for enabling SPI interfaces."
            )
            return

        # Confirm enablement
        result = self.dialog.yesno(
            "Enable SPI",
            "This will enable the SPI interface for HAT radios.\n\n"
            "Supported HATs:\n"
            "  • MeshAdv-Pi-Hat\n"
            "  • Waveshare LoRa HAT\n"
            "  • Other SPI-based radios\n\n"
            "A REBOOT is required after enabling.\n\n"
            "Enable SPI now?"
        )

        if not result:
            return

        self.dialog.infobox("SPI", "Enabling SPI interface...")

        try:
            # Find boot config
            boot_config = None
            for path in ['/boot/firmware/config.txt', '/boot/config.txt']:
                if Path(path).exists():
                    boot_config = path
                    break

            if not boot_config:
                self.dialog.msgbox("Error", "Could not find boot config file.")
                return

            # Use raspi-config if available
            raspi_config = shutil.which('raspi-config')
            if raspi_config:
                subprocess.run(
                    ['raspi-config', 'nonint', 'set_config_var', 'dtparam=spi', 'on', boot_config],
                    timeout=30,
                    check=False
                )

            # Add dtoverlay for HAT compatibility
            config_content = Path(boot_config).read_text()
            needs_write = False
            lines = config_content.split('\n')
            new_lines = []
            added_overlay = False

            for line in lines:
                new_lines.append(line)
                # Add overlay after dtparam=spi=on
                if 'dtparam=spi=on' in line and 'dtoverlay=spi0-0cs' not in config_content:
                    new_lines.append('dtoverlay=spi0-0cs')
                    added_overlay = True
                    needs_write = True

            # If dtparam=spi=on wasn't found, add both
            if 'dtparam=spi=on' not in config_content:
                new_lines.append('dtparam=spi=on')
                new_lines.append('dtoverlay=spi0-0cs')
                needs_write = True

            if needs_write:
                Path(boot_config).write_text('\n'.join(new_lines))

            self.dialog.msgbox(
                "SPI Enabled",
                "SPI interface has been enabled!\n\n"
                "IMPORTANT: You must REBOOT for changes to take effect.\n\n"
                "After reboot:\n"
                "  1. Your HAT radio will be detected\n"
                "  2. Configure meshtasticd for SPI\n"
                "  3. Start meshtasticd service\n\n"
                "Reboot now with: sudo reboot"
            )

        except subprocess.TimeoutExpired:
            self.dialog.msgbox("Error", "Timeout while configuring SPI.")
        except Exception as e:
            self.dialog.msgbox("Error", f"Failed to enable SPI:\n{e}")

    def _is_raspberry_pi(self) -> bool:
        """Check if running on Raspberry Pi."""
        try:
            cpuinfo = Path('/proc/cpuinfo')
            if cpuinfo.exists():
                content = cpuinfo.read_text()
                if 'Raspberry Pi' in content or 'BCM' in content:
                    return True
            model = Path('/proc/device-tree/model')
            if model.exists():
                if 'Raspberry Pi' in model.read_text():
                    return True
        except Exception:
            pass
        return False

    def _settings_menu(self):
        """Settings menu."""
        choices = [
            ("connection", "Meshtastic Connection"),
            ("gateway", "Gateway Settings"),
            ("hamclock", "HamClock Settings"),
            ("wizard", "Run Setup Wizard"),
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
            elif choice == "wizard":
                self._settings_run_wizard()

    def _configure_connection(self):
        """Configure Meshtastic connection."""
        choices = [
            ("localhost", "Local TCP (localhost:4403)"),
            ("serial", "Serial Port"),
            ("remote", "Remote Host"),
            ("back", "Back"),
        ]

        choice = self.dialog.menu(
            "Meshtastic Connection",
            "Select connection type:",
            choices
        )

        if choice is None or choice == "back":
            return
        elif choice == "localhost":
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
                "Destination node ID (e.g. !abc12345)\n"
                "Leave empty for broadcast to channel:",
                ""
            )

            if dest is None:
                return

            # Validate destination format
            if dest:
                dest = dest.strip()
                if not dest.startswith('!'):
                    dest = '!' + dest
                # Must be ! followed by hex chars
                hex_part = dest[1:]
                if not hex_part or not all(c in '0123456789abcdefABCDEF' for c in hex_part):
                    self.dialog.msgbox("Error",
                        f"Invalid node ID: {dest}\n\n"
                        "Format: !abc12345 (hex characters)\n"
                        "Leave empty for broadcast.")
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
