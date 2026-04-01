"""
MeshAnchor TUI Launcher Package

Provides a raspi-config style launcher that works over SSH
and on terminals without X display.

This package splits the launcher into modular components:
- backend.py: Dialog/whiptail UI backend
- main.py: MeshAnchorLauncher class and menu implementations

Usage:
    from launcher_tui import DialogBackend, MeshAnchorLauncher

    # Or run directly: python -m launcher_tui
"""

from .backend import DialogBackend
from .main import MeshAnchorLauncher

__all__ = ['DialogBackend', 'MeshAnchorLauncher']


def main():
    """Main entry point for the TUI launcher."""
    launcher = MeshAnchorLauncher()
    launcher.run()
