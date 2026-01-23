"""
TUI Status Bar — persistent status line for whiptail/dialog --backtitle.

Collects and caches service status information to display a compact
status line at the top of every TUI dialog screen. Designed to be
fast (cached with TTL) since it runs on every menu interaction.

Usage:
    from status_bar import StatusBar

    bar = StatusBar()
    text = bar.get_status_line()
    # Returns: "MeshForge v0.4.7 | meshtasticd: ● | rnsd: ○ | mqtt: ○"
"""

import time
import subprocess
import logging
from typing import Dict, Optional

logger = logging.getLogger(__name__)

# Cache TTL in seconds — how often to re-check service status
STATUS_CACHE_TTL = 10.0

# Services to monitor
MONITORED_SERVICES = [
    ('meshtasticd', 'mesh'),
    ('rnsd', 'rns'),
    ('mosquitto', 'mqtt'),
]

# Status symbols (ASCII-safe for all terminals)
SYM_RUNNING = '*'
SYM_STOPPED = '-'
SYM_UNKNOWN = '?'


class StatusBar:
    """Collects and formats service status for the TUI backtitle.

    Caches results to avoid calling systemctl on every dialog render.
    """

    def __init__(self, version: str = ""):
        """Initialize status bar.

        Args:
            version: MeshForge version string to display.
        """
        self.version = version
        self._cache: Dict[str, str] = {}
        self._cache_time: float = 0.0
        self._node_count: Optional[int] = None
        self._bridge_running: Optional[bool] = None

    def get_status_line(self) -> str:
        """Get the formatted status line for --backtitle.

        Returns:
            Compact status string suitable for terminal top bar.
        """
        self._refresh_if_stale()

        parts = []

        # Version
        if self.version:
            parts.append(f"MeshForge v{self.version}")
        else:
            parts.append("MeshForge")

        # Service statuses
        for service_name, short_name in MONITORED_SERVICES:
            status = self._cache.get(service_name, SYM_UNKNOWN)
            parts.append(f"{short_name}:{status}")

        # Node count if available
        if self._node_count is not None:
            parts.append(f"nodes:{self._node_count}")

        # Bridge status
        if self._bridge_running is not None:
            bridge_sym = SYM_RUNNING if self._bridge_running else SYM_STOPPED
            parts.append(f"bridge:{bridge_sym}")

        return " | ".join(parts)

    def _refresh_if_stale(self) -> None:
        """Refresh cache if TTL has expired."""
        now = time.time()
        if now - self._cache_time < STATUS_CACHE_TTL:
            return

        self._cache_time = now
        self._check_services()
        self._check_bridge()

    def _check_services(self) -> None:
        """Check status of all monitored services."""
        for service_name, _ in MONITORED_SERVICES:
            self._cache[service_name] = self._check_systemd_active(service_name)

    def _check_systemd_active(self, service: str) -> str:
        """Check if a systemd service is active.

        Args:
            service: Service unit name.

        Returns:
            Status symbol character.
        """
        try:
            result = subprocess.run(
                ['systemctl', 'is-active', service],
                capture_output=True, text=True, timeout=3
            )
            if result.stdout.strip() == 'active':
                return SYM_RUNNING
            return SYM_STOPPED
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            return SYM_UNKNOWN

    def _check_bridge(self) -> None:
        """Check if the RNS-Meshtastic bridge process is running."""
        try:
            result = subprocess.run(
                ['pgrep', '-f', 'rns_bridge'],
                capture_output=True, timeout=3
            )
            self._bridge_running = result.returncode == 0
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            self._bridge_running = None

    def set_node_count(self, count: int) -> None:
        """Update the displayed node count.

        Args:
            count: Number of known mesh nodes.
        """
        self._node_count = count

    def invalidate(self) -> None:
        """Force refresh on next access."""
        self._cache_time = 0.0

    def get_service_status(self, service_name: str) -> str:
        """Get cached status symbol for a service.

        Args:
            service_name: Systemd service name.

        Returns:
            Status symbol character.
        """
        self._refresh_if_stale()
        return self._cache.get(service_name, SYM_UNKNOWN)
