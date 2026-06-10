"""Status endpoint mixin for :class:`MapRequestHandler`.

Holds the ``/api/status`` surface — the server health rollup that the
federation poll cycle and dashboards consume — plus its private reader:

- ``_serve_status``             — ``/api/status`` (history/directory stats,
                                  response-cache stats blocks, radio)
- ``_get_radio_status_summary`` — TCP/USB radio connectivity summary
                                  (MeshCore-aware: excludes the device
                                  claimed via /dev/ttyMeshCore)

The cache-stats shapes surfaced under ``status["geojson"]["cache"]``,
``status["topology"]["cache"]`` and ``status["directory_cache"]["cache"]``
are test-pinned (``tests/test_response_byte_cache.py``) — do not alter
them here.

Extracted from ``map_http_handler.py`` to keep that file under the
1,500-line size cap (``CLAUDE.md``). No behaviour change — methods are
mixed into ``MapRequestHandler`` via inheritance.

Mirrors MeshForge's ``_map_status_endpoints.py`` boundary (MA's status
block is leaner — no watchdog/mini-dudeai/radio-config stitches yet).
"""

import json
import logging
from datetime import datetime
from typing import Any, Dict

logger = logging.getLogger(__name__)

from utils.safe_import import safe_import

_get_connection_manager, _ConnectionMode, _HAS_MESHTASTIC_CONN = safe_import(
    'utils.meshtastic_connection', 'get_connection_manager', 'ConnectionMode'
)


class StatusEndpointsMixin:
    """``/api/status`` endpoint + its radio connectivity reader."""

    def _serve_status(self):
        """Serve server status including radio connection info."""
        status = {
            "status": "running",
            "time": datetime.now().isoformat(),
            "collector": self.collector is not None,
        }

        # Include history stats if available
        if self.collector and self.collector._history:
            try:
                status["history"] = self.collector._history.get_stats()
            except Exception:
                status["history"] = None

            # Directory stats (Issue #49) — persistent per-node cache
            # across protocols, with tiered retention. Surfaces total
            # count, by-network, by-source-origin, last-seen range so
            # operators can see at a glance how many MeshCore/AREDN/RNS
            # nodes are cached and which retention tier they fall into.
            try:
                status["directory"] = self.collector._history.get_directory_stats()
            except Exception as e:
                logger.debug(f"directory stats lookup failed: {e}")
                status["directory"] = None

        # Include radio connection status
        status["radio"] = self._get_radio_status_summary()

        # Response-cache observability (Issues #70/#71). Surfaces hit/miss/
        # coalesced counts per heavy endpoint so operators can confirm the
        # caches are coalescing work (and spot a regression where an endpoint
        # stops calling get_or_build, or a TTL too short to coalesce).
        if self.collector:
            for stat_key, attr in (
                ("geojson", "_geojson_response_cache"),
                ("topology", "_topology_response_cache"),
                ("directory_cache", "_directory_response_cache"),
            ):
                try:
                    cache = getattr(self.collector, attr, None)
                    if cache is not None:
                        status[stat_key] = {
                            "cache": {**cache.stats(), "ttl_s": cache.ttl_s}
                        }
                except Exception as e:
                    logger.debug(f"{stat_key} cache stats lookup failed: {e}")

        data = json.dumps(status).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self._send_cors_header()
        self.end_headers()
        self.wfile.write(data)

    def _get_radio_status_summary(self) -> Dict[str, Any]:
        """Get a summary of radio connection status for the status endpoint."""
        if not _HAS_MESHTASTIC_CONN:
            return {"available": False, "error": "meshtastic library not installed"}

        # Check TCP port (meshtasticd)
        tcp_available = False
        try:
            import socket
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.settimeout(1)
                tcp_available = sock.connect_ex(('localhost', 4403)) == 0
        except Exception as e:
            logger.debug(f"TCP port check failed: {e}")

        # Check USB serial device. Exclude any device claimed by MeshCore
        # via the persistent /dev/ttyMeshCore symlink (created by
        # scripts/99-meshcore.rules). Without this filter, on a MeshCore-
        # primary host the glob returns the RAK4631's /dev/ttyACM0 and we
        # report mode='serial' as if a Meshtastic radio were attached.
        import glob
        import os
        excluded: set = set()
        if os.path.exists('/dev/ttyMeshCore'):
            try:
                excluded.add(os.path.realpath('/dev/ttyMeshCore'))
            except OSError:
                pass
        usb_devices = [
            d for d in (glob.glob('/dev/ttyUSB*') + glob.glob('/dev/ttyACM*'))
            if os.path.realpath(d) not in excluded
        ]
        usb_available = len(usb_devices) > 0

        # Determine connection mode
        if tcp_available:
            mode = "tcp"
            connected = True
        elif usb_available:
            mode = "serial"
            connected = True
        else:
            mode = "none"
            connected = False

        return {
            "connected": connected,
            "mode": mode,
            "tcp_available": tcp_available,
            "usb_available": usb_available,
            "usb_devices": usb_devices if usb_available else [],
        }
