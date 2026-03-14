#!/usr/bin/env python3
"""Patch RNS Transport.owner race condition (affects RNS <= 1.1.4).

The __jobs thread can call persist_data() before Transport.start() sets
Transport.owner, crashing rnsd ~12 hours after startup.

This script patches persist_data() and exit_handler() with a hasattr
guard. Safe to run multiple times — skips if already patched.

Usage:
    sudo python3 scripts/patch_rns_transport_race.py

The patch survives rnsd restarts but NOT pip upgrades of rns.
Add this to a post-install hook or cron if needed.
"""

import importlib.util
import os
import sys


VULNERABLE = '''    @staticmethod
    def persist_data():
        Transport.save_packet_hashlist()
        Transport.save_path_table()
        Transport.save_tunnel_table()

    @staticmethod
    def exit_handler():
        if not Transport.owner.is_connected_to_shared_instance:
            Transport.persist_data()'''

PATCHED = '''    @staticmethod
    def persist_data():
        if not hasattr(Transport, "owner") or Transport.owner is None:
            return
        Transport.save_packet_hashlist()
        Transport.save_path_table()
        Transport.save_tunnel_table()

    @staticmethod
    def exit_handler():
        if not hasattr(Transport, "owner") or Transport.owner is None:
            return
        if not Transport.owner.is_connected_to_shared_instance:
            Transport.persist_data()'''


def find_transport_files():
    """Find all RNS Transport.py files on the system."""
    paths = []

    # System site-packages
    spec = importlib.util.find_spec("RNS")
    if spec and spec.submodule_search_locations:
        for loc in spec.submodule_search_locations:
            candidate = os.path.join(loc, "Transport.py")
            if os.path.isfile(candidate):
                paths.append(candidate)

    # Common pipx venv locations
    home = os.environ.get("SUDO_USER", "")
    if home:
        home = f"/home/{home}"
    else:
        home = os.path.expanduser("~")

    for venv_root in [
        os.path.join(home, ".local/share/pipx/venvs"),
        "/opt/meshforge/venv/lib",
    ]:
        if os.path.isdir(venv_root):
            for root, dirs, files in os.walk(venv_root):
                if "Transport.py" in files and root.endswith("/RNS"):
                    candidate = os.path.join(root, "Transport.py")
                    if candidate not in paths:
                        paths.append(candidate)

    return paths


def patch_file(path):
    """Apply the race condition fix to a Transport.py file.

    Returns: 'patched', 'already_patched', or 'not_vulnerable'.
    """
    try:
        with open(path, "r") as f:
            content = f.read()
    except (OSError, PermissionError) as e:
        print(f"  SKIP {path}: {e}")
        return "error"

    if PATCHED in content:
        return "already_patched"

    if VULNERABLE not in content:
        return "not_vulnerable"

    content = content.replace(VULNERABLE, PATCHED)
    try:
        with open(path, "w") as f:
            f.write(content)
    except (OSError, PermissionError) as e:
        print(f"  FAIL {path}: {e}")
        return "error"

    # Clear bytecache
    cache_dir = os.path.join(os.path.dirname(path), "__pycache__")
    if os.path.isdir(cache_dir):
        for cached in os.listdir(cache_dir):
            if cached.startswith("Transport."):
                try:
                    os.remove(os.path.join(cache_dir, cached))
                except OSError:
                    pass

    return "patched"


def main():
    files = find_transport_files()
    if not files:
        print("No RNS Transport.py files found.")
        return 1

    results = {"patched": 0, "already_patched": 0, "not_vulnerable": 0, "error": 0}
    for path in files:
        status = patch_file(path)
        results[status] += 1
        label = {
            "patched": "PATCHED",
            "already_patched": "OK (already patched)",
            "not_vulnerable": "OK (not vulnerable / different version)",
            "error": "ERROR",
        }[status]
        print(f"  {label}: {path}")

    if results["patched"] > 0:
        print(f"\nPatched {results['patched']} file(s). Restart rnsd: sudo systemctl restart rnsd")
    elif results["already_patched"] > 0:
        print("\nAll files already patched.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
