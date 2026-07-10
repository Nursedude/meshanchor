"""
MeshAnchor Version Checker

Checks installed versions of:
- meshtasticd (Linux native daemon)
- meshtastic CLI (Python package)
- Node firmware (via connected device)

And compares them against latest available versions.
"""

import re
import subprocess
import json
import logging
from dataclasses import dataclass
from typing import Optional, Dict, Any
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

from utils.safe_import import safe_import

# Shared SSOT for the fleet version floor (requirements/core.txt) — the SAME
# parser + below-floor test the MeshForge twin and the watchdog probes use,
# so no consumer can disagree about the baseline (ported 2026-07-10).
from utils.requirements_floor import read_floor, version_below

# Module-level safe imports
_version_mod, _HAS_VERSION = safe_import('__version__', '__version__')
from utils.cli import find_meshtastic_cli

# Cache for version checks to avoid hitting APIs too frequently
_version_cache: Dict[str, Any] = {}
_cache_ttl = timedelta(hours=1)


@dataclass
class VersionInfo:
    """Version information for a component"""
    name: str
    installed: Optional[str] = None
    latest: Optional[str] = None
    update_available: bool = False
    install_command: Optional[str] = None
    update_command: Optional[str] = None
    error: Optional[str] = None
    # apt-managed components (meshtasticd): a hold is a DELIBERATE pin on this
    # fleet (canary rolls) — surfaced as its own state, never as a nagging
    # "update available" nor silently hidden. ``notes`` carries operator-facing
    # context (pin state, candidate waiting behind the pin, ...).
    held: bool = False
    notes: Optional[str] = None
    # When this component is gated against the REVIEWED fleet baseline rather
    # than raw PyPI-latest, ``fleet_floor`` is that baseline (from
    # requirements/core.txt) and ``update_available`` means "installed BELOW
    # the floor" — a real laggard — not "PyPI moved past the floor" (a
    # reviewed bump, surfaced informationally in ``pypi_latest``).
    fleet_floor: Optional[str] = None
    pypi_latest: Optional[str] = None


def parse_version(version_str: str) -> tuple:
    """Parse version string into comparable tuple"""
    if not version_str:
        return (0, 0, 0)

    # Remove common prefixes
    version_str = version_str.lstrip('v').strip()

    # Handle versions like "2.5.6.abcd123"
    match = re.match(r'(\d+)\.(\d+)\.(\d+)', version_str)
    if match:
        return tuple(int(x) for x in match.groups())

    return (0, 0, 0)


def compare_versions(installed: str, latest: str) -> bool:
    """Check if latest version is newer than installed"""
    if not installed or not latest:
        return False

    inst_tuple = parse_version(installed)
    latest_tuple = parse_version(latest)

    return latest_tuple > inst_tuple


def get_meshanchor_version() -> Optional[str]:
    """Get installed MeshAnchor version from __version__.py"""
    try:
        # Import from the package
        if _HAS_VERSION:
            return _version_mod

        # Fallback: read the file directly
        version_file = Path(__file__).parent.parent / '__version__.py'
        if version_file.exists():
            content = version_file.read_text()
            match = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', content)
            if match:
                return match.group(1)

    except Exception as e:
        logger.debug(f"Error getting MeshAnchor version: {e}")

    return None


def get_latest_meshanchor_version() -> Optional[str]:
    """Get latest MeshAnchor version from GitHub"""
    cache_key = 'meshanchor_latest'

    # Check cache
    if cache_key in _version_cache:
        cached = _version_cache[cache_key]
        if datetime.now() - cached['timestamp'] < _cache_ttl:
            return cached['version']

    try:
        import urllib.request
        import ssl

        ctx = ssl.create_default_context()

        # Check the __version__.py file in the main branch
        url = 'https://raw.githubusercontent.com/Nursedude/meshanchor/main/src/__version__.py'
        req = urllib.request.Request(url, headers={'User-Agent': 'MeshAnchor'})

        with urllib.request.urlopen(req, timeout=10, context=ctx) as response:
            content = response.read().decode()
            match = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', content)
            if match:
                version = match.group(1)

                # Cache result
                _version_cache[cache_key] = {
                    'version': version,
                    'timestamp': datetime.now()
                }

                return version

    except Exception as e:
        logger.debug(f"Error getting latest MeshAnchor version: {e}")

    return None


def get_meshtasticd_version() -> Optional[str]:
    """Get installed meshtasticd version"""
    try:
        # Try dpkg first (Debian/Ubuntu)
        result = subprocess.run(
            ['dpkg', '-s', 'meshtasticd'],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            for line in result.stdout.split('\n'):
                if line.startswith('Version:'):
                    return line.split(':', 1)[1].strip()

        # Try meshtasticd --version
        result = subprocess.run(
            ['meshtasticd', '--version'],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            # Parse version from output
            match = re.search(r'(\d+\.\d+\.\d+)', result.stdout)
            if match:
                return match.group(1)

    except FileNotFoundError:
        return None
    except Exception as e:
        logger.debug(f"Error getting meshtasticd version: {e}")

    return None


def get_meshtastic_cli_version() -> Optional[str]:
    """Get installed meshtastic CLI version"""
    try:
        # Find meshtastic CLI using centralized function
        cli_path = find_meshtastic_cli()

        if not cli_path:
            return None

        # Get version
        result = subprocess.run(
            [cli_path, '--version'],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            # Parse version - format is usually "meshtastic 2.3.4"
            match = re.search(r'(\d+\.\d+\.\d+)', result.stdout)
            if match:
                return match.group(1)

    except Exception as e:
        logger.debug(f"Error getting meshtastic CLI version: {e}")

    return None


def get_meshanchor_venv_dir() -> Optional[Path]:
    """Return MeshAnchor's venv dir IFF the updater installs into it.

    SINGLE SOURCE OF TRUTH for "which python does MeshAnchor install into" — the
    venv is used when ``venv/bin/python`` exists and there is no ``.no-venv``
    opt-out marker (the same gate ``install.sh`` writes). Consumed by
    ``utils.pip_install.resolve_target_python`` so writes target one interpreter
    instead of the ~10 inline venv-vs-system copies MeshAnchor had. Ported from
    MeshForge's ``get_meshforge_venv_dir`` (install-hardening parity, 2026-06-23).
    """
    repo_root = Path(__file__).resolve().parents[2]
    venv_dir = repo_root / 'venv'
    if (venv_dir / 'bin' / 'python').exists() and not (repo_root / '.no-venv').exists():
        return venv_dir
    return None


def get_meshtastic_lib_version() -> Optional[str]:
    """Get installed meshtastic Python library version.

    This checks the pip-installed library (used for protobuf definitions),
    which is separate from the meshtastic CLI (typically installed via pipx).
    """
    try:
        import importlib.metadata
        return importlib.metadata.version('meshtastic')
    except Exception:
        pass

    # Fallback: check via pip show
    try:
        import sys
        result = subprocess.run(
            [sys.executable, '-m', 'pip', 'show', 'meshtastic'],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            for line in result.stdout.split('\n'):
                if line.startswith('Version:'):
                    return line.split(':', 1)[1].strip()
    except FileNotFoundError:
        return None
    except Exception as e:
        logger.debug(f"Error getting meshtastic library version: {e}")

    return None


def get_node_firmware_version() -> Optional[str]:
    """Get firmware version from connected node via meshtastic CLI"""
    try:
        import socket

        # Quick check if port is available
        sock = None
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(2.0)
            if sock.connect_ex(('localhost', 4403)) != 0:
                return None
        finally:
            if sock:
                try:
                    sock.close()
                except Exception:
                    pass

        # Find CLI using centralized function
        cli_path = find_meshtastic_cli()

        if not cli_path:
            return None

        result = subprocess.run(
            [cli_path, '--host', 'localhost', '--info'],
            capture_output=True, text=True, timeout=30
        )

        if result.returncode == 0:
            # Parse firmware version from JSON
            match = re.search(r'"firmwareVersion":\s*"([^"]+)"', result.stdout)
            if match:
                return match.group(1)

    except Exception as e:
        logger.debug(f"Error getting firmware version: {e}")

    return None


def get_latest_meshtasticd_version() -> Optional[str]:
    """Get latest meshtasticd version from GitHub releases"""
    cache_key = 'meshtasticd_latest'

    # Check cache
    if cache_key in _version_cache:
        cached = _version_cache[cache_key]
        if datetime.now() - cached['timestamp'] < _cache_ttl:
            return cached['version']

    try:
        import urllib.request
        import ssl

        # Create SSL context
        ctx = ssl.create_default_context()

        url = 'https://api.github.com/repos/meshtastic/firmware/releases/latest'
        req = urllib.request.Request(url, headers={'User-Agent': 'MeshAnchor'})

        with urllib.request.urlopen(req, timeout=10, context=ctx) as response:
            data = json.loads(response.read().decode())
            version = data.get('tag_name', '').lstrip('v')

            # Cache result
            _version_cache[cache_key] = {
                'version': version,
                'timestamp': datetime.now()
            }

            return version

    except Exception as e:
        logger.debug(f"Error getting latest meshtasticd version: {e}")

    return None


def get_latest_meshtastic_cli_version() -> Optional[str]:
    """Get latest meshtastic CLI version from PyPI"""
    cache_key = 'meshtastic_cli_latest'

    # Check cache
    if cache_key in _version_cache:
        cached = _version_cache[cache_key]
        if datetime.now() - cached['timestamp'] < _cache_ttl:
            return cached['version']

    try:
        import urllib.request
        import ssl

        ctx = ssl.create_default_context()

        url = 'https://pypi.org/pypi/meshtastic/json'
        req = urllib.request.Request(url, headers={'User-Agent': 'MeshAnchor'})

        with urllib.request.urlopen(req, timeout=10, context=ctx) as response:
            data = json.loads(response.read().decode())
            version = data.get('info', {}).get('version')

            # Cache result
            _version_cache[cache_key] = {
                'version': version,
                'timestamp': datetime.now()
            }

            return version

    except Exception as e:
        logger.debug(f"Error getting latest CLI version: {e}")

    return None


def get_latest_firmware_version() -> Optional[str]:
    """Get latest Meshtastic firmware version from GitHub"""
    # Same as meshtasticd for now - they share the firmware repo
    return get_latest_meshtasticd_version()


def _apply_fleet_floor(info: VersionInfo, pypi_latest) -> None:
    """Gate ``info.update_available`` on the REVIEWED fleet floor, not PyPI-latest.

    For a fleet pinned to a reviewed baseline (``requirements/core.txt:
    meshtastic>=X``), a box is a real laggard only when it is installed BELOW
    that floor. Comparing against raw PyPI-latest produces phantom "update
    available" the moment PyPI moves past the reviewed baseline (the
    2026-06-17 MeshForge phantom-update class; ported 2026-07-10). The floor
    is a LOCAL file read, so the gating decision also works offline.

    Honest failure mode: if the floor can't be read we refuse to claim either
    "up to date" OR a phantom update — ``update_available`` stays False and
    the blindness is recorded in ``error``.
    """
    info.pypi_latest = pypi_latest
    floor = read_floor('meshtastic')
    if not floor:
        info.update_available = False
        note = 'fleet baseline (requirements/core.txt) unavailable'
        info.error = f"{info.error}; {note}" if info.error else note
        return
    info.fleet_floor = floor
    info.latest = floor
    info.update_available = bool(info.installed and version_below(info.installed, floor))


def get_meshanchor_git_snapshot():
    """git-layer truth for the MeshAnchor checkout (patchable seam).

    Cached for the module TTL — check_all_versions runs on TUI startup, and
    a `git fetch` per call would hammer the network. Returns None when the
    git layer errors out, so the caller falls back to the version-string
    compare rather than trusting a half-read state.
    """
    cache_key = 'meshanchor_git_state'
    if cache_key in _version_cache:
        cached = _version_cache[cache_key]
        if datetime.now() - cached['timestamp'] < _cache_ttl:
            return cached['state']
    try:
        from updates.meshanchor_git import get_meshanchor_git_state
        state = get_meshanchor_git_state(fetch=True)
    except Exception as e:
        logger.debug(f"git state read failed: {e}")
        return None
    _version_cache[cache_key] = {'state': state, 'timestamp': datetime.now()}
    return state


def get_meshtasticd_apt_snapshot():
    """apt-layer truth for meshtasticd (installed/candidate/held), no simulation.

    Thin, patchable seam over ``updates.meshtasticd_apt`` — the check-all path
    wants the cheap state read; the update FLOW runs its own dry-run
    simulation. Returns None when the apt layer itself errors out, so the
    caller can fall back rather than trust a half-read state.
    """
    try:
        from updates.meshtasticd_apt import get_meshtasticd_apt_state
        return get_meshtasticd_apt_state(simulate=False)
    except Exception as e:
        logger.debug(f"apt state read failed: {e}")
        return None


def check_all_versions() -> Dict[str, VersionInfo]:
    """Check all component versions and return status"""
    results = {}

    # MeshAnchor itself — judged by GIT truth (HEAD vs origin/main), not
    # release version strings: this repo ships continuously by commit, and a
    # version string that only moves on releases NEVER showed an update
    # (2026-07-10 MeshForge follow-up, ported). Version-string compare
    # remains the non-git fallback.
    meshanchor = VersionInfo(name='MeshAnchor')
    meshanchor.installed = get_meshanchor_version()
    git_state = get_meshanchor_git_snapshot()
    if git_state is not None and git_state.is_git_repo and git_state.head:
        head = (git_state.head or '')[:8]
        remote = (git_state.remote_head or '')[:8]
        meshanchor.installed = f"{meshanchor.installed or '?'} @ {head}"
        meshanchor.latest = f"{meshanchor.installed.split(' @ ')[0]} @ {remote or '?'}"
        meshanchor.update_available = git_state.update_available
        if git_state.fetch_ok is False:
            meshanchor.notes = 'remote unverified (git fetch failed — offline?)'
        elif git_state.behind:
            meshanchor.notes = f'{git_state.behind} commit(s) behind origin/main'
        elif git_state.ahead:
            meshanchor.notes = f'{git_state.ahead} commit(s) AHEAD of origin/main (dev box)'
    else:
        meshanchor.latest = get_latest_meshanchor_version()
        if meshanchor.installed and meshanchor.latest:
            meshanchor.update_available = compare_versions(meshanchor.installed, meshanchor.latest)
    meshanchor.update_command = 'meshanchor-update'  # Special command handled by TUI
    results['meshanchor'] = meshanchor

    # meshtasticd — judged against the apt CANDIDATE (what apt can actually
    # install on this box), not GitHub firmware releases: the GitHub tag says
    # nothing about what the configured OBS repos serve, and comparing against
    # it produced both phantom updates and blindness to broken candidates
    # (2026-07-10 audit, ported from MeshForge). GitHub remains the fallback
    # for non-apt systems only.
    meshtasticd = VersionInfo(name='meshtasticd')
    apt_state = get_meshtasticd_apt_snapshot()
    if apt_state is not None and apt_state.apt_available and (
            apt_state.installed or apt_state.candidate):
        meshtasticd.installed = apt_state.installed
        meshtasticd.latest = apt_state.candidate
        meshtasticd.held = apt_state.held
        if apt_state.held:
            # A hold is a deliberate pin — not a laggard. Don't nag, but do
            # surface a candidate waiting behind the pin.
            meshtasticd.update_available = False
            if (apt_state.installed and apt_state.candidate
                    and apt_state.installed != apt_state.candidate):
                meshtasticd.notes = (
                    f'pinned by apt hold; candidate {apt_state.candidate} '
                    'waiting — use "Update meshtasticd" to unhold deliberately'
                )
            else:
                meshtasticd.notes = 'pinned by apt hold (deliberate)'
        else:
            meshtasticd.update_available = apt_state.update_available
        if apt_state.error:
            meshtasticd.error = apt_state.error
    else:
        meshtasticd.installed = get_meshtasticd_version()
        meshtasticd.latest = get_latest_meshtasticd_version()
        if meshtasticd.installed and meshtasticd.latest:
            meshtasticd.update_available = compare_versions(meshtasticd.installed, meshtasticd.latest)
    meshtasticd.update_command = 'sudo apt-get install --only-upgrade -y meshtasticd'
    results['meshtasticd'] = meshtasticd

    # Meshtastic CLI — gated against the fleet floor, not PyPI-latest.
    cli = VersionInfo(name='Meshtastic CLI')
    cli.installed = get_meshtastic_cli_version()
    _apply_fleet_floor(cli, get_latest_meshtastic_cli_version())
    # Display/manual-copy form: the writer targets the reviewed floor, never
    # PyPI-latest (`pipx upgrade` overshoots the pin the moment PyPI moves).
    if cli.fleet_floor:
        cli.update_command = f'pipx install --force meshtastic=={cli.fleet_floor}'
        cli.install_command = f'pipx install meshtastic=={cli.fleet_floor}'
    else:
        cli.update_command = 'pipx install --force meshtastic'
        cli.install_command = 'pipx install meshtastic'
    results['cli'] = cli

    # Meshtastic Python Library (protobuf definitions) — fleet-floor gated.
    lib = VersionInfo(name='Meshtastic Library')
    lib.installed = get_meshtastic_lib_version()
    _apply_fleet_floor(lib, get_latest_meshtastic_cli_version())  # same PyPI pkg (cached)
    _lib_spec = (f'meshtastic=={lib.fleet_floor}' if lib.fleet_floor
                 else 'meshtastic')
    lib.update_command = f'pip3 install --break-system-packages --upgrade {_lib_spec}'
    lib.install_command = f'pip3 install --break-system-packages {_lib_spec}'
    results['meshtastic_lib'] = lib

    # Node Firmware
    firmware = VersionInfo(name='Node Firmware')
    firmware.installed = get_node_firmware_version()
    firmware.latest = get_latest_firmware_version()
    if firmware.installed and firmware.latest:
        firmware.update_available = compare_versions(firmware.installed, firmware.latest)
    firmware.update_command = 'Use Meshtastic Web Flasher or meshtastic-flasher'
    results['firmware'] = firmware

    return results


def get_version_summary() -> Dict[str, Any]:
    """Get version summary for API/UI consumption"""
    versions = check_all_versions()

    summary = {
        'components': [],
        'updates_available': 0,
        'checked_at': datetime.now().isoformat(),
    }

    for key, info in versions.items():
        component = {
            'id': key,
            'name': info.name,
            'installed': info.installed or 'Not installed',
            'latest': info.latest or 'Unknown',
            'update_available': info.update_available,
            'update_command': info.update_command,
            'held': info.held,
            'notes': info.notes,
            'fleet_floor': info.fleet_floor,
            'pypi_latest': info.pypi_latest,
        }
        summary['components'].append(component)

        if info.update_available:
            summary['updates_available'] += 1

    return summary


if __name__ == '__main__':
    """Test version checker"""
    import pprint

    print("Checking versions...")
    summary = get_version_summary()
    pprint.pprint(summary)
