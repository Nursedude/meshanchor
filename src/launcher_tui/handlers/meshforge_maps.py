"""Meshforge Maps Handler — discovery + browser-launch for the :8808 service.

Phase 6 scaffold. meshforge-maps is a sister project that runs its own HTTP
server on :8808 with `/api/status`, `/api/health`, `/api/sources`. MeshAnchor
discovers it (via `utils.meshforge_maps_client.MeshforgeMapsClient`) and
surfaces two affordances:

    Status            — probe :8808 and render version, health, sources
    Open in Browser   — webbrowser.open the Leaflet UI

The handler does NOT manage lifecycle (start/stop) — meshforge-maps owns its
own systemd unit. If the service is unreachable, Status renders a fix hint
and the install URL.

Lives in the `maps_viz` section so it's gated by the existing `maps` feature
flag at the section level (matches `ai_tools` and `topology`). No per-row
flag needed.
"""

import logging
import webbrowser

from handler_protocol import BaseHandler

from utils.meshforge_maps_client import (
    DEFAULT_HOST,
    DEFAULT_PORT,
    MeshforgeMapsClient,
    MapsServiceStatus,
)

logger = logging.getLogger(__name__)

INSTALL_HINT = "https://github.com/Nursedude/meshforge-maps"


class MeshforgeMapsHandler(BaseHandler):
    """Meshforge Maps — discovery + browser launch for :8808."""

    handler_id = "meshforge_maps"
    menu_section = "maps_viz"

    def menu_items(self):
        return [
            ("mf_status", "Meshforge Maps      Service status (:8808)", None),
            ("mf_open",   "Open Maps Browser   Launch :8808 in browser", None),
        ]

    def execute(self, action):
        dispatch = {
            "mf_status": ("Meshforge Maps Status", self._show_status),
            "mf_open": ("Open Meshforge Maps", self._open_browser),
        }
        entry = dispatch.get(action)
        if entry:
            self.ctx.safe_call(*entry)

    def _client(self) -> MeshforgeMapsClient:
        """Construct a client. Hardcodes localhost:8808 today; if/when
        meshforge-maps grows a remote-host config, plumb it here."""
        return MeshforgeMapsClient(host=DEFAULT_HOST, port=DEFAULT_PORT)

    def _show_status(self):
        """Probe :8808 and render the snapshot."""
        from backend import clear_screen

        clear_screen()
        client = self._client()
        print("=== Meshforge Maps ===\n")
        print(f"Probing {client.web_url} ...")
        status = client.probe()
        print()
        print(_format_status(status))
        print()
        try:
            self.ctx.wait_for_enter("Press Enter to return to menu...")
        except KeyboardInterrupt:
            print()

    def _open_browser(self):
        """webbrowser.open the :8808 Leaflet UI."""
        from backend import clear_screen

        clear_screen()
        client = self._client()
        url = client.web_url
        print(f"Opening {url} ...\n")
        try:
            webbrowser.open(url)
            print("Browser launched.")
            print("(If nothing happened, your environment may lack a default browser.)")
        except webbrowser.Error as e:
            print(f"Could not launch browser: {e}")
            print(f"Open manually: {url}")
        try:
            self.ctx.wait_for_enter("\nPress Enter to return to menu...")
        except KeyboardInterrupt:
            print()


def _format_status(status: MapsServiceStatus) -> str:
    """Render a MapsServiceStatus as a multi-line block. Pure function so
    it's easy to unit-test without standing up a full TUI handler."""
    if not status.available:
        return (
            f"  meshforge-maps: NOT REACHABLE\n"
            f"    {status.error or 'unknown error'}\n"
            f"  Fix:  install or start meshforge-maps\n"
            f"        {INSTALL_HINT}\n"
            f"        sudo systemctl start meshforge-maps"
        )

    lines = [
        f"  meshforge-maps: RUNNING",
        f"    URL:     http://{status.host}:{status.port}",
    ]
    if status.version:
        lines.append(f"    Version: {status.version}")
    if status.health_score is not None:
        lines.append(f"    Health:  {status.health_score}/100")
    if status.sources:
        lines.append(f"    Sources: {', '.join(status.sources)}")
    if status.uptime_seconds is not None:
        lines.append(f"    Uptime:  {_format_uptime(status.uptime_seconds)}")
    return "\n".join(lines)


def _format_uptime(seconds: float) -> str:
    """Compact uptime for display, e.g. '3d 4h' or '12m'."""
    s = int(seconds)
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m"
    if s < 86400:
        return f"{s // 3600}h {(s % 3600) // 60}m"
    return f"{s // 86400}d {(s % 86400) // 3600}h"
