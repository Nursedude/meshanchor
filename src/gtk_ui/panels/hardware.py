"""
Hardware Detection Panel - Detect and configure hardware
"""

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, GLib
import subprocess
import threading
from pathlib import Path


class HardwarePanel(Gtk.Box):
    """Hardware detection and configuration panel"""

    def __init__(self, main_window):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=15)
        self.main_window = main_window

        self.set_margin_start(20)
        self.set_margin_end(20)
        self.set_margin_top(20)
        self.set_margin_bottom(20)

        self._build_ui()

    def _build_ui(self):
        """Build the hardware panel UI"""
        # Title
        title = Gtk.Label(label="Hardware Detection")
        title.add_css_class("title-1")
        title.set_xalign(0)
        self.append(title)

        # System info frame
        sys_frame = Gtk.Frame()
        sys_frame.set_label("System Information")

        sys_grid = Gtk.Grid()
        sys_grid.set_row_spacing(5)
        sys_grid.set_column_spacing(20)
        sys_grid.set_margin_start(15)
        sys_grid.set_margin_end(15)
        sys_grid.set_margin_top(10)
        sys_grid.set_margin_bottom(10)

        # System info labels
        self.sys_labels = {}
        sys_items = [
            ("Platform", "platform"),
            ("Model", "model"),
            ("Architecture", "arch"),
            ("Kernel", "kernel"),
            ("OS", "os"),
        ]

        for i, (label, key) in enumerate(sys_items):
            lbl = Gtk.Label(label=f"{label}:")
            lbl.set_xalign(1)
            lbl.add_css_class("dim-label")
            sys_grid.attach(lbl, 0, i, 1, 1)

            val = Gtk.Label(label="--")
            val.set_xalign(0)
            val.set_hexpand(True)
            sys_grid.attach(val, 1, i, 1, 1)
            self.sys_labels[key] = val

        sys_frame.set_child(sys_grid)
        self.append(sys_frame)

        # Interface status frame
        iface_frame = Gtk.Frame()
        iface_frame.set_label("Interface Status")

        iface_grid = Gtk.Grid()
        iface_grid.set_row_spacing(5)
        iface_grid.set_column_spacing(20)
        iface_grid.set_margin_start(15)
        iface_grid.set_margin_end(15)
        iface_grid.set_margin_top(10)
        iface_grid.set_margin_bottom(10)

        self.iface_labels = {}
        iface_items = [
            ("SPI", "spi"),
            ("I2C", "i2c"),
            ("GPIO", "gpio"),
            ("Serial", "serial"),
        ]

        for i, (label, key) in enumerate(iface_items):
            lbl = Gtk.Label(label=f"{label}:")
            lbl.set_xalign(1)
            lbl.add_css_class("dim-label")
            iface_grid.attach(lbl, 0, i, 1, 1)

            status = Gtk.Label(label="--")
            status.set_xalign(0)
            iface_grid.attach(status, 1, i, 1, 1)
            self.iface_labels[key] = status

            # Action button
            if key in ["spi", "i2c"]:
                btn = Gtk.Button(label="Enable")
                btn.connect("clicked", lambda b, k=key: self._enable_interface(k))
                iface_grid.attach(btn, 2, i, 1, 1)

        iface_frame.set_child(iface_grid)
        self.append(iface_frame)

        # Detected hardware frame
        hw_frame = Gtk.Frame()
        hw_frame.set_label("Detected LoRa Hardware")
        hw_frame.set_vexpand(True)

        hw_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)

        # Hardware list
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_vexpand(True)

        self.hw_list = Gtk.ListBox()
        self.hw_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        scrolled.set_child(self.hw_list)

        hw_box.append(scrolled)

        # Detection info
        self.hw_info = Gtk.Label(label="Click 'Detect Hardware' to scan for devices")
        self.hw_info.set_xalign(0)
        self.hw_info.set_margin_start(10)
        self.hw_info.set_margin_bottom(5)
        hw_box.append(self.hw_info)

        hw_frame.set_child(hw_box)
        self.append(hw_frame)

        # Action buttons
        button_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        button_box.set_halign(Gtk.Align.CENTER)

        detect_btn = Gtk.Button(label="Detect Hardware")
        detect_btn.add_css_class("suggested-action")
        detect_btn.connect("clicked", lambda b: self._detect_hardware())
        button_box.append(detect_btn)

        refresh_btn = Gtk.Button(label="Refresh Status")
        refresh_btn.connect("clicked", lambda b: self._refresh_status())
        button_box.append(refresh_btn)

        apply_btn = Gtk.Button(label="Apply Recommended Config")
        apply_btn.connect("clicked", lambda b: self._apply_recommended())
        button_box.append(apply_btn)

        self.append(button_box)

        # Initial refresh
        self._refresh_status()

    def _refresh_status(self):
        """Refresh all status information"""
        thread = threading.Thread(target=self._fetch_status)
        thread.daemon = True
        thread.start()

    def _fetch_status(self):
        """Fetch status in background"""
        import platform
        import distro

        # System info
        sys_info = {
            "platform": platform.system(),
            "model": self._get_pi_model(),
            "arch": platform.machine(),
            "kernel": platform.release(),
            "os": distro.name(pretty=True) if hasattr(distro, 'name') else platform.platform(),
        }

        for key, value in sys_info.items():
            GLib.idle_add(self.sys_labels[key].set_label, value)

        # Interface status
        iface_status = {
            "spi": "Enabled" if Path('/dev/spidev0.0').exists() else "Disabled",
            "i2c": "Enabled" if Path('/dev/i2c-1').exists() else "Disabled",
            "gpio": "Available" if Path('/dev/gpiomem').exists() else "Not available",
            "serial": "Available" if Path('/dev/serial0').exists() else "Not available",
        }

        for key, value in iface_status.items():
            css = "success" if "Enabled" in value or "Available" in value else "warning"
            GLib.idle_add(self._update_iface_label, key, value, css)

    def _update_iface_label(self, key, value, css):
        """Update interface label with CSS class"""
        label = self.iface_labels[key]
        label.set_label(value)
        label.remove_css_class("success")
        label.remove_css_class("warning")
        label.add_css_class(css)
        return False

    def _get_pi_model(self):
        """Get Raspberry Pi model"""
        try:
            model_file = Path('/proc/device-tree/model')
            if model_file.exists():
                return model_file.read_text().strip().rstrip('\x00')
            return "Unknown"
        except:
            return "Unknown"

    def _enable_interface(self, interface):
        """Enable SPI or I2C"""
        def enable():
            try:
                if interface == "spi":
                    subprocess.run(
                        ['sudo', 'raspi-config', 'nonint', 'do_spi', '0'],
                        check=True, capture_output=True
                    )
                elif interface == "i2c":
                    subprocess.run(
                        ['sudo', 'raspi-config', 'nonint', 'do_i2c', '0'],
                        check=True, capture_output=True
                    )

                GLib.idle_add(
                    self.main_window.set_status_message,
                    f"{interface.upper()} enabled. Reboot required."
                )

                GLib.idle_add(
                    self.main_window.request_reboot,
                    f"{interface.upper()} interface was enabled"
                )

            except Exception as e:
                GLib.idle_add(
                    self.main_window.show_info_dialog,
                    "Error",
                    f"Failed to enable {interface.upper()}: {e}\n\nMake sure you're running as root or have sudo access."
                )

        thread = threading.Thread(target=enable)
        thread.daemon = True
        thread.start()

    def _detect_hardware(self):
        """Detect LoRa hardware"""
        self.hw_info.set_label("Scanning for hardware...")

        # Clear list
        while True:
            row = self.hw_list.get_row_at_index(0)
            if row:
                self.hw_list.remove(row)
            else:
                break

        def detect():
            import os
            import re
            import socket
            detected = []

            # First, check if meshtasticd is running and get its hardware info
            try:
                is_running = False

                # Check systemctl
                result = subprocess.run(
                    ['systemctl', 'is-active', 'meshtasticd'],
                    capture_output=True, text=True
                )
                if result.stdout.strip() == 'active':
                    is_running = True

                # Check process
                if not is_running:
                    result = subprocess.run(['pgrep', '-f', 'meshtasticd'],
                                           capture_output=True, text=True)
                    if result.returncode == 0:
                        is_running = True

                # Check TCP port
                if not is_running:
                    try:
                        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                        sock.settimeout(1.0)
                        if sock.connect_ex(('localhost', 4403)) == 0:
                            is_running = True
                        sock.close()
                    except Exception:
                        pass

                if is_running:
                    # Try to get hardware info from meshtastic CLI
                    cli_paths = [
                        '/root/.local/bin/meshtastic',
                        '/home/pi/.local/bin/meshtastic',
                        os.path.expanduser('~/.local/bin/meshtastic'),
                    ]

                    # Check SUDO_USER path
                    sudo_user = os.environ.get('SUDO_USER')
                    if sudo_user:
                        cli_paths.insert(0, f'/home/{sudo_user}/.local/bin/meshtastic')

                    cli_path = None
                    for path in cli_paths:
                        if os.path.exists(path) and os.access(path, os.X_OK):
                            cli_path = path
                            break
                    if not cli_path:
                        result = subprocess.run(['which', 'meshtastic'], capture_output=True, text=True)
                        if result.returncode == 0:
                            cli_path = result.stdout.strip()

                    hw_model = "Connected"
                    firmware = ""
                    if cli_path:
                        try:
                            result = subprocess.run(
                                [cli_path, '--host', 'localhost', '--info'],
                                capture_output=True, text=True, timeout=15
                            )
                            if result.returncode == 0:
                                output = result.stdout
                                # Parse hardware model from JSON
                                hw_match = re.search(r'"hwModel":\s*"([^"]+)"', output)
                                if hw_match:
                                    hw_model = hw_match.group(1)
                                # Parse firmware
                                fw_match = re.search(r'"firmwareVersion":\s*"([^"]+)"', output)
                                if fw_match:
                                    firmware = f" (v{fw_match.group(1)})"
                        except Exception:
                            pass

                    detected.append({
                        "type": "Active",
                        "device": "meshtasticd",
                        "description": f"Running - {hw_model}{firmware}"
                    })
                else:
                    detected.append({
                        "type": "Info",
                        "device": "meshtasticd",
                        "description": "Service not running"
                    })
            except Exception:
                pass

            # Check active configs in config.d
            config_d = Path('/etc/meshtasticd/config.d')
            if config_d.exists():
                active_configs = list(config_d.glob('*.yaml')) + list(config_d.glob('*.yml'))
                for config in active_configs:
                    detected.append({
                        "type": "Active",
                        "device": config.name,
                        "description": "Active configuration"
                    })

            # Check SPI devices
            spi_devices = list(Path('/dev').glob('spidev*'))
            for dev in spi_devices:
                detected.append({
                    "type": "SPI",
                    "device": str(dev),
                    "description": "SPI interface available"
                })

            # Check I2C devices
            try:
                result = subprocess.run(
                    ['i2cdetect', '-y', '1'],
                    capture_output=True, text=True, timeout=5
                )
                if result.returncode == 0:
                    # Parse I2C addresses
                    for line in result.stdout.split('\n'):
                        parts = line.split()
                        if len(parts) > 1:
                            for addr in parts[1:]:
                                if addr != '--' and len(addr) == 2 and all(c in '0123456789abcdefABCDEF' for c in addr):
                                    detected.append({
                                        "type": "I2C",
                                        "device": f"0x{addr}",
                                        "description": self._identify_i2c_device(addr)
                                    })
            except subprocess.TimeoutExpired:
                pass
            except Exception:
                pass

            # Check for common LoRa HAT configs in available.d
            available_d = Path('/etc/meshtasticd/available.d')
            if available_d.exists():
                lora_configs = list(available_d.glob('lora-*.yaml')) + list(available_d.glob('*.yml'))
                for config in lora_configs[:10]:  # Limit to 10
                    detected.append({
                        "type": "Available",
                        "device": config.name,
                        "description": "Available LoRa configuration"
                    })

            # Update UI
            for hw in detected:
                GLib.idle_add(self._add_hardware_row, hw)

            if detected:
                active_count = len([d for d in detected if d["type"] == "Active"])
                GLib.idle_add(
                    self.hw_info.set_label,
                    f"Found {len(detected)} items ({active_count} active)"
                )
            else:
                GLib.idle_add(
                    self.hw_info.set_label,
                    "No LoRa hardware detected. Check SPI/I2C settings."
                )

        thread = threading.Thread(target=detect)
        thread.daemon = True
        thread.start()

    def _identify_i2c_device(self, addr):
        """Identify common I2C devices by address"""
        known_devices = {
            "3c": "SSD1306 OLED Display",
            "3d": "SSD1306 OLED Display (alt)",
            "27": "PCF8574 I/O Expander",
            "20": "PCF8574 I/O Expander",
            "50": "AT24C32 EEPROM",
            "68": "DS3231 RTC / MPU6050",
            "76": "BME280 Sensor",
            "77": "BME280 Sensor (alt)",
        }
        return known_devices.get(addr.lower(), "Unknown device")

    def _add_hardware_row(self, hw):
        """Add a hardware item to the list"""
        row = Gtk.ListBoxRow()

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=15)
        box.set_margin_start(10)
        box.set_margin_end(10)
        box.set_margin_top(8)
        box.set_margin_bottom(8)

        # Type badge
        type_label = Gtk.Label(label=hw["type"])
        type_label.set_size_request(60, -1)
        type_label.add_css_class("heading")
        box.append(type_label)

        # Device
        device_label = Gtk.Label(label=hw["device"])
        device_label.set_xalign(0)
        device_label.set_size_request(150, -1)
        box.append(device_label)

        # Description
        desc_label = Gtk.Label(label=hw["description"])
        desc_label.set_xalign(0)
        desc_label.set_hexpand(True)
        desc_label.add_css_class("dim-label")
        box.append(desc_label)

        row.set_child(box)
        self.hw_list.append(row)
        return False

    def _apply_recommended(self):
        """Apply recommended configuration based on detected hardware"""
        row = self.hw_list.get_selected_row()

        if not row:
            self.main_window.show_info_dialog(
                "No Selection",
                "Please select a hardware item from the list first."
            )
            return

        # Get the selected hardware info
        box = row.get_child()
        labels = [child for child in box if isinstance(child, Gtk.Label)]

        if len(labels) >= 2:
            hw_type = labels[0].get_label()
            device = labels[1].get_label()

            if hw_type == "Config":
                # This is a config file - offer to activate it
                self.main_window.show_confirm_dialog(
                    "Activate Configuration?",
                    f"Would you like to activate the configuration '{device}'?",
                    lambda confirmed: self._activate_config(device) if confirmed else None
                )
            else:
                self.main_window.show_info_dialog(
                    "Hardware Detected",
                    f"Detected: {hw_type} - {device}\n\n"
                    "Go to 'Config File Manager' to select and activate "
                    "the appropriate configuration for your hardware."
                )

    def _activate_config(self, config_name):
        """Activate a configuration file"""
        import shutil

        try:
            src = Path('/etc/meshtasticd/available.d') / config_name
            dst = Path('/etc/meshtasticd/config.d') / config_name

            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)

            self.main_window.show_info_dialog(
                "Configuration Activated",
                f"'{config_name}' has been activated.\n\n"
                "Go to 'Service Management' to restart the service."
            )
        except Exception as e:
            self.main_window.show_info_dialog(
                "Error",
                f"Failed to activate configuration: {e}"
            )
