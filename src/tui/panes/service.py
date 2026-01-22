"""Service Pane - Service management."""

import asyncio
import logging

from textual.app import ComposeResult
from textual.containers import Container, Horizontal
from textual.widgets import Static, Button, Log, Rule
from textual import work

# Import centralized service checker
try:
    from utils.service_check import check_service, check_systemd_service, ServiceState
    SERVICE_CHECK_AVAILABLE = True
except ImportError:
    SERVICE_CHECK_AVAILABLE = False

logger = logging.getLogger('tui')


class ServicePane(Container):
    """Service management pane"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._following = False
        self._follow_task = None

    def compose(self) -> ComposeResult:
        yield Static("# Service Management", classes="title")
        yield Rule()

        with Container(classes="card"):
            yield Static("Service Status", classes="card-title")
            yield Static("Checking...", id="svc-status", classes="card-value")
            yield Static("", id="svc-detail", classes="card-detail")

        with Horizontal(classes="button-row"):
            yield Button("Start", id="svc-start", variant="success")
            yield Button("Stop", id="svc-stop", variant="error")
            yield Button("Restart", id="svc-restart", variant="warning")
            yield Button("Reload Config", id="svc-reload")

        yield Static("## Boot Options", classes="section-title")
        with Horizontal(classes="button-row"):
            yield Button("Enable on Boot", id="svc-enable")
            yield Button("Disable on Boot", id="svc-disable")

        yield Static("## Service Logs", classes="section-title")
        with Horizontal(classes="button-row"):
            yield Button("Fetch Logs", id="svc-logs")
            yield Button("Follow Logs", id="svc-follow")
            yield Button("Stop Follow", id="svc-stop-follow")
            yield Button("Clear", id="svc-clear")

        yield Log(id="svc-log", classes="log-panel")

    async def on_mount(self):
        self.refresh_status()
        # Hide stop follow button initially
        self.query_one("#svc-stop-follow", Button).display = False

    @work(exclusive=True)
    async def refresh_status(self):
        """Refresh service status"""
        try:
            status_widget = self.query_one("#svc-status", Static)
            detail_widget = self.query_one("#svc-detail", Static)

            if SERVICE_CHECK_AVAILABLE:
                # Use centralized service checker
                svc_status = await asyncio.to_thread(check_service, 'meshtasticd')
                is_active = svc_status.available

                if is_active:
                    status_widget.update("[bold green]● Running[/bold green]")
                elif svc_status.state == ServiceState.DEGRADED:
                    status_widget.update("[bold yellow]● Degraded[/bold yellow]")
                else:
                    status_widget.update("[bold red]○ Stopped[/bold red]")

                # Show detection method and any hints
                detail_text = f"Detection: {svc_status.detection_method}"
                if svc_status.message:
                    detail_text += f"\n{svc_status.message}"
                detail_widget.update(detail_text)
            else:
                # Fallback to direct systemctl call
                result = await asyncio.create_subprocess_exec(
                    'systemctl', 'is-active', 'meshtasticd',
                    stdout=asyncio.subprocess.PIPE
                )
                stdout, _ = await result.communicate()
                is_active = stdout.decode().strip() == "active"

                if is_active:
                    status_widget.update("[bold green]● Running[/bold green]")
                else:
                    status_widget.update("[bold red]○ Stopped[/bold red]")

                # Get details
                result = await asyncio.create_subprocess_exec(
                    'systemctl', 'show', 'meshtasticd',
                    '--property=MainPID,ActiveEnterTimestamp',
                    stdout=asyncio.subprocess.PIPE
                )
                stdout, _ = await result.communicate()
                detail_widget.update(stdout.decode().strip())

        except Exception as e:
            self.query_one("#svc-status", Static).update(f"[red]Error: {e}[/red]")

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button presses"""
        button_id = event.button.id
        logger.info(f"[Service] Button pressed: {button_id}")
        log = self.query_one("#svc-log", Log)

        if button_id == "svc-start":
            log.write("Starting service...")
            self.run_systemctl("start")

        elif button_id == "svc-stop":
            log.write("Stopping service...")
            self.run_systemctl("stop")

        elif button_id == "svc-restart":
            log.write("Restarting service...")
            self.run_systemctl("restart")

        elif button_id == "svc-reload":
            log.write("Reloading daemon...")
            await self.run_command(['systemctl', 'daemon-reload'])

        elif button_id == "svc-enable":
            log.write("Enabling on boot...")
            self.run_systemctl("enable")

        elif button_id == "svc-disable":
            log.write("Disabling from boot...")
            self.run_systemctl("disable")

        elif button_id == "svc-logs":
            self.fetch_logs()

        elif button_id == "svc-follow":
            await self.start_following()

        elif button_id == "svc-stop-follow":
            self.stop_following()

        elif button_id == "svc-clear":
            log.clear()

    @work
    async def run_systemctl(self, action: str):
        """Run a systemctl action"""
        await self.run_command(['systemctl', action, 'meshtasticd'])
        self.refresh_status()

    async def run_command(self, cmd: list):
        """Run a command and log output"""
        log = self.query_one("#svc-log", Log)
        try:
            result = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await result.communicate()

            if stdout:
                log.write(stdout.decode())
            if stderr:
                log.write(f"[red]{stderr.decode()}[/red]")

            if result.returncode == 0:
                log.write("[green]Command completed successfully[/green]")
            else:
                log.write(f"[red]Command failed with code {result.returncode}[/red]")

        except Exception as e:
            log.write(f"[red]Error: {e}[/red]")

    @work
    async def fetch_logs(self):
        """Fetch service logs"""
        log = self.query_one("#svc-log", Log)
        log.clear()

        result = await asyncio.create_subprocess_exec(
            'journalctl', '-u', 'meshtasticd', '-n', '50', '--no-pager',
            stdout=asyncio.subprocess.PIPE
        )
        stdout, _ = await result.communicate()
        log.write(stdout.decode())

    async def start_following(self):
        """Start following logs"""
        self._following = True
        self.query_one("#svc-follow", Button).display = False
        self.query_one("#svc-stop-follow", Button).display = True
        log = self.query_one("#svc-log", Log)
        log.write("[yellow]Following logs... Press 'Stop Follow' to stop[/yellow]\n")
        self._follow_logs()

    def stop_following(self):
        """Stop following logs"""
        self._following = False
        self.query_one("#svc-follow", Button).display = True
        self.query_one("#svc-stop-follow", Button).display = False
        log = self.query_one("#svc-log", Log)
        log.write("[yellow]Log following stopped[/yellow]\n")

    @work(exclusive=True)
    async def _follow_logs(self):
        """Worker that follows logs"""
        log = self.query_one("#svc-log", Log)
        while self._following:
            try:
                result = await asyncio.create_subprocess_exec(
                    'journalctl', '-u', 'meshtasticd', '-n', '20', '--no-pager',
                    stdout=asyncio.subprocess.PIPE
                )
                stdout, _ = await result.communicate()
                log.clear()
                log.write(stdout.decode())
            except Exception as e:
                log.write(f"[red]Error fetching logs: {e}[/red]")
            await asyncio.sleep(2)
