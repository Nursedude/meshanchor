"""
RNS (Reticulum Network Stack) Management Panel
Integrates Reticulum mesh networking with MeshForge
"""

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, GLib
import subprocess
import threading
import shutil
import os
from pathlib import Path
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

# Import centralized path utility
from utils.paths import get_real_user_home

# Import service availability checker
try:
    from utils.service_check import check_service, ServiceState
except ImportError:
    check_service = None
    ServiceState = None


# Import mixins for refactored functionality
from .rns_mixins import (
    ComponentsMixin, ConfigMixin, GatewayMixin,
    MeshChatMixin, NomadNetMixin, RNodeMixin
)

class RNSPanel(ComponentsMixin, ConfigMixin, GatewayMixin,
               MeshChatMixin, NomadNetMixin, RNodeMixin, Gtk.Box):
    """RNS management panel for Reticulum Network Stack integration"""

    # RNS ecosystem components
    COMPONENTS = [
        {
            'name': 'rns',
            'display': 'Reticulum Network Stack',
            'package': 'rns',
            'description': 'Core cryptographic networking protocol',
            'service': 'rnsd',
        },
        {
            'name': 'lxmf',
            'display': 'LXMF',
            'package': 'lxmf',
            'description': 'Lightweight Extensible Message Format',
            'service': None,
        },
        {
            'name': 'nomadnet',
            'display': 'NomadNet',
            'package': 'nomadnet',
            'description': 'Terminal-based messaging client',
            'service': None,
        },
        {
            'name': 'rnodeconf',
            'display': 'RNode Configurator',
            'package': 'rnodeconf',
            'description': 'RNODE device configuration tool',
            'service': None,
        },
        {
            'name': 'meshchat',
            'display': 'MeshChat',
            'package': 'meshchat',
            'description': 'Web-based messaging interface for LXMF',
            'service': None,
        },
    ]
    def __init__(self, main_window):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=15)
        self.main_window = main_window

        self.set_margin_start(20)
        self.set_margin_end(20)
        self.set_margin_top(20)
        self.set_margin_bottom(20)

        self._component_status = {}

        # Track timer IDs for cleanup (prevents crashes on panel destruction)
        self._pending_timers = []

        # Connect cleanup handler
        self.connect("unrealize", self._on_unrealize)

        # Track last known service state
        self._last_rnsd_running = None

        self._build_ui()
        self._refresh_all()

        # NOTE: Periodic monitoring disabled - was causing instability
        # self._start_status_monitor()

    def _on_unrealize(self, widget):
        """Clean up when panel is destroyed to prevent timer crashes."""
        # Cancel all pending timers
        for timer_id in self._pending_timers:
            try:
                GLib.source_remove(timer_id)
            except Exception:
                pass
        self._pending_timers.clear()

    def _schedule_timer(self, delay_ms: int, callback, *args):
        """Schedule a timer and track it for cleanup."""
        if args:
            timer_id = GLib.timeout_add(delay_ms, callback, *args)
        else:
            timer_id = GLib.timeout_add(delay_ms, callback)
        self._pending_timers.append(timer_id)
        return timer_id

    def _start_status_monitor(self):
        """Start periodic monitoring of rnsd service status."""
        # Check every 5 seconds
        self._schedule_timer(5000, self._check_service_status_periodic)

    def _check_service_status_periodic(self):
        """Lightweight periodic check of rnsd service status.

        Only updates UI if status changed to avoid unnecessary redraws.
        """
        def do_check():
            try:
                is_running = self._check_rns_service()
                # Only update UI if status changed
                if is_running != self._last_rnsd_running:
                    self._last_rnsd_running = is_running
                    GLib.idle_add(self._update_service_status_only, is_running)
            except Exception:
                pass

        thread = threading.Thread(target=do_check, daemon=True)
        thread.start()

        # Return True to keep the timer running
        return True

    def _update_service_status_only(self, is_running):
        """Update just the service status UI (lightweight update)."""
        try:
            if is_running:
                self.rns_status_icon.set_from_icon_name("emblem-default")
                self.rns_status_label.set_label("Running")
                service_status = self._get_systemd_service_status()
                if service_status == 'active':
                    self.rns_status_detail.set_label("rnsd running (systemd service)")
                else:
                    self.rns_status_detail.set_label("rnsd running (process)")
                self.rns_install_note.set_label("")
                self.rns_start_btn.set_sensitive(False)
                self.rns_stop_btn.set_sensitive(True)
                self.rns_restart_btn.set_sensitive(True)
            else:
                self.rns_status_icon.set_from_icon_name("dialog-warning")
                self.rns_status_label.set_label("Stopped")
                service_status = self._get_systemd_service_status()
                if service_status == 'inactive':
                    self.rns_status_detail.set_label("rnsd stopped (systemd service installed)")
                elif service_status == 'not-found':
                    self.rns_status_detail.set_label("rnsd not running (no systemd service)")
                else:
                    self.rns_status_detail.set_label("Reticulum daemon is not running")
                self.rns_install_note.set_label("Start with: rnsd")
                self.rns_start_btn.set_sensitive(True)
                self.rns_stop_btn.set_sensitive(False)
                self.rns_restart_btn.set_sensitive(True)
            logger.debug(f"RNS service status changed: running={is_running}")
        except Exception as e:
            logger.warning(f"Error updating service status UI: {e}")
        return False

    def _build_ui(self):
        """Build the RNS panel UI"""
        # Title
        title = Gtk.Label(label="Reticulum Network Stack")
        title.add_css_class("title-1")
        title.set_xalign(0)
        self.append(title)

        subtitle = Gtk.Label(label="Manage RNS ecosystem and gateway integration")
        subtitle.set_xalign(0)
        subtitle.add_css_class("dim-label")
        self.append(subtitle)

        # Scrollable content
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_vexpand(True)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=15)
        content.set_margin_top(15)

        # RNS Service Status
        self._build_service_section(content)

        # Components Section
        self._build_components_section(content)

        # Gateway Section
        self._build_gateway_section(content)

        # Discovered RNS Nodes Section
        self._build_discovered_nodes_section(content)

        # RNode Interface Configuration
        self._build_rnode_config_section(content)

        # Configuration Section
        self._build_config_section(content)

        # NomadNet Tools Section
        self._build_nomadnet_section(content)

        # MeshChat Web Interface Section
        self._build_meshchat_section(content)

        scrolled.set_child(content)
        self.append(scrolled)

    def _build_service_section(self, parent):
        """Build RNS service status section"""
        frame = Gtk.Frame()
        frame.set_label("RNS Service (rnsd)")

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.set_margin_start(15)
        box.set_margin_end(15)
        box.set_margin_top(10)
        box.set_margin_bottom(10)

        # Status row
        status_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=15)

        self.rns_status_icon = Gtk.Image.new_from_icon_name("emblem-question")
        self.rns_status_icon.set_pixel_size(32)
        status_row.append(self.rns_status_icon)

        status_info = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        self.rns_status_label = Gtk.Label(label="Checking...")
        self.rns_status_label.set_xalign(0)
        self.rns_status_label.add_css_class("heading")
        status_info.append(self.rns_status_label)

        self.rns_status_detail = Gtk.Label(label="")
        self.rns_status_detail.set_xalign(0)
        self.rns_status_detail.add_css_class("dim-label")
        status_info.append(self.rns_status_detail)

        status_row.append(status_info)

        # Spacer
        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        status_row.append(spacer)

        # Control buttons
        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)

        self.rns_start_btn = Gtk.Button(label="Start")
        self.rns_start_btn.add_css_class("suggested-action")
        self.rns_start_btn.connect("clicked", lambda b: self._service_action("start"))
        btn_box.append(self.rns_start_btn)

        self.rns_stop_btn = Gtk.Button(label="Stop")
        self.rns_stop_btn.add_css_class("destructive-action")
        self.rns_stop_btn.connect("clicked", lambda b: self._service_action("stop"))
        btn_box.append(self.rns_stop_btn)

        self.rns_restart_btn = Gtk.Button(label="Restart")
        self.rns_restart_btn.connect("clicked", lambda b: self._service_action("restart"))
        btn_box.append(self.rns_restart_btn)

        # Install systemd service button
        self.rns_install_service_btn = Gtk.Button(label="Install Service")
        self.rns_install_service_btn.set_tooltip_text("Create systemd service for rnsd (persistent across reboots)")
        self.rns_install_service_btn.connect("clicked", self._install_rnsd_service)
        btn_box.append(self.rns_install_service_btn)

        status_row.append(btn_box)
        box.append(status_row)

        # Installation note
        self.rns_install_note = Gtk.Label(label="")
        self.rns_install_note.set_xalign(0)
        self.rns_install_note.add_css_class("dim-label")
        self.rns_install_note.set_wrap(True)
        box.append(self.rns_install_note)

        frame.set_child(box)
        parent.append(frame)

    def _build_discovered_nodes_section(self, parent):
        """Build discovered RNS nodes section"""
        frame = Gtk.Frame()
        frame.set_label("Discovered RNS Nodes")

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.set_margin_start(15)
        box.set_margin_end(15)
        box.set_margin_top(10)
        box.set_margin_bottom(10)

        # Description
        desc = Gtk.Label(label="RNS nodes discovered via announces (does not require GPS)")
        desc.set_xalign(0)
        desc.add_css_class("dim-label")
        box.append(desc)

        # Nodes count label
        count_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.rns_nodes_count = Gtk.Label(label="Nodes: 0")
        self.rns_nodes_count.set_xalign(0)
        count_row.append(self.rns_nodes_count)

        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        count_row.append(spacer)

        refresh_btn = Gtk.Button(label="Refresh")
        refresh_btn.connect("clicked", self._refresh_rns_nodes)
        count_row.append(refresh_btn)
        box.append(count_row)

        # Scrollable node list
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_min_content_height(150)
        scroll.set_max_content_height(300)

        # ListBox for nodes
        self.rns_nodes_list = Gtk.ListBox()
        self.rns_nodes_list.set_selection_mode(Gtk.SelectionMode.NONE)
        self.rns_nodes_list.add_css_class("boxed-list")

        # Placeholder row
        placeholder = Gtk.Label(label="No RNS nodes discovered yet")
        placeholder.add_css_class("dim-label")
        placeholder.set_margin_top(20)
        placeholder.set_margin_bottom(20)
        self.rns_nodes_list.append(placeholder)

        scroll.set_child(self.rns_nodes_list)
        box.append(scroll)

        frame.set_child(box)
        parent.append(frame)

        # Auto-refresh nodes on panel load
        GLib.timeout_add(1000, self._refresh_rns_nodes)

    def _refresh_rns_nodes(self, button=None):
        """Refresh the RNS nodes list from node tracker"""
        def do_refresh():
            nodes = []
            try:
                # Try to get node tracker from main window
                if hasattr(self.main_window, 'node_tracker') and self.main_window.node_tracker:
                    rns_nodes = self.main_window.node_tracker.get_rns_nodes()
                    for node in rns_nodes:
                        nodes.append({
                            'id': node.id,
                            'name': node.name or node.short_name or 'Unknown',
                            'hash': node.rns_hash.hex()[:16] if node.rns_hash else '?',
                            'last_seen': node.last_seen.strftime('%H:%M:%S') if node.last_seen else 'Unknown',
                            'online': node.is_online
                        })
            except Exception as e:
                logger.error(f"Error getting RNS nodes: {e}")

            GLib.idle_add(self._update_rns_nodes_ui, nodes)

        threading.Thread(target=do_refresh, daemon=True).start()
        return False  # Don't repeat for GLib.timeout_add

    def _update_rns_nodes_ui(self, nodes):
        """Update the RNS nodes list UI"""
        # Clear existing rows
        while True:
            row = self.rns_nodes_list.get_row_at_index(0)
            if row:
                self.rns_nodes_list.remove(row)
            else:
                break

        # Update count
        self.rns_nodes_count.set_label(f"Nodes: {len(nodes)}")

        if not nodes:
            # Add placeholder
            placeholder = Gtk.Label(label="No RNS nodes discovered yet")
            placeholder.add_css_class("dim-label")
            placeholder.set_margin_top(20)
            placeholder.set_margin_bottom(20)
            self.rns_nodes_list.append(placeholder)
            return

        # Add node rows
        for node in nodes:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            row.set_margin_start(10)
            row.set_margin_end(10)
            row.set_margin_top(5)
            row.set_margin_bottom(5)

            # Online indicator
            status_icon = Gtk.Image.new_from_icon_name(
                "emblem-ok-symbolic" if node['online'] else "emblem-important-symbolic"
            )
            status_icon.set_pixel_size(16)
            row.append(status_icon)

            # Node info
            info_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            info_box.set_hexpand(True)

            name_label = Gtk.Label(label=node['name'])
            name_label.set_xalign(0)
            name_label.add_css_class("heading")
            info_box.append(name_label)

            hash_label = Gtk.Label(label=f"<{node['hash']}>")
            hash_label.set_xalign(0)
            hash_label.add_css_class("dim-label")
            hash_label.add_css_class("monospace")
            info_box.append(hash_label)

            row.append(info_box)

            # Last seen
            time_label = Gtk.Label(label=node['last_seen'])
            time_label.add_css_class("dim-label")
            row.append(time_label)

            self.rns_nodes_list.append(row)

    def _show_info(self, message: str):
        """Show info toast"""
        if hasattr(self.main_window, 'toast_overlay'):
            toast = Adw.Toast.new(message)
            toast.set_timeout(3)
            self.main_window.toast_overlay.add_toast(toast)

    def _show_error(self, message: str):
        """Show error toast"""
        if hasattr(self.main_window, 'toast_overlay'):
            toast = Adw.Toast.new(f"⚠️ {message}")
            toast.set_timeout(5)
            self.main_window.toast_overlay.add_toast(toast)

    def _get_real_user_home(self):
        """Get the real user's home directory, even when running as root via sudo"""
        return get_real_user_home()

    def _get_real_username(self):
        """Get the real username, even when running as root via sudo"""
        import os
        return os.environ.get('SUDO_USER', os.environ.get('USER', 'root'))

    def _is_meshtasticd_running(self):
        """Check if meshtasticd service is running (blocks serial port)"""
        try:
            result = subprocess.run(
                ['pgrep', '-f', 'meshtasticd'],
                capture_output=True, text=True, timeout=5
            )
            return result.returncode == 0 and result.stdout.strip()
        except Exception:
            return False

    def _install_meshtastic_interface(self, button):
        """Download and install Meshtastic_Interface.py"""
        logger.debug("[RNS] Installing Meshtastic Interface...")
        self.main_window.set_status_message("Downloading Meshtastic Interface...")

        def do_install():
            try:
                import urllib.request

                # Create interfaces directory
                interfaces_dir = get_real_user_home() / ".reticulum" / "interfaces"
                interfaces_dir.mkdir(parents=True, exist_ok=True)

                # Download the interface file
                url = "https://raw.githubusercontent.com/Nursedude/RNS_Over_Meshtastic_Gateway/main/Meshtastic_Interface.py"
                dest = interfaces_dir / "Meshtastic_Interface.py"

                logger.debug(f"[RNS] Downloading from {url}")
                urllib.request.urlretrieve(url, str(dest))

                logger.debug(f"[RNS] Saved to {dest}")
                GLib.idle_add(self._install_meshtastic_interface_complete, True, str(dest))

            except Exception as e:
                logger.debug(f"[RNS] Failed to install: {e}")
                GLib.idle_add(self._install_meshtastic_interface_complete, False, str(e))

        threading.Thread(target=do_install, daemon=True).start()

    def _install_meshtastic_interface_complete(self, success, message):
        """Handle install completion"""
        if success:
            self.main_window.set_status_message("Meshtastic Interface installed")
            self.mesh_iface_status.set_label(f"✓ Installed: {message}")
            logger.debug("[RNS] Meshtastic Interface installed successfully")
        else:
            self.main_window.set_status_message(f"Install failed: {message}")
            self.mesh_iface_status.set_label(f"✗ Failed: {message}")
        return False

    def _edit_meshtastic_interface(self, button):
        """Edit Meshtastic_Interface.py in terminal"""
        import os

        real_home = self._get_real_user_home()
        iface_file = real_home / ".reticulum" / "interfaces" / "Meshtastic_Interface.py"

        if not iface_file.exists():
            self.main_window.set_status_message("Interface not installed - click 'Install Interface' first")
            logger.debug(f"[RNS] Interface file not found: {iface_file}")
            return

        logger.debug(f"[RNS] Opening interface file in terminal: {iface_file}")
        self._edit_config_terminal(iface_file)

    def _add_meshtastic_interface_config(self, button):
        """Add Meshtastic Interface config template to RNS config"""
        logger.debug("[RNS] Adding Meshtastic Interface config...")

        config_file = get_real_user_home() / ".reticulum" / "config"

        # Config template - based on Nursedude/RNS_Over_Meshtastic_Gateway
        config_template = '''
# ===== MESHTASTIC INTERFACE =====
# RNS over Meshtastic LoRa - bridges Reticulum with Meshtastic networks
# Source: https://github.com/Nursedude/RNS_Over_Meshtastic_Gateway
#
# Configure ONE connection method: port (serial), ble_port, or tcp_port

[[Meshtastic Interface]]
  type = Meshtastic_Interface
  enabled = true
  mode = gateway

  # === CONNECTION OPTIONS (choose ONE) ===

  # Option 1: USB Serial (most common)
  port = /dev/ttyUSB0
  # port = /dev/ttyACM0

  # Option 2: Bluetooth LE (pair device first)
  # ble_port = RNode_1234

  # Option 3: TCP/IP (via meshtasticd daemon)
  # tcp_port = 127.0.0.1:4403

  # === RADIO SPEED PRESETS ===
  # Speed determines range vs throughput tradeoff
  #   0 = LONG_FAST     (1066 bps, ~30km,  -123dBm)
  #   1 = LONG_SLOW     (293 bps,  ~80km,  -129dBm)
  #   2 = VERY_LONG_SLW (146 bps,  ~120km, -132dBm)
  #   3 = MEDIUM_SLOW   (702 bps,  ~20km,  -120dBm)
  #   4 = MEDIUM_FAST   (3516 bps, ~12km,  -117dBm)
  #   5 = SHORT_SLOW    (4375 bps, ~8km,   -114dBm)
  #   6 = SHORT_FAST    (10937 bps,~5km,   -111dBm)
  #   7 = LONG_MODERATE (878 bps,  ~40km,  -126dBm)
  #   8 = SHORT_TURBO   (21875 bps,~3km,   -108dBm) [Default]
  data_speed = 8

  # Hop limit for multi-hop routing (1-7)
  hop_limit = 3

  # Effective bitrate in bytes/sec (match your speed preset)
  # TURBO=500, FAST=200, SLOW=50
  bitrate = 500
'''

        try:
            # Check if config file exists
            if not config_file.exists():
                self.main_window.set_status_message("RNS config not found - run rnsd first")
                logger.debug("[RNS] Config file not found")
                return

            # Read existing config
            with open(config_file, 'r') as f:
                existing_config = f.read()

            # Check if already configured
            if 'Meshtastic Interface' in existing_config:
                self.main_window.set_status_message("Meshtastic Interface already in config")
                logger.debug("[RNS] Config already contains Meshtastic Interface")
                # Open editor anyway
                self._edit_config_terminal(config_file)
                return

            # Append the template
            with open(config_file, 'a') as f:
                f.write(config_template)

            self.main_window.set_status_message("Meshtastic Interface config added - edit to configure")
            self.mesh_iface_status.set_label("✓ Config template added - edit port settings")
            logger.debug("[RNS] Config template added")

            # Open in terminal editor to configure
            self._edit_config_terminal(config_file)

        except Exception as e:
            self.main_window.set_status_message(f"Failed: {e}")
            logger.debug(f"[RNS] Failed to add config: {e}")

    def _on_setup_gateway(self, button):
        """Open gateway setup wizard"""
        self._on_configure_gateway(button)

    def _open_config_folder(self, path):
        """Open config folder in file manager"""
        try:
            subprocess.run(['xdg-open', str(path)], timeout=10)
        except Exception as e:
            self.main_window.set_status_message(f"Failed to open folder: {e}")

    def _open_rns_config_dialog(self):
        """Open the RNS configuration editor dialog"""
        logger.debug("[RNS] Opening RNS config dialog...")
        try:
            from ..dialogs.rns_config import RNSConfigDialog
            dialog = RNSConfigDialog(self.main_window)
            dialog.present()
            logger.debug("[RNS] Config dialog opened")
        except ImportError as e:
            logger.debug(f"[RNS] Config dialog import failed: {e}")
            # Fallback if dialog not available
            dialog = Adw.MessageDialog(
                transient_for=self.main_window,
                heading="Configuration Editor",
                body=f"Config editor not available: {e}\n\n"
                     "Config file: ~/.reticulum/config"
            )
            dialog.add_response("ok", "OK")
            dialog.present()

    def _edit_config(self, config_file):
        """Open config file in editor"""
        logger.debug(f"[RNS] Opening config: {config_file}")
        try:
            # Try GUI editors only (no terminal editors like nano/vim)
            gui_editors = ['gedit', 'kate', 'xed', 'mousepad', 'pluma', 'featherpad']
            for editor in gui_editors:
                if shutil.which(editor):
                    logger.debug(f"[RNS] Using editor: {editor}")
                    subprocess.Popen([editor, str(config_file)])
                    return
            # Fallback to xdg-open
            logger.debug("[RNS] Using xdg-open")
            subprocess.run(['xdg-open', str(config_file)], timeout=10)
        except Exception as e:
            logger.debug(f"[RNS] Failed to open editor: {e}")
            self.main_window.set_status_message(f"Failed to open editor: {e}")