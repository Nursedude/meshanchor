#!/usr/bin/env python3
"""
MeshForge Launcher - raspi-config Style TUI

A whiptail/dialog based launcher that works:
- Over SSH (no display required)
- On any terminal (local or remote)

Uses whiptail (Debian/Ubuntu default) with dialog fallback.
Falls back to basic terminal menu if neither available.
"""

import os
import re
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
    __version__ = "0.4.7-beta"

# Import centralized path utility
try:
    from utils.paths import get_real_user_home, ReticulumPaths
except ImportError:
    def get_real_user_home() -> Path:
        sudo_user = os.environ.get('SUDO_USER', '')
        if sudo_user and sudo_user != 'root' and '/' not in sudo_user and '..' not in sudo_user:
            candidate = Path(f'/home/{sudo_user}')
            return candidate
        logname = os.environ.get('LOGNAME', '')
        if logname and logname != 'root' and '/' not in logname and '..' not in logname:
            candidate = Path(f'/home/{logname}')
            return candidate
        return Path('/root')

    class ReticulumPaths:
        @classmethod
        def get_config_dir(cls) -> Path:
            if Path('/etc/reticulum/config').is_file():
                return Path('/etc/reticulum')
            home = get_real_user_home()
            xdg = home / '.config' / 'reticulum'
            if (xdg / 'config').is_file():
                return xdg
            return home / '.reticulum'

        @classmethod
        def get_config_file(cls) -> Path:
            return cls.get_config_dir() / 'config'

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

# Import startup checks and conflict resolution (v0.4.8)
try:
    from startup_checks import StartupChecker, EnvironmentState, ServiceRunState
    from conflict_resolver import check_and_resolve_conflicts
    HAS_STARTUP_CHECKS = True
except ImportError:
    HAS_STARTUP_CHECKS = False
    StartupChecker = None
    EnvironmentState = None
    ServiceRunState = None
    check_and_resolve_conflicts = None

# Import mixins to reduce file size
from rf_tools_mixin import RFToolsMixin
from channel_config_mixin import ChannelConfigMixin
from ai_tools_mixin import AIToolsMixin
from meshtasticd_config_mixin import MeshtasticdConfigMixin
from site_planner_mixin import SitePlannerMixin
from service_discovery_mixin import ServiceDiscoveryMixin
from first_run_mixin import FirstRunMixin
from system_tools_mixin import SystemToolsMixin
from quick_actions_mixin import QuickActionsMixin
from emergency_mode_mixin import EmergencyModeMixin
from rns_interfaces_mixin import RNSInterfacesMixin
from nomadnet_client_mixin import NomadNetClientMixin
from topology_mixin import TopologyMixin
from rf_awareness_mixin import RFAwarenessMixin
from metrics_mixin import MetricsMixin
from link_quality_mixin import LinkQualityMixin


class MeshForgeLauncher(
    RFToolsMixin,
    ChannelConfigMixin,
    AIToolsMixin,
    MeshtasticdConfigMixin,
    SitePlannerMixin,
    ServiceDiscoveryMixin,
    FirstRunMixin,
    SystemToolsMixin,
    QuickActionsMixin,
    EmergencyModeMixin,
    RNSInterfacesMixin,
    NomadNetClientMixin,
    TopologyMixin,
    RFAwarenessMixin,
    MetricsMixin,
    LinkQualityMixin
):
    """MeshForge launcher with raspi-config style interface."""

    def __init__(self):
        self.dialog = DialogBackend()
        self.src_dir = Path(__file__).parent.parent  # src/ directory
        self.env = self._detect_environment()
        self._setup_status_bar()
        self._meshtastic_path = None  # Cached CLI path
        self._bridge_log_path = None  # Path to active bridge log file
        # Enhanced startup checker (v0.4.8)
        self._startup_checker = StartupChecker() if HAS_STARTUP_CHECKS else None
        self._env_state: Optional[EnvironmentState] = None

    @staticmethod
    def _wait_for_enter(msg: str = "\nPress Enter to continue...") -> None:
        """Wait for user to press Enter, handling Ctrl+C gracefully."""
        try:
            input(msg)
        except (KeyboardInterrupt, EOFError):
            print()  # Clean newline after ^C

    def _get_meshtastic_cli(self) -> str:
        """Find the meshtastic CLI binary path, with caching."""
        if self._meshtastic_path is None:
            try:
                from utils.cli import find_meshtastic_cli
                self._meshtastic_path = find_meshtastic_cli() or 'meshtastic'
            except ImportError:
                self._meshtastic_path = shutil.which('meshtastic') or 'meshtastic'
        return self._meshtastic_path

    @staticmethod
    def _validate_hostname(host: str) -> bool:
        """Validate hostname or IP address for use in network commands.

        Prevents flag injection (args starting with '-') and restricts
        to safe characters. Used before passing user input to ping,
        DNS lookup, or other network tools.
        """
        if not host or len(host) > 253:
            return False
        if host.startswith('-'):
            return False
        # Allow hostnames, IPv4, IPv6 — alphanumeric, dots, hyphens, colons
        return bool(re.match(r'^[a-zA-Z0-9.\-:]+$', host))

    @staticmethod
    def _validate_port(port_str: str) -> bool:
        """Validate a network port number string."""
        try:
            port = int(port_str)
            return 1 <= port <= 65535
        except (ValueError, TypeError):
            return False

    def _setup_status_bar(self) -> None:
        """Initialize and attach the status bar to the dialog backend."""
        try:
            from status_bar import StatusBar
            self._status_bar = StatusBar(version=__version__)
            self.dialog.set_status_bar(self._status_bar)
        except Exception:
            self._status_bar = None

    def _detect_environment(self) -> dict:
        """Detect the current environment."""
        env = {
            'has_display': False,
            'display_type': None,
            'is_ssh': False,
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

        # Run startup environment checks (v0.4.8)
        if not self._run_startup_checks():
            return  # User aborted due to conflicts

        # Check for first run and offer setup wizard
        if self._check_first_run():
            self._run_first_run_wizard()

        # Check for service misconfiguration (SPI HAT with USB config)
        self._check_service_misconfig()

        # Auto-start map server if configured
        self._maybe_auto_start_map()

        self._run_main_menu()

    def _run_startup_checks(self) -> bool:
        """
        Run startup environment checks and conflict resolution.

        Returns:
            True to continue, False if user aborted
        """
        if not HAS_STARTUP_CHECKS or not self._startup_checker:
            return True

        # Get environment state
        self._env_state = self._startup_checker.check_all()

        # Update status bar with environment info
        if self._status_bar and hasattr(self._status_bar, '_env_state'):
            self._status_bar._env_state = self._env_state

        # Check for port conflicts
        if self._env_state.conflicts:
            if not check_and_resolve_conflicts(self.dialog, self._startup_checker):
                return False  # User aborted

            # Re-check after resolution
            self._startup_checker.invalidate_cache()
            self._env_state = self._startup_checker.check_all()

        # Show alerts if any (non-blocking)
        alerts = self._env_state.get_alerts()
        if alerts and len(alerts) <= 3:
            # Show a quick info message for minor issues
            alert_text = "\n".join(f"  - {a}" for a in alerts)
            self.dialog.msgbox(
                "Startup Notes",
                f"Environment check found:\n\n{alert_text}\n\n"
                "These are informational - press Enter to continue."
            )

        return True

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
        """Display the main NOC menu.

        Redesigned in v0.4.8 to follow UI/UX best practices:
        - Max 10 items per menu (cognitive load)
        - Grouped by user task, not technical domain
        - 2-tap max for common operations
        """
        while True:
            # Build status hint for menu subtitle
            status_hint = self._get_menu_status_hint()

            choices = [
                # Primary Operations (numbered for quick access)
                ("1", "Dashboard           Status, health, alerts"),
                ("2", "Mesh Networks       Meshtastic, RNS, AREDN"),
                ("3", "RF & SDR            Calculators, SDR monitoring"),
                ("4", "Maps & Viz          Coverage maps, topology"),
                ("5", "Configuration       Radio, services, settings"),
                ("6", "System              Hardware, logs, Linux tools"),
                # Separator (visual only)
                ("---", "─────────────────────────────────────"),
                # Quick Access
                ("q", "Quick Actions       Common shortcuts"),
                ("e", "Emergency Mode      Field operations"),
                # Meta
                ("a", "About               Version, help, web client"),
                ("x", "Exit"),
            ]

            choice = self.dialog.menu(
                f"MeshForge NOC v{__version__}",
                status_hint,
                choices
            )

            if choice is None or choice == "x":
                break

            # Handle separator (do nothing)
            if choice == "---":
                continue

            self._handle_main_choice(choice)

    def _get_menu_status_hint(self) -> str:
        """Generate status hint for main menu subtitle."""
        if self._env_state:
            return self._env_state.get_status_line()
        return "Network Operations Center"

    def _handle_main_choice(self, choice: str):
        """Handle main menu selection (v0.4.8 restructured)."""
        if choice == "1":
            self._dashboard_menu()
        elif choice == "2":
            self._mesh_networks_menu()
        elif choice == "3":
            self._rf_sdr_menu()
        elif choice == "4":
            self._maps_viz_menu()
        elif choice == "5":
            self._configuration_menu()
        elif choice == "6":
            self._system_menu()
        elif choice == "q":
            self._quick_actions_menu()
        elif choice == "e":
            self._emergency_mode()
        elif choice == "a":
            self._about_menu()

    # =========================================================================
    # NEW Submenu: Dashboard (1)
    # =========================================================================

    def _dashboard_menu(self):
        """Dashboard - Status, health, alerts."""
        while True:
            choices = [
                ("status", "Service Status      All services with health"),
                ("network", "Network Status      Ports, interfaces, conflicts"),
                ("nodes", "Node Count          Meshtastic + RNS nodes"),
                ("metrics", "Historical Trends   Metrics over time"),
                ("alerts", "View Alerts         Current warnings"),
                ("back", "Back"),
            ]

            choice = self.dialog.menu(
                "Dashboard",
                "System status and monitoring:",
                choices
            )

            if choice is None or choice == "back":
                break

            if choice == "status":
                self._service_status_display()
            elif choice == "network":
                self._network_menu()
            elif choice == "nodes":
                self._show_node_counts()
            elif choice == "metrics":
                self._metrics_menu()
            elif choice == "alerts":
                self._show_alerts()

    def _service_status_display(self):
        """Show comprehensive service status."""
        # Delegate to existing service menu status
        subprocess.run(['clear'], check=False, timeout=5)
        print("=== Service Status ===\n")

        if self._env_state:
            for name, info in self._env_state.services.items():
                if info.state == ServiceRunState.RUNNING:
                    print(f"  \033[0;32m●\033[0m {name:<18} running")
                elif info.state == ServiceRunState.FAILED:
                    print(f"  \033[0;31m●\033[0m {name:<18} FAILED")
                else:
                    print(f"  \033[2m○\033[0m {name:<18} stopped")
        else:
            # Fallback to systemctl
            for svc in ['meshtasticd', 'rnsd', 'mosquitto']:
                try:
                    result = subprocess.run(
                        ['systemctl', 'is-active', svc],
                        capture_output=True, text=True, timeout=5
                    )
                    status = result.stdout.strip()
                    if status == 'active':
                        print(f"  \033[0;32m●\033[0m {svc:<18} running")
                    else:
                        print(f"  \033[2m○\033[0m {svc:<18} {status}")
                except Exception:
                    print(f"  ? {svc:<18} unknown")

        print()
        self._wait_for_enter()

    def _show_node_counts(self):
        """Show node counts from all sources."""
        subprocess.run(['clear'], check=False, timeout=5)
        print("=== Node Counts ===\n")

        # Meshtastic nodes
        try:
            cli = self._get_meshtastic_cli()
            result = subprocess.run(
                [cli, '--host', 'localhost', '--info'],
                capture_output=True, text=True, timeout=30
            )
            # Count nodes in output
            node_count = result.stdout.count('Node ')
            print(f"  Meshtastic nodes: {node_count}")
        except Exception as e:
            print(f"  Meshtastic: unavailable ({e})")

        # RNS destinations
        try:
            result = subprocess.run(
                ['rnstatus', '-a'],
                capture_output=True, text=True, timeout=10
            )
            # Count lines that look like destinations
            dest_count = len([l for l in result.stdout.splitlines() if l.strip().startswith('<')])
            print(f"  RNS destinations: {dest_count}")
        except Exception:
            print("  RNS: unavailable")

        print()
        self._wait_for_enter()

    def _show_alerts(self):
        """Show current alerts from environment state."""
        subprocess.run(['clear'], check=False, timeout=5)
        print("=== Current Alerts ===\n")

        if self._env_state:
            alerts = self._env_state.get_alerts()
            if alerts:
                for alert in alerts:
                    print(f"  \033[0;33m!\033[0m {alert}")
            else:
                print("  No alerts - system healthy")
        else:
            print("  Environment state not available")

        print()
        self._wait_for_enter()

    # =========================================================================
    # NEW Submenu: Mesh Networks (2)
    # =========================================================================

    def _mesh_networks_menu(self):
        """Mesh Networks - Meshtastic, RNS, AREDN."""
        while True:
            choices = [
                ("meshtastic", "Meshtastic          Radio, channels, CLI"),
                ("rns", "RNS / Reticulum     Status, gateway, NomadNet"),
                ("aredn", "AREDN Mesh          AREDN integration"),
                ("services", "Service Control     Start/stop/restart"),
                ("back", "Back"),
            ]

            choice = self.dialog.menu(
                "Mesh Networks",
                "Manage mesh network connections:",
                choices
            )

            if choice is None or choice == "back":
                break

            if choice == "meshtastic":
                self._radio_menu()
            elif choice == "rns":
                self._rns_menu()
            elif choice == "aredn":
                self._aredn_menu()
            elif choice == "services":
                self._service_menu()

    # =========================================================================
    # NEW Submenu: RF & SDR (3)
    # =========================================================================

    def _rf_sdr_menu(self):
        """RF & SDR - Calculators, SDR monitoring."""
        while True:
            choices = [
                ("link", "Link Budget         FSPL, Fresnel, range"),
                ("site", "Site Planner        Coverage estimation"),
                ("freq", "Frequency Slots     Channel calculator"),
                ("sdr", "SDR Monitor         RF awareness (Airspy)"),
                ("back", "Back"),
            ]

            choice = self.dialog.menu(
                "RF & SDR Tools",
                "Radio frequency tools and monitoring:",
                choices
            )

            if choice is None or choice == "back":
                break

            if choice == "link":
                self._rf_tools_menu()
            elif choice == "site":
                self._site_planner_menu()
            elif choice == "freq":
                self._frequency_calculator()
            elif choice == "sdr":
                self._rf_awareness_menu()

    # =========================================================================
    # NEW Submenu: Maps & Viz (4)
    # =========================================================================

    def _maps_viz_menu(self):
        """Maps & Visualization - Coverage maps, topology."""
        while True:
            choices = [
                ("coverage", "Coverage Map        Generate coverage map"),
                ("topology", "Network Topology    D3.js graph view"),
                ("nodes", "Node Map            All nodes on map"),
                ("quality", "Link Quality        Quality analysis"),
                ("export", "Export Data         GeoJSON, CSV, GraphML"),
                ("back", "Back"),
            ]

            choice = self.dialog.menu(
                "Maps & Visualization",
                "Network visualization tools:",
                choices
            )

            if choice is None or choice == "back":
                break

            if choice == "coverage":
                self._ai_tools_menu()
            elif choice == "topology":
                self._topology_menu()
            elif choice == "nodes":
                self._open_node_map()
            elif choice == "quality":
                self._link_quality_menu()
            elif choice == "export":
                self._export_data_menu()

    def _open_node_map(self):
        """Open the node map in browser."""
        # Trigger map generation and open
        self._ai_tools_menu()

    def _export_data_menu(self):
        """Export data in various formats."""
        while True:
            choices = [
                ("geojson", "GeoJSON             For mapping tools"),
                ("csv", "CSV                 Spreadsheet format"),
                ("graphml", "GraphML             For graph analysis"),
                ("d3", "D3.js JSON          For web visualization"),
                ("back", "Back"),
            ]

            choice = self.dialog.menu(
                "Export Data",
                "Export network data:",
                choices
            )

            if choice is None or choice == "back":
                break

            # Delegate to topology export functions
            if choice in ["geojson", "csv", "graphml", "d3"]:
                self._export_topology_data(choice)

    def _export_topology_data(self, format_type: str):
        """Export topology data in specified format."""
        try:
            from utils.topology_visualizer import TopologyVisualizer

            viz = TopologyVisualizer()
            # TODO: Populate with actual data
            output_dir = get_real_user_home() / ".config" / "meshforge" / "exports"
            output_dir.mkdir(parents=True, exist_ok=True)

            if format_type == "geojson":
                path = output_dir / "topology.geojson"
                viz.export_geojson(str(path))
            elif format_type == "csv":
                path = output_dir / "topology.csv"
                viz.export_csv(str(path))
            elif format_type == "graphml":
                path = output_dir / "topology.graphml"
                viz.export_graphml(str(path))
            elif format_type == "d3":
                path = output_dir / "topology.json"
                viz.export_d3_json(str(path))

            self.dialog.msgbox("Export Complete", f"Exported to:\n{path}")

        except Exception as e:
            self.dialog.msgbox("Export Failed", f"Error: {e}")

    # =========================================================================
    # NEW Submenu: Configuration (5)
    # =========================================================================

    def _configuration_menu(self):
        """Configuration - Radio, services, settings."""
        while True:
            choices = [
                ("radio", "Radio Config        meshtasticd settings"),
                ("channels", "Channel Config      Meshtastic channels"),
                ("rns-config", "RNS Config          Reticulum settings"),
                ("services", "Service Config      systemd services"),
                ("meshforge", "MeshForge Settings  App preferences"),
                ("wizard", "Setup Wizard        First-run wizard"),
                ("back", "Back"),
            ]

            choice = self.dialog.menu(
                "Configuration",
                "System and service configuration:",
                choices
            )

            if choice is None or choice == "back":
                break

            if choice == "radio":
                self._config_menu()
            elif choice == "channels":
                self._channel_config_menu()
            elif choice == "rns-config":
                self._edit_rns_config()
            elif choice == "services":
                self._service_menu()
            elif choice == "meshforge":
                self._settings_menu()
            elif choice == "wizard":
                self._run_first_run_wizard()

    # =========================================================================
    # NEW Submenu: System (6)
    # =========================================================================

    def _system_menu(self):
        """System - Hardware, logs, Linux tools."""
        while True:
            choices = [
                ("hardware", "Hardware            Detect SPI/I2C/USB"),
                ("logs", "Logs                View/follow logs"),
                ("network", "Network Tools       Ping, ports, interfaces"),
                ("shell", "Linux Shell         Drop to bash"),
                ("reboot", "Reboot/Shutdown     Safe system control"),
                ("back", "Back"),
            ]

            choice = self.dialog.menu(
                "System Tools",
                "System administration:",
                choices
            )

            if choice is None or choice == "back":
                break

            if choice == "hardware":
                self._hardware_menu()
            elif choice == "logs":
                self._logs_menu()
            elif choice == "network":
                self._network_tools_submenu()
            elif choice == "shell":
                self._drop_to_shell()
            elif choice == "reboot":
                self._reboot_menu()

    def _network_tools_submenu(self):
        """Network diagnostic tools."""
        # Delegate to existing network menu
        self._network_menu()

    def _drop_to_shell(self):
        """Drop to a bash shell."""
        subprocess.run(['clear'], check=False, timeout=5)
        print("Dropping to shell. Type 'exit' to return to MeshForge.\n")
        subprocess.run(['bash'], check=False)

    def _reboot_menu(self):
        """Safe reboot/shutdown options."""
        while True:
            choices = [
                ("reboot", "Reboot              Restart system"),
                ("shutdown", "Shutdown            Power off"),
                ("back", "Back"),
            ]

            choice = self.dialog.menu(
                "Reboot / Shutdown",
                "System power options:",
                choices
            )

            if choice is None or choice == "back":
                break

            if choice == "reboot":
                if self.dialog.yesno("Confirm Reboot", "Reboot the system now?"):
                    subprocess.run(['systemctl', 'reboot'], timeout=30)
            elif choice == "shutdown":
                if self.dialog.yesno("Confirm Shutdown", "Shutdown the system now?"):
                    subprocess.run(['systemctl', 'poweroff'], timeout=30)

    # =========================================================================
    # NEW Submenu: About (a)
    # =========================================================================

    def _about_menu(self):
        """About - Version, help, web client."""
        while True:
            choices = [
                ("version", "Version Info        MeshForge version"),
                ("web", "Web Client          Open web interface"),
                ("help", "Help                Documentation"),
                ("back", "Back"),
            ]

            choice = self.dialog.menu(
                "About MeshForge",
                "Information and help:",
                choices
            )

            if choice is None or choice == "back":
                break

            if choice == "version":
                self._show_about()
            elif choice == "web":
                self._open_web_client()
            elif choice == "help":
                self._show_help()

    def _show_help(self):
        """Show help documentation."""
        help_text = """
MeshForge - Network Operations Center

KEYBOARD SHORTCUTS:
  1-6     Quick access to main sections
  q       Quick Actions
  e       Emergency Mode
  a       About
  x       Exit

NAVIGATION:
  Enter   Select item
  Esc     Go back / Cancel
  Tab     Move between buttons

DOCUMENTATION:
  https://github.com/Nursedude/meshforge

SUPPORT:
  Issues: github.com/Nursedude/meshforge/issues
"""
        subprocess.run(['clear'], check=False, timeout=5)
        print(help_text)
        self._wait_for_enter()

    # =========================================================================
    # Legacy menu handler (for backward compatibility)
    # =========================================================================

    def _handle_choice(self, choice: str):
        """Handle menu selection (legacy - kept for compatibility)."""
        if choice == "status":
            self._run_terminal_status()
        elif choice == "quick":
            self._quick_actions_menu()
        elif choice == "radio":
            self._radio_menu()
        elif choice == "services":
            self._service_menu()
        elif choice == "emcomm":
            self._emergency_mode()
        elif choice == "logs":
            self._logs_menu()
        elif choice == "network":
            self._network_menu()
        elif choice == "rns":
            self._rns_menu()
        elif choice == "aredn":
            self._aredn_menu()
        elif choice == "metrics":
            self._metrics_menu()
        elif choice == "rf":
            self._rf_tools_menu()
        elif choice == "sdr":
            self._rf_awareness_menu()
        elif choice == "maps":
            self._ai_tools_menu()
        elif choice == "config":
            self._config_menu()
        elif choice == "hardware":
            self._hardware_menu()
        elif choice == "system":
            self._system_tools_menu()
        elif choice == "web":
            self._open_web_client()
        elif choice == "about":
            self._show_about()

    # =========================================================================
    # Radio Menu - Direct meshtastic CLI (terminal-native)
    # =========================================================================

    def _radio_menu(self):
        """Radio tools using meshtastic CLI directly."""
        while True:
            # Check if CLI is available and actually working
            cli_path = self._get_meshtastic_cli()
            has_cli = cli_path != 'meshtastic'

            # Even if found, verify it's actually executable
            cli_works = False
            cli_location = ""
            if has_cli:
                cli_location = cli_path
                try:
                    # Quick test - just check if we can run --version
                    result = subprocess.run(
                        [cli_path, '--version'],
                        capture_output=True, timeout=5
                    )
                    cli_works = result.returncode == 0
                except (subprocess.TimeoutExpired, FileNotFoundError, PermissionError):
                    cli_works = False

            choices = []
            if not cli_works:
                choices.append(("install-cli", "** Install meshtastic CLI **"))
            choices.extend([
                ("info", "Radio Info (meshtastic --info)"),
                ("nodes", "Node List (meshtastic --nodes)"),
                ("channels", "Channel Info"),
                ("send", "Send Message"),
                ("position", "Position (view/set)"),
                ("set-region", "Set Region"),
                ("set-name", "Set Node Name"),
                ("reboot", "Reboot Radio"),
                ("reinstall-cli", "Reinstall/Update CLI"),
                ("back", "Back"),
            ])

            if cli_works:
                status = f"\n[CLI: {cli_location}]"
            elif has_cli:
                status = f"\n[CLI found but not working: {cli_location}]"
            else:
                status = "\n[CLI not installed]"

            choice = self.dialog.menu(
                "Radio Tools",
                f"Meshtastic radio control (terminal-native):{status}",
                choices
            )

            if choice is None or choice == "back":
                break

            if choice == "install-cli" or choice == "reinstall-cli":
                self._install_meshtastic_cli()
                continue

            cli = self._get_meshtastic_cli()
            # Use --host localhost to connect via meshtasticd (required for HAT radios)
            conn_args = ['--host', 'localhost']
            if choice == "info":
                self._radio_run([cli] + conn_args + ['--info'], "Radio Info")
            elif choice == "nodes":
                self._radio_run([cli] + conn_args + ['--nodes'], "Node List")
            elif choice == "channels":
                self._radio_run([cli] + conn_args + ['--ch-index', '0', '--ch-getall'], "Channels")
            elif choice == "position":
                self._radio_position_menu()
            elif choice == "send":
                self._radio_send_message()
            elif choice == "set-region":
                self._radio_set_region()
            elif choice == "set-name":
                self._radio_set_name()
            elif choice == "reboot":
                self._radio_reboot()

    def _radio_run(self, cmd: list, title: str):
        """Run a meshtastic CLI command and show output in terminal."""
        subprocess.run(['clear'], check=False, timeout=5)
        print(f"=== {title} ===")
        print("(Ctrl+C to abort)\n")
        try:
            result = subprocess.run(cmd, timeout=30)
            if result.returncode != 0:
                print(f"\nCommand failed (exit {result.returncode})")
                print("Is meshtasticd running? Check: systemctl status meshtasticd")
        except FileNotFoundError:
            self._offer_install_meshtastic_cli()
            return
        except subprocess.TimeoutExpired:
            print("\n\nCommand timed out (30s). Radio may not be connected.")
            print("Check: systemctl status meshtasticd")
        except KeyboardInterrupt:
            print("\n\nAborted.")
        try:
            self._wait_for_enter()
        except KeyboardInterrupt:
            print()

    def _offer_install_meshtastic_cli(self):
        """Offer to install meshtastic CLI when it's missing (from error handler)."""
        install = self.dialog.yesno(
            "Meshtastic CLI Not Found",
            "The 'meshtastic' CLI is not installed.\n\n"
            "This is needed to configure the radio\n"
            "(set presets, region, node name, etc.).\n\n"
            "Install meshtastic CLI now?",
            default_no=False
        )
        if install:
            self._install_meshtastic_cli()

    def _install_meshtastic_cli(self):
        """Install meshtastic CLI via pipx with live terminal output."""
        subprocess.run(['clear'], check=False, timeout=5)
        print("=== Installing Meshtastic CLI ===\n")

        # Determine if we should install as a different user (when running via sudo)
        sudo_user = os.environ.get('SUDO_USER')
        run_as_user = sudo_user if sudo_user and sudo_user != 'root' else None

        try:
            # Ensure pipx is available (this needs root for apt)
            if not shutil.which('pipx'):
                print("Installing pipx...\n")
                result = subprocess.run(
                    ['apt-get', 'install', '-y', 'pipx'],
                    timeout=60
                )
                if result.returncode != 0:
                    print("\nFailed to install pipx.")
                    print("Try manually: sudo apt install pipx")
                    self._wait_for_enter()
                    return

            # Build pipx commands - run as real user if we're under sudo
            def run_pipx_cmd(args, timeout_sec=300):
                """Run pipx command, as real user if running via sudo."""
                if run_as_user:
                    # Run as the real user with login shell (-i) to set HOME correctly
                    # Without -i, HOME stays as /root and pipx installs there
                    cmd = ['sudo', '-i', '-u', run_as_user] + args
                else:
                    cmd = args
                return subprocess.run(cmd, timeout=timeout_sec)

            # Ensure pipx bin dir is in PATH for this session
            print("Ensuring pipx paths...\n")
            run_pipx_cmd(['pipx', 'ensurepath'], timeout_sec=15)

            # Add common pipx bin dirs to current process PATH
            for bindir in [
                get_real_user_home() / '.local' / 'bin',
                Path('/root/.local/bin'),
                Path('/usr/local/bin'),
            ]:
                if bindir.is_dir() and str(bindir) not in os.environ.get('PATH', ''):
                    os.environ['PATH'] = f"{bindir}:{os.environ.get('PATH', '')}"

            # Install meshtastic with CLI extras (live output)
            if run_as_user:
                print(f"\nInstalling meshtastic CLI via pipx (as {run_as_user})...\n")
            else:
                print("\nInstalling meshtastic CLI via pipx...\n")
            result = run_pipx_cmd(['pipx', 'install', 'meshtastic[cli]', '--force'])

            if result.returncode != 0:
                print("\nRetrying without [cli] extras...\n")
                result = run_pipx_cmd(['pipx', 'install', 'meshtastic', '--force'])

            if result.returncode == 0:
                # Clear cached path so it gets re-resolved
                self._meshtastic_path = None
                cli_path = self._get_meshtastic_cli()
                if cli_path and cli_path != 'meshtastic':
                    print(f"\n** meshtastic CLI installed: {cli_path} **")
                else:
                    print("\n** meshtastic installed but not found in PATH **")
                    print("You may need to log out and back in,")
                    print("or run: eval \"$(pipx ensurepath)\"")
            else:
                print("\nInstallation failed.")
                print("Try manually: pipx install meshtastic")

        except FileNotFoundError:
            print("pipx not found.")
            print("Try: sudo apt install pipx && pipx install meshtastic")
        except KeyboardInterrupt:
            print("\n\nInstallation cancelled.")
        except subprocess.TimeoutExpired:
            print("\n\nInstallation timed out.")
            print("Try manually: pipx install meshtastic")
        except Exception as e:
            print(f"\nInstallation error: {e}")
            print("Try manually: pipx install meshtastic")

        try:
            self._wait_for_enter()
        except KeyboardInterrupt:
            print()

    def _radio_send_message(self):
        """Send a mesh message via meshtastic CLI."""
        msg = self.dialog.inputbox(
            "Send Message",
            "Message text (broadcast to default channel):",
            ""
        )
        if not msg:
            return

        dest = self.dialog.inputbox(
            "Destination",
            "Node ID (e.g. !abc12345)\nLeave empty for broadcast:",
            ""
        )

        cmd = [self._get_meshtastic_cli(), '--host', 'localhost', '--sendtext', msg]
        if dest and dest.strip():
            dest = dest.strip()
            if not dest.startswith('!'):
                dest = '!' + dest
            cmd.extend(['--dest', dest])

        self._radio_run(cmd, "Sending Message")

    def _radio_set_region(self):
        """Set LoRa region via meshtastic CLI."""
        choices = [
            ("US", "US (902-928 MHz)"),
            ("EU_868", "EU_868 (863-870 MHz)"),
            ("CN", "CN (470-510 MHz)"),
            ("JP", "JP (920-925 MHz)"),
            ("ANZ", "ANZ (915-928 MHz)"),
            ("KR", "KR (920-923 MHz)"),
            ("TW", "TW (920-925 MHz)"),
            ("RU", "RU (868-870 MHz)"),
            ("IN", "IN (865-867 MHz)"),
            ("NZ_865", "NZ_865 (864-868 MHz)"),
            ("TH", "TH (920-925 MHz)"),
            ("UA_868", "UA_868 (863-870 MHz)"),
            ("LORA_24", "LORA_24 (2.4 GHz)"),
            ("UNSET", "UNSET (clear region)"),
            ("back", "Back"),
        ]

        choice = self.dialog.menu(
            "Set Region",
            "Select your LoRa region:",
            choices
        )

        if choice is None or choice == "back":
            return

        if self.dialog.yesno("Confirm", f"Set region to {choice}?\n\nRadio will restart."):
            self._radio_run(
                [self._get_meshtastic_cli(), '--host', 'localhost', '--set', 'lora.region', choice],
                f"Setting Region: {choice}"
            )

    def _radio_set_name(self):
        """Set node long name via meshtastic CLI."""
        name = self.dialog.inputbox(
            "Node Name",
            "Enter node long name:",
            ""
        )
        if not name:
            return

        short = self.dialog.inputbox(
            "Short Name",
            "Enter short name (max 4 chars):",
            name[:4]
        )

        cmd = [self._get_meshtastic_cli(), '--host', 'localhost', '--set-owner', name]
        if short:
            cmd.extend(['--set-owner-short', short[:4]])
        self._radio_run(cmd, "Setting Node Name")

    def _radio_position_menu(self):
        """Position submenu: view settings or set fixed lat/lon."""
        choices = [
            ("view", "View position settings"),
            ("set", "Set fixed position (lat/lon)"),
            ("back", "Back"),
        ]

        choice = self.dialog.menu(
            "Position",
            "View or set node position:",
            choices
        )

        if choice is None or choice == "back":
            return

        cli = self._get_meshtastic_cli()
        conn_args = ['--host', 'localhost']

        if choice == "view":
            self._radio_run([cli] + conn_args + ['--get', 'position'], "Position Settings")
        elif choice == "set":
            lat = self.dialog.inputbox(
                "Latitude",
                "Enter latitude (decimal degrees):\n\n"
                "Example: 19.435175",
                ""
            )
            if not lat:
                return

            lon = self.dialog.inputbox(
                "Longitude",
                "Enter longitude (decimal degrees):\n\n"
                "Example: -155.213842",
                ""
            )
            if not lon:
                return

            # Validate numeric input
            try:
                lat_f = float(lat.strip())
                lon_f = float(lon.strip())
            except ValueError:
                self.dialog.msgbox("Error", "Invalid coordinates. Use decimal degrees.")
                return

            if not (-90 <= lat_f <= 90) or not (-180 <= lon_f <= 180):
                self.dialog.msgbox("Error",
                    "Coordinates out of range.\n\n"
                    "Latitude: -90 to 90\n"
                    "Longitude: -180 to 180")
                return

            confirm = self.dialog.yesno(
                "Confirm Position",
                f"Set fixed position?\n\n"
                f"Latitude:  {lat_f}\n"
                f"Longitude: {lon_f}",
                default_no=True
            )
            if not confirm:
                return

            self._radio_run(
                [cli] + conn_args + ['--setlat', str(lat_f), '--setlon', str(lon_f)],
                "Setting Position"
            )

    def _radio_reboot(self):
        """Reboot the radio via meshtastic CLI."""
        if self.dialog.yesno("Reboot Radio", "Reboot the Meshtastic radio?\n\nThis restarts the firmware.", default_no=True):
            self._radio_run(
                [self._get_meshtastic_cli(), '--host', 'localhost', '--reboot'],
                "Rebooting Radio"
            )

    # =========================================================================
    # Logs Menu - Terminal-native log viewing
    # =========================================================================

    def _logs_menu(self):
        """Log viewer - all terminal-native."""
        while True:
            choices = [
                ("live-mesh", "Live: meshtasticd (Ctrl+C to stop)"),
                ("live-rns", "Live: rnsd (Ctrl+C to stop)"),
                ("live-all", "Live: all services (Ctrl+C to stop)"),
                ("errors", "Errors (last hour)"),
                ("mesh-50", "meshtasticd (last 50 lines)"),
                ("rns-50", "rnsd (last 50 lines)"),
                ("boot", "Boot messages (this boot)"),
                ("kernel", "Kernel messages (dmesg)"),
                ("meshforge", "MeshForge app logs"),
                ("back", "Back"),
            ]

            choice = self.dialog.menu(
                "Log Viewer",
                "Terminal-native logs (real journalctl):",
                choices
            )

            if choice is None or choice == "back":
                break

            subprocess.run(['clear'], check=False, timeout=5)

            if choice == "live-mesh":
                print("=== meshtasticd live log (Ctrl+C to stop) ===\n")
                try:
                    subprocess.run(
                        ['journalctl', '-u', 'meshtasticd', '-f', '-n', '30', '--no-pager'],
                        timeout=None
                    )
                except KeyboardInterrupt:
                    pass
            elif choice == "live-rns":
                print("=== rnsd live log (Ctrl+C to stop) ===\n")
                try:
                    subprocess.run(
                        ['journalctl', '-u', 'rnsd', '-f', '-n', '30', '--no-pager'],
                        timeout=None
                    )
                except KeyboardInterrupt:
                    pass
            elif choice == "live-all":
                print("=== All services live log (Ctrl+C to stop) ===\n")
                try:
                    subprocess.run(
                        ['journalctl', '-f', '-n', '30', '--no-pager'],
                        timeout=None
                    )
                except KeyboardInterrupt:
                    pass
            elif choice == "errors":
                print("=== Errors (last hour, priority err+) ===\n")
                subprocess.run(
                    ['journalctl', '-p', 'err', '--since', '1 hour ago', '--no-pager'],
                    timeout=30
                )
                self._wait_for_enter()
            elif choice == "mesh-50":
                print("=== meshtasticd (last 50 lines) ===\n")
                subprocess.run(
                    ['journalctl', '-u', 'meshtasticd', '-n', '50', '--no-pager'],
                    timeout=15
                )
                self._wait_for_enter()
            elif choice == "rns-50":
                print("=== rnsd (last 50 lines) ===\n")
                subprocess.run(
                    ['journalctl', '-u', 'rnsd', '-n', '50', '--no-pager'],
                    timeout=15
                )
                self._wait_for_enter()
            elif choice == "boot":
                print("=== Boot messages (this boot) ===\n")
                subprocess.run(
                    ['journalctl', '-b', '-n', '100', '--no-pager'],
                    timeout=15
                )
                self._wait_for_enter()
            elif choice == "kernel":
                print("=== Kernel messages (dmesg) ===\n")
                subprocess.run(['dmesg', '--time-format=reltime'], timeout=10)
                self._wait_for_enter()
            elif choice == "meshforge":
                self._view_meshforge_logs()

    # =========================================================================
    # Network Menu - Ports, interfaces, connectivity
    # =========================================================================

    def _network_menu(self):
        """Network diagnostics - terminal-native."""
        while True:
            choices = [
                ("status", "Quick Network Status"),
                ("ports", "Listening Ports (ss -tlnp)"),
                ("ifaces", "Network Interfaces (ip addr)"),
                ("conns", "Active Connections (ss -tunp)"),
                ("routes", "Routing Table (ip route)"),
                ("ping", "Ping Test"),
                ("dns", "DNS Lookup"),
                ("discover", "Meshtastic Device Discovery"),
                ("back", "Back"),
            ]

            choice = self.dialog.menu(
                "Network & Ports",
                "Network diagnostics (terminal-native):",
                choices
            )

            if choice is None or choice == "back":
                break

            if choice == "status":
                self._run_terminal_network()
            elif choice == "ports":
                subprocess.run(['clear'], check=False, timeout=5)
                print("=== Listening Ports ===\n")
                subprocess.run(['ss', '-tlnp'], timeout=10)
                self._wait_for_enter()
            elif choice == "ifaces":
                subprocess.run(['clear'], check=False, timeout=5)
                print("=== Network Interfaces ===\n")
                subprocess.run(['ip', '-c', 'addr'], timeout=10)
                self._wait_for_enter()
            elif choice == "conns":
                subprocess.run(['clear'], check=False, timeout=5)
                print("=== Active Connections ===\n")
                subprocess.run(['ss', '-tunp'], timeout=10)
                self._wait_for_enter()
            elif choice == "routes":
                subprocess.run(['clear'], check=False, timeout=5)
                print("=== Routing Table ===\n")
                subprocess.run(['ip', 'route'], timeout=10)
                self._wait_for_enter()
            elif choice == "ping":
                self._ping_test()
            elif choice == "dns":
                self._dns_lookup()
            elif choice == "discover":
                self._meshtastic_discovery()

    # =========================================================================
    # RNS / Reticulum Menu
    # =========================================================================

    def _rns_menu(self):
        """Reticulum Network Stack tools."""
        while True:
            choices = [
                ("status", "RNS Status (rnstatus)"),
                ("paths", "RNS Path Table (rnpath)"),
                ("topology", "Network Topology (graph view)"),
                ("quality", "Link Quality Analysis"),
                ("probe", "Probe Destination (rnprobe)"),
                ("identity", "Identity Info (rnid)"),
                ("nodes", "Known Destinations"),
                ("positions", "Set Node Positions (for map)"),
                ("diag", "RNS Diagnostics"),
                ("bridge", "Gateway Bridge (start/stop)"),
                ("nomadnet", "NomadNet Client"),
                ("ifaces", "Manage Interfaces"),
                ("config", "View Reticulum Config"),
                ("edit", "Edit Reticulum Config"),
                ("check", "Check RNS Setup"),
                ("back", "Back"),
            ]

            choice = self.dialog.menu(
                "RNS / Reticulum",
                "Reticulum Network Stack tools:",
                choices
            )

            if choice is None or choice == "back":
                break

            if choice == "status":
                subprocess.run(['clear'], check=False, timeout=5)
                print("=== RNS Status ===\n")
                self._run_rns_tool(['rnstatus'], 'rnstatus')
                self._wait_for_enter()
            elif choice == "paths":
                subprocess.run(['clear'], check=False, timeout=5)
                print("=== RNS Path Table ===\n")
                self._run_rns_tool(['rnpath', '-t'], 'rnpath')
                self._wait_for_enter()
            elif choice == "topology":
                self._topology_menu()
            elif choice == "quality":
                self._link_quality_menu()
            elif choice == "probe":
                self._rns_probe_destination()
            elif choice == "identity":
                self._rns_identity_info()
            elif choice == "nodes":
                self._rns_known_destinations()
            elif choice == "positions":
                self._rns_set_node_positions()
            elif choice == "diag":
                self._rns_diagnostics()
            elif choice == "bridge":
                self._run_bridge()
            elif choice == "nomadnet":
                self._nomadnet_menu()
            elif choice == "ifaces":
                self._rns_interfaces_menu()
            elif choice == "config":
                self._view_rns_config()
            elif choice == "edit":
                self._edit_rns_config()
            elif choice == "check":
                self._check_rns_setup()

    def _rns_probe_destination(self):
        """Probe an RNS destination to test reachability."""
        subprocess.run(['clear'], check=False, timeout=5)
        print("=== Probe RNS Destination ===\n")
        print("Probe tests reachability of a destination on the RNS network.")
        print("Enter the full destination hash (32 hex chars), or a partial hash.\n")

        try:
            dest_hash = input("Destination hash (or 'q' to cancel): ").strip()
        except (KeyboardInterrupt, EOFError):
            print()
            return
        if not dest_hash or dest_hash.lower() == 'q':
            return

        # Validate hex format to prevent flag injection
        if not re.match(r'^[0-9a-fA-F]+$', dest_hash):
            print("Error: Hash must contain only hex characters (0-9, a-f).")
            self._wait_for_enter()
            return

        print(f"\nProbing {dest_hash}...\n")
        self._run_rns_tool(['rnprobe', dest_hash], 'rnprobe')
        self._wait_for_enter()

    def _rns_identity_info(self):
        """Show RNS identity information."""
        subprocess.run(['clear'], check=False, timeout=5)
        print("=== RNS Identity Info ===\n")

        while True:
            choices = [
                ("show", "Show local identity"),
                ("path", "Show identity file paths"),
                ("recall", "Recall identity by hash"),
                ("back", "Back"),
            ]

            choice = self.dialog.menu(
                "RNS Identity",
                "Identity management:",
                choices
            )

            if choice is None or choice == "back":
                break

            if choice == "show":
                subprocess.run(['clear'], check=False, timeout=5)
                print("=== Local RNS Identity ===\n")
                # rnid with no args shows the local identity
                self._run_rns_tool(['rnid'], 'rnid')

                # Also show MeshForge gateway identity path
                try:
                    from commands.rns import get_identity_path
                    gw_id = get_identity_path()
                    print(f"\nMeshForge gateway identity: {gw_id}")
                    if gw_id.exists():
                        print("  Status: exists")
                    else:
                        print("  Status: not created (starts on first bridge run)")
                except ImportError:
                    pass
                self._wait_for_enter()

            elif choice == "path":
                subprocess.run(['clear'], check=False, timeout=5)
                print("=== RNS Identity Paths ===\n")
                config_dir = ReticulumPaths.get_config_dir()
                identity_path = config_dir / 'identity'
                print(f"RNS config dir:    {config_dir}")
                print(f"RNS identity file: {identity_path}")
                if identity_path.exists():
                    stat = identity_path.stat()
                    print(f"  Size: {stat.st_size} bytes")
                    from datetime import datetime
                    mtime = datetime.fromtimestamp(stat.st_mtime)
                    print(f"  Modified: {mtime.strftime('%Y-%m-%d %H:%M:%S')}")
                else:
                    print("  Not found (created on first rnsd start)")

                # Show gateway identity
                try:
                    from commands.rns import get_identity_path
                    gw_id = get_identity_path()
                    print(f"\nMeshForge gateway:  {gw_id}")
                    if gw_id.exists():
                        stat = gw_id.stat()
                        print(f"  Size: {stat.st_size} bytes")
                    else:
                        print("  Not created yet")
                except ImportError:
                    pass
                self._wait_for_enter()

            elif choice == "recall":
                subprocess.run(['clear'], check=False, timeout=5)
                print("=== Recall RNS Identity ===\n")
                print("Look up a known identity by its destination hash.\n")
                try:
                    dest_hash = input("Destination hash (or 'q' to cancel): ").strip()
                except (KeyboardInterrupt, EOFError):
                    print()
                    continue
                if dest_hash and dest_hash.lower() != 'q':
                    # Validate hex format to prevent flag injection
                    if not re.match(r'^[0-9a-fA-F]+$', dest_hash):
                        print("Error: Hash must contain only hex characters (0-9, a-f).")
                    else:
                        self._run_rns_tool(['rnid', '--recall', dest_hash], 'rnid')
                self._wait_for_enter()

    def _rns_known_destinations(self):
        """Show known RNS destinations from the running rnsd instance."""
        subprocess.run(['clear'], check=False, timeout=5)
        print("=== Known RNS Destinations ===\n")

        try:
            from commands.rns import list_known_destinations
            result = list_known_destinations()

            if result.success:
                nodes = result.data.get('nodes', [])
                count = result.data.get('count', 0)

                if count == 0:
                    print("No known destinations yet.")
                    print("\nNodes appear when they announce or when you request paths.")
                    print("Make sure rnsd is running: sudo systemctl start rnsd")
                else:
                    print(f"Found {count} destination(s):\n")
                    print(f"{'Hash':>10}  {'Hops':>5}  {'Source':<20}  {'Name'}")
                    print("-" * 60)
                    for node in nodes:
                        short = node.get('short_hash', '?')
                        hops = node.get('hops', -1)
                        hops_str = str(hops) if hops >= 0 else '?'
                        source = node.get('source', 'unknown')
                        name = node.get('name', '')
                        print(f"{short:>10}  {hops_str:>5}  {source:<20}  {name}")
            else:
                print(f"Error: {result.message}")
                fix_hint = (result.data or {}).get('fix_hint', '')
                if fix_hint:
                    print(f"Fix: {fix_hint}")
        except ImportError:
            # Fallback: use rnstatus which also shows some destination info
            print("Commands module not available, falling back to rnstatus...\n")
            self._run_rns_tool(['rnstatus', '-a'], 'rnstatus')

        self._wait_for_enter()

    def _rns_set_node_positions(self):
        """Set GPS positions for RNS nodes so they appear on the map.

        NomadNet nodes don't broadcast location, so positions must be set manually.
        Sideband nodes with GPS sharing will be auto-populated.
        """
        while True:
            subprocess.run(['clear'], check=False, timeout=5)
            print("=== Set RNS Node Positions ===\n")
            print("NomadNet nodes don't broadcast GPS. Set positions manually")
            print("so your RNS nodes appear on the live network map.\n")

            # Load node tracker and cache
            try:
                from gateway.node_tracker import UnifiedNodeTracker
                tracker = UnifiedNodeTracker()
                rns_nodes = tracker.get_rns_nodes()
            except Exception as e:
                print(f"Error loading node tracker: {e}")
                self._wait_for_enter()
                return

            if not rns_nodes:
                print("No RNS nodes discovered yet.")
                print("\nMake sure rnsd is running and you've exchanged announces")
                print("with other nodes (via NomadNet or Sideband).")
                self._wait_for_enter()
                return

            # Build menu of nodes
            choices = []
            print(f"{'#':<3} {'Name':<20} {'Hash':<12} {'Position'}")
            print("-" * 60)
            for i, node in enumerate(rns_nodes):
                if node.position.is_valid():
                    pos_str = f"({node.position.latitude:.4f}, {node.position.longitude:.4f})"
                else:
                    pos_str = "NOT SET"
                name = node.name[:18] if node.name else node.id[:18]
                hash_short = node.id.replace('rns_', '')[:10]
                print(f"{i+1:<3} {name:<20} {hash_short:<12} {pos_str}")
                choices.append((str(i), f"{name} - {pos_str}"))

            choices.append(("back", "Back to RNS Menu"))
            print()

            choice = self.dialog.menu(
                "Select Node",
                "Choose a node to set its position:",
                choices
            )

            if choice is None or choice == "back":
                break

            try:
                idx = int(choice)
                if 0 <= idx < len(rns_nodes):
                    self._set_single_node_position(rns_nodes[idx])
            except ValueError:
                pass

    def _set_single_node_position(self, node):
        """Set position for a single RNS node."""
        subprocess.run(['clear'], check=False, timeout=5)
        print(f"=== Set Position for {node.name} ===\n")
        print(f"Node ID: {node.id}")
        if node.position.is_valid():
            print(f"Current: ({node.position.latitude:.6f}, {node.position.longitude:.6f})")
        else:
            print("Current: NOT SET")
        print()
        print("Enter coordinates in decimal degrees (e.g., 21.3069 for latitude)")
        print("Tip: Get coords from Google Maps by right-clicking a location\n")

        try:
            lat_str = input("Latitude (e.g., 21.3069): ").strip()
            if not lat_str:
                print("Cancelled.")
                self._wait_for_enter()
                return

            lon_str = input("Longitude (e.g., -157.8583): ").strip()
            if not lon_str:
                print("Cancelled.")
                self._wait_for_enter()
                return

            lat = float(lat_str)
            lon = float(lon_str)

            # Validate
            if not (-90 <= lat <= 90):
                print(f"Invalid latitude: {lat} (must be -90 to 90)")
                self._wait_for_enter()
                return
            if not (-180 <= lon <= 180):
                print(f"Invalid longitude: {lon} (must be -180 to 180)")
                self._wait_for_enter()
                return

            # Optional: name
            name_input = input(f"Name [{node.name}]: ").strip()
            new_name = name_input if name_input else node.name

            # Save to cache
            self._save_rns_node_position(node.id, new_name, lat, lon)
            print(f"\nSaved: {new_name} at ({lat:.6f}, {lon:.6f})")
            print("Refresh the map to see the updated position.")

        except ValueError as e:
            print(f"Invalid input: {e}")
        except (KeyboardInterrupt, EOFError):
            print("\nCancelled.")

        self._wait_for_enter()

    def _save_rns_node_position(self, node_id: str, name: str, lat: float, lon: float):
        """Save an RNS node position to the node cache."""
        import json

        cache_path = get_real_user_home() / '.config' / 'meshforge' / 'node_cache.json'
        cache_path.parent.mkdir(parents=True, exist_ok=True)

        # Load existing cache
        if cache_path.exists():
            try:
                with open(cache_path) as f:
                    data = json.load(f)
            except (json.JSONDecodeError, IOError):
                data = {'version': 1, 'nodes': []}
        else:
            data = {'version': 1, 'nodes': []}

        if 'nodes' not in data:
            data['nodes'] = []

        # Find and update or add node
        found = False
        for node in data['nodes']:
            if node.get('id') == node_id:
                node['name'] = name
                node['position'] = {'latitude': lat, 'longitude': lon, 'altitude': 0}
                node['network'] = 'rns'
                found = True
                break

        if not found:
            data['nodes'].append({
                'id': node_id,
                'name': name,
                'network': 'rns',
                'position': {'latitude': lat, 'longitude': lon, 'altitude': 0},
                'is_online': True,
            })

        # Save
        with open(cache_path, 'w') as f:
            json.dump(data, f, indent=2)

    def _rns_diagnostics(self):
        """Run comprehensive RNS diagnostics."""
        subprocess.run(['clear'], check=False, timeout=5)
        print("=== RNS Diagnostics ===\n")

        try:
            from commands.rns import check_connectivity, get_status
        except ImportError:
            print("RNS commands module not available.")
            print("Run from MeshForge root: sudo python3 src/launcher_tui/main.py")
            self._wait_for_enter()
            return

        # 1. Service status
        print("[1/4] Checking rnsd service...")
        status = get_status()
        status_data = status.data or {}
        running = status_data.get('rnsd_running', False)
        print(f"  rnsd: {'RUNNING' if running else 'NOT RUNNING'}")
        if status_data.get('rnsd_pid'):
            print(f"  PID: {status_data['rnsd_pid']}")
        if status_data.get('service_state'):
            print(f"  State: {status_data['service_state']}")

        # 2. Config check
        print("\n[2/4] Checking configuration...")
        config_exists = status_data.get('config_exists', False)
        print(f"  Config: {'found' if config_exists else 'MISSING'}")
        if config_exists:
            iface_count = status_data.get('interface_count', 0)
            print(f"  Interfaces: {iface_count}")

        # 3. Identity check
        print("\n[3/4] Checking identity...")
        identity_exists = status_data.get('identity_exists', False)
        print(f"  Gateway identity: {'found' if identity_exists else 'not created'}")
        config_dir = ReticulumPaths.get_config_dir()
        rns_identity = config_dir / 'identity'
        print(f"  RNS identity: {'found' if rns_identity.exists() else 'not created'}")

        # 4. Full connectivity check
        print("\n[4/4] Running connectivity check...")
        conn = check_connectivity()
        conn_data = conn.data or {}
        print(f"  RNS importable: {'yes' if conn_data.get('can_import_rns') else 'NO'}")
        if conn_data.get('rns_version'):
            print(f"  RNS version: {conn_data['rns_version']}")
        print(f"  Config valid: {'yes' if conn_data.get('config_valid') else 'NO'}")
        print(f"  Interfaces enabled: {conn_data.get('interfaces_enabled', 0)}")

        # Summary
        issues = conn_data.get('issues', [])
        if issues:
            print(f"\n--- Issues Found ({len(issues)}) ---")
            for issue in issues:
                print(f"  ! {issue}")
        else:
            print("\n--- All checks passed ---")

        # RNS tool availability
        print("\n--- RNS Tool Availability ---")
        for tool in ['rnsd', 'rnstatus', 'rnpath', 'rnprobe', 'rnid', 'rncp', 'rnx']:
            path = shutil.which(tool)
            if path:
                print(f"  {tool}: {path}")
            else:
                print(f"  {tool}: not found")

        self._wait_for_enter()

    @staticmethod
    def _is_root_owned_rns_config(config_path: Path) -> bool:
        """Check if the RNS config is in a root-only location (/root/)."""
        try:
            return str(config_path.resolve()).startswith('/root/')
        except OSError:
            return str(config_path).startswith('/root/')

    def _migrate_rns_config_to_etc(self, source: Path) -> bool:
        """Migrate RNS config from root-owned location to /etc/reticulum/config.

        Copies the config to /etc/reticulum/config (system-wide, preferred location),
        sets world-readable permissions, and renames the old file to avoid confusion.

        Returns True if migration succeeded.
        """
        target = Path('/etc/reticulum/config')
        if target.exists():
            self.dialog.msgbox(
                "Cannot Migrate",
                f"Config already exists at:\n  {target}\n\n"
                f"Remove it first if you want to migrate from:\n  {source}"
            )
            return False
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(source), str(target))
            target.chmod(0o644)
            # Rename old config so rnsd picks up the /etc/ one
            backup = source.with_suffix('.migrated')
            source.rename(backup)
            return True
        except (OSError, PermissionError) as e:
            self.dialog.msgbox("Error", f"Failed to migrate config:\n{e}")
            return False

    def _deploy_rns_template(self) -> Optional[Path]:
        """Deploy RNS template to /etc/reticulum/config (system-wide).

        Returns the path where the config was deployed, or None on failure.
        """
        template = Path(__file__).parent.parent.parent / 'templates' / 'reticulum.conf'
        if not template.exists():
            return None

        # Always deploy to /etc/reticulum/ (system-wide, first in search order)
        target = Path('/etc/reticulum/config')
        if target.exists():
            self.dialog.msgbox(
                "Config Exists",
                f"Config already exists at:\n  {target}\n\n"
                f"Use 'Edit Reticulum Config' to modify it."
            )
            return None
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(template), str(target))
            target.chmod(0o644)  # World-readable so all users and rnsd can read it
            return target
        except (OSError, PermissionError) as e:
            self.dialog.msgbox("Error", f"Failed to deploy config:\n{e}")
            return None

    def _check_meshtastic_plugin(self) -> bool:
        """Check if Meshtastic_Interface.py plugin is installed.

        The plugin bridges RNS over Meshtastic LoRa and must be in
        the RNS interfaces directory (e.g., ~/.reticulum/interfaces/ or
        /etc/reticulum/interfaces/).

        Returns True if plugin is installed.
        """
        plugin_path = ReticulumPaths.get_interfaces_dir() / 'Meshtastic_Interface.py'
        return plugin_path.exists()

    def _install_meshtastic_interface_plugin(self):
        """Download and install Meshtastic_Interface.py plugin from GitHub.

        Clones the RNS_Over_Meshtastic_Gateway repository and copies the
        Meshtastic_Interface.py file to the RNS interfaces directory.
        """
        interfaces_dir = ReticulumPaths.get_interfaces_dir()
        plugin_path = interfaces_dir / 'Meshtastic_Interface.py'

        if plugin_path.exists():
            self.dialog.msgbox(
                "Already Installed",
                f"Meshtastic_Interface.py is already installed at:\n"
                f"  {plugin_path}\n\n"
                f"Size: {plugin_path.stat().st_size} bytes"
            )
            return

        if not self.dialog.yesno(
            "Install Meshtastic Interface Plugin",
            "The Meshtastic_Interface.py plugin is required for\n"
            "bridging RNS over Meshtastic LoRa mesh networks.\n\n"
            "Source: github.com/landandair/RNS_Over_Meshtastic\n\n"
            f"Install to:\n  {plugin_path}\n\n"
            "Requires: git and internet connection.\n\n"
            "Install now?"
        ):
            return

        # Clone repo to temp dir and copy plugin
        import tempfile
        tmp_dir = tempfile.mkdtemp(prefix='meshforge_rns_plugin_')
        clone_url = "https://github.com/landandair/RNS_Over_Meshtastic.git"

        try:
            # Clone the repository
            result = subprocess.run(
                ['git', 'clone', '--depth', '1', clone_url, tmp_dir],
                capture_output=True,
                text=True,
                timeout=60
            )
            if result.returncode != 0:
                self.dialog.msgbox(
                    "Clone Failed",
                    f"Failed to clone repository:\n{result.stderr}\n\n"
                    f"Manual install:\n"
                    f"  git clone {clone_url}\n"
                    f"  cp RNS_Over_Meshtastic/Interface/Meshtastic_Interface.py \\\n"
                    f"    {interfaces_dir}/"
                )
                return

            # Find the plugin file (in Interface/ subfolder per upstream repo)
            source_file = Path(tmp_dir) / 'Interface' / 'Meshtastic_Interface.py'
            if not source_file.exists():
                # Fallback: check repo root in case structure changes
                source_file = Path(tmp_dir) / 'Meshtastic_Interface.py'
            if not source_file.exists():
                self.dialog.msgbox(
                    "Plugin Not Found",
                    f"Meshtastic_Interface.py not found in repository.\n\n"
                    f"Expected at: Interface/Meshtastic_Interface.py\n"
                    f"Check: {clone_url}"
                )
                return

            # Create interfaces directory and copy plugin
            interfaces_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(source_file), str(plugin_path))
            plugin_path.chmod(0o644)

            self.dialog.msgbox(
                "Plugin Installed",
                f"Meshtastic_Interface.py installed to:\n"
                f"  {plugin_path}\n\n"
                f"Restart rnsd to load the new interface:\n"
                f"  sudo systemctl restart rnsd"
            )

        except FileNotFoundError:
            self.dialog.msgbox(
                "Git Not Found",
                "git is required to download the plugin.\n\n"
                "Install git: sudo apt install git\n\n"
                "Or manually download from:\n"
                f"  {clone_url}"
            )
        except subprocess.TimeoutExpired:
            self.dialog.msgbox(
                "Timeout",
                "Download timed out. Check your internet connection."
            )
        except (OSError, PermissionError) as e:
            self.dialog.msgbox(
                "Install Failed",
                f"Failed to install plugin:\n{e}\n\n"
                f"Try running with sudo, or manually copy:\n"
                f"  sudo cp Meshtastic_Interface.py {interfaces_dir}/"
            )
        finally:
            # Clean up temp dir
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def _view_rns_config(self):
        """View current Reticulum config."""
        subprocess.run(['clear'], check=False, timeout=5)
        print("=== Reticulum Configuration ===\n")

        config_path = ReticulumPaths.get_config_file()

        if config_path.exists():
            # Warn if config is in root-only location
            if self._is_root_owned_rns_config(config_path):
                print(f"Config: {config_path}")
                print(f"  ** This config is in /root/ - not editable without sudo **")
                print(f"  ** Use 'Edit Reticulum Config' to migrate to /etc/reticulum/ **\n")
            else:
                print(f"Config: {config_path}\n")
            try:
                content = config_path.read_text()
                print(content)

                # Show validation warnings inline
                issues = self._validate_rns_config_content(content)
                if issues:
                    print("\n--- Config Issues ---")
                    for issue in issues:
                        print(f"  ! {issue}")
            except PermissionError:
                print(f"Permission denied reading {config_path}")
                print(f"Try: sudo cat {config_path}")
        else:
            print(f"No Reticulum config found at: {config_path}")
            user_home = get_real_user_home()
            print(f"\nMeshForge checks (in order):")
            print(f"  1. /etc/reticulum/config  (system-wide, preferred)")
            print(f"  2. {user_home}/.config/reticulum/config")
            print(f"  3. {user_home}/.reticulum/config")
            if os.geteuid() == 0 and os.environ.get('SUDO_USER'):
                print(f"\nNote: rnsd (running as root) uses /root/.reticulum/config")
                print(f"  For shared use, deploy to /etc/reticulum/config")
            print(f"\nTo create: use 'Edit Reticulum Config' to deploy template")
            print(f"Template:  templates/reticulum.conf")

        # Show Meshtastic_Interface plugin status
        plugin_path = ReticulumPaths.get_interfaces_dir() / 'Meshtastic_Interface.py'
        print(f"\n--- Meshtastic Interface Plugin ---")
        if plugin_path.exists():
            print(f"  Installed: {plugin_path}")
            print(f"  Size: {plugin_path.stat().st_size} bytes")
        else:
            print(f"  NOT INSTALLED")
            print(f"  Expected at: {plugin_path}")
            print(f"  Source: https://github.com/landandair/RNS_Over_Meshtastic")
            print(f"  Use 'Install Meshtastic Interface' from the RNS menu to install.")

        self._wait_for_enter()

    def _edit_rns_config(self):
        """Edit Reticulum config with available editor. Deploys template if no config exists."""
        config_path = ReticulumPaths.get_config_file()

        if not config_path.exists():
            # Offer to deploy from template to /etc/reticulum/config (system-wide)
            template = Path(__file__).parent.parent.parent / 'templates' / 'reticulum.conf'

            if template.exists():
                target = Path('/etc/reticulum/config')
                if self.dialog.yesno(
                    "Deploy Reticulum Config",
                    f"No Reticulum config found.\n\n"
                    f"Deploy template to:\n  {target}\n\n"
                    f"This sets up RNS with:\n"
                    f"  - share_instance = Yes (required for rnstatus)\n"
                    f"  - AutoInterface (local network discovery)\n"
                    f"  - Meshtastic_Interface on port 4403\n\n"
                    f"You can edit it after deployment."
                ):
                    deployed = self._deploy_rns_template()
                    if deployed:
                        config_path = deployed
                    else:
                        return
                else:  # User said No
                    return
            else:
                self.dialog.msgbox(
                    "No Config",
                    "No Reticulum config found and template missing.\n\n"
                    "Install RNS first: pipx install rns\n"
                    "Then run rnsd once to generate default config."
                )
                return

        # If config is in /root/, offer to migrate to /etc/reticulum/
        if self._is_root_owned_rns_config(config_path):
            if self.dialog.yesno(
                "Migrate Config",
                f"Config is at:\n  {config_path}\n\n"
                f"This location requires root access to edit.\n\n"
                f"Migrate to /etc/reticulum/config?\n"
                f"(System-wide location, accessible by rnsd and all users)"
            ):
                if self._migrate_rns_config_to_etc(config_path):
                    config_path = Path('/etc/reticulum/config')
                    self.dialog.msgbox(
                        "Migrated",
                        f"Config moved to: {config_path}\n\n"
                        f"Restart rnsd to apply:\n"
                        f"  sudo systemctl restart rnsd"
                    )
                # If migration failed, continue with original path

        # Find editor
        editor = None
        for cmd in ['nano', 'vim', 'vi']:
            if shutil.which(cmd):
                editor = cmd
                break

        if not editor:
            self.dialog.msgbox("Error", "No text editor found (nano, vim, vi)")
            return

        subprocess.run([editor, str(config_path)], timeout=None)

    def _validate_rns_config_content(self, content: str) -> list:
        """Validate RNS config content and return list of issues found.

        Checks for common misconfigurations that cause rnstatus/rnpath failures:
        - Missing [reticulum] section
        - Missing share_instance = Yes (required for client apps to connect)
        - No interfaces configured
        - No Meshtastic_Interface (needed for mesh bridging)
        - Meshtastic_Interface.py plugin not installed
        """
        issues = []
        content_lower = content.lower()

        # Check [reticulum] section exists
        if '[reticulum]' not in content_lower:
            issues.append("Missing [reticulum] section")

        # Check share_instance (required for rnstatus/rnpath to connect to rnsd)
        has_share = False
        for line in content.split('\n'):
            stripped = line.strip().lower()
            if stripped.startswith('#'):
                continue
            if 'share_instance' in stripped:
                if 'yes' in stripped or 'true' in stripped:
                    has_share = True
                break
        if not has_share:
            issues.append("share_instance not set to Yes (rnstatus/client apps won't connect)")

        # Check for at least one active interface
        has_interface = False
        has_meshtastic = False
        for line in content.split('\n'):
            stripped = line.strip()
            if stripped.startswith('#'):
                continue
            if stripped.startswith('[[') and stripped.endswith(']]'):
                has_interface = True
            if 'meshtastic_interface' in stripped.lower() and 'type' in stripped.lower():
                has_meshtastic = True

        if not has_interface:
            issues.append("No interfaces configured")

        # Check Meshtastic_Interface status: config reference + plugin file
        # Only flag as issue if plugin is missing entirely or config references
        # a plugin that isn't installed. Having the plugin installed but not
        # configured is fine - user can enable it when ready.
        plugin_path = ReticulumPaths.get_interfaces_dir() / 'Meshtastic_Interface.py'
        plugin_installed = plugin_path.exists()

        if not has_meshtastic and not plugin_installed:
            issues.append("No Meshtastic_Interface configured (needed for mesh bridging)")
        elif has_meshtastic and not plugin_installed:
            issues.append(
                f"Meshtastic_Interface.py plugin not installed at "
                f"{ReticulumPaths.get_interfaces_dir()}/\n"
                f"    Install from: https://github.com/landandair/RNS_Over_Meshtastic"
            )

        return issues

    def _check_rns_setup(self) -> bool:
        """Check RNS setup and offer to fix common issues.

        Available via 'Check RNS Setup' menu item. Returns True if setup
        looks OK or user chose to continue anyway, False if user wants
        to go back.
        """
        config_path = ReticulumPaths.get_config_file()

        if not config_path.exists():
            template = Path(__file__).parent.parent.parent / 'templates' / 'reticulum.conf'
            if template.exists():
                target = Path('/etc/reticulum/config')
                if self.dialog.yesno(
                    "RNS Not Configured",
                    f"No Reticulum config found.\n\n"
                    f"RNS tools (rnstatus, rnpath) and the gateway bridge\n"
                    f"require a config file to function.\n\n"
                    f"Deploy MeshForge template to:\n"
                    f"  {target}\n\n"
                    f"(Sets up shared instance + Meshtastic bridge)"
                ):
                    deployed = self._deploy_rns_template()
                    if deployed:
                        self.dialog.msgbox(
                            "Config Deployed",
                            f"Deployed to: {deployed}\n\n"
                            f"Restart rnsd to apply:\n"
                            f"  sudo systemctl restart rnsd"
                        )
                        config_path = deployed
            return True  # Continue to menu either way

        # Config exists - check if it's in a root-only location
        if self._is_root_owned_rns_config(config_path):
            if self.dialog.yesno(
                "Config in /root/",
                f"RNS config found at:\n  {config_path}\n\n"
                f"This location requires root access to edit.\n\n"
                f"Migrate to /etc/reticulum/config?\n"
                f"(System-wide location, accessible by all users)"
            ):
                if self._migrate_rns_config_to_etc(config_path):
                    config_path = Path('/etc/reticulum/config')
                    self.dialog.msgbox(
                        "Migrated",
                        f"Config moved to: {config_path}\n\n"
                        f"Restart rnsd to apply:\n"
                        f"  sudo systemctl restart rnsd"
                    )

        # Config exists - validate it
        try:
            content = config_path.read_text()
            issues = self._validate_rns_config_content(content)
            if issues:
                msg = f"Config: {config_path}\n\nIssues found:\n"
                for issue in issues:
                    msg += f"  - {issue}\n"
                msg += f"\nUse 'Edit Reticulum Config' to fix these issues."
                self.dialog.msgbox("RNS Config Issues", msg)
        except PermissionError:
            self.dialog.msgbox(
                "Permission Denied",
                f"Cannot read config at:\n  {config_path}\n\n"
                f"Run MeshForge with sudo to access this file,\n"
                f"or use 'Edit Reticulum Config' to migrate it."
            )

        # Check for Meshtastic_Interface.py plugin (separate from config validation)
        if not self._check_meshtastic_plugin():
            if self.dialog.yesno(
                "Meshtastic Interface Plugin Missing",
                "The Meshtastic_Interface.py plugin is not installed.\n\n"
                "This plugin is required for bridging RNS over\n"
                "Meshtastic LoRa mesh networks.\n\n"
                f"Expected at:\n"
                f"  {ReticulumPaths.get_interfaces_dir()}/Meshtastic_Interface.py\n\n"
                "Download and install it now?"
            ):
                self._install_meshtastic_interface_plugin()

        return True

    def _run_rns_tool(self, cmd: list, tool_name: str):
        """Run an RNS CLI tool with address-in-use error detection.

        Captures both stdout and stderr to detect specific error patterns.
        RNS logs errors to stdout in some configurations, so both streams
        must be checked for the 'Address already in use' pattern.

        Args:
            cmd: Command and arguments to run
            tool_name: Display name for error messages (e.g., "rnpath")
        """
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=15
            )
            # RNS tools may log errors to stdout or stderr depending on config
            combined = (result.stdout or "") + (result.stderr or "")

            if result.returncode == 0:
                # Success - show normal output
                if result.stdout:
                    print(result.stdout, end='')
            elif "address already in use" in combined.lower():
                # Suppress noisy traceback, show actionable diagnostics
                print("\nError: RNS port conflict (Address already in use)")
                print("Another process is bound to the RNS AutoInterface port.\n")
                self._diagnose_rns_port_conflict()
            elif "no shared" in combined.lower():
                # rnsd not running or share_instance not enabled
                print("\nNo shared RNS instance available.")
                # Check if config has share_instance
                cfg_path = ReticulumPaths.get_config_file()
                if cfg_path.exists():
                    try:
                        cfg_content = cfg_path.read_text()
                        issues = self._validate_rns_config_content(cfg_content)
                        if issues:
                            print(f"\nConfig issues ({cfg_path}):")
                            for issue in issues:
                                print(f"  - {issue}")
                            print("\nFix config: use 'Edit Reticulum Config' menu")
                        else:
                            print(f"\nConfig looks OK ({cfg_path})")
                            print("rnsd may not be running:")
                            print("  sudo systemctl start rnsd")
                    except PermissionError:
                        print(f"\nCannot read config: {cfg_path}")
                        print("  Run MeshForge with sudo")
                else:
                    print(f"\nNo config found at: {cfg_path}")
                    print("Use 'Edit Reticulum Config' to deploy template")
            else:
                # Generic failure - show output and suggestions
                if result.stdout:
                    print(result.stdout, end='')
                print(f"\n{tool_name} failed. Possible causes:")
                print("  - rnsd not running: sudo systemctl start rnsd")
                print("  - RNS not installed: pipx install rns")
                if result.stderr and result.stderr.strip():
                    # Show last 3 lines of stderr for context
                    err_lines = result.stderr.strip().split('\n')[-3:]
                    print("\nDetails:")
                    for line in err_lines:
                        print(f"  {line}")
        except FileNotFoundError:
            print(f"\n{tool_name} not found. Is RNS installed?")
            print("Install: pipx install rns")
        except subprocess.TimeoutExpired:
            print(f"\n{tool_name} timed out. RNS may be unresponsive.")
            print("Try restarting rnsd: sudo systemctl restart rnsd")

    def _diagnose_rns_port_conflict(self):
        """Print diagnostic info for RNS Address-in-use port conflicts."""
        try:
            rnsd_check = subprocess.run(
                ['pgrep', '-f', 'rnsd'],
                capture_output=True, text=True, timeout=5
            )
            if rnsd_check.returncode == 0:
                pid = rnsd_check.stdout.strip().split('\n')[0]
                print(f"rnsd is running (PID: {pid}) but may need a restart:")
                print("  sudo systemctl restart rnsd")
            else:
                print("No rnsd found. A stale process may be holding the port.")
                print("  Find it:    sudo lsof -i UDP:29716")
                print("  Kill stale: pkill -f rnsd")
                print("  Or wait ~30s for the socket to timeout")
        except Exception:
            print("  Try: sudo systemctl restart rnsd")

    # =========================================================================
    # AREDN Menu
    # =========================================================================

    def _aredn_menu(self):
        """AREDN mesh network tools."""
        while True:
            choices = [
                ("status", "Node Status"),
                ("neighbors", "Neighbors & Links"),
                ("services", "Advertised Services"),
                ("web", "Open AREDN Web UI"),
                ("scan", "Scan Network"),
                ("back", "Back"),
            ]

            choice = self.dialog.menu(
                "AREDN Mesh",
                "AREDN mesh network tools:",
                choices
            )

            if choice is None or choice == "back":
                break

            if choice == "status":
                self._aredn_node_status()
            elif choice == "neighbors":
                self._aredn_neighbors()
            elif choice == "services":
                self._aredn_services()
            elif choice == "web":
                self._aredn_web()
            elif choice == "scan":
                self._aredn_scan()

    def _aredn_get_node_ip(self) -> str:
        """Get AREDN node IP - try common defaults."""
        import socket
        # Try common AREDN addresses
        for host in ['localnode.local.mesh', '10.0.0.1', 'localnode']:
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(2)
                try:
                    result = sock.connect_ex((host, 80))
                    if result == 0:
                        return host
                finally:
                    sock.close()
            except Exception:
                continue
        return ""

    def _aredn_node_status(self):
        """Show local AREDN node status."""
        subprocess.run(['clear'], check=False, timeout=5)
        print("=== AREDN Node Status ===\n")

        try:
            from utils.aredn import get_aredn_node

            node_ip = self._aredn_get_node_ip()
            if not node_ip:
                print("No AREDN node found on local network.")
                print("\nTried: localnode.local.mesh, 10.0.0.1")
                print("\nIs your AREDN node connected?")
                self._wait_for_enter()
                return

            print(f"Connecting to {node_ip}...\n")
            node = get_aredn_node(node_ip)

            if node:
                print(f"  Hostname:  {node.hostname}")
                print(f"  IP:        {node.ip}")
                print(f"  Model:     {node.model}")
                print(f"  Firmware:  {node.firmware_version}")
                print(f"  SSID:      {node.ssid}")
                print(f"  Channel:   {node.channel} ({node.frequency})")
                print(f"  Width:     {node.channel_width}")
                print(f"  Status:    {node.mesh_status}")
                print(f"  Uptime:    {node.uptime}")
                print(f"  Tunnels:   {node.tunnel_count}")
                if node.loads:
                    print(f"  Load:      {', '.join(str(l) for l in node.loads)}")
            else:
                print(f"Connected to {node_ip} but couldn't parse node info.")
                print(f"Check: http://{node_ip}:8080/cgi-bin/sysinfo.json")

        except ImportError:
            print("AREDN utilities not available.")
            print("Check: src/utils/aredn.py")
        except Exception as e:
            print(f"Error: {e}")

        self._wait_for_enter()

    def _aredn_neighbors(self):
        """Show AREDN neighbor links."""
        subprocess.run(['clear'], check=False, timeout=5)
        print("=== AREDN Neighbors ===\n")

        try:
            from utils.aredn import AREDNClient

            node_ip = self._aredn_get_node_ip()
            if not node_ip:
                print("No AREDN node found. Is it connected?")
                self._wait_for_enter()
                return

            client = AREDNClient(node_ip)
            neighbors = client.get_neighbors()

            if neighbors:
                print(f"Found {len(neighbors)} neighbor(s):\n")
                for link in neighbors:
                    snr_str = f"SNR:{link.snr}dB" if link.snr else ""
                    print(f"  {link.link_type.value:4s} {link.hostname:<30s} {snr_str}")
                    if link.signal:
                        print(f"       Signal:{link.signal} Noise:{link.noise} Rate:{link.tx_rate}Mbps")
            else:
                print("No neighbors found.")
                print("Check that your AREDN node has active RF links.")

        except ImportError:
            print("AREDN utilities not available.")
        except Exception as e:
            print(f"Error: {e}")

        self._wait_for_enter()

    def _aredn_services(self):
        """Show AREDN advertised services."""
        subprocess.run(['clear'], check=False, timeout=5)
        print("=== AREDN Services ===\n")

        try:
            from utils.aredn import AREDNClient

            node_ip = self._aredn_get_node_ip()
            if not node_ip:
                print("No AREDN node found.")
                self._wait_for_enter()
                return

            client = AREDNClient(node_ip)
            sysinfo = client.get_sysinfo(services=True)

            if sysinfo and 'services' in sysinfo:
                services = sysinfo['services']
                if services:
                    print(f"Found {len(services)} service(s):\n")
                    for svc in services:
                        name = svc.get('name', 'Unknown')
                        protocol = svc.get('protocol', '')
                        url = svc.get('url', '')
                        print(f"  {name} ({protocol})")
                        if url:
                            print(f"    {url}")
                else:
                    print("No services advertised.")
            else:
                print("Could not retrieve services.")

        except ImportError:
            print("AREDN utilities not available.")
        except Exception as e:
            print(f"Error: {e}")

        self._wait_for_enter()

    def _aredn_web(self):
        """Show AREDN web UI URL."""
        node_ip = self._aredn_get_node_ip()
        if node_ip:
            msg = (
                f"AREDN Node Web UI\n\n"
                f"  URL: http://{node_ip}:8080\n\n"
                f"Open in any browser on your network.\n\n"
                f"Provides: configuration, neighbor map,\n"
                f"  services, firmware updates"
            )
        else:
            msg = (
                "No AREDN node found on local network.\n\n"
                "Tried: localnode.local.mesh, 10.0.0.1\n\n"
                "Make sure your AREDN node is connected\n"
                "and accessible from this machine."
            )
        self.dialog.msgbox("AREDN Web UI", msg)

    def _aredn_scan(self):
        """Scan for AREDN nodes on network."""
        subprocess.run(['clear'], check=False, timeout=5)
        print("=== AREDN Network Scan ===\n")
        print("Scanning 10.0.0.0/24 for AREDN nodes...\n")

        try:
            from utils.aredn import AREDNScanner

            scanner = AREDNScanner()
            nodes = scanner.scan_subnet("10.0.0.0/24")

            if nodes:
                print(f"Found {len(nodes)} node(s):\n")
                for node in nodes:
                    print(f"  {node.hostname:<30s} {node.ip:<15s} {node.model}")
            else:
                print("No AREDN nodes found on 10.0.0.0/24")
                print("\nYour network may use a different subnet.")
                print("Check your AREDN node's IP configuration.")

        except ImportError:
            print("AREDN utilities not available.")
        except Exception as e:
            print(f"Error: {e}")

        self._wait_for_enter()

    # =========================================================================
    # Config Menu - meshtasticd config.d/ management
    # =========================================================================

    def _config_menu(self):
        """Configuration management for meshtasticd."""
        while True:
            choices = [
                ("view", "View Active Config"),
                ("overlays", "View config.d/ Overlays"),
                ("available", "Available HAT Configs"),
                ("presets", "LoRa Presets"),
                ("channels", "Channel Configuration"),
                ("meshtasticd", "Advanced meshtasticd Config"),
                ("settings", "MeshForge Settings"),
                ("wizard", "Run Setup Wizard"),
                ("back", "Back"),
            ]

            choice = self.dialog.menu(
                "Configuration",
                "meshtasticd & MeshForge configuration:",
                choices
            )

            if choice is None or choice == "back":
                break

            if choice == "view":
                self._view_active_config()
            elif choice == "overlays":
                self._view_config_overlays()
            elif choice == "available":
                self._view_available_hats()
            elif choice == "presets":
                self._radio_presets_menu()
            elif choice == "channels":
                self._channel_config_menu()
            elif choice == "meshtasticd":
                self._meshtasticd_menu()
            elif choice == "settings":
                self._settings_menu()
            elif choice == "wizard":
                self._run_first_run_wizard()

    def _view_active_config(self):
        """Show the active meshtasticd config.yaml."""
        subprocess.run(['clear'], check=False, timeout=5)
        print("=== meshtasticd config.yaml ===\n")

        config_path = Path('/etc/meshtasticd/config.yaml')
        if config_path.exists():
            print(f"File: {config_path}\n")
            try:
                print(config_path.read_text())
            except PermissionError:
                print("Permission denied. Try: sudo cat /etc/meshtasticd/config.yaml")
        else:
            print("config.yaml not found!")
            print("\nInstall meshtasticd:")
            print("  sudo apt install meshtasticd")
            print("  # or run the MeshForge installer")

        self._wait_for_enter()

    def _view_config_overlays(self):
        """Show config.d/ overlay files."""
        subprocess.run(['clear'], check=False, timeout=5)
        print("=== config.d/ Overlays ===\n")

        config_d = Path('/etc/meshtasticd/config.d')
        if not config_d.exists():
            print("config.d/ directory not found.")
            print("Create it: sudo mkdir -p /etc/meshtasticd/config.d")
            self._wait_for_enter()
            return

        overlays = sorted(config_d.glob('*.yaml'))
        if not overlays:
            print("No overlay files in config.d/")
            print("\nOverlays override sections from config.yaml")
            print("MeshForge writes here instead of touching config.yaml")
        else:
            print(f"Found {len(overlays)} overlay(s):\n")
            for f in overlays:
                size = f.stat().st_size
                print(f"  {f.name} ({size} bytes)")

            # Show contents of each
            print("\n" + "=" * 50)
            for f in overlays:
                print(f"\n--- {f.name} ---")
                try:
                    print(f.read_text())
                except PermissionError:
                    print("  (permission denied)")

        self._wait_for_enter()

    def _view_available_hats(self):
        """Show available HAT configurations from meshtasticd package."""
        subprocess.run(['clear'], check=False, timeout=5)
        print("=== Available HAT Configs ===\n")

        available_d = Path('/etc/meshtasticd/available.d')
        if not available_d.exists():
            print("available.d/ not found.")
            print("meshtasticd package should provide this.")
            print("\nInstall: sudo apt install meshtasticd")
            self._wait_for_enter()
            return

        configs = sorted(available_d.glob('*.yaml'))
        if not configs:
            print("No HAT configs available.")
        else:
            print(f"Found {len(configs)} HAT config(s):\n")
            for i, f in enumerate(configs, 1):
                print(f"  {i:2d}. {f.name}")

            print("\nTo activate a HAT config:")
            print("  sudo cp /etc/meshtasticd/available.d/<file>.yaml \\")
            print("         /etc/meshtasticd/config.d/")
            print("  sudo systemctl restart meshtasticd")
            print("\nWARNING: Only ONE Lora config should be in config.d/")

        self._wait_for_enter()

    def _open_web_client(self):
        """Show/open meshtasticd web client for full radio configuration."""
        import socket
        local_ip = "localhost"
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.settimeout(2)
            try:
                s.connect(("8.8.8.8", 80))
                local_ip = s.getsockname()[0]
            finally:
                s.close()
        except Exception:
            pass

        web_url = f"https://{local_ip}:9443"

        # Check if web server is responding
        port_ok = False
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                sock.settimeout(2)
                port_ok = sock.connect_ex((local_ip, 9443)) == 0
            finally:
                sock.close()
        except Exception as e:
            logger.debug(f"Socket check for web client failed: {e}")

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
                f"Access from any device on your network.\n\n"
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
    # Terminal-native utilities (used by menus above)
    # =========================================================================

    def _run_terminal_status(self):
        """Run meshforge-status (terminal-native one-shot status)."""
        subprocess.run(['clear'], check=False, timeout=5)
        try:
            # Run status script directly, showing output in real-time
            result = subprocess.run(
                [sys.executable, str(self.src_dir / 'cli' / 'status.py')],
                timeout=20
            )
            if result.returncode != 0:
                print("\nStatus check encountered an error.")
        except subprocess.TimeoutExpired:
            print("\n\nStatus check timed out (20s).")
        except KeyboardInterrupt:
            print("\n\nAborted.")

        try:
            self._wait_for_enter("\nPress Enter to return to menu...")
        except KeyboardInterrupt:
            print()

    def _run_terminal_network(self):
        """Show network diagnostics directly in terminal."""
        subprocess.run(['clear'], check=False, timeout=5)
        print("MeshForge Network Status")
        print("=" * 50)
        print()

        import socket as sock

        # Port checks
        print("Port Checks:")
        ports = [
            (4403, 'meshtasticd TCP API'),
            (9443, 'meshtasticd Web Client'),
            (37428, 'rnsd (RNS shared instance)'),
            (1883, 'MQTT broker'),
        ]

        for port, desc in ports:
            try:
                s = sock.socket(sock.AF_INET, sock.SOCK_STREAM)
                try:
                    s.settimeout(1)
                    result = s.connect_ex(('127.0.0.1', port))
                finally:
                    s.close()
                if result == 0:
                    print(f"  \033[0;32m●\033[0m {port:<6} {desc}")
                else:
                    print(f"  \033[2m○\033[0m {port:<6} {desc} (not listening)")
            except Exception:
                print(f"  ? {port:<6} {desc} (check failed)")

        # Local IP
        print()
        try:
            s = sock.socket(sock.AF_INET, sock.SOCK_DGRAM)
            s.settimeout(2)
            try:
                s.connect(("8.8.8.8", 80))
                local_ip = s.getsockname()[0]
            finally:
                s.close()
            print(f"  Local IP: {local_ip}")
        except Exception:
            print("  Local IP: Unable to determine")

        # Internet connectivity
        print()
        print("Connectivity:")
        try:
            s = sock.socket(sock.AF_INET, sock.SOCK_STREAM)
            try:
                s.settimeout(3)
                result = s.connect_ex(('8.8.8.8', 53))
            finally:
                s.close()
            if result == 0:
                print(f"  \033[0;32m●\033[0m Internet (Google DNS)")
            else:
                print(f"  \033[0;31m●\033[0m Internet (no route to 8.8.8.8)")
        except Exception:
            print(f"  \033[0;31m●\033[0m Internet (unreachable)")

        print()
        print("-" * 50)
        try:
            self._wait_for_enter("\nPress Enter to return to menu...")
        except KeyboardInterrupt:
            print()

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
            self._wait_for_enter()
        except Exception as e:
            self.dialog.msgbox("Error", f"Failed to read log: {e}")

    # =========================================================================
    # Network Tools
    # =========================================================================

    def _ping_test(self):
        """Run ping test."""
        host = self.dialog.inputbox(
            "Ping Test",
            "Enter host to ping:",
            "8.8.8.8"
        )

        if not host:
            return

        if not self._validate_hostname(host):
            self.dialog.msgbox("Error", "Invalid hostname or IP address.")
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

    def _meshtastic_discovery(self):
        """Discover Meshtastic devices."""
        self.dialog.infobox("Discovery", "Scanning for Meshtastic devices...")

        devices = []

        # Check TCP localhost
        import socket
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                sock.settimeout(2)
                if sock.connect_ex(('localhost', 4403)) == 0:
                    devices.append("TCP: localhost:4403 (meshtasticd)")
            finally:
                sock.close()
        except Exception as e:
            logger.debug(f"Socket check for meshtasticd failed: {e}")

        # Check serial ports
        serial_ports = list(Path('/dev').glob('ttyUSB*')) + list(Path('/dev').glob('ttyACM*'))
        for port in serial_ports:
            devices.append(f"Serial: {port}")

        if not devices:
            text = "No Meshtastic devices found.\n\nMake sure meshtasticd is running."
        else:
            # BLE hint
            devices.append("")
            devices.append("BLE devices require scanning:")
            devices.append("  meshtastic --ble-scan")
            text = "Found devices:\n\n" + "\n".join(devices)

        self.dialog.msgbox("Meshtastic Discovery", text)

    def _dns_lookup(self):
        """Perform DNS lookup."""
        host = self.dialog.inputbox(
            "DNS Lookup",
            "Enter hostname to lookup:",
            "meshtastic.org"
        )

        if not host:
            return

        if not self._validate_hostname(host):
            self.dialog.msgbox("Error", "Invalid hostname.")
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
        """Gateway bridge start/stop/status menu."""
        while True:
            # Check if bridge is already running
            bridge_running = self._is_bridge_running()

            if bridge_running:
                choices = [
                    ("status", "Bridge Status"),
                    ("logs", "View Bridge Logs"),
                    ("stop", "Stop Bridge"),
                    ("back", "Back"),
                ]
                subtitle = "Gateway bridge is RUNNING (background)"
            else:
                choices = [
                    ("start", "Start Bridge (background)"),
                    ("start-fg", "Start Bridge (foreground, live logs)"),
                    ("back", "Back"),
                ]
                subtitle = "Gateway bridge is STOPPED"

            choice = self.dialog.menu(
                "Gateway Bridge",
                f"RNS <-> Meshtastic bridge:\n\n{subtitle}",
                choices
            )

            if choice is None or choice == "back":
                break

            if choice == "start":
                self._start_bridge_background()
            elif choice == "start-fg":
                self._start_bridge_foreground()
            elif choice == "status":
                self._show_bridge_status()
            elif choice == "stop":
                self._stop_bridge()
            elif choice == "logs":
                self._show_bridge_logs()

    def _is_bridge_running(self) -> bool:
        """Check if the gateway bridge process is running."""
        try:
            result = subprocess.run(
                ['pgrep', '-f', 'bridge_cli.py'],
                capture_output=True, text=True, timeout=5
            )
            return result.returncode == 0
        except Exception:
            return False

    def _start_bridge_background(self):
        """Start gateway bridge as a background process."""
        if self._is_bridge_running():
            self.dialog.msgbox("Already Running", "Gateway bridge is already running.")
            return

        self.dialog.infobox("Starting", "Starting gateway bridge in background...")

        try:
            import tempfile
            log_fd, log_path_str = tempfile.mkstemp(
                suffix='.log', prefix='meshforge-gateway-'
            )
            log_path = Path(log_path_str)
            self._bridge_log_path = log_path
            log_file = os.fdopen(log_fd, 'w')
            subprocess.Popen(
                [sys.executable, str(self.src_dir / 'gateway' / 'bridge_cli.py')],
                stdout=log_file,
                stderr=subprocess.STDOUT,
                start_new_session=True
            )
            log_file.close()

            # Wait briefly and verify it started
            import time
            time.sleep(3)

            if self._is_bridge_running():
                self.dialog.msgbox("Started",
                    "Gateway bridge started in background.\n\n"
                    f"Logs: {log_path}\n\n"
                    "Use 'Stop Bridge' to shut it down.")
            else:
                # Read log for error info
                try:
                    error_text = log_path.read_text()[-300:]
                except Exception:
                    error_text = "(no log output)"
                self.dialog.msgbox("Failed",
                    f"Bridge failed to start.\n\n{error_text}")

        except Exception as e:
            self.dialog.msgbox("Error", f"Failed to start bridge:\n{e}")

    def _start_bridge_foreground(self):
        """Start gateway bridge in foreground with live output."""
        if self._is_bridge_running():
            self.dialog.msgbox("Already Running",
                "Gateway bridge is already running in background.\n\n"
                "Stop it first to run in foreground.")
            return

        subprocess.run(['clear'], check=False, timeout=5)
        print("Starting Gateway Bridge (foreground)...")
        print("Press Ctrl+C to stop\n")
        try:
            subprocess.run(
                [sys.executable, str(self.src_dir / 'gateway' / 'bridge_cli.py')],
                timeout=None
            )
        except KeyboardInterrupt:
            print("\nBridge stopped.")
        try:
            self._wait_for_enter()
        except KeyboardInterrupt:
            print()

    def _stop_bridge(self):
        """Stop the background gateway bridge."""
        if not self._is_bridge_running():
            self.dialog.msgbox("Not Running", "Gateway bridge is not running.")
            return

        if not self.dialog.yesno("Stop Bridge", "Stop the gateway bridge?"):
            return

        try:
            subprocess.run(
                ['pkill', '-f', 'bridge_cli.py'],
                capture_output=True, timeout=10
            )
            import time
            time.sleep(1)

            if self._is_bridge_running():
                # Force kill
                subprocess.run(
                    ['pkill', '-9', '-f', 'bridge_cli.py'],
                    capture_output=True, timeout=10
                )

            self.dialog.msgbox("Stopped", "Gateway bridge stopped.")
        except Exception as e:
            self.dialog.msgbox("Error", f"Failed to stop bridge:\n{e}")

    def _find_bridge_log(self) -> Optional[Path]:
        """Find the gateway bridge log file.

        Checks the stored path from the current session first, then searches
        /tmp for the most recent meshforge-gateway-*.log file.
        """
        if self._bridge_log_path and self._bridge_log_path.exists():
            return self._bridge_log_path

        # Search for most recent gateway log in /tmp
        try:
            logs = sorted(
                Path('/tmp').glob('meshforge-gateway-*.log'),
                key=lambda p: p.stat().st_mtime,
                reverse=True
            )
        except OSError:
            logs = []
        if logs:
            self._bridge_log_path = logs[0]
            return logs[0]

        return None

    def _show_bridge_status(self):
        """Show gateway bridge log tail."""
        log_path = self._find_bridge_log()
        if not log_path:
            self.dialog.msgbox("No Logs", "No gateway log found.")
            return

        try:
            lines = log_path.read_text().strip().split('\n')
            # Show last 30 lines
            tail = '\n'.join(lines[-30:])
            self.dialog.msgbox(f"Bridge Status (last 30 lines)\n{log_path}", tail)
        except Exception as e:
            self.dialog.msgbox("Error", f"Failed to read log:\n{e}")

    def _show_bridge_logs(self):
        """Show full gateway bridge logs in less."""
        log_path = self._find_bridge_log()
        if not log_path:
            self.dialog.msgbox("No Logs", "No gateway log found.")
            return

        subprocess.run(['clear'], check=False, timeout=5)
        try:
            subprocess.run(['less', '-R', '-X', '+G', str(log_path)], timeout=300)
        except KeyboardInterrupt:
            pass

    def _service_menu(self):
        """Service management menu - terminal-native."""
        while True:
            choices = [
                ("status", "Service Status (all)"),
                ("meshtasticd", "Manage meshtasticd"),
                ("rnsd", "Manage rnsd"),
                ("restart-mesh", "Restart meshtasticd"),
                ("start-rns", "Start rnsd"),
                ("restart-rns", "Restart rnsd"),
                ("install", "Install meshtasticd"),
                ("back", "Back"),
            ]

            choice = self.dialog.menu(
                "Service Management",
                "Start/stop/restart services:",
                choices
            )

            if choice is None or choice == "back":
                break

            if choice == "status":
                subprocess.run(['clear'], check=False, timeout=5)
                print("=== Service Status ===\n")
                warnings = []
                use_direct_rnsd = not self._has_systemd_unit('rnsd')

                for svc in ['meshtasticd', 'rnsd', 'meshforge']:
                    # Special handling for rnsd without systemd unit
                    if svc == 'rnsd' and use_direct_rnsd:
                        if self._is_rnsd_running():
                            print(f"  \033[0;32m●\033[0m {svc:<18} running (process)")
                        else:
                            print(f"  \033[2m○\033[0m {svc:<18} stopped")
                        continue

                    try:
                        result = subprocess.run(
                            ['systemctl', 'is-active', svc],
                            capture_output=True, text=True, timeout=5
                        )
                        status = result.stdout.strip()

                        # Check boot persistence
                        boot_info = ""
                        try:
                            enabled_result = subprocess.run(
                                ['systemctl', 'is-enabled', svc],
                                capture_output=True, text=True, timeout=5
                            )
                            is_enabled = enabled_result.returncode == 0
                            if status == 'active' and not is_enabled:
                                boot_info = "  (not enabled at boot)"
                                warnings.append(svc)
                        except Exception:
                            pass

                        if status == 'active':
                            print(f"  \033[0;32m●\033[0m {svc:<18} running{boot_info}")
                        elif status == 'failed':
                            print(f"  \033[0;31m●\033[0m {svc:<18} FAILED")
                        else:
                            print(f"  \033[2m○\033[0m {svc:<18} {status}")
                    except Exception:
                        print(f"  ? {svc:<18} unknown")
                print()

                # Surface actionable warning
                if warnings:
                    print(f"  \033[0;33mWarning:\033[0m {', '.join(warnings)} won't start on reboot.")
                    print(f"  Fix: sudo systemctl enable {' '.join(warnings)}\n")

                # Show failed service logs (only for systemd services)
                for svc in ['meshtasticd']:
                    try:
                        r = subprocess.run(['systemctl', 'is-active', svc],
                                           capture_output=True, text=True, timeout=5)
                        if r.stdout.strip() == 'failed':
                            print(f"\033[0;31m{svc} failure:\033[0m")
                            subprocess.run(
                                ['journalctl', '-u', svc, '-n', '5', '--no-pager'],
                                timeout=10
                            )
                            print()
                    except Exception:
                        pass

                # Show rnsd failure logs if systemd-managed and failed
                if not use_direct_rnsd:
                    try:
                        r = subprocess.run(['systemctl', 'is-active', 'rnsd'],
                                           capture_output=True, text=True, timeout=5)
                        if r.stdout.strip() == 'failed':
                            print(f"\033[0;31mrnsd failure:\033[0m")
                            subprocess.run(
                                ['journalctl', '-u', 'rnsd', '-n', '5', '--no-pager'],
                                timeout=10
                            )
                            print()
                    except Exception:
                        pass
                self._wait_for_enter()
            elif choice == "restart-mesh":
                subprocess.run(['clear'], check=False, timeout=5)
                print("Restarting meshtasticd...\n")
                subprocess.run(['systemctl', 'restart', 'meshtasticd'], timeout=30)
                subprocess.run(['systemctl', 'status', 'meshtasticd', '--no-pager', '-l'], timeout=10)
                self._wait_for_enter()
            elif choice == "start-rns":
                subprocess.run(['clear'], check=False, timeout=5)
                print("Starting rnsd...\n")
                # Use direct process control if no systemd unit
                if not self._has_systemd_unit('rnsd'):
                    self._start_rnsd_direct()
                else:
                    subprocess.run(['systemctl', 'start', 'rnsd'], timeout=30)
                    subprocess.run(['systemctl', 'status', 'rnsd', '--no-pager', '-l'], timeout=10)
                self._wait_for_enter()
            elif choice == "restart-rns":
                subprocess.run(['clear'], check=False, timeout=5)
                print("Restarting rnsd...\n")
                # Use direct process control if no systemd unit
                if not self._has_systemd_unit('rnsd'):
                    self._stop_rnsd_direct()
                    import time
                    time.sleep(0.5)
                    self._start_rnsd_direct()
                else:
                    subprocess.run(['systemctl', 'restart', 'rnsd'], timeout=30)
                    subprocess.run(['systemctl', 'status', 'rnsd', '--no-pager', '-l'], timeout=10)
                self._wait_for_enter()
            elif choice == "install":
                self._install_native_meshtasticd()
            else:
                self._manage_service(choice)

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
  MaxMessageQueue: 100
  ConfigDirectory: /etc/meshtasticd/config.d/
  AvailableDirectory: /etc/meshtasticd/available.d/
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

                # Download and install GPG key (no shell=True / bash -c)
                key_result = subprocess.run(
                    ['curl', '-fsSL', f'{repo_url}Release.key'],
                    capture_output=True, timeout=30, check=False
                )
                if key_result.returncode == 0:
                    subprocess.run(
                        ['gpg', '--dearmor', '-o', '/etc/apt/trusted.gpg.d/meshtastic.gpg'],
                        input=key_result.stdout, timeout=30, check=False
                    )

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
  MaxMessageQueue: 100
  ConfigDirectory: /etc/meshtasticd/config.d/
  AvailableDirectory: /etc/meshtasticd/available.d/
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

    def _has_systemd_unit(self, service_name: str) -> bool:
        """Check if a service has a systemd unit file."""
        try:
            result = subprocess.run(
                ['systemctl', 'cat', service_name],
                capture_output=True,
                text=True,
                timeout=5
            )
            return result.returncode == 0
        except Exception:
            return False

    def _is_rnsd_running(self) -> bool:
        """Check if rnsd is running as a process."""
        try:
            result = subprocess.run(
                ['pgrep', '-x', 'rnsd'],
                capture_output=True,
                timeout=5
            )
            return result.returncode == 0
        except Exception:
            return False

    def _start_rnsd_direct(self) -> bool:
        """Start rnsd directly as a background process.

        Returns True if started successfully.
        """
        # Check if already running
        if self._is_rnsd_running():
            print("rnsd is already running.")
            return True

        # Check if rnsd binary exists
        rnsd_path = shutil.which('rnsd')
        if not rnsd_path:
            print("\033[0;31mError:\033[0m rnsd not found in PATH.")
            print("Install Reticulum: pip install rns")
            return False

        try:
            # Start rnsd as a background daemon
            # rnsd itself daemonizes when run without -f flag
            print("Starting rnsd daemon...")
            result = subprocess.run(
                ['rnsd'],
                capture_output=True,
                text=True,
                timeout=10
            )
            # rnsd daemonizes and returns quickly
            # Check if it actually started
            import time
            time.sleep(0.5)
            if self._is_rnsd_running():
                print("\033[0;32m✓\033[0m rnsd started successfully.")
                return True
            else:
                print(f"\033[0;31mError:\033[0m rnsd failed to start.")
                if result.stderr:
                    print(result.stderr)
                return False
        except subprocess.TimeoutExpired:
            # If it times out, check if running anyway (daemon fork)
            if self._is_rnsd_running():
                print("\033[0;32m✓\033[0m rnsd started successfully.")
                return True
            print("\033[0;31mError:\033[0m rnsd start timed out.")
            return False
        except Exception as e:
            print(f"\033[0;31mError:\033[0m Failed to start rnsd: {e}")
            return False

    def _stop_rnsd_direct(self) -> bool:
        """Stop rnsd process directly.

        Returns True if stopped successfully.
        """
        if not self._is_rnsd_running():
            print("rnsd is not running.")
            return True

        try:
            print("Stopping rnsd...")
            # Use pkill to stop rnsd gracefully
            result = subprocess.run(
                ['pkill', '-TERM', '-x', 'rnsd'],
                capture_output=True,
                timeout=10
            )
            import time
            time.sleep(0.5)
            if not self._is_rnsd_running():
                print("\033[0;32m✓\033[0m rnsd stopped.")
                return True
            # If still running, try SIGKILL
            subprocess.run(['pkill', '-KILL', '-x', 'rnsd'], timeout=5)
            time.sleep(0.3)
            if not self._is_rnsd_running():
                print("\033[0;32m✓\033[0m rnsd stopped (forced).")
                return True
            print("\033[0;31mError:\033[0m Could not stop rnsd.")
            return False
        except Exception as e:
            print(f"\033[0;31mError:\033[0m Failed to stop rnsd: {e}")
            return False

    def _service_action(self, service_name: str, action: str):
        """Perform service action using systemctl or direct process control.

        For rnsd: Uses direct process control if no systemd unit exists.
        For other services: Uses systemctl.
        """
        subprocess.run(['clear'], check=False, timeout=5)

        # Check if rnsd needs direct process handling
        use_direct_rnsd = (service_name == 'rnsd' and
                          not self._has_systemd_unit('rnsd'))

        if action == "status":
            print(f"=== {service_name} status ===\n")
            if use_direct_rnsd:
                # Show process status for rnsd
                if self._is_rnsd_running():
                    print(f"\033[0;32m●\033[0m rnsd is \033[0;32mrunning\033[0m")
                    # Show process info
                    try:
                        subprocess.run(
                            ['pgrep', '-a', '-x', 'rnsd'],
                            timeout=5
                        )
                    except Exception as e:
                        logger.debug(f"pgrep for rnsd failed: {e}")
                else:
                    print(f"\033[0;31m○\033[0m rnsd is \033[0;31mnot running\033[0m")
                    print("\nTo start: Select 'Start Service' from the menu")
            else:
                subprocess.run(
                    ['systemctl', 'status', service_name, '--no-pager', '-l'],
                    timeout=10
                )
            self._wait_for_enter()

        elif action == "start":
            print(f"Starting {service_name}...\n")
            if use_direct_rnsd:
                self._start_rnsd_direct()
            else:
                subprocess.run(['systemctl', 'start', service_name], timeout=30)
                subprocess.run(
                    ['systemctl', 'status', service_name, '--no-pager', '-l'],
                    timeout=10
                )
            self._wait_for_enter()

        elif action == "stop":
            if self.dialog.yesno("Confirm", f"Stop {service_name}?", default_no=True):
                subprocess.run(['clear'], check=False, timeout=5)
                print(f"Stopping {service_name}...\n")
                if use_direct_rnsd:
                    self._stop_rnsd_direct()
                else:
                    subprocess.run(['systemctl', 'stop', service_name], timeout=30)
                    print(f"{service_name} stopped.")
                self._wait_for_enter()

        elif action == "restart":
            print(f"Restarting {service_name}...\n")
            if use_direct_rnsd:
                self._stop_rnsd_direct()
                import time
                time.sleep(0.5)
                self._start_rnsd_direct()
            else:
                subprocess.run(['systemctl', 'restart', service_name], timeout=30)
                subprocess.run(
                    ['systemctl', 'status', service_name, '--no-pager', '-l'],
                    timeout=10
                )
            self._wait_for_enter()

        elif action == "logs":
            print(f"=== {service_name} logs (last 30) ===\n")
            if use_direct_rnsd:
                # rnsd logs go to ~/.reticulum/logfile by default
                try:
                    log_path = get_real_user_home() / '.reticulum' / 'logfile'
                    if log_path.exists():
                        print(f"Log file: {log_path}\n")
                        subprocess.run(
                            ['tail', '-n', '30', str(log_path)],
                            timeout=10
                        )
                    else:
                        print("No log file found at ~/.reticulum/logfile")
                        print("rnsd may log to stdout or syslog depending on config.")
                except Exception as e:
                    print(f"Could not read logs: {e}")
            else:
                subprocess.run(
                    ['journalctl', '-u', service_name, '-n', '30', '--no-pager'],
                    timeout=15
                )
            self._wait_for_enter()

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
        """Run hardware detection - terminal-native."""
        subprocess.run(['clear'], check=False, timeout=5)
        print("=== Hardware Detection ===\n")

        # SPI
        spi_devices = list(Path('/dev').glob('spidev*'))
        if spi_devices:
            print(f"  \033[0;32m●\033[0m SPI: {', '.join(d.name for d in spi_devices)}")
        else:
            print(f"  \033[2m○\033[0m SPI: not enabled")

        # I2C
        i2c_devices = list(Path('/dev').glob('i2c-*'))
        if i2c_devices:
            print(f"  \033[0;32m●\033[0m I2C: {', '.join(d.name for d in i2c_devices)}")
        else:
            print(f"  \033[2m○\033[0m I2C: not enabled")

        # Serial/USB
        serial_ports = list(Path('/dev').glob('ttyUSB*')) + list(Path('/dev').glob('ttyACM*'))
        if serial_ports:
            print(f"  \033[0;32m●\033[0m Serial: {', '.join(d.name for d in serial_ports)}")
        else:
            print(f"  \033[2m○\033[0m Serial: no USB serial devices")

        # GPIO
        gpio_available = Path('/sys/class/gpio').exists()
        print(f"  {'●' if gpio_available else '○'} GPIO: {'available' if gpio_available else 'not available'}")

        # USB devices
        print("\nUSB Devices:")
        subprocess.run(['lsusb'], timeout=10)

        # meshtasticd config.d/
        print("\nmeshtasticd config.d/:")
        config_d = Path('/etc/meshtasticd/config.d')
        if config_d.exists():
            configs = list(config_d.glob('*.yaml'))
            if configs:
                for c in configs:
                    print(f"  {c.name}")
            else:
                print("  (empty)")
        else:
            print("  (not found)")

        self._wait_for_enter()

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
            elif choice == "hamclock":
                self._configure_hamclock()

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

    def _configure_hamclock(self):
        """Configure HamClock settings - test API connection."""
        host = self.dialog.inputbox(
            "HamClock Host",
            "Enter HamClock hostname or IP:",
            "localhost"
        )

        if host:
            if not self._validate_hostname(host):
                self.dialog.msgbox("Error", "Invalid hostname or IP address.")
                return

            port = self.dialog.inputbox(
                "HamClock API Port",
                "Enter API port (default 8082):",
                "8082"
            )

            if port:
                if not self._validate_port(port):
                    self.dialog.msgbox("Error", "Invalid port number (1-65535).")
                    return

                try:
                    import urllib.request
                    url = f"http://{host}:{port}/get_de.txt"
                    req = urllib.request.urlopen(url, timeout=5)
                    data = req.read().decode()
                    self.dialog.msgbox("HamClock Connected", f"API: {host}:{port}\n\nDE Station:\n{data}")
                except Exception as e:
                    self.dialog.msgbox("Error", f"Cannot reach HamClock at {host}:{port}\n\n{e}\n\nMake sure HamClock is running.")

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
    try:
        launcher = MeshForgeLauncher()
        launcher.run()
    except KeyboardInterrupt:
        print("\n\nExiting MeshForge...")
        sys.exit(0)


if __name__ == '__main__':
    main()
