"""
Meshtasticd Configuration Mixin for MeshForge Launcher TUI.

Handles all meshtasticd service configuration, radio presets,
hardware config, and channel management.
Extracted from main.py to reduce file size.
"""

import subprocess
import sys
from pathlib import Path

# Import centralized service checker - SINGLE SOURCE OF TRUTH
try:
    from utils.service_check import check_service, check_systemd_service, ServiceState
except ImportError:
    check_service = None
    check_systemd_service = None
    ServiceState = None


class MeshtasticdConfigMixin:
    """Mixin providing meshtasticd configuration methods for the launcher."""

    def _meshtasticd_menu(self):
        """Meshtasticd configuration menu."""
        while True:
            choices = [
                ("web", "Web Client (Full Config)"),
                ("status", "Service Status"),
                ("owner", "Set Owner/Node Name"),
                ("presets", "Radio Presets (LoRa)"),
                ("hardware", "Hardware Config"),
                ("channels", "Channel Config"),
                ("gateway", "Gateway Template"),
                ("edit", "Edit Config Files"),
                ("restart", "Restart Service"),
                ("back", "Back"),
            ]

            choice = self.dialog.menu(
                "Meshtasticd Config",
                "Configure meshtasticd radio daemon:",
                choices
            )

            if choice is None or choice == "back":
                break

            if choice == "web":
                self._show_web_client_info()
            elif choice == "status":
                self._meshtasticd_status()
            elif choice == "owner":
                self._set_owner_name()
            elif choice == "presets":
                self._radio_presets_menu()
            elif choice == "hardware":
                self._hardware_config_menu()
            elif choice == "channels":
                self._channel_config_menu()
            elif choice == "gateway":
                self._gateway_template_menu()
            elif choice == "edit":
                self._edit_config_menu()
            elif choice == "restart":
                self._restart_meshtasticd()

    def _show_web_client_info(self):
        """Show meshtasticd web client URL for full radio configuration."""
        import socket
        try:
            local_ip = socket.gethostbyname(socket.gethostname())
            if local_ip.startswith('127.'):
                # Fallback: get IP from interface
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.connect(("8.8.8.8", 80))
                local_ip = s.getsockname()[0]
                s.close()
        except Exception:
            local_ip = "YOUR_PI_IP"

        web_url = f"http://{local_ip}:4403"

        self.dialog.msgbox(
            "Meshtastic Web Client",
            f"Full radio configuration via browser:\n\n"
            f"  URL: {web_url}\n\n"
            f"Set these to join your mesh network:\n"
            f"  Config → LoRa → Region  (US, EU_868, etc.)\n"
            f"  Config → LoRa → Preset  (LONG_FAST, etc.)\n"
            f"  Config → Channels       (PSK, name)\n\n"
            f"The web client gives full access to all\n"
            f"meshtasticd settings, maps, and messaging."
        )

    def _meshtasticd_status(self):
        """Show meshtasticd service status."""
        self.dialog.infobox("Status", "Checking meshtasticd status...")

        try:
            # Use centralized service checker (SINGLE SOURCE OF TRUTH)
            if check_service is not None and check_systemd_service is not None:
                status = check_service('meshtasticd')
                is_running = status.available
                _, is_enabled = check_systemd_service('meshtasticd')
                output = status.message
            else:
                # Fallback if service_check not available
                result = subprocess.run(
                    ['systemctl', 'status', 'meshtasticd'],
                    capture_output=True,
                    text=True,
                    timeout=10
                )
                output = result.stdout
                is_running = "active (running)" in output
                is_enabled = subprocess.run(
                    ['systemctl', 'is-enabled', 'meshtasticd'],
                    capture_output=True, text=True, timeout=5
                ).returncode == 0

            # Get config file info
            config_path = Path('/etc/meshtasticd/config.yaml')
            config_exists = config_path.exists()

            # Check active configs
            config_d = Path('/etc/meshtasticd/config.d')
            active_configs = list(config_d.glob('*.yaml')) if config_d.exists() else []

            text = f"""Meshtasticd Service Status:

Service: {'RUNNING' if is_running else 'STOPPED'}
Enabled: {'Yes' if is_enabled else 'No'}

Config File: {config_path}
Config Exists: {'Yes' if config_exists else 'No'}

Active Hardware Configs: {len(active_configs)}"""

            for cfg in active_configs[:5]:
                text += f"\n  - {cfg.name}"

            if len(active_configs) > 5:
                text += f"\n  ... and {len(active_configs) - 5} more"

            self.dialog.msgbox("Meshtasticd Status", text)

        except Exception as e:
            self.dialog.msgbox("Error", f"Failed to get status:\n{e}")

    def _set_owner_name(self):
        """Set node owner name (long name and short name)."""
        self.dialog.infobox("Owner", "Getting current owner info...")

        try:
            sys.path.insert(0, str(self.src_dir))
            from commands import meshtastic as mesh_cmd

            # Try to get current owner info
            result = mesh_cmd.get_node_info()
            current_long = ""
            current_short = ""

            if result.success and result.raw:
                # Parse owner info from output
                for line in result.raw.split('\n'):
                    if 'longName' in line or 'long_name' in line:
                        parts = line.split(':')
                        if len(parts) > 1:
                            current_long = parts[1].strip().strip('"')
                    elif 'shortName' in line or 'short_name' in line:
                        parts = line.split(':')
                        if len(parts) > 1:
                            current_short = parts[1].strip().strip('"')

            # Show current info and get new values
            text = f"""Set your node's identity:

Current Long Name: {current_long or '(not set)'}
Current Short Name: {current_short or '(not set)'}

Long Name: Your node's full name (max 40 chars)
           Shown on other devices, maps, etc.

Short Name: 4-character abbreviation
           Shown in compact views

Press Cancel to keep current values."""

            # Get long name
            long_name = self.dialog.inputbox(
                "Set Long Name",
                f"Enter node name (current: {current_long or 'none'}):",
                current_long or ""
            )

            if long_name is None:  # Cancelled
                return

            # Get short name
            short_name = self.dialog.inputbox(
                "Set Short Name",
                f"Enter 4-char short name (current: {current_short or 'none'}):",
                current_short or ""
            )

            if short_name is None:  # Cancelled
                return

            # Validate
            if long_name:
                long_name = long_name[:40]
            if short_name:
                short_name = short_name[:4].upper()

            # Apply changes
            changes_made = []

            if long_name:
                self.dialog.infobox("Setting", f"Setting long name to: {long_name}")
                result = mesh_cmd.set_owner(long_name)
                if result.success:
                    changes_made.append(f"Long name: {long_name}")
                else:
                    self.dialog.msgbox("Error", f"Failed to set long name:\n{result.message}")
                    return

            if short_name:
                self.dialog.infobox("Setting", f"Setting short name to: {short_name}")
                result = mesh_cmd.set_owner_short(short_name)
                if result.success:
                    changes_made.append(f"Short name: {short_name}")
                else:
                    self.dialog.msgbox("Error", f"Failed to set short name:\n{result.message}")
                    return

            if changes_made:
                self.dialog.msgbox("Success", f"Owner settings updated:\n\n" + "\n".join(changes_made))
            else:
                self.dialog.msgbox("Info", "No changes made.")

        except Exception as e:
            self.dialog.msgbox("Error", f"Failed to set owner name:\n{e}")

    def _radio_presets_menu(self):
        """Radio/LoRa preset selection."""
        # Define modem presets with descriptions
        presets = [
            ("SHORT_TURBO", "500kHz SF7  - Max speed, <1km"),
            ("SHORT_FAST", "250kHz SF7  - Urban, 1-5km"),
            ("SHORT_SLOW", "125kHz SF7  - Reliable short"),
            ("MEDIUM_FAST", "250kHz SF10 - MtnMesh std, 5-20km"),
            ("MEDIUM_SLOW", "125kHz SF10 - Alt medium"),
            ("LONG_FAST", "250kHz SF11 - Default, 10-30km"),
            ("LONG_MODERATE", "125kHz SF11 - Extended, 15-40km"),
            ("LONG_SLOW", "125kHz SF12 - Max range, 20-50km"),
            ("back", "Back"),
        ]

        choice = self.dialog.menu(
            "Radio Presets",
            "Select LoRa modem preset:\n\n"
            "Higher speed = shorter range\n"
            "Lower speed = longer range",
            presets
        )

        if choice and choice != "back":
            self._apply_radio_preset(choice)

    def _apply_radio_preset(self, preset: str):
        """Apply a radio preset."""
        # Preset parameters
        preset_params = {
            "SHORT_TURBO": {"bw": 500, "sf": 7, "cr": 5},
            "SHORT_FAST": {"bw": 250, "sf": 7, "cr": 5},
            "SHORT_SLOW": {"bw": 125, "sf": 7, "cr": 8},
            "MEDIUM_FAST": {"bw": 250, "sf": 10, "cr": 5},
            "MEDIUM_SLOW": {"bw": 125, "sf": 10, "cr": 5},
            "LONG_FAST": {"bw": 250, "sf": 11, "cr": 5},
            "LONG_MODERATE": {"bw": 125, "sf": 11, "cr": 8},
            "LONG_SLOW": {"bw": 125, "sf": 12, "cr": 8},
        }

        params = preset_params.get(preset, {})
        if not params:
            return

        confirm = self.dialog.yesno(
            "Apply Preset",
            f"Apply {preset} preset?\n\n"
            f"Bandwidth: {params['bw']} kHz\n"
            f"Spreading Factor: SF{params['sf']}\n"
            f"Coding Rate: 4/{params['cr']}\n\n"
            "This will modify /etc/meshtasticd/config.yaml\n"
            "and restart the service.",
            default_no=True
        )

        if not confirm:
            return

        self.dialog.infobox("Applying", f"Applying {preset} preset...")

        try:
            config_path = Path('/etc/meshtasticd/config.yaml')

            if not config_path.exists():
                self.dialog.msgbox("Error", "Config file not found.\nRun installer first.")
                return

            # Read and modify config
            import yaml
            with open(config_path, 'r') as f:
                config = yaml.safe_load(f) or {}

            if 'Lora' not in config:
                config['Lora'] = {}

            config['Lora']['Bandwidth'] = params['bw']
            config['Lora']['SpreadFactor'] = params['sf']
            config['Lora']['CodingRate'] = params['cr']

            # Backup and write
            backup_path = config_path.with_suffix('.yaml.bak')
            if config_path.exists():
                import shutil
                shutil.copy(config_path, backup_path)

            with open(config_path, 'w') as f:
                yaml.dump(config, f, default_flow_style=False)

            # Restart service
            subprocess.run(['systemctl', 'restart', 'meshtasticd'],
                           capture_output=True, timeout=30)

            self.dialog.msgbox("Success",
                f"{preset} preset applied!\n\n"
                f"Config: {config_path}\n"
                f"Backup: {backup_path}\n\n"
                "Service restarted.")

        except Exception as e:
            self.dialog.msgbox("Error", f"Failed to apply preset:\n{e}")

    def _hardware_config_menu(self):
        """Hardware configuration selection."""
        available_dir = Path('/etc/meshtasticd/available.d')
        config_d = Path('/etc/meshtasticd/config.d')

        if not available_dir.exists():
            self.dialog.msgbox("Error",
                "Hardware templates not found.\n\n"
                f"Expected: {available_dir}\n\n"
                "Run the installer to set up templates.")
            return

        # List available hardware configs
        available = list(available_dir.glob('*.yaml'))
        if not available:
            self.dialog.msgbox("Error", "No hardware templates found.")
            return

        # Get currently active configs
        active = set()
        if config_d.exists():
            active = {f.name for f in config_d.glob('*.yaml')}

        choices = []
        for cfg in sorted(available):
            status = "[ACTIVE]" if cfg.name in active else ""
            # Truncate name for display
            name = cfg.stem[:25]
            choices.append((cfg.name, f"{name} {status}"))

        choices.append(("view", "View Config Details"))
        choices.append(("back", "Back"))

        choice = self.dialog.menu(
            "Hardware Config",
            "Select hardware configuration to activate:\n\n"
            f"Templates: {available_dir}\n"
            f"Active: {config_d}",
            choices
        )

        if choice is None or choice == "back":
            return
        elif choice == "view":
            self._view_hardware_config(available)
        else:
            self._activate_hardware_config(choice, available_dir, config_d)

    def _activate_hardware_config(self, config_name: str, available_dir: Path, config_d: Path):
        """Activate a hardware configuration."""
        src = available_dir / config_name

        if not src.exists():
            self.dialog.msgbox("Error", f"Config not found: {src}")
            return

        confirm = self.dialog.yesno(
            "Activate Config",
            f"Activate hardware config?\n\n"
            f"Template: {config_name}\n\n"
            "This will:\n"
            f"1. Copy to {config_d}/\n"
            "2. Restart meshtasticd service",
            default_no=True
        )

        if not confirm:
            return

        try:
            self.dialog.infobox("Activating", f"Activating {config_name}...")

            # Create config.d if needed
            config_d.mkdir(parents=True, exist_ok=True)

            # Copy config
            import shutil
            dst = config_d / config_name
            shutil.copy(src, dst)

            # Restart service
            subprocess.run(['systemctl', 'daemon-reload'],
                           capture_output=True, timeout=10)
            subprocess.run(['systemctl', 'restart', 'meshtasticd'],
                           capture_output=True, timeout=30)

            self.dialog.msgbox("Success",
                f"Hardware config activated!\n\n"
                f"Config: {dst}\n\n"
                "Service restarted.")

        except Exception as e:
            self.dialog.msgbox("Error", f"Activation failed:\n{e}")

    def _view_hardware_config(self, configs: list):
        """View details of a hardware config."""
        choices = [(cfg.name, cfg.stem[:30]) for cfg in sorted(configs)]
        choices.append(("back", "Back"))

        choice = self.dialog.menu(
            "View Config",
            "Select config to view:",
            choices
        )

        if choice and choice != "back":
            config_path = Path('/etc/meshtasticd/available.d') / choice
            if config_path.exists():
                try:
                    content = config_path.read_text()[:1500]
                    self.dialog.msgbox(f"Config: {choice}", content)
                except Exception as e:
                    self.dialog.msgbox("Error", str(e))

    def _edit_config_menu(self):
        """Edit config files directly."""
        choices = [
            ("main", "Main Config (/etc/meshtasticd/config.yaml)"),
            ("active", "Active Hardware Configs"),
            ("templates", "Hardware Templates"),
            ("back", "Back"),
        ]

        choice = self.dialog.menu(
            "Edit Config Files",
            "Edit meshtasticd configuration files:\n\n"
            "Opens in nano editor.\n"
            "Save: Ctrl+O, Exit: Ctrl+X",
            choices
        )

        if choice is None or choice == "back":
            return

        if choice == "main":
            self._edit_file('/etc/meshtasticd/config.yaml')
        elif choice == "active":
            self._edit_config_d()
        elif choice == "templates":
            self._edit_available_d()

    def _edit_file(self, path: str):
        """Edit a file with nano."""
        if not Path(path).exists():
            self.dialog.msgbox("Error", f"File not found:\n{path}")
            return

        # Clear screen and run nano
        subprocess.run(['clear'], check=False, timeout=5)
        subprocess.run(['nano', path])  # Interactive editor - no timeout

        # Ask to restart service
        if self.dialog.yesno(
            "Restart Service?",
            "Config file modified.\n\n"
            "Restart meshtasticd to apply changes?",
            default_no=False
        ):
            self._restart_meshtasticd()

    def _edit_config_d(self):
        """Edit files in config.d."""
        config_d = Path('/etc/meshtasticd/config.d')
        if not config_d.exists():
            self.dialog.msgbox("Error", f"Directory not found:\n{config_d}")
            return

        configs = list(config_d.glob('*.yaml'))
        if not configs:
            self.dialog.msgbox("Info", "No active configs in config.d/")
            return

        choices = [(str(cfg), cfg.name) for cfg in sorted(configs)]

        choice = self.dialog.menu(
            "Active Configs",
            "Select config to edit (Cancel to go back):",
            choices
        )

        if choice:
            self._edit_file(choice)

    def _edit_available_d(self):
        """Edit files in available.d."""
        available_d = Path('/etc/meshtasticd/available.d')
        if not available_d.exists():
            self.dialog.msgbox("Error", f"Directory not found:\n{available_d}")
            return

        configs = list(available_d.glob('*.yaml'))
        if not configs:
            self.dialog.msgbox("Info", "No templates in available.d/")
            return

        choices = [(str(cfg), cfg.name) for cfg in sorted(configs)]

        choice = self.dialog.menu(
            "Hardware Templates",
            "Select template to view (Cancel to go back):",
            choices
        )

        if choice:
            self._edit_file(choice)

    def _restart_meshtasticd(self):
        """Restart meshtasticd service."""
        confirm = self.dialog.yesno(
            "Restart Service",
            "Restart meshtasticd?\n\n"
            "This will:\n"
            "1. Reload systemd daemon\n"
            "2. Restart meshtasticd service\n"
            "3. Apply any config changes",
            default_no=True
        )

        if not confirm:
            return

        try:
            self.dialog.infobox("Restarting", "Restarting meshtasticd...")

            subprocess.run(['systemctl', 'daemon-reload'],
                           capture_output=True, timeout=10)

            result = subprocess.run(
                ['systemctl', 'restart', 'meshtasticd'],
                capture_output=True,
                text=True,
                timeout=30
            )

            if result.returncode == 0:
                self.dialog.msgbox("Success", "meshtasticd restarted successfully!")
            else:
                self.dialog.msgbox("Error",
                    f"Restart failed:\n{result.stderr or result.stdout}")

        except subprocess.TimeoutExpired:
            self.dialog.msgbox("Error", "Restart timed out")
        except Exception as e:
            self.dialog.msgbox("Error", f"Restart failed:\n{e}")
