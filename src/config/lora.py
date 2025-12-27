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
