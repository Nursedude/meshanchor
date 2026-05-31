"""Fleet RNS/LXMF version drift check (RNS-parity port from MeshForge, 2026-05-31).

Reads the pinned rns/lxmf versions from requirements/rns.txt and compares them to
what is INSTALLED on this box. Exits non-zero on drift. Runs from anywhere
(incl. over SSH): python3 /opt/meshanchor/scripts/rns_version_check.py

Why: RNS upstream withdrew public support (Carrier Switch, Dec 2025), so
MeshForge hard-forked rns/lxmf and pins a known-good version DELIBERATELY — a
bump is a reviewed decision, never an automatic `pip install` grabbing latest.
MeshAnchor adopts the same pin (the two sister NOC apps share the fleet's rnsd
and must run the identical RNS/LXMF build). This catches a box that has drifted
off the pin before it bites.
"""
import os
import re
import sys

try:
    from importlib.metadata import version, PackageNotFoundError
except ImportError:  # pragma: no cover  (py<3.8 fallback)
    from importlib_metadata import version, PackageNotFoundError  # type: ignore

_HERE = os.path.dirname(os.path.abspath(__file__))
REQ = os.path.join(_HERE, "..", "requirements", "rns.txt")


def pinned_versions():
    """Parse the expected rns/lxmf versions out of requirements/rns.txt.

    Two formats are recognised, newest first:
      * fork pin   ``# MF-FORK-PIN rns 1.2.5+mf.0`` — the version SSOT when the
        package is installed from a git URL (pip pins by commit SHA, so the
        version itself isn't in the requirement line).
      * legacy pin ``rns==1.2.5`` — the old PyPI exact pin.
    The ``+`` (PEP 440 local segment) is allowed in the version pattern.
    """
    pins = {}
    try:
        with open(REQ) as f:
            for line in f:
                m = re.match(r"^\s*#\s*MF-FORK-PIN\s+(rns|lxmf)\s+([0-9][0-9A-Za-z.+\-]*)", line)
                if m:
                    pins[m.group(1)] = m.group(2)
                    continue
                m = re.match(r"^\s*(rns|lxmf)==([0-9][0-9A-Za-z.+\-]*)", line)
                if m:
                    pins.setdefault(m.group(1), m.group(2))
    except OSError as e:
        print(f"cannot read {REQ}: {e}")
    return pins


def installed_version(pkg):
    try:
        return version(pkg)
    except PackageNotFoundError:
        return None


def main():
    pins = pinned_versions()
    host = os.uname().nodename
    print(f"RNS version check @ {host}")
    if not pins:
        print("  NO exact pin in requirements/rns.txt (fork pin not applied?)")
        return 2

    drift = False
    for pkg in ("rns", "lxmf"):
        want = pins.get(pkg)
        if want is None:
            continue
        have = installed_version(pkg)
        ok = have == want
        drift = drift or not ok
        print(f"  [{'OK   ' if ok else 'DRIFT'}] {pkg:<5} installed={str(have):<10} pinned={want}")

    if drift:
        print("  -> CONVERGE (watched): pip install --force-reinstall -r requirements/rns.txt")
        print("     (installs the MeshForge fork; verify rnsd + shared instance after)")
        return 1
    print("  -> compliant with the pin")
    return 0


if __name__ == "__main__":
    sys.exit(main())
