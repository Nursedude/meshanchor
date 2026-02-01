"""
NomadNet Client Mixin for MeshForge Launcher TUI.

Provides TUI handlers to install, configure, launch, and manage
NomadNet -- the primary RNS client application used for verifying
Meshtastic <> Reticulum connectivity.

NomadNet runs its own text-UI with a built-in micron page browser
for browsing content hosted on RNS nodes.  It can also run in daemon
mode to serve pages and propagate LXMF messages.

Config directory resolution (mirrors NomadNet upstream):
  /etc/nomadnetwork  ->  ~/.config/nomadnetwork  ->  ~/.nomadnetwork

Requires:  pipx install nomadnet   (pulls in rns + lxmf automatically)
"""

import os
import shutil
import subprocess
import time
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Import centralized service checking
try:
    from utils.service_check import check_process_running
    _HAS_SERVICE_CHECK = True
except ImportError:
    _HAS_SERVICE_CHECK = False

# Import for sudo-safe home directory - see persistent_issues.md Issue #1
try:
    from utils.paths import get_real_user_home
except ImportError:
    def get_real_user_home():
        sudo_user = os.environ.get('SUDO_USER', '')
        if sudo_user and sudo_user != 'root' and '/' not in sudo_user and '..' not in sudo_user:
            candidate = Path(f'/home/{sudo_user}')
            return candidate
        logname = os.environ.get('LOGNAME', '')
        if logname and logname != 'root' and '/' not in logname and '..' not in logname:
            candidate = Path(f'/home/{logname}')
            return candidate
        return Path('/root')


class NomadNetClientMixin:
    """Mixin providing NomadNet client management for the TUI launcher."""

    # ------------------------------------------------------------------
    # Ownership fix for user directories
    # ------------------------------------------------------------------

    def _fix_user_directory_ownership(self) -> bool:
        """Fix ownership of user directories if they were created by root.

        When MeshForge runs with sudo, any user-space applications (NomadNet,
        rnstatus, etc.) that were previously launched as root may have created
        ~/.reticulum or ~/.nomadnetwork with root ownership.

        This function detects and fixes that situation so the real user can
        access their own directories.

        Returns:
            True if directories are accessible (or were fixed successfully).
            False if fix failed and user declined to proceed.
        """
        sudo_user = os.environ.get('SUDO_USER')
        if not sudo_user or sudo_user == 'root':
            # Not running via sudo, nothing to fix
            return True

        user_home = get_real_user_home()
        if not user_home.exists():
            return True

        # Directories that should belong to the user, not root
        user_dirs = [
            user_home / '.reticulum',
            user_home / '.nomadnetwork',
            user_home / '.config' / 'nomadnetwork',
        ]

        dirs_to_fix = []
        for dir_path in user_dirs:
            if dir_path.exists():
                try:
                    stat_info = dir_path.stat()
                    # Check if owned by root (uid 0)
                    if stat_info.st_uid == 0:
                        dirs_to_fix.append(dir_path)
                except (OSError, PermissionError):
                    # Can't stat, might still be a problem
                    dirs_to_fix.append(dir_path)

        if not dirs_to_fix:
            return True

        # Found directories with wrong ownership - offer to fix
        dir_list = '\n'.join(f'  {d}' for d in dirs_to_fix)
        if not self.dialog.yesno(
            "Fix Directory Ownership",
            f"The following directories are owned by root,\n"
            f"which prevents NomadNet from accessing them:\n\n"
            f"{dir_list}\n\n"
            f"This happened because NomadNet or rnsd was\n"
            f"previously run as root.\n\n"
            f"Fix ownership to user '{sudo_user}'?",
        ):
            # User declined - warn but allow proceeding
            return self.dialog.yesno(
                "Proceed Anyway?",
                "Ownership was not fixed.\n\n"
                "NomadNet may fail with 'Permission denied' errors.\n\n"
                "Continue anyway?",
            )

        # Fix ownership recursively
        self.dialog.infobox("Fixing Ownership", f"Changing ownership to {sudo_user}...")

        for dir_path in dirs_to_fix:
            try:
                # chown -R user:user dir_path
                subprocess.run(
                    ['chown', '-R', f'{sudo_user}:{sudo_user}', str(dir_path)],
                    capture_output=True, timeout=30
                )
                logger.info(f"Fixed ownership of {dir_path} to {sudo_user}")
            except Exception as e:
                logger.warning(f"Failed to fix ownership of {dir_path}: {e}")
                self.dialog.msgbox(
                    "Ownership Fix Failed",
                    f"Could not fix ownership of:\n  {dir_path}\n\n"
                    f"Error: {e}\n\n"
                    f"Try manually:\n  sudo chown -R {sudo_user}:{sudo_user} {dir_path}",
                )
                return False

        return True

    # ------------------------------------------------------------------
    # Top-level submenu
    # ------------------------------------------------------------------

    def _nomadnet_menu(self):
        """NomadNet RNS client -- install, configure, launch."""
        while True:
            running = self._is_nomadnet_running()
            installed = self._is_nomadnet_installed()

            if not installed:
                subtitle = "NomadNet is NOT INSTALLED"
            elif running:
                subtitle = "NomadNet is RUNNING"
            else:
                subtitle = "NomadNet is installed (not running)"

            choices = [
                ("status", "NomadNet Status"),
            ]

            if installed:
                if running:
                    choices.append(("stop", "Stop NomadNet"))
                else:
                    choices.append(("textui", "Launch Text UI (interactive)"))
                    choices.append(("daemon", "Start as Daemon (background)"))
                choices.append(("config", "View NomadNet Config"))
                choices.append(("edit", "Edit NomadNet Config"))
            else:
                choices.append(("install", "Install NomadNet"))

            choices.append(("back", "Back"))

            choice = self.dialog.menu(
                "NomadNet Client",
                f"RNS client with page browser & LXMF messaging:\n\n"
                f"{subtitle}",
                choices,
            )

            if choice is None or choice == "back":
                break

            if choice == "status":
                self._nomadnet_status()
            elif choice == "textui":
                self._launch_nomadnet_textui()
            elif choice == "daemon":
                self._launch_nomadnet_daemon()
            elif choice == "stop":
                self._stop_nomadnet()
            elif choice == "config":
                self._view_nomadnet_config()
            elif choice == "edit":
                self._edit_nomadnet_config()
            elif choice == "install":
                self._install_nomadnet()

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def _nomadnet_status(self):
        """Show comprehensive NomadNet status."""
        subprocess.run(['clear'], check=False, timeout=5)
        print("=== NomadNet Status ===\n")

        # Installation
        nn_path = shutil.which('nomadnet')
        if not nn_path:
            # Check user local bin (pipx / pip install --user)
            user_home = get_real_user_home()
            candidate = user_home / '.local' / 'bin' / 'nomadnet'
            if candidate.exists():
                nn_path = str(candidate)

        if nn_path:
            print(f"  Installed: {nn_path}")
            # Get version
            try:
                result = subprocess.run(
                    [nn_path, '--version'],
                    capture_output=True, text=True, timeout=10
                )
                version = result.stdout.strip() or result.stderr.strip()
                if version:
                    print(f"  Version:   {version}")
            except Exception as e:
                logger.debug(f"NomadNet version check failed: {e}")
        else:
            print("  NOT INSTALLED")
            print("  Install:   pipx install nomadnet")
            print("             (installs rns + lxmf automatically)")

        # Process
        print()
        running = self._is_nomadnet_running()
        if running:
            print("  Process:   RUNNING")
            try:
                result = subprocess.run(
                    ['pgrep', '-fa', 'bin/nomadnet'],
                    capture_output=True, text=True, timeout=5
                )
                if result.stdout.strip():
                    for line in result.stdout.strip().split('\n'):
                        if 'pgrep' not in line:
                            print(f"             {line.strip()}")
            except Exception:
                pass
        else:
            print("  Process:   not running")

        # Config file
        print()
        config_path = self._get_nomadnet_config_path()
        if config_path and config_path.exists():
            print(f"  Config:    {config_path}")
            try:
                content = config_path.read_text()
                # Parse key settings
                for line in content.split('\n'):
                    stripped = line.strip()
                    if stripped.startswith('#') or not stripped:
                        continue
                    if any(k in stripped.lower() for k in [
                        'user_interface', 'enable_node', 'enable_client',
                        'announce_at_start', 'node_name', 'display_name',
                    ]):
                        print(f"             {stripped}")
            except PermissionError:
                print(f"             (permission denied)")
        else:
            print(f"  Config:    not found")
            print(f"  Expected:  ~/.nomadnetwork/config")
            print(f"             (created on first run)")

        # RNS shared instance check
        print()
        print("--- RNS Connectivity ---")
        try:
            if _HAS_SERVICE_CHECK:
                rnsd_running = check_process_running('rnsd')
            else:
                # Fallback to direct pgrep call
                result = subprocess.run(
                    ['pgrep', '-f', 'rnsd'],
                    capture_output=True, text=True, timeout=5
                )
                rnsd_running = result.returncode == 0

            if rnsd_running:
                print("  rnsd:      RUNNING (shared instance available)")
            else:
                print("  rnsd:      NOT running")
                print("  WARNING:   NomadNet needs rnsd or share_instance=Yes")
        except Exception:
            print("  rnsd:      (check failed)")

        self._wait_for_enter()

    # ------------------------------------------------------------------
    # Launch text UI
    # ------------------------------------------------------------------

    def _launch_nomadnet_textui(self):
        """Launch NomadNet in interactive text UI mode.

        This takes over the terminal (like running nomadnet directly).
        The user returns to MeshForge when they exit NomadNet.

        When running via sudo, launches as the real user so NomadNet
        uses their config (~/.nomadnetwork) instead of root's.
        """
        nn_path = self._find_nomadnet_binary()
        if not nn_path:
            return

        # Fix ownership of user directories if they were created by root
        # This is a common issue when MeshForge runs with sudo
        if not self._fix_user_directory_ownership():
            return

        # Validate and repair config if needed (e.g., missing [textui] section)
        if not self._validate_nomadnet_config():
            return

        # Check if rnsd is running (NomadNet needs RNS)
        if not self._check_rns_for_nomadnet():
            return

        # Clear screen before launching
        subprocess.run(['clear'], check=False, timeout=5)
        print("=== Launching NomadNet ===")
        print("Exit NomadNet (Ctrl+Q) to return to MeshForge.\n")

        # When running via sudo, we must run NomadNet as the real user.
        # Just setting HOME is not enough - RPC authentication between
        # NomadNet and rnsd requires matching UIDs.
        # Use 'su' which properly handles TTY for interactive apps.
        sudo_user = os.environ.get('SUDO_USER')

        try:
            if sudo_user and sudo_user != 'root':
                # Run as real user using 'su' - preserves TTY for curses
                # The '-' gives a login shell, '-c' runs the command
                result = subprocess.run(
                    ['su', '-', sudo_user, '-c', f'{nn_path} --textui'],
                    timeout=None
                )
            else:
                # Not running via sudo, run directly
                result = subprocess.run([nn_path, '--textui'], timeout=None)

            # After NomadNet exits, show status and wait for user
            print()
            if result.returncode != 0:
                self._diagnose_nomadnet_error(result.returncode, sudo_user)
            else:
                print("NomadNet exited normally.")
            print("\nPress Enter to return to MeshForge...")
            try:
                input()
            except (EOFError, KeyboardInterrupt):
                pass
        except KeyboardInterrupt:
            print("\n\nAborted.")
        except FileNotFoundError:
            print(f"\nError: NomadNet binary not found at: {nn_path}")
            print("\nPress Enter to continue...")
            try:
                input()
            except (EOFError, KeyboardInterrupt):
                pass
        except Exception as e:
            print(f"\nFailed to launch NomadNet: {e}")
            print("\nPress Enter to continue...")
            try:
                input()
            except (EOFError, KeyboardInterrupt):
                pass

    def _diagnose_nomadnet_error(self, returncode: int, sudo_user: str = None):
        """Analyze NomadNet failure and provide helpful diagnostics."""
        print(f"NomadNet exited with error code {returncode}")

        # Try to read the log file for clues
        user_home = get_real_user_home()
        logfile = user_home / '.nomadnetwork' / 'logfile'

        error_hints = []
        if logfile.exists():
            try:
                content = logfile.read_text()
                last_lines = content.strip().split('\n')[-20:]

                # Look for known error patterns
                for line in last_lines:
                    if 'AuthenticationError' in line or 'digest sent was rejected' in line:
                        error_hints.append("RPC authentication failed between NomadNet and rnsd")
                        error_hints.append("This usually means rnsd is running as a different user")
                        break
                    elif 'KeyError' in line and 'textui' in line.lower():
                        error_hints.append("Config missing [textui] section")
                        error_hints.append("Delete ~/.nomadnetwork/config and restart")
                        break
                    elif 'PermissionError' in line or 'Permission denied' in line:
                        error_hints.append("Permission denied accessing files")
                        error_hints.append(f"Check ownership: ls -la ~/.nomadnetwork/")
                        break
                    elif 'ModuleNotFoundError' in line or 'ImportError' in line:
                        error_hints.append("Missing Python dependencies")
                        error_hints.append("Try: pipx reinstall nomadnet")
                        break
            except (OSError, PermissionError):
                pass

        if error_hints:
            print("\nDiagnosis:")
            for hint in error_hints:
                print(f"  - {hint}")
        else:
            print("\nCheck logs for details:")
            print(f"  cat {logfile}")
            print("  journalctl --user -u nomadnet -n 50")

    # ------------------------------------------------------------------
    # Launch daemon
    # ------------------------------------------------------------------

    def _launch_nomadnet_daemon(self):
        """Start NomadNet in daemon mode (background, no UI).

        When running via sudo, launches as the real user so NomadNet
        uses their config (~/.nomadnetwork) instead of root's.
        """
        nn_path = self._find_nomadnet_binary()
        if not nn_path:
            return

        if self._is_nomadnet_running():
            self.dialog.msgbox("Already Running", "NomadNet is already running.")
            return

        # Fix ownership of user directories if they were created by root
        if not self._fix_user_directory_ownership():
            return

        if not self._check_rns_for_nomadnet():
            return

        if not self.dialog.yesno(
            "Start NomadNet Daemon",
            "Start NomadNet in daemon mode (background)?\n\n"
            "This will:\n"
            "  - Announce your node on the RNS network\n"
            "  - Accept and propagate LXMF messages\n"
            "  - Serve node pages (if enabled in config)\n\n"
            "NomadNet will run until stopped.",
        ):
            return

        self.dialog.infobox("Starting", "Starting NomadNet daemon...")

        # Build command - run as real user if we're under sudo
        # This ensures NomadNet uses ~/.nomadnetwork/config, not /root/.nomadnetwork/config
        sudo_user = os.environ.get('SUDO_USER')
        if sudo_user and sudo_user != 'root':
            # Run as real user with -H to set HOME correctly
            # Using -H instead of -i avoids running shell profiles which can interfere
            cmd = ['sudo', '-H', '-u', sudo_user, nn_path, '--daemon']
        else:
            cmd = [nn_path, '--daemon']

        try:
            subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True
            )

            # Wait briefly and verify
            time.sleep(3)

            if self._is_nomadnet_running():
                self.dialog.msgbox(
                    "Daemon Started",
                    "NomadNet daemon is running in the background.\n\n"
                    "Your node is now announcing on the RNS network.\n"
                    "Use 'Stop NomadNet' to shut it down.",
                )
            else:
                self.dialog.msgbox(
                    "Start Failed",
                    "NomadNet daemon failed to start.\n\n"
                    "Check logs: ~/.nomadnetwork/logfile\n"
                    "Or run manually: nomadnet --daemon --console",
                )
        except FileNotFoundError:
            self.dialog.msgbox("Error", f"NomadNet binary not found at: {nn_path}")
        except Exception as e:
            self.dialog.msgbox("Error", f"Failed to start NomadNet daemon:\n{e}")

    # ------------------------------------------------------------------
    # Stop
    # ------------------------------------------------------------------

    def _stop_nomadnet(self):
        """Stop running NomadNet process(es)."""
        if not self._is_nomadnet_running():
            self.dialog.msgbox("Not Running", "NomadNet is not currently running.")
            return

        if not self.dialog.yesno(
            "Stop NomadNet",
            "Stop all running NomadNet processes?",
        ):
            return

        try:
            subprocess.run(
                ['pkill', '-f', 'bin/nomadnet'],
                capture_output=True, timeout=10
            )

            time.sleep(2)

            if self._is_nomadnet_running():
                # Force kill
                subprocess.run(
                    ['pkill', '-9', '-f', 'bin/nomadnet'],
                    capture_output=True, timeout=10
                )
                time.sleep(1)

            if not self._is_nomadnet_running():
                self.dialog.msgbox("Stopped", "NomadNet has been stopped.")
            else:
                self.dialog.msgbox("Warning", "NomadNet may still be running.\nTry: sudo pkill -9 -f nomadnet")
        except Exception as e:
            self.dialog.msgbox("Error", f"Failed to stop NomadNet:\n{e}")

    # ------------------------------------------------------------------
    # Config management
    # ------------------------------------------------------------------

    def _view_nomadnet_config(self):
        """View NomadNet configuration."""
        subprocess.run(['clear'], check=False, timeout=5)
        print("=== NomadNet Configuration ===\n")

        config_path = self._get_nomadnet_config_path()
        if config_path and config_path.exists():
            print(f"Config: {config_path}\n")
            try:
                content = config_path.read_text()
                print(content)

                # Highlight key connectivity settings
                print("\n--- Connectivity Notes ---")
                content_lower = content.lower()
                if 'enable_client = yes' in content_lower:
                    print("  Client:    ENABLED (can send/receive messages)")
                elif 'enable_client = no' in content_lower:
                    print("  Client:    DISABLED")
                if 'enable_node = yes' in content_lower:
                    print("  Node:      ENABLED (serving pages, propagation)")
                elif 'enable_node = no' in content_lower:
                    print("  Node:      DISABLED (not serving)")
                if 'announce_at_start = yes' in content_lower:
                    print("  Announce:  YES (visible to other nodes)")
                if 'user_interface = text' in content_lower:
                    print("  UI mode:   text (interactive TUI with browser)")
            except PermissionError:
                print(f"Permission denied reading {config_path}")
        else:
            print("No NomadNet config found.\n")
            print("Config is created on first run of NomadNet.")
            print("Expected locations (checked in order):")
            print("  1. /etc/nomadnetwork/config")
            user_home = get_real_user_home()
            print(f"  2. {user_home}/.config/nomadnetwork/config")
            print(f"  3. {user_home}/.nomadnetwork/config")
            print("\nRun 'Launch Text UI' to create the default config.")

        self._wait_for_enter()

    def _edit_nomadnet_config(self):
        """Edit NomadNet config with available editor."""
        config_path = self._get_nomadnet_config_path()

        if not config_path or not config_path.exists():
            if self.dialog.yesno(
                "No Config Found",
                "NomadNet config doesn't exist yet.\n\n"
                "It is created automatically on first run.\n"
                "Launch NomadNet once to generate it?\n\n"
                "(It will create the config and exit.)",
            ):
                nn_path = self._find_nomadnet_binary()
                if nn_path:
                    self.dialog.infobox("Generating Config", "Running NomadNet briefly to generate config...")
                    try:
                        # Build command - run as real user if we're under sudo
                        # This ensures config is created with correct ownership
                        sudo_user = os.environ.get('SUDO_USER')
                        if sudo_user and sudo_user != 'root':
                            # Using -H instead of -i to set HOME without shell profiles
                            cmd = ['sudo', '-H', '-u', sudo_user, nn_path, '--daemon']
                        else:
                            cmd = [nn_path, '--daemon']

                        # Run daemon briefly, then kill to generate config
                        proc = subprocess.Popen(
                            cmd,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            start_new_session=True,
                        )
                        time.sleep(5)
                        proc.terminate()
                        try:
                            proc.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            proc.kill()

                        config_path = self._get_nomadnet_config_path()
                        if config_path and config_path.exists():
                            self.dialog.msgbox(
                                "Config Generated",
                                f"Config created at:\n  {config_path}\n\n"
                                f"Opening editor...",
                            )
                        else:
                            self.dialog.msgbox(
                                "Config Not Found",
                                "NomadNet ran but config was not generated.\n"
                                "Check: ~/.nomadnetwork/config",
                            )
                            return
                    except FileNotFoundError:
                        self.dialog.msgbox("Error", f"NomadNet not found at: {nn_path}")
                        return
                    except Exception as e:
                        self.dialog.msgbox("Error", f"Failed to generate config:\n{e}")
                        return
            else:
                return

        if not config_path or not config_path.exists():
            return

        # Find editor
        editor = None
        for cmd in ['nano', 'vim', 'vi']:
            if shutil.which(cmd):
                editor = cmd
                break

        if not editor:
            self.dialog.msgbox("Error", "No text editor found (nano, vim, vi)")
            return

        subprocess.run([editor, str(config_path)], timeout=None)

    # ------------------------------------------------------------------
    # Install
    # ------------------------------------------------------------------

    def _install_nomadnet(self):
        """Install NomadNet via pipx (isolated environment)."""
        if self._is_nomadnet_installed():
            self.dialog.msgbox("Already Installed", "NomadNet is already installed.")
            return

        if not self.dialog.yesno(
            "Install NomadNet",
            "Install NomadNet RNS client?\n\n"
            "This will run:\n"
            "  pipx install nomadnet\n\n"
            "NomadNet pulls in RNS and LXMF automatically.\n\n"
            "It provides:\n"
            "  - Text UI with micron page browser\n"
            "  - LXMF encrypted messaging\n"
            "  - Node hosting and page serving\n"
            "  - Network announcement/discovery\n\n"
            "Source: github.com/markqvist/NomadNet\n\n"
            "Install now?",
        ):
            return

        subprocess.run(['clear'], check=False, timeout=5)
        print("=== Installing NomadNet ===\n")

        # Determine if we should install as a different user (when running via sudo)
        sudo_user = os.environ.get('SUDO_USER')
        run_as_user = sudo_user if sudo_user and sudo_user != 'root' else None

        try:
            # Ensure pipx is available (this needs root for apt)
            if not shutil.which('pipx'):
                print("Installing pipx...\n")
                result = subprocess.run(
                    ['apt-get', 'install', '-y', 'pipx'],
                    timeout=60
                )
                if result.returncode != 0:
                    print("\nFailed to install pipx.")
                    print("Try manually: sudo apt install pipx")
                    self._wait_for_enter()
                    return

            # Build pipx commands - run as real user if we're under sudo
            def run_pipx_cmd(args, timeout_sec=300):
                """Run pipx command, as real user if running via sudo."""
                if run_as_user:
                    # Run as real user with login shell (-i) to set HOME correctly
                    cmd = ['sudo', '-i', '-u', run_as_user] + args
                else:
                    cmd = args
                return subprocess.run(cmd, timeout=timeout_sec)

            # Ensure pipx bin dir is in PATH for this session
            print("Ensuring pipx paths...\n")
            run_pipx_cmd(['pipx', 'ensurepath'], timeout_sec=15)

            # Add common pipx bin dirs to current process PATH
            for bindir in [
                get_real_user_home() / '.local' / 'bin',
                Path('/root/.local/bin'),
                Path('/usr/local/bin'),
            ]:
                if bindir.is_dir() and str(bindir) not in os.environ.get('PATH', ''):
                    os.environ['PATH'] = f"{bindir}:{os.environ.get('PATH', '')}"

            # Install nomadnet via pipx (live output)
            if run_as_user:
                print(f"\nInstalling NomadNet via pipx (as {run_as_user})...\n")
            else:
                print("\nInstalling NomadNet via pipx...\n")
            result = run_pipx_cmd(['pipx', 'install', 'nomadnet'])

            if result.returncode == 0:
                print("\nInstallation complete.")
                if self._is_nomadnet_installed():
                    nn_path = shutil.which('nomadnet')
                    if nn_path:
                        print(f"NomadNet installed at: {nn_path}")
                    else:
                        # Check user's local bin
                        user_bin = get_real_user_home() / '.local' / 'bin' / 'nomadnet'
                        if user_bin.exists():
                            print(f"NomadNet installed at: {user_bin}")

                    # Configure NomadNet for shared instance mode (use rnsd)
                    self._setup_nomadnet_shared_instance(run_as_user)
                else:
                    print("\nnomadnet not found in PATH.")
                    print("You may need to log out and back in,")
                    print("or run: eval \"$(pipx ensurepath)\"")
            else:
                print(f"\nInstallation failed (exit code {result.returncode}).")
                print("Try manually: pipx install nomadnet")
        except FileNotFoundError:
            print("pipx not found.")
            print("Try: sudo apt install pipx && pipx install nomadnet")
        except KeyboardInterrupt:
            print("\n\nInstallation cancelled.")
        except subprocess.TimeoutExpired:
            print("\nInstallation timed out. Check your internet connection.")
            print("Try manually: pipx install nomadnet")
        except Exception as e:
            print(f"\nInstallation error: {e}")
            print("Try manually: pipx install nomadnet")

        try:
            self._wait_for_enter()
        except (EOFError, KeyboardInterrupt):
            pass

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _is_nomadnet_installed(self) -> bool:
        """Check if NomadNet is installed."""
        if shutil.which('nomadnet'):
            return True
        # Check user local bin
        user_home = get_real_user_home()
        candidate = user_home / '.local' / 'bin' / 'nomadnet'
        return candidate.exists()

    def _setup_nomadnet_shared_instance(self, run_as_user: str = None):
        """Post-install message for NomadNet.

        NomadNet creates its own complete default config on first run.
        We don't create configs - let NomadNet use its defaults.
        """
        user_home = get_real_user_home()
        config_file = user_home / '.nomadnetwork' / 'config'

        if config_file.exists():
            print(f"\nNomadNet config exists: {config_file}")
        else:
            print("\nNomadNet will create its default config on first run.")

        print("\nNomadNet uses the shared RNS instance from rnsd by default.")
        print("Config location: ~/.nomadnetwork/config")

    def _is_nomadnet_running(self) -> bool:
        """Check if NomadNet process is running.

        Uses centralized service_check module when available, with fallback
        to direct pgrep for custom filtering.
        """
        # Try unified check first (faster and standardized)
        if _HAS_SERVICE_CHECK:
            if check_process_running('nomadnet'):
                return True

        # Fallback to direct pgrep with custom filtering
        try:
            result = subprocess.run(
                ['pgrep', '-f', 'bin/nomadnet'],
                capture_output=True, text=True, timeout=5
            )
            # Filter out false positives (our own grep, etc.)
            if result.returncode == 0 and result.stdout.strip():
                for pid in result.stdout.strip().split('\n'):
                    if pid.strip() and pid.strip() != str(os.getpid()):
                        return True
            return False
        except Exception:
            return False

    def _find_nomadnet_binary(self) -> str:
        """Find NomadNet binary path, or show error and return None."""
        nn_path = shutil.which('nomadnet')
        if not nn_path:
            user_home = get_real_user_home()
            candidate = user_home / '.local' / 'bin' / 'nomadnet'
            if candidate.exists():
                nn_path = str(candidate)

        if not nn_path:
            self.dialog.msgbox(
                "Not Installed",
                "NomadNet is not installed.\n\n"
                "Install with: pipx install nomadnet\n"
                "Or use the Install option from this menu.",
            )
            return None
        return nn_path

    def _get_nomadnet_config_path(self):
        """Find the NomadNet config file.

        Mirrors NomadNet's own resolution order:
          /etc/nomadnetwork/config  ->
          ~/.config/nomadnetwork/config  ->
          ~/.nomadnetwork/config
        """
        user_home = get_real_user_home()

        candidates = [
            Path('/etc/nomadnetwork/config'),
            user_home / '.config' / 'nomadnetwork' / 'config',
            user_home / '.nomadnetwork' / 'config',
        ]

        for candidate in candidates:
            if candidate.exists():
                return candidate

        # Return the default path (even if it doesn't exist yet)
        return user_home / '.nomadnetwork' / 'config'

    def _check_rns_for_nomadnet(self) -> bool:
        """Check that RNS/rnsd is available before launching NomadNet.

        Uses centralized service_check module when available.
        Returns True if OK to proceed, False if user cancelled.
        """
        try:
            if _HAS_SERVICE_CHECK:
                rnsd_running = check_process_running('rnsd')
            else:
                # Fallback to direct pgrep call
                result = subprocess.run(
                    ['pgrep', '-f', 'rnsd'],
                    capture_output=True, text=True, timeout=5
                )
                rnsd_running = result.returncode == 0
        except Exception:
            rnsd_running = False

        if rnsd_running:
            return True

        # rnsd not running -- warn but allow proceeding
        # (NomadNet can run its own RNS instance)
        return self.dialog.yesno(
            "rnsd Not Running",
            "The RNS daemon (rnsd) is not running.\n\n"
            "NomadNet can start its own RNS instance,\n"
            "but for Meshtastic bridging you should run rnsd\n"
            "with share_instance = Yes in the Reticulum config.\n\n"
            "Start rnsd first:\n"
            "  sudo systemctl start rnsd\n\n"
            "Continue anyway?",
        )

    def _validate_nomadnet_config(self) -> bool:
        """Validate and repair NomadNet config if needed.

        NomadNet requires a [textui] section when running in text UI mode.
        If the config exists but lacks this section (e.g., old config from
        before [textui] was required), NomadNet will crash with KeyError.

        This function checks for and adds a minimal [textui] section if missing.

        Returns:
            True to proceed with launch, False if user cancelled.
        """
        config_path = self._get_nomadnet_config_path()
        if not config_path or not config_path.exists():
            # No config yet - NomadNet will create default on first run
            return True

        try:
            content = config_path.read_text()
        except (OSError, PermissionError) as e:
            logger.warning(f"Cannot read NomadNet config: {e}")
            return True  # Let NomadNet handle the error

        # Check if [textui] section exists (case-insensitive)
        if '[textui]' in content.lower():
            return True

        # Missing [textui] section - need to add it
        logger.info(f"NomadNet config missing [textui] section: {config_path}")

        if not self.dialog.yesno(
            "Config Repair Needed",
            f"Your NomadNet config is missing the [textui] section\n"
            f"required for text UI mode.\n\n"
            f"Config: {config_path}\n\n"
            f"Add a default [textui] section now?",
        ):
            return self.dialog.yesno(
                "Proceed Anyway?",
                "Without [textui], NomadNet will crash.\n\n"
                "Continue anyway?",
            )

        # Add minimal [textui] section
        textui_section = """

[textui]
# Text UI configuration added by MeshForge
intro_time = 1
theme = dark
colormode = 256
glyphs = unicode
mouse_enabled = yes
hide_guide = no
"""
        try:
            # Append [textui] section to config
            with open(config_path, 'a') as f:
                f.write(textui_section)
            logger.info(f"Added [textui] section to {config_path}")

            # Fix ownership if running via sudo
            sudo_user = os.environ.get('SUDO_USER')
            if sudo_user and sudo_user != 'root':
                import subprocess
                subprocess.run(
                    ['chown', f'{sudo_user}:{sudo_user}', str(config_path)],
                    capture_output=True, timeout=10
                )

            self.dialog.msgbox(
                "Config Updated",
                f"Added [textui] section to config.\n\n"
                f"NomadNet text UI should now work.",
            )
            return True
        except (OSError, PermissionError) as e:
            self.dialog.msgbox(
                "Config Update Failed",
                f"Could not update config:\n  {config_path}\n\n"
                f"Error: {e}\n\n"
                f"Add [textui] section manually or delete config\n"
                f"and let NomadNet recreate it.",
            )
            return False
