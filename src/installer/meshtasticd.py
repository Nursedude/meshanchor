"""Meshtasticd installer module"""

import os
import subprocess
from pathlib import Path
from rich.console import Console

from utils.system import (
    get_system_info,
    get_os_type,
    run_command,
    check_internet_connection,
    is_service_running,
    enable_service
)
from utils.logger import log, log_command, log_exception

console = Console()


class MeshtasticdInstaller:
    """Handles meshtasticd installation and updates"""

    def __init__(self):
        self.system_info = get_system_info()
        self.os_type = get_os_type()
        self.scripts_dir = Path(__file__).parent.parent.parent / 'scripts'

    def check_prerequisites(self):
        """Check if system meets prerequisites"""
        issues = []

        # Check if Raspberry Pi
        if not self.system_info['is_pi']:
            issues.append("This tool is designed for Raspberry Pi OS")

        # Check internet connection
        if not check_internet_connection():
            issues.append("No internet connection detected")

        # Check available disk space (need at least 100MB)
        from utils.system import get_disk_space
        disk_space = get_disk_space()
        if disk_space < 100:
            issues.append(f"Low disk space: {disk_space}MB available (need 100MB minimum)")

        # Check if supported architecture
        if self.os_type == 'unknown':
            issues.append(f"Unsupported architecture: {self.system_info['arch']}")

        return issues

    def install(self, version_type='stable'):
        """Install meshtasticd"""
        log(f"Starting meshtasticd installation (version: {version_type})")
        console.print(f"\n[cyan]Installing meshtasticd ({version_type} version)...[/cyan]")

        # Check prerequisites
        issues = self.check_prerequisites()
        if issues:
            console.print("\n[bold red]Cannot proceed with installation:[/bold red]")
            for issue in issues:
                console.print(f"  - {issue}")
            log(f"Installation aborted due to prerequisites: {issues}", 'error')
            return False

        # Display system info
        console.print(f"\n[cyan]System: {self.system_info['os']} ({self.os_type})[/cyan]")
        console.print(f"[cyan]Architecture: {self.system_info['arch']} ({self.system_info['bits']}-bit)[/cyan]")

        # Update package lists
        console.print("\n[cyan]Updating package lists...[/cyan]")
        result = run_command('apt-get update')
        log_command('apt-get update', result)

        if not result['success']:
            console.print("[bold red]Failed to update package lists[/bold red]")
            return False

        # Run appropriate installation script
        if self.os_type == 'armhf':
            return self._install_armhf(version_type)
        elif self.os_type == 'arm64':
            return self._install_arm64(version_type)
        else:
            console.print(f"[bold red]Unsupported OS type: {self.os_type}[/bold red]")
            return False

    def _install_armhf(self, version_type):
        """Install on 32-bit Raspberry Pi OS"""
        log("Installing on armhf (32-bit)")
        console.print("\n[cyan]Installing for 32-bit Raspberry Pi OS...[/cyan]")

        script_path = self.scripts_dir / 'install_armhf.sh'

        if not script_path.exists():
            console.print(f"[bold red]Installation script not found: {script_path}[/bold red]")
            return False

        # Make script executable
        os.chmod(script_path, 0o755)

        # Run installation script
        result = run_command(f'bash {script_path} {version_type}', shell=True)
        log_command(f'bash {script_path}', result)

        if result['success']:
            console.print("\n[bold green]Installation completed successfully![/bold green]")

            # Setup permissions
            self._setup_permissions()

            # Enable and start service
            if self._setup_service():
                console.print("[bold green]Service enabled and started[/bold green]")
            else:
                console.print("[bold yellow]Service setup had issues (check logs)[/bold yellow]")

            return True
        else:
            console.print("\n[bold red]Installation failed![/bold red]")
            if result['stderr']:
                console.print(f"Error: {result['stderr']}")
            return False

    def _install_arm64(self, version_type):
        """Install on 64-bit Raspberry Pi OS"""
        log("Installing on arm64 (64-bit)")
        console.print("\n[cyan]Installing for 64-bit Raspberry Pi OS...[/cyan]")

        script_path = self.scripts_dir / 'install_arm64.sh'

        if not script_path.exists():
            console.print(f"[bold red]Installation script not found: {script_path}[/bold red]")
            return False

        # Make script executable
        os.chmod(script_path, 0o755)

        # Run installation script
        result = run_command(f'bash {script_path} {version_type}', shell=True)
        log_command(f'bash {script_path}', result)

        if result['success']:
            console.print("\n[bold green]Installation completed successfully![/bold green]")

            # Setup permissions
            self._setup_permissions()

            # Enable and start service
            if self._setup_service():
                console.print("[bold green]Service enabled and started[/bold green]")
            else:
                console.print("[bold yellow]Service setup had issues (check logs)[/bold yellow]")

            return True
        else:
            console.print("\n[bold red]Installation failed![/bold red]")
            if result['stderr']:
                console.print(f"Error: {result['stderr']}")
            return False

    def _setup_permissions(self):
        """Setup GPIO/SPI permissions"""
        log("Setting up GPIO/SPI permissions")
        console.print("\n[cyan]Setting up GPIO/SPI permissions...[/cyan]")

        script_path = self.scripts_dir / 'setup_permissions.sh'

        if script_path.exists():
            os.chmod(script_path, 0o755)
            result = run_command(f'bash {script_path}', shell=True)
            log_command('setup_permissions.sh', result)
            return result['success']

        return False

    def _setup_service(self):
        """Enable and start meshtasticd service"""
        log("Setting up meshtasticd service")
        console.print("\n[cyan]Setting up meshtasticd service...[/cyan]")

        return enable_service('meshtasticd')

    def update(self):
        """Update meshtasticd"""
        log("Starting meshtasticd update")
        console.print("\n[cyan]Updating meshtasticd...[/cyan]")

        # Check if meshtasticd is installed
        result = run_command('which meshtasticd')
        if not result['success']:
            console.print("[bold red]meshtasticd is not installed[/bold red]")
            return False

        # Update package lists
        console.print("\n[cyan]Updating package lists...[/cyan]")
        result = run_command('apt-get update')
        log_command('apt-get update', result)

        # Upgrade meshtasticd
        console.print("\n[cyan]Upgrading meshtasticd...[/cyan]")
        result = run_command('apt-get install --only-upgrade meshtasticd -y')
        log_command('apt-get upgrade meshtasticd', result)

        if result['success']:
            console.print("\n[bold green]Update completed successfully![/bold green]")

            # Restart service
            from utils.system import restart_service
            if restart_service('meshtasticd'):
                console.print("[bold green]Service restarted[/bold green]")

            return True
        else:
            console.print("\n[bold red]Update failed![/bold red]")
            return False

    def get_installed_version(self):
        """Get currently installed version"""
        result = run_command('meshtasticd --version')
        if result['success']:
            return result['stdout'].strip()
        return None

    def is_installed(self):
        """Check if meshtasticd is installed"""
        result = run_command('which meshtasticd')
        return result['success']
