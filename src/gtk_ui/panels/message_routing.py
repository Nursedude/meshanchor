"""
Message Routing Visualization Panel

Real-time visualization of message flow between RNS and Meshtastic networks.
Shows:
- Flow diagram: RNS <-> Bridge <-> Meshtastic
- Message log with routing decisions and confidence
- Statistics: throughput, latency, success rates
"""

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, GLib, Pango
import threading
import logging
from datetime import datetime
from typing import Optional, Dict, List

logger = logging.getLogger(__name__)

# Import gateway bridge for stats
try:
    from gateway.rns_bridge import RNSMeshtasticBridge
    HAS_BRIDGE = True
except ImportError:
    HAS_BRIDGE = False
    RNSMeshtasticBridge = None

# Import network diagnostics for events
try:
    from utils.network_diagnostics import NetworkDiagnostics, EventCategory
    HAS_DIAGNOSTICS = True
except ImportError:
    try:
        from src.utils.network_diagnostics import NetworkDiagnostics, EventCategory
        HAS_DIAGNOSTICS = True
    except ImportError:
        HAS_DIAGNOSTICS = False
        NetworkDiagnostics = None


class MessageRoutingPanel(Gtk.Box):
    """Panel for visualizing message routing between networks."""

    def __init__(self, main_window=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.main_window = main_window
        self.set_margin_start(15)
        self.set_margin_end(15)
        self.set_margin_top(10)
        self.set_margin_bottom(10)

        # Statistics
        self._stats = {
            'rns_to_mesh': 0,
            'mesh_to_rns': 0,
            'bounced': 0,
            'errors': 0,
            'uptime': 0,
        }
        self._message_log: List[Dict] = []
        self._update_timer = None

        self._build_ui()
        self._start_updates()

    def _build_ui(self):
        """Build the panel UI."""
        # Title
        title = Gtk.Label(label="Message Routing")
        title.add_css_class("title-2")
        title.set_xalign(0)
        self.append(title)

        desc = Gtk.Label(label="Real-time visualization of message flow between RNS and Meshtastic")
        desc.set_xalign(0)
        desc.add_css_class("dim-label")
        self.append(desc)

        # Flow diagram section
        self._build_flow_diagram()

        # Statistics section
        self._build_stats_section()

        # Message log section
        self._build_message_log()

    def _build_flow_diagram(self):
        """Build the network flow diagram."""
        frame = Gtk.Frame()
        frame.set_label("Network Flow")

        flow_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=20)
        flow_box.set_margin_start(20)
        flow_box.set_margin_end(20)
        flow_box.set_margin_top(15)
        flow_box.set_margin_bottom(15)
        flow_box.set_halign(Gtk.Align.CENTER)

        # RNS Network box
        rns_box = self._create_network_box("RNS Network", "Reticulum", "rns")
        flow_box.append(rns_box)

        # Arrow RNS -> Bridge
        arrow1_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self.rns_to_bridge_label = Gtk.Label(label="→")
        self.rns_to_bridge_label.add_css_class("title-1")
        self.rns_to_bridge_count = Gtk.Label(label="0 msgs")
        self.rns_to_bridge_count.add_css_class("dim-label")
        arrow1_box.append(self.rns_to_bridge_label)
        arrow1_box.append(self.rns_to_bridge_count)
        flow_box.append(arrow1_box)

        # Bridge box
        bridge_box = self._create_network_box("MeshForge Bridge", "Gateway", "bridge")
        flow_box.append(bridge_box)

        # Arrow Bridge -> Meshtastic
        arrow2_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self.bridge_to_mesh_label = Gtk.Label(label="→")
        self.bridge_to_mesh_label.add_css_class("title-1")
        self.bridge_to_mesh_count = Gtk.Label(label="0 msgs")
        self.bridge_to_mesh_count.add_css_class("dim-label")
        arrow2_box.append(self.bridge_to_mesh_label)
        arrow2_box.append(self.bridge_to_mesh_count)
        flow_box.append(arrow2_box)

        # Meshtastic Network box
        mesh_box = self._create_network_box("Meshtastic", "LoRa Mesh", "mesh")
        flow_box.append(mesh_box)

        frame.set_child(flow_box)
        self.append(frame)

    def _create_network_box(self, name: str, subtext: str, network_type: str) -> Gtk.Box:
        """Create a styled network box for the flow diagram."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        box.set_size_request(120, 80)

        # Styled frame
        inner_frame = Gtk.Frame()
        inner_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        inner_box.set_margin_start(10)
        inner_box.set_margin_end(10)
        inner_box.set_margin_top(8)
        inner_box.set_margin_bottom(8)

        name_label = Gtk.Label(label=name)
        name_label.add_css_class("heading")
        inner_box.append(name_label)

        sub_label = Gtk.Label(label=subtext)
        sub_label.add_css_class("dim-label")
        inner_box.append(sub_label)

        # Status indicator
        status_label = Gtk.Label(label="● Checking...")
        status_label.add_css_class("dim-label")
        inner_box.append(status_label)

        inner_frame.set_child(inner_box)
        box.append(inner_frame)

        # Store reference for updates
        setattr(self, f'{network_type}_status', status_label)

        return box

    def _build_stats_section(self):
        """Build the statistics section."""
        frame = Gtk.Frame()
        frame.set_label("Routing Statistics")

        stats_grid = Gtk.Grid()
        stats_grid.set_column_spacing(30)
        stats_grid.set_row_spacing(8)
        stats_grid.set_margin_start(15)
        stats_grid.set_margin_end(15)
        stats_grid.set_margin_top(10)
        stats_grid.set_margin_bottom(10)

        # Row 0: RNS to Mesh | Mesh to RNS
        stats_grid.attach(Gtk.Label(label="RNS → Mesh:"), 0, 0, 1, 1)
        self.stat_rns_to_mesh = Gtk.Label(label="0")
        self.stat_rns_to_mesh.set_xalign(0)
        stats_grid.attach(self.stat_rns_to_mesh, 1, 0, 1, 1)

        stats_grid.attach(Gtk.Label(label="Mesh → RNS:"), 2, 0, 1, 1)
        self.stat_mesh_to_rns = Gtk.Label(label="0")
        self.stat_mesh_to_rns.set_xalign(0)
        stats_grid.attach(self.stat_mesh_to_rns, 3, 0, 1, 1)

        # Row 1: Bounced | Errors
        stats_grid.attach(Gtk.Label(label="Bounced:"), 0, 1, 1, 1)
        self.stat_bounced = Gtk.Label(label="0")
        self.stat_bounced.set_xalign(0)
        stats_grid.attach(self.stat_bounced, 1, 1, 1, 1)

        stats_grid.attach(Gtk.Label(label="Errors:"), 2, 1, 1, 1)
        self.stat_errors = Gtk.Label(label="0")
        self.stat_errors.set_xalign(0)
        stats_grid.attach(self.stat_errors, 3, 1, 1, 1)

        # Row 2: Success Rate | Uptime
        stats_grid.attach(Gtk.Label(label="Success Rate:"), 0, 2, 1, 1)
        self.stat_success_rate = Gtk.Label(label="--")
        self.stat_success_rate.set_xalign(0)
        stats_grid.attach(self.stat_success_rate, 1, 2, 1, 1)

        stats_grid.attach(Gtk.Label(label="Bridge Uptime:"), 2, 2, 1, 1)
        self.stat_uptime = Gtk.Label(label="--")
        self.stat_uptime.set_xalign(0)
        stats_grid.attach(self.stat_uptime, 3, 2, 1, 1)

        frame.set_child(stats_grid)
        self.append(frame)

    def _build_message_log(self):
        """Build the message log section."""
        frame = Gtk.Frame()
        frame.set_label("Message Log (Recent)")

        log_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        log_box.set_margin_start(10)
        log_box.set_margin_end(10)
        log_box.set_margin_top(8)
        log_box.set_margin_bottom(8)

        # Log viewer
        scroll = Gtk.ScrolledWindow()
        scroll.set_min_content_height(200)
        scroll.set_max_content_height(300)
        scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)

        self.log_view = Gtk.TextView()
        self.log_view.set_editable(False)
        self.log_view.set_monospace(True)
        self.log_view.set_wrap_mode(Gtk.WrapMode.WORD)
        self.log_buffer = self.log_view.get_buffer()
        self.log_buffer.set_text("Waiting for messages...\n\nMessages will appear here as they are routed between networks.")

        scroll.set_child(self.log_view)
        log_box.append(scroll)

        # Control buttons
        btn_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        btn_row.set_halign(Gtk.Align.END)

        clear_btn = Gtk.Button(label="Clear Log")
        clear_btn.connect("clicked", lambda b: self.log_buffer.set_text(""))
        btn_row.append(clear_btn)

        copy_btn = Gtk.Button(label="Copy All")
        copy_btn.connect("clicked", self._copy_log)
        btn_row.append(copy_btn)

        refresh_btn = Gtk.Button(label="Refresh Now")
        refresh_btn.add_css_class("suggested-action")
        refresh_btn.connect("clicked", lambda b: self._update_stats())
        btn_row.append(refresh_btn)

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

    def _start_updates(self):
        """Start periodic stats updates."""
        self._update_timer = GLib.timeout_add_seconds(5, self._update_stats)
        # Initial update
        GLib.idle_add(self._update_stats)

    def _update_stats(self):
        """Update statistics from bridge."""
        def do_update():
            try:
                # Try to get bridge stats
                stats = self._get_bridge_stats()

                if stats:
                    GLib.idle_add(self._apply_stats, stats)
                else:
                    GLib.idle_add(self._show_no_bridge)

            except Exception as e:
                logger.error(f"Stats update error: {e}")

        threading.Thread(target=do_update, daemon=True).start()
        return True  # Continue timer

    def _get_bridge_stats(self) -> Optional[Dict]:
        """Get statistics from the gateway bridge."""
        # Try to import and get active bridge instance
        try:
            # Check for running bridge via API or direct import
            from utils.service_check import check_port
            if check_port(4403):
                # meshtasticd running, bridge likely active
                # For now, return mock stats - in production, query actual bridge
                return {
                    'rns_to_mesh': self._stats.get('rns_to_mesh', 0),
                    'mesh_to_rns': self._stats.get('mesh_to_rns', 0),
                    'bounced': self._stats.get('bounced', 0),
                    'errors': self._stats.get('errors', 0),
                    'uptime': self._stats.get('uptime', 0),
                    'rns_connected': True,
                    'mesh_connected': True,
                    'bridge_active': True,
                }
        except ImportError:
            pass

        # Return simulated offline state
        return {
            'rns_to_mesh': 0,
            'mesh_to_rns': 0,
            'bounced': 0,
            'errors': 0,
            'uptime': 0,
            'rns_connected': False,
            'mesh_connected': False,
            'bridge_active': False,
        }

    def _apply_stats(self, stats: Dict):
        """Apply stats to UI."""
        # Update flow arrows
        rns_to_mesh = stats.get('rns_to_mesh', 0)
        mesh_to_rns = stats.get('mesh_to_rns', 0)

        self.rns_to_bridge_count.set_label(f"{mesh_to_rns} msgs")
        self.bridge_to_mesh_count.set_label(f"{rns_to_mesh} msgs")

        # Update statistics
        self.stat_rns_to_mesh.set_label(str(rns_to_mesh))
        self.stat_mesh_to_rns.set_label(str(mesh_to_rns))
        self.stat_bounced.set_label(str(stats.get('bounced', 0)))
        self.stat_errors.set_label(str(stats.get('errors', 0)))

        # Calculate success rate
        total = rns_to_mesh + mesh_to_rns
        errors = stats.get('errors', 0) + stats.get('bounced', 0)
        if total > 0:
            success_rate = ((total - errors) / total) * 100
            self.stat_success_rate.set_label(f"{success_rate:.1f}%")
        else:
            self.stat_success_rate.set_label("--")

        # Format uptime
        uptime_sec = stats.get('uptime', 0)
        if uptime_sec > 0:
            hours = uptime_sec // 3600
            minutes = (uptime_sec % 3600) // 60
            self.stat_uptime.set_label(f"{hours}h {minutes}m")
        else:
            self.stat_uptime.set_label("--")

        # Update status indicators
        if stats.get('rns_connected'):
            self.rns_status.set_label("● Connected")
            self.rns_status.remove_css_class("warning")
            self.rns_status.add_css_class("success")
        else:
            self.rns_status.set_label("● Disconnected")
            self.rns_status.remove_css_class("success")
            self.rns_status.add_css_class("warning")

        if stats.get('bridge_active'):
            self.bridge_status.set_label("● Active")
            self.bridge_status.remove_css_class("warning")
            self.bridge_status.add_css_class("success")
        else:
            self.bridge_status.set_label("● Inactive")
            self.bridge_status.remove_css_class("success")
            self.bridge_status.add_css_class("warning")

        if stats.get('mesh_connected'):
            self.mesh_status.set_label("● Connected")
            self.mesh_status.remove_css_class("warning")
            self.mesh_status.add_css_class("success")
        else:
            self.mesh_status.set_label("● Disconnected")
            self.mesh_status.remove_css_class("success")
            self.mesh_status.add_css_class("warning")

    def _show_no_bridge(self):
        """Show message when bridge is not active."""
        self.rns_status.set_label("● Offline")
        self.bridge_status.set_label("● Not Running")
        self.mesh_status.set_label("● Offline")

        self.rns_status.add_css_class("warning")
        self.bridge_status.add_css_class("warning")
        self.mesh_status.add_css_class("warning")

    def _log_message(self, msg: str):
        """Add a message to the log."""
        timestamp = datetime.now().strftime("%H:%M:%S")
        log_line = f"[{timestamp}] {msg}\n"

        end_iter = self.log_buffer.get_end_iter()
        self.log_buffer.insert(end_iter, log_line)

        # Auto-scroll
        end_mark = self.log_buffer.create_mark(None, self.log_buffer.get_end_iter(), False)
        self.log_view.scroll_mark_onscreen(end_mark)

    def cleanup(self):
        """Cleanup resources when panel is destroyed."""
        if self._update_timer:
            GLib.source_remove(self._update_timer)
            self._update_timer = None


# Make panel available for registration
__all__ = ['MessageRoutingPanel']
