"""
EAS Alerts Panel - Emergency Alert System Integration

Displays emergency alerts from:
- NOAA/NWS Weather Alerts
- USGS Volcano Alerts
- FEMA iPAWS Alerts

Provides real-time monitoring and mesh broadcast capabilities.
"""

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, GLib, Pango
import threading
import logging
from datetime import datetime
from typing import List, Optional

logger = logging.getLogger(__name__)

# Import EAS plugin
try:
    from plugins.eas_alerts import (
        EASAlertsPlugin,
        Alert,
        AlertSeverity,
        AlertSource,
    )
    EAS_AVAILABLE = True
except ImportError:
    EAS_AVAILABLE = False
    logger.warning("EAS Alerts plugin not available")


class AlertCard(Gtk.Frame):
    """Widget displaying a single alert"""

    SEVERITY_COLORS = {
        "Extreme": "#dc3545",   # Red
        "Severe": "#fd7e14",    # Orange
        "Moderate": "#ffc107",  # Yellow
        "Minor": "#28a745",     # Green
        "Unknown": "#6c757d",   # Gray
    }

    def __init__(self, alert: 'Alert'):
        super().__init__()
        self.alert = alert
        self.add_css_class("card")
        self._build_ui()

    def _build_ui(self):
        """Build the alert card UI"""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_margin_start(12)
        box.set_margin_end(12)
        box.set_margin_top(12)
        box.set_margin_bottom(12)

        # Header with severity indicator and source
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)

        # Severity dot
        severity_color = self.SEVERITY_COLORS.get(
            self.alert.severity.value if hasattr(self.alert.severity, 'value') else str(self.alert.severity),
            "#6c757d"
        )
        severity_dot = Gtk.Label(label="●")
        severity_dot.set_markup(f'<span foreground="{severity_color}" size="x-large">●</span>')
        header.append(severity_dot)

        # Source badge
        source_name = self.alert.source.value if hasattr(self.alert.source, 'value') else str(self.alert.source)
        source_label = Gtk.Label(label=source_name)
        source_label.add_css_class("dim-label")
        header.append(source_label)

        # Spacer
        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        header.append(spacer)

        # Severity text
        severity_name = self.alert.severity.value if hasattr(self.alert.severity, 'value') else str(self.alert.severity)
        severity_label = Gtk.Label(label=severity_name)
        severity_label.add_css_class("heading")
        header.append(severity_label)

        box.append(header)

        # Title
        title_label = Gtk.Label(label=self.alert.title)
        title_label.set_xalign(0)
        title_label.set_wrap(True)
        title_label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        title_label.add_css_class("title-3")
        box.append(title_label)

        # Event type and areas
        if self.alert.event_type or self.alert.areas:
            meta_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)

            if self.alert.event_type:
                event_label = Gtk.Label(label=f"Type: {self.alert.event_type}")
                event_label.add_css_class("dim-label")
                meta_box.append(event_label)

            if self.alert.areas:
                areas_str = ", ".join(self.alert.areas[:3])
                if len(self.alert.areas) > 3:
                    areas_str += f" (+{len(self.alert.areas) - 3} more)"
                areas_label = Gtk.Label(label=f"Areas: {areas_str}")
                areas_label.add_css_class("dim-label")
                areas_label.set_ellipsize(Pango.EllipsizeMode.END)
                meta_box.append(areas_label)

            box.append(meta_box)

        # Time info
        time_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)

        if self.alert.effective:
            eff_str = self.alert.effective.strftime("%Y-%m-%d %H:%M")
            eff_label = Gtk.Label(label=f"From: {eff_str}")
            eff_label.add_css_class("dim-label")
            time_box.append(eff_label)

        if self.alert.expires:
            exp_str = self.alert.expires.strftime("%Y-%m-%d %H:%M")
            exp_label = Gtk.Label(label=f"Until: {exp_str}")
            exp_label.add_css_class("dim-label")
            time_box.append(exp_label)

        if time_box.get_first_child():
            box.append(time_box)

        # Description (truncated)
        if self.alert.description:
            desc = self.alert.description[:300]
            if len(self.alert.description) > 300:
                desc += "..."
            desc_label = Gtk.Label(label=desc)
            desc_label.set_xalign(0)
            desc_label.set_wrap(True)
            desc_label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
            desc_label.set_max_width_chars(80)
            box.append(desc_label)

        self.set_child(box)


class EASAlertsPanel(Gtk.Box):
    """Panel for Emergency Alert System integration"""

    def __init__(self, main_window):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.main_window = main_window
        self.plugin: Optional[EASAlertsPlugin] = None
        self.alerts: List[Alert] = []

        self.set_margin_start(20)
        self.set_margin_end(20)
        self.set_margin_top(20)
        self.set_margin_bottom(20)

        self._init_plugin()
        self._build_ui()
        self._refresh_alerts()

    def _init_plugin(self):
        """Initialize the EAS plugin"""
        if not EAS_AVAILABLE:
            return

        try:
            self.plugin = EASAlertsPlugin()
            self.plugin.activate()
            logger.info("[EAS Panel] Plugin initialized")
        except Exception as e:
            logger.error(f"[EAS Panel] Failed to initialize plugin: {e}")
            self.plugin = None

    def _build_ui(self):
        """Build the panel UI"""
        # Header with title and controls
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=15)

        title = Gtk.Label(label="Emergency Alert System")
        title.add_css_class("title-1")
        title.set_xalign(0)
        header.append(title)

        # Spacer
        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        header.append(spacer)

        # Refresh button
        refresh_btn = Gtk.Button(label="Refresh")
        refresh_btn.set_icon_name("view-refresh-symbolic")
        refresh_btn.connect("clicked", lambda b: self._refresh_alerts())
        header.append(refresh_btn)

        # Settings button
        settings_btn = Gtk.Button()
        settings_btn.set_icon_name("emblem-system-symbolic")
        settings_btn.set_tooltip_text("Configure alert sources")
        settings_btn.connect("clicked", self._on_settings_clicked)
        header.append(settings_btn)

        self.append(header)

        # Status row
        status_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.status_icon = Gtk.Image.new_from_icon_name("emblem-question-symbolic")
        status_box.append(self.status_icon)

        self.status_label = Gtk.Label(label="Initializing...")
        self.status_label.set_xalign(0)
        status_box.append(self.status_label)

        self.last_update_label = Gtk.Label()
        self.last_update_label.add_css_class("dim-label")
        self.last_update_label.set_xalign(1)
        self.last_update_label.set_hexpand(True)
        status_box.append(self.last_update_label)

        self.append(status_box)

        # Source filter tabs
        source_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        source_box.set_margin_top(10)

        self.source_all = Gtk.ToggleButton(label="All")
        self.source_all.set_active(True)
        self.source_all.connect("toggled", lambda b: self._filter_alerts("all"))
        source_box.append(self.source_all)

        self.source_noaa = Gtk.ToggleButton(label="Weather")
        self.source_noaa.connect("toggled", lambda b: self._filter_alerts("noaa"))
        source_box.append(self.source_noaa)

        self.source_volcano = Gtk.ToggleButton(label="Volcano")
        self.source_volcano.connect("toggled", lambda b: self._filter_alerts("volcano"))
        source_box.append(self.source_volcano)

        self.source_fema = Gtk.ToggleButton(label="FEMA")
        self.source_fema.connect("toggled", lambda b: self._filter_alerts("fema"))
        source_box.append(self.source_fema)

        self.current_filter = "all"
        self.append(source_box)

        # Alerts list in scrolled window
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_vexpand(True)

        self.alerts_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.alerts_box.set_margin_top(10)
        scrolled.set_child(self.alerts_box)

        self.append(scrolled)

        # Show unavailable message if plugin not loaded
        if not EAS_AVAILABLE:
            self._show_unavailable_message()

    def _show_unavailable_message(self):
        """Show message when EAS plugin is not available"""
        # Clear alerts box
        while child := self.alerts_box.get_first_child():
            self.alerts_box.remove(child)

        # Add error message
        error_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=15)
        error_box.set_valign(Gtk.Align.CENTER)
        error_box.set_halign(Gtk.Align.CENTER)
        error_box.set_vexpand(True)

        icon = Gtk.Image.new_from_icon_name("dialog-warning-symbolic")
        icon.set_pixel_size(64)
        error_box.append(icon)

        label = Gtk.Label(label="EAS Alerts Plugin Not Available")
        label.add_css_class("title-2")
        error_box.append(label)

        desc = Gtk.Label(label="The Emergency Alert System plugin could not be loaded.\nCheck that plugins/eas_alerts.py exists.")
        desc.add_css_class("dim-label")
        desc.set_justify(Gtk.Justification.CENTER)
        error_box.append(desc)

        self.alerts_box.append(error_box)

        self.status_icon.set_from_icon_name("dialog-error-symbolic")
        self.status_label.set_text("Plugin not available")

    def _refresh_alerts(self):
        """Refresh alerts from all sources"""
        if not self.plugin:
            return

        self.status_label.set_text("Checking for alerts...")
        self.status_icon.set_from_icon_name("content-loading-symbolic")

        def fetch_alerts():
            try:
                alerts = self.plugin.check_all_alerts()
                GLib.idle_add(self._update_alerts_display, alerts)
            except Exception as e:
                logger.error(f"[EAS Panel] Error fetching alerts: {e}")
                GLib.idle_add(self._show_error, str(e))

        thread = threading.Thread(target=fetch_alerts, daemon=True)
        thread.start()

    def _update_alerts_display(self, alerts: List['Alert']):
        """Update the alerts display (must be called from main thread)"""
        self.alerts = alerts

        # Update status
        active_count = sum(1 for a in alerts if a.is_active())
        self.status_label.set_text(f"{active_count} active alerts")

        if active_count > 0:
            # Check for extreme/severe
            has_extreme = any(a.severity.value == "Extreme" for a in alerts if a.is_active())
            has_severe = any(a.severity.value == "Severe" for a in alerts if a.is_active())

            if has_extreme:
                self.status_icon.set_from_icon_name("dialog-error-symbolic")
            elif has_severe:
                self.status_icon.set_from_icon_name("dialog-warning-symbolic")
            else:
                self.status_icon.set_from_icon_name("dialog-information-symbolic")
        else:
            self.status_icon.set_from_icon_name("emblem-default-symbolic")

        self.last_update_label.set_text(f"Updated: {datetime.now().strftime('%H:%M:%S')}")

        # Apply current filter
        self._display_filtered_alerts()

    def _display_filtered_alerts(self):
        """Display alerts based on current filter"""
        # Clear existing alerts
        while child := self.alerts_box.get_first_child():
            self.alerts_box.remove(child)

        # Filter alerts
        filtered = self.alerts
        if self.current_filter == "noaa":
            filtered = [a for a in self.alerts if a.source.value == "NOAA/NWS"]
        elif self.current_filter == "volcano":
            filtered = [a for a in self.alerts if a.source.value == "USGS Volcano"]
        elif self.current_filter == "fema":
            filtered = [a for a in self.alerts if a.source.value == "FEMA iPAWS"]

        # Only show active alerts
        filtered = [a for a in filtered if a.is_active()]

        if not filtered:
            # Show no alerts message
            no_alerts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
            no_alerts.set_valign(Gtk.Align.CENTER)
            no_alerts.set_halign(Gtk.Align.CENTER)
            no_alerts.set_vexpand(True)

            icon = Gtk.Image.new_from_icon_name("weather-clear-symbolic")
            icon.set_pixel_size(48)
            no_alerts.append(icon)

            label = Gtk.Label(label="No Active Alerts")
            label.add_css_class("title-3")
            no_alerts.append(label)

            desc = Gtk.Label(label="All clear - no emergency alerts for your configured areas.")
            desc.add_css_class("dim-label")
            no_alerts.append(desc)

            self.alerts_box.append(no_alerts)
        else:
            # Sort by severity (Extreme first)
            severity_order = {"Extreme": 0, "Severe": 1, "Moderate": 2, "Minor": 3, "Unknown": 4}
            filtered.sort(key=lambda a: severity_order.get(a.severity.value, 5))

            # Add alert cards
            for alert in filtered:
                card = AlertCard(alert)
                self.alerts_box.append(card)

    def _filter_alerts(self, source: str):
        """Filter alerts by source"""
        # Update toggle button states
        self.source_all.set_active(source == "all")
        self.source_noaa.set_active(source == "noaa")
        self.source_volcano.set_active(source == "volcano")
        self.source_fema.set_active(source == "fema")

        self.current_filter = source
        self._display_filtered_alerts()

    def _show_error(self, error_msg: str):
        """Show error message"""
        self.status_icon.set_from_icon_name("dialog-error-symbolic")
        self.status_label.set_text(f"Error: {error_msg[:50]}")

    def _on_settings_clicked(self, button):
        """Open settings dialog"""
        dialog = Adw.MessageDialog.new(
            self.main_window,
            "EAS Configuration",
            "Emergency Alert System settings can be configured in:\n\n"
            "~/.config/meshforge/plugins/eas_alerts.ini\n\n"
            "Configure:\n"
            "- Location (SAME codes for your county)\n"
            "- Alert severity filters\n"
            "- Notification preferences\n"
            "- Mesh broadcast settings"
        )
        dialog.add_response("close", "Close")
        dialog.add_response("open", "Open Config")
        dialog.set_default_response("close")
        dialog.connect("response", self._on_settings_response)
        dialog.present()

    def _on_settings_response(self, dialog, response):
        """Handle settings dialog response"""
        if response == "open":
            # Open config file in default editor
            import subprocess
            config_path = "~/.config/meshforge/plugins/eas_alerts.ini"
            try:
                subprocess.run(["xdg-open", config_path], check=False)
            except Exception as e:
                logger.error(f"Failed to open config: {e}")

    def cleanup(self):
        """Clean up resources"""
        if self.plugin:
            try:
                self.plugin.deactivate()
            except Exception:
                pass
