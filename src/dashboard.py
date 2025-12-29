"""Quick Status Dashboard for Meshtasticd"""

import os
import subprocess
from datetime import datetime
from pathlib import Path
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.columns import Columns
from rich.text import Text
from rich.layout import Layout
from rich.live import Live
from rich.prompt import Prompt, Confirm
import time

console = Console()


class StatusDashboard:
    """Quick Status Dashboard showing system and mesh network status"""

    def __init__(self):
        self.config_path = Path('/etc/meshtasticd/config.yaml')
        self.log_path = Path('/var/log/meshtasticd.log')

    def get_service_status(self):
        """Get meshtasticd service status"""
        try:
            result = subprocess.run(
                ['systemctl', 'is-active', 'meshtasticd'],
                capture_output=True, text=True, timeout=5
            )
            status = result.stdout.strip()
            return {
                'status': status,
                'running': status == 'active',
                'color': 'green' if status == 'active' else 'red'
            }
        except Exception:
            return {'status': 'unknown', 'running': False, 'color': 'yellow'}

    def get_service_uptime(self):
        """Get service uptime"""
        try:
            result = subprocess.run(
                ['systemctl', 'show', 'meshtasticd', '--property=ActiveEnterTimestamp'],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                timestamp_line = result.stdout.strip()
                if '=' in timestamp_line:
                    timestamp_str = timestamp_line.split('=')[1]
                    if timestamp_str:
                        return timestamp_str
            return 'N/A'
        except Exception:
            return 'N/A'

    def get_installed_version(self):
        """Get installed meshtasticd version"""
        try:
            result = subprocess.run(
                ['meshtasticd', '--version'],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                return result.stdout.strip()
            return 'Not installed'
        except FileNotFoundError:
            return 'Not installed'
        except Exception:
            return 'Unknown'

    def get_system_info(self):
        """Get system information"""
        info = {}

        # CPU temperature
        try:
            with open('/sys/class/thermal/thermal_zone0/temp', 'r') as f:
                temp = int(f.read().strip()) / 1000
                info['cpu_temp'] = f'{temp:.1f}°C'
                info['temp_status'] = 'normal' if temp < 70 else ('warning' if temp < 80 else 'critical')
        except Exception:
            info['cpu_temp'] = 'N/A'
            info['temp_status'] = 'unknown'

        # Memory usage
        try:
            with open('/proc/meminfo', 'r') as f:
                meminfo = {}
                for line in f:
                    parts = line.split(':')
                    if len(parts) == 2:
                        key = parts[0].strip()
                        value = parts[1].strip().split()[0]
                        meminfo[key] = int(value)
                total = meminfo.get('MemTotal', 0)
                available = meminfo.get('MemAvailable', 0)
                used = total - available
                if total > 0:
                    usage_pct = (used / total) * 100
                    info['memory'] = f'{usage_pct:.1f}%'
                    info['memory_status'] = 'normal' if usage_pct < 80 else 'warning'
                else:
                    info['memory'] = 'N/A'
                    info['memory_status'] = 'unknown'
        except Exception:
            info['memory'] = 'N/A'
            info['memory_status'] = 'unknown'

        # Disk usage
        try:
            statvfs = os.statvfs('/')
            total = statvfs.f_blocks * statvfs.f_frsize
            free = statvfs.f_bavail * statvfs.f_frsize
            used = total - free
            if total > 0:
                usage_pct = (used / total) * 100
                info['disk'] = f'{usage_pct:.1f}%'
                info['disk_status'] = 'normal' if usage_pct < 90 else 'warning'
            else:
                info['disk'] = 'N/A'
                info['disk_status'] = 'unknown'
        except Exception:
            info['disk'] = 'N/A'
            info['disk_status'] = 'unknown'

        # System uptime
        try:
            with open('/proc/uptime', 'r') as f:
                uptime_seconds = float(f.read().split()[0])
                days = int(uptime_seconds // 86400)
                hours = int((uptime_seconds % 86400) // 3600)
                minutes = int((uptime_seconds % 3600) // 60)
                if days > 0:
                    info['uptime'] = f'{days}d {hours}h {minutes}m'
                elif hours > 0:
                    info['uptime'] = f'{hours}h {minutes}m'
                else:
                    info['uptime'] = f'{minutes}m'
        except Exception:
            info['uptime'] = 'N/A'

        return info

    def get_network_info(self):
        """Get network information"""
        info = {}

        # Get IP addresses
        try:
            result = subprocess.run(
                ['hostname', '-I'],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                ips = result.stdout.strip().split()
                info['ip'] = ips[0] if ips else 'No IP'
            else:
                info['ip'] = 'N/A'
        except Exception:
            info['ip'] = 'N/A'

        # Check internet connectivity
        try:
            result = subprocess.run(
                ['ping', '-c', '1', '-W', '2', '8.8.8.8'],
                capture_output=True, timeout=5
            )
            info['internet'] = result.returncode == 0
        except Exception:
            info['internet'] = False

        return info

    def get_config_status(self):
        """Check configuration status"""
        status = {
            'config_exists': self.config_path.exists(),
            'config_path': str(self.config_path),
            'active_template': None
        }

        # Check for active templates in config.d
        config_d = Path('/etc/meshtasticd/config.d')
        if config_d.exists():
            templates = list(config_d.glob('*.yaml'))
            if templates:
                status['active_template'] = templates[0].name

        return status

    def get_recent_logs(self, lines=5):
        """Get recent log entries"""
        logs = []
        log_files = [
            Path('/var/log/meshtasticd.log'),
            Path('/var/log/syslog')
        ]

        for log_file in log_files:
            if log_file.exists():
                try:
                    result = subprocess.run(
                        ['tail', '-n', str(lines), str(log_file)],
                        capture_output=True, text=True, timeout=5
                    )
                    if result.returncode == 0:
                        for line in result.stdout.strip().split('\n'):
                            if 'meshtasticd' in line.lower():
                                logs.append(line[:100])
                except Exception:
                    pass
                if logs:
                    break

        return logs[-lines:] if logs else ['No recent logs available']

    def create_status_panel(self):
        """Create the main status panel"""
        service = self.get_service_status()

        # Service status with icon
        if service['running']:
            status_text = Text("● RUNNING", style="bold green")
        else:
            status_text = Text("● STOPPED", style="bold red")

        content = Text()
        content.append("Service Status: ")
        content.append(status_text)
        content.append(f"\nVersion: {self.get_installed_version()}")
        content.append(f"\nStarted: {self.get_service_uptime()}")

        return Panel(content, title="[bold cyan]Meshtasticd Service[/bold cyan]", border_style="cyan")

    def create_system_panel(self):
        """Create system information panel"""
        info = self.get_system_info()

        # Format temperature with color
        temp_color = 'green' if info['temp_status'] == 'normal' else ('yellow' if info['temp_status'] == 'warning' else 'red')

        content = Text()
        content.append("CPU Temp: ")
        content.append(info['cpu_temp'], style=temp_color)
        content.append(f"\nMemory:   {info['memory']}")
        content.append(f"\nDisk:     {info['disk']}")
        content.append(f"\nUptime:   {info['uptime']}")

        return Panel(content, title="[bold magenta]System Health[/bold magenta]", border_style="magenta")

    def create_network_panel(self):
        """Create network information panel"""
        info = self.get_network_info()

        internet_status = Text("● Connected", style="green") if info['internet'] else Text("● Offline", style="red")

        content = Text()
        content.append(f"IP Address: {info['ip']}\n")
        content.append("Internet:   ")
        content.append(internet_status)

        return Panel(content, title="[bold yellow]Network[/bold yellow]", border_style="yellow")

    def create_config_panel(self):
        """Create configuration status panel"""
        status = self.get_config_status()

        config_status = Text("● Found", style="green") if status['config_exists'] else Text("● Missing", style="red")

        content = Text()
        content.append("Config: ")
        content.append(config_status)
        content.append(f"\nPath: {status['config_path']}")
        if status['active_template']:
            content.append(f"\nTemplate: {status['active_template']}")

        return Panel(content, title="[bold blue]Configuration[/bold blue]", border_style="blue")

    def show_dashboard(self):
        """Display the complete status dashboard"""
        console.clear()
        console.print("\n[bold cyan]═══════════════════════════════════════════════════════════[/bold cyan]")
        console.print("[bold cyan]           MESHTASTICD QUICK STATUS DASHBOARD              [/bold cyan]")
        console.print("[bold cyan]═══════════════════════════════════════════════════════════[/bold cyan]\n")

        # Create panels
        service_panel = self.create_status_panel()
        system_panel = self.create_system_panel()
        network_panel = self.create_network_panel()
        config_panel = self.create_config_panel()

        # Display in two columns
        console.print(Columns([service_panel, system_panel], equal=True, expand=True))
        console.print()
        console.print(Columns([network_panel, config_panel], equal=True, expand=True))

        # Recent logs section
        console.print("\n[bold cyan]─── Recent Activity ───[/bold cyan]")
        logs = self.get_recent_logs(3)
        for log in logs:
            console.print(f"[dim]{log}[/dim]")

        # Quick actions
        console.print("\n[bold cyan]─── Quick Actions ───[/bold cyan]")
        console.print("[1] Refresh  [2] View Full Logs  [3] Restart Service  [4] Check Updates  [5] Back")

    def interactive_dashboard(self):
        """Interactive dashboard with auto-refresh option"""
        while True:
            self.show_dashboard()

            console.print()
            choice = Prompt.ask(
                "Select action",
                choices=["1", "2", "3", "4", "5"],
                default="5"
            )

            if choice == "1":
                continue  # Refresh
            elif choice == "2":
                self.show_full_logs()
            elif choice == "3":
                self.restart_service()
            elif choice == "4":
                self.check_updates()
            elif choice == "5":
                break

    def show_full_logs(self):
        """Show full logs"""
        console.print("\n[cyan]Recent meshtasticd logs (last 30 lines):[/cyan]\n")
        logs = self.get_recent_logs(30)
        for log in logs:
            console.print(f"[dim]{log}[/dim]")
        console.print()
        Prompt.ask("Press Enter to continue")

    def restart_service(self):
        """Restart meshtasticd service"""
        if Confirm.ask("\n[yellow]Restart meshtasticd service?[/yellow]", default=False):
            console.print("[cyan]Restarting service...[/cyan]")
            try:
                result = subprocess.run(
                    ['systemctl', 'restart', 'meshtasticd'],
                    capture_output=True, text=True, timeout=30
                )
                if result.returncode == 0:
                    console.print("[green]Service restarted successfully![/green]")
                else:
                    console.print(f"[red]Failed to restart: {result.stderr}[/red]")
            except Exception as e:
                console.print(f"[red]Error: {str(e)}[/red]")
            time.sleep(2)

    def check_updates(self):
        """Check for available updates"""
        console.print("\n[cyan]Checking for updates...[/cyan]")

        from installer.version import VersionManager
        vm = VersionManager()

        update_info = vm.check_for_updates()

        if update_info:
            if update_info.get('update_available'):
                console.print(f"\n[bold green]Update available![/bold green]")
                console.print(f"  Current: {update_info['current']}")
                console.print(f"  Latest:  {update_info['latest']}")
            else:
                console.print(f"\n[green]You're running the latest version ({update_info['current']})[/green]")
        else:
            console.print("[yellow]Could not check for updates[/yellow]")

        Prompt.ask("\nPress Enter to continue")

    def get_quick_status_line(self):
        """Get a single-line status summary for the main menu"""
        service = self.get_service_status()
        version = self.get_installed_version()
        info = self.get_system_info()

        if service['running']:
            status = "[green]●[/green] Running"
        else:
            status = "[red]●[/red] Stopped"

        return f"{status} | {version} | CPU: {info['cpu_temp']} | Mem: {info['memory']}"
