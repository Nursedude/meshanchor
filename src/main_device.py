"""
MeshForge Device Configuration Module

Contains device wizard and configuration functions:
- device_wizard: Industrial-class device detection
- configure_device: Device configuration menu
- configure_* functions: LoRa, channels, modules, etc.

Extracted from main.py for maintainability.
"""

import subprocess
from pathlib import Path

from rich.console import Console
from rich.prompt import Prompt, Confirm
from rich.table import Table

from utils import emoji as em
from config.device import DeviceConfigurator

console = Console()


def device_wizard():
    """
    Industrial-class device detection and configuration wizard.
    Scans USB, SPI, TCP, and BLE for Meshtastic devices.
    """
    import socket
    from utils.device_scanner import DeviceScanner

    console.print("\n[bold cyan]═══════════ MeshForge Device Wizard ═══════════[/bold cyan]")
    console.print("[dim]Industrial-class port detection for LoRa mesh devices[/dim]\n")

    devices_found = []

    # === SCAN USB DEVICES ===
    console.print("[cyan]Scanning USB ports...[/cyan]")
    try:
        scanner = DeviceScanner()
        scan_result = scanner.scan_all()

        for port in scan_result.get('serial_ports', []):
            if port.meshtastic_compatible:
                devices_found.append({
                    'type': 'USB',
                    'port': port.device,
                    'by_id': port.by_id or '',
                    'description': port.description or f"{port.usb_vendor}:{port.usb_product}",
                    'driver': port.driver,
                })

        console.print(f"  [green]✓[/green] Found {len(scan_result.get('serial_ports', []))} serial ports")
    except Exception as e:
        console.print(f"  [yellow]⚠ USB scan error: {e}[/yellow]")

    # === SCAN SPI DEVICES ===
    console.print("[cyan]Scanning SPI/GPIO...[/cyan]")
    spi_devices = []
    for spi_path in ['/dev/spidev0.0', '/dev/spidev0.1', '/dev/spidev1.0']:
        if Path(spi_path).exists():
            spi_devices.append(spi_path)

    if spi_devices:
        devices_found.append({
            'type': 'SPI',
            'port': spi_devices[0],
            'by_id': '',
            'description': 'SPI LoRa HAT (MeshAdv, Waveshare, etc.)',
            'driver': 'spidev',
        })
        console.print(f"  [green]✓[/green] Found SPI: {', '.join(spi_devices)}")
    else:
        console.print("  [dim]No SPI devices found[/dim]")

    # === SCAN TCP (meshtasticd) ===
    console.print("[cyan]Scanning TCP (meshtasticd)...[/cyan]")
    tcp_ports = [4403, 4404]  # Primary and alternate
    for tcp_port in tcp_ports:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(1)
            result = sock.connect_ex(('127.0.0.1', tcp_port))
            sock.close()
            if result == 0:
                devices_found.append({
                    'type': 'TCP',
                    'port': f'127.0.0.1:{tcp_port}',
                    'by_id': '',
                    'description': f'meshtasticd daemon (port {tcp_port})',
                    'driver': 'tcp',
                })
                console.print(f"  [green]✓[/green] Found meshtasticd on port {tcp_port}")
                break
        except Exception:
            pass
    else:
        console.print("  [dim]meshtasticd not running (TCP 4403/4404)[/dim]")

    # === DISPLAY RESULTS ===
    console.print(f"\n[bold]Found {len(devices_found)} Meshtastic-compatible device(s)[/bold]\n")

    if not devices_found:
        console.print("[yellow]No devices detected.[/yellow]")
        console.print("\n[dim]Tips:[/dim]")
        console.print("  • Connect a USB LoRa radio (T-Beam, Heltec, RAK, etc.)")
        console.print("  • Enable SPI in raspi-config for HAT devices")
        console.print("  • Start meshtasticd service for TCP connection")
        input("\nPress Enter to continue...")
        return

    # Build selection table
    table = Table(title="Detected Devices", show_header=True, header_style="bold magenta")
    table.add_column("#", style="cyan", width=3)
    table.add_column("Type", style="yellow", width=6)
    table.add_column("Port", style="green")
    table.add_column("Description", style="dim")

    for i, dev in enumerate(devices_found, 1):
        table.add_row(str(i), dev['type'], dev['port'], dev['description'])

    console.print(table)

    # === SELECT DEVICE ===
    choices = [str(i) for i in range(1, len(devices_found) + 1)] + ["0"]
    console.print(f"\n  [bold]0[/bold]. {em.get('⬅️')}  Back (no configuration)")

    choice = Prompt.ask("\n[cyan]Select device to configure[/cyan]",
                       choices=choices, default="1")

    if choice == "0":
        return

    selected = devices_found[int(choice) - 1]
    console.print(f"\n[green]Selected: {selected['type']} - {selected['port']}[/green]")

    # === CONFIGURE DEVICE ===
    _configure_device_wizard(selected)


def _configure_device_wizard(device: dict):
    """Walk through complete device configuration"""
    console.print("\n[bold cyan]═══════════ Device Configuration ═══════════[/bold cyan]")
    console.print(f"[dim]Configuring: {device['type']} at {device['port']}[/dim]\n")

    config = {'device': device}

    # --- Step 1: Node Identity ---
    console.print("[bold]Step 1: Node Identity[/bold]")

    long_name = Prompt.ask(
        "  Long name (up to 40 chars)",
        default="MeshForge Node"
    )[:40]
    config['long_name'] = long_name

    # Generate short name suggestion from long name
    suggested_short = ''.join(c for c in long_name[:4].upper() if c.isalnum())
    short_name = Prompt.ask(
        "  Short name (4 chars for mesh display)",
        default=suggested_short or "MESH"
    )[:4].upper()
    config['short_name'] = short_name

    console.print(f"  [green]✓[/green] Identity: {long_name} ({short_name})")

    # --- Step 2: Region ---
    console.print("\n[bold]Step 2: Region Selection[/bold]")

    regions = {
        '1': ('US', '902-928 MHz ISM'),
        '2': ('EU_868', '863-870 MHz'),
        '3': ('CN', '470-510 MHz'),
        '4': ('JP', '920-925 MHz'),
        '5': ('ANZ', '915-928 MHz Australia/NZ'),
        '6': ('KR', '920-923 MHz Korea'),
        '7': ('TW', '920-925 MHz Taiwan'),
        '8': ('RU', '868-870 MHz Russia'),
        '9': ('IN', '865-867 MHz India'),
    }

    for key, (code, desc) in regions.items():
        console.print(f"  [bold]{key}[/bold]. {code} - {desc}")

    region_choice = Prompt.ask("  Select region", choices=list(regions.keys()), default="1")
    config['region'] = regions[region_choice][0]
    console.print(f"  [green]✓[/green] Region: {config['region']}")

    # --- Step 3: Modem Preset ---
    console.print("\n[bold]Step 3: Modem Preset[/bold]")

    presets = {
        '1': ('LONG_FAST', 'Default - Good range/speed balance'),
        '2': ('SHORT_TURBO', 'High-speed gateway (~6.8 kbps, shorter range)'),
        '3': ('LONG_SLOW', 'Maximum range, slower speed'),
        '4': ('MEDIUM_FAST', 'Balanced for urban areas'),
        '5': ('LONG_MODERATE', 'Long range with moderate speed'),
    }

    for key, (name, desc) in presets.items():
        marker = " [cyan](Recommended for gateway)[/cyan]" if name == "SHORT_TURBO" else ""
        console.print(f"  [bold]{key}[/bold]. {name} - {desc}{marker}")

    preset_choice = Prompt.ask("  Select modem preset", choices=list(presets.keys()), default="1")
    config['modem_preset'] = presets[preset_choice][0]
    console.print(f"  [green]✓[/green] Preset: {config['modem_preset']}")

    # --- Step 4: Frequency Slot ---
    console.print("\n[bold]Step 4: Frequency Slot[/bold]")
    console.print("  [dim]Different slots avoid interference between networks[/dim]")
    console.print("  [dim]Slot 0 = default, Slot 8 = common gateway slot[/dim]")

    slot = Prompt.ask("  Frequency slot (0-103 for US)", default="0")
    try:
        config['frequency_slot'] = int(slot)
    except ValueError:
        config['frequency_slot'] = 0
    console.print(f"  [green]✓[/green] Slot: {config['frequency_slot']}")

    # --- Step 5: TX Power ---
    console.print("\n[bold]Step 5: TX Power[/bold]")
    console.print("  [dim]Higher = longer range, more power consumption[/dim]")
    console.print("  [dim]Standard: 20 dBm, High-power HAT: 30 dBm (1W)[/dim]")

    tx_power = Prompt.ask("  TX Power (dBm)", default="20")
    try:
        config['tx_power'] = int(tx_power)
    except ValueError:
        config['tx_power'] = 20
    console.print(f"  [green]✓[/green] TX Power: {config['tx_power']} dBm")

    # --- Step 6: Position (Optional) ---
    console.print("\n[bold]Step 6: Position (Optional)[/bold]")

    if Confirm.ask("  Set fixed position?", default=False):
        lat = Prompt.ask("  Latitude (e.g., 19.435175)", default="0.0")
        lon = Prompt.ask("  Longitude (e.g., -155.213842)", default="0.0")
        try:
            config['latitude'] = float(lat)
            config['longitude'] = float(lon)
            console.print(f"  [green]✓[/green] Position: {config['latitude']}, {config['longitude']}")
        except ValueError:
            console.print("  [yellow]Invalid coordinates - skipping position[/yellow]")
    else:
        console.print("  [dim]Position not set (use GPS or set later)[/dim]")

    # --- Step 7: MQTT ---
    console.print("\n[bold]Step 7: MQTT Policy[/bold]")

    mqtt_enabled = Confirm.ask("  Enable MQTT uplink?", default=False)
    config['mqtt_enabled'] = mqtt_enabled
    if mqtt_enabled:
        console.print("  [green]✓[/green] MQTT enabled")
    else:
        console.print("  [dim]MQTT disabled (recommended for gateway bridging to RNS)[/dim]")

    # === DISPLAY SUMMARY ===
    console.print("\n[bold cyan]═══════════ Configuration Summary ═══════════[/bold cyan]\n")

    summary_table = Table(show_header=False, box=None)
    summary_table.add_column("Setting", style="cyan")
    summary_table.add_column("Value", style="green")

    summary_table.add_row("Device", f"{config['device']['type']} - {config['device']['port']}")
    summary_table.add_row("Long Name", config['long_name'])
    summary_table.add_row("Short Name", config['short_name'])
    summary_table.add_row("Region", config['region'])
    summary_table.add_row("Modem Preset", config['modem_preset'])
    summary_table.add_row("Frequency Slot", str(config['frequency_slot']))
    summary_table.add_row("TX Power", f"{config['tx_power']} dBm")
    if 'latitude' in config:
        summary_table.add_row("Position", f"{config['latitude']}, {config['longitude']}")
    summary_table.add_row("MQTT", "Enabled" if config['mqtt_enabled'] else "Disabled")

    console.print(summary_table)

    # === APPLY CONFIGURATION ===
    if Confirm.ask("\n[cyan]Apply this configuration?[/cyan]", default=True):
        _apply_device_config(config)
    else:
        console.print("[yellow]Configuration cancelled[/yellow]")

    input("\nPress Enter to continue...")


def _apply_device_config(config: dict):
    """Apply configuration to the device via meshtastic CLI or meshtasticd"""
    console.print("\n[cyan]Applying configuration...[/cyan]")

    device = config['device']
    commands = []

    # Build meshtastic CLI commands
    if device['type'] == 'TCP':
        base_cmd = ['meshtastic', '--host', '127.0.0.1']
    elif device['type'] == 'USB':
        port = device.get('by_id') or device['port']
        base_cmd = ['meshtastic', '--port', port]
    else:
        # SPI - use TCP to meshtasticd
        base_cmd = ['meshtastic', '--host', '127.0.0.1']

    # Set owner/identity
    commands.append(base_cmd + ['--set-owner', config['long_name']])
    commands.append(base_cmd + ['--set-owner-short', config['short_name']])

    # Set region
    commands.append(base_cmd + ['--set', 'lora.region', config['region']])

    # Set modem preset
    commands.append(base_cmd + ['--set', 'lora.modem_preset', config['modem_preset']])

    # Set channel/frequency slot
    commands.append(base_cmd + ['--set', 'lora.channel_num', str(config['frequency_slot'])])

    # Set TX power
    commands.append(base_cmd + ['--set', 'lora.tx_power', str(config['tx_power'])])

    # Set position if provided
    if 'latitude' in config and 'longitude' in config:
        commands.append(base_cmd + ['--setlat', str(config['latitude'])])
        commands.append(base_cmd + ['--setlon', str(config['longitude'])])

    # Set MQTT
    mqtt_val = 'true' if config['mqtt_enabled'] else 'false'
    commands.append(base_cmd + ['--set', 'mqtt.enabled', mqtt_val])

    # Execute commands
    success_count = 0
    for cmd in commands:
        try:
            console.print(f"  [dim]Running: {' '.join(cmd[:4])}...[/dim]")
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if result.returncode == 0:
                success_count += 1
            else:
                console.print(f"  [yellow]Warning: {result.stderr.strip()}[/yellow]")
        except subprocess.TimeoutExpired:
            console.print(f"  [yellow]Command timed out[/yellow]")
        except FileNotFoundError:
            console.print("[red]meshtastic CLI not found. Install with: pip install meshtastic[/red]")
            return
        except Exception as e:
            console.print(f"  [red]Error: {e}[/red]")

    console.print(f"\n[green]✓ Applied {success_count}/{len(commands)} settings[/green]")

    # Reminder about verification
    console.print("\n[bold yellow]Important:[/bold yellow]")
    console.print("  CLI settings may not always apply reliably (upstream bug).")
    console.print("  [cyan]Verify settings in browser: http://localhost:9443[/cyan]")


def configure_device():
    """Configure meshtastic device"""
    console.print("\n[bold cyan]=============== Device Configuration ===============[/bold cyan]\n")

    while True:
        console.print("\n[dim cyan]-- Radio Settings --[/dim cyan]")
        console.print(f"  [bold]1[/bold]. {em.get('📻')} Complete Radio Setup [dim](Recommended)[/dim]")
        console.print(f"  [bold]2[/bold]. {em.get('🌐')} LoRa Settings [dim](Region, Preset)[/dim]")
        console.print(f"  [bold]3[/bold]. {em.get('📢')} Channel Configuration")
        console.print(f"  [bold]4[/bold]. {em.get('⚡')} [yellow]Channel Presets[/yellow] [dim](Quick Setup)[/dim]")

        console.print("\n[dim cyan]-- Device & Modules --[/dim cyan]")
        console.print(f"  [bold]5[/bold]. {em.get('🔌')} Module Configuration [dim](MQTT, Serial, etc.)[/dim]")
        console.print(f"  [bold]6[/bold]. {em.get('📝')} Device Settings [dim](Name, WiFi, etc.)[/dim]")

        console.print("\n[dim cyan]-- Hardware --[/dim cyan]")
        console.print(f"  [bold]7[/bold]. {em.get('🔍')} Hardware Detection")
        console.print(f"  [bold]8[/bold]. {em.get('🎛️')}  SPI HAT Configuration [dim](MeshAdv-Mini, etc.)[/dim]")

        console.print(f"\n  [bold]9[/bold]. {em.get('⬅️')}  Back to Main Menu")

        choice = Prompt.ask("\n[cyan]Select configuration option[/cyan]", choices=["1", "2", "3", "4", "5", "6", "7", "8", "9"], default="1")

        if choice == "1":
            configure_radio_complete()
        elif choice == "2":
            configure_lora()
        elif choice == "3":
            configure_channels()
        elif choice == "4":
            # Import here to avoid circular import
            from main import configure_channel_presets
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

    # Show next steps
    console.print("\n[bold cyan]═══════════ Next Steps ═══════════[/bold cyan]\n")
    console.print("[yellow]Complete your node setup:[/yellow]\n")
    console.print("  [bold]1. Set Regional Settings (REQUIRED)[/bold]")
    console.print("    Web: [cyan]http://localhost:9443[/cyan] → Radio Config")
    console.print("    CLI: [cyan]meshtastic --host localhost --set lora.region US[/cyan]\n")
    console.print("  [bold]2. Set Node Identity[/bold]")
    console.print("    [cyan]meshtastic --host localhost --set-owner 'YourCallsign'[/cyan]\n")
    console.print("  [bold]3. Verify Connection[/bold]")
    console.print("    [cyan]meshtastic --host localhost --info[/cyan]")
    console.print("\n[dim]Docs: https://meshtastic.org/docs/getting-started/initial-config/[/dim]")


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
