"""
MeshForge RNS Tools Module

Contains RNS/Reticulum management functions:
- rns_tools_menu: Service control, installation, configuration
- gateway_bridge_menu: Meshtastic ↔ RNS bridge control
- RNS config wizard and templates

Extracted from main.py for maintainability.
"""

import os
import subprocess
from pathlib import Path

from rich.console import Console
from rich.prompt import Prompt, Confirm
from rich.table import Table

from utils import emoji as em
from utils.paths import get_real_user_home

console = Console()


def rns_tools_menu():
    """RNS/Reticulum tools menu"""
    console.print("\n[bold cyan]═══════════ RNS Tools ═══════════[/bold cyan]\n")

    while True:
        # Check RNS installation status
        rns_installed = False
        try:
            import RNS
            rns_installed = True
            rns_version = getattr(RNS, '__version__', 'unknown')
        except ImportError:
            rns_version = "Not installed"

        # Check rnsd service status
        rnsd_status = "unknown"
        try:
            result = subprocess.run(['systemctl', 'is-active', 'rnsd'],
                                   capture_output=True, text=True, timeout=5)
            rnsd_status = result.stdout.strip()
        except Exception:
            pass

        console.print(f"[dim]RNS: {rns_version} | rnsd: {rnsd_status}[/dim]\n")

        console.print("[dim cyan]-- Service Control --[/dim cyan]")
        console.print(f"  [bold]1[/bold]. {em.get('▶️')}  Start rnsd")
        console.print(f"  [bold]2[/bold]. {em.get('⏹️')}  Stop rnsd")
        console.print(f"  [bold]3[/bold]. {em.get('🔄')} Restart rnsd")
        console.print(f"  [bold]4[/bold]. {em.get('📋')} View rnsd logs")

        console.print("\n[dim cyan]-- Installation --[/dim cyan]")
        console.print(f"  [bold]5[/bold]. {em.get('📦')} Install/Update RNS")
        console.print(f"  [bold]6[/bold]. {em.get('📦')} Install NomadNet")
        console.print(f"  [bold]7[/bold]. {em.get('📦')} Install LXMF")
        console.print(f"  [bold]i[/bold]. {em.get('🔌')} Install Meshtastic Interface")

        console.print("\n[dim cyan]-- Configuration --[/dim cyan]")
        console.print(f"  [bold]c[/bold]. {em.get('🛠️')} [green]Create/Setup RNS Config[/green] [dim](Templates)[/dim]")
        console.print(f"  [bold]8[/bold]. {em.get('📝')} Edit RNS config")
        console.print(f"  [bold]9[/bold]. {em.get('📊')} Show rnstatus")

        console.print("\n[dim cyan]-- Applications --[/dim cyan]")
        console.print(f"  [bold]n[/bold]. {em.get('🌐')} Launch NomadNet")

        console.print(f"\n  [bold]0[/bold]. {em.get('⬅️')}  Back to Main Menu")

        choice = Prompt.ask("\n[cyan]Select option[/cyan]",
                          choices=["0", "1", "2", "3", "4", "5", "6", "7", "8", "9", "c", "i", "n"],
                          default="0")

        if choice == "0":
            break
        elif choice == "1":
            _run_service_command('rnsd', 'start')
        elif choice == "2":
            _run_service_command('rnsd', 'stop')
        elif choice == "3":
            _run_service_command('rnsd', 'restart')
        elif choice == "4":
            console.print("\n[cyan]Recent rnsd logs:[/cyan]")
            try:
                result = subprocess.run(
                    ['journalctl', '-u', 'rnsd', '-n', '50', '--no-pager'],
                    capture_output=True, text=True, timeout=10
                )
                if result.stdout:
                    console.print(result.stdout)
                elif result.returncode != 0:
                    console.print("[yellow]No logs found or service not configured[/yellow]")
            except FileNotFoundError:
                console.print("[yellow]journalctl not available (non-systemd system?)[/yellow]")
                console.print("[dim]Try: cat /var/log/syslog | grep rnsd[/dim]")
            except subprocess.TimeoutExpired:
                console.print("[yellow]Timeout reading logs[/yellow]")
            input("\nPress Enter to continue...")
        elif choice == "5":
            console.print("\n[cyan]Installing RNS...[/cyan]")
            try:
                result = subprocess.run(
                    ['pip3', 'install', '--upgrade', '--break-system-packages', 'rns'],
                    timeout=120
                )
                if result.returncode == 0:
                    console.print("[green]RNS installed successfully![/green]")
                else:
                    console.print("[red]Installation may have failed. Check output above.[/red]")
            except subprocess.TimeoutExpired:
                console.print("[red]Installation timed out (network issue?)[/red]")
            except FileNotFoundError:
                console.print("[red]pip3 not found. Install python3-pip first.[/red]")
            input("\nPress Enter to continue...")
        elif choice == "6":
            console.print("\n[cyan]Installing NomadNet...[/cyan]")
            try:
                result = subprocess.run(
                    ['pip3', 'install', '--upgrade', '--break-system-packages', 'nomadnet'],
                    timeout=120
                )
                if result.returncode == 0:
                    console.print("[green]NomadNet installed successfully![/green]")
                else:
                    console.print("[red]Installation may have failed. Check output above.[/red]")
            except subprocess.TimeoutExpired:
                console.print("[red]Installation timed out (network issue?)[/red]")
            except FileNotFoundError:
                console.print("[red]pip3 not found. Install python3-pip first.[/red]")
            input("\nPress Enter to continue...")
        elif choice == "7":
            console.print("\n[cyan]Installing LXMF...[/cyan]")
            try:
                result = subprocess.run(
                    ['pip3', 'install', '--upgrade', '--break-system-packages', 'lxmf'],
                    timeout=120
                )
                if result.returncode == 0:
                    console.print("[green]LXMF installed successfully![/green]")
                else:
                    console.print("[red]Installation may have failed. Check output above.[/red]")
            except subprocess.TimeoutExpired:
                console.print("[red]Installation timed out (network issue?)[/red]")
            except FileNotFoundError:
                console.print("[red]pip3 not found. Install python3-pip first.[/red]")
            input("\nPress Enter to continue...")
        elif choice == "i":
            _install_meshtastic_interface()
        elif choice == "c":
            _create_rns_config_wizard()
        elif choice == "8":
            config_path = get_real_user_home() / '.reticulum' / 'config'
            if config_path.exists():
                try:
                    subprocess.run(['nano', str(config_path)], timeout=None)  # Interactive editor
                except FileNotFoundError:
                    # Fallback to other editors
                    for editor in ['vim', 'vi', 'less']:
                        try:
                            subprocess.run([editor, str(config_path)], timeout=None)  # Interactive
                            break
                        except FileNotFoundError:
                            continue
                    else:
                        console.print(f"[yellow]No editor found. View config at:[/yellow]")
                        console.print(f"  [cyan]{config_path}[/cyan]")
                        input("\nPress Enter to continue...")
            else:
                console.print(f"[yellow]Config not found at {config_path}[/yellow]")
                console.print("[dim]Run 'rnsd' once to create default config[/dim]")
                input("\nPress Enter to continue...")
        elif choice == "9":
            console.print("\n[cyan]RNS Status:[/cyan]")
            try:
                result = subprocess.run(['rnstatus'], capture_output=True, text=True, timeout=10)
                if result.stdout:
                    console.print(result.stdout)
                if result.stderr:
                    console.print(f"[yellow]{result.stderr}[/yellow]")
            except FileNotFoundError:
                console.print("[yellow]rnstatus not found. Install RNS first (option 5).[/yellow]")
            except subprocess.TimeoutExpired:
                console.print("[yellow]Timeout - rnsd may not be running[/yellow]")
            input("\nPress Enter to continue...")
        elif choice == "n":
            console.print("\n[cyan]Launching NomadNet...[/cyan]")
            console.print("[dim]Press Ctrl+C to exit NomadNet[/dim]\n")
            try:
                subprocess.run(['nomadnet'], timeout=None)
            except KeyboardInterrupt:
                pass
            except FileNotFoundError:
                console.print("[red]NomadNet not installed. Use option 6 to install.[/red]")
                input("\nPress Enter to continue...")


def gateway_bridge_menu():
    """Gateway bridge menu (Meshtastic ↔ RNS)"""
    console.print("\n[bold cyan]═══════════ Gateway Bridge ═══════════[/bold cyan]\n")

    while True:
        # Check gateway status using the bridge module
        gateway_running = False
        try:
            from gateway.rns_bridge import is_gateway_running
            gateway_running = is_gateway_running()
        except ImportError:
            # Fall back to process check if module not available
            try:
                result = subprocess.run(['pgrep', '-f', 'gateway.*bridge'],
                                       capture_output=True, text=True, timeout=5)
                gateway_running = result.returncode == 0
            except Exception:
                pass

        status = "[green]Running[/green]" if gateway_running else "[yellow]Stopped[/yellow]"
        console.print(f"[dim]Gateway Status: {status}[/dim]\n")

        console.print("[dim cyan]-- Bridge Control --[/dim cyan]")
        console.print(f"  [bold]1[/bold]. {em.get('▶️')}  Start Gateway Bridge")
        console.print(f"  [bold]2[/bold]. {em.get('⏹️')}  Stop Gateway Bridge")
        console.print(f"  [bold]3[/bold]. {em.get('📊')} View Bridge Stats")

        console.print("\n[dim cyan]-- Configuration --[/dim cyan]")
        console.print(f"  [bold]4[/bold]. {em.get('⚙️')}  Configure Gateway")
        console.print(f"  [bold]5[/bold]. {em.get('📋')} Apply Gateway Template")

        console.print("\n[dim cyan]-- Diagnostics --[/dim cyan]")
        console.print(f"  [bold]6[/bold]. {em.get('🔍')} Test Meshtastic Connection")
        console.print(f"  [bold]7[/bold]. {em.get('🔍')} Test RNS Connection")

        console.print(f"\n  [bold]0[/bold]. {em.get('⬅️')}  Back to Main Menu")

        choice = Prompt.ask("\n[cyan]Select option[/cyan]",
                          choices=["0", "1", "2", "3", "4", "5", "6", "7"],
                          default="0")

        if choice == "0":
            break
        elif choice == "1":
            console.print("\n[cyan]Starting Gateway Bridge...[/cyan]")
            try:
                from gateway.rns_bridge import start_gateway_headless
                start_gateway_headless()
            except ImportError as e:
                console.print(f"[red]Failed to import gateway: {e}[/red]")
                console.print("[dim]Make sure RNS is installed: pip3 install rns[/dim]")
            except Exception as e:
                console.print(f"[red]Error starting gateway: {e}[/red]")
            input("\nPress Enter to continue...")
        elif choice == "2":
            console.print("\n[cyan]Stopping Gateway Bridge...[/cyan]")
            subprocess.run(['pkill', '-f', 'gateway.*bridge'], timeout=10)
            console.print("[green]Gateway stopped[/green]")
            input("\nPress Enter to continue...")
        elif choice == "3":
            console.print("\n[cyan]Gateway Stats:[/cyan]")
            try:
                from gateway.rns_bridge import get_gateway_stats
                stats = get_gateway_stats()
                if stats:
                    console.print(f"  Messages Mesh→RNS: {stats.get('messages_mesh_to_rns', 0)}")
                    console.print(f"  Messages RNS→Mesh: {stats.get('messages_rns_to_mesh', 0)}")
                    console.print(f"  Errors: {stats.get('errors', 0)}")
                    console.print(f"  Bounced: {stats.get('bounced', 0)}")
                else:
                    console.print("[yellow]Gateway not running or no stats available[/yellow]")
            except Exception as e:
                console.print(f"[red]Error getting stats: {e}[/red]")
            input("\nPress Enter to continue...")
        elif choice == "4":
            console.print("\n[cyan]Gateway Configuration:[/cyan]")
            config_path = get_real_user_home() / '.config' / 'meshforge' / 'gateway.json'
            if config_path.exists():
                try:
                    subprocess.run(['nano', str(config_path)], timeout=None)  # Interactive editor
                except FileNotFoundError:
                    # Fallback to other editors
                    for editor in ['vim', 'vi', 'less']:
                        try:
                            subprocess.run([editor, str(config_path)], timeout=None)  # Interactive
                            break
                        except FileNotFoundError:
                            continue
                    else:
                        console.print(f"[yellow]No editor found. Config location:[/yellow]")
                        console.print(f"  [cyan]{config_path}[/cyan]")
                        input("\nPress Enter to continue...")
            else:
                console.print(f"[yellow]Config not found at {config_path}[/yellow]")
                console.print("[dim]Start the gateway once to create default config[/dim]")
                input("\nPress Enter to continue...")
        elif choice == "5":
            # Gateway template selection and application
            import shutil
            template_dir = Path(__file__).parent.parent / 'templates' / 'available.d'
            gateway_templates = sorted(template_dir.glob('gateway-*.yaml'))

            if not gateway_templates:
                console.print("[yellow]No gateway templates found[/yellow]")
                console.print("[dim]Gateway templates should be named 'gateway-*.yaml'[/dim]")
                input("\nPress Enter to continue...")
                continue

            console.print("\n[bold cyan]═══════════ Gateway Templates ═══════════[/bold cyan]\n")
            for i, t in enumerate(gateway_templates, 1):
                # Extract description from first comment lines
                try:
                    with open(t, 'r') as f:
                        first_lines = [l.strip('# \n') for l in f.readlines()[:3] if l.startswith('#')]
                        desc = first_lines[0] if first_lines else "No description"
                except Exception:
                    desc = "No description"
                console.print(f"  [bold]{i}[/bold]. {t.stem}")
                console.print(f"      [dim]{desc}[/dim]")

            console.print(f"\n  [bold]0[/bold]. Cancel")

            choices = ["0"] + [str(i) for i in range(1, len(gateway_templates) + 1)]
            selection = Prompt.ask("\n[cyan]Select template[/cyan]", choices=choices, default="0")

            if selection == "0":
                continue

            selected_template = gateway_templates[int(selection) - 1]
            dest_path = Path('/etc/meshtasticd/config.yaml')

            # Show preview
            console.print(f"\n[cyan]Template: {selected_template.name}[/cyan]")
            console.print("[dim]Preview (first 25 lines):[/dim]\n")
            try:
                with open(selected_template, 'r') as f:
                    lines = f.readlines()[:25]
                    for line in lines:
                        console.print(f"[dim]{line.rstrip()}[/dim]")
                    if len(lines) >= 25:
                        console.print("[dim]...[/dim]")
            except Exception as e:
                console.print(f"[red]Error reading template: {e}[/red]")
                input("\nPress Enter to continue...")
                continue

            if Confirm.ask(f"\nApply template to {dest_path}?", default=True):
                try:
                    # Backup existing config
                    if dest_path.exists():
                        backup_path = dest_path.with_suffix('.yaml.bak')
                        shutil.copy2(dest_path, backup_path)
                        console.print(f"[dim]Backed up to {backup_path}[/dim]")

                    shutil.copy2(selected_template, dest_path)
                    console.print("[green]Template applied successfully![/green]")

                    if Confirm.ask("\nRestart meshtasticd service?", default=True):
                        result = subprocess.run(
                            ['systemctl', 'restart', 'meshtasticd'],
                            capture_output=True, text=True, timeout=30
                        )
                        if result.returncode == 0:
                            console.print("[green]Service restarted![/green]")
                        else:
                            console.print(f"[red]Restart failed: {result.stderr}[/red]")

                    # Gateway-specific next steps
                    console.print("\n[bold cyan]═══════════ Next Steps ═══════════[/bold cyan]\n")
                    console.print("[yellow]Complete gateway setup:[/yellow]\n")
                    console.print("  1. Configure RNS identity (option 3 in RNS Tools)")
                    console.print("  2. Start the gateway bridge (option 1)")
                    console.print("  3. Verify connectivity (options 6 & 7)")
                    console.print("\n[dim]Gateway config: ~/.config/meshforge/gateway.json[/dim]")

                except PermissionError:
                    console.print("[red]Permission denied. Run with sudo.[/red]")
                except Exception as e:
                    console.print(f"[red]Failed to apply template: {e}[/red]")

            input("\nPress Enter to continue...")
        elif choice == "6":
            console.print("\n[cyan]Testing Meshtastic Connection...[/cyan]")
            try:
                result = subprocess.run(
                    ['meshtastic', '--host', 'localhost', '--info'],
                    capture_output=True, text=True, timeout=15
                )
                if result.returncode == 0:
                    console.print("[green]✓ Meshtastic connection OK[/green]")
                    # Show first few lines
                    lines = result.stdout.split('\n')[:10]
                    for line in lines:
                        console.print(f"  [dim]{line}[/dim]")
                else:
                    console.print(f"[red]✗ Connection failed: {result.stderr}[/red]")
            except Exception as e:
                console.print(f"[red]✗ Error: {e}[/red]")
            input("\nPress Enter to continue...")
        elif choice == "7":
            console.print("\n[cyan]Testing RNS Connection...[/cyan]")
            try:
                import RNS
                console.print(f"[green]✓ RNS library loaded (v{RNS.__version__})[/green]")

                # Try to connect to shared instance
                import socket
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(2)
                try:
                    sock.connect(('localhost', 37428))  # Default RNS shared instance port
                    console.print("[green]✓ RNS shared instance reachable[/green]")
                except Exception:
                    console.print("[yellow]⚠ RNS shared instance not reachable on port 37428[/yellow]")
                    console.print("[dim]  Start rnsd or check if it's using a different port[/dim]")
                finally:
                    sock.close()
            except ImportError:
                console.print("[red]✗ RNS library not installed[/red]")
                console.print("[dim]  Install with: pip3 install rns[/dim]")
            except Exception as e:
                console.print(f"[red]✗ Error: {e}[/red]")
            input("\nPress Enter to continue...")


def _run_service_command(service: str, action: str):
    """Run a systemctl command on a service"""
    console.print(f"\n[cyan]{action.capitalize()}ing {service}...[/cyan]")
    try:
        result = subprocess.run(
            ['systemctl', action, service],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode == 0:
            console.print(f"[green]✓ {service} {action}ed successfully[/green]")
        else:
            console.print(f"[red]✗ Failed: {result.stderr}[/red]")
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
    input("\nPress Enter to continue...")


def _install_meshtastic_interface():
    """Download and install Meshtastic_Interface.py for RNS"""
    import requests

    console.print("\n[bold cyan]Install Meshtastic Interface[/bold cyan]")
    console.print("[dim]RNS interface for Meshtastic LoRa transport[/dim]")
    console.print("[dim]Source: github.com/Nursedude/RNS_Over_Meshtastic_Gateway[/dim]\n")

    # Use get_real_user_home for sudo compatibility
    real_home = get_real_user_home()
    interfaces_dir = real_home / '.reticulum' / 'interfaces'
    interface_file = interfaces_dir / 'Meshtastic_Interface.py'

    # Check if already installed
    if interface_file.exists():
        console.print(f"[yellow]Meshtastic_Interface.py already exists at:[/yellow]")
        console.print(f"  {interface_file}")
        if not Confirm.ask("\n[cyan]Overwrite with latest version?[/cyan]", default=False):
            return

    # Create interfaces directory if needed
    try:
        interfaces_dir.mkdir(parents=True, exist_ok=True)
        console.print(f"[green]✓[/green] Interfaces directory: {interfaces_dir}")
    except PermissionError:
        console.print(f"[red]✗ Cannot create {interfaces_dir} - permission denied[/red]")
        console.print("[dim]Try running without sudo, or check directory permissions[/dim]")
        input("\nPress Enter to continue...")
        return

    # Download from Nursedude's fork
    url = "https://raw.githubusercontent.com/Nursedude/RNS_Over_Meshtastic_Gateway/main/Meshtastic_Interface.py"
    console.print(f"\n[cyan]Downloading from {url}...[/cyan]")

    try:
        response = requests.get(url, timeout=30)
        response.raise_for_status()

        interface_file.write_text(response.text)
        console.print(f"[green]✓ Installed: {interface_file}[/green]")

        # Show config snippet
        console.print("\n[bold]Add to ~/.reticulum/config:[/bold]")
        console.print("""
[dim][[Meshtastic LoRa]]
  type = Meshtastic_Interface
  enabled = true
  # Connection method (choose one):
  # port = /dev/ttyUSB0     # Serial
  # ble_addr = AA:BB:CC:DD  # Bluetooth
  # tcp_addr = 127.0.0.1    # TCP (meshtasticd)
  tcp_addr = 127.0.0.1
  tcp_port = 4403
  data_speed = 8            # 0-8, higher = faster (Short Turbo = 8)[/dim]
""")
        console.print("[dim]Note: data_speed 8 requires SHORT_TURBO modem preset[/dim]")

    except requests.exceptions.RequestException as e:
        console.print(f"[red]✗ Download failed: {e}[/red]")
    except PermissionError:
        console.print(f"[red]✗ Cannot write to {interface_file} - permission denied[/red]")
    except Exception as e:
        console.print(f"[red]✗ Error: {e}[/red]")

    input("\nPress Enter to continue...")


def _create_rns_config_wizard():
    """Create or reconfigure ~/.reticulum/config with templates"""
    console.print("\n[bold cyan]═══════════ RNS Configuration Wizard ═══════════[/bold cyan]")
    console.print("[dim]Configure Reticulum Network Stack[/dim]\n")

    real_home = get_real_user_home()
    config_dir = real_home / '.reticulum'
    config_file = config_dir / 'config'

    # Check if config exists
    if config_file.exists():
        console.print(f"[yellow]Existing config found: {config_file}[/yellow]")
        if not Confirm.ask("Overwrite with new configuration?", default=False):
            console.print("[dim]Keeping existing config[/dim]")
            input("\nPress Enter to continue...")
            return

    # Template selection
    console.print("\n[bold]Step 1: Select Configuration Template[/bold]\n")

    templates = {
        '1': ('Local Only', 'AutoInterface for LAN discovery only'),
        '2': ('Gateway Node', 'Transport + AutoInterface + TCP Server (host)'),
        '3': ('Client + Testnet', 'AutoInterface + RNS Testnet connections'),
        '4': ('Connect to Server', 'AutoInterface + TCPClientInterface (join network)'),
        '5': ('Meshtastic Bridge', 'AutoInterface + Meshtastic_Interface'),
        '6': ('Full Gateway', 'Transport + TCP Server + Meshtastic + Client'),
        '7': ('Gateway Client', 'TCPClient + Meshtastic (NO AutoInterface) - MOC2 style'),
    }

    for key, (name, desc) in templates.items():
        console.print(f"  [bold]{key}[/bold]. {name} - [dim]{desc}[/dim]")

    template_choice = Prompt.ask("\n[cyan]Select template[/cyan]",
                                 choices=list(templates.keys()), default="1")

    # Build configuration based on template
    config = _build_rns_config(template_choice)

    # Additional options
    console.print("\n[bold]Step 2: Additional Options[/bold]\n")

    # Instance name (for multiple RNS instances)
    set_instance = Confirm.ask("Set custom instance name? (for multiple RNS instances)", default=False)
    if set_instance:
        import socket
        default_name = socket.gethostname() + " RNS"
        instance_name = Prompt.ask("  Instance name", default=default_name)
        config = config.replace('# instance_name = default', f'instance_name = {instance_name}')

    # Transport node
    if template_choice in ['2', '6']:
        console.print("[green]✓ Transport enabled (routing for other nodes)[/green]")
    else:
        enable_transport = Confirm.ask("Enable transport (route traffic for others)?", default=False)
        if enable_transport:
            config = config.replace('enable_transport = False', 'enable_transport = True')

    # TCP Server port (for hosting)
    if template_choice in ['2', '6']:
        tcp_port = Prompt.ask("TCP Server port (for incoming connections)", default="4242")
        config = config.replace('listen_port = 4242', f'listen_port = {tcp_port}')

    # TCP Client (connect to remote server)
    if template_choice in ['4', '6', '7']:
        console.print("\n[bold]TCP Client Connection (connect to remote RNS node):[/bold]")
        target_host = Prompt.ask("  Target host/IP", default="192.168.1.1")
        target_port = Prompt.ask("  Target port", default="4242")
        conn_name = Prompt.ask("  Connection name", default="Remote RNS")
        config = config.replace('target_host = remote.example.com', f'target_host = {target_host}')
        config = config.replace('target_port = 4242', f'target_port = {target_port}')
        config = config.replace('name = Remote Server', f'name = {conn_name}')

    # RNode LoRa interface (direct RNode, not Meshtastic)
    add_rnode = Confirm.ask("\nAdd RNode LoRa interface? (direct RNode hardware)", default=False)
    if add_rnode:
        config = _add_rnode_interface(config)

    # Meshtastic interface
    if template_choice in ['5', '6', '7']:
        console.print("\n[bold]Meshtastic Interface Config:[/bold]")
        console.print("  [dim]Connection method:[/dim]")
        console.print("    1. TCP (meshtasticd on localhost:4403)")
        console.print("    2. Serial (/dev/ttyUSB0)")
        console.print("    3. BLE (Bluetooth)")

        mesh_conn = Prompt.ask("  Connection type", choices=["1", "2", "3"], default="1")

        if mesh_conn == "1":
            config = config.replace('# tcp_port = 127.0.0.1:4403', 'tcp_port = 127.0.0.1:4403')
            config = config.replace('port = /dev/ttyUSB0', '# port = /dev/ttyUSB0')
        elif mesh_conn == "2":
            port = Prompt.ask("  Serial port", default="/dev/ttyUSB0")
            config = config.replace('port = /dev/ttyUSB0', f'port = {port}')
        else:
            ble_addr = Prompt.ask("  BLE device name/address", default="RNode_1234")
            config = config.replace('port = /dev/ttyUSB0', f'# port = /dev/ttyUSB0')
            config = config.replace('# ble_port = RNode_1234', f'ble_port = {ble_addr}')

        data_speed = Prompt.ask("  Data speed (0=LongFast, 8=Turbo)", default="8")
        config = config.replace('data_speed = 8', f'data_speed = {data_speed}')

    # Display preview
    console.print("\n[bold]Configuration Preview:[/bold]")
    console.print("[dim]" + "─" * 50 + "[/dim]")
    # Show first 30 lines
    preview_lines = config.split('\n')[:35]
    for line in preview_lines:
        if line.startswith('#'):
            console.print(f"[dim]{line}[/dim]")
        elif line.startswith('['):
            console.print(f"[cyan]{line}[/cyan]")
        elif '=' in line and not line.strip().startswith('#'):
            console.print(f"[green]{line}[/green]")
        else:
            console.print(line)
    console.print("[dim]... (truncated)[/dim]")
    console.print("[dim]" + "─" * 50 + "[/dim]")

    # Save config
    if Confirm.ask("\n[cyan]Save this configuration?[/cyan]", default=True):
        try:
            config_dir.mkdir(parents=True, exist_ok=True)
            config_file.write_text(config)
            console.print(f"\n[green]✓ Config saved to {config_file}[/green]")

            # Offer to create rnsd service
            if not Path('/etc/systemd/system/rnsd.service').exists():
                if Confirm.ask("\nCreate rnsd systemd service?", default=True):
                    _create_rnsd_service()

            console.print("\n[bold yellow]Next steps:[/bold yellow]")
            console.print("  1. Start rnsd: [cyan]sudo systemctl start rnsd[/cyan]")
            console.print("  2. Check status: [cyan]rnstatus[/cyan]")
            console.print("  3. View logs: [cyan]journalctl -u rnsd -f[/cyan]")

        except PermissionError:
            console.print(f"[red]✗ Cannot write to {config_file} - permission denied[/red]")
        except Exception as e:
            console.print(f"[red]✗ Error: {e}[/red]")
    else:
        console.print("[yellow]Configuration cancelled[/yellow]")

    input("\nPress Enter to continue...")


def _build_rns_config(template: str) -> str:
    """Build RNS config based on template choice"""

    # Base config
    base = '''# Reticulum Network Stack Configuration
# Generated by MeshForge
# Reference: https://reticulum.network/manual/interfaces.html

[reticulum]
enable_transport = False
share_instance = Yes
shared_instance_port = 37428
# instance_name = default
panic_on_interface_error = No

[logging]
loglevel = 4

[interfaces]
'''

    # AutoInterface (all templates)
    auto_interface = '''
# Local network discovery
[[Default Interface]]
    type = AutoInterface
    enabled = Yes
'''

    # TCP Server (for gateway templates)
    tcp_server = '''
# Accept incoming RNS connections
[[TCP Server]]
    type = TCPServerInterface
    enabled = Yes
    listen_ip = 0.0.0.0
    listen_port = 4242
'''

    # Testnet connections
    testnet = '''
# RNS Testnet - Dublin
[[RNS Testnet Dublin]]
    type = TCPClientInterface
    enabled = Yes
    target_host = dublin.connect.reticulum.network
    target_port = 4965

# RNS Testnet - BetweenTheBorders
[[RNS Testnet BTB]]
    type = TCPClientInterface
    enabled = Yes
    target_host = reticulum.betweentheborders.com
    target_port = 4242
'''

    # Meshtastic interface
    meshtastic = '''
# Meshtastic LoRa Bridge
# Requires: Meshtastic_Interface.py in ~/.reticulum/interfaces/
[[Meshtastic LoRa]]
    type = Meshtastic_Interface
    enabled = Yes
    mode = gateway
    port = /dev/ttyUSB0
    # tcp_port = 127.0.0.1:4403
    # ble_port = RNode_1234
    data_speed = 8
    hop_limit = 3
'''

    # TCP Client (connect to remote RNS server)
    tcp_client = '''
# Connect to remote RNS server
[[Remote Server]]
    type = TCPClientInterface
    enabled = Yes
    target_host = remote.example.com
    target_port = 4242
    name = Remote Server
'''

    # Build based on template
    if template == '1':  # Local Only
        return base + auto_interface

    elif template == '2':  # Gateway Node (host)
        config = base.replace('enable_transport = False', 'enable_transport = True')
        return config + auto_interface + tcp_server

    elif template == '3':  # Client + Testnet
        return base + auto_interface + testnet

    elif template == '4':  # Connect to Server (TCPClientInterface)
        return base + auto_interface + tcp_client

    elif template == '5':  # Meshtastic Bridge
        return base + auto_interface + meshtastic

    elif template == '6':  # Full Gateway (everything)
        config = base.replace('enable_transport = False', 'enable_transport = True')
        return config + auto_interface + tcp_server + tcp_client + meshtastic

    elif template == '7':  # Gateway Client (MOC2 style - NO AutoInterface)
        return base + tcp_client + meshtastic

    return base + auto_interface


def _create_rnsd_service():
    """Create rnsd systemd service"""
    import pwd

    # Get the real user (not root if running with sudo)
    real_user = os.environ.get('SUDO_USER', os.environ.get('USER', 'pi'))

    service_content = f'''[Unit]
Description=Reticulum Network Stack Daemon
After=network.target

[Service]
Type=simple
User={real_user}
ExecStart=/usr/local/bin/rnsd
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
'''

    try:
        service_path = Path('/etc/systemd/system/rnsd.service')
        service_path.write_text(service_content)
        subprocess.run(['systemctl', 'daemon-reload'], timeout=10)
        subprocess.run(['systemctl', 'enable', 'rnsd'], timeout=10)
        console.print("[green]✓ rnsd service created and enabled[/green]")
    except PermissionError:
        console.print("[red]✗ Need sudo to create systemd service[/red]")
        console.print(f"[dim]Run: sudo tee /etc/systemd/system/rnsd.service << 'EOF'\n{service_content}EOF[/dim]")
    except Exception as e:
        console.print(f"[red]✗ Error creating service: {e}[/red]")


def _add_rnode_interface(config: str) -> str:
    """Add RNode LoRa interface configuration interactively"""
    console.print("\n[bold]RNode LoRa Interface Config:[/bold]")
    console.print("[dim]Direct RNode hardware (not Meshtastic bridge)[/dim]\n")

    # Interface name
    import socket
    default_name = socket.gethostname() + " rnode"
    iface_name = Prompt.ask("  Interface name", default=default_name)

    # Serial port
    console.print("  [dim]Common ports: /dev/ttyACM0, /dev/ttyUSB0[/dim]")
    port = Prompt.ask("  Serial port", default="/dev/ttyACM0")

    # Frequency (US 900 MHz band)
    console.print("  [dim]US frequencies: 902-928 MHz (e.g., 903625000 = 903.625 MHz)[/dim]")
    frequency = Prompt.ask("  Frequency (Hz)", default="903625000")

    # TX Power
    console.print("  [dim]TX power: typically 2-22 dBm depending on hardware[/dim]")
    txpower = Prompt.ask("  TX Power (dBm)", default="22")

    # Bandwidth
    bandwidths = {'1': '125000', '2': '250000', '3': '500000'}
    console.print("  Bandwidth: 1=125kHz, 2=250kHz, 3=500kHz")
    bw_choice = Prompt.ask("  Select bandwidth", choices=["1", "2", "3"], default="2")
    bandwidth = bandwidths[bw_choice]

    # Spreading factor
    console.print("  [dim]Spreading factor: 7-12 (lower=faster, higher=longer range)[/dim]")
    sf = Prompt.ask("  Spreading factor", default="7")

    # Coding rate
    console.print("  [dim]Coding rate: 5-8 (5=4/5, 6=4/6, 7=4/7, 8=4/8)[/dim]")
    cr = Prompt.ask("  Coding rate", default="5")

    rnode_config = f'''
# RNode LoRa Interface
[[{iface_name}]]
    type = RNodeInterface
    interface_enabled = True
    port = {port}
    frequency = {frequency}
    txpower = {txpower}
    bandwidth = {bandwidth}
    spreadingfactor = {sf}
    codingrate = {cr}
'''

    console.print(f"\n[green]✓ RNode interface '{iface_name}' configured[/green]")
    return config + rnode_config
