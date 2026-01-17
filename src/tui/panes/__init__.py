"""TUI Panes Module

Modular structure for TUI panes, extracted from app.py for maintainability.

Structure:
- dashboard.py - Dashboard status view
- service.py   - Service management
- config.py    - Config file management
- cli.py       - Meshtastic CLI
- tools.py     - System tools
"""

from .dashboard import DashboardPane
from .service import ServicePane
from .config import ConfigPane
from .cli import CLIPane
from .tools import ToolsPane

__all__ = [
    'DashboardPane',
    'ServicePane',
    'ConfigPane',
    'CLIPane',
    'ToolsPane',
]
