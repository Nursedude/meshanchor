"""
Gateway Setup Wizard

A step-by-step wizard for configuring the RNS-Meshtastic gateway.
Guides users through service verification, configuration, and testing.
"""

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Gtk, Adw, GLib, Gio
import threading
import json
import logging
from pathlib import Path
from dataclasses import dataclass
from typing import Optional, Callable, Dict, Any

logger = logging.getLogger(__name__)


@dataclass
class WizardStep:
    """Represents a wizard step."""
    title: str
    description: str
    icon: str = "dialog-information-symbolic"
    can_skip: bool = False


class GatewaySetupWizard(Adw.Window):
    """
    Step-by-step wizard for gateway configuration.

    Steps:
    1. Welcome - Explain what the gateway does
    2. Prerequisites - Check meshtasticd and rnsd
    3. Connection Test - Verify connectivity
    4. Configuration - Set gateway options
    5. Summary - Show configuration and start
    """

    STEPS = [
        WizardStep(
            title="Welcome",
            description="Set up your RNS-Meshtastic Gateway",
            icon="starred-symbolic"
        ),
        WizardStep(
            title="Prerequisites",
            description="Verify required services",
            icon="emblem-system-symbolic"
        ),
        WizardStep(
            title="Connection Test",
            description="Test service connectivity",
            icon="network-wired-symbolic"
        ),
        WizardStep(
            title="Configuration",
            description="Configure gateway settings",
            icon="preferences-system-symbolic"
        ),
        WizardStep(
            title="Complete",
            description="Review and start gateway",
            icon="emblem-ok-symbolic"
        ),
    ]

    def __init__(self, parent: Optional[Gtk.Window] = None,
                 on_complete: Optional[Callable] = None):
        super().__init__(
            title="Gateway Setup Wizard",
            modal=True,
            default_width=600,
            default_height=500,
        )

        if parent:
            self.set_transient_for(parent)

        self.on_complete = on_complete
        self.current_step = 0
        self.config = {
            'enabled': True,
            'auto_start': False,
            'bridge_mode': 'message_bridge',
            'meshtastic': {
                'host': 'localhost',
                'port': 4403,
                'channel': 0
            },
            'rns': {
                'identity_name': 'meshforge_gateway',
                'announce_interval': 300
            }
        }
        self.service_status = {
            'meshtasticd': None,
            'rnsd': None
        }

        self._build_ui()
        self._show_step(0)

    def _build_ui(self):
        """Build the wizard UI."""
        # Main container
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.set_content(main_box)

        # Header bar with step indicator
        header = Adw.HeaderBar()
        header.set_show_end_title_buttons(True)
        main_box.append(header)

        # Step indicator
        self.step_indicator = Gtk.Label()
        self.step_indicator.add_css_class("dim-label")
        header.set_title_widget(self.step_indicator)

        # Content area (stack for each step)
        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.SLIDE_LEFT_RIGHT)
        self.stack.set_vexpand(True)
        main_box.append(self.stack)

        # Build each step page
        self._build_welcome_page()
        self._build_prerequisites_page()
        self._build_connection_page()
        self._build_config_page()
        self._build_complete_page()

        # Navigation buttons
        nav_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        nav_box.set_margin_start(20)
        nav_box.set_margin_end(20)
        nav_box.set_margin_top(12)
        nav_box.set_margin_bottom(20)
        nav_box.set_halign(Gtk.Align.END)
        main_box.append(nav_box)

        self.back_button = Gtk.Button(label="Back")
        self.back_button.connect("clicked", self._on_back)
        nav_box.append(self.back_button)

        self.next_button = Gtk.Button(label="Next")
        self.next_button.add_css_class("suggested-action")
        self.next_button.connect("clicked", self._on_next)
        nav_box.append(self.next_button)

    def _build_welcome_page(self):
        """Build the welcome page."""
        page = Adw.StatusPage()
        page.set_icon_name("network-transmit-receive-symbolic")
        page.set_title("RNS-Meshtastic Gateway Setup")
        page.set_description(
            "This wizard will help you configure the gateway that bridges "
            "Reticulum (RNS) and Meshtastic mesh networks.\n\n"
            "The gateway enables:\n"
            "• Message translation between networks\n"
            "• Position and telemetry sharing\n"
            "• RNS transport over Meshtastic LoRa"
        )
        self.stack.add_named(page, "welcome")

    def _build_prerequisites_page(self):
        """Build the prerequisites check page."""
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=20)
        page.set_margin_start(40)
        page.set_margin_end(40)
        page.set_margin_top(30)
        page.set_margin_bottom(30)

        # Title
        title = Gtk.Label(label="Checking Prerequisites")
        title.add_css_class("title-1")
        page.append(title)

        desc = Gtk.Label(
            label="The gateway requires both meshtasticd and rnsd services."
        )
        desc.add_css_class("dim-label")
        desc.set_wrap(True)
        page.append(desc)

        # Service status boxes
        services_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        services_box.set_margin_top(20)
        page.append(services_box)

        # Meshtasticd status
        self.meshtasticd_row = self._create_service_row(
            "meshtasticd",
            "Meshtastic Daemon",
            "Provides LoRa mesh connectivity"
        )
        services_box.append(self.meshtasticd_row)

        # RNSD status
        self.rnsd_row = self._create_service_row(
            "rnsd",
            "Reticulum Daemon",
            "Provides RNS network connectivity"
        )
        services_box.append(self.rnsd_row)

        # Check button
        check_button = Gtk.Button(label="Check Services")
        check_button.add_css_class("pill")
        check_button.set_halign(Gtk.Align.CENTER)
        check_button.set_margin_top(20)
        check_button.connect("clicked", self._check_services)
        page.append(check_button)

        # Status label
        self.prereq_status = Gtk.Label()
        self.prereq_status.set_margin_top(10)
        page.append(self.prereq_status)

        self.stack.add_named(page, "prerequisites")

    def _create_service_row(self, service_id: str, name: str, desc: str) -> Gtk.Box:
        """Create a service status row."""
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        row.add_css_class("card")
        row.set_margin_start(10)
        row.set_margin_end(10)

        # Status icon
        icon = Gtk.Image.new_from_icon_name("content-loading-symbolic")
        icon.set_pixel_size(24)
        icon.set_margin_start(16)
        icon.set_margin_top(12)
        icon.set_margin_bottom(12)
        row.append(icon)

        # Store reference for updating
        row.status_icon = icon

        # Labels
        labels = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        labels.set_hexpand(True)
        labels.set_margin_top(12)
        labels.set_margin_bottom(12)
        row.append(labels)

        name_label = Gtk.Label(label=name)
        name_label.set_halign(Gtk.Align.START)
        name_label.add_css_class("heading")
        labels.append(name_label)

        desc_label = Gtk.Label(label=desc)
        desc_label.set_halign(Gtk.Align.START)
        desc_label.add_css_class("dim-label")
        labels.append(desc_label)

        # Status text
        status = Gtk.Label(label="Checking...")
        status.set_margin_end(16)
        status.add_css_class("dim-label")
        row.append(status)
        row.status_label = status

        return row

    def _build_connection_page(self):
        """Build the connection test page."""
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=20)
        page.set_margin_start(40)
        page.set_margin_end(40)
        page.set_margin_top(30)
        page.set_margin_bottom(30)

        title = Gtk.Label(label="Connection Test")
        title.add_css_class("title-1")
        page.append(title)

        desc = Gtk.Label(
            label="Testing connectivity to meshtasticd and RNS interfaces."
        )
        desc.add_css_class("dim-label")
        page.append(desc)

        # Connection details
        self.connection_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.connection_box.set_margin_top(20)
        page.append(self.connection_box)

        # Test button
        test_button = Gtk.Button(label="Run Connection Test")
        test_button.add_css_class("pill")
        test_button.set_halign(Gtk.Align.CENTER)
        test_button.set_margin_top(20)
        test_button.connect("clicked", self._run_connection_test)
        page.append(test_button)

        # Results area
        self.connection_result = Gtk.Label()
        self.connection_result.set_wrap(True)
        self.connection_result.set_margin_top(20)
        page.append(self.connection_result)

        self.stack.add_named(page, "connection")

    def _build_config_page(self):
        """Build the configuration page."""
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        page.set_margin_start(40)
        page.set_margin_end(40)
        page.set_margin_top(30)
        page.set_margin_bottom(30)

        title = Gtk.Label(label="Gateway Configuration")
        title.add_css_class("title-1")
        page.append(title)

        # Preferences group
        prefs = Adw.PreferencesGroup()
        prefs.set_title("Settings")
        page.append(prefs)

        # Bridge mode
        mode_row = Adw.ComboRow()
        mode_row.set_title("Bridge Mode")
        mode_row.set_subtitle("How to bridge the networks")
        mode_model = Gtk.StringList.new([
            "Message Bridge",
            "RNS Transport",
            "Both"
        ])
        mode_row.set_model(mode_model)
        mode_row.set_selected(0)
        mode_row.connect("notify::selected", self._on_mode_changed)
        prefs.add(mode_row)
        self.mode_row = mode_row

        # Auto-start
        autostart_row = Adw.SwitchRow()
        autostart_row.set_title("Auto-start Gateway")
        autostart_row.set_subtitle("Start gateway when MeshForge launches")
        autostart_row.connect("notify::active", self._on_autostart_changed)
        prefs.add(autostart_row)
        self.autostart_row = autostart_row

        # Meshtastic settings group
        mesh_prefs = Adw.PreferencesGroup()
        mesh_prefs.set_title("Meshtastic")
        mesh_prefs.set_margin_top(16)
        page.append(mesh_prefs)

        # Host
        host_row = Adw.EntryRow()
        host_row.set_title("Host")
        host_row.set_text("localhost")
        host_row.connect("changed", self._on_host_changed)
        mesh_prefs.add(host_row)
        self.host_row = host_row

        # Port
        port_row = Adw.SpinRow.new_with_range(1, 65535, 1)
        port_row.set_title("Port")
        port_row.set_value(4403)
        port_row.connect("notify::value", self._on_port_changed)
        mesh_prefs.add(port_row)
        self.port_row = port_row

        # Channel
        channel_row = Adw.SpinRow.new_with_range(0, 7, 1)
        channel_row.set_title("Channel")
        channel_row.set_value(0)
        channel_row.connect("notify::value", self._on_channel_changed)
        mesh_prefs.add(channel_row)
        self.channel_row = channel_row

        self.stack.add_named(page, "config")

    def _build_complete_page(self):
        """Build the completion page."""
        page = Adw.StatusPage()
        page.set_icon_name("emblem-ok-symbolic")
        page.set_title("Setup Complete")
        page.set_description(
            "Your gateway is configured and ready to start.\n\n"
            "Click 'Start Gateway' to begin bridging networks."
        )

        # Summary box
        summary_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        summary_box.set_halign(Gtk.Align.CENTER)
        summary_box.add_css_class("card")
        summary_box.set_margin_start(40)
        summary_box.set_margin_end(40)

        self.summary_label = Gtk.Label()
        self.summary_label.set_margin_start(20)
        self.summary_label.set_margin_end(20)
        self.summary_label.set_margin_top(16)
        self.summary_label.set_margin_bottom(16)
        self.summary_label.set_wrap(True)
        summary_box.append(self.summary_label)

        page.set_child(summary_box)
        self.stack.add_named(page, "complete")

    def _show_step(self, step: int):
        """Show a specific wizard step."""
        self.current_step = step
        step_info = self.STEPS[step]

        # Update step indicator
        self.step_indicator.set_text(f"Step {step + 1} of {len(self.STEPS)}")

        # Show the appropriate page
        pages = ["welcome", "prerequisites", "connection", "config", "complete"]
        self.stack.set_visible_child_name(pages[step])

        # Update navigation buttons
        self.back_button.set_sensitive(step > 0)

        if step == len(self.STEPS) - 1:
            self.next_button.set_label("Start Gateway")
            self.next_button.remove_css_class("suggested-action")
            self.next_button.add_css_class("destructive-action")
        else:
            self.next_button.set_label("Next")
            self.next_button.remove_css_class("destructive-action")
            self.next_button.add_css_class("suggested-action")

        # Trigger step-specific actions
        if step == 1:  # Prerequisites
            self._check_services(None)
        elif step == 2:  # Connection
            self._run_connection_test(None)
        elif step == 4:  # Complete
            self._update_summary()

    def _on_back(self, button):
        """Go to previous step."""
        if self.current_step > 0:
            self._show_step(self.current_step - 1)

    def _on_next(self, button):
        """Go to next step or complete."""
        if self.current_step < len(self.STEPS) - 1:
            self._show_step(self.current_step + 1)
        else:
            self._complete_wizard()

    def _check_services(self, button):
        """Check if required services are running."""
        def check():
            import subprocess
            results = {}

            for service in ['meshtasticd', 'rnsd']:
                try:
                    result = subprocess.run(
                        ['systemctl', 'is-active', service],
                        capture_output=True, text=True, timeout=5
                    )
                    results[service] = result.stdout.strip() == 'active'
                except Exception:
                    results[service] = False

            GLib.idle_add(self._update_service_status, results)

        threading.Thread(target=check, daemon=True).start()

    def _update_service_status(self, results: Dict[str, bool]):
        """Update service status UI."""
        self.service_status = results

        for service, row in [('meshtasticd', self.meshtasticd_row),
                             ('rnsd', self.rnsd_row)]:
            is_running = results.get(service, False)

            if is_running:
                row.status_icon.set_from_icon_name("emblem-ok-symbolic")
                row.status_label.set_text("Running")
                row.status_label.remove_css_class("error")
                row.status_label.add_css_class("success")
            else:
                row.status_icon.set_from_icon_name("dialog-warning-symbolic")
                row.status_label.set_text("Not running")
                row.status_label.remove_css_class("success")
                row.status_label.add_css_class("error")

        # Update overall status
        all_running = all(results.values())
        if all_running:
            self.prereq_status.set_text("✓ All services running")
            self.prereq_status.remove_css_class("error")
            self.prereq_status.add_css_class("success")
        else:
            self.prereq_status.set_text(
                "⚠ Some services not running. Gateway may not work correctly."
            )
            self.prereq_status.remove_css_class("success")
            self.prereq_status.add_css_class("warning")

    def _run_connection_test(self, button):
        """Test connection to services."""
        def test():
            import socket
            results = []

            # Test meshtasticd TCP connection
            host = self.config['meshtastic']['host']
            port = self.config['meshtastic']['port']

            sock = None
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(3)
                sock.connect((host, port))
                results.append(f"✓ Meshtasticd ({host}:{port}) - Connected")
            except Exception as e:
                results.append(f"✗ Meshtasticd ({host}:{port}) - {e}")
            finally:
                if sock:
                    try:
                        sock.close()
                    except Exception:
                        pass

            # Test RNS availability
            try:
                import RNS
                results.append("✓ RNS library - Available")
            except ImportError:
                results.append("✗ RNS library - Not installed")

            GLib.idle_add(self._update_connection_result, results)

        self.connection_result.set_text("Testing...")
        threading.Thread(target=test, daemon=True).start()

    def _update_connection_result(self, results: list):
        """Update connection test results."""
        self.connection_result.set_text("\n".join(results))

    def _on_mode_changed(self, row, param):
        """Handle bridge mode change."""
        modes = ['message_bridge', 'rns_transport', 'both']
        self.config['bridge_mode'] = modes[row.get_selected()]

    def _on_autostart_changed(self, row, param):
        """Handle auto-start toggle."""
        self.config['auto_start'] = row.get_active()

    def _on_host_changed(self, row):
        """Handle host change."""
        self.config['meshtastic']['host'] = row.get_text()

    def _on_port_changed(self, row, param):
        """Handle port change."""
        self.config['meshtastic']['port'] = int(row.get_value())

    def _on_channel_changed(self, row, param):
        """Handle channel change."""
        self.config['meshtastic']['channel'] = int(row.get_value())

    def _update_summary(self):
        """Update the summary on completion page."""
        mode_names = {
            'message_bridge': 'Message Bridge',
            'rns_transport': 'RNS Transport',
            'both': 'Message Bridge + RNS Transport'
        }

        summary = (
            f"Mode: {mode_names.get(self.config['bridge_mode'], 'Unknown')}\n"
            f"Host: {self.config['meshtastic']['host']}\n"
            f"Port: {self.config['meshtastic']['port']}\n"
            f"Channel: {self.config['meshtastic']['channel']}\n"
            f"Auto-start: {'Yes' if self.config['auto_start'] else 'No'}"
        )
        self.summary_label.set_text(summary)

    def _complete_wizard(self):
        """Complete the wizard and save configuration."""
        # Save configuration
        try:
            self._save_config()
            logger.info("Gateway configuration saved")
        except Exception as e:
            logger.error(f"Failed to save config: {e}")

        # Notify callback
        if self.on_complete:
            self.on_complete(self.config)

        # Close wizard
        self.close()

    def _save_config(self):
        """Save gateway configuration to file."""
        from utils.paths import get_real_user_home
        config_dir = get_real_user_home() / '.config' / 'meshforge'

        config_dir.mkdir(parents=True, exist_ok=True)
        config_file = config_dir / 'gateway.json'

        with open(config_file, 'w') as f:
            json.dump(self.config, f, indent=2)

        logger.info(f"Saved gateway config to {config_file}")


def show_gateway_wizard(parent: Optional[Gtk.Window] = None,
                        on_complete: Optional[Callable] = None):
    """
    Convenience function to show the gateway wizard.

    Args:
        parent: Parent window for modal
        on_complete: Callback when wizard completes with config
    """
    wizard = GatewaySetupWizard(parent=parent, on_complete=on_complete)
    wizard.present()
    return wizard
