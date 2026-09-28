"""
Radio Menu Handler — Meshtastic radio control via CLI.

Converted from radio_menu_mixin.py as part of the mixin-to-registry migration.
"""

import logging
import re
import os
import shutil
import subprocess
import sys
from pathlib import Path

from backend import clear_screen
from handler_protocol import BaseHandler
from utils.paths import get_real_user_home
from utils.tx_guard import DEFAULT_MESH_TCP_PORT, assert_tx_allowed

logger = logging.getLogger(__name__)


class RadioMenuHandler(BaseHandler):
    """TUI handler for Meshtastic radio control."""

    handler_id = "radio_menu"
    menu_section = "mesh_networks"

    def menu_items(self):
        return [
            ("meshtastic", "Meshtastic          Radio, channels, CLI", "meshtastic"),
        ]

    def execute(self, action):
        if action == "meshtastic":
            self._radio_menu()

    def _radio_menu(self):
        """Radio tools using meshtastic CLI directly."""
        while True:
            cli_path = self.ctx.get_meshtastic_cli()
            has_cli = cli_path != 'meshtastic'

            cli_works = False
            cli_location = ""
            if has_cli:
                cli_location = cli_path
                try:
                    result = subprocess.run(
                        [cli_path, '--version'],
                        capture_output=True, timeout=5,
                        stdin=subprocess.DEVNULL,
                    )
                    cli_works = result.returncode == 0
                except (subprocess.TimeoutExpired, FileNotFoundError, PermissionError):
                    cli_works = False

            choices = []
            if not cli_works:
                choices.append(("install-cli", "** Install meshtastic CLI **"))

            # --- Radio Config ---
            choices.append(("_cfg_", "--- Radio Config ---"))
            choices.extend([
                ("hw-config", "Hardware HAT Help"),
                ("presets", "Radio Presets (LoRa)"),
                ("set-region", "Set Region"),
                ("set-txpower", "Set TX Power"),
                ("set-name", "Set Node Name"),
            ])

            # --- Radio Info ---
            choices.append(("_info_", "--- Radio Info ---"))
            choices.extend([
                ("info", "Radio Info"),
                ("nodes", "Node List"),
                ("favorites", "Favorites (BaseUI 2.7+)"),
                ("channels", "Channel Info"),
                ("webui", "Open Web UI (:9443)"),
            ])

            # --- Radio Control ---
            choices.append(("_ctrl_", "--- Radio Control ---"))
            choices.extend([
                ("send", "Send Message"),
                ("position", "Position (view/set)"),
                ("reboot", "Reboot Radio"),
                ("reinstall-cli", "Reinstall/Update CLI"),
                ("back", "Back"),
            ])

            if cli_works:
                status = f" [CLI: {cli_location}]"
            elif has_cli:
                status = f" [CLI found but not working: {cli_location}]"
            else:
                status = " [CLI not installed]"

            choice = self.ctx.dialog.menu(
                "Radio Tools",
                f"Meshtastic radio control:{status}",
                choices
            )

            if choice is None or choice == "back":
                if choice is None:
                    logger.debug(
                        "Radio menu returned None (cancel or render failure), items=%d",
                        len(choices),
                    )
                break

            # Section headers — just re-display menu
            if choice.startswith("_") and choice.endswith("_"):
                continue

            if choice in ("install-cli", "reinstall-cli"):
                self.ctx.safe_call("Install CLI", self._install_meshtastic_cli)
                continue

            if choice == "favorites":
                # Delegate to FavoritesHandler
                self.ctx.safe_call("Favorites", self._favorites_submenu)
                continue

            dispatch = {
                "presets": ("Radio Presets", self._radio_preset_picker),
                "hw-config": ("Hardware HAT Help", self._radio_hat_help),
                "webui": ("Web UI", self._radio_open_webui),
                "position": ("Position", self._radio_position_menu),
                "send": ("Send Message", self._radio_send_message),
                "set-region": ("Set Region", self._radio_set_region),
                "set-txpower": ("Set TX Power", self._radio_set_tx_power),
                "set-name": ("Set Node Name", self._radio_set_name),
                "reboot": ("Reboot Radio", self._radio_reboot),
            }
            entry = dispatch.get(choice)
            if entry:
                self.ctx.safe_call(*entry)
                continue

            # CLI commands that build args dynamically
            try:
                cli = self.ctx.get_meshtastic_cli()
                conn_args = ['--host', 'localhost']
                if choice == "info":
                    self._radio_run([cli] + conn_args + ['--info'], "Radio Info")
                elif choice == "nodes":
                    self._radio_run([cli] + conn_args + ['--nodes'], "Node List")
                elif choice == "channels":
                    self._radio_run([cli] + conn_args + ['--ch-index', '0', '--ch-getall'], "Channels")
                else:
                    # Hybrid dispatch — see NetworkToolsHandler._network_menu.
                    self.ctx.notify_unwired(choice, "RadioMenuHandler._radio_menu")
            except KeyboardInterrupt:
                pass
            except Exception as e:
                self.ctx.dialog.msgbox(
                    "Radio Error",
                    f"Operation failed:\n{type(e).__name__}: {e}\n\n"
                    f"Check that meshtasticd is running:\n"
                    f"  sudo systemctl status meshtasticd"
                )

    def _radio_preset_picker(self):
        """Pick a Meshtastic LoRa modem preset and apply via CLI.

        Replaces the prior MN-1b dead-end that delegated to a
        ``meshtasticd_radio`` sub-handler that doesn't exist in MeshAnchor
        (Meshtasticd config editors are not ported per the MN-1b scope
        decision). Drives ``meshtastic --set lora.modem_preset`` directly
        with descriptions sourced from ``utils.lora_presets``.
        """
        from utils.lora_presets import MESHTASTIC_PRESETS

        choices = []
        for name, spec in MESHTASTIC_PRESETS.items():
            tag = "*" if spec.get("recommended") else " "
            speed = spec.get("estimated_throughput", "")
            rng = spec.get("estimated_range", "")
            label = f"{tag} {name:<14} {rng:<8} {speed}"
            choices.append((name, label))
        choices.append(("back", "Back"))

        choice = self.ctx.dialog.menu(
            "LoRa Modem Preset",
            "Select a Meshtastic preset to apply:\n\n"
            "* = recommended for general use (MEDIUM_FAST)\n"
            "Faster preset = shorter range, more bandwidth.\n"
            "Slower preset = longer range, less bandwidth.",
            choices,
        )

        if choice is None or choice == "back":
            return

        spec = MESHTASTIC_PRESETS.get(choice, {})
        warning = spec.get("warning")
        body = (
            f"Apply preset {choice}?\n\n"
            f"Description: {spec.get('description', '')}\n"
            f"Range:       {spec.get('estimated_range', 'unknown')}\n"
            f"Throughput:  {spec.get('estimated_throughput', 'unknown')}\n"
        )
        if warning:
            body += f"\nWARNING: {warning}\n"
        body += "\nRadio will restart after the change."

        if not self.ctx.dialog.yesno(f"Apply {choice}?", body, default_no=True):
            return

        self._radio_run(
            [self.ctx.get_meshtastic_cli(), '--host', 'localhost',
             '--set', 'lora.modem_preset', choice],
            f"Setting preset: {choice}",
        )

    def _radio_hat_help(self):
        """Explain the Meshtasticd HAT-selection process.

        Replaces the prior MN-1b dead-end that delegated to a
        ``meshtasticd_radio`` sub-handler that doesn't exist in MeshAnchor.
        MeshAnchor does NOT ship a HAT picker (per Issue #22 — never
        overwrite ``config.yaml``, never auto-create files in
        ``available.d/``); this stub points the operator at the canonical
        process documented by meshtasticd upstream.
        """
        self.ctx.dialog.msgbox(
            "Hardware HAT Selection",
            "MeshAnchor does NOT manage meshtasticd HAT selection — that\n"
            "lives with the meshtasticd package itself (see Issue #22 for\n"
            "the rationale: never overwrite /etc/meshtasticd/config.yaml).\n\n"
            "To select a HAT manually:\n"
            "  1. ls /etc/meshtasticd/available.d/\n"
            "  2. sudo cp /etc/meshtasticd/available.d/<your-hat>.yaml \\\n"
            "       /etc/meshtasticd/config.d/\n"
            "  3. sudo systemctl restart meshtasticd\n\n"
            "Or use the meshtasticd web UI at http://localhost:9443 —\n"
            "it has a HAT picker under Settings > Module > Serial.\n"
            "(Use 'Open Web UI' from the Radio Info menu.)"
        )

    def _radio_open_webui(self):
        """Surface the meshtasticd web UI URL.

        On a graphical session (``$DISPLAY`` set), offer to xdg-open the
        URL. Headless: print the URL plus an SSH-tunnel hint so the
        operator on the remote workstation can paste it into a browser.
        """
        url = "http://localhost:9443"
        has_display = bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))

        if has_display and shutil.which("xdg-open"):
            if self.ctx.dialog.yesno(
                "Open Meshtasticd Web UI?",
                f"Open {url} in the default browser?\n\n"
                "(For HAT selection, channel admin, MQTT, and most\n"
                "settings the web UI is the easier path.)",
                default_no=False,
            ):
                try:
                    subprocess.Popen(
                        ["xdg-open", url],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        stdin=subprocess.DEVNULL,
                    )
                    self.ctx.dialog.msgbox(
                        "Opening",
                        f"Launched browser to {url}.\n\n"
                        "If nothing opens, paste the URL manually."
                    )
                except OSError as e:
                    self.ctx.dialog.msgbox(
                        "Open Failed",
                        f"xdg-open failed: {e}\n\nURL: {url}"
                    )
            return

        # Headless path
        self.ctx.dialog.msgbox(
            "Meshtasticd Web UI",
            f"Web UI URL:\n  {url}\n\n"
            "This Pi is headless — paste the URL on a workstation that\n"
            "can reach it. If the Pi isn't directly reachable, tunnel:\n\n"
            "  ssh -L 9443:localhost:9443 <pi-host>\n\n"
            "...then open http://localhost:9443 in the workstation's\n"
            "browser. The web UI handles HAT selection, channel admin,\n"
            "MQTT, position, and most other settings."
        )

    def _favorites_submenu(self):
        """Delegate to FavoritesHandler."""
        from handlers.favorites import FavoritesHandler
        handler = FavoritesHandler()
        handler.set_context(self.ctx)
        handler._favorites_menu()

    def _radio_run(self, cmd: list, title: str):
        """Run a meshtastic CLI command and show output in terminal."""
        clear_screen()
        print(f"=== {title} ===")
        print("(Ctrl+C to abort)\n")
        try:
            result = subprocess.run(cmd, timeout=30)
            if result.returncode != 0:
                print(f"\nCommand failed (exit {result.returncode})")
                print("Is meshtasticd running? Check: systemctl status meshtasticd")
        except FileNotFoundError:
            self._offer_install_meshtastic_cli()
            return
        except subprocess.TimeoutExpired:
            print("\n\nCommand timed out (30s). Radio may not be connected.")
            print("Check: systemctl status meshtasticd")
        except KeyboardInterrupt:
            print("\n\nAborted.")
        try:
            self.ctx.wait_for_enter()
        except KeyboardInterrupt:
            print()

    def _offer_install_meshtastic_cli(self):
        """Offer to install meshtastic CLI when missing."""
        install = self.ctx.dialog.yesno(
            "Meshtastic CLI Not Found",
            "The 'meshtastic' CLI is not installed.\n\n"
            "This is needed to configure the radio\n"
            "(set presets, region, node name, etc.).\n\n"
            "Install meshtastic CLI now?",
            default_no=False
        )
        if install:
            self._install_meshtastic_cli()

    def _install_meshtastic_cli(self):
        """Install meshtastic CLI via pipx with live terminal output."""
        clear_screen()
        print("=== Installing Meshtastic CLI ===\n")

        sudo_user = os.environ.get('SUDO_USER')
        run_as_user = sudo_user if sudo_user and sudo_user != 'root' else None

        try:
            if not shutil.which('pipx'):
                print("Installing pipx...\n")
                result = subprocess.run(
                    ['apt-get', 'install', '-y', 'pipx'],
                    timeout=60
                )
                if result.returncode != 0:
                    print("\nFailed to install pipx.")
                    print("Try manually: sudo apt install pipx")
                    self.ctx.wait_for_enter()
                    return

            def run_pipx_cmd(args, timeout_sec=300):
                if run_as_user:
                    cmd = ['sudo', '-i', '-u', run_as_user] + args
                else:
                    cmd = args
                return subprocess.run(cmd, timeout=timeout_sec)

            print("Ensuring pipx paths...\n")
            run_pipx_cmd(['pipx', 'ensurepath'], timeout_sec=15)

            for bindir in [
                get_real_user_home() / '.local' / 'bin',
                Path('/root/.local/bin'),
                Path('/usr/local/bin'),
            ]:
                if bindir.is_dir() and str(bindir) not in os.environ.get('PATH', ''):
                    os.environ['PATH'] = f"{bindir}:{os.environ.get('PATH', '')}"

            if run_as_user:
                print(f"\nInstalling meshtastic CLI via pipx (as {run_as_user})...\n")
            else:
                print("\nInstalling meshtastic CLI via pipx...\n")
            result = run_pipx_cmd(['pipx', 'install', 'meshtastic[cli]', '--force'])

            if result.returncode != 0:
                print("\nRetrying without [cli] extras...\n")
                result = run_pipx_cmd(['pipx', 'install', 'meshtastic', '--force'])

            if result.returncode == 0:
                # Clear cached path so it gets re-resolved
                self.ctx._meshtastic_path = None
                cli_path = self.ctx.get_meshtastic_cli()
                if cli_path and cli_path != 'meshtastic':
                    print(f"\n** meshtastic CLI installed: {cli_path} **")
                else:
                    print("\n** meshtastic installed but not found in PATH **")
                    print("You may need to log out and back in,")
                    print("or run: eval \"$(pipx ensurepath)\"")
            else:
                print("\nInstallation failed.")
                print("Try manually: pipx install meshtastic")

        except FileNotFoundError:
            print("pipx not found.")
            print("Try: sudo apt install pipx && pipx install meshtastic")
        except KeyboardInterrupt:
            print("\n\nInstallation cancelled.")
        except subprocess.TimeoutExpired:
            print("\n\nInstallation timed out.")
            print("Try manually: pipx install meshtastic")
        except Exception as e:
            print(f"\nInstallation error: {e}")
            print("Try manually: pipx install meshtastic")

        try:
            self.ctx.wait_for_enter()
        except KeyboardInterrupt:
            print()

    def _radio_send_message(self):
        """Send a mesh message via meshtastic CLI."""
        msg = self.ctx.dialog.inputbox(
            "Send Message",
            "Message text (broadcast to default channel):",
            ""
        )
        if not msg:
            return

        dest = self.ctx.dialog.inputbox(
            "Destination",
            "Node ID (e.g. !abc12345)\nLeave empty for broadcast:",
            ""
        )

        assert_tx_allowed('localhost', DEFAULT_MESH_TCP_PORT,
                          kind="meshtastic_cli", detail="radio_menu send message")
        cmd = [self.ctx.get_meshtastic_cli(), '--host', 'localhost', '--sendtext', msg]
        if dest and dest.strip():
            dest = dest.strip()
            if not dest.startswith('!'):
                dest = '!' + dest
            cmd.extend(['--dest', dest])

        self._radio_run(cmd, "Sending Message")

    def _radio_set_region(self):
        """Set LoRa region via meshtastic CLI."""
        choices = [
            ("US", "US (902-928 MHz)"),
            ("EU_868", "EU_868 (863-870 MHz)"),
            ("CN", "CN (470-510 MHz)"),
            ("JP", "JP (920-925 MHz)"),
            ("ANZ", "ANZ (915-928 MHz)"),
            ("KR", "KR (920-923 MHz)"),
            ("TW", "TW (920-925 MHz)"),
            ("RU", "RU (868-870 MHz)"),
            ("IN", "IN (865-867 MHz)"),
            ("NZ_865", "NZ_865 (864-868 MHz)"),
            ("TH", "TH (920-925 MHz)"),
            ("UA_868", "UA_868 (863-870 MHz)"),
            ("LORA_24", "LORA_24 (2.4 GHz)"),
            ("UNSET", "UNSET (clear region)"),
            ("back", "Back"),
        ]

        choice = self.ctx.dialog.menu(
            "Set Region",
            "Select your LoRa region:",
            choices
        )

        if choice is None or choice == "back":
            return

        if self.ctx.dialog.yesno("Confirm", f"Set region to {choice}?\n\nRadio will restart."):
            self._radio_run(
                [self.ctx.get_meshtastic_cli(), '--host', 'localhost', '--set', 'lora.region', choice],
                f"Setting Region: {choice}"
            )

    def _radio_set_tx_power(self):
        """Set LoRa TX power via meshtastic CLI."""
        choices = [
            ("1", "1 dBm   (1 mW) - Minimum"),
            ("5", "5 dBm   (3 mW) - Very low"),
            ("10", "10 dBm  (10 mW) - Low"),
            ("14", "14 dBm  (25 mW) - EU limit"),
            ("17", "17 dBm  (50 mW) - Medium"),
            ("20", "20 dBm  (100 mW) - Standard"),
            ("22", "22 dBm  (158 mW) - High"),
            ("27", "27 dBm  (500 mW) - Very high"),
            ("30", "30 dBm  (1 W) - US max"),
            ("custom", "Custom value..."),
            ("back", "Back"),
        ]

        choice = self.ctx.dialog.menu(
            "Set TX Power",
            "Select transmit power level:\n\n"
            "Note: Check your region's legal limit.\n"
            "Higher power = more range but more battery use.",
            choices
        )

        if choice is None or choice == "back":
            return

        if choice == "custom":
            power_str = self.ctx.dialog.inputbox(
                "Custom TX Power",
                "Enter TX power in dBm (1-30):\n\n"
                "Common limits:\n"
                "  US: 30 dBm (1W)\n"
                "  EU: 14 dBm (25mW)\n"
                "  JP: 13 dBm",
                "20"
            )
            if not power_str:
                return
            try:
                power = int(power_str.strip())
            except ValueError:
                self.ctx.dialog.msgbox("Error", "Invalid power value. Enter a number 1-30.")
                return
        else:
            power = int(choice)

        if not (1 <= power <= 30):
            self.ctx.dialog.msgbox("Error", "TX power must be between 1 and 30 dBm.")
            return

        if self.ctx.dialog.yesno(
            "Confirm TX Power",
            f"Set TX power to {power} dBm?\n\n"
            f"This is approximately {10 ** (power / 10):.0f} mW.\n\n"
            "Ensure this complies with your region's regulations."
        ):
            self._radio_run(
                [self.ctx.get_meshtastic_cli(), '--host', 'localhost', '--set', 'lora.tx_power', str(power)],
                f"Setting TX Power: {power} dBm"
            )

    def _radio_set_name(self):
        """Set node name through the hardened owner writer below.

        This used to be a blank-pre-fill writer: no read of the current name,
        no confirm, no `"` refusal, the short name pre-filled from the long
        one, raw `--set-owner` to the radio. MeshAnchor has no
        meshtasticd_radio handler to delegate to (MeshForge does, 949736ee),
        so the writer MeshForge fixed after the 2026-09-20 rename incidents is
        carried here verbatim (TUI audit finding 2).
        """
        self._set_owner_name()

    # --- owner writer: verbatim twin of MeshForge meshtasticd_radio ---
    # `meshtastic --info` prints OUR owner on exactly one line —
    # `Owner: <long> (<short>)` (meshtastic/mesh_interface.py showInfo) —
    # and then "Nodes in mesh:" followed by EVERY node's user JSON, where each
    # entry carries its own `"longName": "...",` line.
    _OWNER_LINE = re.compile(r'^\s*Owner:\s*(?P<long>.*?)\s*\((?P<short>[^()]*)\)\s*$')

    @classmethod
    def _parse_current_owner(cls, raw):
        """Return (long_name, short_name) of OUR node from `--info` output.

        ⚠️ Until 2026-09-20 this dialog scanned the WHOLE output for any line
        containing `longName`, kept the LAST match, and stripped it with
        `split(':')` + `strip('"')`. The last such line is some OTHER node
        in the node list, and the strip leaves the trailing fragment — so
        the dialog offered `GreenPanda",` / `Meshtastic 8d30",` as the
        "current" name, and one Enter wrote a stranger's name (and, via the
        matching shortName line, its short name `8D30`) to the radio. That
        is how the desktop box's radio lost its real name more than once.
        Read only the Owner line; stop at the node list; absent = blank,
        never a guess. The CLI prints a literal `None` for a field it has no
        record of (`Owner: None (None)` right after a meshtasticd restart,
        before our own node is in its DB) — that is absent too, never a name.
        """
        for line in (raw or '').splitlines():
            m = cls._OWNER_LINE.match(line)
            if m:
                long_, short_ = m.group('long').strip(), m.group('short').strip()
                return (('' if long_ == 'None' else long_),
                        ('' if short_ == 'None' else short_))
            if line.strip().startswith('Nodes in mesh'):
                break
        return '', ''

    @classmethod
    def _owner_was_read(cls, raw) -> bool:
        """True only when `--info` printed an Owner line that is NOT the
        CLI's no-record placeholder `Owner: None (None)`."""
        for line in (raw or '').splitlines():
            m = cls._OWNER_LINE.match(line)
            if m:
                return not (m.group('long').strip() == 'None'
                            and m.group('short').strip() == 'None')
            if line.strip().startswith('Nodes in mesh'):
                break
        return False

    def _set_owner_name(self):
        """Set node owner name (long name and short name)."""
        self.ctx.dialog.infobox("Owner", "Getting current owner info...")

        try:
            sys.path.insert(0, str(self.ctx.src_dir))
            from commands import meshtastic as mesh_cmd

            result = mesh_cmd.get_node_info()
            current_long = ""
            current_short = ""

            raw = (getattr(result, 'raw', None) or getattr(result, 'raw_output', None) or "")
            if result.success and raw:
                current_long, current_short = self._parse_current_owner(raw)

            # "none" means the radio HAS no name; a failed read is not that.
            # With the CLI dead this dialog used to say "current: none" — the
            # same words as an unnamed radio — and invite a write on top of
            # a name it never saw (truth sweep 2026-09-22). Three states, two
            # labels: "none" only when the CLI succeeded AND printed an Owner
            # line that was genuinely empty; `Owner: None (None)` is the CLI
            # having NO RECORD (right after a restart) and a missing line is a
            # read that did not happen — both UNKNOWN, never a name-shaped
            # blank (non-author review 2026-09-22).
            absent = ('none' if (result.success and self._owner_was_read(raw))
                      else 'UNKNOWN — could not read the radio')

            long_name = self.ctx.dialog.inputbox(
                "Set Long Name",
                f"Enter node name (current: {current_long or absent}):",
                current_long or ""
            )

            if long_name is None:
                return

            short_name = self.ctx.dialog.inputbox(
                "Set Short Name",
                f"Enter 4-char short name (current: {current_short or absent}):",
                current_short or ""
            )

            if short_name is None:
                return

            # A field left as pre-filled is NOT written back. Accepting the
            # defaults used to rewrite both — and set_owner_short's .upper()
            # turned an unchanged lowercase `moc3` into `MOC3` on one Enter.
            if long_name == current_long:
                long_name = ""
            if short_name == current_short:
                short_name = ""
            if long_name:
                long_name = long_name[:40]
            if short_name:
                short_name = short_name[:4].upper()

            # A double quote in a node name is not something an operator
            # means — it is the signature of the old pre-fill bug (`GreenPanda",`).
            # Refuse at the authoring boundary rather than write it to the
            # radio (honest_failure_modes #3).
            bad = [n for n in (long_name, short_name) if n and '"' in n]
            if bad:
                self.ctx.dialog.msgbox(
                    "Error",
                    "A node name cannot contain a double quote (\").\n\n"
                    f"Refused: {bad[0]}\n\n"
                    "This shape is what the old dialog pre-filled from the node "
                    "list; type the name you mean instead.")
                return

            changes_made = []

            if long_name:
                self.ctx.dialog.infobox("Setting", f"Setting long name to: {long_name}")
                result = mesh_cmd.set_owner(long_name)
                if result.success:
                    changes_made.append(f"Long name: {long_name}")
                else:
                    self.ctx.dialog.msgbox("Error", f"Failed to set long name:\n{result.message}")
                    return

            if short_name:
                self.ctx.dialog.infobox("Setting", f"Setting short name to: {short_name}")
                result = mesh_cmd.set_owner_short(short_name)
                if result.success:
                    changes_made.append(f"Short name: {short_name}")
                else:
                    self.ctx.dialog.msgbox("Error", f"Failed to set short name:\n{result.message}")
                    return

            if changes_made:
                from utils.device_config_store import save_device_settings
                owner_data = {}
                if long_name:
                    owner_data['long_name'] = long_name
                if short_name:
                    owner_data['short_name'] = short_name
                saved = save_device_settings({'owner': owner_data})

                if saved:
                    self.ctx.dialog.msgbox("Success",
                        f"Owner settings updated:\n\n"
                        + "\n".join(changes_made)
                        + "\n\nSaved for restart persistence.")
                else:
                    self.ctx.dialog.msgbox("Partial Success",
                        f"Owner settings updated on device:\n\n"
                        + "\n".join(changes_made)
                        + "\n\nWARNING: Failed to save for restart "
                        "persistence. Settings will be lost on next "
                        "meshtasticd restart.")
            else:
                self.ctx.dialog.msgbox("Info", "No changes made.")

        except Exception as e:
            self.ctx.dialog.msgbox("Error", f"Failed to set owner name:\n{e}")

    def _radio_position_menu(self):
        """Position submenu: view settings or set fixed lat/lon."""
        choices = [
            ("view", "View position settings"),
            ("set", "Set fixed position (lat/lon)"),
            ("back", "Back"),
        ]

        choice = self.ctx.dialog.menu(
            "Position",
            "View or set node position:",
            choices
        )

        if choice is None or choice == "back":
            return

        cli = self.ctx.get_meshtastic_cli()
        conn_args = ['--host', 'localhost']

        if choice == "view":
            self._radio_run([cli] + conn_args + ['--get', 'position'], "Position Settings")
        elif choice == "set":
            lat = self.ctx.dialog.inputbox(
                "Latitude",
                "Enter latitude (decimal degrees):\n\n"
                "Example: 19.435175",
                ""
            )
            if not lat:
                return

            lon = self.ctx.dialog.inputbox(
                "Longitude",
                "Enter longitude (decimal degrees):\n\n"
                "Example: -155.213842",
                ""
            )
            if not lon:
                return

            try:
                lat_f = float(lat.strip())
                lon_f = float(lon.strip())
            except ValueError:
                self.ctx.dialog.msgbox("Error", "Invalid coordinates. Use decimal degrees.")
                return

            if not (-90 <= lat_f <= 90) or not (-180 <= lon_f <= 180):
                self.ctx.dialog.msgbox("Error",
                    "Coordinates out of range.\n\n"
                    "Latitude: -90 to 90\n"
                    "Longitude: -180 to 180")
                return

            confirm = self.ctx.dialog.yesno(
                "Confirm Position",
                f"Set fixed position?\n\n"
                f"Latitude:  {lat_f}\n"
                f"Longitude: {lon_f}",
                default_no=True
            )
            if not confirm:
                return

            self._radio_run(
                [cli] + conn_args + ['--setlat', str(lat_f), '--setlon', str(lon_f)],
                "Setting Position"
            )

    def _radio_reboot(self):
        """Reboot the radio via meshtastic CLI."""
        if self.ctx.dialog.yesno("Reboot Radio", "Reboot the Meshtastic radio?\n\nThis restarts the firmware.", default_no=True):
            self._radio_run(
                [self.ctx.get_meshtastic_cli(), '--host', 'localhost', '--reboot'],
                "Rebooting Radio"
            )
