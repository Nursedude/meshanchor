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

        # Try to load current config
        GLib.timeout_add(1500, self._load_rnode_config)

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

    def _apply_rnode_config(self, button):
        """Apply RNode configuration to ~/.reticulum/config"""
        # Get values
        port = self.rnode_port.get_text().strip()
        freq_mhz = self.rnode_freq.get_value()
        freq_hz = int(freq_mhz * 1000000)
        bw_values = [7800, 10400, 15600, 20800, 31250, 41700, 62500, 125000, 250000, 500000]
        bw_hz = bw_values[self.rnode_bw.get_selected()]
        sf = int(self.rnode_sf.get_value())
        cr = int(self.rnode_cr.get_selected()) + 5  # 0->5, 1->6, 2->7, 3->8
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

        def do_detect():
            settings = detect_meshtastic_settings()

            if settings:
                # Update UI with detected settings
                preset = settings.get('preset', 'Unknown')
                region = settings.get('region', 'US')
                bw = settings.get('bandwidth', 0) / 1000  # Hz to kHz
                sf = settings.get('spreading_factor', 0)
                cr = settings.get('coding_rate', 0)

                info = (
                    f"✓ Detected: {preset} ({region})\n"
                    f"  BW: {bw:.0f}kHz, SF: {sf}, CR: 4/{cr}"
                )
                GLib.idle_add(self.detected_settings_label.set_label, info)

                # Select the detected preset in dropdown
                preset_names = list(MESHTASTIC_PRESETS.keys())
                try:
                    idx = preset_names.index(preset)
                    GLib.idle_add(self.meshtastic_preset_dropdown.set_selected, idx)
                except ValueError:
                    pass

                GLib.idle_add(self._set_rnode_status, f"Detected: {preset}")
            else:
                GLib.idle_add(
                    self.detected_settings_label.set_label,
                    "⚠️ Could not detect Meshtastic settings.\n"
                    "Ensure meshtasticd is running (localhost:4403) or device is connected."
                )
                GLib.idle_add(self._set_rnode_status, "Detection failed - select preset manually")

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
