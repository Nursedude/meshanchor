"""Meshforge Maps Handler — discovery + lifecycle for the :8808 service.

Phase 6 scaffold + Phase 6.3 endpoint config + Phase 6.2 lifecycle.
meshforge-maps is a sister project that runs its own HTTP server on :8808
with `/api/status`, `/api/health`, `/api/sources`. MeshAnchor discovers it
(via :class:`utils.meshforge_maps_client.MeshforgeMapsClient`) and surfaces:

    Status             — probe :8808 and render version, health, sources
    Open in Browser    — webbrowser.open the Leaflet UI
    Configure Endpoint — edit host / port / timeout (Phase 6.3)
    Lifecycle          — start / stop / restart the systemd unit (6.2)

Lifecycle is the only path that mutates host state. It's gated by:

* **Localhost only** — refuses if Phase 6.3 has the endpoint pointed at a
  remote host (``sudo systemctl`` can't cross machines).
* **Explicit double-confirm** — every state change requires two ``yesno``
  dialogs in a row. Mirrors Phase 4b radio writes' guard pattern.
* **Issue #31** — actions never fire from a daemon loop or auto-recovery
  path; only from a deliberate menu choice.

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
from utils.meshforge_maps_lifecycle import (
    INSTALL_HINT_URL,
    LifecycleResult,
    SYSTEMD_UNIT,
    UnitStatus,
    format_unit_status,
    get_unit_status,
    is_localhost,
    is_unit_installed,
    restart as lifecycle_restart,
    start as lifecycle_start,
    stop as lifecycle_stop,
)

logger = logging.getLogger(__name__)

INSTALL_HINT = "https://github.com/Nursedude/meshforge-maps"


class MeshforgeMapsHandler(BaseHandler):
    """Meshforge Maps — discovery + browser launch for :8808."""

    handler_id = "meshforge_maps"
    menu_section = "maps_viz"

    def menu_items(self):
        return [
            ("mf_status",    "Meshforge Maps      Service status",          None),
            ("mf_open",      "Open Maps Browser   Launch UI in browser",    None),
            ("mf_endpoint",  "Maps Endpoint       Configure host/port",     None),
            ("mf_lifecycle", "Maps Lifecycle      Start / stop / restart",  None),
        ]

    def execute(self, action):
        dispatch = {
            "mf_status":    ("Meshforge Maps Status",    self._show_status),
            "mf_open":      ("Open Meshforge Maps",      self._open_browser),
            "mf_endpoint":  ("Configure Maps Endpoint",  self._configure_endpoint),
            "mf_lifecycle": ("Maps Lifecycle Control",   self._lifecycle_menu),
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

    # ─────────────────────────────────────────────────────────────────
    # Phase 6.2 — Lifecycle (start / stop / restart)
    # ─────────────────────────────────────────────────────────────────

    def _lifecycle_menu(self):
        """Sub-submenu for systemd unit control.

        Refuses early if the configured endpoint is remote (Issue #31 spirit
        — we don't reach across hosts) or if the unit isn't installed
        locally. Otherwise shows Start / Stop / Restart / Show Status with
        each mutating action gated behind a confirm dialog + post-action
        re-probe so the user immediately sees the new state.
        """
        cfg = load_maps_config()
        dialog = self.ctx.dialog

        if not is_localhost(cfg.host):
            dialog.msgbox(
                "Lifecycle Unavailable",
                f"Maps endpoint is configured for {cfg.host!r}, which is not "
                f"this machine.\n\nLifecycle control (start/stop/restart) is "
                f"local-only because it requires sudo on the host that runs "
                f"the meshforge-maps systemd unit.\n\n"
                f"To control a remote install, log into that host and use "
                f"its own NOC, or run:\n"
                f"  ssh {cfg.host} sudo systemctl restart {SYSTEMD_UNIT}",
            )
            return

        installed, install_err = is_unit_installed()
        if install_err is not None:
            dialog.msgbox(
                "Cannot Detect Unit",
                f"Could not query systemctl for {SYSTEMD_UNIT}.service:\n\n"
                f"{install_err}\n\n"
                f"Lifecycle control requires a working systemd host.",
            )
            return
        if not installed:
            dialog.msgbox(
                "Unit Not Installed",
                f"{SYSTEMD_UNIT}.service is not installed on this host.\n\n"
                f"Install meshforge-maps first:\n"
                f"  {INSTALL_HINT_URL}\n\n"
                f"Once installed, return here to start/stop/restart it.",
            )
            return

        while True:
            unit = get_unit_status()
            choice = dialog.menu(
                "Maps Lifecycle Control",
                f"Endpoint: http://{cfg.host}:{cfg.port}\n"
                f"Unit:     {SYSTEMD_UNIT}.service\n"
                f"Active:   {_unit_active_label(unit)}\n\n"
                "Pick an action:",
                [
                    ("start",   "Start      systemctl start"),
                    ("stop",    "Stop       systemctl stop"),
                    ("restart", "Restart    systemctl restart"),
                    ("status",  "Show systemd status"),
                    ("back",    "Back       Return to maps menu"),
                ],
            )
            if not choice or choice == "back":
                return
            if choice == "status":
                self._show_unit_status()
            elif choice == "start":
                self._lifecycle_action("start", lifecycle_start, cfg)
            elif choice == "stop":
                self._lifecycle_action("stop", lifecycle_stop, cfg)
            elif choice == "restart":
                self._lifecycle_action("restart", lifecycle_restart, cfg)

    def _lifecycle_action(self, action: str, fn, cfg: MapsConfig) -> None:
        """Confirm + invoke a lifecycle helper + render the result.

        The confirm dialog defaults to NO so an accidental Enter doesn't
        fire a state change. After the action runs, we re-probe so the user
        sees the new active/enabled state without re-entering the menu.
        """
        dialog = self.ctx.dialog
        verb = action.capitalize()
        if not dialog.yesno(
            f"{verb} meshforge-maps?",
            f"This will run:\n"
            f"  sudo systemctl {action} {SYSTEMD_UNIT}\n\n"
            f"Continue?",
            default_no=True,
        ):
            return

        result: LifecycleResult = fn(host=cfg.host)
        title = f"{verb} {'Succeeded' if result.success else 'Failed'}"
        if result.success:
            unit_after = get_unit_status()
            message = (
                f"{result.message}\n\n"
                f"Unit state after action:\n"
                f"{format_unit_status(unit_after)}"
            )
        else:
            message = result.message
        dialog.msgbox(title, message)

    def _show_unit_status(self) -> None:
        """Render the current systemd unit status in a msgbox."""
        unit = get_unit_status()
        self.ctx.dialog.msgbox(
            f"{SYSTEMD_UNIT}.service Status",
            format_unit_status(unit),
        )


def _unit_active_label(unit: UnitStatus) -> str:
    """One-line label for the lifecycle menu header."""
    if not unit.installed:
        return "not installed"
    if unit.active is None:
        return "unknown"
    return "active" if unit.active else "inactive"


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
