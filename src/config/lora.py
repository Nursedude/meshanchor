"""LoRa-specific configuration module"""

from rich.console import Console
from rich.prompt import Prompt, Confirm
from rich.table import Table

console = Console()


class LoRaConfigurator:
    """Configure LoRa radio settings"""

    # LoRa regions and their frequencies
    REGIONS = {
        'US': {
            'name': 'United States',
            'frequency': '902-928 MHz',
            'channels': 104
        },
        'EU_433': {
            'name': 'Europe 433 MHz',
            'frequency': '433 MHz',
            'channels': 8
        },
        'EU_868': {
            'name': 'Europe 868 MHz',
            'frequency': '863-870 MHz',
            'channels': 8
        },
        'CN': {
            'name': 'China',
            'frequency': '470-510 MHz',
            'channels': 20
        },
        'JP': {
            'name': 'Japan',
            'frequency': '920-923 MHz',
            'channels': 10
        },
        'ANZ': {
            'name': 'Australia/New Zealand',
            'frequency': '915-928 MHz',
            'channels': 20
        },
        'KR': {
            'name': 'Korea',
            'frequency': '920-923 MHz',
            'channels': 8
        },
        'TW': {
            'name': 'Taiwan',
            'frequency': '920-925 MHz',
            'channels': 10
        },
        'RU': {
            'name': 'Russia',
            'frequency': '868-870 MHz',
            'channels': 8
        },
        'IN': {
            'name': 'India',
            'frequency': '865-867 MHz',
            'channels': 4
        },
    }

    # LoRa bandwidth options (kHz)
    BANDWIDTHS = [125, 250, 500]

    # LoRa spreading factors
    SPREADING_FACTORS = [7, 8, 9, 10, 11, 12]

    # Coding rates
    CODING_RATES = [5, 6, 7, 8]  # Represented as 4/5, 4/6, 4/7, 4/8

    # Official Meshtastic Modem Presets
    MODEM_PRESETS = {
        'LONG_FAST': {
            'name': 'Long Fast',
            'bandwidth': 250,
            'spreading_factor': 11,
            'coding_rate': 8,
            'description': 'Long range, moderate speed',
            'use_case': 'Best for most deployments, good range with acceptable speed',
            'air_time': '~1.3s per message',
            'range': 'Very Long (10-30+ km)',
            'recommended_by': 'Default Meshtastic preset'
        },
        'LONG_MODERATE': {
            'name': 'Long Moderate',
            'bandwidth': 125,
            'spreading_factor': 11,
            'coding_rate': 8,
            'description': 'Maximum range, slower speed',
            'use_case': 'Maximum range when speed is not critical',
            'air_time': '~2.6s per message',
            'range': 'Maximum (15-40+ km)',
            'recommended_by': 'For extreme range needs'
        },
        'MEDIUM_FAST': {
            'name': 'Medium Fast',
            'bandwidth': 250,
            'spreading_factor': 10,
            'coding_rate': 8,
            'description': 'Balanced range and speed',
            'use_case': 'Good for busy networks, faster than LongFast',
            'air_time': '~0.65s per message',
            'range': 'Medium-Long (5-20 km)',
            'recommended_by': 'MtnMesh community standard (Oct 2025)'
        },
        'MEDIUM_SLOW': {
            'name': 'Medium Slow',
            'bandwidth': 125,
            'spreading_factor': 10,
            'coding_rate': 8,
            'description': 'Medium range, better than MediumFast in congested areas',
            'use_case': 'Good balance of range and reliability',
            'air_time': '~1.3s per message',
            'range': 'Medium-Long (5-20 km)',
            'recommended_by': 'Alternative to MediumFast'
        },
        'SHORT_FAST': {
            'name': 'Short Fast',
            'bandwidth': 250,
            'spreading_factor': 7,
            'coding_rate': 8,
            'description': 'Fastest speed, shortest range',
            'use_case': 'High-density areas, rapid messaging',
            'air_time': '~0.08s per message',
            'range': 'Short (1-5 km)',
            'recommended_by': 'Urban/high-density deployments'
        },
        'SHORT_SLOW': {
            'name': 'Short Slow',
            'bandwidth': 125,
            'spreading_factor': 7,
            'coding_rate': 8,
            'description': 'Short range, reliable',
            'use_case': 'Close-range reliable communication',
            'air_time': '~0.16s per message',
            'range': 'Short (1-5 km)',
            'recommended_by': 'Reliable short-range'
        },
        'LONG_SLOW': {
            'name': 'Long Slow',
            'bandwidth': 125,
            'spreading_factor': 12,
            'coding_rate': 8,
            'description': 'Absolute maximum range',
            'use_case': 'Extreme range, very slow',
            'air_time': '~5.2s per message',
            'range': 'Extreme (20-50+ km)',
            'recommended_by': 'Only for extreme range scenarios'
        },
        'VERY_LONG_SLOW': {
            'name': 'Very Long Slow',
            'bandwidth': 62.5,
            'spreading_factor': 12,
            'coding_rate': 8,
            'description': 'Experimental extreme range',
            'use_case': 'Experimental, extremely slow',
            'air_time': '~10.4s per message',
            'range': 'Experimental (30-60+ km)',
            'recommended_by': 'Experimental only'
        }
    }

    def __init__(self, interface=None):
        self.interface = interface

    def show_regions(self):
        """Display available LoRa regions"""
        table = Table(title="LoRa Regions", show_header=True, header_style="bold magenta")
        table.add_column("Code", style="cyan")
        table.add_column("Name", style="green")
        table.add_column("Frequency", style="yellow")
        table.add_column("Channels", style="blue")

        for code, info in self.REGIONS.items():
            table.add_row(code, info['name'], info['frequency'], str(info['channels']))

        console.print(table)

    def configure_region(self):
        """Configure LoRa region"""
        console.print("\n[bold cyan]LoRa Region Configuration[/bold cyan]\n")

        self.show_regions()

        console.print("\n[yellow]Important:[/yellow] Select the region appropriate for your location.")
        console.print("Using the wrong region may be illegal and can cause interference.")

        region_codes = list(self.REGIONS.keys())
        region = Prompt.ask("\nSelect region", choices=region_codes, default="US")

        console.print(f"\n[green]Region set to: {self.REGIONS[region]['name']} ({self.REGIONS[region]['frequency']})[/green]")

        return region

    def configure_advanced(self):
        """Configure advanced LoRa parameters"""
        console.print("\n[bold cyan]Advanced LoRa Configuration[/bold cyan]\n")

        console.print("[yellow]Warning:[/yellow] Changing these settings can affect network compatibility")
        console.print("Only modify if you know what you're doing!\n")

        if not Confirm.ask("Continue with advanced configuration?", default=False):
            return None

        config = {}

        # Bandwidth
        console.print("\n[cyan]Bandwidth (kHz):[/cyan]")
        console.print("Higher bandwidth = faster data rate, but shorter range")
        bandwidth = Prompt.ask("Select bandwidth", choices=[str(b) for b in self.BANDWIDTHS], default="125")
        config['bandwidth'] = int(bandwidth)

        # Spreading Factor
        console.print("\n[cyan]Spreading Factor:[/cyan]")
        console.print("Higher SF = longer range, but slower data rate")
        sf = Prompt.ask("Select spreading factor", choices=[str(s) for s in self.SPREADING_FACTORS], default="7")
        config['spreading_factor'] = int(sf)

        # Coding Rate
        console.print("\n[cyan]Coding Rate (4/x):[/cyan]")
        console.print("Higher CR = more error correction, but more overhead")
        cr = Prompt.ask("Select coding rate", choices=[str(c) for c in self.CODING_RATES], default="5")
        config['coding_rate'] = int(cr)

        # Transmit Power
        console.print("\n[cyan]Transmit Power (dBm):[/cyan]")
        console.print("Higher power = longer range, but more battery consumption")
        power = Prompt.ask("Enter transmit power (0-30)", default="20")
        try:
            power_val = int(power)
            if 0 <= power_val <= 30:
                config['tx_power'] = power_val
            else:
                console.print("[yellow]Using default: 20 dBm[/yellow]")
                config['tx_power'] = 20
        except ValueError:
            console.print("[yellow]Using default: 20 dBm[/yellow]")
            config['tx_power'] = 20

        # Display summary
        self._display_config_summary(config)

        return config

    def _display_config_summary(self, config):
        """Display configuration summary"""
        console.print("\n[bold cyan]Configuration Summary:[/bold cyan]\n")

        table = Table(show_header=True, header_style="bold magenta")
        table.add_column("Parameter", style="cyan")
        table.add_column("Value", style="green")

        for key, value in config.items():
            display_key = key.replace('_', ' ').title()
            table.add_row(display_key, str(value))

        console.print(table)

        # Calculate approximate data rate
        self._show_performance_estimate(config)

    def _show_performance_estimate(self, config):
        """Show estimated performance based on configuration"""
        console.print("\n[cyan]Estimated Performance:[/cyan]")

        sf = config.get('spreading_factor', 7)
        bw = config.get('bandwidth', 125)

        # Rough estimates
        if sf <= 7:
            range_estimate = "Short (< 5 km)"
            speed_estimate = "Fast"
        elif sf <= 9:
            range_estimate = "Medium (5-10 km)"
            speed_estimate = "Medium"
        else:
            range_estimate = "Long (> 10 km)"
            speed_estimate = "Slow"

        console.print(f"  Range: [yellow]{range_estimate}[/yellow]")
        console.print(f"  Speed: [yellow]{speed_estimate}[/yellow]")
        console.print(f"  Bandwidth: [yellow]{bw} kHz[/yellow]")

        console.print("\n[dim]Note: Actual range depends on terrain, antennas, and interference[/dim]")

    def get_recommended_settings(self, use_case='general'):
        """Get recommended settings for common use cases"""
        presets = {
            'general': {
                'bandwidth': 125,
                'spreading_factor': 7,
                'coding_rate': 5,
                'tx_power': 20,
                'description': 'Balanced settings for general use'
            },
            'long_range': {
                'bandwidth': 125,
                'spreading_factor': 11,
                'coding_rate': 8,
                'tx_power': 30,
                'description': 'Maximum range (slow, high power)'
            },
            'fast': {
                'bandwidth': 250,
                'spreading_factor': 7,
                'coding_rate': 5,
                'tx_power': 20,
                'description': 'Fast data rate (shorter range)'
            },
            'low_power': {
                'bandwidth': 125,
                'spreading_factor': 9,
                'coding_rate': 5,
                'tx_power': 10,
                'description': 'Battery-efficient (reduced range)'
            }
        }

        return presets.get(use_case, presets['general'])

    def show_modem_presets(self):
        """Display available modem presets"""
        console.print("\n[bold cyan]Meshtastic Modem Presets[/bold cyan]\n")

        table = Table(show_header=True, header_style="bold magenta")
        table.add_column("Preset", style="cyan", width=15)
        table.add_column("Range", style="green", width=20)
        table.add_column("Air Time", style="yellow", width=15)
        table.add_column("Use Case", style="blue", width=40)

        # Sort by typical usage order
        preset_order = ['MEDIUM_FAST', 'LONG_FAST', 'SHORT_FAST', 'MEDIUM_SLOW', 'LONG_MODERATE', 'SHORT_SLOW', 'LONG_SLOW', 'VERY_LONG_SLOW']

        for preset_key in preset_order:
            if preset_key in self.MODEM_PRESETS:
                preset = self.MODEM_PRESETS[preset_key]
                marker = " ⭐" if preset_key == 'MEDIUM_FAST' else ""
                table.add_row(
                    preset['name'] + marker,
                    preset['range'],
                    preset['air_time'],
                    preset['use_case']
                )

        console.print(table)
        console.print("\n[yellow]⭐ MediumFast is the current MtnMesh community standard (Oct 2025)[/yellow]")

    def configure_modem_preset(self):
        """Configure using a modem preset"""
        console.print("\n[bold cyan]Modem Preset Selection[/bold cyan]\n")

        self.show_modem_presets()

        console.print("\n[cyan]Select a modem preset:[/cyan]")
        console.print("1. Medium Fast [yellow](MtnMesh standard)[/yellow]")
        console.print("2. Long Fast [yellow](Default Meshtastic)[/yellow]")
        console.print("3. Short Fast")
        console.print("4. Medium Slow")
        console.print("5. Long Moderate")
        console.print("6. Short Slow")
        console.print("7. Long Slow")
        console.print("8. Very Long Slow [yellow](Experimental)[/yellow]")
        console.print("9. Custom (Advanced)")

        choice = Prompt.ask("\nSelect preset", choices=["1", "2", "3", "4", "5", "6", "7", "8", "9"], default="1")

        preset_map = {
            "1": "MEDIUM_FAST",
            "2": "LONG_FAST",
            "3": "SHORT_FAST",
            "4": "MEDIUM_SLOW",
            "5": "LONG_MODERATE",
            "6": "SHORT_SLOW",
            "7": "LONG_SLOW",
            "8": "VERY_LONG_SLOW"
        }

        if choice == "9":
            # Custom configuration
            return self.configure_advanced()

        preset_key = preset_map[choice]
        preset = self.MODEM_PRESETS[preset_key]

        config = {
            'preset': preset_key,
            'preset_name': preset['name'],
            'bandwidth': preset['bandwidth'],
            'spreading_factor': preset['spreading_factor'],
            'coding_rate': preset['coding_rate']
        }

        # Display selected preset details
        console.print(f"\n[bold green]Selected: {preset['name']}[/bold green]")
        console.print(f"[cyan]Description:[/cyan] {preset['description']}")
        console.print(f"[cyan]Range:[/cyan] {preset['range']}")
        console.print(f"[cyan]Air Time:[/cyan] {preset['air_time']}")
        console.print(f"[cyan]Recommended by:[/cyan] {preset['recommended_by']}")

        # Show technical details
        table = Table(title="Technical Settings", show_header=True, header_style="bold magenta")
        table.add_column("Parameter", style="cyan")
        table.add_column("Value", style="green")

        table.add_row("Bandwidth", f"{preset['bandwidth']} kHz")
        table.add_row("Spreading Factor", str(preset['spreading_factor']))
        table.add_row("Coding Rate", f"4/{preset['coding_rate']}")

        console.print("\n")
        console.print(table)

        if Confirm.ask("\nUse this preset?", default=True):
            return config
        else:
            return self.configure_modem_preset()

    def configure_channels(self):
        """Configure channel settings"""
        console.print("\n[bold cyan]Channel Configuration[/bold cyan]\n")

        channels = []

        # Primary channel
        console.print("[cyan]Primary Channel (0):[/cyan]")
        console.print("This is the main communication channel for your mesh network.\n")

        primary = {}
        primary['name'] = Prompt.ask("Channel name", default="LongFast")
        primary['psk'] = Prompt.ask("Pre-shared key (base64, or press Enter for default)", default="AQ==")

        # Role
        console.print("\n[cyan]Channel role:[/cyan]")
        console.print("1. Primary (default)")
        console.print("2. Secondary")
        role_choice = Prompt.ask("Select role", choices=["1", "2"], default="1")
        primary['role'] = "PRIMARY" if role_choice == "1" else "SECONDARY"

        channels.append(primary)

        # Additional channels
        if Confirm.ask("\nAdd additional channels?", default=False):
            for i in range(1, 8):  # Max 8 channels (0-7)
                if Confirm.ask(f"\nConfigure channel {i}?", default=False):
                    channel = {}
                    channel['index'] = i
                    channel['name'] = Prompt.ask(f"Channel {i} name", default=f"Channel{i}")
                    channel['psk'] = Prompt.ask("Pre-shared key (base64)", default="AQ==")
                    channels.append(channel)
                else:
                    break

        # Display summary
        table = Table(title="Channel Summary", show_header=True, header_style="bold magenta")
        table.add_column("Index", style="cyan")
        table.add_column("Name", style="green")
        table.add_column("Role", style="yellow")

        for idx, ch in enumerate(channels):
            role = ch.get('role', 'SECONDARY')
            table.add_row(str(idx), ch['name'], role)

        console.print("\n")
        console.print(table)

        return channels
