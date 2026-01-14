"""
MeshForge TUI Launcher Package

Provides a raspi-config style launcher that works over SSH
and on terminals without X display.

This package splits the launcher into modular components:
- backend.py: Dialog/whiptail UI backend
- main.py: MeshForgeLauncher class and menu implementations

Usage:
    from launcher_tui import DialogBackend, MeshForgeLauncher

    # Or run directly: python -m launcher_tui
"""

from .backend import DialogBackend
from .main import MeshForgeLauncher

__all__ = ['DialogBackend', 'MeshForgeLauncher']


def main():
    """Main entry point for the TUI launcher."""
    launcher = MeshForgeLauncher()
    launcher.run()
