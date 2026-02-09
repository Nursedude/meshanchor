"""
Broker Management Mixin - Private MQTT broker setup and management for MeshForge TUI.

Provides:
- Broker profile selection (Private / Public / Custom)
- Mosquitto installation and configuration
- Meshtastic radio MQTT setup commands
- Broker service management (start/stop/status)

Architecture:
    Meshtastic Radio -> mosquitto (private broker) -> MeshForge MQTT subscriber
                                                   -> RNS Gateway bridge
                                                   -> Other MQTT consumers

This enables MeshForge to be its own private broker, solving the
Meshtastic <-> RNS communication gap via MQTT as the common transport.
"""

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

# Import broker profiles
try:
    from utils.broker_profiles import (
        BrokerProfile,
        BrokerType,
        create_private_profile,
        create_public_profile,
        create_custom_profile,
        load_profiles,
        save_profiles,
        set_active_profile,
        get_active_profile,
        ensure_default_profiles,
        generate_mosquitto_conf,
        generate_mosquitto_acl,
        install_mosquitto_config,
        check_mosquitto_installed,
        check_mosquitto_running,
        restart_mosquitto,
        enable_mosquitto_at_boot,
        get_meshtastic_mqtt_setup_commands,
    )
    _HAS_BROKER_PROFILES = True
except ImportError:
    _HAS_BROKER_PROFILES = False


class BrokerMixin:
    """Mixin providing MQTT broker management for the MeshForge TUI."""

    def _broker_menu(self):
        """MQTT Broker management menu."""
        if not _HAS_BROKER_PROFILES:
            self.dialog.msgbox(
                "Module Unavailable",
                "Broker profiles module not found.\n\n"
                "Ensure utils/broker_profiles.py exists."
            )
            return

        while True:
            # Get current state
            profiles = ensure_default_profiles()
            active = get_active_profile(profiles)
            active_name = active.display_name if active else "None"

            # Check mosquitto status
            installed, _ = check_mosquitto_installed()
            running, _ = check_mosquitto_running()

            mosquitto_status = "Not installed"
            if installed and running:
                mosquitto_status = "Running"
            elif installed:
                mosquitto_status = "Installed (stopped)"

            choices = [
                ("profiles", f"Broker Profiles     Active: {active_name[:20]}"),
                ("private", "Setup Private Broker  MeshForge mosquitto"),
                ("public", "Use Public Broker     mqtt.meshtastic.org"),
                ("custom", "Add Custom Broker     Your own server"),
                ("mosquitto", f"Mosquitto Service    {mosquitto_status}"),
                ("radio", "Radio MQTT Setup      Configure device uplink"),
                ("back", "Back"),
            ]

            choice = self.dialog.menu(
                "MQTT Broker Manager",
                "Manage MQTT broker for Meshtastic <-> RNS bridging.\n\n"
                "A private broker enables MeshForge as the central\n"
                "message hub between mesh networks.",
                choices
            )

            if choice is None or choice == "back":
                break

            dispatch = {
                "profiles": ("Broker Profiles", self._broker_profiles_menu),
                "private": ("Private Broker Setup", self._setup_private_broker),
                "public": ("Public Broker Setup", self._setup_public_broker),
                "custom": ("Custom Broker Setup", self._setup_custom_broker),
                "mosquitto": ("Mosquitto Service", self._mosquitto_service_menu),
                "radio": ("Radio MQTT Setup", self._radio_mqtt_setup),
            }
            entry = dispatch.get(choice)
            if entry:
                self._safe_call(*entry)

    def _broker_profiles_menu(self):
        """View and manage broker profiles."""
        profiles = ensure_default_profiles()

        while True:
            choices = []
            for name, profile in profiles.items():
                active_marker = " [ACTIVE]" if profile.is_active else ""
                ptype = profile.broker_type.upper()[:4]
                choices.append(
                    (name, f"[{ptype}] {profile.host}:{profile.port}{active_marker}")
                )
            choices.append(("back", "Back"))

            choice = self.dialog.menu(
                f"Broker Profiles ({len(profiles)})",
                "Select a profile to view/activate:",
                choices
            )

            if choice is None or choice == "back":
                break

            if choice in profiles:
                self._profile_detail_menu(choice, profiles)
                # Reload in case changes were made
                profiles = load_profiles()

    def _profile_detail_menu(self, name: str, profiles: dict):
        """Show profile details and management options."""
        profile = profiles[name]

        while True:
            lines = [
                f"Profile: {name}",
                f"Type: {profile.broker_type}",
                f"Host: {profile.host}:{profile.port}",
                f"Username: {profile.username or '(anonymous)'}",
                f"TLS: {'Yes' if profile.use_tls else 'No'}",
                f"Channel: {profile.channel}",
                f"Region: {profile.region}",
                f"Topic: {profile.topic_filter}",
                f"Active: {'Yes' if profile.is_active else 'No'}",
                "",
                profile.description,
            ]

            choices = [
                ("activate", "Set as Active Profile"),
                ("apply", "Apply to MQTT Subscriber"),
                ("radio", "Show Radio Setup Commands"),
            ]

            if profile.broker_type == BrokerType.PRIVATE.value:
                choices.append(("install", "Install Mosquitto Config"))
                choices.append(("conf", "View mosquitto.conf"))

            choices.extend([
                ("delete", "Delete Profile"),
                ("back", "Back"),
            ])

            choice = self.dialog.menu(
                f"Profile: {name}",
                "\n".join(lines),
                choices
            )

            if choice is None or choice == "back":
                break

            if choice == "activate":
                set_active_profile(name, profiles)
                profile.is_active = True
                self.dialog.msgbox("Activated", f"Profile '{name}' is now active.")

            elif choice == "apply":
                self._apply_profile_to_mqtt(profile)

            elif choice == "radio":
                cmds = get_meshtastic_mqtt_setup_commands(profile)
                self.dialog.msgbox(
                    "Radio MQTT Setup",
                    f"Run these commands to configure your radio:\n\n{cmds}",
                    width=70
                )

            elif choice == "install":
                self._install_broker_config(profile)

            elif choice == "conf":
                conf = generate_mosquitto_conf(profile)
                self.dialog.msgbox(
                    "mosquitto.conf Preview",
                    conf,
                    width=70
                )

            elif choice == "delete":
                if profile.is_active:
                    self.dialog.msgbox(
                        "Cannot Delete",
                        "Cannot delete the active profile.\n"
                        "Activate a different profile first."
                    )
                elif self.dialog.yesno("Confirm Delete", f"Delete profile '{name}'?"):
                    del profiles[name]
                    save_profiles(profiles)
                    self.dialog.msgbox("Deleted", f"Profile '{name}' deleted.")
                    break

    def _setup_private_broker(self):
        """Guided setup for MeshForge private broker."""
        # Check if mosquitto is installed
        installed, msg = check_mosquitto_installed()

        if not installed:
            self.dialog.msgbox(
                "Mosquitto Required",
                f"{msg}\n\n"
                "After installing, re-run this setup."
            )
            if self.dialog.yesno(
                "Install Now?",
                "Attempt to install mosquitto?\n\n"
                "This requires an internet connection."
            ):
                self._install_mosquitto()
            return

        # Gather configuration
        channel = self.dialog.inputbox(
            "Channel Name",
            "Meshtastic channel for this broker:\n\n"
            "  LongFast    (default Meshtastic)\n"
            "  ShortTurbo  (short range, high speed)\n"
            "  YourChannel (custom name)\n\n"
            "Must match your radio's primary channel.",
            init="LongFast"
        )
        if not channel:
            return

        region = self.dialog.inputbox(
            "Region",
            "Meshtastic region code:\n\n"
            "  US      (902-928 MHz)\n"
            "  EU_868  (863-870 MHz)\n"
            "  ANZ     (915-928 MHz)\n"
            "  Other supported regions as configured",
            init="US"
        )
        if not region:
            return

        username = self.dialog.inputbox(
            "MQTT Username",
            "Username for broker authentication:\n\n"
            "This is used by MeshForge and your gateway\n"
            "nodes to connect to the private broker.",
            init="meshforge"
        )
        if not username:
            return

        # Generate a password
        from utils.broker_profiles import generate_password
        default_pw = generate_password(12)

        password = self.dialog.inputbox(
            "MQTT Password",
            "Password for broker authentication:\n\n"
            "A random password has been generated.\n"
            "You can use it or enter your own.",
            init=default_pw
        )
        if not password:
            return

        # Create profile
        profile = create_private_profile(
            name="meshforge_private",
            channel=channel,
            region=region,
            username=username,
            password=password,
        )

        # Save profile
        profiles = load_profiles()
        # Deactivate others
        for p in profiles.values():
            p.is_active = False
        profile.is_active = True
        profiles["meshforge_private"] = profile
        save_profiles(profiles)

        # Offer to install mosquitto config
        if os.geteuid() == 0:
            if self.dialog.yesno(
                "Install Config",
                "Install mosquitto configuration now?\n\n"
                "This will create:\n"
                "  /etc/mosquitto/conf.d/meshforge.conf\n"
                "  /etc/mosquitto/meshforge_passwd\n"
                "  /etc/mosquitto/meshforge_acl\n\n"
                "And restart mosquitto."
            ):
                self._install_broker_config(profile)
        else:
            self.dialog.msgbox(
                "Manual Install Required",
                "Run MeshForge with sudo to install broker config,\n"
                "or manually create the mosquitto configuration.\n\n"
                "Use 'View mosquitto.conf' to see the template."
            )

        # Show radio setup commands
        cmds = get_meshtastic_mqtt_setup_commands(profile)
        self.dialog.msgbox(
            "Setup Complete",
            f"Private broker profile created and activated!\n\n"
            f"Broker: localhost:{profile.port}\n"
            f"User: {username}\n"
            f"Channel: {channel}\n"
            f"Region: {region}\n\n"
            f"Configure your Meshtastic radio:\n\n{cmds}",
            width=70
        )

        # Apply to MQTT subscriber config
        self._apply_profile_to_mqtt(profile)

    def _setup_public_broker(self):
        """Quick setup for Meshtastic public broker."""
        region = self.dialog.inputbox(
            "Region",
            "Meshtastic region:\n\n"
            "  US, EU_868, ANZ, etc.",
            init="US"
        )
        if not region:
            return

        channel = self.dialog.inputbox(
            "Channel",
            "Channel to monitor:\n\n"
            "  LongFast (default, highest traffic)",
            init="LongFast"
        )
        if not channel:
            return

        profile = create_public_profile(region=region, channel=channel)

        profiles = load_profiles()
        for p in profiles.values():
            p.is_active = False
        profile.is_active = True
        profiles["meshtastic_public"] = profile
        save_profiles(profiles)

        self.dialog.msgbox(
            "Public Broker Set",
            f"Configured for Meshtastic public broker.\n\n"
            f"Broker: mqtt.meshtastic.org:8883 (TLS)\n"
            f"Channel: {channel}\n"
            f"Region: {region}\n\n"
            "This is read-only nodeless monitoring.\n"
            "No local radio needed.\n\n"
            "Public broker enforces zero-hop policy\n"
            "(downlinked messages don't re-enter mesh)."
        )

        self._apply_profile_to_mqtt(profile)

    def _setup_custom_broker(self):
        """Guided setup for a custom MQTT broker."""
        name = self.dialog.inputbox(
            "Profile Name",
            "Name for this broker profile:",
            init="my_broker"
        )
        if not name:
            return

        host = self.dialog.inputbox(
            "Broker Host",
            "MQTT broker hostname or IP:\n\n"
            "Examples:\n"
            "  mqtt.example.com\n"
            "  192.168.1.100\n"
            "  gt.wildc.net",
        )
        if not host:
            return

        port = self.dialog.inputbox(
            "Port",
            "MQTT port:\n\n"
            "  1883 = Plain TCP\n"
            "  8883 = TLS encrypted",
            init="1883"
        )
        if not port or not port.isdigit():
            return

        username = self.dialog.inputbox(
            "Username",
            "MQTT username (blank for anonymous):",
        )

        password = ""
        if username:
            password = self.dialog.inputbox(
                "Password",
                "MQTT password:",
            ) or ""

        region = self.dialog.inputbox(
            "Region",
            "Meshtastic region code:",
            init="US"
        ) or "US"

        channel = self.dialog.inputbox(
            "Channel",
            "Meshtastic channel name:",
            init="LongFast"
        ) or "LongFast"

        root_topic = self.dialog.inputbox(
            "Root Topic",
            "MQTT root topic:\n\n"
            "  msh/{region}/2/e  (standard)\n"
            "  msh              (all regions)",
            init=f"msh/{region}/2/e"
        ) or f"msh/{region}/2/e"

        profile = create_custom_profile(
            name=name,
            host=host,
            port=int(port),
            username=username or "",
            password=password,
            use_tls=(int(port) == 8883),
            root_topic=root_topic,
            channel=channel,
            region=region,
        )

        profiles = load_profiles()
        for p in profiles.values():
            p.is_active = False
        profile.is_active = True
        profiles[name] = profile
        save_profiles(profiles)

        self.dialog.msgbox(
            "Custom Broker Saved",
            f"Profile '{name}' created and activated.\n\n"
            f"Broker: {host}:{port}\n"
            f"Channel: {channel}\n"
            f"Topic: {profile.topic_filter}"
        )

        self._apply_profile_to_mqtt(profile)

    def _mosquitto_service_menu(self):
        """Manage the local mosquitto service."""
        while True:
            installed, inst_msg = check_mosquitto_installed()
            running, run_msg = check_mosquitto_running()

            if not installed:
                choices = [
                    ("install", "Install Mosquitto"),
                    ("back", "Back"),
                ]
                subtitle = f"Status: {inst_msg}"
            else:
                status = "Running" if running else "Stopped"
                choices = [
                    ("status", f"Status: {status}"),
                    ("start", "Start Mosquitto"),
                    ("stop", "Stop Mosquitto"),
                    ("restart", "Restart Mosquitto"),
                    ("enable", "Enable at Boot"),
                    ("logs", "View Logs"),
                    ("test", "Test Connection"),
                    ("back", "Back"),
                ]
                subtitle = f"Mosquitto: {status}"

            choice = self.dialog.menu(
                "Mosquitto Service",
                subtitle,
                choices
            )

            if choice is None or choice == "back":
                break

            if choice == "install":
                self._install_mosquitto()

            elif choice == "status":
                self._show_mosquitto_status()

            elif choice == "start":
                self._mosquitto_action("start")

            elif choice == "stop":
                self._mosquitto_action("stop")

            elif choice == "restart":
                success, msg = restart_mosquitto()
                self.dialog.msgbox(
                    "Restart" if success else "Error",
                    msg
                )

            elif choice == "enable":
                success, msg = enable_mosquitto_at_boot()
                self.dialog.msgbox(
                    "Enabled" if success else "Error",
                    msg
                )

            elif choice == "logs":
                self._show_mosquitto_logs()

            elif choice == "test":
                self._test_mosquitto_connection()

    def _install_mosquitto(self):
        """Install mosquitto via apt."""
        if os.geteuid() != 0:
            self.dialog.msgbox(
                "Root Required",
                "Run MeshForge with sudo to install packages.\n\n"
                "Or install manually:\n"
                "  sudo apt install mosquitto mosquitto-clients"
            )
            return

        if not self.dialog.yesno(
            "Install Mosquitto",
            "Install mosquitto MQTT broker?\n\n"
            "This will run:\n"
            "  apt install -y mosquitto mosquitto-clients"
        ):
            return

        self.dialog.infobox("Installing", "Installing mosquitto...")

        import subprocess
        try:
            result = subprocess.run(
                ["apt", "install", "-y", "mosquitto", "mosquitto-clients"],
                capture_output=True, text=True, timeout=120
            )
            if result.returncode == 0:
                self.dialog.msgbox(
                    "Installed",
                    "Mosquitto installed successfully.\n\n"
                    "Use 'Setup Private Broker' to configure it."
                )
            else:
                self.dialog.msgbox(
                    "Install Failed",
                    f"apt install failed:\n{result.stderr[:500]}"
                )
        except subprocess.TimeoutExpired:
            self.dialog.msgbox("Timeout", "Installation timed out.")
        except Exception as e:
            self.dialog.msgbox("Error", f"Installation failed:\n{e}")

    def _install_broker_config(self, profile: BrokerProfile):
        """Install mosquitto config for a profile."""
        success, msg = install_mosquitto_config(profile)

        if success:
            self.dialog.msgbox("Config Installed", msg)

            # Offer to restart mosquitto
            if self.dialog.yesno(
                "Restart Mosquitto",
                "Restart mosquitto to apply the new configuration?"
            ):
                rsuccess, rmsg = restart_mosquitto()
                self.dialog.msgbox(
                    "Restarted" if rsuccess else "Error",
                    rmsg
                )
        else:
            self.dialog.msgbox("Error", msg)

    def _show_mosquitto_status(self):
        """Show detailed mosquitto status."""
        import subprocess

        lines = ["MOSQUITTO STATUS", "=" * 40, ""]

        # Check installed
        installed, inst_msg = check_mosquitto_installed()
        lines.append(f"Installed: {'Yes' if installed else 'No'}")

        if installed:
            # Check running
            running, run_msg = check_mosquitto_running()
            lines.append(f"Running: {'Yes' if running else 'No'}")

            # Get systemd status
            try:
                result = subprocess.run(
                    ["systemctl", "status", "mosquitto", "--no-pager", "-l"],
                    capture_output=True, text=True, timeout=10
                )
                # Show first few lines of status
                status_lines = result.stdout.strip().split('\n')[:8]
                lines.append("")
                lines.append("SYSTEMD STATUS:")
                lines.extend(f"  {l}" for l in status_lines)
            except (subprocess.TimeoutExpired, FileNotFoundError):
                pass

            # Check if MeshForge config is installed
            from pathlib import Path
            mf_conf = Path("/etc/mosquitto/conf.d/meshforge.conf")
            lines.append("")
            lines.append(f"MeshForge config: {'Installed' if mf_conf.exists() else 'Not installed'}")

        self.dialog.msgbox("Mosquitto Status", "\n".join(lines), width=60)

    def _mosquitto_action(self, action: str):
        """Start/stop mosquitto service."""
        if os.geteuid() != 0:
            self.dialog.msgbox(
                "Root Required",
                f"Run MeshForge with sudo to {action} services."
            )
            return

        import subprocess
        try:
            result = subprocess.run(
                ["systemctl", action, "mosquitto"],
                capture_output=True, text=True, timeout=30
            )
            if result.returncode == 0:
                self.dialog.msgbox(
                    action.title(),
                    f"Mosquitto {action}ed successfully."
                )
            else:
                self.dialog.msgbox(
                    "Error",
                    f"Failed to {action} mosquitto:\n{result.stderr.strip()}"
                )
        except subprocess.TimeoutExpired:
            self.dialog.msgbox("Timeout", f"Command timed out.")
        except FileNotFoundError:
            self.dialog.msgbox("Error", "systemctl not found.")

    def _show_mosquitto_logs(self):
        """Show recent mosquitto logs."""
        import subprocess
        try:
            result = subprocess.run(
                ["journalctl", "-u", "mosquitto", "-n", "30", "--no-pager"],
                capture_output=True, text=True, timeout=10
            )
            if result.stdout:
                self.dialog.msgbox(
                    "Mosquitto Logs (last 30 lines)",
                    result.stdout,
                    width=76
                )
            else:
                self.dialog.msgbox("No Logs", "No mosquitto log entries found.")
        except (subprocess.TimeoutExpired, FileNotFoundError):
            self.dialog.msgbox("Error", "Could not retrieve logs.")

    def _test_mosquitto_connection(self):
        """Test MQTT connection to the active broker."""
        profiles = load_profiles()
        active = get_active_profile(profiles)

        if not active:
            self.dialog.msgbox(
                "No Active Profile",
                "No broker profile is active.\n"
                "Set up a broker profile first."
            )
            return

        self.dialog.infobox("Testing", f"Connecting to {active.host}:{active.port}...")

        import subprocess
        try:
            # Use mosquitto_pub to test connection
            cmd = [
                "mosquitto_pub",
                "-h", active.host,
                "-p", str(active.port),
                "-t", "meshforge/test",
                "-m", "MeshForge connection test",
            ]

            if active.username:
                cmd.extend(["-u", active.username])
            if active.password:
                cmd.extend(["-P", active.password])

            result = subprocess.run(
                cmd,
                capture_output=True, text=True, timeout=10
            )

            if result.returncode == 0:
                self.dialog.msgbox(
                    "Connection OK",
                    f"Successfully connected to {active.host}:{active.port}\n\n"
                    f"Published test message to meshforge/test topic."
                )
            else:
                self.dialog.msgbox(
                    "Connection Failed",
                    f"Could not connect to {active.host}:{active.port}\n\n"
                    f"Error: {result.stderr.strip()}\n\n"
                    "Check:\n"
                    "  1. Mosquitto is running\n"
                    "  2. Credentials are correct\n"
                    "  3. Firewall allows port " + str(active.port)
                )
        except FileNotFoundError:
            self.dialog.msgbox(
                "Tool Missing",
                "mosquitto_pub not found.\n\n"
                "Install: sudo apt install mosquitto-clients"
            )
        except subprocess.TimeoutExpired:
            self.dialog.msgbox(
                "Connection Timeout",
                f"Connection to {active.host}:{active.port} timed out."
            )

    def _radio_mqtt_setup(self):
        """Show Meshtastic radio MQTT configuration commands."""
        profiles = load_profiles()
        active = get_active_profile(profiles)

        if not active:
            self.dialog.msgbox(
                "No Active Profile",
                "No broker profile is active.\n"
                "Set up a broker profile first."
            )
            return

        cmds = get_meshtastic_mqtt_setup_commands(active)

        lines = [
            "MESHTASTIC RADIO MQTT SETUP",
            "=" * 50,
            "",
            f"Active Profile: {active.display_name}",
            f"Broker: {active.host}:{active.port}",
            "",
            "Run these commands on each gateway node to connect",
            "the radio to your MQTT broker:",
            "",
            cmds,
            "",
            "IMPORTANT NOTES:",
            "- The radio connects to the broker directly via WiFi",
            "- Set mqtt.address to the broker's LAN IP (not localhost)",
            "- Enable uplink on channels you want to bridge",
            "- Enable downlink to receive MQTT messages on mesh",
            "",
            "For private brokers: do NOT use the default AQ== key",
            "on your mesh channel. Use a custom PSK for security.",
        ]

        self.dialog.msgbox(
            "Radio MQTT Setup",
            "\n".join(lines),
            width=70
        )

    def _apply_profile_to_mqtt(self, profile: BrokerProfile):
        """Apply a broker profile to the MQTT subscriber configuration.

        This updates the mqtt_nodeless.json config that the MQTT
        subscriber and TUI uses for connecting.
        """
        tui_config = profile.to_tui_config()

        # Preserve existing auto-start settings
        existing_config = self._load_mqtt_config()
        tui_config['auto_start'] = existing_config.get('auto_start', False)
        tui_config['auto_start_telemetry'] = existing_config.get('auto_start_telemetry', True)

        self._save_mqtt_config(tui_config)
        logger.info("Applied broker profile '%s' to MQTT subscriber config", profile.name)
