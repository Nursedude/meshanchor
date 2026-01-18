"""
Meshtasticd Manager - Textual TUI Application

A modern terminal UI that works over SSH and on headless systems.
Uses the Textual framework for a rich, interactive experience.
"""

import sys
import os
import subprocess
import asyncio
import shlex
import logging
from pathlib import Path

# Set up logging for TUI diagnostics
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    handlers=[
        logging.FileHandler('/tmp/meshforge-tui.log'),
    ]
)
logger = logging.getLogger('tui')

from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, Vertical, ScrollableContainer
from textual.widgets import (
    Header, Footer, Static, Button, Label, ListItem, ListView,
    Input, Log, TabbedContent, TabPane, DataTable, ProgressBar,
    Markdown, Rule
)
from textual.binding import Binding
from textual.screen import Screen
from textual import work

# Add parent to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from __version__ import __version__

# Import centralized service checker
try:
    from utils.service_check import check_service, check_port, ServiceStatus
except ImportError:
    check_service = None
    check_port = None
    ServiceStatus = None

# Import path utility for sudo-safe home directory
try:
    from utils.paths import get_real_user_home
except ImportError:
    def get_real_user_home():
        """Fallback for sudo-safe home directory."""
        sudo_user = os.environ.get('SUDO_USER')
        if sudo_user and sudo_user != 'root':
            return Path(f'/home/{sudo_user}')
        return Path.home()

# Import modular pane classes
from panes import DashboardPane, ServicePane, ConfigPane, CLIPane, ToolsPane


class StatusWidget(Static):
    """Status bar widget showing service state"""

    def __init__(self):
        super().__init__("")
        self.service_status = "checking"
        self.update_status()

    def update_status(self):
        """Update the status display"""
        if self.service_status == "active":
            status_text = "[green]● Service: Running[/green]"
        elif self.service_status == "inactive":
            status_text = "[red]○ Service: Stopped[/red]"
        else:
            status_text = "[yellow]? Service: Unknown[/yellow]"

        self.update(status_text)

    def set_service_status(self, status: str):
        """Set the service status"""
        self.service_status = status
        self.update_status()


class MeshtasticdTUI(App):
    """Meshtasticd Manager TUI Application"""

    CSS = """
    Screen {
        background: $surface;
    }

    .title {
        text-style: bold;
        color: $primary;
        padding: 1;
    }

    .subtitle {
        color: $text-muted;
        padding-left: 1;
    }

    .section-title {
        text-style: bold;
        margin-top: 1;
        padding-left: 1;
    }

    .card {
        border: solid $primary;
        padding: 1;
        margin: 1;
        width: 1fr;
    }

    .card-title {
        text-style: bold;
    }

    .card-value {
        margin-top: 1;
    }

    .card-detail {
        color: $text-muted;
    }

    .status-cards {
        height: auto;
    }

    .button-row {
        padding: 1;
        height: auto;
    }

    .button-row Button {
        margin-right: 1;
    }

    .input-row {
        padding: 1;
        height: auto;
    }

    .input-label {
        width: 10;
        padding-top: 1;
    }

    .input-row Input {
        width: 1fr;
    }

    .list-container {
        width: 1fr;
        height: 100%;
        border: solid $primary;
        margin: 1;
    }

    .list-title {
        text-style: bold;
        text-align: center;
        background: $primary;
        color: $text;
    }

    .log-panel {
        height: 1fr;
        min-height: 10;
        max-height: 100%;
        margin: 1;
        border: solid $surface-lighten-2;
        overflow-y: auto;
    }

    Log {
        scrollbar-gutter: stable;
    }

    TabPane {
        height: 100%;
    }

    TabPane > Container {
        height: 100%;
        /* NOTE: Do NOT add overflow-y: auto here - it breaks height: 1fr children */
    }

    TabbedContent {
        padding: 0;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("d", "switch_tab('dashboard')", "Dashboard"),
        Binding("s", "switch_tab('service')", "Service"),
        Binding("c", "switch_tab('config')", "Config"),
        Binding("m", "switch_tab('cli')", "CLI"),
        Binding("t", "switch_tab('tools')", "Tools"),
        Binding("r", "refresh", "Refresh"),
        Binding("T", "toggle_theme", "Theme"),
    ]

    TITLE = f"Meshtasticd Manager v{__version__}"

    def compose(self) -> ComposeResult:
        yield Header()

        with TabbedContent(initial="dashboard"):
            with TabPane("Dashboard", id="dashboard"):
                yield DashboardPane()
            with TabPane("Service", id="service"):
                yield ServicePane()
            with TabPane("Config", id="config"):
                yield ConfigPane()
            with TabPane("CLI", id="cli"):
                yield CLIPane()
            with TabPane("Tools", id="tools"):
                yield ToolsPane()

        yield Footer()

    def action_switch_tab(self, tab_id: str):
        """Switch to a specific tab"""
        tabbed = self.query_one(TabbedContent)
        tabbed.active = tab_id

    async def action_refresh(self):
        """Refresh current view"""
        tabbed = self.query_one(TabbedContent)
        active = tabbed.active

        if active == "dashboard":
            self.query_one(DashboardPane).refresh_data()
        elif active == "service":
            self.query_one(ServicePane).refresh_status()
        elif active == "config":
            await self.query_one(ConfigPane).refresh_lists()

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        """Global button handler"""
        logger.debug(f"[App] Global button handler: {event.button.id}")
        if event.button.id == "refresh-dashboard":
            self.query_one(DashboardPane).refresh_data()

    def action_toggle_theme(self) -> None:
        """Toggle between dark and light theme"""
        self.dark = not self.dark
        theme_name = "dark" if self.dark else "light"
        logger.info(f"[App] Theme switched to: {theme_name}")
        # Save preference to settings
        self._save_theme_preference(theme_name)

    def _load_theme_preference(self) -> None:
        """Load theme preference from settings file"""
        try:
            settings_dir = get_real_user_home() / ".config" / "meshforge"
            settings_file = settings_dir / "settings.json"
            if settings_file.exists():
                import json
                with open(settings_file) as f:
                    settings = json.load(f)
                theme = settings.get("theme", "dark")
                self.dark = theme != "light"
                logger.info(f"[App] Loaded theme preference: {theme}")
        except Exception as e:
            logger.debug(f"[App] Could not load theme preference: {e}")
            self.dark = True  # Default to dark

    def _save_theme_preference(self, theme: str) -> None:
        """Save theme preference to settings file"""
        try:
            settings_dir = get_real_user_home() / ".config" / "meshforge"
            settings_dir.mkdir(parents=True, exist_ok=True)
            settings_file = settings_dir / "settings.json"

            # Load existing settings or create new
            import json
            settings = {}
            if settings_file.exists():
                with open(settings_file) as f:
                    settings = json.load(f)

            settings["theme"] = theme
            settings["dark_mode"] = theme == "dark"  # For GTK compatibility

            with open(settings_file, 'w') as f:
                json.dump(settings, f, indent=2)

            logger.info(f"[App] Saved theme preference: {theme}")
        except Exception as e:
            logger.debug(f"[App] Could not save theme preference: {e}")

    async def on_mount(self) -> None:
        """Called when app is mounted"""
        logger.info("MeshForge TUI started - logging to /tmp/meshforge-tui.log")
        # Load and apply theme preference
        self._load_theme_preference()


def main():
    """Main entry point"""
    # Check root
    if os.geteuid() != 0:
        print("This application requires root privileges.")
        print("Please run with: sudo python3 src/main_tui.py")
        sys.exit(1)

    # Initialize config
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from utils.env_config import initialize_config
    initialize_config()

    # Run app
    app = MeshtasticdTUI()
    app.run()


if __name__ == '__main__':
    main()
