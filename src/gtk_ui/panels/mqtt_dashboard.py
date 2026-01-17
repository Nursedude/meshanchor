"""
MQTT Dashboard Panel

GTK4 interface for MQTT bridge plugin management:
- Connection status and configuration
- Message monitoring (published/received)
- Topic subscription management
- Broker connection settings
- Nodeless monitoring mode (no hardware required)
"""

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, GLib
import threading
import logging
from datetime import datetime
from typing import Optional, Dict

logger = logging.getLogger(__name__)

# Import MQTT plugin
try:
    from plugins.mqtt_bridge import MQTTBridgePlugin, DEFAULT_MQTT_BROKER, DEFAULT_MQTT_PORT_TLS
    HAS_MQTT_PLUGIN = True
except ImportError:
    try:
        from src.plugins.mqtt_bridge import MQTTBridgePlugin, DEFAULT_MQTT_BROKER, DEFAULT_MQTT_PORT_TLS
        HAS_MQTT_PLUGIN = True
    except ImportError:
        HAS_MQTT_PLUGIN = False
        MQTTBridgePlugin = None
        DEFAULT_MQTT_BROKER = "mqtt.meshtastic.org"
        DEFAULT_MQTT_PORT_TLS = 8883

# Import nodeless subscriber for hardware-free monitoring
try:
    from monitoring.mqtt_subscriber import MQTTNodelessSubscriber
    HAS_NODELESS = True
except ImportError:
    try:
        from src.monitoring.mqtt_subscriber import MQTTNodelessSubscriber
        HAS_NODELESS = True
    except ImportError:
        HAS_NODELESS = False
        MQTTNodelessSubscriber = None

# Import path utility
try:
    from utils.paths import get_real_user_home
except ImportError:
    try:
        from src.utils.paths import get_real_user_home
    except ImportError:
        import os
        from pathlib import Path
        def get_real_user_home():
            sudo_user = os.environ.get('SUDO_USER')
            if sudo_user and sudo_user != 'root':
                return Path(f'/home/{sudo_user}')
            return Path.home()


class MQTTDashboardPanel(Gtk.Box):
    """MQTT Dashboard panel for MeshForge."""

    def __init__(self, main_window=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.main_window = main_window
        self.set_margin_start(15)
        self.set_margin_end(15)
        self.set_margin_top(10)
        self.set_margin_bottom(10)

        self._plugin: Optional[MQTTBridgePlugin] = None
        self._nodeless: Optional[MQTTNodelessSubscriber] = None
        self._message_count = 0
        self._nodeless_node_count = 0
        self._update_timer = None

        self._build_ui()
        self._start_updates()

    def _build_ui(self):
        """Build the panel UI."""
        # Title
        title = Gtk.Label(label="MQTT Dashboard")
        title.add_css_class("title-2")
        title.set_xalign(0)
        self.append(title)

        desc = Gtk.Label(label="Bridge Meshtastic mesh to MQTT brokers for Home Assistant, Node-RED, and more")
        desc.set_xalign(0)
        desc.add_css_class("dim-label")
        self.append(desc)

        if not HAS_MQTT_PLUGIN:
            self._show_plugin_missing()
            return

        # Nodeless monitoring section (P1 feature)
        if HAS_NODELESS:
            self._build_nodeless_section()

        # Connection section
        self._build_connection_section()

        # Configuration section
        self._build_config_section()

        # Message log section
        self._build_message_log()

    def _show_plugin_missing(self):
        """Show message when MQTT plugin is not available."""
        msg_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        msg_box.set_margin_top(20)

        icon = Gtk.Label(label="MQTT plugin not available")
        icon.add_css_class("title-3")
        msg_box.append(icon)

        info = Gtk.Label(label="Install paho-mqtt: pip install paho-mqtt")
        info.add_css_class("dim-label")
        msg_box.append(info)

        self.append(msg_box)

    def _build_nodeless_section(self):
        """Build nodeless monitoring section - monitor mesh without hardware."""
        frame = Gtk.Frame()
        frame.set_label("Nodeless Monitoring (No Hardware Required)")

        nodeless_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        nodeless_box.set_margin_start(15)
        nodeless_box.set_margin_end(15)
        nodeless_box.set_margin_top(10)
        nodeless_box.set_margin_bottom(10)

        # Info label
        info = Gtk.Label(label="Monitor the mesh network via MQTT without local Meshtastic hardware")
        info.set_xalign(0)
        info.add_css_class("dim-label")
        nodeless_box.append(info)

        # Status row
        status_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=20)

        self.nodeless_status = Gtk.Label(label="● Inactive")
        self.nodeless_status.set_xalign(0)
        self.nodeless_status.add_css_class("dim-label")
        status_row.append(self.nodeless_status)

        self.nodeless_nodes = Gtk.Label(label="Nodes: 0")
        self.nodeless_nodes.set_xalign(0)
        status_row.append(self.nodeless_nodes)

        self.nodeless_online = Gtk.Label(label="Online: 0")
        self.nodeless_online.set_xalign(0)
        status_row.append(self.nodeless_online)

        self.nodeless_msgs = Gtk.Label(label="Messages: 0")
        self.nodeless_msgs.set_xalign(0)
        self.nodeless_msgs.set_hexpand(True)
        status_row.append(self.nodeless_msgs)

        nodeless_box.append(status_row)

        # Control buttons
        btn_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

        self.nodeless_start_btn = Gtk.Button(label="Start Monitoring")
        self.nodeless_start_btn.add_css_class("suggested-action")
        self.nodeless_start_btn.connect("clicked", self._on_nodeless_start)
        btn_row.append(self.nodeless_start_btn)

        self.nodeless_stop_btn = Gtk.Button(label="Stop")
        self.nodeless_stop_btn.set_sensitive(False)
        self.nodeless_stop_btn.connect("clicked", self._on_nodeless_stop)
        btn_row.append(self.nodeless_stop_btn)

        export_btn = Gtk.Button(label="Export Map")
        export_btn.set_tooltip_text("Generate coverage map from discovered nodes")
        export_btn.connect("clicked", self._on_export_nodeless_map)
        btn_row.append(export_btn)

        nodeless_box.append(btn_row)

        frame.set_child(nodeless_box)
        self.append(frame)

    def _on_nodeless_start(self, button):
        """Start nodeless MQTT monitoring."""
        self._log_message("Starting nodeless monitoring...")
        button.set_sensitive(False)

        def do_start():
            try:
                self._nodeless = MQTTNodelessSubscriber()

                # Register callbacks
                self._nodeless.register_node_callback(self._on_nodeless_node)
                self._nodeless.register_message_callback(self._on_nodeless_message)

                if self._nodeless.start():
                    self._log_message("Nodeless monitoring started - discovering nodes...")
                    GLib.idle_add(self._update_nodeless_status, True)
                else:
                    self._log_message("Failed to start nodeless monitoring")
                    GLib.idle_add(button.set_sensitive, True)

            except Exception as e:
                self._log_message(f"Nodeless start error: {e}")
                GLib.idle_add(button.set_sensitive, True)

        threading.Thread(target=do_start, daemon=True).start()

    def _on_nodeless_stop(self, button):
        """Stop nodeless monitoring."""
        if self._nodeless:
            self._nodeless.stop()
            self._nodeless = None
            self._log_message("Nodeless monitoring stopped")
            self._update_nodeless_status(False)

    def _update_nodeless_status(self, active: bool):
        """Update nodeless monitoring status UI."""
        if active:
            self.nodeless_status.set_label("● Active")
            self.nodeless_status.remove_css_class("dim-label")
            self.nodeless_status.add_css_class("success")
            self.nodeless_start_btn.set_sensitive(False)
            self.nodeless_stop_btn.set_sensitive(True)
        else:
            self.nodeless_status.set_label("● Inactive")
            self.nodeless_status.remove_css_class("success")
            self.nodeless_status.add_css_class("dim-label")
            self.nodeless_start_btn.set_sensitive(True)
            self.nodeless_stop_btn.set_sensitive(False)

    def _on_nodeless_node(self, node):
        """Handle node discovered via nodeless monitoring."""
        self._nodeless_node_count += 1
        if self._nodeless:
            stats = self._nodeless.get_stats()
            GLib.idle_add(self._update_nodeless_stats, stats)

    def _on_nodeless_message(self, message):
        """Handle message received via nodeless monitoring."""
        self._log_message(f"[MQTT] {message.from_id}: {message.text[:100]}")
        if self._nodeless:
            stats = self._nodeless.get_stats()
            GLib.idle_add(self._update_nodeless_stats, stats)

    def _update_nodeless_stats(self, stats: Dict):
        """Update nodeless statistics display."""
        self.nodeless_nodes.set_label(f"Nodes: {stats.get('node_count', 0)}")
        self.nodeless_online.set_label(f"Online: {stats.get('online_count', 0)}")
        self.nodeless_msgs.set_label(f"Messages: {stats.get('message_count', 0)}")

    def _on_export_nodeless_map(self, button):
        """Export coverage map from nodeless discovered nodes."""
        if not self._nodeless:
            self._log_message("Start nodeless monitoring first to discover nodes")
            return

        def do_export():
            try:
                # Import coverage map generator
                try:
                    from utils.coverage_map import CoverageMapGenerator
                except ImportError:
                    from src.utils.coverage_map import CoverageMapGenerator

                # Get GeoJSON from nodeless subscriber
                geojson = self._nodeless.get_geojson()
                node_count = len(geojson.get('features', []))

                if node_count == 0:
                    self._log_message("No nodes with position data to map")
                    return

                # Generate map
                generator = CoverageMapGenerator()
                generator.add_nodes_from_geojson(geojson)
                output_path = generator.generate()

                self._log_message(f"Coverage map generated: {output_path}")

                # Try to open in browser
                import subprocess
                import os
                user = os.environ.get('SUDO_USER', os.environ.get('USER', 'pi'))
                try:
                    subprocess.run(
                        ['sudo', '-u', user, 'xdg-open', output_path],
                        capture_output=True, timeout=10
                    )
                except Exception:
                    self._log_message(f"Open map manually: {output_path}")

            except Exception as e:
                self._log_message(f"Map export error: {e}")

        threading.Thread(target=do_export, daemon=True).start()

    def _build_connection_section(self):
        """Build connection status and controls."""
        frame = Gtk.Frame()
        frame.set_label("Connection Status")

        conn_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        conn_box.set_margin_start(15)
        conn_box.set_margin_end(15)
        conn_box.set_margin_top(10)
        conn_box.set_margin_bottom(10)

        # Status row
        status_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=20)

        self.connection_status = Gtk.Label(label="● Disconnected")
        self.connection_status.set_xalign(0)
        self.connection_status.add_css_class("warning")
        status_row.append(self.connection_status)

        self.broker_label = Gtk.Label(label="Broker: Not configured")
        self.broker_label.set_xalign(0)
        self.broker_label.add_css_class("dim-label")
        self.broker_label.set_hexpand(True)
        status_row.append(self.broker_label)

        conn_box.append(status_row)

        # Stats row
        stats_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=30)

        self.msgs_published = Gtk.Label(label="Published: 0")
        self.msgs_published.set_xalign(0)
        stats_row.append(self.msgs_published)

        self.msgs_received = Gtk.Label(label="Received: 0")
        self.msgs_received.set_xalign(0)
        stats_row.append(self.msgs_received)

        self.uptime_label = Gtk.Label(label="Uptime: --")
        self.uptime_label.set_xalign(0)
        stats_row.append(self.uptime_label)

        conn_box.append(stats_row)

        # Control buttons
        btn_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        btn_row.set_margin_top(5)

        self.connect_btn = Gtk.Button(label="Connect")
        self.connect_btn.add_css_class("suggested-action")
        self.connect_btn.connect("clicked", self._on_connect)
        btn_row.append(self.connect_btn)

        self.disconnect_btn = Gtk.Button(label="Disconnect")
        self.disconnect_btn.set_sensitive(False)
        self.disconnect_btn.connect("clicked", self._on_disconnect)
        btn_row.append(self.disconnect_btn)

        test_btn = Gtk.Button(label="Test Connection")
        test_btn.connect("clicked", self._on_test_connection)
        btn_row.append(test_btn)

        conn_box.append(btn_row)

        frame.set_child(conn_box)
        self.append(frame)

    def _build_config_section(self):
        """Build configuration section."""
        frame = Gtk.Frame()
        frame.set_label("Broker Configuration")

        config_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        config_box.set_margin_start(15)
        config_box.set_margin_end(15)
        config_box.set_margin_top(10)
        config_box.set_margin_bottom(10)

        # Broker row
        broker_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        broker_label = Gtk.Label(label="Broker:")
        broker_label.set_width_chars(12)
        broker_label.set_xalign(0)
        broker_row.append(broker_label)

        self.broker_entry = Gtk.Entry()
        self.broker_entry.set_text(DEFAULT_MQTT_BROKER)
        self.broker_entry.set_hexpand(True)
        self.broker_entry.set_placeholder_text("mqtt.meshtastic.org")
        broker_row.append(self.broker_entry)

        port_label = Gtk.Label(label="Port:")
        port_label.set_margin_start(10)
        broker_row.append(port_label)

        self.port_entry = Gtk.SpinButton.new_with_range(1, 65535, 1)
        self.port_entry.set_value(DEFAULT_MQTT_PORT_TLS)
        broker_row.append(self.port_entry)

        config_box.append(broker_row)

        # Auth row
        auth_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        user_label = Gtk.Label(label="Username:")
        user_label.set_width_chars(12)
        user_label.set_xalign(0)
        auth_row.append(user_label)

        self.username_entry = Gtk.Entry()
        self.username_entry.set_hexpand(True)
        self.username_entry.set_placeholder_text("(optional)")
        auth_row.append(self.username_entry)

        pass_label = Gtk.Label(label="Password:")
        pass_label.set_margin_start(10)
        auth_row.append(pass_label)

        self.password_entry = Gtk.Entry()
        self.password_entry.set_visibility(False)
        self.password_entry.set_hexpand(True)
        self.password_entry.set_placeholder_text("(optional)")
        auth_row.append(self.password_entry)

        config_box.append(auth_row)

        # Topic row
        topic_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        topic_label = Gtk.Label(label="Topic Prefix:")
        topic_label.set_width_chars(12)
        topic_label.set_xalign(0)
        topic_row.append(topic_label)

        self.topic_entry = Gtk.Entry()
        self.topic_entry.set_text("msh/US/2/e")
        self.topic_entry.set_hexpand(True)
        self.topic_entry.set_placeholder_text("msh/US/2/e")
        topic_row.append(self.topic_entry)

        config_box.append(topic_row)

        # Options row
        options_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=20)

        self.tls_check = Gtk.CheckButton(label="Use TLS")
        self.tls_check.set_active(True)
        options_row.append(self.tls_check)

        self.reconnect_check = Gtk.CheckButton(label="Auto-reconnect")
        self.reconnect_check.set_active(True)
        options_row.append(self.reconnect_check)

        self.publish_nodes_check = Gtk.CheckButton(label="Publish nodes")
        self.publish_nodes_check.set_active(True)
        options_row.append(self.publish_nodes_check)

        config_box.append(options_row)

        # Save button
        save_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        save_row.set_halign(Gtk.Align.END)

        save_btn = Gtk.Button(label="Save Configuration")
        save_btn.add_css_class("suggested-action")
        save_btn.connect("clicked", self._on_save_config)
        save_row.append(save_btn)

        config_box.append(save_row)

        frame.set_child(config_box)
        self.append(frame)

    def _build_message_log(self):
        """Build message log section."""
        frame = Gtk.Frame()
        frame.set_label("Message Log")

        log_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        log_box.set_margin_start(10)
        log_box.set_margin_end(10)
        log_box.set_margin_top(8)
        log_box.set_margin_bottom(8)

        # Log viewer
        scroll = Gtk.ScrolledWindow()
        scroll.set_min_content_height(150)
        scroll.set_max_content_height(250)
        scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)

        self.log_view = Gtk.TextView()
        self.log_view.set_editable(False)
        self.log_view.set_monospace(True)
        self.log_view.set_wrap_mode(Gtk.WrapMode.WORD)
        self.log_buffer = self.log_view.get_buffer()
        self.log_buffer.set_text("MQTT messages will appear here when connected.\n\nTo get started:\n1. Configure broker settings above\n2. Click 'Connect'\n3. Messages will stream in real-time")

        scroll.set_child(self.log_view)
        log_box.append(scroll)

        # Control buttons
        btn_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        btn_row.set_halign(Gtk.Align.END)

        clear_btn = Gtk.Button(label="Clear")
        clear_btn.connect("clicked", lambda b: self.log_buffer.set_text(""))
        btn_row.append(clear_btn)

        copy_btn = Gtk.Button(label="Copy All")
        copy_btn.connect("clicked", self._copy_log)
        btn_row.append(copy_btn)

        log_box.append(btn_row)

        frame.set_child(log_box)
        self.append(frame)

    def _copy_log(self, button):
        """Copy log to clipboard."""
        text = self.log_buffer.get_text(
            self.log_buffer.get_start_iter(),
            self.log_buffer.get_end_iter(),
            True
        )
        clipboard = self.log_view.get_clipboard()
        clipboard.set(text)

    def _log_message(self, msg: str):
        """Add message to log."""
        timestamp = datetime.now().strftime("%H:%M:%S")
        log_line = f"[{timestamp}] {msg}\n"

        GLib.idle_add(self._append_log, log_line)

    def _append_log(self, text: str):
        """Append text to log buffer (GTK thread safe)."""
        end_iter = self.log_buffer.get_end_iter()
        self.log_buffer.insert(end_iter, text)

        # Auto-scroll
        end_mark = self.log_buffer.create_mark(None, self.log_buffer.get_end_iter(), False)
        self.log_view.scroll_mark_onscreen(end_mark)

    def _start_updates(self):
        """Start periodic status updates."""
        self._update_timer = GLib.timeout_add_seconds(3, self._update_status)

    def _update_status(self):
        """Update connection status."""
        if self._plugin and hasattr(self._plugin, 'is_connected'):
            connected = self._plugin.is_connected()
            GLib.idle_add(self._apply_status, connected)
        return True  # Continue timer

    def _apply_status(self, connected: bool):
        """Apply connection status to UI."""
        if connected:
            self.connection_status.set_label("● Connected")
            self.connection_status.remove_css_class("warning")
            self.connection_status.add_css_class("success")
            self.connect_btn.set_sensitive(False)
            self.disconnect_btn.set_sensitive(True)
        else:
            self.connection_status.set_label("● Disconnected")
            self.connection_status.remove_css_class("success")
            self.connection_status.add_css_class("warning")
            self.connect_btn.set_sensitive(True)
            self.disconnect_btn.set_sensitive(False)

    def _on_connect(self, button):
        """Handle connect button click."""
        self._log_message("Connecting to MQTT broker...")
        button.set_sensitive(False)

        def do_connect():
            try:
                if not self._plugin:
                    self._plugin = MQTTBridgePlugin()

                # Apply current settings
                config = {
                    'broker': self.broker_entry.get_text(),
                    'port': int(self.port_entry.get_value()),
                    'username': self.username_entry.get_text() or None,
                    'password': self.password_entry.get_text() or None,
                    'topic_prefix': self.topic_entry.get_text(),
                    'use_tls': self.tls_check.get_active(),
                    'auto_reconnect': self.reconnect_check.get_active(),
                }

                # Register message callback
                self._plugin.register_callback(self._on_mqtt_message)

                # Connect
                success = self._plugin.connect(
                    broker=config['broker'],
                    port=config['port'],
                    username=config['username'],
                    password=config['password'],
                )

                if success:
                    self._log_message(f"Connected to {config['broker']}:{config['port']}")
                    GLib.idle_add(self._apply_status, True)
                    GLib.idle_add(self.broker_label.set_label, f"Broker: {config['broker']}")
                else:
                    self._log_message("Connection failed - check settings")
                    GLib.idle_add(button.set_sensitive, True)

            except Exception as e:
                self._log_message(f"Connection error: {e}")
                GLib.idle_add(button.set_sensitive, True)

        threading.Thread(target=do_connect, daemon=True).start()

    def _on_disconnect(self, button):
        """Handle disconnect button click."""
        if self._plugin:
            self._plugin.disconnect()
            self._log_message("Disconnected from broker")
            self._apply_status(False)

    def _on_test_connection(self, button):
        """Test MQTT broker connection."""
        self._log_message(f"Testing connection to {self.broker_entry.get_text()}...")

        def do_test():
            sock = None
            try:
                import socket
                host = self.broker_entry.get_text()
                port = int(self.port_entry.get_value())

                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(5)
                result = sock.connect_ex((host, port))

                if result == 0:
                    self._log_message(f"SUCCESS: {host}:{port} is reachable")
                else:
                    self._log_message(f"FAILED: Cannot reach {host}:{port}")

            except Exception as e:
                self._log_message(f"Test error: {e}")
            finally:
                if sock:
                    try:
                        sock.close()
                    except Exception:  # Ignore errors during cleanup
                        pass

        threading.Thread(target=do_test, daemon=True).start()

    def _on_save_config(self, button):
        """Save configuration to file."""
        import json

        config = {
            'broker': self.broker_entry.get_text(),
            'port': int(self.port_entry.get_value()),
            'username': self.username_entry.get_text(),
            'password': self.password_entry.get_text(),
            'topic_prefix': self.topic_entry.get_text(),
            'use_tls': self.tls_check.get_active(),
            'auto_reconnect': self.reconnect_check.get_active(),
            'publish_nodes': self.publish_nodes_check.get_active(),
        }

        config_dir = get_real_user_home() / ".config" / "meshforge" / "plugins"
        config_dir.mkdir(parents=True, exist_ok=True)
        config_file = config_dir / "mqtt_bridge.json"

        try:
            config_file.write_text(json.dumps(config, indent=2))
            self._log_message(f"Configuration saved to {config_file}")
        except Exception as e:
            self._log_message(f"Save error: {e}")

    def _on_mqtt_message(self, topic: str, payload: bytes):
        """Handle incoming MQTT message."""
        try:
            msg = payload.decode('utf-8', errors='replace')[:200]
            self._log_message(f"[{topic}] {msg}")
            self._message_count += 1
            GLib.idle_add(self.msgs_received.set_label, f"Received: {self._message_count}")
        except Exception as e:
            logger.error(f"Message handling error: {e}")

    def cleanup(self):
        """Cleanup resources."""
        if self._update_timer:
            GLib.source_remove(self._update_timer)
            self._update_timer = None
        if self._plugin:
            self._plugin.disconnect()
        if self._nodeless:
            self._nodeless.stop()
            self._nodeless = None


__all__ = ['MQTTDashboardPanel']
