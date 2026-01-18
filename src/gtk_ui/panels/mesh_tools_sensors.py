"""
Sensors Tab Mixin - Extracted from mesh_tools.py

Handles sensor data display including:
- Environment sensors (temperature, humidity, pressure)
- Air quality sensors (PM2.5, PM10, CO2, IAQ)
- Detection sensors (motion, door alerts)
"""

import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk, GLib
import threading

# Import logging
try:
    from utils.logging_utils import get_logger
    logger = get_logger(__name__)
except ImportError:
    import logging
    logger = logging.getLogger(__name__)


class SensorsTabMixin:
    """
    Mixin providing Sensors tab functionality.

    Requires parent class to provide:
    - self._notebook: Gtk.Notebook to add tab to
    """

    def _add_sensors_tab(self):
        """Add Sensors tab showing telemetry data from mesh nodes"""
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=15)
        box.set_margin_start(15)
        box.set_margin_end(15)
        box.set_margin_top(15)
        box.set_margin_bottom(15)

        # Info header
        info_label = Gtk.Label(
            label="Telemetry data from Meshtastic nodes with sensors (BME280, PMSA003I, etc.)"
        )
        info_label.set_xalign(0)
        info_label.add_css_class("dim-label")
        info_label.set_wrap(True)
        box.append(info_label)

        # Environment Sensors Frame
        env_frame = Gtk.Frame()
        env_frame.set_label("Environment Sensors")
        env_frame.set_vexpand(True)
        env_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        env_box.set_margin_start(15)
        env_box.set_margin_end(15)
        env_box.set_margin_top(10)
        env_box.set_margin_bottom(10)

        # Environment sensor list: Node, Temp, Humidity, Pressure, Battery, Last Update
        self._env_store = Gtk.ListStore(str, str, str, str, str, str)
        self._env_tree = Gtk.TreeView(model=self._env_store)
        self._env_tree.set_headers_visible(True)

        env_columns = [
            ("Node", 150),
            ("Temperature", 100),
            ("Humidity", 100),
            ("Pressure", 100),
            ("Battery", 80),
            ("Last Update", 120),
        ]

        for i, (title, width) in enumerate(env_columns):
            renderer = Gtk.CellRendererText()
            column = Gtk.TreeViewColumn(title, renderer, text=i)
            column.set_resizable(True)
            column.set_min_width(width)
            self._env_tree.append_column(column)

        env_scroll = Gtk.ScrolledWindow()
        env_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        env_scroll.set_min_content_height(120)
        env_scroll.set_vexpand(True)
        env_scroll.set_child(self._env_tree)
        env_box.append(env_scroll)

        env_frame.set_child(env_box)
        box.append(env_frame)

        # Air Quality Sensors Frame
        aq_frame = Gtk.Frame()
        aq_frame.set_label("Air Quality Sensors")
        aq_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        aq_box.set_margin_start(15)
        aq_box.set_margin_end(15)
        aq_box.set_margin_top(10)
        aq_box.set_margin_bottom(10)

        # Air quality list: Node, PM2.5, PM10, CO2, IAQ, Last Update
        self._aq_store = Gtk.ListStore(str, str, str, str, str, str)
        self._aq_tree = Gtk.TreeView(model=self._aq_store)
        self._aq_tree.set_headers_visible(True)

        aq_columns = [
            ("Node", 150),
            ("PM2.5", 80),
            ("PM10", 80),
            ("CO2 (ppm)", 100),
            ("IAQ", 80),
            ("Last Update", 120),
        ]

        for i, (title, width) in enumerate(aq_columns):
            renderer = Gtk.CellRendererText()
            column = Gtk.TreeViewColumn(title, renderer, text=i)
            column.set_resizable(True)
            column.set_min_width(width)
            self._aq_tree.append_column(column)

        aq_scroll = Gtk.ScrolledWindow()
        aq_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        aq_scroll.set_min_content_height(100)
        aq_scroll.set_child(self._aq_tree)
        aq_box.append(aq_scroll)

        aq_frame.set_child(aq_box)
        box.append(aq_frame)

        # Detection Sensors Frame
        det_frame = Gtk.Frame()
        det_frame.set_label("Detection Sensors (Motion/Door Alerts)")
        det_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        det_box.set_margin_start(15)
        det_box.set_margin_end(15)
        det_box.set_margin_top(10)
        det_box.set_margin_bottom(10)

        # Detection sensor list: Node, Sensor, State, Last Triggered, Count
        self._det_store = Gtk.ListStore(str, str, str, str, str)
        self._det_tree = Gtk.TreeView(model=self._det_store)
        self._det_tree.set_headers_visible(True)

        det_columns = [
            ("Node", 150),
            ("Sensor", 120),
            ("State", 100),
            ("Last Triggered", 150),
            ("Count", 80),
        ]

        for i, (title, width) in enumerate(det_columns):
            renderer = Gtk.CellRendererText()
            column = Gtk.TreeViewColumn(title, renderer, text=i)
            column.set_resizable(True)
            column.set_min_width(width)
            self._det_tree.append_column(column)

        det_scroll = Gtk.ScrolledWindow()
        det_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        det_scroll.set_min_content_height(80)
        det_scroll.set_child(self._det_tree)
        det_box.append(det_scroll)

        det_frame.set_child(det_box)
        box.append(det_frame)

        # Controls row
        controls = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)

        refresh_btn = Gtk.Button(label="Refresh Sensors")
        refresh_btn.connect("clicked", self._on_refresh_sensors)
        controls.append(refresh_btn)

        self._sensor_stats_label = Gtk.Label(label="No sensor data loaded")
        self._sensor_stats_label.set_xalign(0)
        self._sensor_stats_label.add_css_class("dim-label")
        self._sensor_stats_label.set_hexpand(True)
        controls.append(self._sensor_stats_label)

        box.append(controls)

        scrolled.set_child(box)

        # Tab label
        tab_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        tab_box.append(Gtk.Image.new_from_icon_name("weather-few-clouds-symbolic"))
        tab_box.append(Gtk.Label(label="Sensors"))

        self._notebook.append_page(scrolled, tab_box)

    # =========================================================================
    # Sensor Handlers
    # =========================================================================

    def _on_refresh_sensors(self, button):
        """Refresh sensor data from mesh nodes"""
        button.set_sensitive(False)
        self._sensor_stats_label.set_label("Loading sensor data...")

        def do_refresh():
            env_nodes = []
            aq_nodes = []
            det_alerts = []

            try:
                # Try to get data from node tracker
                # First check if main_window has a tracker
                tracker = None
                if hasattr(self, 'main_window') and hasattr(self.main_window, 'node_tracker'):
                    tracker = self.main_window.node_tracker

                # Fall back to creating/importing tracker
                if not tracker:
                    try:
                        from gateway.node_tracker import UnifiedNodeTracker
                        tracker = UnifiedNodeTracker()
                        # Cache is auto-loaded in __init__
                    except ImportError:
                        logger.warning("UnifiedNodeTracker not available")
                        tracker = None

                if tracker and hasattr(tracker, 'get_all_nodes'):
                    for node in tracker.get_all_nodes():
                        if not hasattr(node, 'telemetry') or not node.telemetry:
                            continue

                        t = node.telemetry
                        node_name = node.name or node.id[:12]
                        last_update = t.timestamp.strftime("%H:%M:%S") if t.timestamp else "N/A"

                        # Environment sensors
                        if t.temperature is not None or t.humidity is not None:
                            temp = f"{t.temperature:.1f}°C" if t.temperature else "—"
                            hum = f"{t.humidity:.0f}%" if t.humidity else "—"
                            pres = f"{t.pressure:.0f} hPa" if t.pressure else "—"
                            batt = f"{t.battery_level}%" if t.battery_level else "—"
                            env_nodes.append((node_name, temp, hum, pres, batt, last_update))

                        # Air quality sensors
                        if t.air_quality and t.air_quality.has_data():
                            aq = t.air_quality
                            pm25 = str(aq.pm25_standard) if aq.pm25_standard else "—"
                            pm10 = str(aq.pm100_standard) if aq.pm100_standard else "—"
                            co2 = str(aq.co2) if aq.co2 else "—"
                            iaq = str(aq.iaq) if aq.iaq else "—"
                            aq_nodes.append((node_name, pm25, pm10, co2, iaq, last_update))

                        # Detection sensors
                        for sensor in t.detection_sensors:
                            state = "TRIGGERED" if sensor.triggered else "Clear"
                            last_trig = sensor.last_triggered.strftime("%H:%M:%S") if sensor.last_triggered else "Never"
                            det_alerts.append((node_name, sensor.name, state, last_trig, str(sensor.trigger_count)))

            except Exception as e:
                logger.error(f"Error loading sensor data: {e}")
                # Add placeholder if no data
                if not env_nodes:
                    env_nodes.append(("No nodes", "—", "—", "—", "—", "Connect to mesh"))

            # Update UI
            GLib.idle_add(self._update_sensor_display, env_nodes, aq_nodes, det_alerts)
            GLib.idle_add(button.set_sensitive, True)

        threading.Thread(target=do_refresh, daemon=True).start()

    def _update_sensor_display(self, env_nodes, aq_nodes, det_alerts):
        """Update sensor display lists (called on main thread)"""
        # Update environment sensors
        self._env_store.clear()
        for row in env_nodes:
            self._env_store.append(row)

        # Update air quality sensors
        self._aq_store.clear()
        for row in aq_nodes:
            self._aq_store.append(row)

        # Update detection sensors
        self._det_store.clear()
        for row in det_alerts:
            self._det_store.append(row)

        # Update stats
        total = len(env_nodes) + len(aq_nodes) + len(det_alerts)
        self._sensor_stats_label.set_label(
            f"Environment: {len(env_nodes)} | Air Quality: {len(aq_nodes)} | Detection: {len(det_alerts)}"
        )
        return False
