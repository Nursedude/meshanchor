"""Meshforge Maps Handler — discovery + browser-launch for the :8808 service.

Phase 6 scaffold + Phase 6.3 endpoint config. meshforge-maps is a sister
project that runs its own HTTP server on :8808 with `/api/status`,
`/api/health`, `/api/sources`. MeshAnchor discovers it (via
:class:`utils.meshforge_maps_client.MeshforgeMapsClient`) and surfaces:

    Status            — probe :8808 and render version, health, sources
    Open in Browser   — webbrowser.open the Leaflet UI
    Configure Endpoint — edit host / port / timeout (Phase 6.3)

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
    DEFAULT_TIMEOUT,
    MeshforgeMapsClient,
    MapsServiceStatus,
)
from utils.meshforge_maps_config import (
    MapsConfig,
    MapsConfigError,
    load_maps_config,
    reset_maps_config,
    save_maps_config,
)

logger = logging.getLogger(__name__)

INSTALL_HINT = "https://github.com/Nursedude/meshforge-maps"


class MeshforgeMapsHandler(BaseHandler):
    """Meshforge Maps — discovery + browser launch for :8808."""

    handler_id = "meshforge_maps"
    menu_section = "maps_viz"

    def menu_items(self):
        return [
            ("mf_status",   "Meshforge Maps      Service status",          None),
            ("mf_open",     "Open Maps Browser   Launch UI in browser",    None),
            ("mf_endpoint", "Maps Endpoint       Configure host/port",     None),
        ]

    def execute(self, action):
        dispatch = {
            "mf_status":   ("Meshforge Maps Status",   self._show_status),
            "mf_open":     ("Open Meshforge Maps",     self._open_browser),
            "mf_endpoint": ("Configure Maps Endpoint", self._configure_endpoint),
        }
        entry = dispatch.get(action)
        if entry:
            self.ctx.safe_call(*entry)

    def _client(self) -> MeshforgeMapsClient:
        """Build a probe client from the persisted endpoint config.

        Falls back transparently to the Phase 6 defaults
        (``localhost:8808`` / ``timeout=3.0``) if no settings file exists.
        """
        return load_maps_config().build_client()

    def _show_status(self):
        """Probe the configured endpoint and render the snapshot."""
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
        """webbrowser.open the configured endpoint."""
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

    def _configure_endpoint(self):
        """Prompt for host / port / timeout and persist via SettingsManager."""
        cfg = load_maps_config()
        dialog = self.ctx.dialog

        while True:
            choice = dialog.menu(
                "Meshforge Maps Endpoint",
                f"Currently: http://{cfg.host}:{cfg.port}  (timeout {cfg.timeout}s)\n\n"
                "Pick a field to edit, or Reset to restore defaults.",
                [
                    ("host",    f"Host       [{cfg.host}]"),
                    ("port",    f"Port       [{cfg.port}]"),
                    ("timeout", f"Timeout    [{cfg.timeout}s]"),
                    ("reset",   f"Reset      Restore localhost:{DEFAULT_PORT}"),
                    ("back",    "Back       Return to maps menu"),
                ],
            )
            if not choice or choice == "back":
                return

            if choice == "host":
                cfg = self._prompt_host(cfg) or cfg
            elif choice == "port":
                cfg = self._prompt_port(cfg) or cfg
            elif choice == "timeout":
                cfg = self._prompt_timeout(cfg) or cfg
            elif choice == "reset":
                if dialog.yesno(
                    "Reset Maps Endpoint",
                    f"Reset to {DEFAULT_HOST}:{DEFAULT_PORT} "
                    f"(timeout {DEFAULT_TIMEOUT}s)?",
                ):
                    cfg = reset_maps_config()
                    dialog.msgbox("Reset", "Endpoint restored to defaults.")

    def _prompt_host(self, cfg: MapsConfig):
        new_host = self.ctx.dialog.inputbox(
            "Maps Host",
            "Enter hostname or IP for meshforge-maps:",
            cfg.host,
        )
        if not new_host:
            return None
        try:
            return save_maps_config(host=new_host.strip())
        except MapsConfigError as e:
            self.ctx.dialog.msgbox("Invalid Host", str(e))
            return None

    def _prompt_port(self, cfg: MapsConfig):
        new_port = self.ctx.dialog.inputbox(
            "Maps Port",
            "Enter HTTP port for meshforge-maps (1-65535):",
            str(cfg.port),
        )
        if not new_port:
            return None
        try:
            return save_maps_config(port=int(new_port.strip()))
        except (ValueError, MapsConfigError) as e:
            self.ctx.dialog.msgbox("Invalid Port", str(e))
            return None

    def _prompt_timeout(self, cfg: MapsConfig):
        new_timeout = self.ctx.dialog.inputbox(
            "Maps Probe Timeout",
            "Enter probe timeout in seconds (e.g. 3.0):",
            str(cfg.timeout),
        )
        if not new_timeout:
            return None
        try:
            return save_maps_config(timeout=float(new_timeout.strip()))
        except (ValueError, MapsConfigError) as e:
            self.ctx.dialog.msgbox("Invalid Timeout", str(e))
            return None


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
