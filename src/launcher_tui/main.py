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
    from utils.paths import get_real_user_home
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
from quick_actions_mixin import QuickActionsMixin
from emergency_mode_mixin import EmergencyModeMixin


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
    EmergencyModeMixin
):
    """MeshForge launcher with raspi-config style interface."""

    def __init__(self):
        self.dialog = DialogBackend()
        self.src_dir = Path(__file__).parent.parent  # src/ directory
        self.env = self._detect_environment()
        self._setup_status_bar()
        self._meshtastic_path = None  # Cached CLI path

    def _get_meshtastic_cli(self) -> str:
        """Find the meshtastic CLI binary path, with caching."""
        if self._meshtastic_path is None:
            self._meshtastic_path = shutil.which('meshtastic') or 'meshtastic'
        return self._meshtastic_path

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

        # Check for first run and offer setup wizard
        if self._check_first_run():
            self._run_first_run_wizard()

        # Check for service misconfiguration (SPI HAT with USB config)
        self._check_service_misconfig()

        # Auto-start map server if configured
        self._maybe_auto_start_map()

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
        """Display the main NOC menu."""
        while True:
            choices = [
                # Monitor
                ("status", "Status Overview"),
                ("quick", "Quick Actions (shortcuts)"),
                ("logs", "Logs (live follow, errors, analysis)"),
                ("network", "Network & Ports"),
                # Operate
                ("radio", "Radio (meshtastic CLI)"),
                ("services", "Services (start/stop/restart)"),
                ("emcomm", "EMERGENCY MODE (field ops)"),
                # Mesh Networks
                ("rns", "RNS / Reticulum"),
                ("aredn", "AREDN Mesh"),
                # Tools & Config
                ("rf", "RF Tools & Calculator"),
                ("config", "Configuration"),
                ("hardware", "Hardware Detection"),
                ("system", "System Tools (full Linux CLI)"),
                # Meta
                ("web", "Web Client URL"),
                ("about", "About"),
                ("quit", "Exit"),
            ]

            choice = self.dialog.menu(
                f"MeshForge NOC v{__version__}",
                "Network Operations Center:",
                choices
            )

            if choice is None or choice == "quit":
                break

            self._handle_choice(choice)

    def _handle_choice(self, choice: str):
        """Handle menu selection."""
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
        elif choice == "rf":
            self._rf_tools_menu()
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
            # Check if CLI is available and show install option if not
            has_cli = shutil.which('meshtastic') is not None

            choices = []
            if not has_cli:
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
                ("back", "Back"),
            ])

            status = "" if has_cli else "\n[CLI not installed]\n"
            choice = self.dialog.menu(
                "Radio Tools",
                f"Meshtastic radio control (terminal-native):{status}",
                choices
            )

            if choice is None or choice == "back":
                break

            if choice == "install-cli":
                self._install_meshtastic_cli()
                continue

            cli = self._get_meshtastic_cli()
            if choice == "info":
                self._radio_run([cli, '--info'], "Radio Info")
            elif choice == "nodes":
                self._radio_run([cli, '--nodes'], "Node List")
            elif choice == "channels":
                self._radio_run([cli, '--ch-index', '0', '--ch-getall'], "Channels")
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
            input("\nPress Enter to continue...")
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

        try:
            # Ensure pipx is available
            if not shutil.which('pipx'):
                print("Installing pipx...\n")
                result = subprocess.run(
                    ['apt-get', 'install', '-y', 'pipx'],
                    timeout=60
                )
                if result.returncode != 0:
                    print("\nFailed to install pipx.")
                    print("Try manually: sudo apt install pipx")
                    input("\nPress Enter to continue...")
                    return

            # Ensure pipx bin dir is in PATH for this session
            print("Ensuring pipx paths...\n")
            subprocess.run(['pipx', 'ensurepath'], timeout=15)

            # Add common pipx bin dirs to current process PATH
            for bindir in [
                Path.home() / '.local' / 'bin',
                Path('/root/.local/bin'),
                Path('/usr/local/bin'),
            ]:
                if bindir.is_dir() and str(bindir) not in os.environ.get('PATH', ''):
                    os.environ['PATH'] = f"{bindir}:{os.environ.get('PATH', '')}"

            # Install meshtastic with CLI extras (live output)
            print("\nInstalling meshtastic CLI via pipx...\n")
            result = subprocess.run(
                ['pipx', 'install', 'meshtastic[cli]', '--force'],
                timeout=300
            )

            if result.returncode != 0:
                print("\nRetrying without [cli] extras...\n")
                result = subprocess.run(
                    ['pipx', 'install', 'meshtastic', '--force'],
                    timeout=300
                )

            if result.returncode == 0:
                # Clear cached path so it gets re-resolved
                self._meshtastic_path = None
                cli_path = shutil.which('meshtastic')
                if cli_path:
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
            input("\nPress Enter to continue...")
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

        cmd = [self._get_meshtastic_cli(), '--sendtext', msg]
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
                [self._get_meshtastic_cli(), '--set', 'lora.region', choice],
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

        cmd = [self._get_meshtastic_cli(), '--set-owner', name]
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

        if choice == "view":
            self._radio_run([cli, '--get', 'position'], "Position Settings")
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
                [cli, '--setlat', str(lat_f), '--setlon', str(lon_f)],
                "Setting Position"
            )

    def _radio_reboot(self):
        """Reboot the radio via meshtastic CLI."""
        if self.dialog.yesno("Reboot Radio", "Reboot the Meshtastic radio?\n\nThis restarts the firmware.", default_no=True):
            self._radio_run(
                [self._get_meshtastic_cli(), '--reboot'],
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
                input("\nPress Enter to continue...")
            elif choice == "mesh-50":
                print("=== meshtasticd (last 50 lines) ===\n")
                subprocess.run(
                    ['journalctl', '-u', 'meshtasticd', '-n', '50', '--no-pager'],
                    timeout=15
                )
                input("\nPress Enter to continue...")
            elif choice == "rns-50":
                print("=== rnsd (last 50 lines) ===\n")
                subprocess.run(
                    ['journalctl', '-u', 'rnsd', '-n', '50', '--no-pager'],
                    timeout=15
                )
                input("\nPress Enter to continue...")
            elif choice == "boot":
                print("=== Boot messages (this boot) ===\n")
                subprocess.run(
                    ['journalctl', '-b', '-n', '100', '--no-pager'],
                    timeout=15
                )
                input("\nPress Enter to continue...")
            elif choice == "kernel":
                print("=== Kernel messages (dmesg) ===\n")
                subprocess.run(['dmesg', '--time-format=reltime'], timeout=10)
                input("\nPress Enter to continue...")
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
                input("\nPress Enter to continue...")
            elif choice == "ifaces":
                subprocess.run(['clear'], check=False, timeout=5)
                print("=== Network Interfaces ===\n")
                subprocess.run(['ip', '-c', 'addr'], timeout=10)
                input("\nPress Enter to continue...")
            elif choice == "conns":
                subprocess.run(['clear'], check=False, timeout=5)
                print("=== Active Connections ===\n")
                subprocess.run(['ss', '-tunp'], timeout=10)
                input("\nPress Enter to continue...")
            elif choice == "routes":
                subprocess.run(['clear'], check=False, timeout=5)
                print("=== Routing Table ===\n")
                subprocess.run(['ip', 'route'], timeout=10)
                input("\nPress Enter to continue...")
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
                ("bridge", "Gateway Bridge (start/stop)"),
                ("config", "View Reticulum Config"),
                ("edit", "Edit Reticulum Config"),
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
                rns_config = str(get_real_user_home() / ".reticulum")
                self._run_rns_tool(
                    ['rnstatus', '--config', rns_config], 'rnstatus'
                )
                input("\nPress Enter to continue...")
            elif choice == "paths":
                subprocess.run(['clear'], check=False, timeout=5)
                print("=== RNS Path Table ===\n")
                rns_config = str(get_real_user_home() / ".reticulum")
                self._run_rns_tool(
                    ['rnpath', '-t', '--config', rns_config], 'rnpath'
                )
                input("\nPress Enter to continue...")
            elif choice == "bridge":
                self._run_bridge()
            elif choice == "config":
                self._view_rns_config()
            elif choice == "edit":
                self._edit_rns_config()

    def _view_rns_config(self):
        """View current Reticulum config."""
        subprocess.run(['clear'], check=False, timeout=5)
        print("=== Reticulum Configuration ===\n")

        # Try common config locations
        config_paths = [
            get_real_user_home() / '.reticulum' / 'config',
            Path('/root/.reticulum/config'),
            Path('/etc/reticulum/config'),
        ]

        found = False
        for cfg in config_paths:
            if cfg.exists():
                print(f"Config: {cfg}\n")
                try:
                    content = cfg.read_text()
                    print(content)
                    found = True
                    break
                except PermissionError:
                    print(f"Permission denied reading {cfg}")
                    print(f"Try: sudo cat {cfg}")

        if not found:
            print("No Reticulum config found.")
            print("\nExpected locations:")
            for p in config_paths:
                print(f"  {p}")
            print("\nInstall RNS: pip3 install rns")
            print("Template:    templates/reticulum.conf")

        input("\nPress Enter to continue...")

    def _edit_rns_config(self):
        """Edit Reticulum config with available editor. Deploys template if no config exists."""
        user_home = get_real_user_home()
        config_paths = [
            user_home / '.reticulum' / 'config',
            Path('/root/.reticulum/config'),
        ]

        config_path = None
        for cfg in config_paths:
            if cfg.exists():
                config_path = str(cfg)
                break

        if not config_path:
            # Offer to deploy from template
            template = Path(__file__).parent.parent.parent / 'templates' / 'reticulum.conf'
            target = user_home / '.reticulum' / 'config'

            if template.exists():
                if self.dialog.yesno(
                    "Deploy Reticulum Config",
                    f"No Reticulum config found.\n\n"
                    f"Deploy template to:\n  {target}\n\n"
                    f"This sets up RNS with Meshtastic bridge on port 4403.\n"
                    f"You can edit it after deployment."
                ):
                    try:
                        target.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(str(template), str(target))
                        config_path = str(target)
                        print(f"\nDeployed: {target}")
                    except (OSError, PermissionError) as e:
                        self.dialog.msgbox("Error", f"Failed to deploy config:\n{e}")
                        return
                else:  # User said No
                    return
            else:
                self.dialog.msgbox(
                    "No Config",
                    "No Reticulum config found and template missing.\n\n"
                    "Install RNS first: pip3 install rns\n"
                    "Then run rnsd once to generate default config."
                )
                return

        # Find editor
        editor = None
        for cmd in ['nano', 'vim', 'vi']:
            if shutil.which(cmd):
                editor = cmd
                break

        if not editor:
            self.dialog.msgbox("Error", "No text editor found (nano, vim, vi)")
            return

        subprocess.run([editor, config_path], timeout=None)  # Interactive editor

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
                # rnsd not running or not reachable
                if result.stdout:
                    print(result.stdout, end='')
                print("\nStart rnsd: sudo systemctl start rnsd")
            else:
                # Generic failure - show output and suggestions
                if result.stdout:
                    print(result.stdout, end='')
                print(f"\n{tool_name} failed. Possible causes:")
                print("  - rnsd not running: sudo systemctl start rnsd")
                print("  - RNS not installed: pip3 install rns")
                if result.stderr and result.stderr.strip():
                    # Show last 3 lines of stderr for context
                    err_lines = result.stderr.strip().split('\n')[-3:]
                    print("\nDetails:")
                    for line in err_lines:
                        print(f"  {line}")
        except FileNotFoundError:
            print(f"\n{tool_name} not found. Is RNS installed?")
            print("Install: pip3 install rns")
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
                input("\nPress Enter to continue...")
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

        input("\nPress Enter to continue...")

    def _aredn_neighbors(self):
        """Show AREDN neighbor links."""
        subprocess.run(['clear'], check=False, timeout=5)
        print("=== AREDN Neighbors ===\n")

        try:
            from utils.aredn import AREDNClient

            node_ip = self._aredn_get_node_ip()
            if not node_ip:
                print("No AREDN node found. Is it connected?")
                input("\nPress Enter to continue...")
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

        input("\nPress Enter to continue...")

    def _aredn_services(self):
        """Show AREDN advertised services."""
        subprocess.run(['clear'], check=False, timeout=5)
        print("=== AREDN Services ===\n")

        try:
            from utils.aredn import AREDNClient

            node_ip = self._aredn_get_node_ip()
            if not node_ip:
                print("No AREDN node found.")
                input("\nPress Enter to continue...")
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

        input("\nPress Enter to continue...")

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

        input("\nPress Enter to continue...")

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

        input("\nPress Enter to continue...")

    def _view_config_overlays(self):
        """Show config.d/ overlay files."""
        subprocess.run(['clear'], check=False, timeout=5)
        print("=== config.d/ Overlays ===\n")

        config_d = Path('/etc/meshtasticd/config.d')
        if not config_d.exists():
            print("config.d/ directory not found.")
            print("Create it: sudo mkdir -p /etc/meshtasticd/config.d")
            input("\nPress Enter to continue...")
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

        input("\nPress Enter to continue...")

    def _view_available_hats(self):
        """Show available HAT configurations from meshtasticd package."""
        subprocess.run(['clear'], check=False, timeout=5)
        print("=== Available HAT Configs ===\n")

        available_d = Path('/etc/meshtasticd/available.d')
        if not available_d.exists():
            print("available.d/ not found.")
            print("meshtasticd package should provide this.")
            print("\nInstall: sudo apt install meshtasticd")
            input("\nPress Enter to continue...")
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

        input("\nPress Enter to continue...")

    def _open_web_client(self):
        """Show/open meshtasticd web client for full radio configuration."""
        import socket
        local_ip = "localhost"
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
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
            # Capture output and pipe through less so user can scroll
            result = subprocess.run(
                [sys.executable, str(self.src_dir / 'cli' / 'status.py')],
                capture_output=True, text=True, timeout=30
            )
            output = result.stdout or ""
            if result.stderr:
                output += result.stderr

            if output.strip():
                # Use less with -R for ANSI colors, -X to not clear on exit
                proc = subprocess.Popen(
                    ['less', '-R', '-X'],
                    stdin=subprocess.PIPE
                )
                try:
                    proc.communicate(input=output.encode(), timeout=300)
                except subprocess.TimeoutExpired:
                    proc.kill()
                except KeyboardInterrupt:
                    proc.kill()
            else:
                print("No status output available.")
                try:
                    input("\nPress Enter to continue...")
                except KeyboardInterrupt:
                    print()
        except subprocess.TimeoutExpired:
            print("\n\nStatus check timed out (30s).")
            try:
                input("\nPress Enter to return to menu...")
            except KeyboardInterrupt:
                print()
        except KeyboardInterrupt:
            print("\n\nAborted.")

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
            input("\nPress Enter to return to menu...")
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
            input("\nPress Enter to continue...")
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
        except Exception:
            pass

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
            log_path = Path('/tmp/meshforge-gateway.log')
            log_file = open(log_path, 'w')
            subprocess.Popen(
                [sys.executable, str(self.src_dir / 'gateway' / 'bridge_cli.py')],
                stdout=log_file,
                stderr=subprocess.STDOUT,
                start_new_session=True
            )

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
            input("\nPress Enter to continue...")
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

    def _show_bridge_status(self):
        """Show gateway bridge log tail."""
        log_path = Path('/tmp/meshforge-gateway.log')
        if not log_path.exists():
            self.dialog.msgbox("No Logs", "No gateway log found.")
            return

        try:
            lines = log_path.read_text().strip().split('\n')
            # Show last 30 lines
            tail = '\n'.join(lines[-30:])
            self.dialog.msgbox("Bridge Status (last 30 lines)", tail)
        except Exception as e:
            self.dialog.msgbox("Error", f"Failed to read log:\n{e}")

    def _show_bridge_logs(self):
        """Show full gateway bridge logs in less."""
        log_path = Path('/tmp/meshforge-gateway.log')
        if not log_path.exists():
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
                for svc in ['meshtasticd', 'rnsd', 'meshforge']:
                    try:
                        result = subprocess.run(
                            ['systemctl', 'is-active', svc],
                            capture_output=True, text=True, timeout=5
                        )
                        status = result.stdout.strip()
                        if status == 'active':
                            print(f"  \033[0;32m●\033[0m {svc:<18} running")
                        elif status == 'failed':
                            print(f"  \033[0;31m●\033[0m {svc:<18} FAILED")
                        else:
                            print(f"  \033[2m○\033[0m {svc:<18} {status}")
                    except Exception:
                        print(f"  ? {svc:<18} unknown")
                print()
                # Show failed service logs
                for svc in ['meshtasticd', 'rnsd']:
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
                input("\nPress Enter to continue...")
            elif choice == "restart-mesh":
                subprocess.run(['clear'], check=False, timeout=5)
                print("Restarting meshtasticd...\n")
                subprocess.run(['systemctl', 'restart', 'meshtasticd'], timeout=30)
                subprocess.run(['systemctl', 'status', 'meshtasticd', '--no-pager', '-l'], timeout=10)
                input("\nPress Enter to continue...")
            elif choice == "restart-rns":
                subprocess.run(['clear'], check=False, timeout=5)
                print("Restarting rnsd...\n")
                subprocess.run(['systemctl', 'restart', 'rnsd'], timeout=30)
                subprocess.run(['systemctl', 'status', 'rnsd', '--no-pager', '-l'], timeout=10)
                input("\nPress Enter to continue...")
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

    def _service_action(self, service_name: str, action: str):
        """Perform service action using direct systemctl."""
        subprocess.run(['clear'], check=False, timeout=5)

        if action == "status":
            print(f"=== {service_name} status ===\n")
            subprocess.run(
                ['systemctl', 'status', service_name, '--no-pager', '-l'],
                timeout=10
            )
            input("\nPress Enter to continue...")

        elif action == "start":
            print(f"Starting {service_name}...\n")
            subprocess.run(['systemctl', 'start', service_name], timeout=30)
            subprocess.run(
                ['systemctl', 'status', service_name, '--no-pager', '-l'],
                timeout=10
            )
            input("\nPress Enter to continue...")

        elif action == "stop":
            if self.dialog.yesno("Confirm", f"Stop {service_name}?", default_no=True):
                subprocess.run(['clear'], check=False, timeout=5)
                print(f"Stopping {service_name}...\n")
                subprocess.run(['systemctl', 'stop', service_name], timeout=30)
                print(f"{service_name} stopped.")
                input("\nPress Enter to continue...")

        elif action == "restart":
            print(f"Restarting {service_name}...\n")
            subprocess.run(['systemctl', 'restart', service_name], timeout=30)
            subprocess.run(
                ['systemctl', 'status', service_name, '--no-pager', '-l'],
                timeout=10
            )
            input("\nPress Enter to continue...")

        elif action == "logs":
            print(f"=== {service_name} logs (last 30) ===\n")
            subprocess.run(
                ['journalctl', '-u', service_name, '-n', '30', '--no-pager'],
                timeout=15
            )
            input("\nPress Enter to continue...")

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

        input("\nPress Enter to continue...")

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
            port = self.dialog.inputbox(
                "HamClock API Port",
                "Enter API port (default 8082):",
                "8082"
            )

            if port:
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
    launcher = MeshForgeLauncher()
    launcher.run()


if __name__ == '__main__':
    main()
