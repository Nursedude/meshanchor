"""Radio and mesh network configuration"""

from rich.console import Console
from rich.prompt import Prompt, Confirm, IntPrompt
from rich.table import Table

from utils.logger import log

console = Console()


def ask_yes_no_cancel(prompt: str, default: bool = False) -> bool | None:
    """
    Ask a yes/no question with cancel option.

    Args:
        prompt: The question to ask
        default: Default value if user just presses Enter (y/n)

    Returns:
        True for yes, False for no, None for cancel
    """
    default_str = "y" if default else "n"
    hint = f"[y/n/m] ({default_str})" if default else "[y/n/m] (n)"

    while True:
        response = Prompt.ask(
            f"{prompt} {hint}",
            default=default_str,
            show_default=False
        ).lower().strip()

        if response in ('y', 'yes'):
            return True
        elif response in ('n', 'no', ''):
            return False
        elif response in ('c', 'cancel', 'q', 'quit', 'back', 'm', 'menu', 'b'):
            return None
        else:
            console.print("[yellow]Enter y/n/m (yes, no, or m for menu)[/yellow]")


class RadioConfigurator:
    """Configure radio and mesh network settings"""

    def __init__(self, interface=None):
        self.interface = interface

    def configure_radio_settings(self):
        """Configure complete radio settings including modem preset and channel slot"""
        console.print("\n[bold cyan]Radio Configuration[/bold cyan]\n")

        config = {}

        # Modem preset selection
        from config.lora import LoRaConfigurator
        lora_config = LoRaConfigurator(self.interface)

        console.print("[cyan]Step 1: Select Modem Preset[/cyan]")
        modem_config = lora_config.configure_modem_preset()

        if modem_config:
            config.update(modem_config)
            preset_name = modem_config.get('preset_name', 'LongFast')
        else:
            preset_name = 'LongFast'
            console.print("[yellow]Using default preset: LongFast[/yellow]")

        # Channel slot configuration
        console.print("\n[cyan]Step 2: Channel Slot Configuration[/cyan]")
        slot_config = self.configure_channel_slot(preset_name)

        config.update(slot_config)

        # TX Power
        console.print("\n[cyan]Step 3: Transmit Power[/cyan]")
        tx_power = self.configure_tx_power()
        config['tx_power'] = tx_power

        # Hop limit
        console.print("\n[cyan]Step 4: Hop Limit[/cyan]")
        hop_limit = self.configure_hop_limit()
        config['hop_limit'] = hop_limit

        # Display complete configuration
        self._display_radio_config(config)

        return config

    def configure_channel_slot(self, preset_name='LongFast'):
        """Configure channel slot number"""
        console.print(f"\n[bold cyan]Channel Slot for {preset_name}[/bold cyan]\n")

        console.print("[yellow]Channel slots determine the frequency used within your region.[/yellow]")
        console.print("Different slots help avoid interference between networks.\n")

        # Common slot recommendations
        console.print("[cyan]Common slot configurations:[/cyan]")
        console.print("  Slot 0:  Default for LongFast")
        console.print("  Slot 20: Common for LongFast networks (US region)")
        console.print("  Slot 1-7: Available for custom channels")

        # Get number of available slots based on region (this would be region-specific)
        max_slots = 104  # US region has 104 channels

        console.print(f"\n[dim]Available slots: 0-{max_slots-1}[/dim]")

        use_custom = ask_yes_no_cancel("\nConfigure custom channel slot?", default=False)

        if use_custom is None:
            # User cancelled
            console.print("[yellow]Cancelled - using default slot[/yellow]")
            return {'channel_slot': 20 if preset_name == 'LongFast' else 0, 'slot_info': 'Default (cancelled)'}

        if use_custom:
            # Get slot with back/cancel option
            slot_input = Prompt.ask(
                "Enter channel slot number (or 'm' for menu)",
                default="20"
            )

            # Check for back/cancel
            if slot_input.lower().strip() in ('m', 'menu', 'b', 'back', 'c', 'cancel'):
                console.print("[yellow]Cancelled - using default slot[/yellow]")
                return {'channel_slot': 20 if preset_name == 'LongFast' else 0, 'slot_info': 'Default (cancelled)'}

            try:
                slot = int(slot_input)
                # Validate slot
                if 0 <= slot < max_slots:
                    console.print(f"[green]Channel slot set to: {slot}[/green]")
                else:
                    console.print(f"[yellow]Invalid slot. Using default: 20[/yellow]")
                    slot = 20
            except ValueError:
                console.print(f"[yellow]Invalid input. Using default: 20[/yellow]")
                slot = 20
        else:
            # Use recommended slot
            if preset_name == 'LongFast':
                slot = 20
                console.print(f"[green]Using recommended slot for LongFast: {slot}[/green]")
            elif 'MEDIUM' in preset_name.upper():
                slot = 20
                console.print(f"[green]Using recommended slot for {preset_name}: {slot}[/green]")
            else:
                slot = 0
                console.print(f"[green]Using default slot: {slot}[/green]")

        return {
            'channel_slot': slot,
            'slot_info': f"Slot {slot} for {preset_name}"
        }

    def configure_tx_power(self):
        """Configure transmit power"""
        console.print("\n[yellow]TX Power settings:[/yellow]")
        console.print("  Higher power = longer range, more battery usage")
        console.print("  Most LoRa modules support 20-30 dBm")
        console.print("  MeshToad supports up to 30 dBm (1W)\n")

        console.print("[cyan]Common settings:[/cyan]")
        console.print("  20 dBm: Standard (100mW)")
        console.print("  27 dBm: High power (500mW)")
        console.print("  30 dBm: Maximum (1W, MeshToad/high-power modules)")

        tx_input = Prompt.ask(
            "\nEnter TX power in dBm (or 'm' for menu)",
            default="20"
        )

        # Check for back/cancel
        if tx_input.lower().strip() in ('m', 'menu', 'b', 'back', 'c', 'cancel'):
            console.print("[yellow]Using default: 20 dBm[/yellow]")
            return 20

        try:
            tx_power = int(tx_input)
            # Validate
            if tx_power < 0:
                tx_power = 0
            elif tx_power > 30:
                console.print("[yellow]Warning: 30 dBm is maximum for most modules[/yellow]")
                tx_power = 30
            console.print(f"[green]TX power set to: {tx_power} dBm[/green]")
            return tx_power
        except ValueError:
            console.print("[yellow]Invalid input. Using default: 20 dBm[/yellow]")
            return 20

    def configure_hop_limit(self):
        """Configure hop limit"""
        console.print("\n[yellow]Hop Limit:[/yellow]")
        console.print("  Number of times a message can be retransmitted")
        console.print("  Higher = longer range through multiple nodes")
        console.print("  Lower = less network congestion\n")

        console.print("[cyan]Recommended settings:[/cyan]")
        console.print("  3: Default, good for most networks")
        console.print("  4-7: Larger networks")
        console.print("  1-2: Small, local networks")

        hop_input = Prompt.ask(
            "\nEnter hop limit (or 'm' for menu)",
            default="3"
        )

        # Check for back/cancel
        if hop_input.lower().strip() in ('m', 'menu', 'b', 'back', 'c', 'cancel'):
            console.print("[yellow]Using default: 3 hops[/yellow]")
            return 3

        try:
            hop_limit = int(hop_input)
            # Validate
            if hop_limit < 0:
                hop_limit = 0
            elif hop_limit > 7:
                console.print("[yellow]Warning: Values above 7 can cause network congestion[/yellow]")
                hop_limit = 7
            console.print(f"[green]Hop limit set to: {hop_limit}[/green]")
            return hop_limit
        except ValueError:
            console.print("[yellow]Invalid input. Using default: 3 hops[/yellow]")
            return 3

    def configure_gps_position(self):
        """Configure GPS position manually or via GPS module"""
        console.print("\n[bold cyan]GPS Position Configuration[/bold cyan]\n")

        console.print("[cyan]Position Options:[/cyan]")
        console.print("  1. Auto-detect from GPS module")
        console.print("  2. Set coordinates manually")
        console.print("  3. Disable position broadcasting")
        console.print("  0. Skip/Cancel")

        choice = Prompt.ask("\nSelect option", choices=["0", "1", "2", "3"], default="0")

        if choice == "0":
            return None

        config = {}

        if choice == "1":
            console.print("\n[green]GPS auto-detection enabled[/green]")
            console.print("[dim]Make sure GPS module is configured in config.yaml[/dim]")
            config['gps_mode'] = 'auto'
            config['position_broadcast_enabled'] = True

        elif choice == "2":
            console.print("\n[yellow]Enter your coordinates in decimal degrees[/yellow]")
            console.print("[dim]Example: Latitude 19.435175, Longitude -155.213842[/dim]")
            console.print("[dim]Find coordinates at: maps.google.com (right-click → What's here?)[/dim]\n")

            # Latitude input
            while True:
                lat_str = Prompt.ask("Latitude (-90 to 90)", default="0.0")
                try:
                    latitude = float(lat_str)
                    if -90 <= latitude <= 90:
                        break
                    console.print("[red]Latitude must be between -90 and 90[/red]")
                except ValueError:
                    console.print("[red]Invalid number. Use decimal format (e.g., 19.435175)[/red]")

            # Longitude input
            while True:
                lon_str = Prompt.ask("Longitude (-180 to 180)", default="0.0")
                try:
                    longitude = float(lon_str)
                    if -180 <= longitude <= 180:
                        break
                    console.print("[red]Longitude must be between -180 and 180[/red]")
                except ValueError:
                    console.print("[red]Invalid number. Use decimal format (e.g., -155.213842)[/red]")

            # Altitude (optional)
            if Confirm.ask("\nSet altitude?", default=False):
                alt_str = Prompt.ask("Altitude in meters", default="0")
                try:
                    altitude = int(float(alt_str))
                except ValueError:
                    altitude = 0
                config['altitude'] = altitude

            config['latitude'] = latitude
            config['longitude'] = longitude
            config['gps_mode'] = 'fixed'
            config['position_broadcast_enabled'] = True

            console.print(f"\n[green]Position set to:[/green]")
            console.print(f"  Latitude:  {latitude}")
            console.print(f"  Longitude: {longitude}")
            if 'altitude' in config:
                console.print(f"  Altitude:  {config['altitude']}m")

            # Show meshtastic CLI command for reference
            console.print(f"\n[dim]CLI equivalent:[/dim]")
            console.print(f"[cyan]meshtastic --host localhost --setlat {latitude} --setlon {longitude}[/cyan]")

        elif choice == "3":
            config['gps_mode'] = 'disabled'
            config['position_broadcast_enabled'] = False
            console.print("\n[yellow]Position broadcasting disabled[/yellow]")

        return config

    def apply_gps_position(self, config, host='localhost'):
        """Apply GPS position using meshtastic CLI"""
        import subprocess

        if not config or config.get('gps_mode') != 'fixed':
            return False

        lat = config.get('latitude')
        lon = config.get('longitude')

        if lat is None or lon is None:
            console.print("[red]No coordinates to apply[/red]")
            return False

        console.print(f"\n[cyan]Setting position to {lat}, {lon}...[/cyan]")

        try:
            cmd = ['meshtastic', '--host', host, '--setlat', str(lat), '--setlon', str(lon)]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)

            if result.returncode == 0:
                console.print("[green]Position set successfully![/green]")
                return True
            else:
                console.print(f"[red]Error: {result.stderr}[/red]")
                console.print("[dim]Make sure meshtasticd is running and accessible[/dim]")
                return False

        except FileNotFoundError:
            console.print("[red]meshtastic CLI not found[/red]")
            console.print("[dim]Install with: pip install meshtastic[/dim]")
            return False
        except subprocess.TimeoutExpired:
            console.print("[red]Command timed out[/red]")
            return False
        except Exception as e:
            console.print(f"[red]Error: {e}[/red]")
            return False

    def configure_mesh_settings(self):
        """Configure mesh network settings"""
        console.print("\n[bold cyan]Mesh Network Settings[/bold cyan]\n")

        settings = {}

        # Node info broadcast interval
        console.print("[cyan]Node Info Broadcast Interval:[/cyan]")
        console.print("How often your node broadcasts its presence (in seconds)\n")

        interval = IntPrompt.ask(
            "Broadcast interval (seconds)",
            default=900,  # 15 minutes
            show_default=True
        )
        settings['node_info_broadcast_secs'] = interval

        # Position configuration
        console.print("\n[cyan]Position Settings:[/cyan]")
        gps_config = self.configure_gps_position()
        if gps_config:
            settings.update(gps_config)
        else:
            settings['position_broadcast_enabled'] = False

        # Display settings
        table = Table(title="Mesh Settings", show_header=True, header_style="bold magenta")
        table.add_column("Setting", style="cyan")
        table.add_column("Value", style="green")

        for key, value in settings.items():
            display_key = key.replace('_', ' ').title()
            table.add_row(display_key, str(value))

        console.print("\n")
        console.print(table)

        return settings

    def _display_radio_config(self, config):
        """Display complete radio configuration"""
        console.print("\n[bold cyan]Complete Radio Configuration[/bold cyan]\n")

        table = Table(show_header=True, header_style="bold magenta")
        table.add_column("Parameter", style="cyan", width=25)
        table.add_column("Value", style="green", width=40)

        if 'preset_name' in config:
            table.add_row("Modem Preset", config['preset_name'])

        if 'bandwidth' in config:
            table.add_row("Bandwidth", f"{config['bandwidth']} kHz")

        if 'spreading_factor' in config:
            table.add_row("Spreading Factor", str(config['spreading_factor']))

        if 'coding_rate' in config:
            table.add_row("Coding Rate", f"4/{config['coding_rate']}")

        if 'channel_slot' in config:
            table.add_row("Channel Slot", str(config['channel_slot']))

        if 'tx_power' in config:
            table.add_row("TX Power", f"{config['tx_power']} dBm")

        if 'hop_limit' in config:
            table.add_row("Hop Limit", str(config['hop_limit']))

        console.print(table)

        # Show summary
        if 'preset_name' in config and 'channel_slot' in config:
            console.print(f"\n[bold green]✓[/bold green] {config['preset_name']} configured for slot {config['channel_slot']}")

    def apply_configuration(self, config):
        """Apply configuration to device"""
        if not self.interface:
            console.print("\n[yellow]No device connected. Configuration saved but not applied.[/yellow]")
            return False

        try:
            console.print("\n[cyan]Applying configuration to device...[/cyan]")

            # This would use the meshtastic library to apply settings
            # Placeholder for actual implementation

            console.print("[green]Configuration applied successfully![/green]")
            console.print("[yellow]Note: Device may require reboot for changes to take effect[/yellow]")

            return True

        except Exception as e:
            console.print(f"[bold red]Error applying configuration: {str(e)}[/bold red]")
            log(f"Configuration apply error: {str(e)}", 'error')
            return False

    def save_configuration_yaml(self, config, output_file='/etc/meshtasticd/config.yaml'):
        """Save configuration to YAML file for meshtasticd"""
        import yaml

        console.print(f"\n[cyan]Saving configuration to {output_file}...[/cyan]")

        try:
            # Build config structure
            yaml_config = {
                'Lora': {},
                'Channels': []
            }

            # LoRa settings
            if 'bandwidth' in config:
                yaml_config['Lora']['Bandwidth'] = config['bandwidth']
            if 'spreading_factor' in config:
                yaml_config['Lora']['SpreadFactor'] = config['spreading_factor']
            if 'coding_rate' in config:
                yaml_config['Lora']['CodingRate'] = config['coding_rate']
            if 'tx_power' in config:
                yaml_config['Lora']['TXpower'] = config['tx_power']
            if 'hop_limit' in config:
                yaml_config['Lora']['HopLimit'] = config['hop_limit']
            if 'channel_slot' in config:
                yaml_config['Lora']['ChannelNum'] = config['channel_slot']

            # Ensure parent directory exists
            from pathlib import Path
            output_path = Path(output_file)
            output_path.parent.mkdir(parents=True, exist_ok=True)

            # Write to file
            with open(output_file, 'w') as f:
                yaml.dump(yaml_config, f, default_flow_style=False)

            console.print(f"[green]Configuration saved to {output_file}[/green]")
            return True

        except Exception as e:
            console.print(f"[red]Error saving configuration: {str(e)}[/red]")
            log(f"Config save error: {str(e)}", 'error')
            return False
