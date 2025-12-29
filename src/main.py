#!/usr/bin/env python3
"""
Meshtasticd Interactive Installer & Manager
Main entry point for the application

Version: 2.0.0
Features:
- Quick Status Dashboard
- Interactive Channel Configuration with Presets
- Automatic Update Notifications
- Configuration Templates for Common Setups
- Version Control
"""

import os
import sys
import click
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt, Confirm
from rich.table import Table

# Add src to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from utils.system import check_root, get_system_info
from utils.logger import setup_logger, log
from installer.meshtasticd import MeshtasticdInstaller
from config.device import DeviceConfigurator
from __version__ import __version__, get_full_version

console = Console()

BANNER = f"""
╔═══════════════════════════════════════════════════════════╗
║   Meshtasticd Interactive Installer & Manager             ║
║   For Raspberry Pi OS                          v{__version__}   ║
╚═══════════════════════════════════════════════════════════╝
"""


def show_banner():
    """Display application banner"""
    console.print(BANNER, style="bold cyan")


def show_system_info():
    """Display system information"""
    info = get_system_info()

    table = Table(title="System Information", show_header=True, header_style="bold magenta")
    table.add_column("Property", style="cyan")
    table.add_column("Value", style="green")

    table.add_row("OS", info.get('os', 'Unknown'))
    table.add_row("Architecture", info.get('arch', 'Unknown'))
    table.add_row("Platform", info.get('platform', 'Unknown'))
    table.add_row("Python Version", info.get('python', 'Unknown'))
    table.add_row("Kernel", info.get('kernel', 'Unknown'))

    console.print(table)
    console.print()


def check_for_updates_on_startup():
    """Check for updates on startup and show notification if available"""
    try:
        from installer.update_notifier import UpdateNotifier
        notifier = UpdateNotifier()
        notifier.startup_update_check()
    except Exception:
        # Don't let update check failures interrupt startup
        pass


def show_quick_status():
    """Show quick status line in menu"""
    try:
        from dashboard import StatusDashboard
        dashboard = StatusDashboard()
        status_line = dashboard.get_quick_status_line()
        console.print(f"\n[dim]Status:[/dim] {status_line}")
    except Exception:
        pass


def interactive_menu():
    """Show interactive menu and handle user choices"""
    show_banner()

    # Check if running as root
    if not check_root():
        console.print("[bold red]Error:[/bold red] This tool requires root/sudo privileges")
        console.print("Please run with: [cyan]sudo python3 src/main.py[/cyan]")
        sys.exit(1)

    # Check for updates on startup
    check_for_updates_on_startup()

    show_system_info()

    while True:
        # Show quick status
        show_quick_status()

        console.print("\n[bold cyan]Main Menu:[/bold cyan]")
        console.print("1. [green]Quick Status Dashboard[/green]")
        console.print("2. Install meshtasticd")
        console.print("3. Update meshtasticd")
        console.print("4. Configure device")
        console.print("5. [yellow]Channel Presets[/yellow]")
        console.print("6. Configuration Templates")
        console.print("7. Check dependencies")
        console.print("8. Hardware detection")
        console.print("9. Debug & troubleshooting")
        console.print("0. Exit")

        choice = Prompt.ask("\nSelect an option", choices=["0", "1", "2", "3", "4", "5", "6", "7", "8", "9"], default="1")

        if choice == "1":
            show_dashboard()
        elif choice == "2":
            install_meshtasticd()
        elif choice == "3":
            update_meshtasticd()
        elif choice == "4":
            configure_device()
        elif choice == "5":
            configure_channel_presets()
        elif choice == "6":
            manage_templates()
        elif choice == "7":
            check_dependencies()
        elif choice == "8":
            detect_hardware()
        elif choice == "9":
            debug_menu()
        elif choice == "0":
            console.print("\n[green]Goodbye![/green]")
            sys.exit(0)


def show_dashboard():
    """Show the quick status dashboard"""
    from dashboard import StatusDashboard
    dashboard = StatusDashboard()
    dashboard.interactive_dashboard()


def configure_channel_presets():
    """Configure channels using presets"""
    console.print("\n[bold cyan]Channel Configuration with Presets[/bold cyan]\n")

    from config.channel_presets import ChannelPresetManager

    preset_manager = ChannelPresetManager()
    config = preset_manager.select_preset()

    if config:
        if Confirm.ask("\nApply this configuration?", default=True):
            preset_manager.apply_preset_to_config(config)
            console.print("\n[green]Channel configuration applied![/green]")
    else:
        console.print("\n[yellow]Configuration cancelled[/yellow]")


def manage_templates():
    """Manage configuration templates"""
    console.print("\n[bold cyan]Configuration Templates[/bold cyan]\n")

    console.print("[cyan]Available Templates:[/cyan]")
    console.print("1. MeshAdv-Mini (SX1262/SX1268 HAT)")
    console.print("2. MeshAdv-Mini 400MHz variant")
    console.print("3. Waveshare SX1262")
    console.print("4. Adafruit RFM9x")
    console.print("5. [yellow]MtnMesh Community[/yellow]")
    console.print("6. [yellow]Emergency/SAR[/yellow]")
    console.print("7. [yellow]Urban High-Speed[/yellow]")
    console.print("8. [yellow]Repeater Node[/yellow]")
    console.print("9. Back to Main Menu")

    choice = Prompt.ask("\nSelect template", choices=["1", "2", "3", "4", "5", "6", "7", "8", "9"], default="9")

    template_map = {
        "1": "meshadv-mini.yaml",
        "2": "meshadv-mini-400mhz.yaml",
        "3": "waveshare-sx1262.yaml",
        "4": "adafruit-rfm9x.yaml",
        "5": "mtnmesh-community.yaml",
        "6": "emergency-sar.yaml",
        "7": "urban-highspeed.yaml",
        "8": "repeater-node.yaml"
    }

    if choice in template_map:
        apply_template(template_map[choice])


def apply_template(template_name):
    """Apply a configuration template"""
    import shutil
    from pathlib import Path

    src_dir = Path(__file__).parent.parent / 'templates' / 'available.d'
    template_path = src_dir / template_name
    dest_path = Path('/etc/meshtasticd/config.yaml')

    if not template_path.exists():
        console.print(f"[red]Template not found: {template_name}[/red]")
        return

    # Show template content
    console.print(f"\n[cyan]Template: {template_name}[/cyan]")
    console.print("[dim]Preview:[/dim]\n")

    with open(template_path, 'r') as f:
        content = f.read()
        # Show first 30 lines
        lines = content.split('\n')[:30]
        for line in lines:
            console.print(f"[dim]{line}[/dim]")
        if len(content.split('\n')) > 30:
            console.print("[dim]...[/dim]")

    if Confirm.ask(f"\nApply template to {dest_path}?", default=True):
        try:
            # Backup existing config
            if dest_path.exists():
                backup_path = dest_path.with_suffix('.yaml.bak')
                shutil.copy2(dest_path, backup_path)
                console.print(f"[dim]Backed up existing config to {backup_path}[/dim]")

            shutil.copy2(template_path, dest_path)
            console.print(f"[green]Template applied successfully![/green]")

            if Confirm.ask("\nRestart meshtasticd service?", default=True):
                os.system("systemctl restart meshtasticd")
                console.print("[green]Service restarted![/green]")

        except Exception as e:
            console.print(f"[red]Failed to apply template: {e}[/red]")


def install_meshtasticd():
    """Install meshtasticd"""
    console.print("\n[bold cyan]Installing meshtasticd[/bold cyan]\n")

    # Ask for version preference
    version_type = Prompt.ask(
        "Select version",
        choices=["stable", "beta"],
        default="stable"
    )

    installer = MeshtasticdInstaller()

    with console.status("[bold green]Installing..."):
        success = installer.install(version_type=version_type)

    if success:
        console.print("\n[bold green]Installation completed successfully![/bold green]")

        if Confirm.ask("\nWould you like to configure the device now?"):
            configure_device()
    else:
        console.print("\n[bold red]Installation failed. Check logs for details.[/bold red]")


def update_meshtasticd():
    """Update meshtasticd"""
    console.print("\n[bold cyan]Updating meshtasticd[/bold cyan]\n")

    # First check for available updates
    from installer.update_notifier import UpdateNotifier
    notifier = UpdateNotifier()

    update_info = notifier.check_for_updates(force=True)

    if update_info:
        if update_info.get('update_available'):
            console.print(f"[green]Update available![/green]")
            console.print(f"  Current: {update_info['current']}")
            console.print(f"  Latest:  {update_info['latest']}")

            if not Confirm.ask("\nProceed with update?", default=True):
                return
        else:
            console.print(f"[green]You're running the latest version ({update_info.get('current', 'Unknown')})[/green]")
            if not Confirm.ask("\nReinstall anyway?", default=False):
                return

    installer = MeshtasticdInstaller()

    with console.status("[bold green]Updating..."):
        success = installer.update()

    if success:
        console.print("\n[bold green]Update completed successfully![/bold green]")
    else:
        console.print("\n[bold red]Update failed. Check logs for details.[/bold red]")


def configure_device():
    """Configure meshtastic device"""
    console.print("\n[bold cyan]Device Configuration[/bold cyan]\n")

    while True:
        console.print("\n[cyan]Configuration Options:[/cyan]")
        console.print("1. Complete Radio Setup (Modem Preset + Channel Slot)")
        console.print("2. LoRa Settings (Region, Preset)")
        console.print("3. Channel Configuration")
        console.print("4. [yellow]Channel Presets[/yellow] (New!)")
        console.print("5. Module Configuration (MQTT, Serial, etc.)")
        console.print("6. Device Settings (Name, WiFi, etc.)")
        console.print("7. Hardware Detection")
        console.print("8. SPI HAT Configuration (MeshAdv-Mini, etc.)")
        console.print("9. Back to Main Menu")

        choice = Prompt.ask("\nSelect configuration option", choices=["1", "2", "3", "4", "5", "6", "7", "8", "9"], default="1")

        if choice == "1":
            configure_radio_complete()
        elif choice == "2":
            configure_lora()
        elif choice == "3":
            configure_channels()
        elif choice == "4":
            configure_channel_presets()
        elif choice == "5":
            configure_modules()
        elif choice == "6":
            configure_device_settings()
        elif choice == "7":
            detect_hardware()
        elif choice == "8":
            configure_spi_hat()
        elif choice == "9":
            break


def configure_spi_hat():
    """Configure SPI HAT devices (MeshAdv-Mini, etc.)"""
    console.print("\n[bold cyan]SPI HAT Configuration[/bold cyan]\n")

    from config.spi_hats import SPIHatConfigurator

    spi_config = SPIHatConfigurator()
    config = spi_config.interactive_configure()

    if config:
        console.print("\n[green]SPI HAT configuration complete![/green]")
    else:
        console.print("\n[yellow]Configuration cancelled[/yellow]")


def configure_radio_complete():
    """Complete radio configuration with modem preset and channel slot"""
    console.print("\n[bold cyan]Complete Radio Configuration[/bold cyan]\n")

    from config.radio import RadioConfigurator

    radio_config = RadioConfigurator()
    config = radio_config.configure_radio_settings()

    # Ask to save
    if Confirm.ask("\nSave configuration to /etc/meshtasticd/config.yaml?", default=True):
        radio_config.save_configuration_yaml(config)

    console.print("\n[green]Radio configuration complete![/green]")


def configure_lora():
    """Configure LoRa settings"""
    console.print("\n[bold cyan]LoRa Configuration[/bold cyan]\n")

    from config.lora import LoRaConfigurator

    lora_config = LoRaConfigurator()

    # Region
    region = lora_config.configure_region()

    # Modem preset
    if Confirm.ask("\nConfigure modem preset?", default=True):
        preset_config = lora_config.configure_modem_preset()
        console.print("\n[green]LoRa settings configured![/green]")


def configure_channels():
    """Configure channels"""
    console.print("\n[bold cyan]Channel Configuration[/bold cyan]\n")

    from config.lora import LoRaConfigurator

    lora_config = LoRaConfigurator()
    channels = lora_config.configure_channels()

    console.print("\n[green]Channels configured![/green]")


def configure_modules():
    """Configure Meshtastic modules"""
    console.print("\n[bold cyan]Module Configuration[/bold cyan]\n")

    from config.modules import ModuleConfigurator

    module_config = ModuleConfigurator()
    config = module_config.interactive_module_config()

    console.print("\n[green]Module configuration complete![/green]")


def configure_device_settings():
    """Configure device settings"""
    configurator = DeviceConfigurator()
    configurator.interactive_configure()


def check_dependencies():
    """Check and fix dependencies"""
    console.print("\n[bold cyan]Checking Dependencies[/bold cyan]\n")

    from installer.dependencies import DependencyManager

    manager = DependencyManager()

    with console.status("[bold green]Checking..."):
        issues = manager.check_all()

    if issues:
        console.print("\n[bold yellow]Found issues:[/bold yellow]")
        for issue in issues:
            console.print(f"  - {issue}")

        if Confirm.ask("\nWould you like to fix these issues?"):
            with console.status("[bold green]Fixing..."):
                manager.fix_all()
            console.print("\n[bold green]Dependencies fixed![/bold green]")
    else:
        console.print("\n[bold green]All dependencies are up to date![/bold green]")


def detect_hardware():
    """Detect hardware"""
    console.print("\n[bold cyan]Hardware Detection[/bold cyan]\n")

    from config.hardware import HardwareDetector

    detector = HardwareDetector()

    with console.status("[bold green]Detecting..."):
        hardware = detector.detect_all()

    if hardware:
        table = Table(title="Detected Hardware", show_header=True, header_style="bold magenta")
        table.add_column("Type", style="cyan")
        table.add_column("Details", style="green")

        for hw_type, details in hardware.items():
            table.add_row(hw_type, str(details))

        console.print(table)
    else:
        console.print("\n[bold yellow]No compatible hardware detected[/bold yellow]")


def debug_menu():
    """Debug and troubleshooting menu"""
    console.print("\n[bold cyan]Debug & Troubleshooting[/bold cyan]\n")
    console.print("1. View logs")
    console.print("2. Test meshtasticd service")
    console.print("3. Check permissions")
    console.print("4. [yellow]Check for updates[/yellow]")
    console.print("5. [yellow]Version history[/yellow]")
    console.print("6. [yellow]Show version info[/yellow]")
    console.print("7. Back to main menu")

    choice = Prompt.ask("\nSelect an option", choices=["1", "2", "3", "4", "5", "6", "7"], default="7")

    if choice == "1":
        view_logs()
    elif choice == "2":
        test_service()
    elif choice == "3":
        check_permissions()
    elif choice == "4":
        check_updates_manual()
    elif choice == "5":
        show_version_history()
    elif choice == "6":
        show_version_info()


def view_logs():
    """View application logs"""
    log_file = "/var/log/meshtasticd-installer.log"
    if os.path.exists(log_file):
        console.print(f"\n[cyan]Showing last 50 lines of {log_file}:[/cyan]\n")
        os.system(f"tail -n 50 {log_file}")
    else:
        console.print("\n[yellow]No log file found[/yellow]")


def test_service():
    """Test meshtasticd service"""
    console.print("\n[cyan]Testing meshtasticd service...[/cyan]\n")
    os.system("systemctl status meshtasticd")


def check_permissions():
    """Check GPIO/SPI permissions"""
    console.print("\n[cyan]Checking permissions...[/cyan]\n")

    from installer.dependencies import DependencyManager
    manager = DependencyManager()
    manager.check_permissions()


def check_updates_manual():
    """Manually check for updates"""
    console.print("\n[cyan]Checking for updates...[/cyan]\n")

    from installer.update_notifier import UpdateNotifier
    notifier = UpdateNotifier()

    update_info = notifier.check_for_updates(force=True)

    if update_info:
        if update_info.get('update_available'):
            notifier.show_update_notification(update_info)
        else:
            console.print(f"[green]You're running the latest version ({update_info.get('current', 'Unknown')})[/green]")
    else:
        console.print("[yellow]Could not check for updates[/yellow]")


def show_version_history():
    """Show version history"""
    from installer.update_notifier import UpdateNotifier
    notifier = UpdateNotifier()
    notifier.get_version_history()


def show_version_info():
    """Show version information"""
    from __version__ import show_version_history, get_full_version
    console.print(f"\n[bold cyan]Installer Version: {get_full_version()}[/bold cyan]\n")
    show_version_history()


@click.command()
@click.option('--install', type=click.Choice(['stable', 'beta']), help='Install meshtasticd')
@click.option('--update', is_flag=True, help='Update meshtasticd')
@click.option('--configure', is_flag=True, help='Configure device')
@click.option('--check', is_flag=True, help='Check dependencies')
@click.option('--dashboard', is_flag=True, help='Show status dashboard')
@click.option('--version', is_flag=True, help='Show version information')
@click.option('--debug', is_flag=True, help='Enable debug logging')
def main(install, update, configure, check, dashboard, version, debug):
    """Meshtasticd Interactive Installer & Manager"""

    # Setup logging
    setup_logger(debug=debug)

    # Show version and exit
    if version:
        console.print(f"Meshtasticd Interactive Installer v{get_full_version()}")
        return

    # Show dashboard
    if dashboard:
        show_dashboard()
        return

    # If no arguments, show interactive menu
    if not any([install, update, configure, check]):
        interactive_menu()
        return

    # Check root
    if not check_root():
        console.print("[bold red]Error:[/bold red] This tool requires root/sudo privileges")
        sys.exit(1)

    # Handle command line options
    if install:
        installer = MeshtasticdInstaller()
        installer.install(version_type=install)

    if update:
        installer = MeshtasticdInstaller()
        installer.update()

    if configure:
        configurator = DeviceConfigurator()
        configurator.interactive_configure()

    if check:
        from installer.dependencies import DependencyManager
        manager = DependencyManager()
        manager.check_all()


if __name__ == '__main__':
    main()
