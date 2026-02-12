"""
Hardware Menu Mixin - Hardware detection and configuration handlers.

Extracted from main.py to reduce file size per CLAUDE.md guidelines.
"""

import logging
import shutil
import subprocess
from pathlib import Path
from backend import clear_screen

logger = logging.getLogger(__name__)


class HardwareMenuMixin:
    """Mixin providing hardware detection and configuration functionality."""

    def _hardware_menu(self):
        """Hardware detection and configuration menu."""
        while True:
            choices = [
                ("detect", "Detect Hardware"),
                ("spi", "Enable SPI (for HAT radios)"),
                ("back", "Back"),
            ]

            choice = self.dialog.menu(
                "Hardware",
                "Hardware detection and configuration:",
                choices
            )

            if choice is None or choice == "back":
                break

            dispatch = {
                "detect": ("Detect Hardware", self._detect_hardware),
                "spi": ("Enable SPI", self._enable_spi),
            }
            entry = dispatch.get(choice)
            if entry:
                self._safe_call(*entry)

    def _detect_hardware(self):
        """Run hardware detection - terminal-native."""
        clear_screen()
        print("=== Hardware Detection ===\n")

        # SPI
        spi_devices = list(Path('/dev').glob('spidev*'))
        if spi_devices:
            print(f"  \033[0;32m●\033[0m SPI: {', '.join(d.name for d in spi_devices)}")
        else:
            print(f"  \033[2m○\033[0m SPI: not enabled")

        # I2C
        i2c_devices = list(Path('/dev').glob('i2c-*'))
        if i2c_devices:
            print(f"  \033[0;32m●\033[0m I2C: {', '.join(d.name for d in i2c_devices)}")
        else:
            print(f"  \033[2m○\033[0m I2C: not enabled")

        # Serial/USB
        serial_ports = list(Path('/dev').glob('ttyUSB*')) + list(Path('/dev').glob('ttyACM*'))
        if serial_ports:
            print(f"  \033[0;32m●\033[0m Serial: {', '.join(d.name for d in serial_ports)}")
        else:
            print(f"  \033[2m○\033[0m Serial: no USB serial devices")

        # GPIO
        gpio_available = Path('/sys/class/gpio').exists()
        print(f"  {'●' if gpio_available else '○'} GPIO: {'available' if gpio_available else 'not available'}")

        # USB devices
        print("\nUSB Devices:")
        subprocess.run(['lsusb'], timeout=10)

        # meshtasticd config.d/
        print("\nmeshtasticd config.d/:")
        config_d = Path('/etc/meshtasticd/config.d')
        if config_d.exists():
            configs = list(config_d.glob('*.yaml'))
            if configs:
                for c in configs:
                    print(f"  {c.name}")
            else:
                print("  (empty)")
        else:
            print("  (not found)")

        self._wait_for_enter()

    def _enable_spi(self):
        """Enable SPI interface for HAT-based radios."""
        # Check if SPI is already enabled
        spi_devices = list(Path('/dev').glob('spidev*'))
        if spi_devices:
            self.dialog.msgbox(
                "SPI Status",
                "SPI is already enabled!\n\n"
                f"Devices: {', '.join(d.name for d in spi_devices)}\n\n"
                "Your HAT radio should be detected."
            )
            return

        # Check if on Raspberry Pi
        is_pi = self._is_raspberry_pi()
        if not is_pi:
            self.dialog.msgbox(
                "Not Raspberry Pi",
                "SPI auto-enable is only available on Raspberry Pi.\n\n"
                "For other systems, consult your board's documentation\n"
                "for enabling SPI interfaces."
            )
            return

        # Confirm enablement
        result = self.dialog.yesno(
            "Enable SPI",
            "This will enable the SPI interface for HAT radios.\n\n"
            "Supported HATs:\n"
            "  • MeshAdv-Pi-Hat\n"
            "  • Waveshare LoRa HAT\n"
            "  • Other SPI-based radios\n\n"
            "A REBOOT is required after enabling.\n\n"
            "Enable SPI now?"
        )

        if not result:
            return

        self.dialog.infobox("SPI", "Enabling SPI interface...")

        try:
            # Find boot config
            boot_config = None
            for path in ['/boot/firmware/config.txt', '/boot/config.txt']:
                if Path(path).exists():
                    boot_config = path
                    break

            if not boot_config:
                self.dialog.msgbox("Error", "Could not find boot config file.")
                return

            # Use raspi-config if available
            raspi_config = shutil.which('raspi-config')
            if raspi_config:
                subprocess.run(
                    ['raspi-config', 'nonint', 'set_config_var', 'dtparam=spi', 'on', boot_config],
                    timeout=30,
                    check=False
                )

            # Add dtoverlay for HAT compatibility
            config_content = Path(boot_config).read_text()
            needs_write = False
            lines = config_content.split('\n')
            new_lines = []
            added_overlay = False

            for line in lines:
                new_lines.append(line)
                # Add overlay after dtparam=spi=on
                if 'dtparam=spi=on' in line and 'dtoverlay=spi0-0cs' not in config_content:
                    new_lines.append('dtoverlay=spi0-0cs')
                    added_overlay = True
                    needs_write = True

            # If dtparam=spi=on wasn't found, add both
            if 'dtparam=spi=on' not in config_content:
                new_lines.append('dtparam=spi=on')
                new_lines.append('dtoverlay=spi0-0cs')
                needs_write = True

            if needs_write:
                Path(boot_config).write_text('\n'.join(new_lines))

            self.dialog.msgbox(
                "SPI Enabled",
                "SPI interface has been enabled!\n\n"
                "IMPORTANT: You must REBOOT for changes to take effect.\n\n"
                "After reboot:\n"
                "  1. Your HAT radio will be detected\n"
                "  2. Configure meshtasticd for SPI\n"
                "  3. Start meshtasticd service\n\n"
                "Reboot now with: sudo reboot"
            )

        except subprocess.TimeoutExpired:
            self.dialog.msgbox("Error", "Timeout while configuring SPI.")
        except Exception as e:
            self.dialog.msgbox("Error", f"Failed to enable SPI:\n{e}")

    def _is_raspberry_pi(self) -> bool:
        """Check if running on Raspberry Pi."""
        try:
            cpuinfo = Path('/proc/cpuinfo')
            if cpuinfo.exists():
                content = cpuinfo.read_text()
                if 'Raspberry Pi' in content or 'BCM' in content:
                    return True
            model = Path('/proc/device-tree/model')
            if model.exists():
                if 'Raspberry Pi' in model.read_text():
                    return True
        except OSError as e:
            logger.debug("RPi detection failed: %s", e)
        return False
