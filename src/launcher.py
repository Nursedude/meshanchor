#!/usr/bin/env python3
"""
MeshForge Launcher

Detects environment and launches the appropriate interface:
  - GTK4 Desktop (if display available)
  - Launcher TUI (raspi-config style, works everywhere)

User preferences are saved for future launches.
"""

import os
import sys
import subprocess
import json
from pathlib import Path

# Import version
try:
    from __version__ import __version__
except ImportError:
    __version__ = "0.4.7-beta"

# Import centralized path utility for sudo compatibility
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

# Import NOC orchestrator for service management
try:
    from core.orchestrator import ServiceOrchestrator, ServiceState
    HAS_ORCHESTRATOR = True
except ImportError:
    HAS_ORCHESTRATOR = False

# Import startup health check
try:
    from utils.startup_health import run_health_check, print_health_summary
    HAS_HEALTH_CHECK = True
except ImportError:
    HAS_HEALTH_CHECK = False
    run_health_check = None
    print_health_summary = None

# Config file location
CONFIG_DIR = get_real_user_home() / '.config' / 'meshforge'
CONFIG_FILE = CONFIG_DIR / 'preferences.json'


# Colors for terminal output
class Colors:
    CYAN = '\033[0;36m'
    GREEN = '\033[0;32m'
    YELLOW = '\033[1;33m'
    RED = '\033[0;31m'
    BOLD = '\033[1m'
    DIM = '\033[2m'
    NC = '\033[0m'  # No Color


def load_preferences():
    """Load saved user preferences"""
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, 'r') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {}


def save_preferences(prefs):
    """Save user preferences"""
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        with open(CONFIG_FILE, 'w') as f:
            json.dump(prefs, f, indent=2)
    except IOError:
        pass


def check_first_run() -> bool:
    """Check if this is a first run (no setup marker exists)"""
    marker = get_real_user_home() / ".meshforge" / ".setup_complete"
    return not marker.exists()


def run_setup_wizard():
    """Run the interactive setup wizard"""
    print(f"\n{Colors.CYAN}{'='*60}")
    print("  MeshForge First-Run Setup")
    print(f"{'='*60}{Colors.NC}\n")

    print("This appears to be your first time running MeshForge.")
    print("The setup wizard will detect installed services and guide")
    print("you through initial configuration.\n")

    try:
        response = input(f"Run setup wizard now? [Y/n]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        response = 'n'

    if response != 'n':
        try:
            from setup_wizard import SetupWizard
            wizard = SetupWizard(interactive=True)
            wizard.run_interactive_setup()
            wizard.mark_setup_complete()
        except ImportError:
            try:
                import importlib.util
                spec = importlib.util.spec_from_file_location(
                    "setup_wizard",
                    Path(__file__).parent / "setup_wizard.py"
                )
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                wizard = module.SetupWizard(interactive=True)
                wizard.run_interactive_setup()
                wizard.mark_setup_complete()
            except Exception as e:
                print(f"{Colors.YELLOW}Setup wizard not available: {e}{Colors.NC}")
                print("Continuing to main launcher...\n")
                marker = get_real_user_home() / ".meshforge" / ".setup_complete"
                marker.parent.mkdir(parents=True, exist_ok=True)
                marker.write_text("skipped")
    else:
        print(f"\n{Colors.DIM}Skipping setup. Run 'meshforge --setup' anytime.{Colors.NC}\n")
        marker = get_real_user_home() / ".meshforge" / ".setup_complete"
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("skipped")


def print_banner():
    """Print the welcome banner"""
    print(f"""{Colors.CYAN}
    MeshForge NOC v{__version__}
    Network Operations Center for Mesh Networks
{Colors.NC}""")


def show_startup_health():
    """Show startup health summary."""
    if not HAS_HEALTH_CHECK:
        return

    print(f"{Colors.CYAN}{'─' * 50}{Colors.NC}")
    print()

    try:
        health = run_health_check()
        summary = print_health_summary(health, use_color=True)
        print(summary)
    except Exception as e:
        print(f"{Colors.YELLOW}Health check skipped: {e}{Colors.NC}")

    print()
    print(f"{Colors.CYAN}{'─' * 50}{Colors.NC}")
    print()


def detect_environment():
    """Detect the current environment and capabilities"""
    env = {
        'has_display': False,
        'display_type': None,
        'is_ssh': False,
        'has_gtk': False,
        'is_root': os.geteuid() == 0,
        'terminal': os.environ.get('TERM', 'unknown'),
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


def print_environment_info(env):
    """Print detected environment information"""
    print(f"{Colors.DIM}Environment:{Colors.NC}")

    if env['has_display']:
        print(f"  {Colors.GREEN}+{Colors.NC} Display: {env['display_type']}")
    else:
        print(f"  {Colors.YELLOW}○{Colors.NC} No display")

    if env['is_ssh']:
        print(f"  {Colors.YELLOW}○{Colors.NC} SSH session")

    if env['has_gtk']:
        print(f"  {Colors.GREEN}+{Colors.NC} GTK4/libadwaita")
    else:
        print(f"  {Colors.DIM}○{Colors.NC} GTK4 not available")

    print()


def get_recommendation(env):
    """Get the recommended interface based on environment"""
    if env['has_display'] and env['has_gtk'] and not env['is_ssh']:
        return '1'  # GTK4 GUI
    return '2'  # TUI (raspi-config style)


def print_menu(env, recommended):
    """Print the interface selection menu"""
    print(f"{Colors.BOLD}=== INTERFACES ============================================{Colors.NC}\n")

    # Option 1: GTK4 GUI
    gtk_status = ""
    if not env['has_display']:
        gtk_status = f" {Colors.DIM}(no display){Colors.NC}"
    elif not env['has_gtk']:
        gtk_status = f" {Colors.YELLOW}(not installed){Colors.NC}"
    elif env['is_ssh']:
        gtk_status = f" {Colors.YELLOW}(may not work over SSH){Colors.NC}"

    rec1 = f" {Colors.GREEN}<- Recommended{Colors.NC}" if recommended == '1' else ""
    print(f"  {Colors.BOLD}1{Colors.NC}. {Colors.CYAN}GTK4 Desktop{Colors.NC}{gtk_status}{rec1}")
    print(f"     {Colors.DIM}Full graphical interface with maps, charts, panels{Colors.NC}")
    print()

    # Option 2: TUI (raspi-config style)
    rec2 = f" {Colors.GREEN}<- Recommended{Colors.NC}" if recommended == '2' else ""
    print(f"  {Colors.BOLD}2{Colors.NC}. {Colors.GREEN}Terminal UI{Colors.NC} (raspi-config style){rec2}")
    print(f"     {Colors.DIM}Works everywhere: SSH, serial, local. Full feature set.{Colors.NC}")
    print()

    # Quick tools
    print(f"{Colors.BOLD}=== QUICK TOOLS ==========================================={Colors.NC}\n")

    print(f"  {Colors.BOLD}3{Colors.NC}. {Colors.YELLOW}Run Diagnostics{Colors.NC}")
    print(f"     {Colors.DIM}Check system health, services, and connectivity{Colors.NC}")
    print()

    print(f"  {Colors.BOLD}4{Colors.NC}. {Colors.YELLOW}Start Gateway Bridge{Colors.NC}")
    print(f"     {Colors.DIM}RNS <-> Meshtastic bridge (headless mode){Colors.NC}")
    print()

    print(f"  {Colors.BOLD}5{Colors.NC}. {Colors.YELLOW}Monitor Mode{Colors.NC}")
    print(f"     {Colors.DIM}Real-time node and message monitoring{Colors.NC}")
    print()

    # Options
    print(f"{Colors.BOLD}=== OPTIONS ==============================================={Colors.NC}\n")

    if not env['has_gtk']:
        print(f"  {Colors.BOLD}i{Colors.NC}. Install GTK4 dependencies")
    print(f"  {Colors.BOLD}w{Colors.NC}. Run setup wizard")
    print(f"  {Colors.BOLD}q{Colors.NC}. Quit")
    print()


def install_gtk():
    """Install GTK4 dependencies"""
    print(f"\n{Colors.CYAN}Installing GTK4 dependencies...{Colors.NC}")
    print(f"{Colors.DIM}sudo apt install python3-gi python3-gi-cairo gir1.2-gtk-4.0 libadwaita-1-0 gir1.2-adw-1{Colors.NC}\n")

    try:
        subprocess.run([
            'sudo', 'apt', 'install', '-y',
            'python3-gi', 'python3-gi-cairo',
            'gir1.2-gtk-4.0', 'libadwaita-1-0', 'gir1.2-adw-1'
        ], check=True, timeout=300)
        print(f"{Colors.GREEN}GTK4 dependencies installed!{Colors.NC}")
    except subprocess.CalledProcessError as e:
        print(f"{Colors.RED}Failed to install GTK4 dependencies: {e}{Colors.NC}")
    except subprocess.TimeoutExpired:
        print(f"{Colors.RED}Installation timed out (5 min limit){Colors.NC}")

    input(f"\n{Colors.DIM}Press Enter to continue...{Colors.NC}")


def launch_interface(choice):
    """Launch the selected interface"""
    src_dir = Path(__file__).parent

    if choice == "1":
        # GTK4 GUI
        print(f"\n{Colors.GREEN}Launching GTK4 Desktop...{Colors.NC}\n")
        os.execv(sys.executable, [sys.executable, str(src_dir / 'main_gtk.py')])

    elif choice == "2":
        # Launcher TUI (raspi-config style)
        print(f"\n{Colors.GREEN}Launching Terminal UI...{Colors.NC}\n")
        os.execv(sys.executable, [sys.executable, str(src_dir / 'launcher_tui' / 'main.py')])

    elif choice == "3":
        # Diagnostics
        print(f"\n{Colors.GREEN}Running Diagnostics...{Colors.NC}\n")
        subprocess.run([sys.executable, str(src_dir / 'cli' / 'diagnose.py')], timeout=600)

    elif choice == "4":
        # Gateway Bridge
        print(f"\n{Colors.GREEN}Starting Gateway Bridge...{Colors.NC}")
        print(f"{Colors.DIM}Press Ctrl+C to stop{Colors.NC}\n")
        try:
            launch_gateway_bridge(src_dir)
        except KeyboardInterrupt:
            print(f"\n{Colors.YELLOW}Gateway stopped.{Colors.NC}")

    elif choice == "5":
        # Monitor Mode
        print(f"\n{Colors.GREEN}Starting Monitor Mode...{Colors.NC}\n")
        try:
            subprocess.run([sys.executable, str(src_dir / 'monitor.py')], timeout=3600)
        except KeyboardInterrupt:
            print(f"\n{Colors.YELLOW}Monitor stopped.{Colors.NC}")
        except subprocess.TimeoutExpired:
            print(f"\n{Colors.YELLOW}Monitor timed out after 1hr.{Colors.NC}")


def launch_gateway_bridge(src_dir):
    """Launch the gateway bridge in headless mode"""
    try:
        sys.path.insert(0, str(src_dir))
        from gateway.rns_bridge import RNSMeshtasticBridge
        from gateway.config import GatewayConfig

        config = GatewayConfig.load()
        if not config.enabled:
            print(f"{Colors.YELLOW}Gateway bridge is disabled in config.{Colors.NC}")
            print(f"Enable it in ~/.config/meshforge/gateway.json or via the UI.\n")
            try:
                enable = input(f"Enable and start now? [y/N]: ").strip().lower()
                if enable in ['y', 'yes']:
                    config.enabled = True
                    config.save()
                else:
                    return
            except (KeyboardInterrupt, EOFError):
                return

        bridge = RNSMeshtasticBridge(config)
        print(f"{Colors.GREEN}Bridge starting...{Colors.NC}")

        if bridge.start():
            print(f"{Colors.GREEN}+ Gateway bridge running{Colors.NC}")
            print(f"{Colors.DIM}Stats: {bridge.get_routing_stats()}{Colors.NC}\n")

            import time
            try:
                while bridge.is_running:
                    time.sleep(5)
                    stats = bridge.get_routing_stats()
                    print(f"\r{Colors.DIM}M->R:{stats.get('messages_mesh_to_rns', 0)} "
                          f"R->M:{stats.get('messages_rns_to_mesh', 0)} "
                          f"Bounced:{stats.get('bounced', 0)}{Colors.NC}", end='', flush=True)
            except KeyboardInterrupt:
                print(f"\n\n{Colors.YELLOW}Stopping bridge...{Colors.NC}")
                bridge.stop()
                print(f"{Colors.GREEN}Bridge stopped.{Colors.NC}")
        else:
            print(f"{Colors.RED}Failed to start bridge. Check logs for details.{Colors.NC}")

    except ImportError as e:
        print(f"{Colors.RED}Gateway module not available: {e}{Colors.NC}")
    except Exception as e:
        print(f"{Colors.RED}Error starting bridge: {e}{Colors.NC}")


def start_noc_services():
    """Start NOC managed services (meshtasticd, rnsd) if in local mode."""
    if not HAS_ORCHESTRATOR:
        return True

    noc_config_path = Path('/etc/meshforge/noc.yaml')
    if not noc_config_path.exists():
        return True

    try:
        import yaml
        with open(noc_config_path) as f:
            config = yaml.safe_load(f)
    except Exception:
        return True

    noc_mode = config.get('noc', {}).get('mode', 'client')
    if noc_mode != 'local':
        return True

    print(f"{Colors.CYAN}Starting NOC services...{Colors.NC}")

    orch = ServiceOrchestrator()
    statuses = orch.get_all_status()

    for name, status in statuses.items():
        if status.state == ServiceState.NOT_INSTALLED:
            print(f"  {Colors.YELLOW}! {name} not installed{Colors.NC}")
        elif status.state == ServiceState.RUNNING:
            print(f"  {Colors.GREEN}+ {name} running{Colors.NC}")

    success = orch.startup()

    if success:
        print(f"{Colors.GREEN}+ NOC services ready{Colors.NC}")
    else:
        print(f"{Colors.YELLOW}! Some services failed to start{Colors.NC}")

    return success


def main():
    """Main entry point"""
    # Handle --status (no root needed, quick exit)
    if '--status' in sys.argv:
        src_dir = Path(__file__).parent
        subprocess.run([sys.executable, str(src_dir / 'cli' / 'status.py')] +
                       [a for a in sys.argv[1:] if a != '--status'], timeout=30)
        sys.exit(0)

    # Check root
    if os.geteuid() != 0:
        print(f"\n{Colors.RED}Error: This application requires root/sudo privileges{Colors.NC}")
        print(f"Please run with: {Colors.CYAN}sudo python3 src/launcher.py{Colors.NC}")
        sys.exit(1)

    # Direct interface flags (skip menu)
    if '--gtk' in sys.argv:
        launch_interface('1')
    if '--tui' in sys.argv:
        launch_interface('2')

    # Start NOC services if in local mode
    if '--no-services' not in sys.argv:
        start_noc_services()

    # Check for first run
    if '--setup' in sys.argv or check_first_run():
        run_setup_wizard()

    # Load saved preferences
    prefs = load_preferences()
    saved_interface = prefs.get('interface')
    auto_launch = prefs.get('auto_launch', False)

    # Auto-launch saved preference if set
    if auto_launch and saved_interface in ['1', '2']:
        env = detect_environment()
        can_launch = True
        if saved_interface == '1' and not (env['has_display'] and env['has_gtk']):
            can_launch = False

        if can_launch:
            print(f"{Colors.GREEN}Auto-launching saved preference...{Colors.NC}")
            print(f"{Colors.DIM}(Run with --wizard to change){Colors.NC}")
            import time
            time.sleep(1)
            launch_interface(saved_interface)
        else:
            print(f"{Colors.YELLOW}Saved interface not available, showing menu...{Colors.NC}")

    # Check for --wizard flag
    if '--wizard' in sys.argv:
        prefs['auto_launch'] = False
        save_preferences(prefs)

    while True:
        subprocess.run(['clear'] if os.name == 'posix' else ['cls'], check=False, timeout=5)

        print_banner()
        show_startup_health()

        env = detect_environment()
        print_environment_info(env)

        recommended = get_recommendation(env)
        print_menu(env, recommended)

        try:
            choice = input(f"{Colors.CYAN}Select [{recommended}]: {Colors.NC}").strip() or recommended
        except (KeyboardInterrupt, EOFError):
            print(f"\n\n{Colors.YELLOW}A Hui Hou!{Colors.NC}")
            sys.exit(0)

        if choice.lower() == 'q':
            print(f"\n{Colors.YELLOW}A Hui Hou!{Colors.NC}")
            sys.exit(0)

        elif choice.lower() == 'i':
            install_gtk()

        elif choice.lower() == 'w':
            run_setup_wizard()

        elif choice in ['1', '2']:
            # Validate GTK availability
            if choice == '1' and not env['has_display']:
                print(f"\n{Colors.YELLOW}No display detected. GTK4 requires a display.{Colors.NC}")
                try:
                    confirm = input(f"Continue anyway? [y/N]: ").strip().lower()
                    if confirm not in ['y', 'yes']:
                        continue
                except (KeyboardInterrupt, EOFError):
                    continue

            if choice == '1' and not env['has_gtk']:
                print(f"\n{Colors.YELLOW}GTK4 not installed.{Colors.NC}")
                try:
                    confirm = input(f"Install now? [Y/n]: ").strip().lower()
                    if confirm in ['', 'y', 'yes']:
                        install_gtk()
                        continue
                except (KeyboardInterrupt, EOFError):
                    continue

            # Save preference
            prefs['interface'] = choice
            try:
                confirm = input(f"\n{Colors.DIM}Remember this choice? [Y/n]: {Colors.NC}").strip().lower()
                prefs['auto_launch'] = confirm in ['', 'y', 'yes']
            except (KeyboardInterrupt, EOFError):
                prefs['auto_launch'] = False
            save_preferences(prefs)

            launch_interface(choice)

        elif choice in ['3', '4', '5']:
            launch_interface(choice)

        else:
            print(f"\n{Colors.RED}Invalid option.{Colors.NC}")
            input(f"{Colors.DIM}Press Enter...{Colors.NC}")


if __name__ == '__main__':
    main()
