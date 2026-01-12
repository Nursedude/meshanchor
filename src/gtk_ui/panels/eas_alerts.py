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

        # Load location settings
        self._load_location_settings()

        self._init_plugin()
        self._build_ui()
        self._refresh_alerts()

    def _load_location_settings(self):
        """Load user location settings"""
        import os
        from pathlib import Path

        # Default location (Hawaii - user's example)
        self.user_lat = 19.435175
        self.user_lon = -155.213842
        self.show_all_alerts = False

        # Try to load from settings
        try:
            from utils.paths import get_real_user_home
            config_dir = get_real_user_home() / ".config" / "meshforge"
        except ImportError:
            config_dir = Path.home() / ".config" / "meshforge"

        settings_file = config_dir / "eas_location.json"
        if settings_file.exists():
            try:
                import json
                with open(settings_file) as f:
                    settings = json.load(f)
                    self.user_lat = settings.get('latitude', self.user_lat)
                    self.user_lon = settings.get('longitude', self.user_lon)
                    self.show_all_alerts = settings.get('show_all_alerts', False)
            except Exception as e:
                logger.debug(f"[EAS] Could not load location settings: {e}")

    def _save_location_settings(self):
        """Save user location settings"""
        import os
        from pathlib import Path

        try:
            from utils.paths import get_real_user_home
            config_dir = get_real_user_home() / ".config" / "meshforge"
        except ImportError:
            config_dir = Path.home() / ".config" / "meshforge"

        try:
            config_dir.mkdir(parents=True, exist_ok=True)
            settings_file = config_dir / "eas_location.json"

            import json
            settings = {
                'latitude': self.user_lat,
                'longitude': self.user_lon,
                'show_all_alerts': self.show_all_alerts
            }
            with open(settings_file, 'w') as f:
                json.dump(settings, f, indent=2)
            logger.info(f"[EAS] Saved location: {self.user_lat}, {self.user_lon}")
        except Exception as e:
            logger.error(f"[EAS] Could not save location settings: {e}")

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

        # Location settings frame
        location_frame = Gtk.Frame()
        location_frame.set_label("Your Location")
        location_frame.set_margin_top(10)

        location_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        location_box.set_margin_start(10)
        location_box.set_margin_end(10)
        location_box.set_margin_top(8)
        location_box.set_margin_bottom(8)

        # Lat/Lon row
        coords_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)

        lat_label = Gtk.Label(label="Latitude:")
        coords_row.append(lat_label)

        self.lat_entry = Gtk.Entry()
        self.lat_entry.set_text(str(self.user_lat))
        self.lat_entry.set_width_chars(12)
        self.lat_entry.set_placeholder_text("e.g., 19.435175")
        self.lat_entry.set_tooltip_text("Your latitude (decimal degrees)")
        coords_row.append(self.lat_entry)

        lon_label = Gtk.Label(label="Longitude:")
        lon_label.set_margin_start(15)
        coords_row.append(lon_label)

        self.lon_entry = Gtk.Entry()
        self.lon_entry.set_text(str(self.user_lon))
        self.lon_entry.set_width_chars(12)
        self.lon_entry.set_placeholder_text("e.g., -155.213842")
        self.lon_entry.set_tooltip_text("Your longitude (decimal degrees)")
        coords_row.append(self.lon_entry)

        # Apply location button
        apply_btn = Gtk.Button(label="Apply")
        apply_btn.set_tooltip_text("Apply location and refresh alerts")
        apply_btn.connect("clicked", self._on_apply_location)
        apply_btn.set_margin_start(15)
        coords_row.append(apply_btn)

        location_box.append(coords_row)

        # Show all alerts toggle
        all_alerts_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)

        self.show_all_switch = Gtk.Switch()
        self.show_all_switch.set_active(self.show_all_alerts)
        self.show_all_switch.connect("notify::active", self._on_show_all_toggled)
        all_alerts_row.append(self.show_all_switch)

        all_alerts_label = Gtk.Label(label="Show alerts outside my area (nationwide)")
        all_alerts_label.set_xalign(0)
        all_alerts_row.append(all_alerts_label)

        location_box.append(all_alerts_row)

        # Current location display
        self.location_label = Gtk.Label()
        self.location_label.set_xalign(0)
        self.location_label.add_css_class("dim-label")
        self._update_location_label()
        location_box.append(self.location_label)

        location_frame.set_child(location_box)
        self.append(location_frame)

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

        # Update plugin config with user's location
        if hasattr(self.plugin, '_config') and self.plugin._config:
            if not self.plugin._config.has_section('location'):
                self.plugin._config.add_section('location')
            self.plugin._config.set('location', 'latitude', str(self.user_lat))
            self.plugin._config.set('location', 'longitude', str(self.user_lon))

        def fetch_alerts():
            try:
                if self.show_all_alerts:
                    # Fetch nationwide alerts (no location filter)
                    alerts = self._fetch_nationwide_alerts()
                else:
                    # Fetch local alerts based on user's location
                    alerts = self.plugin.check_all_alerts()
                GLib.idle_add(self._update_alerts_display, alerts)
            except Exception as e:
                logger.error(f"[EAS Panel] Error fetching alerts: {e}")
                GLib.idle_add(self._show_error, str(e))

        thread = threading.Thread(target=fetch_alerts, daemon=True)
        thread.start()

    def _fetch_nationwide_alerts(self):
        """Fetch alerts from all US without location filtering"""
        import urllib.request
        import json

        all_alerts = []

        # NOAA/NWS nationwide alerts
        try:
            url = "https://api.weather.gov/alerts/active?status=actual"
            req = urllib.request.Request(url)
            req.add_header('User-Agent', 'MeshForge/1.0')
            req.add_header('Accept', 'application/geo+json')

            with urllib.request.urlopen(req, timeout=15) as response:
                data = json.loads(response.read().decode('utf-8'))

            if 'features' in data:
                for feature in data['features'][:50]:  # Limit to 50 for performance
                    props = feature.get('properties', {})
                    if EAS_AVAILABLE:
                        from plugins.eas_alerts import Alert, AlertSeverity, AlertSource
                        try:
                            severity_map = {
                                'Extreme': AlertSeverity.EXTREME,
                                'Severe': AlertSeverity.SEVERE,
                                'Moderate': AlertSeverity.MODERATE,
                                'Minor': AlertSeverity.MINOR
                            }
                            severity = severity_map.get(props.get('severity', ''), AlertSeverity.UNKNOWN)

                            alert = Alert(
                                title=props.get('headline', props.get('event', 'Alert')),
                                description=props.get('description', ''),
                                severity=severity,
                                source=AlertSource.NOAA,
                                event_type=props.get('event', ''),
                                areas=props.get('areaDesc', '').split('; ') if props.get('areaDesc') else [],
                            )
                            all_alerts.append(alert)
                        except Exception as e:
                            logger.debug(f"[EAS] Could not parse alert: {e}")

        except Exception as e:
            logger.error(f"[EAS] Error fetching nationwide alerts: {e}")

        # Also get local alerts if plugin available
        if self.plugin:
            try:
                local_alerts = self.plugin.check_all_alerts()
                # Merge, avoiding duplicates
                existing_titles = {a.title for a in all_alerts}
                for alert in local_alerts:
                    if alert.title not in existing_titles:
                        all_alerts.append(alert)
            except Exception as e:
                logger.debug(f"[EAS] Could not get local alerts: {e}")

        return all_alerts

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

    def _update_location_label(self):
        """Update the location display label"""
        if self.show_all_alerts:
            self.location_label.set_text(f"Location: {self.user_lat:.4f}, {self.user_lon:.4f} (showing nationwide)")
        else:
            self.location_label.set_text(f"Location: {self.user_lat:.4f}, {self.user_lon:.4f} (local alerts only)")

    def _on_apply_location(self, button):
        """Apply new location settings"""
        try:
            new_lat = float(self.lat_entry.get_text().strip())
            new_lon = float(self.lon_entry.get_text().strip())

            # Validate ranges
            if not (-90 <= new_lat <= 90):
                self.main_window.set_status_message("Latitude must be between -90 and 90")
                return
            if not (-180 <= new_lon <= 180):
                self.main_window.set_status_message("Longitude must be between -180 and 180")
                return

            self.user_lat = new_lat
            self.user_lon = new_lon
            self._save_location_settings()
            self._update_location_label()
            self.main_window.set_status_message(f"Location set to {new_lat:.4f}, {new_lon:.4f}")

            # Refresh alerts with new location
            self._refresh_alerts()

        except ValueError:
            self.main_window.set_status_message("Invalid coordinates - use decimal degrees (e.g., 19.435)")

    def _on_show_all_toggled(self, switch, gparam):
        """Handle show all alerts toggle"""
        self.show_all_alerts = switch.get_active()
        self._save_location_settings()
        self._update_location_label()
        self.main_window.set_status_message(
            "Showing nationwide alerts" if self.show_all_alerts else "Showing local alerts only"
        )
        self._refresh_alerts()

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
            from pathlib import Path
            # Use get_real_user_home to handle sudo properly
            try:
                from utils.paths import get_real_user_home
                config_path = str(get_real_user_home() / ".config/meshforge/plugins/eas_alerts.ini")
            except ImportError:
                config_path = str(Path.home() / ".config/meshforge/plugins/eas_alerts.ini")
            try:
                subprocess.run(["xdg-open", config_path], check=False, timeout=10)
            except Exception as e:
                logger.error(f"Failed to open config: {e}")

    def cleanup(self):
        """Clean up resources"""
        if self.plugin:
            try:
                self.plugin.deactivate()
            except Exception as e:
                logger.debug(f"Plugin cleanup (non-critical): {e}")
