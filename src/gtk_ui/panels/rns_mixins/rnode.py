"""
RNode Interface Configuration Section for RNS Panel

Configure RNode LoRa interface parameters for RNS.
Includes Meshtastic preset detection for gateway bridging.
"""

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, GLib
import threading
import logging

logger = logging.getLogger(__name__)

# Import path utility
import os
from pathlib import Path
from utils.paths import get_real_user_home

# Import LoRa presets for Meshtastic compatibility
try:
    from utils.lora_presets import (
        MESHTASTIC_PRESETS, PROVEN_GATEWAY_CONFIGS,
        detect_meshtastic_settings, bandwidth_hz_to_index,
        coding_rate_to_index, get_rnode_config_for_meshtastic_preset
    )
    HAS_LORA_PRESETS = True
except ImportError:
    HAS_LORA_PRESETS = False
    MESHTASTIC_PRESETS = {}
    PROVEN_GATEWAY_CONFIGS = {}

# Import RNode device detection
try:
    from commands.rnode import detect_devices, RNodeDevice
    HAS_RNODE_DETECTION = True
except ImportError:
    HAS_RNODE_DETECTION = False
    detect_devices = None
    RNodeDevice = None

# Import service check for meshtasticd
try:
    from utils.service_check import check_port
    HAS_SERVICE_CHECK = True
except ImportError:
    HAS_SERVICE_CHECK = False
    check_port = None


class RNodeMixin:
    """
    Mixin class providing RNode configuration for RNSPanel.

    Expects the panel to have:
    - main_window: Reference to main application window
    - _get_real_user_home(): Method to get real user's home directory
    - _edit_config_terminal(path): Method to edit config in terminal
    """

    def _build_rnode_config_section(self, parent):
        """Build RNode LoRa interface configuration section"""
        frame = Gtk.Frame()
        frame.set_label("RNode Interface Configuration")

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.set_margin_start(15)
        box.set_margin_end(15)
        box.set_margin_top(10)
        box.set_margin_bottom(10)

        # Description
        desc = Gtk.Label(label="Configure RNode LoRa interface for RNS")
        desc.set_xalign(0)
        desc.add_css_class("dim-label")
        box.append(desc)

        # =====================================================================
        # Radio Detection Section
        # =====================================================================
        detect_frame = Gtk.Frame()
        detect_frame.set_label("Radio Detection")

        detect_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        detect_box.set_margin_start(10)
        detect_box.set_margin_end(10)
        detect_box.set_margin_top(8)
        detect_box.set_margin_bottom(8)

        # Service status row
        status_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)

        # meshtasticd status
        self.meshtasticd_status = Gtk.Label(label="● meshtasticd: checking...")
        self.meshtasticd_status.set_xalign(0)
        status_row.append(self.meshtasticd_status)

        # rnsd status
        self.rnsd_status = Gtk.Label(label="● rnsd: checking...")
        self.rnsd_status.set_xalign(0)
        self.rnsd_status.set_margin_start(20)
        status_row.append(self.rnsd_status)

        detect_box.append(status_row)

        # Device selection row
        device_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)

        device_label = Gtk.Label(label="Device:")
        device_label.set_width_chars(10)
        device_label.set_xalign(0)
        device_row.append(device_label)

        # Device dropdown (starts empty, populated on detect)
        self.device_dropdown = Gtk.DropDown.new_from_strings(["No devices detected"])
        self.device_dropdown.set_hexpand(True)
        self.device_dropdown.connect("notify::selected", self._on_device_selected)
        device_row.append(self.device_dropdown)

        # Detect button
        detect_btn = Gtk.Button(label="Detect")
        detect_btn.add_css_class("suggested-action")
        detect_btn.set_tooltip_text("Scan for RNode and Meshtastic devices")
        detect_btn.connect("clicked", self._on_detect_devices)
        device_row.append(detect_btn)

        detect_box.append(device_row)

        # Device info display (short summary - details go to log)
        self.device_info_label = Gtk.Label(label="Click 'Detect' to scan for radios")
        self.device_info_label.set_xalign(0)
        self.device_info_label.add_css_class("dim-label")
        self.device_info_label.set_wrap(True)
        self.device_info_label.set_max_width_chars(60)
        detect_box.append(self.device_info_label)

        detect_frame.set_child(detect_box)
        box.append(detect_frame)

        # Store detected devices for later reference
        self._detected_devices = []

        # Check service status on startup
        GLib.idle_add(self._check_radio_services)

        # =====================================================================
        # Meshtastic Preset Section (for gateway bridging)
        # =====================================================================
        if HAS_LORA_PRESETS:
            preset_frame = Gtk.Frame()
            preset_frame.set_label("Match Meshtastic Settings")

            preset_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            preset_box.set_margin_start(10)
            preset_box.set_margin_end(10)
            preset_box.set_margin_top(8)
            preset_box.set_margin_bottom(8)

            preset_info = Gtk.Label(
                label="Detect or select Meshtastic preset to configure RNode for gateway bridging"
            )
            preset_info.set_xalign(0)
            preset_info.add_css_class("dim-label")
            preset_info.set_wrap(True)
            preset_box.append(preset_info)

            # Preset selection row
            preset_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)

            preset_label = Gtk.Label(label="Preset:")
            preset_label.set_width_chars(10)
            preset_label.set_xalign(0)
            preset_row.append(preset_label)

            # Build preset dropdown
            preset_names = list(MESHTASTIC_PRESETS.keys())
            self.meshtastic_preset_dropdown = Gtk.DropDown.new_from_strings(preset_names)
            # Default to MEDIUM_FAST (MtnMesh standard)
            try:
                default_idx = preset_names.index('MEDIUM_FAST')
                self.meshtastic_preset_dropdown.set_selected(default_idx)
            except ValueError:
                pass
            self.meshtastic_preset_dropdown.set_hexpand(True)
            self.meshtastic_preset_dropdown.connect("notify::selected", self._on_preset_changed)
            preset_row.append(self.meshtastic_preset_dropdown)

            preset_box.append(preset_row)

            # Preset description label
            self.preset_description = Gtk.Label(label="MtnMesh Community Standard - Best balance")
            self.preset_description.set_xalign(0)
            self.preset_description.add_css_class("dim-label")
            preset_box.append(self.preset_description)

            # Detected settings display
            self.detected_settings_label = Gtk.Label(label="")
            self.detected_settings_label.set_xalign(0)
            self.detected_settings_label.set_wrap(True)
            preset_box.append(self.detected_settings_label)

            # Preset buttons
            preset_btn_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            preset_btn_row.set_margin_top(5)

            detect_btn = Gtk.Button(label="Detect Meshtastic")
            detect_btn.set_tooltip_text("Auto-detect settings from connected Meshtastic device")
            detect_btn.connect("clicked", self._on_detect_meshtastic)
            preset_btn_row.append(detect_btn)

            apply_preset_btn = Gtk.Button(label="Apply Preset")
            apply_preset_btn.add_css_class("suggested-action")
            apply_preset_btn.set_tooltip_text("Fill RNode settings from selected preset")
            apply_preset_btn.connect("clicked", self._on_apply_preset)
            preset_btn_row.append(apply_preset_btn)

            preset_box.append(preset_btn_row)

            # Proven configs section
            proven_label = Gtk.Label(label="Proven Gateway Configs:")
            proven_label.set_xalign(0)
            proven_label.set_margin_top(8)
            preset_box.append(proven_label)

            proven_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

            proven_configs = list(PROVEN_GATEWAY_CONFIGS.keys())
            proven_names = [PROVEN_GATEWAY_CONFIGS[k]['name'] for k in proven_configs]
            self.proven_dropdown = Gtk.DropDown.new_from_strings(proven_names)
            self.proven_dropdown.set_hexpand(True)
            proven_row.append(self.proven_dropdown)

            apply_proven_btn = Gtk.Button(label="Apply")
            apply_proven_btn.set_tooltip_text("Apply tested gateway configuration")
            apply_proven_btn.connect("clicked", self._on_apply_proven_config)
            proven_row.append(apply_proven_btn)

            preset_box.append(proven_row)

            preset_frame.set_child(preset_box)
            box.append(preset_frame)

            # Separator
            box.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        # =====================================================================
        # Manual Configuration Section
        # =====================================================================
        manual_label = Gtk.Label(label="Manual Configuration")
        manual_label.set_xalign(0)
        manual_label.add_css_class("heading")
        manual_label.set_margin_top(5)
        box.append(manual_label)

        # Port selection
        port_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        port_label = Gtk.Label(label="Port:")
        port_label.set_width_chars(14)
        port_label.set_xalign(0)
        port_row.append(port_label)
        self.rnode_port = Gtk.Entry()
        self.rnode_port.set_text("/dev/ttyACM0")
        self.rnode_port.set_hexpand(True)
        port_row.append(self.rnode_port)
        box.append(port_row)

        # Frequency
        freq_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        freq_label = Gtk.Label(label="Frequency (MHz):")
        freq_label.set_width_chars(14)
        freq_label.set_xalign(0)
        freq_row.append(freq_label)
        self.rnode_freq = Gtk.SpinButton.new_with_range(137.0, 1020.0, 0.025)
        self.rnode_freq.set_digits(3)
        self.rnode_freq.set_value(903.625)
        freq_row.append(self.rnode_freq)
        box.append(freq_row)

        # Bandwidth dropdown
        bw_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        bw_label = Gtk.Label(label="Bandwidth:")
        bw_label.set_width_chars(14)
        bw_label.set_xalign(0)
        bw_row.append(bw_label)
        self.rnode_bw = Gtk.DropDown.new_from_strings([
            "7.8 kHz", "10.4 kHz", "15.6 kHz", "20.8 kHz", "31.25 kHz",
            "41.7 kHz", "62.5 kHz", "125 kHz", "250 kHz", "500 kHz"
        ])
        self.rnode_bw.set_selected(8)  # 250 kHz default
        bw_row.append(self.rnode_bw)
        box.append(bw_row)

        # Spreading Factor
        sf_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        sf_label = Gtk.Label(label="Spread Factor:")
        sf_label.set_width_chars(14)
        sf_label.set_xalign(0)
        sf_row.append(sf_label)
        self.rnode_sf = Gtk.SpinButton.new_with_range(7, 12, 1)
        self.rnode_sf.set_value(7)
        sf_row.append(self.rnode_sf)
        box.append(sf_row)

        # Coding Rate
        cr_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        cr_label = Gtk.Label(label="Coding Rate:")
        cr_label.set_width_chars(14)
        cr_label.set_xalign(0)
        cr_row.append(cr_label)
        self.rnode_cr = Gtk.DropDown.new_from_strings(["4/5", "4/6", "4/7", "4/8"])
        self.rnode_cr.set_selected(0)  # 4/5 = codingrate 5
        cr_row.append(self.rnode_cr)
        box.append(cr_row)

        # TX Power
        tx_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        tx_label = Gtk.Label(label="TX Power (dBm):")
        tx_label.set_width_chars(14)
        tx_label.set_xalign(0)
        tx_row.append(tx_label)
        self.rnode_tx = Gtk.SpinButton.new_with_range(0, 22, 1)
        self.rnode_tx.set_value(22)
        tx_row.append(self.rnode_tx)
        box.append(tx_row)

        # Action buttons
        btn_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        btn_row.set_halign(Gtk.Align.CENTER)
        btn_row.set_margin_top(10)

        apply_btn = Gtk.Button(label="Apply to Config")
        apply_btn.add_css_class("suggested-action")
        apply_btn.connect("clicked", self._apply_rnode_config)
        btn_row.append(apply_btn)

        load_btn = Gtk.Button(label="Load Current")
        load_btn.connect("clicked", self._load_rnode_config)
        btn_row.append(load_btn)

        # Edit in terminal with nano (per configurable files rule)
        config_path = get_real_user_home() / ".reticulum" / "config"
        terminal_btn = Gtk.Button(label="Edit (Terminal)")
        terminal_btn.set_tooltip_text("Edit ~/.reticulum/config in nano")
        terminal_btn.connect("clicked", lambda b: self._edit_config_terminal(config_path))
        btn_row.append(terminal_btn)

        box.append(btn_row)

        # Status
        self.rnode_status = Gtk.Label(label="")
        self.rnode_status.set_xalign(0)
        self.rnode_status.add_css_class("dim-label")
        box.append(self.rnode_status)

        frame.set_child(box)
        parent.append(frame)

        # =====================================================================
        # Detection Log Section (copyable output like other panels)
        # =====================================================================
        log_frame = Gtk.Frame()
        log_frame.set_label("Detection Log")

        log_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        log_box.set_margin_start(10)
        log_box.set_margin_end(10)
        log_box.set_margin_top(8)
        log_box.set_margin_bottom(8)

        # Log viewer (Gtk.TextView for copyable output)
        log_scroll = Gtk.ScrolledWindow()
        log_scroll.set_min_content_height(120)
        log_scroll.set_max_content_height(200)
        log_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)

        self.detection_log_view = Gtk.TextView()
        self.detection_log_view.set_editable(False)
        self.detection_log_view.set_monospace(True)
        self.detection_log_view.set_wrap_mode(Gtk.WrapMode.WORD)
        self.detection_log_buffer = self.detection_log_view.get_buffer()
        self.detection_log_buffer.set_text("Click 'Detect' to scan for radios and services.\nLogs will appear here (copyable).")
        log_scroll.set_child(self.detection_log_view)
        log_box.append(log_scroll)

        # Log action buttons
        log_btn_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        log_btn_row.set_halign(Gtk.Align.END)

        clear_log_btn = Gtk.Button(label="Clear")
        clear_log_btn.set_tooltip_text("Clear detection log")
        clear_log_btn.connect("clicked", lambda b: self.detection_log_buffer.set_text(""))
        log_btn_row.append(clear_log_btn)

        copy_log_btn = Gtk.Button(label="Copy All")
        copy_log_btn.set_tooltip_text("Copy log to clipboard")
        copy_log_btn.connect("clicked", self._copy_detection_log)
        log_btn_row.append(copy_log_btn)

        log_box.append(log_btn_row)

        log_frame.set_child(log_box)
        parent.append(log_frame)

        # =====================================================================
        # Config Preview Section
        # =====================================================================
        config_frame = Gtk.Frame()
        config_frame.set_label("RNS Config Preview")

        config_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        config_box.set_margin_start(10)
        config_box.set_margin_end(10)
        config_box.set_margin_top(8)
        config_box.set_margin_bottom(8)

        # Config preview (read-only)
        config_scroll = Gtk.ScrolledWindow()
        config_scroll.set_min_content_height(100)
        config_scroll.set_max_content_height(150)
        config_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)

        self.config_preview_view = Gtk.TextView()
        self.config_preview_view.set_editable(False)
        self.config_preview_view.set_monospace(True)
        self.config_preview_view.set_wrap_mode(Gtk.WrapMode.NONE)
        self.config_preview_buffer = self.config_preview_view.get_buffer()
        self.config_preview_buffer.set_text("Config will be shown here after loading.")
        config_scroll.set_child(self.config_preview_view)
        config_box.append(config_scroll)

        # Config action buttons
        config_btn_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        config_btn_row.set_halign(Gtk.Align.END)

        # Add RNode Template button (shown when no RNode is configured)
        self.add_rnode_template_btn = Gtk.Button(label="Add RNode Template")
        self.add_rnode_template_btn.add_css_class("suggested-action")
        self.add_rnode_template_btn.set_tooltip_text("Insert RNode interface template into config")
        self.add_rnode_template_btn.connect("clicked", self._on_add_rnode_template)
        self.add_rnode_template_btn.set_visible(False)  # Hidden until no RNode found
        config_btn_row.append(self.add_rnode_template_btn)

        refresh_config_btn = Gtk.Button(label="Refresh")
        refresh_config_btn.set_tooltip_text("Reload config from file")
        refresh_config_btn.connect("clicked", lambda b: self._load_config_preview())
        config_btn_row.append(refresh_config_btn)

        config_path = get_real_user_home() / ".reticulum" / "config"
        edit_config_btn = Gtk.Button(label="Edit in Terminal")
        edit_config_btn.set_tooltip_text(f"Edit {config_path} in nano")
        edit_config_btn.connect("clicked", lambda b: self._edit_config_terminal(config_path))
        config_btn_row.append(edit_config_btn)

        config_box.append(config_btn_row)

        config_frame.set_child(config_box)
        parent.append(config_frame)

        # Load config preview after UI is built (use tracked timer for cleanup)
        self._schedule_timer(2000, self._load_config_preview)
        self._schedule_timer(1500, self._load_rnode_config)

    def _load_rnode_config(self, button=None):
        """Load RNode config from ~/.reticulum/config"""
        def do_load():
            try:
                config_path = get_real_user_home() / ".reticulum" / "config"
                if not config_path.exists():
                    GLib.idle_add(self._set_rnode_status, "Config file not found")
                    return

                content = config_path.read_text()

                # Parse RNodeInterface section
                in_rnode = False
                rnode_config = {}
                for line in content.split('\n'):
                    line = line.strip()
                    if line.startswith('[') and 'RNode' in line:
                        in_rnode = True
                        continue
                    if in_rnode and line.startswith('['):
                        break
                    if in_rnode and '=' in line and not line.startswith('#'):
                        key, val = line.split('=', 1)
                        rnode_config[key.strip().lower()] = val.strip()

                if rnode_config:
                    GLib.idle_add(self._update_rnode_ui, rnode_config)
                else:
                    GLib.idle_add(self._set_rnode_status, "No RNode interface found in config")

            except Exception as e:
                logger.error(f"Load RNode config error: {e}")
                GLib.idle_add(self._set_rnode_status, f"Error: {e}")

        threading.Thread(target=do_load, daemon=True).start()
        return False

    def _update_rnode_ui(self, config):
        """Update UI with loaded RNode config"""
        if 'port' in config:
            self.rnode_port.set_text(config['port'])
        if 'frequency' in config:
            freq_hz = int(config['frequency'])
            self.rnode_freq.set_value(freq_hz / 1000000.0)
        if 'bandwidth' in config:
            bw_hz = int(config['bandwidth'])
            bw_map = {7800: 0, 10400: 1, 15600: 2, 20800: 3, 31250: 4,
                      41700: 5, 62500: 6, 125000: 7, 250000: 8, 500000: 9}
            self.rnode_bw.set_selected(bw_map.get(bw_hz, 8))
        if 'spreadingfactor' in config:
            self.rnode_sf.set_value(int(config['spreadingfactor']))
        if 'codingrate' in config:
            cr = int(config['codingrate']) - 5  # 5->0, 6->1, 7->2, 8->3
            self.rnode_cr.set_selected(max(0, min(3, cr)))
        if 'txpower' in config:
            self.rnode_tx.set_value(int(config['txpower']))
        self._set_rnode_status("Config loaded")

    def _set_rnode_status(self, msg):
        """Set RNode status message"""
        self.rnode_status.set_label(msg)

    def _log_detection(self, msg, clear=False):
        """Append message to detection log (copyable TextView)"""
        if not hasattr(self, 'detection_log_buffer'):
            return

        def do_log():
            if clear:
                self.detection_log_buffer.set_text(msg)
            else:
                end_iter = self.detection_log_buffer.get_end_iter()
                current_text = self.detection_log_buffer.get_text(
                    self.detection_log_buffer.get_start_iter(),
                    end_iter,
                    True
                )
                if current_text and not current_text.endswith('\n'):
                    msg_with_newline = '\n' + msg
                else:
                    msg_with_newline = msg
                self.detection_log_buffer.insert(end_iter, msg_with_newline)

                # Auto-scroll to bottom
                end_mark = self.detection_log_buffer.create_mark(None, self.detection_log_buffer.get_end_iter(), False)
                self.detection_log_view.scroll_mark_onscreen(end_mark)

        GLib.idle_add(do_log)

    def _copy_detection_log(self, button):
        """Copy detection log to clipboard"""
        if not hasattr(self, 'detection_log_buffer'):
            return

        text = self.detection_log_buffer.get_text(
            self.detection_log_buffer.get_start_iter(),
            self.detection_log_buffer.get_end_iter(),
            True
        )

        clipboard = self.detection_log_view.get_clipboard()
        clipboard.set(text)
        self._set_rnode_status("Log copied to clipboard")

    def _load_config_preview(self):
        """Load and display RNS config file in preview"""
        def do_load():
            try:
                config_path = get_real_user_home() / ".reticulum" / "config"
                has_rnode = False

                if not config_path.exists():
                    preview = f"# Config file not found: {config_path}\n\n"
                    preview += "# Create config by running: rnsd\n"
                    preview += "# Then click 'Add RNode Template' to add an interface."
                    GLib.idle_add(self.config_preview_buffer.set_text, preview)
                    GLib.idle_add(self._show_add_template_button, True)
                    return

                content = config_path.read_text()

                # Extract RNode section for focused preview
                lines = content.split('\n')
                rnode_section = []
                in_rnode = False
                for line in lines:
                    if '[[' in line and 'RNode' in line:
                        in_rnode = True
                    elif '[[' in line and in_rnode:
                        break
                    if in_rnode:
                        rnode_section.append(line)

                if rnode_section:
                    has_rnode = True
                    preview = f"# RNode Interface Configuration\n# File: {config_path}\n\n"
                    preview += '\n'.join(rnode_section)
                else:
                    # Show a helpful template when no RNode is configured
                    preview = f"# No RNode interface found in config\n# File: {config_path}\n\n"
                    preview += "# ═══════════════════════════════════════════════════════════\n"
                    preview += "# EXAMPLE RNode TEMPLATE (click 'Add RNode Template' to add):\n"
                    preview += "# ═══════════════════════════════════════════════════════════\n\n"
                    preview += "[[RNode LoRa Interface]]\n"
                    preview += "  type = RNodeInterface\n"
                    preview += "  interface_enabled = True\n"
                    preview += "  port = /dev/ttyUSB0        # Your RNode port\n"
                    preview += "  frequency = 906000000      # 906 MHz (US)\n"
                    preview += "  bandwidth = 250000         # 250 kHz\n"
                    preview += "  txpower = 17               # dBm\n"
                    preview += "  spreadingfactor = 11       # SF11\n"
                    preview += "  codingrate = 6             # 4/6\n\n"
                    preview += "# ───────────────────────────────────────────────────────────\n"
                    preview += "# Options:\n"
                    preview += "#   1. Click 'Add RNode Template' to auto-add this template\n"
                    preview += "#   2. Use 'Apply' above to apply form settings\n"
                    preview += "#   3. Click 'Edit in Terminal' for manual editing\n"
                    preview += "# ───────────────────────────────────────────────────────────"

                GLib.idle_add(self.config_preview_buffer.set_text, preview)
                GLib.idle_add(self._show_add_template_button, not has_rnode)

            except Exception as e:
                logger.error(f"Load config preview error: {e}")
                GLib.idle_add(
                    self.config_preview_buffer.set_text,
                    f"Error loading config: {e}"
                )

        threading.Thread(target=do_load, daemon=True).start()
        return False  # Don't repeat timeout

    def _show_add_template_button(self, show: bool):
        """Show or hide the Add RNode Template button"""
        if hasattr(self, 'add_rnode_template_btn'):
            self.add_rnode_template_btn.set_visible(show)

    def _on_add_rnode_template(self, button):
        """Add RNode template to the RNS config file"""
        button.set_sensitive(False)

        def do_add():
            try:
                from ..utils.rns_config import get_rns_config_path, add_interface_to_config

                config_path = get_rns_config_path()

                # Default RNode template - US frequency, MEDIUM_FAST compatible
                rnode_template = """[[RNode LoRa Interface]]
  type = RNodeInterface
  interface_enabled = True
  port = /dev/ttyUSB0
  frequency = 906000000
  bandwidth = 250000
  txpower = 17
  spreadingfactor = 11
  codingrate = 6
"""

                result = add_interface_to_config(config_path, rnode_template, "RNode")

                if result['success']:
                    GLib.idle_add(self._set_rnode_status, "RNode template added! Edit port as needed.")
                    # Reload the config preview and form
                    GLib.idle_add(self._load_config_preview)
                    GLib.idle_add(self._load_rnode_config)
                else:
                    GLib.idle_add(self._set_rnode_status, f"Failed: {result.get('error', 'Unknown error')}")

            except Exception as e:
                logger.error(f"Add RNode template error: {e}")
                GLib.idle_add(self._set_rnode_status, f"Error: {e}")
            finally:
                GLib.idle_add(button.set_sensitive, True)

        threading.Thread(target=do_add, daemon=True).start()

    def _apply_rnode_config(self, button):
        """Apply RNode configuration to ~/.reticulum/config"""
        # Get values with safe bounds checking
        port = self.rnode_port.get_text().strip()
        freq_mhz = self.rnode_freq.get_value()
        freq_hz = int(freq_mhz * 1000000)
        bw_values = [7800, 10400, 15600, 20800, 31250, 41700, 62500, 125000, 250000, 500000]
        bw_idx = self.rnode_bw.get_selected()
        # Guard against -1 (GTK_INVALID_LIST_POSITION) or out of bounds
        if bw_idx < 0 or bw_idx >= len(bw_values):
            bw_idx = 8  # Default to 250 kHz
        bw_hz = bw_values[bw_idx]
        sf = int(self.rnode_sf.get_value())
        cr_idx = self.rnode_cr.get_selected()
        # Guard against invalid coding rate selection
        if cr_idx < 0 or cr_idx > 3:
            cr_idx = 0  # Default to 4/5
        cr = cr_idx + 5  # 0->5, 1->6, 2->7, 3->8
        tx = int(self.rnode_tx.get_value())

        def do_apply():
            try:
                # Use safe config utilities
                from ..utils.rns_config import get_rns_config_path, add_interface_to_config

                config_path = get_rns_config_path()

                # Build RNode section
                rnode_section = f"""[[RNode LoRa Interface]]
  type = RNodeInterface
  interface_enabled = True
  port = {port}
  frequency = {freq_hz}
  bandwidth = {bw_hz}
  txpower = {tx}
  spreadingfactor = {sf}
  codingrate = {cr}
"""

                # Use safe add_interface_to_config with validation and backup
                result = add_interface_to_config(config_path, rnode_section, "RNode")

                if result['success']:
                    backup_msg = f" (backup: {result['backup_path']})" if result['backup_path'] else ""
                    GLib.idle_add(self._set_rnode_status, f"Config saved! Restart rnsd to apply.{backup_msg}")
                    logger.info(f"RNode config saved: freq={freq_hz}, bw={bw_hz}, sf={sf}, cr={cr}, tx={tx}")
                else:
                    GLib.idle_add(self._set_rnode_status, f"Error: {result['error']}")
                    logger.error(f"RNode config save failed: {result['error']}")

            except ImportError:
                # Fallback to old method if utils not available
                logger.warning("rns_config utils not available, using fallback")
                self._apply_rnode_config_fallback(port, freq_hz, bw_hz, tx, sf, cr)
            except Exception as e:
                logger.error(f"Apply RNode config error: {e}")
                GLib.idle_add(self._set_rnode_status, f"Error: {e}")

        self._set_rnode_status("Saving...")
        threading.Thread(target=do_apply, daemon=True).start()

    def _apply_rnode_config_fallback(self, port, freq_hz, bw_hz, tx, sf, cr):
        """Fallback config save without validation (legacy support)"""
        import re
        try:
            config_path = get_real_user_home() / ".reticulum" / "config"

            if config_path.exists():
                content = config_path.read_text()
            else:
                content = "[reticulum]\n  share_instance = Yes\n\n[interfaces]\n"

            rnode_section = f"""[[RNode LoRa Interface]]
  type = RNodeInterface
  interface_enabled = True
  port = {port}
  frequency = {freq_hz}
  bandwidth = {bw_hz}
  txpower = {tx}
  spreadingfactor = {sf}
  codingrate = {cr}
"""
            if '[[RNode' in content or '[RNode' in content:
                pattern = r'\[\[?RNode[^\]]*\]\]?[^\[]*'
                content = re.sub(pattern, rnode_section.strip() + '\n\n', content, flags=re.IGNORECASE)
            else:
                content = content.rstrip() + '\n\n' + rnode_section

            config_path.write_text(content)
            GLib.idle_add(self._set_rnode_status, f"Config saved (legacy)! Restart rnsd.")
        except Exception as e:
            logger.error(f"Fallback config save error: {e}")
            GLib.idle_add(self._set_rnode_status, f"Error: {e}")

    # =========================================================================
    # Meshtastic Preset Methods
    # =========================================================================

    def _on_preset_changed(self, dropdown, param):
        """Handle preset dropdown selection change"""
        if not HAS_LORA_PRESETS:
            return

        preset_names = list(MESHTASTIC_PRESETS.keys())
        selected_idx = dropdown.get_selected()
        if 0 <= selected_idx < len(preset_names):
            preset_name = preset_names[selected_idx]
            preset_data = MESHTASTIC_PRESETS.get(preset_name, {})

            # Update description label
            desc = preset_data.get('description', '')
            warning = preset_data.get('warning', '')
            if warning:
                desc = f"⚠️ {warning}\n{desc}"
            if preset_data.get('recommended'):
                desc = f"⭐ {desc}"
            if preset_data.get('default'):
                desc = f"🔧 {desc}"

            self.preset_description.set_label(desc)

    def _on_detect_meshtastic(self, button):
        """Detect Meshtastic LoRa settings from connected device"""
        if not HAS_LORA_PRESETS:
            self._set_rnode_status("Preset module not available")
            return

        button.set_sensitive(False)
        self._set_rnode_status("Detecting Meshtastic settings...")
        self.detected_settings_label.set_label("Scanning...")

        # Clear and start fresh log
        self._log_detection("--- Meshtastic Detection Started ---", clear=True)
        import datetime
        self._log_detection(f"Time: {datetime.datetime.now().strftime('%H:%M:%S')}")

        def do_detect():
            # Use verbose mode to get detailed logging
            settings = detect_meshtastic_settings(verbose=True)

            # Log all attempts to the log viewer (copyable!)
            attempts = settings.get('attempts_log', []) if settings else []
            for attempt in attempts:
                self._log_detection(attempt)

            if settings and settings.get('preset'):
                # Update UI with detected settings
                preset = settings.get('preset', 'Unknown')
                region = settings.get('region', 'US')
                bw = settings.get('bandwidth', 0) / 1000  # Hz to kHz
                sf = settings.get('spreading_factor', 0)
                cr = settings.get('coding_rate', 0)
                method = settings.get('detection_method', 'unknown')

                # Short summary in label
                info = f"✓ {preset} ({region}) via {method}"
                GLib.idle_add(self.detected_settings_label.set_label, info)

                # Detailed info in log
                self._log_detection(f"\n✓ SUCCESS: Detected {preset}")
                self._log_detection(f"  Region: {region}")
                self._log_detection(f"  Bandwidth: {bw:.0f} kHz")
                self._log_detection(f"  Spreading Factor: {sf}")
                self._log_detection(f"  Coding Rate: 4/{cr}")
                self._log_detection(f"  Method: {method}")

                # Select the detected preset in dropdown
                preset_names = list(MESHTASTIC_PRESETS.keys())
                try:
                    idx = preset_names.index(preset)
                    GLib.idle_add(self.meshtastic_preset_dropdown.set_selected, idx)
                except ValueError:
                    pass

                GLib.idle_add(self._set_rnode_status, f"Detected: {preset} via {method}")
            else:
                # Short error in label
                GLib.idle_add(self.detected_settings_label.set_label, "⚠️ Not detected - see log")

                # Detailed troubleshooting in log
                self._log_detection("\n⚠️ DETECTION FAILED")
                self._log_detection("Troubleshooting steps:")
                self._log_detection("  1. Check meshtasticd: systemctl status meshtasticd")
                self._log_detection("  2. Check USB devices: ls /dev/ttyUSB* /dev/ttyACM*")
                self._log_detection("  3. Check CLI: meshtastic --version")
                self._log_detection("\nNote: Copy this log for debugging (use 'Copy All' button)")

                GLib.idle_add(self._set_rnode_status, "Detection failed - check log below")

            GLib.idle_add(button.set_sensitive, True)

        threading.Thread(target=do_detect, daemon=True).start()

    def _on_apply_preset(self, button):
        """Apply selected Meshtastic preset to RNode configuration form"""
        if not HAS_LORA_PRESETS:
            self._set_rnode_status("Preset module not available")
            return

        preset_names = list(MESHTASTIC_PRESETS.keys())
        selected_idx = self.meshtastic_preset_dropdown.get_selected()

        if 0 <= selected_idx < len(preset_names):
            preset_name = preset_names[selected_idx]

            try:
                config = get_rnode_config_for_meshtastic_preset(
                    preset_name,
                    region='US',  # Default to US, could add region selector later
                    channel_slot=20,  # MtnMesh standard slot
                    tx_power=22
                )

                # Apply to form fields
                self.rnode_freq.set_value(config.frequency / 1000000.0)
                self.rnode_bw.set_selected(bandwidth_hz_to_index(config.bandwidth))
                self.rnode_sf.set_value(config.spreading_factor)
                self.rnode_cr.set_selected(coding_rate_to_index(config.coding_rate))
                self.rnode_tx.set_value(config.tx_power)

                self._set_rnode_status(
                    f"Applied {preset_name}: {config.frequency/1e6:.3f}MHz, "
                    f"BW{config.bandwidth/1000:.0f}k, SF{config.spreading_factor}, CR4/{config.coding_rate}"
                )

            except Exception as e:
                logger.error(f"Apply preset error: {e}")
                self._set_rnode_status(f"Error applying preset: {e}")

    def _on_apply_proven_config(self, button):
        """Apply a proven/tested gateway configuration"""
        if not HAS_LORA_PRESETS:
            self._set_rnode_status("Preset module not available")
            return

        proven_configs = list(PROVEN_GATEWAY_CONFIGS.keys())
        selected_idx = self.proven_dropdown.get_selected()

        if 0 <= selected_idx < len(proven_configs):
            config_key = proven_configs[selected_idx]
            config = PROVEN_GATEWAY_CONFIGS[config_key]

            try:
                # Apply configuration to form
                self.rnode_freq.set_value(config['frequency'] / 1000000.0)
                self.rnode_bw.set_selected(bandwidth_hz_to_index(config['bandwidth']))
                self.rnode_sf.set_value(config['spreading_factor'])
                self.rnode_cr.set_selected(coding_rate_to_index(config['coding_rate']))
                self.rnode_tx.set_value(config['tx_power'])

                # Also select the matching Meshtastic preset
                preset_names = list(MESHTASTIC_PRESETS.keys())
                try:
                    idx = preset_names.index(config['meshtastic_preset'])
                    self.meshtastic_preset_dropdown.set_selected(idx)
                except ValueError:
                    pass

                self._set_rnode_status(
                    f"Applied '{config['name']}' - {config.get('notes', '')}"
                )

            except Exception as e:
                logger.error(f"Apply proven config error: {e}")
                self._set_rnode_status(f"Error applying config: {e}")

    # =========================================================================
    # Radio Detection Methods
    # =========================================================================

    def _check_radio_services(self):
        """Check status of meshtasticd and rnsd services"""
        def do_check():
            import datetime

            # Log startup check
            self._log_detection("--- Service Status Check ---", clear=True)
            self._log_detection(f"Time: {datetime.datetime.now().strftime('%H:%M:%S')}")

            # Check meshtasticd (port 4403)
            meshtasticd_running = False
            meshtasticd_method = "port check"
            if HAS_SERVICE_CHECK and check_port:
                meshtasticd_running = check_port(4403)
            else:
                # Fallback: try socket
                import socket
                meshtasticd_method = "socket"
                sock = None
                try:
                    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    sock.settimeout(1)
                    result = sock.connect_ex(('localhost', 4403))
                    meshtasticd_running = result == 0
                except (socket.error, OSError):
                    pass
                finally:
                    if sock:
                        try:
                            sock.close()
                        except Exception:
                            pass

            # Check rnsd (use systemctl)
            rnsd_running = False
            try:
                import subprocess
                result = subprocess.run(
                    ['systemctl', 'is-active', 'rnsd'],
                    capture_output=True, text=True, timeout=5
                )
                rnsd_running = result.returncode == 0
            except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
                pass

            # Log results
            if meshtasticd_running:
                self._log_detection(f"✓ meshtasticd: running (TCP :4403 via {meshtasticd_method})")
            else:
                self._log_detection(f"✗ meshtasticd: not running (checked :4403)")

            if rnsd_running:
                self._log_detection("✓ rnsd: running (systemctl)")
            else:
                self._log_detection("✗ rnsd: not running")

            self._log_detection("\nClick 'Detect' to scan for hardware devices.")
            self._log_detection("Click 'Detect Meshtastic' to read radio settings.")

            # Update UI indicators
            if meshtasticd_running:
                GLib.idle_add(
                    self.meshtasticd_status.set_label,
                    "● meshtasticd: running"
                )
                GLib.idle_add(
                    self.meshtasticd_status.remove_css_class, "error"
                )
                GLib.idle_add(
                    self.meshtasticd_status.add_css_class, "success"
                )
            else:
                GLib.idle_add(
                    self.meshtasticd_status.set_label,
                    "● meshtasticd: stopped"
                )
                GLib.idle_add(
                    self.meshtasticd_status.add_css_class, "warning"
                )

            if rnsd_running:
                GLib.idle_add(
                    self.rnsd_status.set_label,
                    "● rnsd: running"
                )
                GLib.idle_add(
                    self.rnsd_status.add_css_class, "success"
                )
            else:
                GLib.idle_add(
                    self.rnsd_status.set_label,
                    "● rnsd: stopped"
                )
                GLib.idle_add(
                    self.rnsd_status.add_css_class, "warning"
                )

        threading.Thread(target=do_check, daemon=True).start()

    def _on_detect_devices(self, button):
        """Detect RNode and Meshtastic devices"""
        button.set_sensitive(False)
        self.device_info_label.set_label("Scanning...")

        # Log to detection log
        import datetime
        self._log_detection("--- Device Detection Started ---", clear=True)
        self._log_detection(f"Time: {datetime.datetime.now().strftime('%H:%M:%S')}")

        def do_detect():
            devices = []
            device_names = []

            # Use centralized RNode detection
            if HAS_RNODE_DETECTION and detect_devices:
                self._log_detection("Using RNode detection module...")
                try:
                    detected = detect_devices(probe=True)
                    if detected:  # Only iterate if we got results
                        for dev in detected:
                            try:
                                devices.append(dev)
                                # Build display name
                                name = f"{dev.port}"
                                if dev.model and dev.model != "Unknown":
                                    name += f" ({dev.model})"
                                if dev.is_rnode:
                                    name += " [RNode]"
                                if dev.is_configured:
                                    name += " [Configured]"
                                device_names.append(name)

                                # Log device details
                                self._log_detection(f"\n✓ Found: {dev.port}")
                                if dev.model and dev.model != "Unknown":
                                    self._log_detection(f"  Model: {dev.model}")
                                if dev.vid and dev.pid:
                                    self._log_detection(f"  USB: VID={dev.vid} PID={dev.pid}")
                                if dev.is_rnode:
                                    self._log_detection("  Type: RNode firmware detected")
                                if dev.firmware_version:
                                    self._log_detection(f"  Firmware: {dev.firmware_version}")
                                if dev.is_configured:
                                    self._log_detection("  Status: Configured in RNS")
                            except Exception as dev_err:
                                logger.error(f"Error processing device: {dev_err}")
                                self._log_detection(f"  ⚠️ Error reading device: {dev_err}")

                except OSError as e:
                    # Handle errno 22 (EINVAL) and errno 24 (EMFILE) specifically
                    import errno
                    if hasattr(e, 'errno'):
                        if e.errno == errno.EINVAL:
                            logger.error(f"Invalid argument error during detection: {e}")
                            self._log_detection(f"✗ Serial port error (EINVAL): {e}")
                            self._log_detection("  This may indicate a device permission or configuration issue")
                        elif e.errno == errno.EMFILE:
                            logger.error(f"Too many open files during detection: {e}")
                            self._log_detection(f"✗ File descriptor limit reached: {e}")
                            self._log_detection("  Try: ulimit -n 4096 or restart MeshForge")
                        else:
                            logger.error(f"OS error during detection (errno {e.errno}): {e}")
                            self._log_detection(f"✗ OS error: {e}")
                    else:
                        logger.error(f"Detection OS error: {e}")
                        self._log_detection(f"✗ Detection error: {e}")
                except Exception as e:
                    logger.error(f"Device detection error: {e}")
                    self._log_detection(f"✗ Detection error: {e}")
            else:
                # Fallback: just glob serial ports
                self._log_detection("Fallback mode: scanning serial ports...")
                import glob
                ports = []
                for pattern in ['/dev/ttyUSB*', '/dev/ttyACM*', '/dev/ttyAMA*']:
                    found = glob.glob(pattern)
                    ports.extend(found)
                    if found:
                        self._log_detection(f"  {pattern}: {len(found)} found")
                for port in sorted(set(ports)):
                    device_names.append(port)

            # Store detected devices
            self._detected_devices = devices

            if not device_names:
                device_names = ["No devices detected"]
                info_text = "No devices found"
                self._log_detection("\n⚠️ No devices detected")
                self._log_detection("Check:")
                self._log_detection("  • USB cable connected?")
                self._log_detection("  • Device powered on?")
                self._log_detection("  • Permissions: user in 'dialout' group?")
            else:
                info_text = f"Found {len(device_names)} device(s)"
                self._log_detection(f"\nTotal: {len(device_names)} device(s) found")

            # Update UI
            GLib.idle_add(self._update_device_dropdown, device_names)
            GLib.idle_add(self.device_info_label.set_label, info_text)
            GLib.idle_add(button.set_sensitive, True)

        threading.Thread(target=do_detect, daemon=True).start()

    def _update_device_dropdown(self, device_names):
        """Update the device dropdown with detected devices"""
        # Create new string list model
        model = Gtk.StringList.new(device_names)
        self.device_dropdown.set_model(model)
        if device_names and device_names[0] != "No devices detected":
            self.device_dropdown.set_selected(0)

    def _on_device_selected(self, dropdown, _):
        """Handle device selection from dropdown"""
        selected_idx = dropdown.get_selected()

        # Guard against GTK_INVALID_LIST_POSITION (-1) and out of bounds
        if selected_idx < 0 or not self._detected_devices or selected_idx >= len(self._detected_devices):
            return

        device = self._detected_devices[selected_idx]

        # Update port entry with selected device
        if hasattr(self, 'rnode_port'):
            self.rnode_port.set_text(device.port)

        # Update device info
        info_parts = [f"Port: {device.port}"]
        if device.model and device.model != "Unknown":
            info_parts.append(f"Model: {device.model}")
        if device.vid and device.pid:
            info_parts.append(f"USB: VID:{device.vid} PID:{device.pid}")
        if device.is_rnode:
            info_parts.append("✓ RNode firmware detected")
        if device.firmware_version:
            info_parts.append(f"Firmware: {device.firmware_version}")
        if device.is_configured:
            info_parts.append("✓ Configured in RNS config")

        self.device_info_label.set_label("\n".join(info_parts))

        # If device has RNS config, try to auto-detect Meshtastic and suggest gateway template
        if device.is_rnode or device.model != "Unknown":
            self._auto_match_gateway_template()

    def _auto_match_gateway_template(self):
        """Automatically suggest gateway template based on detected settings"""
        if not HAS_LORA_PRESETS:
            return

        def do_match():
            # Detect Meshtastic settings with verbose logging
            settings = detect_meshtastic_settings(verbose=True) if detect_meshtastic_settings else None

            if settings and settings.get('preset'):
                preset = settings.get('preset', 'Unknown')
                method = settings.get('detection_method', 'unknown')

                # Find matching proven gateway config
                matched_config = None
                matched_key = None
                for key, config in PROVEN_GATEWAY_CONFIGS.items():
                    if config.get('meshtastic_preset') == preset:
                        matched_config = config
                        matched_key = key
                        break

                if matched_config:
                    info = (
                        f"✓ Detected Meshtastic: {preset} (via {method})\n"
                        f"→ Recommended: {matched_config['name']}\n"
                        f"  {matched_config.get('notes', '')}"
                    )
                    GLib.idle_add(self._suggest_gateway_config, matched_key, info)
                else:
                    info = (
                        f"✓ Detected Meshtastic: {preset} (via {method})\n"
                        f"→ No proven gateway template for this preset.\n"
                        f"  Use 'Apply Preset' to configure manually."
                    )
                    GLib.idle_add(self._append_device_info, info)
            else:
                # Show what was tried
                attempts = settings.get('attempts_log', []) if settings else []
                log_summary = attempts[-3:] if attempts else ["No detection attempts"]

                info = (
                    "Could not detect Meshtastic settings.\n"
                    f"Tried: {len(attempts)} methods\n"
                    "Select a preset manually or check:\n"
                    "• meshtasticd status\n"
                    "• USB device connection"
                )
                GLib.idle_add(self._append_device_info, info)

        threading.Thread(target=do_match, daemon=True).start()

    def _append_device_info(self, text):
        """Append text to device info label"""
        current = self.device_info_label.get_label()
        if current:
            self.device_info_label.set_label(f"{current}\n\n{text}")
        else:
            self.device_info_label.set_label(text)

    def _suggest_gateway_config(self, config_key, info_text):
        """Suggest and optionally auto-select a gateway configuration"""
        # Update info display
        current_text = self.device_info_label.get_label()
        self.device_info_label.set_label(f"{current_text}\n\n{info_text}")

        # Select the matching proven config in dropdown
        if hasattr(self, 'proven_dropdown'):
            proven_configs = list(PROVEN_GATEWAY_CONFIGS.keys())
            try:
                idx = proven_configs.index(config_key)
                self.proven_dropdown.set_selected(idx)
            except ValueError:
                pass

        # Also set the RNode status
        self._set_rnode_status(f"Suggested: {PROVEN_GATEWAY_CONFIGS[config_key]['name']}")
