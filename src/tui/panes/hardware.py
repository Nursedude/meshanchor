"""Hardware Pane - Raspberry Pi hardware setup assistant."""

import asyncio
import logging
from pathlib import Path

from textual.app import ComposeResult
from textual.containers import Container, Horizontal, Vertical
from textual.widgets import Static, Button, Log, Rule, Checkbox
from textual import work

logger = logging.getLogger('tui')


class HardwarePane(Container):
    """Hardware setup assistant for Raspberry Pi meshtasticd"""

    BOOT_CONFIG = Path("/boot/firmware/config.txt")
    BOOT_CONFIG_ALT = Path("/boot/config.txt")  # Older Pi OS

    def compose(self) -> ComposeResult:
        yield Static("# Hardware Setup Assistant", classes="title")
        yield Static("Configure your Raspberry Pi for meshtasticd", classes="subtitle")
        yield Rule()

        # Hardware Status Section
        yield Static("## Current Hardware Status", classes="section-title")

        with Horizontal(classes="status-cards"):
            with Container(classes="card"):
                yield Static("[SPI] SPI Bus", classes="card-title")
                yield Static("Checking...", id="hw-spi-status", classes="card-value")
                yield Static("", id="hw-spi-detail", classes="card-detail")

            with Container(classes="card"):
                yield Static("[I2C] I2C Bus", classes="card-title")
                yield Static("Checking...", id="hw-i2c-status", classes="card-value")
                yield Static("", id="hw-i2c-detail", classes="card-detail")

            with Container(classes="card"):
                yield Static("[UART] Serial Port", classes="card-title")
                yield Static("Checking...", id="hw-uart-status", classes="card-value")
                yield Static("", id="hw-uart-detail", classes="card-detail")

        # Boot Config Section
        yield Static("## Boot Configuration", classes="section-title")
        yield Static("Required settings in /boot/firmware/config.txt:", classes="subtitle")

        with Container(classes="card"):
            yield Static("[dtparam] Device Tree Parameters", classes="card-title")
            yield Static("", id="hw-dtparam-status", classes="card-value")

        # Quick Setup Buttons
        yield Static("## Quick Setup", classes="section-title")
        with Horizontal(classes="button-row"):
            yield Button("Enable SPI", id="hw-enable-spi", variant="primary")
            yield Button("Enable I2C", id="hw-enable-i2c")
            yield Button("Enable UART", id="hw-enable-uart")
            yield Button("Check All", id="hw-check", variant="success")

        yield Static("## Advanced Configuration", classes="section-title")
        with Horizontal(classes="button-row"):
            yield Button("Edit boot config", id="hw-edit-boot")
            yield Button("Reboot Required", id="hw-reboot", variant="warning")

        # Log output
        yield Static("## Setup Log", classes="section-title")
        yield Log(id="hw-log", classes="log-panel")

    async def on_mount(self):
        """Initialize hardware checks on mount"""
        self.refresh_status()

    @work(exclusive=True)
    async def refresh_status(self):
        """Refresh all hardware status indicators"""
        log = self.query_one("#hw-log", Log)
        log.write("[cyan]Checking hardware configuration...[/cyan]")

        # Check SPI
        spi_status = self.query_one("#hw-spi-status", Static)
        spi_detail = self.query_one("#hw-spi-detail", Static)

        spi0 = Path('/dev/spidev0.0').exists()
        spi1 = Path('/dev/spidev0.1').exists()

        if spi0 or spi1:
            devices = []
            if spi0:
                devices.append("spidev0.0")
            if spi1:
                devices.append("spidev0.1")
            spi_status.update(f"[green]Enabled[/green]")
            spi_detail.update(f"Devices: {', '.join(devices)}")
            log.write("[green][OK][/green] SPI enabled")
        else:
            spi_status.update("[red]Disabled[/red]")
            spi_detail.update("Required for LoRa radio")
            log.write("[red][X][/red] SPI not enabled - required for LoRa")

        # Check I2C
        i2c_status = self.query_one("#hw-i2c-status", Static)
        i2c_detail = self.query_one("#hw-i2c-detail", Static)

        i2c1 = Path('/dev/i2c-1').exists()
        i2c0 = Path('/dev/i2c-0').exists()

        if i2c1 or i2c0:
            devices = []
            if i2c0:
                devices.append("i2c-0")
            if i2c1:
                devices.append("i2c-1")
            i2c_status.update(f"[green]Enabled[/green]")
            i2c_detail.update(f"Devices: {', '.join(devices)}")
            log.write("[green][OK][/green] I2C enabled")
        else:
            i2c_status.update("[yellow]Disabled[/yellow]")
            i2c_detail.update("Optional (for sensors)")
            log.write("[yellow][~][/yellow] I2C disabled (optional)")

        # Check UART
        uart_status = self.query_one("#hw-uart-status", Static)
        uart_detail = self.query_one("#hw-uart-detail", Static)

        serial0 = Path('/dev/serial0').exists()
        ttyS0 = Path('/dev/ttyS0').exists()
        ttyAMA0 = Path('/dev/ttyAMA0').exists()

        if serial0 or ttyS0 or ttyAMA0:
            devices = []
            if serial0:
                devices.append("serial0")
            if ttyS0:
                devices.append("ttyS0")
            if ttyAMA0:
                devices.append("ttyAMA0")
            uart_status.update(f"[green]Enabled[/green]")
            uart_detail.update(f"Devices: {', '.join(devices)}")
            log.write("[green][OK][/green] UART enabled")
        else:
            uart_status.update("[yellow]Disabled[/yellow]")
            uart_detail.update("Optional (for GPS)")
            log.write("[yellow][~][/yellow] UART disabled (optional for GPS)")

        # Check boot config
        await self._check_boot_config()

        log.write("[cyan]Hardware check complete[/cyan]")

    async def _check_boot_config(self):
        """Check boot configuration file"""
        log = self.query_one("#hw-log", Log)
        dtparam_status = self.query_one("#hw-dtparam-status", Static)

        # Determine boot config path
        boot_config = self.BOOT_CONFIG if self.BOOT_CONFIG.exists() else self.BOOT_CONFIG_ALT

        if not boot_config.exists():
            dtparam_status.update("[yellow]Boot config not found[/yellow]")
            log.write("[yellow][~][/yellow] Boot config not found at standard locations")
            return

        try:
            content = boot_config.read_text()
            lines = content.split('\n')

            settings = {
                'dtparam=spi=on': False,
                'dtoverlay=spi0-0cs': False,
                'dtparam=i2c_arm=on': False,
                'enable_uart=1': False,
            }

            for line in lines:
                line = line.strip()
                if line.startswith('#'):
                    continue
                for setting in settings:
                    if setting in line:
                        settings[setting] = True

            # Build status display
            status_parts = []
            missing = []

            if settings['dtparam=spi=on']:
                status_parts.append("[green]SPI=on[/green]")
            else:
                missing.append("dtparam=spi=on")

            if settings['dtoverlay=spi0-0cs']:
                status_parts.append("[green]spi0-0cs[/green]")
            else:
                missing.append("dtoverlay=spi0-0cs")

            if settings['dtparam=i2c_arm=on']:
                status_parts.append("[green]I2C=on[/green]")

            if settings['enable_uart=1']:
                status_parts.append("[green]UART=1[/green]")

            if status_parts:
                dtparam_status.update(" | ".join(status_parts))
            else:
                dtparam_status.update("[yellow]No settings found[/yellow]")

            if missing:
                log.write(f"[yellow]Missing boot settings: {', '.join(missing)}[/yellow]")

        except Exception as e:
            dtparam_status.update(f"[red]Error reading config[/red]")
            log.write(f"[red]Error reading boot config: {e}[/red]")

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button presses"""
        button_id = event.button.id
        log = self.query_one("#hw-log", Log)

        if button_id == "hw-check":
            log.clear()
            self.refresh_status()

        elif button_id == "hw-enable-spi":
            await self._enable_spi()

        elif button_id == "hw-enable-i2c":
            await self._enable_i2c()

        elif button_id == "hw-enable-uart":
            await self._enable_uart()

        elif button_id == "hw-edit-boot":
            self._edit_boot_config()  # Sync - suspends app for nano
            self.refresh_status()  # Refresh after editor closes

        elif button_id == "hw-reboot":
            await self._prompt_reboot()

    async def _enable_spi(self):
        """Enable SPI interface"""
        log = self.query_one("#hw-log", Log)
        log.write("[yellow]Enabling SPI...[/yellow]")

        try:
            # Use raspi-config nonint
            result = await asyncio.create_subprocess_exec(
                'raspi-config', 'nonint', 'do_spi', '0',
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await result.communicate()

            if result.returncode == 0:
                log.write("[green]SPI enabled via raspi-config[/green]")

                # Now add spi0-0cs overlay if not present
                boot_config = self.BOOT_CONFIG if self.BOOT_CONFIG.exists() else self.BOOT_CONFIG_ALT
                if boot_config.exists():
                    content = boot_config.read_text()
                    if 'dtoverlay=spi0-0cs' not in content:
                        # Add after dtparam=spi=on
                        if 'dtparam=spi=on' in content:
                            content = content.replace(
                                'dtparam=spi=on',
                                'dtparam=spi=on\ndtoverlay=spi0-0cs'
                            )
                            boot_config.write_text(content)
                            log.write("[green]Added dtoverlay=spi0-0cs[/green]")
                        else:
                            # Append to end
                            with open(boot_config, 'a') as f:
                                f.write('\n# MeshForge SPI config\ndtparam=spi=on\ndtoverlay=spi0-0cs\n')
                            log.write("[green]Added SPI settings to boot config[/green]")

                log.write("[yellow]Reboot required to apply changes[/yellow]")
            else:
                log.write(f"[red]raspi-config failed: {stderr.decode()}[/red]")
                log.write("[yellow]Trying manual method...[/yellow]")
                await self._manual_enable_spi()

        except FileNotFoundError:
            log.write("[yellow]raspi-config not found, using manual method[/yellow]")
            await self._manual_enable_spi()
        except Exception as e:
            log.write(f"[red]Error: {e}[/red]")

        self.refresh_status()

    async def _manual_enable_spi(self):
        """Manually enable SPI by editing boot config"""
        log = self.query_one("#hw-log", Log)

        boot_config = self.BOOT_CONFIG if self.BOOT_CONFIG.exists() else self.BOOT_CONFIG_ALT
        if not boot_config.exists():
            log.write("[red]Boot config not found[/red]")
            return

        try:
            content = boot_config.read_text()
            modified = False

            if 'dtparam=spi=on' not in content:
                content += '\n# MeshForge SPI config\ndtparam=spi=on\n'
                modified = True

            if 'dtoverlay=spi0-0cs' not in content:
                content += 'dtoverlay=spi0-0cs\n'
                modified = True

            if modified:
                boot_config.write_text(content)
                log.write("[green]SPI settings added to boot config[/green]")
                log.write("[yellow]Reboot required to apply changes[/yellow]")
            else:
                log.write("[green]SPI settings already present[/green]")

        except Exception as e:
            log.write(f"[red]Error modifying boot config: {e}[/red]")

    async def _enable_i2c(self):
        """Enable I2C interface"""
        log = self.query_one("#hw-log", Log)
        log.write("[yellow]Enabling I2C...[/yellow]")

        try:
            result = await asyncio.create_subprocess_exec(
                'raspi-config', 'nonint', 'do_i2c', '0',
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            _, stderr = await result.communicate()

            if result.returncode == 0:
                log.write("[green]I2C enabled via raspi-config[/green]")
                log.write("[yellow]Reboot required to apply changes[/yellow]")
            else:
                log.write(f"[red]Error: {stderr.decode()}[/red]")
        except FileNotFoundError:
            # Manual method
            boot_config = self.BOOT_CONFIG if self.BOOT_CONFIG.exists() else self.BOOT_CONFIG_ALT
            if boot_config.exists():
                content = boot_config.read_text()
                if 'dtparam=i2c_arm=on' not in content:
                    with open(boot_config, 'a') as f:
                        f.write('\n# MeshForge I2C config\ndtparam=i2c_arm=on\n')
                    log.write("[green]I2C setting added to boot config[/green]")
                else:
                    log.write("[green]I2C already enabled[/green]")
        except Exception as e:
            log.write(f"[red]Error: {e}[/red]")

        self.refresh_status()

    async def _enable_uart(self):
        """Enable UART interface"""
        log = self.query_one("#hw-log", Log)
        log.write("[yellow]Enabling UART...[/yellow]")

        try:
            result = await asyncio.create_subprocess_exec(
                'raspi-config', 'nonint', 'do_serial_hw', '0',
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            _, stderr = await result.communicate()

            if result.returncode == 0:
                log.write("[green]UART enabled via raspi-config[/green]")
                log.write("[yellow]Reboot required to apply changes[/yellow]")
            else:
                log.write(f"[red]Error: {stderr.decode()}[/red]")
        except FileNotFoundError:
            # Manual method
            boot_config = self.BOOT_CONFIG if self.BOOT_CONFIG.exists() else self.BOOT_CONFIG_ALT
            if boot_config.exists():
                content = boot_config.read_text()
                if 'enable_uart=1' not in content:
                    with open(boot_config, 'a') as f:
                        f.write('\n# MeshForge UART config\nenable_uart=1\n')
                    log.write("[green]UART setting added to boot config[/green]")
                else:
                    log.write("[green]UART already enabled[/green]")
        except Exception as e:
            log.write(f"[red]Error: {e}[/red]")

        self.refresh_status()

    def _edit_boot_config(self):
        """Edit boot config in nano - properly suspends TUI"""
        log = self.query_one("#hw-log", Log)

        boot_config = self.BOOT_CONFIG if self.BOOT_CONFIG.exists() else self.BOOT_CONFIG_ALT

        if boot_config.exists():
            log.write(f"[yellow]Opening {boot_config} in nano...[/yellow]")
            log.write("[dim]Press Ctrl+O to save, Ctrl+X to exit nano[/dim]")

            # Suspend app to properly release terminal for external editor
            with self.app.suspend():
                import subprocess
                subprocess.run(['nano', str(boot_config)], timeout=300)

            log.write("[green]Editor closed - returned to TUI[/green]")
            # Note: refresh_status() is called by the button handler after this returns
        else:
            log.write("[red]Boot config not found[/red]")

    async def _prompt_reboot(self):
        """Prompt for system reboot"""
        log = self.query_one("#hw-log", Log)
        log.write("")
        log.write("[bold yellow]REBOOT REQUIRED[/bold yellow]")
        log.write("Hardware changes require a reboot to take effect.")
        log.write("")
        log.write("To reboot now, run: [cyan]sudo reboot[/cyan]")
        log.write("")
        log.write("[bold green]After reboot, type:[/bold green]")
        log.write("  [cyan]meshforge[/cyan]")
        log.write("[dim]to continue configuration[/dim]")
        log.write("")

        # Show what will happen on reboot
        spi_pending = not (Path('/dev/spidev0.0').exists() or Path('/dev/spidev0.1').exists())
        if spi_pending:
            boot_config = self.BOOT_CONFIG if self.BOOT_CONFIG.exists() else self.BOOT_CONFIG_ALT
            if boot_config.exists():
                content = boot_config.read_text()
                if 'dtparam=spi=on' in content:
                    log.write("[green]After reboot: SPI will be enabled[/green]")
