"""Config Pane - Configuration file manager with validation."""

import asyncio
import logging
import subprocess
from pathlib import Path

from textual.app import ComposeResult
from textual.containers import Container, Horizontal, Vertical
from textual.widgets import Static, Button, Label, ListItem, ListView, Log, Rule
from textual import work

logger = logging.getLogger('tui')


class ConfigPane(Container):
    """Configuration file manager pane with validation and visual feedback"""

    CONFIG_BASE = Path("/etc/meshtasticd")
    AVAILABLE_D = CONFIG_BASE / "available.d"
    CONFIG_D = CONFIG_BASE / "config.d"

    # Required sections in config.yaml for a working setup
    REQUIRED_SECTIONS = {
        'Lora': 'LoRa radio configuration (required for hardware)',
        'General': 'General settings like MaxNodes',
    }

    # Optional but recommended sections
    OPTIONAL_SECTIONS = {
        'Webserver': 'Web interface configuration',
        'GPS': 'GPS module settings',
        'Display': 'OLED/LCD display settings',
    }

    def compose(self) -> ComposeResult:
        yield Static("# Meshtasticd Configuration", classes="title")
        yield Static("Manage configuration files and validate settings", classes="subtitle")
        yield Rule()

        # Config Status Section
        yield Static("## Configuration Status", classes="section-title")
        with Horizontal(classes="status-cards"):
            with Container(classes="card"):
                yield Static("[CFG] config.yaml", classes="card-title")
                yield Static("Checking...", id="cfg-main-status", classes="card-value")
                yield Static("", id="cfg-main-detail", classes="card-detail")

            with Container(classes="card"):
                yield Static("[HW] Hardware Config", classes="card-title")
                yield Static("Checking...", id="cfg-hw-status", classes="card-value")
                yield Static("", id="cfg-hw-detail", classes="card-detail")

            with Container(classes="card"):
                yield Static("[ACT] Active Configs", classes="card-title")
                yield Static("Checking...", id="cfg-active-count", classes="card-value")
                yield Static("", id="cfg-active-detail", classes="card-detail")

        # File Lists
        yield Static("## Available Configurations", classes="section-title")
        yield Static("Copy from available.d to config.d to activate", classes="subtitle")

        with Horizontal():
            with Container(classes="list-container"):
                yield Static("Available (available.d/)", classes="list-title")
                yield ListView(id="available-list")

            with Container(classes="list-container"):
                yield Static("Active (config.d/)", classes="list-title")
                yield ListView(id="active-list")

        with Horizontal(classes="button-row"):
            yield Button("Activate ->", id="cfg-activate", variant="primary")
            yield Button("<- Deactivate", id="cfg-deactivate", variant="error")
            yield Button("Preview", id="cfg-preview-btn")
            yield Button("Refresh", id="cfg-refresh")

        # Edit Section
        yield Static("## Edit Configuration", classes="section-title")
        with Horizontal(classes="button-row"):
            yield Button("Edit config.yaml", id="cfg-main", variant="primary")
            yield Button("Edit selected", id="cfg-edit")
            yield Button("Validate All", id="cfg-validate", variant="success")

        # Apply Section
        yield Static("## Apply Changes", classes="section-title")
        with Horizontal(classes="button-row"):
            yield Button("Apply & Restart Service", id="cfg-apply", variant="warning")
            yield Button("View Service Logs", id="cfg-logs")

        # Preview/Log output
        yield Static("## Preview / Validation", classes="section-title")
        yield Log(id="cfg-preview", classes="log-panel")

    async def on_mount(self):
        """Initialize and validate configuration"""
        await self.refresh_lists()
        await self._check_config_status()

    async def _check_config_status(self):
        """Check and display configuration status"""
        preview = self.query_one("#cfg-preview", Log)

        # Check main config.yaml
        main_status = self.query_one("#cfg-main-status", Static)
        main_detail = self.query_one("#cfg-main-detail", Static)
        main_config = self.CONFIG_BASE / "config.yaml"

        if main_config.exists():
            try:
                content = main_config.read_text()
                # Check for required sections
                missing = []
                found = []
                for section in self.REQUIRED_SECTIONS:
                    if section + ':' in content:
                        found.append(section)
                    else:
                        missing.append(section)

                if missing:
                    main_status.update(f"[yellow]Incomplete[/yellow]")
                    main_detail.update(f"Missing: {', '.join(missing)}")
                else:
                    main_status.update("[green]Valid[/green]")
                    main_detail.update(f"Found: {', '.join(found)}")
            except Exception as e:
                main_status.update("[red]Error[/red]")
                main_detail.update(str(e)[:30])
        else:
            main_status.update("[red]Missing[/red]")
            main_detail.update("Create or copy from template")

        # Check hardware config (active in config.d)
        hw_status = self.query_one("#cfg-hw-status", Static)
        hw_detail = self.query_one("#cfg-hw-detail", Static)

        if self.CONFIG_D.exists():
            lora_configs = list(self.CONFIG_D.glob("lora-*.yaml"))
            if lora_configs:
                hw_status.update("[green]Configured[/green]")
                hw_detail.update(lora_configs[0].name)
            else:
                hw_status.update("[yellow]No LoRa[/yellow]")
                hw_detail.update("Activate a lora-*.yaml")
        else:
            hw_status.update("[red]Missing[/red]")
            hw_detail.update("config.d/ not found")

        # Count active configs
        active_status = self.query_one("#cfg-active-count", Static)
        active_detail = self.query_one("#cfg-active-detail", Static)

        if self.CONFIG_D.exists():
            active_files = list(self.CONFIG_D.glob("*.yaml"))
            count = len(active_files)
            if count > 0:
                active_status.update(f"[green]{count} files[/green]")
                active_detail.update("Ready")
            else:
                active_status.update("[yellow]0 files[/yellow]")
                active_detail.update("No configs active")
        else:
            active_status.update("[red]N/A[/red]")
            active_detail.update("Directory missing")

    async def refresh_lists(self):
        """Refresh config lists"""
        available_list = self.query_one("#available-list", ListView)
        active_list = self.query_one("#active-list", ListView)

        await available_list.clear()
        await active_list.clear()

        # Load available configs with categories
        if self.AVAILABLE_D.exists():
            configs = sorted(self.AVAILABLE_D.glob("*.yaml"))
            for config in configs:
                safe_id = config.stem.replace(".", "_")
                # Color code by type
                name = config.name
                if name.startswith("lora-"):
                    display = f"[cyan]{name}[/cyan]"
                elif name.startswith("gps-"):
                    display = f"[green]{name}[/green]"
                elif name.startswith("display-"):
                    display = f"[yellow]{name}[/yellow]"
                else:
                    display = name
                available_list.append(ListItem(Label(display), id=f"avail-{safe_id}"))

        # Load active configs
        if self.CONFIG_D.exists():
            for config in sorted(self.CONFIG_D.glob("*.yaml")):
                safe_id = config.stem.replace(".", "_")
                active_list.append(ListItem(Label(f"[bold]{config.name}[/bold]"), id=f"active-{safe_id}"))

        # Update status after refresh
        await self._check_config_status()

    async def on_list_view_selected(self, event: ListView.Selected) -> None:
        """Handle list selection - show preview"""
        await self._preview_selected(event.item.id)

    async def _preview_selected(self, item_id: str):
        """Preview selected config file"""
        preview = self.query_one("#cfg-preview", Log)

        if item_id and item_id.startswith("avail-"):
            name = item_id[6:].replace("_", ".")
            for ext in ['.yaml', '']:
                config_path = self.AVAILABLE_D / f"{name}{ext}"
                if config_path.exists():
                    try:
                        content = config_path.read_text()
                        preview.clear()
                        preview.write(f"[bold cyan]--- {config_path.name} ---[/bold cyan]\n")
                        preview.write(f"[dim]Location: {config_path}[/dim]\n\n")
                        preview.write(content[:2000])
                        if len(content) > 2000:
                            preview.write("\n[dim]... (truncated)[/dim]")
                    except Exception as e:
                        preview.write(f"[red]Error reading: {e}[/red]")
                    break

        elif item_id and item_id.startswith("active-"):
            name = item_id[7:].replace("_", ".")
            for ext in ['.yaml', '']:
                config_path = self.CONFIG_D / f"{name}{ext}"
                if config_path.exists():
                    try:
                        content = config_path.read_text()
                        preview.clear()
                        preview.write(f"[bold green]--- {config_path.name} (ACTIVE) ---[/bold green]\n")
                        preview.write(f"[dim]Location: {config_path}[/dim]\n\n")
                        preview.write(content[:2000])
                    except Exception as e:
                        preview.write(f"[red]Error reading: {e}[/red]")
                    break

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button presses"""
        button_id = event.button.id
        logger.info(f"[Config] Button pressed: {button_id}")
        preview = self.query_one("#cfg-preview", Log)

        if button_id == "cfg-refresh":
            await self.refresh_lists()
            preview.write("[green]Lists refreshed[/green]")

        elif button_id == "cfg-activate":
            await self._activate_selected()

        elif button_id == "cfg-deactivate":
            await self._deactivate_selected()

        elif button_id == "cfg-edit":
            self._edit_selected()

        elif button_id == "cfg-main":
            self._edit_main_config()

        elif button_id == "cfg-apply":
            await self._apply_and_restart()

        elif button_id == "cfg-validate":
            await self._validate_all()

        elif button_id == "cfg-preview-btn":
            available_list = self.query_one("#available-list", ListView)
            active_list = self.query_one("#active-list", ListView)
            if available_list.highlighted_child:
                await self._preview_selected(available_list.highlighted_child.id)
            elif active_list.highlighted_child:
                await self._preview_selected(active_list.highlighted_child.id)

        elif button_id == "cfg-logs":
            await self._show_service_logs()

    async def _validate_all(self):
        """Validate all configuration files"""
        preview = self.query_one("#cfg-preview", Log)
        preview.clear()
        preview.write("[bold cyan]Configuration Validation Report[/bold cyan]\n")
        preview.write("=" * 40 + "\n\n")

        issues = []
        warnings = []

        # Check config.yaml
        main_config = self.CONFIG_BASE / "config.yaml"
        if main_config.exists():
            preview.write("[green]OK[/green] config.yaml exists\n")
            try:
                content = main_config.read_text()

                # Check YAML syntax
                try:
                    import yaml
                    yaml.safe_load(content)
                    preview.write("[green]OK[/green] YAML syntax valid\n")
                except yaml.YAMLError as e:
                    issues.append(f"config.yaml: YAML syntax error - {e}")
                    preview.write(f"[red]ERR[/red] YAML syntax error\n")
                except ImportError:
                    preview.write("[yellow]SKIP[/yellow] YAML validation (pyyaml not installed)\n")

                # Check required sections
                for section, desc in self.REQUIRED_SECTIONS.items():
                    if section + ':' in content:
                        preview.write(f"[green]OK[/green] {section} section found\n")
                    else:
                        warnings.append(f"config.yaml: Missing {section} section ({desc})")
                        preview.write(f"[yellow]WARN[/yellow] {section} section missing\n")

            except Exception as e:
                issues.append(f"config.yaml: Cannot read - {e}")
        else:
            issues.append("config.yaml: File not found")
            preview.write("[red]ERR[/red] config.yaml not found\n")

        # Check config.d
        preview.write("\n[bold]Checking config.d/:[/bold]\n")
        if self.CONFIG_D.exists():
            active_files = list(self.CONFIG_D.glob("*.yaml"))
            lora_found = False

            for f in active_files:
                preview.write(f"  [green]Active[/green] {f.name}\n")
                if f.name.startswith("lora-"):
                    lora_found = True

            if not lora_found:
                warnings.append("No LoRa configuration active in config.d/")
                preview.write("[yellow]WARN[/yellow] No lora-*.yaml active (required for radio)\n")

            if not active_files:
                warnings.append("No configuration files in config.d/")
                preview.write("[yellow]WARN[/yellow] config.d/ is empty\n")
        else:
            issues.append("config.d/ directory not found")
            preview.write("[red]ERR[/red] config.d/ not found\n")

        # Summary
        preview.write("\n" + "=" * 40 + "\n")
        preview.write("[bold]Summary:[/bold]\n")

        if issues:
            preview.write(f"[red]Errors: {len(issues)}[/red]\n")
            for issue in issues:
                preview.write(f"  [red]X[/red] {issue}\n")
        else:
            preview.write("[green]No errors[/green]\n")

        if warnings:
            preview.write(f"[yellow]Warnings: {len(warnings)}[/yellow]\n")
            for warning in warnings:
                preview.write(f"  [yellow]![/yellow] {warning}\n")
        else:
            preview.write("[green]No warnings[/green]\n")

        if not issues and not warnings:
            preview.write("\n[bold green]Configuration looks good![/bold green]\n")
        elif issues:
            preview.write("\n[bold red]Fix errors before starting service[/bold red]\n")

    async def _activate_selected(self):
        """Activate selected config"""
        preview = self.query_one("#cfg-preview", Log)
        available_list = self.query_one("#available-list", ListView)

        if available_list.highlighted_child is None:
            preview.write("[yellow]Select a config from Available list first[/yellow]")
            return

        item_id = available_list.highlighted_child.id
        if not item_id:
            return

        name = item_id[6:].replace("_", ".")
        for ext in ['.yaml', '']:
            src = self.AVAILABLE_D / f"{name}{ext}"
            if src.exists():
                dst = self.CONFIG_D / src.name
                try:
                    self.CONFIG_D.mkdir(parents=True, exist_ok=True)
                    import shutil
                    shutil.copy(src, dst)
                    preview.write(f"[green]Activated: {src.name}[/green]")
                    preview.write(f"[dim]Copied to: {dst}[/dim]")
                    preview.write("[yellow]Remember to 'Apply & Restart' to use new config[/yellow]")
                    await self.refresh_lists()
                except Exception as e:
                    preview.write(f"[red]Error: {e}[/red]")
                break

    async def _deactivate_selected(self):
        """Deactivate selected config"""
        preview = self.query_one("#cfg-preview", Log)
        active_list = self.query_one("#active-list", ListView)

        if active_list.highlighted_child is None:
            preview.write("[yellow]Select a config from Active list first[/yellow]")
            return

        item_id = active_list.highlighted_child.id
        if not item_id:
            return

        name = item_id[7:].replace("_", ".")
        for ext in ['.yaml', '']:
            config_path = self.CONFIG_D / f"{name}{ext}"
            if config_path.exists():
                try:
                    config_path.unlink()
                    preview.write(f"[green]Deactivated: {config_path.name}[/green]")
                    preview.write("[yellow]Remember to 'Apply & Restart' to apply changes[/yellow]")
                    await self.refresh_lists()
                except Exception as e:
                    preview.write(f"[red]Error: {e}[/red]")
                break

    @work
    async def _edit_selected(self):
        """Edit selected config in nano"""
        preview = self.query_one("#cfg-preview", Log)
        available_list = self.query_one("#available-list", ListView)
        active_list = self.query_one("#active-list", ListView)

        config_path = None
        if available_list.highlighted_child:
            item_id = available_list.highlighted_child.id
            if item_id:
                name = item_id[6:].replace("_", ".")
                for ext in ['.yaml', '']:
                    path = self.AVAILABLE_D / f"{name}{ext}"
                    if path.exists():
                        config_path = path
                        break

        if not config_path and active_list.highlighted_child:
            item_id = active_list.highlighted_child.id
            if item_id:
                name = item_id[7:].replace("_", ".")
                for ext in ['.yaml', '']:
                    path = self.CONFIG_D / f"{name}{ext}"
                    if path.exists():
                        config_path = path
                        break

        if config_path:
            preview.write(f"[yellow]Opening {config_path.name} in nano...[/yellow]")
            preview.write("[dim]Press Ctrl+O to save, Ctrl+X to exit[/dim]")
            subprocess.run(['nano', str(config_path)], timeout=300)
            preview.write("[green]Editor closed[/green]")
            await self.refresh_lists()
        else:
            preview.write("[yellow]Select a config first[/yellow]")

    @work
    async def _edit_main_config(self):
        """Edit main config.yaml"""
        preview = self.query_one("#cfg-preview", Log)
        main_config = self.CONFIG_BASE / "config.yaml"

        if main_config.exists():
            preview.write(f"[yellow]Opening {main_config} in nano...[/yellow]")
            preview.write("[dim]Press Ctrl+O to save, Ctrl+X to exit[/dim]")
            subprocess.run(['nano', str(main_config)], timeout=300)
            preview.write("[green]Editor closed[/green]")
            await self._check_config_status()
        else:
            preview.write(f"[red]Main config not found: {main_config}[/red]")
            preview.write("[yellow]Creating template config.yaml...[/yellow]")

            # Create a basic template
            template = """# Meshtasticd Configuration
# See: https://meshtastic.org/docs/hardware/devices/linux-native-hardware/

# General settings
General:
  MaxNodes: 200

# LoRa settings (hardware-specific settings in config.d/)
# Lora:
#   Region: US  # Set your region

# Web server (optional)
# Webserver:
#   Port: 443
#   RootPath: /usr/share/meshtasticd/web
"""
            try:
                self.CONFIG_BASE.mkdir(parents=True, exist_ok=True)
                main_config.write_text(template)
                preview.write(f"[green]Created template: {main_config}[/green]")
                subprocess.run(['nano', str(main_config)], timeout=300)
                await self._check_config_status()
            except Exception as e:
                preview.write(f"[red]Error creating config: {e}[/red]")

    async def _apply_and_restart(self):
        """Apply configuration and restart service"""
        preview = self.query_one("#cfg-preview", Log)
        preview.write("[yellow]Applying configuration...[/yellow]")

        try:
            # Daemon reload
            preview.write("[dim]Running: systemctl daemon-reload[/dim]")
            result = await asyncio.create_subprocess_exec(
                'systemctl', 'daemon-reload',
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            await asyncio.wait_for(result.communicate(), timeout=30)

            # Restart service
            preview.write("[dim]Running: systemctl restart meshtasticd[/dim]")
            result = await asyncio.create_subprocess_exec(
                'systemctl', 'restart', 'meshtasticd',
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await asyncio.wait_for(result.communicate(), timeout=30)

            if result.returncode == 0:
                preview.write("[green]Configuration applied - service restarted[/green]")

                # Wait a moment then check status
                await asyncio.sleep(2)
                result = await asyncio.create_subprocess_exec(
                    'systemctl', 'is-active', 'meshtasticd',
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                stdout, _ = await asyncio.wait_for(result.communicate(), timeout=10)
                status = stdout.decode().strip()

                if status == "active":
                    preview.write("[bold green]Service is running[/bold green]")
                else:
                    preview.write(f"[yellow]Service status: {status}[/yellow]")
                    preview.write("[dim]Check logs for details[/dim]")
            else:
                preview.write(f"[red]Error: {stderr.decode()}[/red]")

        except asyncio.TimeoutError:
            preview.write("[red]Operation timed out[/red]")
        except Exception as e:
            preview.write(f"[red]Error: {e}[/red]")

    async def _show_service_logs(self):
        """Show recent service logs"""
        preview = self.query_one("#cfg-preview", Log)
        preview.clear()
        preview.write("[bold cyan]Recent meshtasticd logs:[/bold cyan]\n\n")

        try:
            result = await asyncio.create_subprocess_exec(
                'journalctl', '-u', 'meshtasticd', '-n', '30', '--no-pager',
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await asyncio.wait_for(result.communicate(), timeout=10)

            if stdout:
                preview.write(stdout.decode())
            if stderr and result.returncode != 0:
                preview.write(f"[red]{stderr.decode()}[/red]")

        except asyncio.TimeoutError:
            preview.write("[red]Timeout fetching logs[/red]")
        except Exception as e:
            preview.write(f"[red]Error: {e}[/red]")
