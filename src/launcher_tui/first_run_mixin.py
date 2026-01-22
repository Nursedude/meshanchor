"""
First-Run Wizard Mixin - Initial Setup Experience

Guides new users through MeshForge setup:
1. Hardware detection (USB devices)
2. Service status check
3. Basic configuration
4. Quick start guidance
"""

import os
import subprocess
from pathlib import Path
from typing import Optional

# Import path utilities
try:
    from utils.paths import get_real_user_home
except ImportError:
    from src.utils.paths import get_real_user_home

# Import service check
try:
    from utils.service_check import check_service
except ImportError:
    from src.utils.service_check import check_service

# Import device scanner
try:
    from utils.device_scanner import DeviceScanner
except ImportError:
    try:
        from src.utils.device_scanner import DeviceScanner
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

        if DeviceScanner is None:
            self.dialog.msgbox(
                "Hardware Detection",
                "Device scanner not available.\n\n"
                "Connect your Meshtastic device via USB\n"
                "and ensure drivers are loaded."
            )
            return

        scanner = DeviceScanner()
        results = scanner.scan_all()

        lines = ["Hardware Detection\n"]
        lines.append("=" * 40)

        if results['meshtastic_candidates']:
            lines.append(f"\n✓ Found {len(results['meshtastic_candidates'])} Meshtastic-compatible device(s):\n")
            for dev in results['meshtastic_candidates']:
                lines.append(f"  • {dev.description}")
        else:
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

        self.dialog.msgbox("Step 1: Hardware", "\n".join(lines))

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
            "Quick Start:\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "• Diagnostics → Check system health\n"
            "• Service Manager → Start/stop services\n"
            "• Radio Config → Configure Meshtastic\n"
            "• Network Tools → Test connectivity\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
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
