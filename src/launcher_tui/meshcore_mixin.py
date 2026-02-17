"""
MeshCore Mixin — MeshCore companion radio management in the TUI.

Provides menu items for:
- Device detection (serial scan)
- Connection status and configuration
- Gateway bridge MeshCore settings (enable/disable, connection type)
- Node listing and statistics

Uses gateway.meshcore_handler for actual device interaction and
gateway.config.MeshCoreConfig for persistent settings.
"""

import logging
from backend import clear_screen
from utils.safe_import import safe_import

logger = logging.getLogger(__name__)

# Import gateway components
_detect_meshcore_devices, _HAS_DETECT = safe_import(
    'gateway.meshcore_handler', 'detect_meshcore_devices'
)
_GatewayConfig, _MeshCoreConfig, _HAS_GW_CONFIG = safe_import(
    'gateway.config', 'GatewayConfig', 'MeshCoreConfig'
)


class MeshCoreMixin:
    """TUI mixin for MeshCore companion radio management."""

    def _meshcore_menu(self):
        """MeshCore — companion radio setup and monitoring."""
        while True:
            # Show current status in subtitle
            status_line = self._meshcore_status_line()

            choices = [
                ("status", "Connection Status   MeshCore radio state"),
                ("detect", "Detect Devices      Scan for serial devices"),
                ("config", "Configure           Connection settings"),
                ("enable", "Enable/Disable      Toggle MeshCore in gateway"),
                ("nodes", "View Nodes          MeshCore network nodes"),
                ("stats", "Statistics          Message & connection stats"),
                ("back", "Back"),
            ]

            choice = self.dialog.menu(
                "MeshCore Radio",
                status_line,
                choices
            )

            if choice is None or choice == "back":
                break

            dispatch = {
                "status": ("MeshCore Status", self._meshcore_status),
                "detect": ("Detect Devices", self._meshcore_detect),
                "config": ("MeshCore Config", self._meshcore_configure),
                "enable": ("Enable/Disable", self._meshcore_toggle),
                "nodes": ("MeshCore Nodes", self._meshcore_nodes),
                "stats": ("MeshCore Stats", self._meshcore_stats),
            }
            entry = dispatch.get(choice)
            if entry:
                self._safe_call(*entry)

    def _meshcore_status_line(self) -> str:
        """Build status line for MeshCore menu subtitle."""
        if not _HAS_GW_CONFIG:
            return "MeshCore companion radio management"

        try:
            config = _GatewayConfig.load()
            mc = getattr(config, 'meshcore', None)
            if not mc or not mc.enabled:
                return "MeshCore: DISABLED in gateway config"
            conn = mc.connection_type
            device = mc.device_path if conn == "serial" else f"{mc.tcp_host}:{mc.tcp_port}"
            return f"MeshCore: ENABLED ({conn} -> {device})"
        except Exception:
            return "MeshCore companion radio management"

    def _meshcore_status(self):
        """Show MeshCore connection status."""
        clear_screen()
        print("=== MeshCore Connection Status ===\n")

        if not _HAS_GW_CONFIG:
            print("  Gateway config module not available.")
            self._wait_for_enter()
            return

        try:
            config = _GatewayConfig.load()
        except Exception as e:
            print(f"  Could not load gateway config: {e}")
            self._wait_for_enter()
            return

        mc = getattr(config, 'meshcore', None)
        if not mc:
            print("  MeshCore not configured.")
            print("  Use 'Configure' to set up connection.")
            self._wait_for_enter()
            return

        print(f"  Enabled:          {'Yes' if mc.enabled else 'No'}")
        print(f"  Connection Type:  {mc.connection_type}")
        if mc.connection_type == "serial":
            print(f"  Device Path:      {mc.device_path}")
            print(f"  Baud Rate:        {mc.baud_rate}")

            # Check if device exists
            import os
            exists = os.path.exists(mc.device_path)
            print(f"  Device Present:   {'Yes' if exists else 'No (not plugged in?)'}")
        elif mc.connection_type == "tcp":
            print(f"  TCP Host:         {mc.tcp_host}")
            print(f"  TCP Port:         {mc.tcp_port}")
        print(f"  Bridge Channels:  {'Yes' if mc.bridge_channels else 'No'}")
        print(f"  Bridge DMs:       {'Yes' if mc.bridge_dms else 'No'}")
        print(f"  Simulation Mode:  {'Yes' if mc.simulation_mode else 'No'}")
        print(f"  Auto-Fetch Msgs:  {'Yes' if mc.auto_fetch_messages else 'No'}")

        # Check meshcore_py availability
        try:
            import meshcore as _mc_check  # noqa: F401
            print(f"\n  meshcore_py:      Installed")
        except ImportError:
            print(f"\n  meshcore_py:      NOT installed")
            print(f"  Install:          pip install meshcore")

        self._wait_for_enter()

    def _meshcore_detect(self):
        """Scan for MeshCore-compatible serial devices."""
        clear_screen()
        print("=== MeshCore Device Detection ===\n")

        if not _HAS_DETECT:
            print("  Device detection module not available.")
            self._wait_for_enter()
            return

        devices = _detect_meshcore_devices()

        if not devices:
            print("  No serial devices found.")
            print("\n  Check:")
            print("  - Is the radio plugged in via USB?")
            print("  - Does it show up with: ls /dev/ttyUSB* /dev/ttyACM*")
            print("  - Is the user in the 'dialout' group?")
            self._wait_for_enter()
            return

        print(f"  Found {len(devices)} serial device(s):\n")
        for i, dev in enumerate(devices, 1):
            print(f"  {i}. {dev}")

        print("\n  Note: These are serial ports that MAY be MeshCore radios.")
        print("  Verify by connecting and checking firmware response.")

        # Offer to set as device path
        if _HAS_GW_CONFIG and len(devices) >= 1:
            print(f"\n  Set {devices[0]} as MeshCore device? (Configure menu)")

        self._wait_for_enter()

    def _meshcore_configure(self):
        """Configure MeshCore connection settings."""
        if not _HAS_GW_CONFIG:
            self.dialog.msgbox(
                "Module Missing",
                "Gateway configuration module not found.\n\n"
                "Ensure src/gateway/config.py exists."
            )
            return

        try:
            config = _GatewayConfig.load()
        except Exception:
            config = _GatewayConfig()

        mc = getattr(config, 'meshcore', None)
        if mc is None:
            mc = _MeshCoreConfig()
            config.meshcore = mc

        while True:
            choices = [
                ("type", f"Connection Type     {mc.connection_type}"),
                ("device", f"Device Path         {mc.device_path}"),
                ("baud", f"Baud Rate           {mc.baud_rate}"),
                ("tcp_host", f"TCP Host            {mc.tcp_host or '(not set)'}"),
                ("tcp_port", f"TCP Port            {mc.tcp_port}"),
                ("channels", f"Bridge Channels     {'Yes' if mc.bridge_channels else 'No'}"),
                ("dms", f"Bridge DMs          {'Yes' if mc.bridge_dms else 'No'}"),
                ("sim", f"Simulation Mode     {'Yes' if mc.simulation_mode else 'No'}"),
                ("save", "Save Configuration"),
                ("back", "Back"),
            ]

            choice = self.dialog.menu(
                "MeshCore Configuration",
                "Configure MeshCore companion radio connection:",
                choices
            )

            if choice is None or choice == "back":
                break

            if choice == "type":
                type_choice = self.dialog.menu(
                    "Connection Type",
                    "How is the MeshCore radio connected?",
                    [
                        ("serial", "USB Serial          Direct USB connection"),
                        ("tcp", "TCP                 Network connection"),
                        ("ble", "Bluetooth LE        BLE connection"),
                    ]
                )
                if type_choice:
                    mc.connection_type = type_choice

            elif choice == "device":
                # Auto-detect and offer choices
                devices = []
                if _HAS_DETECT:
                    devices = _detect_meshcore_devices()

                if devices:
                    dev_choices = [(d, d) for d in devices]
                    dev_choices.append(("custom", "Enter custom path"))
                    selected = self.dialog.menu(
                        "Select Device",
                        "Detected serial devices:",
                        dev_choices
                    )
                    if selected and selected != "custom":
                        mc.device_path = selected
                    elif selected == "custom":
                        path = self.dialog.inputbox(
                            "Device Path",
                            "Enter serial device path:",
                            mc.device_path
                        )
                        if path:
                            mc.device_path = path
                else:
                    path = self.dialog.inputbox(
                        "Device Path",
                        "No devices detected. Enter path manually:",
                        mc.device_path
                    )
                    if path:
                        mc.device_path = path

            elif choice == "baud":
                baud = self.dialog.inputbox(
                    "Baud Rate",
                    "Enter baud rate (typically 115200):",
                    str(mc.baud_rate)
                )
                if baud:
                    try:
                        mc.baud_rate = int(baud)
                    except ValueError:
                        self.dialog.msgbox("Invalid Input", "Baud rate must be a number.")

            elif choice == "tcp_host":
                host = self.dialog.inputbox(
                    "TCP Host",
                    "Enter TCP host for MeshCore connection:",
                    mc.tcp_host or "localhost"
                )
                if host and self._validate_hostname(host):
                    mc.tcp_host = host
                elif host:
                    self.dialog.msgbox("Invalid Host", "Invalid hostname or IP address.")

            elif choice == "tcp_port":
                port = self.dialog.inputbox(
                    "TCP Port",
                    "Enter TCP port (default 4000):",
                    str(mc.tcp_port)
                )
                if port and self._validate_port(port):
                    mc.tcp_port = int(port)
                elif port:
                    self.dialog.msgbox("Invalid Port", "Port must be 1-65535.")

            elif choice == "channels":
                mc.bridge_channels = not mc.bridge_channels

            elif choice == "dms":
                mc.bridge_dms = not mc.bridge_dms

            elif choice == "sim":
                mc.simulation_mode = not mc.simulation_mode

            elif choice == "save":
                try:
                    config.save()
                    self.dialog.msgbox(
                        "Saved",
                        "MeshCore configuration saved.\n\n"
                        "Restart the gateway bridge for changes to take effect."
                    )
                except Exception as e:
                    self.dialog.msgbox("Save Error", f"Could not save config:\n\n{e}")

    def _meshcore_toggle(self):
        """Enable or disable MeshCore in gateway config."""
        if not _HAS_GW_CONFIG:
            self.dialog.msgbox(
                "Module Missing",
                "Gateway configuration module not found."
            )
            return

        try:
            config = _GatewayConfig.load()
        except Exception:
            config = _GatewayConfig()

        mc = getattr(config, 'meshcore', None)
        if mc is None:
            mc = _MeshCoreConfig()
            config.meshcore = mc

        mc.enabled = not mc.enabled
        action = "enabled" if mc.enabled else "disabled"

        try:
            config.save()
            self.dialog.msgbox(
                f"MeshCore {action.title()}",
                f"MeshCore is now {action}.\n\n"
                f"Restart the gateway bridge for changes to take effect."
            )
        except Exception as e:
            self.dialog.msgbox("Save Error", f"Could not save config:\n\n{e}")

    def _meshcore_nodes(self):
        """Show MeshCore nodes (if bridge is running)."""
        clear_screen()
        print("=== MeshCore Nodes ===\n")
        print("  MeshCore nodes are tracked in the unified node tracker")
        print("  when the gateway bridge is running with MeshCore enabled.\n")
        print("  To view nodes:")
        print("  1. Enable MeshCore in gateway config")
        print("  2. Start the gateway bridge")
        print("  3. Use Dashboard > Node Count to see all nodes")
        print("\n  MeshCore nodes are prefixed with 'meshcore:' in the tracker.")
        self._wait_for_enter()

    def _meshcore_stats(self):
        """Show MeshCore statistics."""
        clear_screen()
        print("=== MeshCore Statistics ===\n")

        if not _HAS_GW_CONFIG:
            print("  Gateway config not available.")
            self._wait_for_enter()
            return

        try:
            config = _GatewayConfig.load()
        except Exception:
            print("  Could not load gateway config.")
            self._wait_for_enter()
            return

        mc = getattr(config, 'meshcore', None)
        if not mc or not mc.enabled:
            print("  MeshCore is not enabled in gateway config.")
            print("  Enable it via 'Enable/Disable' option.")
            self._wait_for_enter()
            return

        print("  MeshCore statistics are available when the")
        print("  gateway bridge is running.\n")
        print("  Stats tracked:")
        print("  - meshcore_rx:  Messages received from MeshCore")
        print("  - meshcore_tx:  Messages sent to MeshCore")
        print("  - meshcore_acks: Delivery acknowledgments")
        print("  - Connection events (connect/disconnect/retry)")
        print("\n  Use Dashboard > Service Status to see live stats.")
        self._wait_for_enter()
