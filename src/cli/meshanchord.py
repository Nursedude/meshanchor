#!/usr/bin/env python3
"""
meshanchord — MeshAnchor daemon management CLI

Thin entry point that delegates to DaemonController in src/daemon.py.

Usage:
    meshanchord start [--profile <name>] [--config <path>] [--foreground]
    meshanchord stop
    meshanchord status [--json]
    meshanchord restart
    meshanchord reload
"""

import os
import sys
from pathlib import Path

# Ensure src/ is in path
_src_dir = Path(__file__).parent.parent
if str(_src_dir) not in sys.path:
    sys.path.insert(0, str(_src_dir))


def main():
    """Entry point — delegates to daemon.main()."""
    from daemon import main as daemon_main
    sys.exit(daemon_main())


if __name__ == "__main__":
    main()
