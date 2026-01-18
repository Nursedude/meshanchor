"""Config Pane - Configuration file manager."""

import asyncio
import logging
import subprocess
from pathlib import Path

from textual.app import ComposeResult
from textual.containers import Container, Horizontal
from textual.widgets import Static, Button, Label, ListItem, ListView, Log, Rule
from textual import work

logger = logging.getLogger('tui')


class ConfigPane(Container):
    """Configuration file manager pane"""

    CONFIG_BASE = Path("/etc/meshtasticd")
    AVAILABLE_D = CONFIG_BASE / "available.d"
    CONFIG_D = CONFIG_BASE / "config.d"

    def compose(self) -> ComposeResult:
        yield Static("# Config File Manager", classes="title")
        yield Static("Select configs from available.d to activate", classes="subtitle")
        yield Rule()

        with Horizontal():
            with Container(classes="list-container"):
                yield Static("Available Configs", classes="list-title")
                yield ListView(id="available-list")

            with Container(classes="list-container"):
                yield Static("Active Configs", classes="list-title")
                yield ListView(id="active-list")

        with Horizontal(classes="button-row"):
            yield Button("Activate", id="cfg-activate", variant="primary")
            yield Button("Deactivate", id="cfg-deactivate", variant="error")
            yield Button("Edit with nano", id="cfg-edit")
            yield Button("Edit config.yaml", id="cfg-main")
            yield Button("Refresh", id="cfg-refresh")

        yield Static("## Apply Changes", classes="section-title")
        with Horizontal(classes="button-row"):
            yield Button("Apply & Restart Service", id="cfg-apply", variant="warning")

        yield Static("## Preview", classes="section-title")
        yield Log(id="cfg-preview", classes="log-panel")

    async def on_mount(self):
        await self.refresh_lists()

    async def refresh_lists(self):
        """Refresh config lists"""
        available_list = self.query_one("#available-list", ListView)
        active_list = self.query_one("#active-list", ListView)

        await available_list.clear()
        await active_list.clear()

        # Load available configs
        if self.AVAILABLE_D.exists():
            for config in sorted(self.AVAILABLE_D.glob("*.yaml")):
                safe_id = config.stem.replace(".", "_")
                available_list.append(ListItem(Label(config.name), id=f"avail-{safe_id}"))

        # Load active configs
        if self.CONFIG_D.exists():
            for config in sorted(self.CONFIG_D.glob("*.yaml")):
                safe_id = config.stem.replace(".", "_")
                active_list.append(ListItem(Label(config.name), id=f"active-{safe_id}"))

    async def on_list_view_selected(self, event: ListView.Selected) -> None:
        """Handle list selection"""
        item_id = event.item.id
        preview = self.query_one("#cfg-preview", Log)

        if item_id and item_id.startswith("avail-"):
            name = item_id[6:].replace("_", ".")
            for ext in ['.yaml', '']:
                config_path = self.AVAILABLE_D / f"{name}{ext}"
                if config_path.exists():
                    try:
                        content = config_path.read_text()
                        preview.clear()
                        preview.write(f"[cyan]--- {config_path.name} ---[/cyan]\n")
                        preview.write(content[:2000])
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
                        preview.write(f"[cyan]--- {config_path.name} (ACTIVE) ---[/cyan]\n")
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
            await self._edit_selected()

        elif button_id == "cfg-main":
            await self._edit_main_config()

        elif button_id == "cfg-apply":
            await self._apply_and_restart()

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
            preview.write(f"[yellow]Launching nano to edit {config_path.name}...[/yellow]")
            preview.write("Press Ctrl+O to save, Ctrl+X to exit")
            subprocess.run(['nano', str(config_path)])
            preview.write("[green]Editor closed[/green]")
        else:
            preview.write("[yellow]Select a config first[/yellow]")

    @work
    async def _edit_main_config(self):
        """Edit main config.yaml"""
        preview = self.query_one("#cfg-preview", Log)
        main_config = self.CONFIG_BASE / "config.yaml"

        if main_config.exists():
            preview.write(f"[yellow]Launching nano to edit {main_config}...[/yellow]")
            subprocess.run(['nano', str(main_config)])
            preview.write("[green]Editor closed[/green]")
        else:
            preview.write(f"[red]Main config not found: {main_config}[/red]")

    async def _apply_and_restart(self):
        """Apply configuration and restart service"""
        preview = self.query_one("#cfg-preview", Log)
        preview.write("[yellow]Restarting meshtasticd...[/yellow]")

        try:
            result = await asyncio.create_subprocess_exec(
                'systemctl', 'daemon-reload',
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            await result.communicate()

            result = await asyncio.create_subprocess_exec(
                'systemctl', 'restart', 'meshtasticd',
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await result.communicate()

            if result.returncode == 0:
                preview.write("[green]Configuration applied - service restarted[/green]")
            else:
                preview.write(f"[red]Error: {stderr.decode()}[/red]")

        except Exception as e:
            preview.write(f"[red]Error: {e}[/red]")
