"""
RNS Interface Management Mixin for MeshForge Launcher TUI.

Provides TUI handlers to list, add, remove, enable/disable, and apply
templates for Reticulum network interfaces.  All heavy lifting is
delegated to the backend in commands.rns (add_interface, remove_interface,
enable_interface, disable_interface, list_interfaces, get_interface_templates,
apply_template).
"""

import re
import sys
import subprocess
import logging

logger = logging.getLogger(__name__)


class RNSInterfacesMixin:
    """Mixin providing RNS interface management for the TUI launcher."""

    # ------------------------------------------------------------------
    # Top-level submenu
    # ------------------------------------------------------------------

    def _rns_interfaces_menu(self):
        """Manage RNS interfaces (add / remove / enable / disable)."""
        while True:
            choices = [
                ("list", "List Configured Interfaces"),
                ("add", "Add Interface from Template"),
                ("enable", "Enable Interface"),
                ("disable", "Disable Interface"),
                ("remove", "Remove Interface"),
                ("plugin", "Install Meshtastic Plugin"),
                ("back", "Back"),
            ]

            choice = self.dialog.menu(
                "RNS Interfaces",
                "Manage Reticulum network interfaces:",
                choices,
            )

            if choice is None or choice == "back":
                break

            if choice == "list":
                self._rns_list_interfaces()
            elif choice == "add":
                self._rns_add_interface()
            elif choice == "enable":
                self._rns_toggle_interface(enable=True)
            elif choice == "disable":
                self._rns_toggle_interface(enable=False)
            elif choice == "remove":
                self._rns_remove_interface()
            elif choice == "plugin":
                # Defined in MeshForgeLauncher (main.py), resolved via MRO
                self._install_meshtastic_interface_plugin()

    # ------------------------------------------------------------------
    # List interfaces
    # ------------------------------------------------------------------

    def _rns_list_interfaces(self):
        """Display all configured RNS interfaces."""
        subprocess.run(['clear'], check=False, timeout=5)
        print("=== Configured RNS Interfaces ===\n")

        result = self._rns_cmd_list_interfaces()
        if result is None:
            input("\nPress Enter to continue...")
            return

        interfaces = result.data.get('interfaces', [])
        if not interfaces:
            print("No interfaces found in the Reticulum config.\n")
            print("Use 'Add Interface from Template' to create one.")
            input("\nPress Enter to continue...")
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
        input("\nPress Enter to continue...")

    # ------------------------------------------------------------------
    # Add interface (template-based)
    # ------------------------------------------------------------------

    def _rns_add_interface(self):
        """Add a new RNS interface by picking a template and customising."""
        rns_mod = self._import_rns_commands()
        if rns_mod is None:
            return

        tpl_result = rns_mod.get_interface_templates()
        if not tpl_result.success:
            self.dialog.msgbox("Error", f"Could not load templates:\n{tpl_result.message}")
            return

        templates = tpl_result.data.get('templates', {})
        if not templates:
            self.dialog.msgbox("Error", "No interface templates available.")
            return

        # Build menu of templates
        choices = []
        for key, tpl in templates.items():
            label = f"{tpl['type']} - {tpl['description']}"
            # Truncate long descriptions for whiptail
            if len(label) > 60:
                label = label[:57] + "..."
            choices.append((key, label))
        choices.append(("back", "Back"))

        tpl_choice = self.dialog.menu(
            "Add Interface",
            "Select an interface template:",
            choices,
        )

        if tpl_choice is None or tpl_choice == "back":
            return

        template = templates[tpl_choice]

        # Ask for a name
        default_name = template.get('name', tpl_choice)
        iface_name = self.dialog.inputbox(
            "Interface Name",
            f"Name for the new {template['type']} interface:\n"
            f"(alphanumeric, spaces, dashes allowed)",
            default_name,
        )
        if not iface_name:
            return
        iface_name = iface_name.strip()
        if not iface_name or not re.match(r'^[\w\s\-]+$', iface_name):
            self.dialog.msgbox(
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
        result = rns_mod.apply_template(tpl_choice, iface_name, settings)
        if result.success:
            self.dialog.msgbox(
                "Interface Added",
                f"Added [[{iface_name}]] ({template['type']})\n\n"
                f"Restart rnsd to apply:\n"
                f"  sudo systemctl restart rnsd",
            )
        else:
            self.dialog.msgbox("Error", f"Failed to add interface:\n{result.message}")

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

        if not self.dialog.yesno("Customise Settings", "\n".join(desc_lines)):
            return settings

        # Walk through each setting with an inputbox
        updated = {}
        for key, val in settings.items():
            new_val = self.dialog.inputbox(
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
        rns_mod = self._import_rns_commands()
        if rns_mod is None:
            return

        # List interfaces so user can pick one
        iface_name = self._rns_pick_interface(
            "Enable Interface" if enable else "Disable Interface"
        )
        if not iface_name:
            return

        action = "enable" if enable else "disable"
        if not self.dialog.yesno(
            f"Confirm {action.title()}",
            f"{action.title()} interface [[{iface_name}]]?\n\n"
            f"rnsd restart required to apply.",
        ):
            return

        if enable:
            result = rns_mod.enable_interface(iface_name)
        else:
            result = rns_mod.disable_interface(iface_name)

        if result.success:
            self.dialog.msgbox(
                f"Interface {action.title()}d",
                f"[[{iface_name}]] is now {action}d.\n\n"
                f"Restart rnsd to apply:\n"
                f"  sudo systemctl restart rnsd",
            )
        else:
            self.dialog.msgbox("Error", f"Failed to {action} interface:\n{result.message}")

    # ------------------------------------------------------------------
    # Remove interface
    # ------------------------------------------------------------------

    def _rns_remove_interface(self):
        """Remove an interface from the Reticulum config."""
        rns_mod = self._import_rns_commands()
        if rns_mod is None:
            return

        iface_name = self._rns_pick_interface("Remove Interface")
        if not iface_name:
            return

        if not self.dialog.yesno(
            "Confirm Remove",
            f"Remove interface [[{iface_name}]]?\n\n"
            f"A backup of the config will be created.\n"
            f"This cannot be undone without the backup.",
        ):
            return

        result = rns_mod.remove_interface(iface_name)
        if result.success:
            self.dialog.msgbox(
                "Interface Removed",
                f"[[{iface_name}]] has been removed.\n\n"
                f"Restart rnsd to apply:\n"
                f"  sudo systemctl restart rnsd",
            )
        else:
            self.dialog.msgbox("Error", f"Failed to remove interface:\n{result.message}")

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
            self.dialog.msgbox(title, "No interfaces configured.")
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

        choice = self.dialog.menu(title, "Select an interface:", choices)
        if choice is None or choice == "back":
            return None
        return choice

    def _rns_cmd_list_interfaces(self):
        """Call commands.rns.list_interfaces(), returning CommandResult or None on error."""
        rns_mod = self._import_rns_commands()
        if rns_mod is None:
            return None

        result = rns_mod.list_interfaces()
        if not result.success:
            self.dialog.msgbox("Error", f"Could not read interfaces:\n{result.message}")
            return None
        return result

    def _import_rns_commands(self):
        """Import and return the commands.rns module, or None on failure."""
        try:
            src = str(self.src_dir)
            if src not in sys.path:
                sys.path.insert(0, src)
            from commands import rns as rns_mod
            return rns_mod
        except ImportError as e:
            self.dialog.msgbox(
                "Import Error",
                f"Could not import RNS commands module:\n{e}\n\n"
                "Ensure src/commands/rns.py exists.",
            )
            return None
