"""CLI Pane - Meshtastic CLI commands."""

import asyncio
import logging
import shlex

from textual.app import ComposeResult
from textual.containers import Container, Horizontal
from textual.widgets import Static, Button, Input, Log, Rule
from textual import work

logger = logging.getLogger('tui')


class CLIPane(Container):
    """Meshtastic CLI commands pane"""

    def compose(self) -> ComposeResult:
        yield Static("# Meshtastic CLI", classes="title")
        yield Rule()

        yield Static("## Connection", classes="section-title")
        with Horizontal(classes="input-row"):
            yield Static("Host:", classes="input-label")
            yield Input("127.0.0.1", id="cli-host")

        yield Static("## Quick Commands", classes="section-title")
        with Horizontal(classes="button-row"):
            yield Button("--info", id="cli-info")
            yield Button("--nodes", id="cli-nodes")
            yield Button("--get all", id="cli-getall")
            yield Button("--help", id="cli-help")

        yield Static("## Custom Command", classes="section-title")
        with Horizontal(classes="input-row"):
            yield Static("meshtastic", classes="input-label")
            yield Input("--info", id="cli-custom")
            yield Button("Run", id="cli-run", variant="primary")

        yield Static("## Output", classes="section-title")
        with Horizontal(classes="button-row"):
            yield Button("Clear", id="cli-clear")

        yield Log(id="cli-output", classes="log-panel")

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button presses"""
        button_id = event.button.id
        logger.info(f"[CLI] Button pressed: {button_id}")
        output = self.query_one("#cli-output", Log)
        host = self.query_one("#cli-host", Input).value

        cmd_map = {
            "cli-info": "--info",
            "cli-nodes": "--nodes",
            "cli-getall": "--get all",
            "cli-help": "--help",
        }

        if button_id == "cli-clear":
            output.clear()
            return

        if button_id == "cli-run":
            custom = self.query_one("#cli-custom", Input).value
            try:
                args = shlex.split(custom)
            except ValueError as e:
                output.write(f"[red]Invalid command syntax: {e}[/red]")
                return
        elif button_id in cmd_map:
            args = shlex.split(cmd_map[button_id])
        else:
            return

        self.run_meshtastic(host, args, output)

    @work
    async def run_meshtastic(self, host: str, args: list, output: Log):
        """Run meshtastic command"""
        cli_path = self._find_meshtastic_cli()
        if not cli_path:
            output.write("[red]meshtastic CLI not found. Install with:[/red]")
            output.write("sudo apt install pipx && pipx install 'meshtastic[cli]'")
            return

        cmd = [cli_path, '--host', host] + args
        output.write(f"$ {' '.join(cmd)}\n")

        try:
            result = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await result.communicate()

            if stdout:
                output.write(stdout.decode())
            if stderr:
                output.write(f"[red]{stderr.decode()}[/red]")

        except FileNotFoundError:
            output.write("[red]meshtastic CLI not found. Install with:[/red]")
            output.write("sudo apt install pipx && pipx install 'meshtastic[cli]'")
        except Exception as e:
            output.write(f"[red]Error: {e}[/red]")

    def _find_meshtastic_cli(self):
        """Find the meshtastic CLI executable"""
        try:
            from utils.cli import find_meshtastic_cli
            return find_meshtastic_cli()
        except ImportError:
            import shutil
            return shutil.which('meshtastic')
