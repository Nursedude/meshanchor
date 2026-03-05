"""
Automation Handler - TUI menu for auto-ping, auto-traceroute, auto-welcome.

Provides configuration and control for the AutomationEngine's periodic
network monitoring and node greeting tasks.
"""

import logging
from typing import List, Optional, Tuple

from handler_protocol import BaseHandler
from utils.safe_import import safe_import

_AutomationEngine, _HAS_ENGINE = safe_import(
    'utils.automation_engine', 'AutomationEngine'
)
_get_automation_engine, _HAS_GET_ENGINE = safe_import(
    'utils.automation_engine', 'get_automation_engine'
)

logger = logging.getLogger(__name__)


class AutomationHandler(BaseHandler):
    """TUI handler for mesh network automation features."""

    handler_id = "automation"
    menu_section = "mesh_networks"

    def menu_items(self) -> List[Tuple[str, str, Optional[str]]]:
        return [
            ("automation", "Automation       Auto-ping, traceroute, welcome", None),
        ]

    def execute(self, action: str) -> None:
        if action == "automation":
            self._menu_automation()

    def _get_engine(self):
        """Get the automation engine instance."""
        if not _HAS_GET_ENGINE or _get_automation_engine is None:
            self.ctx.dialog.msgbox(
                "Automation Not Available",
                "The automation engine module is not available.",
                height=6, width=50
            )
            return None
        try:
            return _get_automation_engine()
        except Exception as e:
            self.ctx.dialog.msgbox(
                "Automation Error",
                f"Failed to initialize automation engine:\n{e}",
                height=8, width=55
            )
            return None

    def _menu_automation(self) -> None:
        """Automation menu — configure and control automated tasks."""
        engine = self._get_engine()
        if not engine:
            return

        while True:
            status = engine.get_status()
            running = status.get("running", False)
            active = status.get("active_threads", [])
            state_str = f"RUNNING ({', '.join(active)})" if running and active else "STOPPED"

            choice = self.ctx.dialog.menu(
                "Automation",
                f"MeshMonitor-inspired automation tasks\n"
                f"Status: {state_str}",
                choices=[
                    ("1", "Status & Statistics    - Current automation state"),
                    ("2", "Configure Auto-Ping   - Periodic node pinging"),
                    ("3", "Configure Traceroute  - Periodic route tracing"),
                    ("4", "Configure Welcome     - Greet new nodes"),
                    ("5", "Start Automation      - Launch enabled tasks"),
                    ("6", "Stop Automation       - Stop all tasks"),
                ],
                height=16, width=62
            )

            if not choice:
                return

            dispatch = {
                "1": ("Status", self._show_status),
                "2": ("Auto-Ping Config", self._configure_ping),
                "3": ("Auto-Traceroute Config", self._configure_traceroute),
                "4": ("Auto-Welcome Config", self._configure_welcome),
                "5": ("Start Automation", self._start_automation),
                "6": ("Stop Automation", self._stop_automation),
            }
            entry = dispatch.get(choice)
            if entry:
                self.ctx.safe_call(*entry)

    def _show_status(self) -> None:
        """Show automation status and statistics."""
        engine = self._get_engine()
        if not engine:
            return

        status = engine.get_status()
        stats = status.get("stats", {})
        config = status.get("config", {})

        ping_cfg = config.get("auto_ping", {})
        trace_cfg = config.get("auto_traceroute", {})
        welcome_cfg = config.get("auto_welcome", {})

        lines = [
            f"Engine: {'RUNNING' if status.get('running') else 'STOPPED'}",
            f"Active: {', '.join(status.get('active_threads', [])) or 'none'}",
            "",
            "=== Auto-Ping ===",
            f"  Enabled:  {ping_cfg.get('enabled', False)}",
            f"  Targets:  {len(ping_cfg.get('targets', []))}",
            f"  Interval: {ping_cfg.get('interval_minutes', 15)} min",
            f"  Sent:     {stats.get('pings_sent', 0)} "
            f"(OK: {stats.get('pings_success', 0)}, "
            f"Fail: {stats.get('pings_failed', 0)})",
            f"  Last:     {stats.get('last_ping_cycle', 'never')}",
            "",
            "=== Auto-Traceroute ===",
            f"  Enabled:  {trace_cfg.get('enabled', False)}",
            f"  Targets:  {len(trace_cfg.get('targets', []))}",
            f"  Interval: {trace_cfg.get('interval_minutes', 60)} min",
            f"  Sent:     {stats.get('traceroutes_sent', 0)} "
            f"(OK: {stats.get('traceroutes_success', 0)}, "
            f"Fail: {stats.get('traceroutes_failed', 0)})",
            f"  Last:     {stats.get('last_traceroute_cycle', 'never')}",
            "",
            "=== Auto-Welcome ===",
            f"  Enabled:  {welcome_cfg.get('enabled', False)}",
            f"  Message:  {welcome_cfg.get('message', 'N/A')[:40]}",
            f"  Cooldown: {welcome_cfg.get('cooldown_hours', 24)}h",
            f"  Sent:     {stats.get('welcomes_sent', 0)}",
            f"  Last:     {stats.get('last_welcome_check', 'never')}",
        ]

        self.ctx.dialog.msgbox(
            "Automation Status",
            "\n".join(lines),
            height=28, width=60
        )

    def _configure_ping(self) -> None:
        """Configure auto-ping settings."""
        engine = self._get_engine()
        if not engine:
            return

        settings = engine.get_settings()
        ping_cfg = settings.get("auto_ping", {})

        choice = self.ctx.dialog.menu(
            "Auto-Ping Configuration",
            f"Currently: {'ENABLED' if ping_cfg.get('enabled') else 'DISABLED'}",
            choices=[
                ("1", f"Toggle    - {'Disable' if ping_cfg.get('enabled') else 'Enable'} auto-ping"),
                ("2", f"Interval  - Currently {ping_cfg.get('interval_minutes', 15)} min"),
                ("3", f"Targets   - {len(ping_cfg.get('targets', []))} nodes configured"),
            ],
            height=12, width=55
        )

        if choice == "1":
            ping_cfg["enabled"] = not ping_cfg.get("enabled", False)
            settings.set("auto_ping", ping_cfg)
            settings.save()
            state = "enabled" if ping_cfg["enabled"] else "disabled"
            self.ctx.dialog.msgbox("Auto-Ping", f"Auto-ping {state}.", height=6, width=35)

        elif choice == "2":
            val = self.ctx.dialog.inputbox(
                "Ping Interval",
                "Enter interval in minutes (1-1440):",
                init=str(ping_cfg.get("interval_minutes", 15)),
                height=8, width=45
            )
            if val:
                try:
                    minutes = max(1, min(1440, int(val)))
                    ping_cfg["interval_minutes"] = minutes
                    settings.set("auto_ping", ping_cfg)
                    settings.save()
                except ValueError:
                    self.ctx.dialog.msgbox("Error", "Invalid number.", height=6, width=30)

        elif choice == "3":
            self._edit_targets("auto_ping", "Ping")

    def _configure_traceroute(self) -> None:
        """Configure auto-traceroute settings."""
        engine = self._get_engine()
        if not engine:
            return

        settings = engine.get_settings()
        trace_cfg = settings.get("auto_traceroute", {})

        choice = self.ctx.dialog.menu(
            "Auto-Traceroute Configuration",
            f"Currently: {'ENABLED' if trace_cfg.get('enabled') else 'DISABLED'}",
            choices=[
                ("1", f"Toggle    - {'Disable' if trace_cfg.get('enabled') else 'Enable'} auto-traceroute"),
                ("2", f"Interval  - Currently {trace_cfg.get('interval_minutes', 60)} min"),
                ("3", f"Targets   - {len(trace_cfg.get('targets', []))} nodes configured"),
            ],
            height=12, width=58
        )

        if choice == "1":
            trace_cfg["enabled"] = not trace_cfg.get("enabled", False)
            settings.set("auto_traceroute", trace_cfg)
            settings.save()
            state = "enabled" if trace_cfg["enabled"] else "disabled"
            self.ctx.dialog.msgbox("Auto-Traceroute", f"Auto-traceroute {state}.", height=6, width=40)

        elif choice == "2":
            val = self.ctx.dialog.inputbox(
                "Traceroute Interval",
                "Enter interval in minutes (5-1440):",
                init=str(trace_cfg.get("interval_minutes", 60)),
                height=8, width=45
            )
            if val:
                try:
                    minutes = max(5, min(1440, int(val)))
                    trace_cfg["interval_minutes"] = minutes
                    settings.set("auto_traceroute", trace_cfg)
                    settings.save()
                except ValueError:
                    self.ctx.dialog.msgbox("Error", "Invalid number.", height=6, width=30)

        elif choice == "3":
            self._edit_targets("auto_traceroute", "Traceroute")

    def _configure_welcome(self) -> None:
        """Configure auto-welcome settings."""
        engine = self._get_engine()
        if not engine:
            return

        settings = engine.get_settings()
        welcome_cfg = settings.get("auto_welcome", {})

        choice = self.ctx.dialog.menu(
            "Auto-Welcome Configuration",
            f"Currently: {'ENABLED' if welcome_cfg.get('enabled') else 'DISABLED'}",
            choices=[
                ("1", f"Toggle     - {'Disable' if welcome_cfg.get('enabled') else 'Enable'} auto-welcome"),
                ("2", f"Message    - Edit welcome message"),
                ("3", f"Cooldown   - Currently {welcome_cfg.get('cooldown_hours', 24)}h"),
            ],
            height=12, width=55
        )

        if choice == "1":
            welcome_cfg["enabled"] = not welcome_cfg.get("enabled", False)
            settings.set("auto_welcome", welcome_cfg)
            settings.save()
            state = "enabled" if welcome_cfg["enabled"] else "disabled"
            self.ctx.dialog.msgbox("Auto-Welcome", f"Auto-welcome {state}.", height=6, width=38)

        elif choice == "2":
            val = self.ctx.dialog.inputbox(
                "Welcome Message",
                "Enter the message to send to new nodes:",
                init=welcome_cfg.get("message", "Welcome to the mesh!"),
                height=8, width=55
            )
            if val:
                welcome_cfg["message"] = val
                settings.set("auto_welcome", welcome_cfg)
                settings.save()

        elif choice == "3":
            val = self.ctx.dialog.inputbox(
                "Welcome Cooldown",
                "Hours before re-welcoming a node (1-168):",
                init=str(welcome_cfg.get("cooldown_hours", 24)),
                height=8, width=45
            )
            if val:
                try:
                    hours = max(1, min(168, int(val)))
                    welcome_cfg["cooldown_hours"] = hours
                    settings.set("auto_welcome", welcome_cfg)
                    settings.save()
                except ValueError:
                    self.ctx.dialog.msgbox("Error", "Invalid number.", height=6, width=30)

    def _edit_targets(self, config_key: str, label: str) -> None:
        """Edit target node list for ping or traceroute."""
        engine = self._get_engine()
        if not engine:
            return

        settings = engine.get_settings()
        cfg = settings.get(config_key, {})
        targets = cfg.get("targets", [])

        current = "\n".join(targets) if targets else "(none)"
        val = self.ctx.dialog.inputbox(
            f"{label} Targets",
            f"Enter node IDs (comma-separated, e.g. !abc123,!def456):\n\n"
            f"Current: {current}",
            init=",".join(targets),
            height=12, width=60
        )

        if val is not None:
            new_targets = [
                t.strip() for t in val.split(",")
                if t.strip()
            ]
            cfg["targets"] = new_targets
            settings.set(config_key, cfg)
            settings.save()
            self.ctx.dialog.msgbox(
                f"{label} Targets Updated",
                f"Set {len(new_targets)} target(s).",
                height=6, width=35
            )

    def _start_automation(self) -> None:
        """Start all enabled automation tasks."""
        engine = self._get_engine()
        if not engine:
            return

        if engine.is_alive():
            self.ctx.dialog.msgbox(
                "Already Running",
                "Automation engine is already running.",
                height=6, width=42
            )
            return

        started = engine.start()
        if started:
            status = engine.get_status()
            active = status.get("active_threads", [])
            self.ctx.dialog.msgbox(
                "Automation Started",
                f"Running tasks: {', '.join(active)}",
                height=7, width=50
            )
        else:
            self.ctx.dialog.msgbox(
                "No Tasks Enabled",
                "Enable at least one automation task first\n"
                "(auto-ping, auto-traceroute, or auto-welcome).",
                height=8, width=50
            )

    def _stop_automation(self) -> None:
        """Stop all automation tasks."""
        engine = self._get_engine()
        if not engine:
            return

        if not engine.is_alive():
            self.ctx.dialog.msgbox(
                "Not Running",
                "Automation engine is not currently running.",
                height=6, width=44
            )
            return

        engine.stop()
        self.ctx.dialog.msgbox(
            "Automation Stopped",
            "All automation tasks have been stopped.",
            height=6, width=44
        )
