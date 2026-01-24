"""
First-Run Wizard Mixin - Initial Setup Experience

Guides new users through MeshForge setup:
1. Hardware detection (USB devices)
2. Service status check
3. Basic configuration
4. Quick start guidance
"""

import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional

# Import path utilities
try:
    from utils.paths import get_real_user_home
except ImportError:
    def get_real_user_home() -> Path:
        sudo_user = os.environ.get('SUDO_USER', '')
        if sudo_user and sudo_user != 'root' and '/' not in sudo_user and '..' not in sudo_user:
            candidate = Path(f'/home/{sudo_user}')
            return candidate
        logname = os.environ.get('LOGNAME', '')
        if logname and logname != 'root' and '/' not in logname and '..' not in logname:
            candidate = Path(f'/home/{logname}')
            return candidate
        return Path('/root')

# Import service check
try:
    from utils.service_check import check_service
except ImportError:
    check_service = None

# Import device scanner
try:
    from utils.device_scanner import DeviceScanner
except ImportError:
    DeviceScanner = None


class FirstRunMixin:
    """Mixin for first-run wizard in launcher TUI"""

    FIRST_RUN_FLAG = ".meshforge_setup_complete"

    def _check_first_run(self) -> bool:
        """Check if this is a first run (no setup flag exists)"""
        config_dir = get_real_user_home() / ".config" / "meshforge"
        flag_file = config_dir / self.FIRST_RUN_FLAG
        return not flag_file.exists()

    def _mark_setup_complete(self):
        """Mark setup as complete"""
        config_dir = get_real_user_home() / ".config" / "meshforge"
        config_dir.mkdir(parents=True, exist_ok=True)
        flag_file = config_dir / self.FIRST_RUN_FLAG
        flag_file.touch()

    def _run_first_run_wizard(self) -> bool:
        """
        Run the first-run wizard.
        Returns True if completed, False if skipped.
        """
        # Welcome
        result = self.dialog.yesno(
            "Welcome to MeshForge!",
            "It looks like this is your first time running MeshForge.\n\n"
            "Would you like to run the setup wizard?\n\n"
            "The wizard will:\n"
            "• Detect connected hardware\n"
            "• Check service status\n"
            "• Help configure your mesh setup\n\n"
            "You can run this wizard again from Settings > Setup Wizard."
        )

        if not result:
            # User skipped - mark as complete anyway
            skip = self.dialog.yesno(
                "Skip Setup",
                "Skip the wizard and mark setup as complete?\n\n"
                "You can always run it later from Settings."
            )
            if skip:
                self._mark_setup_complete()
            return False

        # Step 1: Hardware Detection
        self._wizard_step_hardware()

        # Step 2: Service Status
        self._wizard_step_services()

        # Step 3: Quick Configuration
        self._wizard_step_config()

        # Step 4: Completion
        self._wizard_complete()

        return True

    def _wizard_step_hardware(self):
        """Wizard Step 1: Hardware Detection"""
        self.dialog.infobox("Step 1/4", "Detecting connected hardware...")

        lines = ["Hardware Detection\n"]
        lines.append("=" * 40)

        # Check for SPI devices (HAT-based radios like MeshAdv-Pi-Hat)
        spi_devices = list(Path('/dev').glob('spidev*'))
        is_raspberry_pi = self._is_raspberry_pi()

        if spi_devices:
            lines.append(f"\n✓ SPI Interface Available:")
            for spi in spi_devices[:3]:
                lines.append(f"  • {spi.name}")
            lines.append("  (Supports HAT radios: MeshAdv-Pi-Hat, Waveshare)")
        elif is_raspberry_pi:
            # No SPI but on Pi - offer to enable it
            lines.append("\n✗ SPI Interface Not Enabled")
            lines.append("  HAT radios require SPI to be enabled.")
            self.dialog.msgbox("Step 1: Hardware", "\n".join(lines))

            # Ask if they want to enable SPI
            if self._offer_enable_spi():
                # Re-check after enable
                spi_devices = list(Path('/dev').glob('spidev*'))
                if spi_devices:
                    self.dialog.msgbox(
                        "SPI Enabled",
                        "SPI has been enabled!\n\n"
                        "A REBOOT is required for changes to take effect.\n\n"
                        "After reboot, your HAT radio will be detected."
                    )
                    lines = ["Hardware Detection\n", "=" * 40]
                    lines.append("\n✓ SPI Enabled (reboot required)")

        if DeviceScanner is None:
            if not spi_devices:
                lines.append("\n✗ Device scanner not available")
                lines.append("\nConnect a Meshtastic device via USB")
                lines.append("or configure meshtasticd for HAT/SPI")
            self.dialog.msgbox("Step 1: Hardware", "\n".join(lines))
            return

        scanner = DeviceScanner()
        results = scanner.scan_all()

        if results['meshtastic_candidates']:
            lines.append(f"\n✓ Found {len(results['meshtastic_candidates'])} Meshtastic-compatible device(s):\n")
            for dev in results['meshtastic_candidates']:
                lines.append(f"  • {dev.description}")
        elif not spi_devices:
            lines.append("\n✗ No Meshtastic devices detected")
            lines.append("\nTo use MeshForge with a radio:")
            lines.append("  1. Connect a Meshtastic device via USB")
            lines.append("  2. Or configure meshtasticd for HAT/SPI")

        if results['serial_ports']:
            compat_ports = [p for p in results['serial_ports'] if p.meshtastic_compatible]
            if compat_ports:
                lines.append(f"\n✓ Serial Ports Available:")
                for port in compat_ports[:3]:  # Show first 3
                    lines.append(f"  • {port.device}")

        if results['recommended_port']:
            lines.append(f"\n→ Recommended port: {results['recommended_port']}")

        # Summary for new users
        if spi_devices or results.get('meshtastic_candidates'):
            lines.append("\n" + "-" * 40)
            lines.append("Hardware detected! Continue to configure.")
        else:
            lines.append("\n" + "-" * 40)
            lines.append("No radio found - you can still explore")
            lines.append("the interface and configure later.")

        self.dialog.msgbox("Step 1: Hardware", "\n".join(lines))

    def _is_raspberry_pi(self) -> bool:
        """Check if running on Raspberry Pi."""
        try:
            # Check /proc/cpuinfo for Raspberry Pi
            cpuinfo = Path('/proc/cpuinfo')
            if cpuinfo.exists():
                content = cpuinfo.read_text()
                if 'Raspberry Pi' in content or 'BCM' in content:
                    return True
            # Check device tree model
            model = Path('/proc/device-tree/model')
            if model.exists():
                if 'Raspberry Pi' in model.read_text():
                    return True
        except Exception:
            pass
        return False

    def _offer_enable_spi(self) -> bool:
        """Offer to enable SPI on Raspberry Pi. Returns True if enabled."""
        result = self.dialog.yesno(
            "Enable SPI?",
            "No SPI interface detected.\n\n"
            "HAT-based radios (MeshAdv-Pi-Hat, Waveshare, etc.)\n"
            "require SPI to be enabled.\n\n"
            "Would you like to enable SPI now?\n\n"
            "(Requires reboot to take effect)"
        )

        if not result:
            return False

        self.dialog.infobox("Enabling SPI", "Configuring SPI interface...")

        try:
            import subprocess

            # Find the boot config file
            boot_config = None
            for path in ['/boot/firmware/config.txt', '/boot/config.txt']:
                if Path(path).exists():
                    boot_config = path
                    break

            if not boot_config:
                self.dialog.msgbox("Error", "Could not find boot config file.")
                return False

            # Enable SPI using raspi-config if available
            raspi_config = shutil.which('raspi-config')
            if raspi_config:
                subprocess.run(
                    ['raspi-config', 'nonint', 'set_config_var', 'dtparam=spi', 'on', boot_config],
                    timeout=30,
                    check=False
                )

            # Add dtoverlay=spi0-0cs if not present (for HAT compatibility)
            config_content = Path(boot_config).read_text()
            if 'dtoverlay=spi0-0cs' not in config_content:
                # Find dtparam=spi=on line and add overlay after it
                lines = config_content.split('\n')
                new_lines = []
                added = False
                for line in lines:
                    new_lines.append(line)
                    if 'dtparam=spi=on' in line and not added:
                        new_lines.append('dtoverlay=spi0-0cs')
                        added = True

                # If dtparam=spi=on wasn't found, add both at the end
                if not added:
                    new_lines.append('dtparam=spi=on')
                    new_lines.append('dtoverlay=spi0-0cs')

                Path(boot_config).write_text('\n'.join(new_lines))

            return True

        except subprocess.TimeoutExpired:
            self.dialog.msgbox("Error", "Timeout while configuring SPI.")
            return False
        except Exception as e:
            self.dialog.msgbox("Error", f"Failed to enable SPI: {e}")
            return False

    def _wizard_step_services(self):
        """Wizard Step 2: Service Status"""
        self.dialog.infobox("Step 2/4", "Checking mesh services...")

        services = [
            ('meshtasticd', 'Meshtastic Daemon', 'Required for radio communication'),
            ('rnsd', 'Reticulum Network Stack', 'Optional - enables RNS mesh'),
        ]

        lines = ["Service Status\n"]
        lines.append("=" * 40)

        all_running = True
        for svc_id, svc_name, description in services:
            status = check_service(svc_id)
            if status.available:
                lines.append(f"\n✓ {svc_name}")
                lines.append(f"  Status: Running")
            else:
                all_running = False
                lines.append(f"\n✗ {svc_name}")
                lines.append(f"  Status: {status.message}")
                lines.append(f"  ({description})")
                if status.fix_hint:
                    lines.append(f"  Fix: {status.fix_hint}")

        if all_running:
            lines.append("\n" + "-" * 40)
            lines.append("All services are running!")
        else:
            lines.append("\n" + "-" * 40)
            lines.append("Some services need to be started.")
            lines.append("Use Service Manager from the main menu.")

        self.dialog.msgbox("Step 2: Services", "\n".join(lines))

    def _wizard_step_config(self):
        """Wizard Step 3: Quick Configuration"""
        # Check if basic config exists
        config_dir = get_real_user_home() / ".config" / "meshforge"
        settings_file = config_dir / "settings.json"

        if settings_file.exists():
            self.dialog.msgbox(
                "Step 3: Configuration",
                "Configuration file found.\n\n"
                "Your settings are preserved from a previous install.\n\n"
                "You can modify settings from:\n"
                "  Main Menu → Settings"
            )
            return

        # Offer basic setup
        result = self.dialog.yesno(
            "Step 3: Configuration",
            "Would you like to configure basic settings?\n\n"
            "This includes:\n"
            "• Callsign (for ham operators)\n"
            "• Default region\n"
            "• UI preferences"
        )

        if result:
            # Get callsign
            callsign = self.dialog.inputbox(
                "Callsign",
                "Enter your callsign (optional):",
                ""
            )

            if callsign:
                # Save to settings
                try:
                    from utils.common import SettingsManager
                    settings = SettingsManager()
                    settings.set("callsign", callsign.upper())
                    settings.save()
                    self.dialog.msgbox("Saved", f"Callsign set to: {callsign.upper()}")
                except Exception as e:
                    self.dialog.msgbox("Note", f"Could not save settings: {e}")

    def _wizard_complete(self):
        """Wizard completion"""
        self._mark_setup_complete()

        self.dialog.msgbox(
            "Setup Complete!",
            "MeshForge is ready to use!\n\n"
            "Next Steps:\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "1. Service Manager → Start meshtasticd\n"
            "2. Meshtasticd Config → Configure your radio\n"
            "3. Diagnostics → Verify everything works\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "\nNeed Help?\n"
            "  • Run diagnostics for system health\n"
            "  • Check GitHub issues for known fixes\n"
            "  • HAM community: 73s and good luck!\n\n"
            "Press Enter to continue to main menu."
        )

    def _settings_run_wizard(self):
        """Run wizard from settings menu"""
        result = self.dialog.yesno(
            "Run Setup Wizard",
            "Run the first-run setup wizard again?\n\n"
            "This will walk through hardware detection,\n"
            "service checks, and basic configuration."
        )

        if result:
            self._run_first_run_wizard()
