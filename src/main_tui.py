#!/usr/bin/env python3
"""
Meshtasticd Manager - Textual TUI Entry Point

This is the terminal-based interface for SSH and headless systems.
Works over Raspberry Pi Connect, VNC terminal, or any SSH session.

For systems with a display, you can also use main_gtk.py for a
full graphical interface.
"""

import os
import sys
from pathlib import Path


def check_root():
    """Check for root privileges"""
    if os.geteuid() != 0:
        print("=" * 60)
        print("ERROR: Root privileges required")
        print("=" * 60)
        print()
        print("This application requires root/sudo privileges.")
        print("Please run with:")
        print("  sudo python3 src/main_tui.py")
        print("=" * 60)
        sys.exit(1)


def check_textual():
    """Check if Textual is available"""
    try:
        import textual
        return True
    except ImportError:
        print("=" * 60)
        print("ERROR: Textual not installed")
        print("=" * 60)
        print()
        print("Install Textual with:")
        print("  pip install textual")
        print()
        print("Or use the original Rich-based UI:")
        print("  sudo python3 src/main.py")
        print("=" * 60)
        sys.exit(1)


def main():
    """Main entry point"""
    check_root()
    check_textual()

    # Add src to path
    src_dir = Path(__file__).parent
    sys.path.insert(0, str(src_dir))

    # Initialize configuration
    from utils.env_config import initialize_config
    initialize_config()

    # Launch TUI
    from tui.app import MeshtasticdTUI
    app = MeshtasticdTUI()
    app.run()


if __name__ == '__main__':
    main()
