"""
Webhooks Mixin — Manage webhook endpoints for external notifications.

Wires utils/webhooks.py (WebhookManager) to TUI menus.
Extracted as a mixin to keep main.py under 1,500 lines.
"""

import logging
from backend import clear_screen
from utils.safe_import import safe_import

logger = logging.getLogger(__name__)

# Module-level safe imports
get_webhook_manager, WebhookEndpoint, EventType, _HAS_WEBHOOKS = safe_import(
    'utils.webhooks', 'get_webhook_manager', 'WebhookEndpoint', 'EventType'
)


class WebhooksMixin:
    """TUI mixin for webhook management."""

    def _webhooks_menu(self):
        """Webhooks — manage external notification endpoints."""
        while True:
            choices = [
                ("list", "List Endpoints      Show configured hooks"),
                ("add", "Add Endpoint        Register new webhook"),
                ("remove", "Remove Endpoint     Delete a webhook"),
                ("toggle", "Enable/Disable      Toggle endpoint state"),
                ("test", "Test Webhook        Send test event"),
                ("events", "Event Types         Supported event list"),
                ("back", "Back"),
            ]

            choice = self.dialog.menu(
                "Webhooks",
                "External notification management:",
                choices
            )

            if choice is None or choice == "back":
                break

            dispatch = {
                "list": ("List Endpoints", self._webhooks_list),
                "add": ("Add Endpoint", self._webhooks_add),
                "remove": ("Remove Endpoint", self._webhooks_remove),
                "toggle": ("Toggle Endpoint", self._webhooks_toggle),
                "test": ("Test Webhook", self._webhooks_test),
                "events": ("Event Types", self._webhooks_event_types),
            }
            entry = dispatch.get(choice)
            if entry:
                self._safe_call(*entry)

    def _webhooks_list(self):
        """List all configured webhook endpoints."""
        clear_screen()
        print("=== Webhook Endpoints ===\n")

        if not _HAS_WEBHOOKS:
            print("  Webhooks module not available.")
            print("  File: src/utils/webhooks.py")
            self._wait_for_enter()
            return

        manager = get_webhook_manager()
        endpoints = manager.list_endpoints()

        if not endpoints:
            print("  No webhook endpoints configured.")
            print("  Use 'Add Endpoint' to register a webhook URL.")
            self._wait_for_enter()
            return

        for i, ep in enumerate(endpoints, 1):
            enabled = "\033[0;32mON\033[0m" if ep.get('enabled', False) else "\033[0;31mOFF\033[0m"
            name = ep.get('name', 'unnamed')
            url = ep.get('url', '?')
            events = ep.get('events', [])
            event_str = ", ".join(events[:3]) if events else "all events"
            if len(events) > 3:
                event_str += f" +{len(events) - 3} more"

            print(f"  {i}. [{enabled}] {name}")
            print(f"     URL: {url}")
            print(f"     Events: {event_str}")
            print()

        print()
        self._wait_for_enter()

    def _webhooks_add(self):
        """Add a new webhook endpoint via dialog."""
        if not _HAS_WEBHOOKS:
            self.dialog.msgbox("Unavailable", "Webhooks module not available.")
            return

        # Get name
        name = self.dialog.inputbox("Webhook Name", "Short name for this endpoint:")
        if not name:
            return

        # Get URL
        url = self.dialog.inputbox("Webhook URL", "Full URL (https://...):")
        if not url:
            return

        # Basic URL validation
        if not url.startswith(('http://', 'https://')):
            self.dialog.msgbox("Invalid URL", "URL must start with http:// or https://")
            return

        manager = get_webhook_manager()
        endpoint = WebhookEndpoint(url=url, name=name)
        if manager.add_endpoint(endpoint):
            self.dialog.msgbox("Added", f"Webhook '{name}' added.\nURL: {url}")
        else:
            self.dialog.msgbox("Failed", f"Endpoint already exists for URL:\n{url}")

    def _webhooks_remove(self):
        """Remove a webhook endpoint."""
        if not _HAS_WEBHOOKS:
            self.dialog.msgbox("Unavailable", "Webhooks module not available.")
            return

        manager = get_webhook_manager()
        endpoints = manager.list_endpoints()

        if not endpoints:
            self.dialog.msgbox("No Endpoints", "No webhook endpoints configured.")
            return

        choices = []
        for ep in endpoints:
            name = ep.get('name', 'unnamed')
            url = ep.get('url', '?')
            short_url = url[:40] + "..." if len(url) > 40 else url
            choices.append((ep['url'], f"{name:<16} {short_url}"))

        selected = self.dialog.menu(
            "Remove Webhook",
            "Select endpoint to remove:",
            choices
        )

        if selected:
            confirm = self.dialog.yesno(
                "Confirm Remove",
                f"Remove webhook endpoint?\n\nURL: {selected}"
            )
            if confirm:
                if manager.remove_endpoint(selected):
                    self.dialog.msgbox("Removed", "Webhook endpoint removed.")
                else:
                    self.dialog.msgbox("Failed", "Could not remove endpoint.")

    def _webhooks_toggle(self):
        """Toggle a webhook endpoint on/off."""
        if not _HAS_WEBHOOKS:
            self.dialog.msgbox("Unavailable", "Webhooks module not available.")
            return

        manager = get_webhook_manager()
        endpoints = manager.list_endpoints()

        if not endpoints:
            self.dialog.msgbox("No Endpoints", "No webhook endpoints configured.")
            return

        choices = []
        for ep in endpoints:
            name = ep.get('name', 'unnamed')
            state = "ON" if ep.get('enabled', False) else "OFF"
            choices.append((ep['url'], f"[{state}] {name}"))

        selected = self.dialog.menu(
            "Toggle Webhook",
            "Select endpoint to toggle:",
            choices
        )

        if selected:
            # Find current state and flip it
            for ep in endpoints:
                if ep['url'] == selected:
                    new_state = not ep.get('enabled', False)
                    manager.update_endpoint(selected, enabled=new_state)
                    state_str = "enabled" if new_state else "disabled"
                    self.dialog.msgbox("Updated", f"Webhook {state_str}.")
                    break

    def _webhooks_test(self):
        """Send a test event to all enabled webhooks."""
        clear_screen()
        print("=== Test Webhook Delivery ===\n")

        if not _HAS_WEBHOOKS:
            print("  Webhooks module not available.")
            self._wait_for_enter()
            return

        manager = get_webhook_manager()
        endpoints = manager.list_endpoints()
        enabled = [ep for ep in endpoints if ep.get('enabled', False)]

        if not enabled:
            print("  No enabled webhook endpoints.")
            print("  Add and enable an endpoint first.")
            self._wait_for_enter()
            return

        print(f"  Sending test event to {len(enabled)} endpoint(s)...\n")

        manager.emit(EventType.CUSTOM, {
            "test": True,
            "message": "MeshForge webhook test event",
            "source": "tui_test",
        })

        for ep in enabled:
            print(f"  -> {ep.get('name', '?')}: {ep.get('url', '?')}")

        print("\n  Test event queued for delivery.")
        print("  Check your webhook receiver for the event.")
        print()
        self._wait_for_enter()

    def _webhooks_event_types(self):
        """Show all supported webhook event types."""
        clear_screen()
        print("=== Supported Webhook Event Types ===\n")

        if not _HAS_WEBHOOKS:
            print("  Webhooks module not available.")
            self._wait_for_enter()
            return

        descriptions = {
            "node_online": "Node came online or was first seen",
            "node_offline": "Node went offline or unreachable",
            "message_received": "Text message received from mesh",
            "position_update": "GPS position update from a node",
            "telemetry_update": "Telemetry data (battery, voltage, etc.)",
            "alert_battery_low": "Node battery below threshold",
            "alert_signal_poor": "Link quality degraded",
            "alert_node_unreachable": "Node stopped responding",
            "gateway_status": "Gateway bridge state change",
            "service_status": "Service started/stopped/failed",
            "custom": "Custom user-defined events",
        }

        for evt in EventType:
            desc = descriptions.get(evt.value, "")
            print(f"  {evt.value:<28} {desc}")

        print(f"\n  Total: {len(list(EventType))} event types")
        print("\n  Filter endpoints to specific events when adding,")
        print("  or leave empty to receive all events.")
        print()
        self._wait_for_enter()
