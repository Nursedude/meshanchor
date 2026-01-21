"""
MeshForge Utilities Module

Contains debug, diagnostic, and utility functions:
- debug_menu: Debug and troubleshooting menu
- check_dependencies: Dependency verification
- detect_hardware: Hardware detection display
- view_logs: Log viewing utilities
- Version and update checking

Extracted from main.py for maintainability.
"""

import os
import subprocess

from rich.console import Console
from rich.prompt import Prompt, Confirm
from rich.table import Table

from utils import emoji as em

console = Console()


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
    console.print("\n[bold cyan]=============== Debug & Troubleshooting ===============[/bold cyan]\n")

    console.print("[dim cyan]-- Diagnostics --[/dim cyan]")
    console.print(f"  [bold]1[/bold]. {em.get('📜')} View installation logs")
    console.print(f"  [bold]2[/bold]. {em.get('⚠️')} View error logs")
    console.print(f"  [bold]3[/bold]. {em.get('🔄')} Test meshtasticd service")
    console.print(f"  [bold]4[/bold]. {em.get('🔐')} Check permissions")

    console.print("\n[dim cyan]-- Updates & Version --[/dim cyan]")
    console.print(f"  [bold]5[/bold]. {em.get('⬆️')}  [yellow]Check for updates[/yellow]")
    console.print(f"  [bold]6[/bold]. {em.get('📋')} [yellow]Version history[/yellow]")
    console.print(f"  [bold]7[/bold]. {em.get('ℹ️')}  [yellow]Show version info[/yellow]")

    console.print("\n[dim cyan]-- Configuration --[/dim cyan]")
    console.print(f"  [bold]8[/bold]. {em.get('⚙️')}  [yellow]Show environment config[/yellow]")
    console.print(f"  [bold]9[/bold]. {em.get('🎨', '[EMJ]')}  [yellow]Emoji support status[/yellow]")

    console.print(f"\n  [bold]0[/bold]. {em.get('⬅️')}  Back to main menu")

    choice = Prompt.ask("\n[cyan]Select an option[/cyan]", choices=["0", "1", "2", "3", "4", "5", "6", "7", "8", "9"], default="0")

    if choice == "1":
        view_logs()
    elif choice == "2":
        view_error_logs()
    elif choice == "3":
        test_service()
    elif choice == "4":
        check_permissions()
    elif choice == "5":
        check_updates_manual()
    elif choice == "6":
        show_version_history()
    elif choice == "7":
        show_version_info()
    elif choice == "8":
        show_environment_config()
    elif choice == "9":
        check_emoji_support()


def check_emoji_support():
    """Check and display emoji support status"""
    from utils import emoji as emoji_utils

    emoji_utils.setup_emoji_support(console)
    Prompt.ask("\n[dim]Press Enter to return[/dim]")


def show_environment_config():
    """Show current environment configuration"""
    console.print("\n[bold cyan]Environment Configuration[/bold cyan]\n")

    from utils.env_config import show_config_summary, validate_config
    show_config_summary()

    # Also show validation results
    validation = validate_config()
    if validation['warnings']:
        console.print("\n[yellow]Warnings:[/yellow]")
        for warning in validation['warnings']:
            console.print(f"  [yellow]⚠ {warning}[/yellow]")

    if validation['errors']:
        console.print("\n[red]Errors:[/red]")
        for error in validation['errors']:
            console.print(f"  [red]✗ {error}[/red]")

    Prompt.ask("\n[dim]Press Enter to return[/dim]")


def view_logs():
    """View application logs"""
    log_file = "/var/log/meshtasticd-installer.log"
    if os.path.exists(log_file):
        console.print(f"\n[cyan]Showing last 50 lines of {log_file}:[/cyan]\n")
        try:
            result = subprocess.run(['tail', '-n', '50', log_file],
                                  capture_output=True, text=True, check=True, timeout=10)
            from rich.panel import Panel
            console.print(Panel(result.stdout, title="[cyan]Installation Log[/cyan]", border_style="cyan"))
        except subprocess.CalledProcessError as e:
            console.print(f"[red]Error reading log file: {e}[/red]")
    else:
        console.print("\n[yellow]No installation log file found[/yellow]")
        console.print("[dim]Log will be created on first installation[/dim]")


def view_error_logs():
    """View detailed error logs"""
    error_log_file = "/var/log/meshtasticd-installer-error.log"

    if os.path.exists(error_log_file):
        console.print(f"\n[red bold]Installation Error Log[/red bold]\n")

        # Check file size
        file_size = os.path.getsize(error_log_file)

        if file_size == 0:
            console.print("[green]No errors logged - installation has been successful![/green]")
            return

        try:
            # Read the entire error log
            with open(error_log_file, 'r') as f:
                error_content = f.read()

            from rich.panel import Panel

            # Show last 100 lines to avoid overwhelming output
            lines = error_content.split('\n')
            if len(lines) > 100:
                display_content = '\n'.join(lines[-100:])
                console.print(f"[dim]Showing last 100 lines (file has {len(lines)} total lines)[/dim]\n")
            else:
                display_content = error_content

            console.print(Panel(
                display_content,
                title="[red]Error Details[/red]",
                border_style="red",
                expand=False
            ))

            console.print(f"\n[dim]Full error log location: {error_log_file}[/dim]")
            console.print(f"[dim]File size: {file_size} bytes[/dim]")

            # Offer to clear the log
            if Confirm.ask("\n[yellow]Clear error log?[/yellow]", default=False):
                try:
                    with open(error_log_file, 'w') as f:
                        f.write("")
                    console.print("[green]Error log cleared[/green]")
                except Exception as e:
                    console.print(f"[red]Failed to clear log: {e}[/red]")

        except Exception as e:
            console.print(f"[red]Error reading error log file: {e}[/red]")
    else:
        console.print("\n[green]No error log found - no errors have been recorded![/green]")
        console.print("[dim]Error log will be created if installation fails[/dim]")


def test_service():
    """Test meshtasticd service"""
    console.print("\n[cyan]Testing meshtasticd service...[/cyan]\n")
    result = subprocess.run(['systemctl', 'status', 'meshtasticd'],
                          capture_output=True, text=True, timeout=15)
    console.print(result.stdout)
    if result.stderr:
        console.print(result.stderr)


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
