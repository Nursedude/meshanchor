"""
RNode Mixin — RNode device detection, info, and recommended configuration.

Wires commands/rnode.py (detect_rnode_devices, get_device_info, get_recommended_config)
to TUI menus. Extracted as a mixin to keep main.py under 1,500 lines.
"""

import logging
from backend import clear_screen
from utils.safe_import import safe_import

logger = logging.getLogger(__name__)

# Import RNode command functions
_detect_rnode_devices, _get_recommended_config, _HAS_RNODE = safe_import(
    'commands.rnode', 'detect_rnode_devices', 'get_recommended_config'
)


class RNodeMixin:
    """TUI mixin for RNode device management."""

    def _rnode_menu(self):
        """RNode Setup — detect devices, view info, get config."""
        while True:
            choices = [
                ("detect", "Detect Devices      Scan for RNode hardware"),
                ("probe", "Deep Scan           Detect + firmware probe"),
                ("config", "Recommended Config  RNS config for region"),
                ("back", "Back"),
            ]

            choice = self.dialog.menu(
                "RNode Setup",
                "RNode device detection and configuration:",
                choices
            )

            if choice is None or choice == "back":
                break

            dispatch = {
                "detect": ("Detect Devices", self._rnode_detect),
                "probe": ("Deep Scan", self._rnode_deep_scan),
                "config": ("Recommended Config", self._rnode_recommended_config),
            }
            entry = dispatch.get(choice)
            if entry:
                self._safe_call(*entry)

    def _rnode_detect(self):
        """Quick scan for RNode-compatible serial devices."""
        clear_screen()
        print("=== RNode Device Detection ===\n")

        if not _HAS_RNODE:
            print("  RNode module not available.")
            print("  File: src/commands/rnode.py")
            self._wait_for_enter()
            return

        result = _detect_rnode_devices(probe=False)

        if not result.success:
            print(f"  {result.message}")
            self._wait_for_enter()
            return

        data = result.data
        devices = data.get('devices', [])
        print(f"  Found {data.get('count', 0)} serial device(s)")
        print(f"  Likely RNode:  {data.get('rnode_count', 0)}")
        print(f"  In RNS config: {data.get('configured_count', 0)}\n")

        if devices:
            print(f"  {'Port':<16} {'Model':<22} {'RNode':>6} {'Config':>7}")
            print(f"  {'-'*54}")
            for dev in devices:
                is_rnode = "Yes" if dev.get('is_rnode') else ""
                is_cfg = "Yes" if dev.get('is_configured') else ""
                model = dev.get('model', 'Unknown')
                if len(model) > 20:
                    model = model[:18] + ".."
                print(f"  {dev.get('port', '?'):<16} {model:<22} {is_rnode:>6} {is_cfg:>7}")
        else:
            print("  No serial devices found.")
            print("  Check that your RNode is plugged in via USB.")

        print()
        self._wait_for_enter()

    def _rnode_deep_scan(self):
        """Deep scan with firmware probing (slower but more accurate)."""
        clear_screen()
        print("=== RNode Deep Scan (Firmware Probe) ===\n")
        print("  Probing serial ports for RNode firmware...\n")

        if not _HAS_RNODE:
            print("  RNode module not available.")
            self._wait_for_enter()
            return

        result = _detect_rnode_devices(probe=True)

        if not result.success:
            print(f"  {result.message}")
            self._wait_for_enter()
            return

        data = result.data
        devices = data.get('devices', [])
        print(f"  Found {data.get('count', 0)} serial device(s)")
        print(f"  Confirmed RNode: {data.get('rnode_count', 0)}")
        print(f"  In RNS config:   {data.get('configured_count', 0)}\n")

        if devices:
            print(f"  {'Port':<16} {'Model':<20} {'Firmware':<12} {'Config':>7}")
            print(f"  {'-'*58}")
            for dev in devices:
                is_cfg = "Yes" if dev.get('is_configured') else ""
                fw = dev.get('firmware_version', '')
                model = dev.get('model', 'Unknown')
                if len(model) > 18:
                    model = model[:16] + ".."
                print(f"  {dev.get('port', '?'):<16} {model:<20} {fw:<12} {is_cfg:>7}")

                # Show RNode confirmation
                if dev.get('is_rnode'):
                    print(f"  {'':>16} \033[0;32mConfirmed RNode firmware\033[0m")
        else:
            print("  No serial devices found.")

        print()
        self._wait_for_enter()

    def _rnode_recommended_config(self):
        """Generate recommended RNS config for a detected device."""
        clear_screen()
        print("=== RNode Recommended Configuration ===\n")

        try:
            from commands.rnode import detect_rnode_devices, get_recommended_config
        except ImportError:
            print("  RNode module not available.")
            self._wait_for_enter()
            return

        # First detect devices to let user pick one
        detect_result = detect_rnode_devices(probe=False)
        devices = detect_result.data.get('devices', []) if detect_result.success else []

        if not devices:
            print("  No serial devices found.")
            print("  Plug in an RNode device and try again.")
            self._wait_for_enter()
            return

        # Build port selection menu
        port_choices = []
        for dev in devices:
            model = dev.get('model', 'Unknown')
            if len(model) > 20:
                model = model[:18] + ".."
            label = f"{dev['port']:<16} {model}"
            port_choices.append((dev['port'], label))
        port_choices.append(("back", "Back"))

        port = self.dialog.menu(
            "Select Device",
            "Choose a device for configuration:",
            port_choices
        )

        if not port or port == "back":
            return

        # Region selection
        region_choices = [
            ("US", "US              FCC 902-928 MHz"),
            ("EU", "EU              ETSI 863-870 MHz"),
            ("AU", "AU              ACMA 915-928 MHz"),
        ]

        region = self.dialog.menu(
            "Select Region",
            "Choose regulatory region:",
            region_choices
        )

        if not region:
            return

        result = get_recommended_config(port, region)

        if not result.success:
            print(f"  {result.message}")
            self._wait_for_enter()
            return

        clear_screen()
        print(f"=== Recommended Config: {port} ({region}) ===\n")

        config = result.data.get('config', {})
        print(f"  Port:             {config.get('port', port)}")
        print(f"  Region:           {config.get('region', region)}")
        print(f"  Frequency:        {config.get('frequency', 0) / 1e6:.3f} MHz")
        print(f"  Bandwidth:        {config.get('bandwidth', 0) / 1e3:.0f} kHz")
        print(f"  Spreading Factor: {config.get('spreading_factor', '?')}")
        print(f"  Coding Rate:      {config.get('coding_rate', '?')}")
        print(f"  TX Power:         {config.get('tx_power', '?')} dBm")

        snippet = result.data.get('snippet', '')
        if snippet:
            print(f"\n  Config snippet for ~/.reticulum/config:\n")
            for line in snippet.splitlines():
                print(f"    {line}")

        print()
        self._wait_for_enter()
