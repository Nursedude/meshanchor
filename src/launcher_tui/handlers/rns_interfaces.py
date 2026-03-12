"""
RNS Interfaces Handler — RNS network interface CRUD management.

Converted from rns_interfaces_mixin.py as part of the mixin-to-registry migration.
"""

import re
import logging

from handler_protocol import BaseHandler
from backend import clear_screen
from commands import rns as rns_mod

logger = logging.getLogger(__name__)


class RNSInterfacesHandler(BaseHandler):
    """TUI handler for RNS interface management."""

    handler_id = "rns_interfaces"
    menu_section = "rns"

    def menu_items(self):
        return [
            ("ifaces", "Manage Interfaces", None),
        ]

    def execute(self, action):
        if action == "ifaces":
            self._rns_interfaces_menu()

    # ------------------------------------------------------------------
    # Top-level submenu
    # ------------------------------------------------------------------

    def _rns_interfaces_menu(self):
        """Manage RNS interfaces (add / remove / enable / disable)."""
        while True:
            choices = [
                ("status", "Interface Status (live)"),
                ("list", "List Configured Interfaces"),
                ("add", "Add Interface from Template"),
                ("enable", "Enable Interface"),
                ("disable", "Disable Interface"),
                ("remove", "Remove Interface"),
                ("plugin", "Install Meshtastic Plugin"),
                ("back", "Back"),
            ]

            choice = self.ctx.dialog.menu(
                "RNS Interfaces",
                "Manage Reticulum network interfaces:",
                choices,
            )

            if choice is None or choice == "back":
                break

            if choice == "plugin":
                # Cross-handler call to config handler for plugin install
                config_handler = self.ctx.registry.get_handler("rns_config") if self.ctx.registry else None
                if config_handler:
                    self.ctx.safe_call("Install Meshtastic Plugin", config_handler._install_meshtastic_interface_plugin)
                else:
                    self.ctx.dialog.msgbox("Error", "RNS config handler not available.")
                continue

            dispatch = {
                "status": ("Interface Status", self._rns_interface_status),
                "list": ("List Interfaces", self._rns_list_interfaces),
                "add": ("Add Interface", self._rns_add_interface),
                "enable": ("Enable Interface", lambda: self._rns_toggle_interface(enable=True)),
                "disable": ("Disable Interface", lambda: self._rns_toggle_interface(enable=False)),
                "remove": ("Remove Interface", self._rns_remove_interface),
            }
            entry = dispatch.get(choice)
            if entry:
                self.ctx.safe_call(*entry)

    # ------------------------------------------------------------------
    # Interface Status (live)
    # ------------------------------------------------------------------

    def _rns_interface_status(self):
        """Show live interface status: config + blocking reasons + TX/RX."""
        import subprocess
        from pathlib import Path
        from ._rns_interface_mgr import find_blocking_interfaces
        from ._rns_diagnostics_engine import check_rns_interface_health
        from utils.service_check import (
            check_process_running, get_rns_shared_instance_info,
            get_udp_port_owner,
        )

        clear_screen()
        print("=== Interface Status ===\n")

        # 1. Determine who is running the RNS instance
        rnsd_running = check_process_running('rnsd')
        si_info = get_rns_shared_instance_info()
        si_available = si_info.get('available', False)

        if rnsd_running and si_available:
            print(f"  RNS Instance: rnsd — shared instance available")
        elif rnsd_running:
            # Degraded state: rnsd running but shared instance not responding
            # Check abstract Unix socket for more specific diagnosis
            unix_socket_exists = False
            try:
                proc_unix = Path('/proc/net/unix').read_text()
                unix_socket_exists = (
                    '@rns/default' in proc_unix
                    or 'rns/default' in proc_unix
                )
            except (OSError, PermissionError):
                pass
            if unix_socket_exists:
                print(f"  RNS Instance: rnsd — DEGRADED (socket exists, auth may be stale)")
            else:
                print(f"  RNS Instance: rnsd — DEGRADED (socket missing, may be hung)")
        else:
            # Check if NomadNet or Sideband is serving as shared instance
            port_owner = None
            try:
                port_owner = get_udp_port_owner(37428)
            except Exception:
                pass
            if port_owner:
                proc_name, pid = port_owner
                print(f"  RNS Instance: {proc_name} (PID {pid}) — running its own RNS")
            elif si_available:
                print(f"  RNS Instance: available (unknown process)")
            else:
                print(f"  RNS Instance: NOT RUNNING — no shared instance")
        print()

        # 2. Get configured interfaces from config
        result = self._rns_cmd_list_interfaces()
        if result is None:
            self.ctx.wait_for_enter()
            return

        interfaces = result.data.get('interfaces', [])
        if not interfaces:
            print("  No interfaces configured in Reticulum config.\n")
            print("  Use 'Add Interface from Template' to create one.")
            self.ctx.wait_for_enter()
            return

        # 3. Get blocking info (why interfaces can't connect)
        blocking_map = {}
        try:
            blocking = find_blocking_interfaces()
            for iface_name, reason, fix in blocking:
                blocking_map[iface_name] = (reason, fix)
        except Exception as e:
            logger.debug("Blocking interface check failed: %s", e)

        # 4. Get live health from rnstatus (TX/RX counters)
        health_map = {}
        try:
            health = check_rns_interface_health()
            for entry in health:
                # entry = (display_name, tx_str, rx_str, is_healthy)
                name = entry[0]
                health_map[name] = {
                    'tx': entry[1], 'rx': entry[2], 'healthy': entry[3],
                }
        except Exception as e:
            logger.debug("Interface health check failed: %s", e)

        # 5. Display unified table
        print(f"  {'Name':<26} {'Type':<24} {'Status':<10} Detail")
        print(f"  {'─' * 80}")

        for iface in interfaces:
            name = iface.get('name', '(unnamed)')
            settings = iface.get('settings', {})
            itype = settings.get('type', '?')
            enabled_raw = str(settings.get('enabled', 'no')).lower()
            enabled = enabled_raw in ('yes', 'true', '1')

            if not enabled:
                status = "DISABLED"
                detail = ""
            elif name in blocking_map:
                status = "BLOCKED"
                reason, fix = blocking_map[name]
                detail = reason
            else:
                # Try to match against rnstatus output
                live = self._match_live_health(name, itype, health_map)
                if live:
                    if not live['healthy']:
                        status = "RX-ONLY"
                        detail = f"↑{live['tx']}  ↓{live['rx']} (link establishment failing)"
                    else:
                        status = "UP"
                        detail = f"↑{live['tx']}  ↓{live['rx']}"
                elif rnsd_running and si_available:
                    # rnsd is running and we have shared instance
                    # but no rnstatus data for this interface
                    if itype == 'TCPServerInterface':
                        status = "LISTEN"
                        detail = "waiting for clients"
                    else:
                        status = "UP"
                        detail = "(no traffic data)"
                elif rnsd_running:
                    status = "UNKNOWN"
                    detail = "rnsd running but shared instance not available"
                else:
                    status = "DOWN"
                    detail = "rnsd not running"

            # Color-coded status hint via text markers
            if status == "BLOCKED":
                marker = "!"
            elif status in ("DOWN", "RX-ONLY"):
                marker = "~"
            elif status == "DISABLED":
                marker = "-"
            else:
                marker = " "

            print(f" {marker}{name:<26} {itype:<24} {status:<10} {detail}")

            # Show fix hint for blocked interfaces
            if name in blocking_map:
                _, fix = blocking_map[name]
                print(f"  {' ' * 26} {' ' * 24} {'':10} Fix: {fix}")

        print(f"\n  Total: {len(interfaces)} interface(s)")

        # Summary hints
        if blocking_map:
            print(f"\n  ! = blocked (dependency missing)")
        if not rnsd_running:
            print(f"\n  Start rnsd: sudo systemctl start rnsd")
        elif not si_available:
            # Degraded state: show journal tail for immediate visibility
            print(f"\n  rnsd is running but shared instance is NOT responding.")
            print(f"  Common causes: stale auth tokens, config drift, hung interface.")
            try:
                r = subprocess.run(
                    ['journalctl', '-u', 'rnsd', '-n', '5',
                     '--no-pager', '-q', '--no-hostname'],
                    capture_output=True, text=True, timeout=10,
                )
                if r.stdout and r.stdout.strip():
                    print(f"\n  Recent rnsd log:")
                    for line in r.stdout.strip().splitlines():
                        print(f"    {line.strip()[:90]}")
            except (subprocess.SubprocessError, OSError):
                pass

            # Offer repair wizard via cross-handler dispatch
            diag = (self.ctx.registry.get_handler("rns_diagnostics")
                    if self.ctx.registry else None)
            if diag:
                print()
                if self.ctx.dialog.yesno(
                    "Repair Shared Instance",
                    "rnsd is running but the shared instance is not\n"
                    "responding. This prevents NomadNet, rnstatus, and\n"
                    "all RNS tools from connecting.\n\n"
                    "Run the repair wizard?\n"
                    "(Clears stale auth, checks config, restarts rnsd)",
                ):
                    clear_screen()
                    diag._repair_rns_shared_instance()

        self.ctx.wait_for_enter()

    def _match_live_health(self, config_name: str, iface_type: str,
                           health_map: dict) -> dict:
        """Match a config interface name to rnstatus output.

        rnstatus shows interfaces as 'TypeName[DisplayName]'.
        Config names are [[DisplayName]]. Try fuzzy matching.
        """
        # Direct match: TypeName[config_name]
        for key, data in health_map.items():
            if config_name in key:
                return data

        # Type-based partial match
        type_prefix = iface_type.replace('_', '')
        for key, data in health_map.items():
            if key.startswith(type_prefix):
                return data

        return {}

    # ------------------------------------------------------------------
    # List interfaces
    # ------------------------------------------------------------------

    def _rns_list_interfaces(self):
        """Display all configured RNS interfaces."""
        clear_screen()
        print("=== Configured RNS Interfaces ===\n")

        result = self._rns_cmd_list_interfaces()
        if result is None:
            self.ctx.wait_for_enter()
            return

        interfaces = result.data.get('interfaces', [])
        if not interfaces:
            print("No interfaces found in the Reticulum config.\n")
            print("Use 'Add Interface from Template' to create one.")
            self.ctx.wait_for_enter()
            return

        for idx, iface in enumerate(interfaces, 1):
            name = iface.get('name', '(unnamed)')
            settings = iface.get('settings', {})
            itype = settings.get('type', '?')
            enabled = settings.get('enabled', '?')
            print(f"  {idx}. [[{name}]]")
            print(f"     type    = {itype}")
            print(f"     enabled = {enabled}")
            # Show a few key settings per type
            for key, val in settings.items():
                if key in ('type', 'enabled'):
                    continue
                print(f"     {key} = {val}")
            print()

        print(f"Total: {len(interfaces)} interface(s)")
        self.ctx.wait_for_enter()

    # ------------------------------------------------------------------
    # Add interface (template-based)
    # ------------------------------------------------------------------

    def _rns_add_interface(self):
        """Add a new RNS interface by picking a template and customising."""
        cmd_mod = self._import_rns_commands()
        if cmd_mod is None:
            return

        tpl_result = cmd_mod.get_interface_templates()
        if not tpl_result.success:
            self.ctx.dialog.msgbox("Error", f"Could not load templates:\n{tpl_result.message}")
            return

        templates = tpl_result.data.get('templates', {})
        if not templates:
            self.ctx.dialog.msgbox("Error", "No interface templates available.")
            return

        # Build menu of templates
        choices = []
        for key, tpl in templates.items():
            if tpl.get('multi_interface'):
                label = f"Multi - {tpl['description']}"
            else:
                label = f"{tpl['type']} - {tpl['description']}"
            # Truncate long descriptions for whiptail
            if len(label) > 60:
                label = label[:57] + "..."
            choices.append((key, label))
        choices.append(("back", "Back"))

        tpl_choice = self.ctx.dialog.menu(
            "Add Interface",
            "Select an interface template:",
            choices,
        )

        if tpl_choice is None or tpl_choice == "back":
            return

        template = templates[tpl_choice]

        # Multi-interface templates have a different flow
        if template.get('multi_interface'):
            self._rns_add_multi_interface(cmd_mod, tpl_choice, template)
            return

        # Ask for a name
        default_name = template.get('name', tpl_choice)
        iface_name = self.ctx.dialog.inputbox(
            "Interface Name",
            f"Name for the new {template['type']} interface:\n"
            f"(alphanumeric, spaces, dashes allowed)",
            default_name,
        )
        if not iface_name:
            return
        iface_name = iface_name.strip()
        if not iface_name or not re.match(r'^[\w\s\-]+$', iface_name):
            self.ctx.dialog.msgbox(
                "Invalid Name",
                "Name must contain only alphanumeric characters,\n"
                "spaces, and dashes.",
            )
            return

        # Let user customise key settings
        settings = dict(template.get('settings', {}))
        settings = self._rns_edit_interface_settings(template['type'], settings)
        if settings is None:
            return  # user cancelled

        # Apply
        result = cmd_mod.apply_template(tpl_choice, iface_name, settings)
        if result.success:
            self.ctx.dialog.msgbox(
                "Interface Added",
                f"Added [[{iface_name}]] ({template['type']})\n\n"
                f"Restart rnsd to apply:\n"
                f"  sudo systemctl restart rnsd",
            )
        else:
            self.ctx.dialog.msgbox("Error", f"Failed to add interface:\n{result.message}")

    def _rns_add_multi_interface(self, cmd_mod, tpl_key: str, template: dict):
        """Handle adding a multi-interface template (e.g. dual-radio Meshtastic)."""
        iface_defs = template.get('interfaces', [])
        if not iface_defs:
            self.ctx.dialog.msgbox("Error", "Template has no interface definitions.")
            return

        # Show overview of what will be created
        overview_lines = [f"{template['name']}\n"]
        for i, idef in enumerate(iface_defs, 1):
            overview_lines.append(f"  Radio {i}: {idef['default_name']}")
            overview_lines.append(f"    type = {idef['type']}")
            for k, v in idef['settings'].items():
                overview_lines.append(f"    {k} = {v}")
            overview_lines.append("")
        overview_lines.append("Proceed? (settings can be customised next)")

        if not self.ctx.dialog.yesno(template['name'], "\n".join(overview_lines)):
            return

        # Collect user config for each interface
        interface_configs = []
        for i, idef in enumerate(iface_defs, 1):
            default_name = idef['default_name']
            iface_name = self.ctx.dialog.inputbox(
                f"Radio {i} Name",
                f"Name for radio {i} ({idef['type']}):\n"
                f"(alphanumeric, spaces, dashes allowed)",
                default_name,
            )
            if not iface_name:
                return
            iface_name = iface_name.strip()
            if not iface_name or not re.match(r'^[\w\s\-]+$', iface_name):
                self.ctx.dialog.msgbox(
                    "Invalid Name",
                    "Name must contain only alphanumeric characters,\n"
                    "spaces, and dashes.",
                )
                return

            # Let user customise this radio's settings
            settings = dict(idef.get('settings', {}))
            settings = self._rns_edit_interface_settings(idef['type'], settings)
            if settings is None:
                return  # user cancelled

            interface_configs.append({
                'name': iface_name,
                'overrides': settings,
            })

        # Apply all interfaces
        result = cmd_mod.apply_multi_template(tpl_key, interface_configs)
        if result.success:
            added = result.data.get('added', [])
            names_str = "\n".join(f"  - [[{n}]]" for n in added)
            self.ctx.dialog.msgbox(
                "Interfaces Added",
                f"Added {len(added)} interfaces:\n{names_str}\n\n"
                f"Restart rnsd to apply:\n"
                f"  sudo systemctl restart rnsd",
            )
        else:
            self.ctx.dialog.msgbox("Error", f"Failed:\n{result.message}")

    def _rns_edit_interface_settings(self, iface_type: str, settings: dict):
        """Let the user edit key settings for an interface template.

        Returns the (possibly modified) settings dict, or None if cancelled.
        """
        if not settings:
            return settings

        # Build a description of defaults
        desc_lines = [f"Current defaults for {iface_type}:\n"]
        for key, val in settings.items():
            desc_lines.append(f"  {key} = {val}")
        desc_lines.append("\nEdit settings? (No keeps defaults)")

        if not self.ctx.dialog.yesno("Customise Settings", "\n".join(desc_lines)):
            return settings

        # Walk through each setting with an inputbox
        updated = {}
        for key, val in settings.items():
            new_val = self.ctx.dialog.inputbox(
                f"Setting: {key}",
                f"Interface type: {iface_type}\n\n"
                f"Enter value for '{key}':",
                str(val),
            )
            if new_val is None:
                # User cancelled mid-edit
                return None
            updated[key] = new_val.strip()

        return updated

    # ------------------------------------------------------------------
    # Enable / Disable interface
    # ------------------------------------------------------------------

    def _rns_toggle_interface(self, enable: bool):
        """Enable or disable a configured interface."""
        cmd_mod = self._import_rns_commands()
        if cmd_mod is None:
            return

        # List interfaces so user can pick one
        iface_name = self._rns_pick_interface(
            "Enable Interface" if enable else "Disable Interface"
        )
        if not iface_name:
            return

        action = "enable" if enable else "disable"
        if not self.ctx.dialog.yesno(
            f"Confirm {action.title()}",
            f"{action.title()} interface [[{iface_name}]]?\n\n"
            f"rnsd restart required to apply.",
        ):
            return

        if enable:
            result = cmd_mod.enable_interface(iface_name)
        else:
            result = cmd_mod.disable_interface(iface_name)

        if result.success:
            self.ctx.dialog.msgbox(
                f"Interface {action.title()}d",
                f"[[{iface_name}]] is now {action}d.\n\n"
                f"Restart rnsd to apply:\n"
                f"  sudo systemctl restart rnsd",
            )
        else:
            self.ctx.dialog.msgbox("Error", f"Failed to {action} interface:\n{result.message}")

    # ------------------------------------------------------------------
    # Remove interface
    # ------------------------------------------------------------------

    def _rns_remove_interface(self):
        """Remove an interface from the Reticulum config."""
        cmd_mod = self._import_rns_commands()
        if cmd_mod is None:
            return

        iface_name = self._rns_pick_interface("Remove Interface")
        if not iface_name:
            return

        if not self.ctx.dialog.yesno(
            "Confirm Remove",
            f"Remove interface [[{iface_name}]]?\n\n"
            f"A backup of the config will be created.\n"
            f"This cannot be undone without the backup.",
        ):
            return

        result = cmd_mod.remove_interface(iface_name)
        if result.success:
            self.ctx.dialog.msgbox(
                "Interface Removed",
                f"[[{iface_name}]] has been removed.\n\n"
                f"Restart rnsd to apply:\n"
                f"  sudo systemctl restart rnsd",
            )
        else:
            self.ctx.dialog.msgbox("Error", f"Failed to remove interface:\n{result.message}")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _rns_pick_interface(self, title: str):
        """Show a menu of configured interfaces and return the selected name.

        Returns the interface name string, or None if cancelled/empty.
        """
        result = self._rns_cmd_list_interfaces()
        if result is None:
            return None

        interfaces = result.data.get('interfaces', [])
        if not interfaces:
            self.ctx.dialog.msgbox(title, "No interfaces configured.")
            return None

        choices = []
        for iface in interfaces:
            name = iface.get('name', '(unnamed)')
            settings = iface.get('settings', {})
            itype = settings.get('type', '?')
            enabled = settings.get('enabled', '?')
            desc = f"{itype} (enabled={enabled})"
            choices.append((name, desc))
        choices.append(("back", "Back"))

        choice = self.ctx.dialog.menu(title, "Select an interface:", choices)
        if choice is None or choice == "back":
            return None
        return choice

    def _rns_cmd_list_interfaces(self):
        """Call commands.rns.list_interfaces(), returning CommandResult or None on error."""
        cmd_mod = self._import_rns_commands()
        if cmd_mod is None:
            return None

        result = cmd_mod.list_interfaces()
        if not result.success:
            self.ctx.dialog.msgbox("Error", f"Could not read interfaces:\n{result.message}")
            return None
        return result

    def _import_rns_commands(self):
        """Import and return the commands.rns module."""
        return rns_mod
