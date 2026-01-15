"""
NanoVNA Antenna Analyzer Plugin for MeshForge

Provides integration with NanoVNA and NanoVNA-H vector network analyzers
for real-time SWR, impedance, and frequency response measurements.
"""

import logging
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

# Try to import plugin base
try:
    from core.plugin_base import Plugin, PluginContext, PluginType
    HAS_PLUGIN_BASE = True
except ImportError:
    HAS_PLUGIN_BASE = False
    Plugin = object
    PluginContext = None
    PluginType = None

# Import local modules
from .nanovna_device import NanoVNADevice, SweepResult, HAS_SERIAL
from .nanovna_panel import NanoVNAPanel, HAS_GTK


class NanoVNAPlugin(Plugin):
    """NanoVNA antenna analyzer plugin.

    Provides:
    - Auto-detection of NanoVNA devices
    - Real-time frequency sweeps
    - SWR, impedance, and return loss measurements
    - GTK4 panel for visualization
    """

    def __init__(self):
        """Initialize plugin."""
        self._panel: Optional[NanoVNAPanel] = None
        self._device: Optional[NanoVNADevice] = None
        self._settings: Dict[str, Any] = {}
        self._context: Optional[PluginContext] = None

    def activate(self, context: PluginContext) -> None:
        """Activate the plugin.

        Args:
            context: Plugin context for registration and services.
        """
        logger.info("[NanoVNA] Activating plugin")
        self._context = context

        # Load settings from manifest defaults
        if context and hasattr(context, 'settings'):
            self._settings = context.settings or {}
        else:
            self._settings = {
                "frequency_start_mhz": 400,
                "frequency_stop_mhz": 500,
                "sweep_points": 101,
                "auto_refresh": True,
                "refresh_interval_ms": 2000,
            }

        # Register panel if GTK available
        if HAS_GTK and context:
            try:
                context.register_panel(
                    panel_id="nanovna_panel",
                    panel_class=NanoVNAPanel,
                    title="NanoVNA Analyzer",
                    icon="network-wired-symbolic",
                    settings=self._settings
                )
                logger.info("[NanoVNA] Panel registered")
            except Exception as e:
                logger.error(f"[NanoVNA] Failed to register panel: {e}")

        # Check dependencies
        if not HAS_SERIAL:
            logger.warning("[NanoVNA] pyserial not installed - device access disabled")

    def deactivate(self) -> None:
        """Deactivate the plugin."""
        logger.info("[NanoVNA] Deactivating plugin")

        # Save settings
        if self._panel:
            self._settings.update(self._panel.get_settings())

        # Cleanup panel
        if self._panel:
            self._panel.cleanup()
            self._panel = None

        # Disconnect device
        if self._device:
            self._device.disconnect()
            self._device = None

        self._context = None

    @property
    def is_available(self) -> bool:
        """Check if plugin can function."""
        return HAS_SERIAL

    def get_devices(self) -> list:
        """Get list of connected NanoVNA devices.

        Returns:
            List of serial port paths.
        """
        return NanoVNADevice.find_devices()

    def quick_sweep(self, start_mhz: float, stop_mhz: float,
                    points: int = 101) -> Optional[SweepResult]:
        """Perform a quick frequency sweep.

        Args:
            start_mhz: Start frequency in MHz.
            stop_mhz: Stop frequency in MHz.
            points: Number of measurement points.

        Returns:
            SweepResult or None if failed.
        """
        devices = self.get_devices()
        if not devices:
            logger.warning("[NanoVNA] No devices found")
            return None

        device = NanoVNADevice(port=devices[0])
        if not device.connect():
            return None

        try:
            result = device.sweep(
                start_hz=int(start_mhz * 1e6),
                stop_hz=int(stop_mhz * 1e6),
                points=points
            )
            return result
        finally:
            device.disconnect()


# Plugin factory function (for dynamic loading)
def create_plugin() -> NanoVNAPlugin:
    """Create plugin instance.

    Returns:
        NanoVNAPlugin instance.
    """
    return NanoVNAPlugin()


# Standalone usage
if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO)

    print("NanoVNA Plugin Test")
    print("=" * 40)

    # Check dependencies
    print(f"pyserial available: {HAS_SERIAL}")
    print(f"GTK4 available: {HAS_GTK}")

    if not HAS_SERIAL:
        print("\nInstall pyserial: pip install pyserial")
        sys.exit(1)

    # Scan for devices
    devices = NanoVNADevice.find_devices()
    print(f"\nFound devices: {devices}")

    if devices:
        print(f"\nConnecting to {devices[0]}...")
        device = NanoVNADevice(port=devices[0])

        if device.connect():
            print(f"Connected: {device.device_version}")

            # Do a test sweep
            print("\nPerforming sweep 430-450 MHz...")
            result = device.sweep(430_000_000, 450_000_000, 21)

            print(f"Got {len(result.points)} points")

            if result.points:
                min_swr, min_freq = result.min_swr
                print(f"Min SWR: {min_swr:.2f}:1 at {min_freq:.3f} MHz")

                print("\nFrequency (MHz)  SWR      Impedance")
                print("-" * 45)
                for point in result.points[:10]:
                    z = point.impedance
                    print(f"{point.frequency_mhz:>8.3f}       "
                          f"{point.swr:>5.2f}:1  "
                          f"{z.real:>6.1f} {'+' if z.imag >= 0 else '-'} "
                          f"j{abs(z.imag):.1f}")

            device.disconnect()
        else:
            print("Connection failed")
    else:
        print("\nNo NanoVNA devices found")
        print("Check connection and ensure device is recognized")
