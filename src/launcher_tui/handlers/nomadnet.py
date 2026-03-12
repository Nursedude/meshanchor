"""
NomadNet Handler — NomadNet client installation, configuration, and management.

Provides TUI handlers to install, configure, launch, and manage
NomadNet -- the primary RNS client application used for verifying
Meshtastic <> Reticulum connectivity.

NomadNet runs its own text-UI with a built-in micron page browser
for browsing content hosted on RNS nodes.  It can also run in daemon
mode to serve pages and propagate LXMF messages.

Config directory resolution (mirrors NomadNet upstream):
  /etc/nomadnetwork  ->  ~/.config/nomadnetwork  ->  ~/.nomadnetwork

Requires:  pipx install nomadnet   (pulls in rns + lxmf automatically)

Converted from nomadnet_client_mixin.py as part of the mixin-to-registry migration (Batch 8).
"""

import os
import shutil
import subprocess
import time
import logging
from pathlib import Path
from typing import Optional

from handler_protocol import BaseHandler
from backend import clear_screen

logger = logging.getLogger(__name__)

from utils.paths import ReticulumPaths

from utils.safe_import import safe_import

# Import centralized service checking
check_process_running, start_service, stop_service, apply_config_and_restart, _sudo_cmd, _HAS_SERVICE_CHECK = safe_import(
    'utils.service_check', 'check_process_running', 'start_service', 'stop_service', 'apply_config_and_restart', '_sudo_cmd'
)

# Sudo-safe home directory — first-party, always available (MF001)
from utils.paths import get_real_user_home

# LXMF exclusivity — prevent concurrent LXMF apps on port 37428
from handlers._lxmf_utils import ensure_lxmf_exclusive

# RNS prerequisite checks extracted for file size compliance (CLAUDE.md #6)
from handlers._nomadnet_rns_checks import NomadNetRNSChecksMixin


class NomadNetHandler(NomadNetRNSChecksMixin, BaseHandler):
    """TUI handler for NomadNet client management."""

    handler_id = "nomadnet"
    menu_section = "mesh_networks"

    def menu_items(self):
        return [
            ("nomadnet", "NomadNet Client     RNS messaging", "rns"),
        ]

    def execute(self, action):
        if action == "nomadnet":
            self._nomadnet_menu()

    # ------------------------------------------------------------------
    # LXMF exclusivity — imported from shared utility
    # ------------------------------------------------------------------

    def _ensure_lxmf_exclusive(self, starting_app: str) -> bool:
        """Ensure no other LXMF app is using port 37428."""
        return ensure_lxmf_exclusive(self.ctx.dialog, starting_app)

    # ------------------------------------------------------------------
    # Cross-handler helpers (delegate to rns_diagnostics handler)
    # ------------------------------------------------------------------

    def _get_rns_diagnostics_handler(self):
        """Get the RNS diagnostics handler from the registry."""
        if self.ctx.registry:
            return self.ctx.registry.get_handler("rns_diagnostics")
        return None

    def _get_rnsd_user(self) -> Optional[str]:
        """Get the OS user running the rnsd process, or None if not running.

        Delegates to RNSDiagnosticsHandler when available, falls back to
        direct process check.
        """
        diag = self._get_rns_diagnostics_handler()
        if diag:
            return diag._get_rnsd_user()
        # Fallback: direct ps check
        try:
            result = subprocess.run(
                ['ps', '-o', 'user=', '-C', 'rnsd'],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode != 0:
                return None
            lines = result.stdout.strip().splitlines()
            return lines[0].strip() if lines else None
        except (subprocess.SubprocessError, OSError):
            return None

    def _fix_rnsd_user(self, target_user: str) -> bool:
        """Configure rnsd systemd service to run as the specified user.

        Delegates to RNSDiagnosticsHandler.
        """
        diag = self._get_rns_diagnostics_handler()
        if diag:
            return diag._fix_rnsd_user(target_user)
        self.ctx.dialog.msgbox(
            "Not Available",
            "RNS diagnostics handler not available.\n\n"
            "Cannot reconfigure rnsd user automatically.",
        )
        return False

    def _wait_for_rns_port(self, max_wait: int = 10) -> bool:
        """Wait for rnsd shared instance to become available.

        Delegates to RNSDiagnosticsHandler when available.
        """
        diag = self._get_rns_diagnostics_handler()
        if diag:
            return diag._wait_for_rns_port(max_wait=max_wait)
        # Fallback: simple socket check
        import socket
        for _ in range(max_wait):
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(1)
                result = s.connect_ex(('127.0.0.1', 37428))
                s.close()
                if result == 0:
                    return True
            except OSError:
                pass
            time.sleep(1)
        return False

    def _find_blocking_interfaces(self) -> list:
        """Check if enabled RNS interfaces have missing dependencies.

        Delegates to RNSDiagnosticsHandler when available.
        """
        diag = self._get_rns_diagnostics_handler()
        if diag:
            return diag._find_blocking_interfaces()
        return []

    # ------------------------------------------------------------------
    # RNS config path detection
    # ------------------------------------------------------------------

    def _get_rns_config_for_user(self) -> str:
        """Get RNS config directory path appropriate for the current user.

        Returns the EXPLICIT config dir that NomadNet should use via
        --rnsconfig. This MUST match the config that rnsd is using to
        prevent config drift (different identities, stale auth tokens).

        Strategy:
        1. If /etc/reticulum/config exists AND storage is writable -> use it
        2. If storage is NOT writable -> FIX permissions (we run as root)
        3. Never fall back to ~/.reticulum -- that creates config drift

        IMPORTANT: Always return an explicit path. Never return None to
        let RNS use its own resolution, because user-context resolution
        may pick ~/.reticulum instead of /etc/reticulum, causing auth
        mismatches with rnsd.

        Returns:
            Path string to pass to --rnsconfig.
        """
        import stat

        etc_rns = Path('/etc/reticulum')
        etc_config = etc_rns / 'config'

        # If system config exists, always use it -- fix permissions if needed
        if etc_config.is_file():
            storage_dir = etc_rns / 'storage'
            try:
                if storage_dir.exists():
                    mode = storage_dir.stat().st_mode
                    if not (mode & stat.S_IWOTH):
                        # Fix permissions -- we're root (sudo), we can do this.
                        # This prevents NomadNet from falling back to ~/.reticulum
                        # which would cause config drift with rnsd.
                        logger.info(
                            f"/etc/reticulum/storage mode {oct(mode)} missing "
                            f"world-writable bit, fixing to 0o777"
                        )
                        old_umask = os.umask(0)
                        try:
                            storage_dir.chmod(0o777)
                        finally:
                            os.umask(old_umask)
                        # Also fix file permissions inside storage
                        ReticulumPaths._fix_storage_file_permissions()
                else:
                    # Create storage dir with correct permissions
                    old_umask = os.umask(0)
                    try:
                        storage_dir.mkdir(mode=0o777, parents=True, exist_ok=True)
                    finally:
                        os.umask(old_umask)
            except (OSError, PermissionError) as e:
                logger.warning(f"Could not fix /etc/reticulum/storage: {e}")

            return str(etc_rns)

        # No system config -- use default resolution
        # (ReticulumPaths.get_config_dir will find XDG or ~/.reticulum)
        config_dir = ReticulumPaths.get_config_dir()
        return str(config_dir)

    # ------------------------------------------------------------------
    # share_instance pre-flight check
    # ------------------------------------------------------------------

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
        if not self.ctx.dialog.yesno(
            "Fix Directory Ownership",
            f"The following directories are owned by root,\n"
            f"which prevents NomadNet from accessing them:\n\n"
            f"{dir_list}\n\n"
            f"This happened because NomadNet or rnsd was\n"
            f"previously run as root.\n\n"
            f"Fix ownership to user '{sudo_user}'?",
        ):
            # User declined - warn but allow proceeding
            return self.ctx.dialog.yesno(
                "Proceed Anyway?",
                "Ownership was not fixed.\n\n"
                "NomadNet may fail with 'Permission denied' errors.\n\n"
                "Continue anyway?",
            )

        # Fix ownership recursively
        self.ctx.dialog.infobox("Fixing Ownership", f"Changing ownership to {sudo_user}...")

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
                self.ctx.dialog.msgbox(
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
                choices.append(("logs", "View NomadNet Logs"))
                choices.append(("config", "View NomadNet Config"))
                choices.append(("edit", "Edit NomadNet Config"))
                choices.append(("uninstall", "Disable NomadNet"))
            else:
                choices.append(("install", "Install NomadNet"))

            choices.append(("back", "Back"))

            choice = self.ctx.dialog.menu(
                "NomadNet Client",
                f"RNS client with page browser & LXMF messaging:\n\n"
                f"{subtitle}",
                choices,
            )

            if choice is None or choice == "back":
                break

            dispatch = {
                "status": ("NomadNet Status", self._nomadnet_status),
                "textui": ("Launch NomadNet TUI", self._launch_nomadnet_textui),
                "daemon": ("Start NomadNet Daemon", self._launch_nomadnet_daemon),
                "stop": ("Stop NomadNet", self._stop_nomadnet),
                "logs": ("View NomadNet Logs", self._view_nomadnet_logs),
                "config": ("View NomadNet Config", self._view_nomadnet_config),
                "edit": ("Edit NomadNet Config", self._edit_nomadnet_config),
                "install": ("Install NomadNet", self._install_nomadnet),
                "uninstall": ("Disable NomadNet", self._uninstall_nomadnet),
            }
            entry = dispatch.get(choice)
            if entry:
                self.ctx.safe_call(*entry)

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def _nomadnet_status(self):
        """Show comprehensive NomadNet status."""
        clear_screen()
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
            except (subprocess.SubprocessError, OSError) as e:
                logger.debug("NomadNet process check failed: %s", e)
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
        except (subprocess.SubprocessError, OSError) as e:
            logger.debug("rnsd status check failed: %s", e)
            print("  rnsd:      (check failed)")

        self.ctx.wait_for_enter()

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

        # LXMF exclusivity: prevent concurrent LXMF apps
        if not self._ensure_lxmf_exclusive("nomadnet"):
            return

        # Fix ownership of user directories if they were created by root
        # This is a common issue when MeshForge runs with sudo
        if not self._fix_user_directory_ownership():
            return

        # Validate and repair config if needed (e.g., missing [textui] section)
        if not self._validate_nomadnet_config():
            return

        # Check if rnsd is running (NomadNet needs RNS)
        if not self._check_rns_for_nomadnet(nn_path=nn_path):
            return

        # Check if we need to use a specific RNS config path
        # This handles the case where /etc/reticulum exists but isn't writable
        rns_config_path = self._get_rns_config_for_user()

        # Clear screen before launching
        clear_screen()
        print("=== Launching NomadNet ===")
        if rns_config_path:
            print(f"Using RNS config: {rns_config_path}")
        print("Exit NomadNet (Ctrl+Q) to return to MeshForge.\n")

        # When running via sudo, we must run NomadNet as the real user.
        # Just setting HOME is not enough - RPC authentication between
        # NomadNet and rnsd requires matching UIDs.
        sudo_user = os.environ.get('SUDO_USER')

        try:
            # Build base command with optional --rnsconfig
            nn_args = ['--textui']
            if rns_config_path:
                nn_args = ['--rnsconfig', rns_config_path, '--textui']

            # Build command — use wrapper to patch RPC if possible
            cmd = self._get_wrapper_command(nn_path, nn_args)

            if sudo_user and sudo_user != 'root':
                # Run as real user using 'sudo -u' with explicit PATH
                # The -H sets HOME correctly, we pass PATH for pipx binaries
                user_home = get_real_user_home()
                user_path = f"{user_home}/.local/bin:/usr/local/bin:/usr/bin:/bin"
                result = subprocess.run(
                    ['sudo', '-u', sudo_user, '-H',
                     f'PATH={user_path}'] + cmd,
                    timeout=None
                )
            else:
                # Not running via sudo, run directly
                result = subprocess.run(cmd, timeout=None)

            # After NomadNet exits, show status and wait for user
            print()
            if result.returncode != 0:
                was_conn_refused = self._diagnose_nomadnet_error(
                    result.returncode, sudo_user
                )
                if was_conn_refused:
                    # Offer active recovery — restart rnsd (iterative, NOT recursive)
                    try:
                        answer = input(
                            "\nRestart rnsd and retry? [Y/n] "
                        )
                    except (EOFError, KeyboardInterrupt):
                        answer = 'n'
                    if answer.strip().lower() in ('', 'y', 'yes'):
                        if self._restart_rnsd_and_verify_rpc(nn_path=nn_path):
                            print("\nrnsd RPC is now available.")
                            print("Please re-launch NomadNet from the menu.")
                        # Do NOT recursively call _launch_nomadnet_textui()
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

    def _diagnose_nomadnet_error(self, returncode: int, sudo_user: str = None) -> bool:
        """Analyze NomadNet failure and provide helpful diagnostics.

        Returns True if the failure was ConnectionRefusedError (caller
        can offer auto-restart), False otherwise.
        """
        print(f"NomadNet exited with error code {returncode}")
        connection_refused = False

        # Try to read the log file for clues
        user_home = get_real_user_home()
        logfile = user_home / '.nomadnetwork' / 'logfile'

        error_hints = []
        if logfile.exists():
            try:
                import collections
                with open(logfile, 'r') as f:
                    last_lines = list(
                        collections.deque(f, maxlen=50)
                    )

                # Look for known error patterns
                for line in last_lines:
                    if 'ConnectionRefusedError' in line or 'Errno 111' in line:
                        connection_refused = True
                        error_hints.append("RPC connection to rnsd refused (Errno 111)")
                        rnsd_user = self._get_rnsd_user()
                        if not rnsd_user:
                            error_hints.append("rnsd is NOT running — NomadNet cannot connect")
                            error_hints.append("Fix: sudo systemctl start rnsd")
                            error_hints.append("     Then wait a few seconds and retry")
                        else:
                            if rnsd_user == 'root' and sudo_user and sudo_user != 'root':
                                error_hints.append(f"rnsd runs as root, NomadNet as '{sudo_user}'")
                                error_hints.append("Different users = different RNS identities")
                                error_hints.append("Fix: stop rnsd, reconfigure to run as your user")
                            else:
                                error_hints.append("rnsd is running but RPC socket refused connection")
                                error_hints.append("Possible causes:")
                                error_hints.append("  - RNS version mismatch (pipx venv vs system)")
                                error_hints.append("  - Stale auth tokens after rnsd restart")
                                error_hints.append("Verify: rnstatus")
                                error_hints.append("Fix: pipx upgrade nomadnet && sudo systemctl restart rnsd")
                        break
                    elif 'AuthenticationError' in line or 'digest sent was rejected' in line:
                        error_hints.append("RPC authentication failed between NomadNet and rnsd")
                        # Check if rnsd is running as root
                        rnsd_user = self._get_rnsd_user()
                        if rnsd_user == 'root':
                            error_hints.append("rnsd is running as root - identities don't match")
                            error_hints.append("Fix: sudo systemctl stop rnsd")
                            error_hints.append("     Then run rnsd as your user, or reconfigure")
                        elif rnsd_user and rnsd_user != sudo_user:
                            error_hints.append(f"rnsd runs as '{rnsd_user}', you are '{sudo_user}'")
                        else:
                            error_hints.append("Check that rnsd uses the same ~/.reticulum/ identity")
                        break
                    elif 'KeyError' in line and 'textui' in line.lower():
                        error_hints.append("Config missing [textui] section")
                        error_hints.append("Delete ~/.nomadnetwork/config and restart")
                        break
                    elif 'PermissionError' in line or 'Permission denied' in line:
                        if '/etc/reticulum' in line:
                            error_hints.append("Cannot write to /etc/reticulum/ (system config)")
                            error_hints.append("This happens when rnsd was run as root first")
                            error_hints.append("Fix: sudo rm -rf /etc/reticulum")
                            error_hints.append("     (or sudo chown -R $USER /etc/reticulum)")
                        else:
                            error_hints.append("Permission denied accessing files")
                            error_hints.append(f"Check ownership: ls -la ~/.nomadnetwork/")
                        break
                    elif 'meshtastic' in line.lower() and (
                        'critical' in line.lower() or 'requires' in line.lower()
                        or 'no module' in line.lower() or 'modulenotfounderror' in line.lower()
                    ):
                        error_hints.append("rnsd cannot load the meshtastic module")
                        error_hints.append("The Meshtastic_Interface.py plugin requires meshtastic")
                        error_hints.append(
                            "Fix: sudo pip3 install --break-system-packages "
                            "--ignore-installed meshtastic"
                        )
                        error_hints.append("Then: sudo systemctl restart rnsd")
                        break
                    elif 'TypeError' in line and 'list indices' in line:
                        error_hints.append(
                            "NomadNet crash: interface stats returned wrong "
                            "type (list instead of dict)"
                        )
                        error_hints.append(
                            "The MeshForge NomadNet wrapper needs updating"
                        )
                        error_hints.append(
                            "Fix: Relaunch NomadNet from MeshForge "
                            "(wrapper auto-updates)"
                        )
                        break
                    elif 'ModuleNotFoundError' in line or 'ImportError' in line:
                        error_hints.append("Missing Python dependencies")
                        error_hints.append("Try: pipx reinstall nomadnet")
                        break
            except (OSError, PermissionError):
                pass

        # If no NomadNet-specific error found, check rnsd journal for clues.
        # NomadNet fails when rnsd is down due to meshtastic module issue.
        if not error_hints:
            try:
                journal_r = subprocess.run(
                    ['journalctl', '-u', 'rnsd', '-n', '20', '--no-pager', '-q'],
                    capture_output=True, text=True, timeout=5
                )
                journal_text = journal_r.stdout.lower()
                if 'meshtastic' in journal_text and (
                    'critical' in journal_text or 'module' in journal_text
                ):
                    error_hints.append("rnsd crashed because the meshtastic module is missing")
                    error_hints.append("NomadNet depends on rnsd for network access")
                    error_hints.append(
                        "Fix: sudo pip3 install --break-system-packages "
                        "--ignore-installed meshtastic"
                    )
                    error_hints.append("Then: sudo systemctl restart rnsd")
                elif 'status=255' in journal_text or 'exception' in journal_text:
                    error_hints.append("rnsd is crashing (exit code 255)")
                    error_hints.append("Check: sudo journalctl -u rnsd -n 30")
            except (subprocess.SubprocessError, OSError):
                pass

        if error_hints:
            print("\nDiagnosis:")
            for hint in error_hints:
                print(f"  - {hint}")
        else:
            # No known pattern matched — show the log tail directly
            # so the user doesn't have to manually cat the file.
            print(f"\nNo known error pattern detected.")
            if logfile.exists():
                try:
                    import collections
                    with open(logfile, 'r') as f:
                        tail = list(collections.deque(f, maxlen=15))
                    if tail:
                        print(f"\n--- Last {len(tail)} lines of {logfile} ---")
                        for line in tail:
                            print(f"  {line.rstrip()}")
                        print("---")
                except OSError:
                    print(f"\nCheck logs: cat {logfile}")
            else:
                print(f"\nNo logfile found at: {logfile}")
            print(f"  journalctl --user -u nomadnet -n 50")

        return connection_refused

    # ------------------------------------------------------------------
    # Log viewer
    # ------------------------------------------------------------------

    def _view_nomadnet_logs(self):
        """View NomadNet logfile (works in daemon and textui mode).

        NomadNet writes to ~/.nomadnetwork/logfile independently of
        stdout/stderr, so this works regardless of launch mode.
        """
        import collections

        user_home = get_real_user_home()
        logfile = user_home / '.nomadnetwork' / 'logfile'

        if not logfile.exists():
            self.ctx.dialog.msgbox(
                "No Logs",
                "NomadNet logfile not found yet.\n\n"
                f"Expected at: {logfile}\n\n"
                "Logs are created when NomadNet runs.",
            )
            return

        clear_screen()

        # Offer view options
        choices = [
            ("last50", "Last 50 lines"),
            ("last200", "Last 200 lines"),
            ("errors", "Errors only (last 200 lines)"),
            ("follow", "Follow live (Ctrl+C to stop)"),
            ("back", "Back"),
        ]

        choice = self.ctx.dialog.menu(
            "NomadNet Logs",
            f"Logfile: {logfile}",
            choices,
        )

        if choice is None or choice == "back":
            return

        if choice == "follow":
            clear_screen()
            print(f"=== NomadNet log — {logfile} "
                  f"(Ctrl+C to stop) ===\n")
            try:
                subprocess.run(
                    ['tail', '-f', '-n', '30', str(logfile)],
                    timeout=None
                )
            except KeyboardInterrupt:
                pass
            return

        # Read the logfile tail
        if choice == "last200":
            maxlines = 200
        else:
            maxlines = 50  # last50 and errors both read 200

        clear_screen()

        try:
            with open(logfile, 'r') as f:
                lines = list(collections.deque(
                    f, maxlen=max(maxlines, 200)
                ))

            if choice == "errors":
                error_patterns = [
                    'Error', 'Exception', 'CRITICAL',
                    'WARNING', 'AuthenticationError',
                    'PermissionError', 'Traceback',
                ]
                lines = [
                    line for line in lines
                    if any(p in line for p in error_patterns)
                ]
                print(f"=== NomadNet errors "
                      f"({len(lines)} found) ===\n")
            else:
                lines = lines[-maxlines:]
                print(f"=== NomadNet log (last "
                      f"{len(lines)} lines) ===\n")

            if lines:
                for line in lines:
                    print(line.rstrip())
            else:
                print("  (no matching lines)")

        except PermissionError:
            print(f"Cannot read {logfile} — permission denied")
        except OSError as e:
            print(f"Error reading logfile: {e}")

        self.ctx.wait_for_enter()

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
            self.ctx.dialog.msgbox("Already Running", "NomadNet is already running.")
            return

        # LXMF exclusivity: prevent concurrent LXMF apps
        if not self._ensure_lxmf_exclusive("nomadnet"):
            return

        # Fix ownership of user directories if they were created by root
        if not self._fix_user_directory_ownership():
            return

        if not self._check_rns_for_nomadnet(nn_path=nn_path):
            return

        # Get RNS config path (must match rnsd to prevent config drift)
        rns_config_path = self._get_rns_config_for_user()

        if not self.ctx.dialog.yesno(
            "Start NomadNet Daemon",
            "Start NomadNet in daemon mode (background)?\n\n"
            "This will:\n"
            "  - Announce your node on the RNS network\n"
            "  - Accept and propagate LXMF messages\n"
            "  - Serve node pages (if enabled in config)\n\n"
            "NomadNet will run until stopped.",
        ):
            return

        self.ctx.dialog.infobox("Starting", "Starting NomadNet daemon...")

        # Build command - run as real user if we're under sudo
        # This ensures NomadNet uses ~/.nomadnetwork/config, not /root/.nomadnetwork/config
        sudo_user = os.environ.get('SUDO_USER')

        # Build base args with optional --rnsconfig
        nn_args = ['--daemon']
        if rns_config_path:
            nn_args = ['--rnsconfig', rns_config_path, '--daemon']

        # Build command — use wrapper to patch RPC if possible
        base_cmd = self._get_wrapper_command(nn_path, nn_args)

        if sudo_user and sudo_user != 'root':
            # Run as real user with -H to set HOME correctly
            # Using -H instead of -i avoids running shell profiles which can interfere
            cmd = ['sudo', '-H', '-u', sudo_user] + base_cmd
        else:
            cmd = base_cmd

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
                self.ctx.dialog.msgbox(
                    "Daemon Started",
                    "NomadNet daemon is running in the background.\n\n"
                    "Your node is now announcing on the RNS network.\n"
                    "Use 'Stop NomadNet' to shut it down.",
                )
            else:
                # Check log for specific errors to provide better diagnosis
                user_home = get_real_user_home()
                logfile = user_home / '.nomadnetwork' / 'logfile'
                conn_refused = False
                if logfile.exists():
                    try:
                        import collections
                        with open(logfile, 'r') as f:
                            last_lines = list(
                                collections.deque(f, maxlen=10)
                            )
                        for line in last_lines:
                            if 'ConnectionRefusedError' in line or 'Errno 111' in line:
                                conn_refused = True
                                break
                    except OSError:
                        pass

                if conn_refused:
                    self.ctx.dialog.msgbox(
                        "Start Failed — Connection Refused",
                        "NomadNet daemon crashed: ConnectionRefusedError.\n\n"
                        "rnsd RPC socket is not accepting connections.\n\n"
                        "Possible causes:\n"
                        "  - rnsd not fully initialized yet\n"
                        "  - RNS version mismatch (pipx vs system)\n"
                        "  - User/identity mismatch with rnsd\n\n"
                        "Try: sudo systemctl restart rnsd\n"
                        "     Then wait 20s and re-launch NomadNet.",
                    )
                else:
                    self.ctx.dialog.msgbox(
                        "Start Failed",
                        "NomadNet daemon failed to start.\n\n"
                        "Check logs: ~/.nomadnetwork/logfile\n"
                        "Or run manually: nomadnet --daemon --console",
                    )
        except FileNotFoundError:
            self.ctx.dialog.msgbox("Error", f"NomadNet binary not found at: {nn_path}")
        except Exception as e:
            self.ctx.dialog.msgbox("Error", f"Failed to start NomadNet daemon:\n{e}")

    # ------------------------------------------------------------------
    # Stop
    # ------------------------------------------------------------------

    def _stop_nomadnet(self):
        """Stop running NomadNet process(es)."""
        if not self._is_nomadnet_running():
            self.ctx.dialog.msgbox("Not Running", "NomadNet is not currently running.")
            return

        if not self.ctx.dialog.yesno(
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
                self.ctx.dialog.msgbox("Stopped", "NomadNet has been stopped.")
            else:
                self.ctx.dialog.msgbox("Warning", "NomadNet may still be running.\nTry: sudo pkill -9 -f nomadnet")
        except Exception as e:
            self.ctx.dialog.msgbox("Error", f"Failed to stop NomadNet:\n{e}")

    # ------------------------------------------------------------------
    # Uninstall (stop + disable)
    # ------------------------------------------------------------------

    def _uninstall_nomadnet(self):
        """Stop NomadNet and leave it disabled.

        Does not remove files -- just stops the process and shows how
        to reinstall later if desired.
        """
        if not self.ctx.dialog.yesno(
            "Disable NomadNet",
            "Stop NomadNet and disable it?\n\n"
            "This will:\n"
            "  - Stop NomadNet if running\n"
            "  - Leave files in place\n\n"
            "Reinstall later with: pipx install nomadnet\n\n"
            "Disable now?",
        ):
            return

        clear_screen()
        print("=== Disabling NomadNet ===\n")

        # Stop running processes
        if self._is_nomadnet_running():
            print("Stopping NomadNet...")
            try:
                subprocess.run(
                    ['pkill', '-f', 'bin/nomadnet'],
                    capture_output=True, timeout=10,
                )
                time.sleep(2)
            except (subprocess.SubprocessError, OSError):
                pass

            if self._is_nomadnet_running():
                try:
                    subprocess.run(
                        ['pkill', '-9', '-f', 'bin/nomadnet'],
                        capture_output=True, timeout=10,
                    )
                    time.sleep(1)
                except (subprocess.SubprocessError, OSError):
                    pass

        if self._is_nomadnet_running():
            print("NomadNet may still be running.")
            print("Try: sudo pkill -9 -f nomadnet")
        else:
            print("NomadNet stopped.")

        user_home = get_real_user_home()
        print(f"\nConfig remains at: {user_home}/.nomadnetwork/")
        print("Reinstall: pipx install nomadnet")

        self.ctx.wait_for_enter()

    # ------------------------------------------------------------------
    # Config management
    # ------------------------------------------------------------------

    def _view_nomadnet_config(self):
        """View NomadNet configuration."""
        clear_screen()
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

        self.ctx.wait_for_enter()

    def _edit_nomadnet_config(self):
        """Edit NomadNet config with available editor."""
        config_path = self._get_nomadnet_config_path()

        if not config_path or not config_path.exists():
            if self.ctx.dialog.yesno(
                "No Config Found",
                "NomadNet config doesn't exist yet.\n\n"
                "It is created automatically on first run.\n"
                "Launch NomadNet once to generate it?\n\n"
                "(It will create the config and exit.)",
            ):
                nn_path = self._find_nomadnet_binary()
                if nn_path:
                    self.ctx.dialog.infobox("Generating Config", "Running NomadNet briefly to generate config...")
                    try:
                        # Check if we need to use a specific RNS config path
                        rns_config_path = self._get_rns_config_for_user()

                        # Build command - run as real user if we're under sudo
                        # This ensures config is created with correct ownership
                        sudo_user = os.environ.get('SUDO_USER')

                        # Build base args with optional --rnsconfig
                        nn_args = ['--daemon']
                        if rns_config_path:
                            nn_args = ['--rnsconfig', rns_config_path, '--daemon']

                        if sudo_user and sudo_user != 'root':
                            # Using -H instead of -i to set HOME without shell profiles
                            cmd = ['sudo', '-H', '-u', sudo_user, nn_path] + nn_args
                        else:
                            cmd = [nn_path] + nn_args

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
                            self.ctx.dialog.msgbox(
                                "Config Generated",
                                f"Config created at:\n  {config_path}\n\n"
                                f"Opening editor...",
                            )
                        else:
                            self.ctx.dialog.msgbox(
                                "Config Not Found",
                                "NomadNet ran but config was not generated.\n"
                                "Check: ~/.nomadnetwork/config",
                            )
                            return
                    except FileNotFoundError:
                        self.ctx.dialog.msgbox("Error", f"NomadNet not found at: {nn_path}")
                        return
                    except Exception as e:
                        self.ctx.dialog.msgbox("Error", f"Failed to generate config:\n{e}")
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
            self.ctx.dialog.msgbox("Error", "No text editor found (nano, vim, vi)")
            return

        subprocess.run([editor, str(config_path)], timeout=None)

    # ------------------------------------------------------------------
    # Install
    # ------------------------------------------------------------------

    def _install_nomadnet(self):
        """Install NomadNet via pipx (isolated environment)."""
        if self._is_nomadnet_installed():
            self.ctx.dialog.msgbox("Already Installed", "NomadNet is already installed.")
            return

        if not self.ctx.dialog.yesno(
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

        clear_screen()
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
                    self.ctx.wait_for_enter()
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
            self.ctx.wait_for_enter()
        except (EOFError, KeyboardInterrupt):
            pass

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _find_pipx(self) -> str:
        """Find pipx binary, checking PATH and common locations.

        pipx is often installed to ~/.local/bin which may not be in
        the current shell's PATH (especially under sudo).
        Returns the path string, or None if not found.
        """
        pipx_path = shutil.which('pipx')
        if pipx_path:
            return pipx_path
        # Check common locations
        for candidate in [
            get_real_user_home() / '.local' / 'bin' / 'pipx',
            Path('/usr/bin/pipx'),
            Path('/usr/local/bin/pipx'),
        ]:
            if candidate.exists():
                return str(candidate)
        return None

    def _upgrade_nomadnet(self) -> bool:
        """Upgrade NomadNet and its RNS dependency to fix version mismatches.

        Strategy:
        1. pipx upgrade nomadnet (upgrades NomadNet + deps)
        2. If already at latest, also upgrade RNS inside the venv
           (pipx runpip nomadnet -- install --upgrade rns)
        3. Show version comparison between venv RNS and system RNS

        Returns True if upgrade succeeded, False otherwise.
        """
        pipx_path = self._find_pipx()
        if not pipx_path:
            self.ctx.dialog.msgbox(
                "pipx Not Found",
                "Cannot find pipx to upgrade NomadNet.\n\n"
                "Install pipx first:\n"
                "  sudo apt install pipx\n\n"
                "Then upgrade:\n"
                "  pipx upgrade nomadnet",
            )
            return False

        sudo_user = os.environ.get('SUDO_USER')

        def _run_pipx(args, timeout_sec=120):
            """Run pipx command as real user."""
            if sudo_user and sudo_user != 'root':
                cmd = ['sudo', '-H', '-u', sudo_user, pipx_path] + args
            else:
                cmd = [pipx_path] + args
            return subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout_sec,
            )

        # Step 1: Show RNS version comparison for diagnostics
        self.ctx.dialog.infobox(
            "Checking Versions",
            "Comparing RNS versions (system vs NomadNet venv)...",
        )
        versions = self._get_rns_version_info(pipx_path, sudo_user)

        # Step 2: Upgrade NomadNet package
        self.ctx.dialog.infobox(
            "Upgrading NomadNet",
            "Running pipx upgrade nomadnet...",
        )
        try:
            result = _run_pipx(['upgrade', 'nomadnet'])
            already_latest = 'already at latest' in (result.stdout + result.stderr).lower()
        except (subprocess.SubprocessError, OSError) as e:
            self.ctx.dialog.msgbox("Error", f"pipx upgrade failed: {e}")
            return False

        # Step 3: Also upgrade RNS inside the venv
        # pipx upgrade only upgrades the package itself; if NomadNet
        # pins an older RNS, the venv RNS stays stale.
        self.ctx.dialog.infobox(
            "Upgrading RNS",
            "Upgrading RNS library inside NomadNet venv...",
        )
        try:
            rns_result = _run_pipx(
                ['runpip', 'nomadnet', '--', 'install', '--upgrade', 'rns'],
            )
            rns_output = (rns_result.stdout + rns_result.stderr).strip()
            rns_upgraded = rns_result.returncode == 0
        except (subprocess.SubprocessError, OSError) as e:
            logger.warning("Failed to upgrade RNS in venv: %s", e)
            rns_output = str(e)
            rns_upgraded = False

        # Step 4: Show results
        new_versions = self._get_rns_version_info(pipx_path, sudo_user)
        summary_lines = []
        if versions:
            summary_lines.append(f"Before: {versions}")
        if new_versions:
            summary_lines.append(f"After:  {new_versions}")
        if rns_upgraded:
            summary_lines.append("\nRNS upgraded in NomadNet venv.")
        else:
            summary_lines.append(f"\nRNS upgrade issue:\n{rns_output[:150]}")

        self.ctx.dialog.msgbox(
            "Upgrade Complete",
            "\n".join(summary_lines),
        )
        return rns_upgraded

    def _get_rns_version_info(self, pipx_path: str, sudo_user: str) -> str:
        """Get RNS version comparison: system vs NomadNet venv.

        Returns a short summary string, or empty string on failure.
        """
        sys_ver = ''
        venv_ver = ''

        # System RNS version
        try:
            r = subprocess.run(
                ['pip3', 'show', 'rns'],
                capture_output=True, text=True, timeout=10,
            )
            for line in r.stdout.splitlines():
                if line.startswith('Version:'):
                    sys_ver = line.split(':', 1)[1].strip()
                    break
        except (subprocess.SubprocessError, OSError):
            pass

        # Venv RNS version
        try:
            if sudo_user and sudo_user != 'root':
                cmd = ['sudo', '-H', '-u', sudo_user, pipx_path,
                       'runpip', 'nomadnet', '--', 'show', 'rns']
            else:
                cmd = [pipx_path, 'runpip', 'nomadnet', '--', 'show', 'rns']
            r = subprocess.run(
                cmd, capture_output=True, text=True, timeout=10,
            )
            for line in r.stdout.splitlines():
                if line.startswith('Version:'):
                    venv_ver = line.split(':', 1)[1].strip()
                    break
        except (subprocess.SubprocessError, OSError):
            pass

        if sys_ver or venv_ver:
            match = "MATCH" if sys_ver == venv_ver else "MISMATCH"
            return f"system={sys_ver or '?'} venv={venv_ver or '?'} ({match})"
        return ''

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
        except (subprocess.SubprocessError, OSError) as e:
            logger.debug("NomadNet running check failed: %s", e)
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
            self.ctx.dialog.msgbox(
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

    # ------------------------------------------------------------------
    # NomadNet wrapper (monkey-patch broken RPC)
    # ------------------------------------------------------------------

    _WRAPPER_VERSION = "5"  # bump to force re-creation

    def _create_nomadnet_wrapper(self) -> Optional[Path]:
        """Create a wrapper script that patches get_interface_stats.

        NomadNet's TextUI.MainDisplay.__init__() calls
        RNS.Reticulum.get_interface_stats() which uses the RPC management
        socket (multiprocessing.connection). When rnsd's RPC listener is
        broken, this crashes NomadNet with ConnectionRefusedError.

        The wrapper monkey-patches get_interface_stats to catch the error
        and return an empty stats dict (graceful degradation — no stats shown).

        Returns the wrapper path, or None if creation failed.
        """
        user_home = get_real_user_home()
        wrapper_dir = user_home / '.config' / 'meshforge'
        wrapper_path = wrapper_dir / 'nomadnet_wrapper.py'

        wrapper_content = '''\
"""MeshForge NomadNet wrapper — patches RPC ConnectionRefusedError.

Version: {version}

NomadNet crashes when rnsd RPC management socket is not listening.
This wrapper patches RNS.Reticulum.get_interface_stats to catch the
error gracefully so NomadNet can still run (without interface stats).
"""
import sys
import RNS

_orig_get_interface_stats = RNS.Reticulum.get_interface_stats

_FALLBACK = dict(interfaces=[])

def _safe_get_interface_stats(self):
    try:
        result = _orig_get_interface_stats(self)
    except (ConnectionRefusedError, BrokenPipeError, TypeError, KeyError, OSError):
        return _FALLBACK
    if not isinstance(result, dict) or 'interfaces' not in result:
        return _FALLBACK
    return result

RNS.Reticulum.get_interface_stats = _safe_get_interface_stats

from nomadnet.nomadnet import main
sys.exit(main())
'''.format(version=self._WRAPPER_VERSION)

        # Check if wrapper already exists with correct version
        version_marker = f"Version: {self._WRAPPER_VERSION}"
        if wrapper_path.exists():
            try:
                existing = wrapper_path.read_text()
                if version_marker in existing:
                    return wrapper_path
            except OSError:
                pass

        # Create/update the wrapper
        try:
            wrapper_dir.mkdir(parents=True, exist_ok=True)
            wrapper_path.write_text(wrapper_content)
            logger.debug("Created NomadNet wrapper at %s", wrapper_path)

            # Fix ownership if running under sudo
            sudo_user = os.environ.get('SUDO_USER')
            if sudo_user and sudo_user != 'root':
                import pwd
                try:
                    pw = pwd.getpwnam(sudo_user)
                    os.chown(wrapper_dir, pw.pw_uid, pw.pw_gid)
                    os.chown(wrapper_path, pw.pw_uid, pw.pw_gid)
                except (KeyError, OSError) as e:
                    logger.debug("Could not chown wrapper: %s", e)

            return wrapper_path
        except OSError as e:
            logger.warning("Failed to create NomadNet wrapper: %s", e)
            return None

    def _get_wrapper_command(self, nn_path: str, nn_args: list) -> list:
        """Build launch command using wrapper if possible.

        Returns [venv_python, wrapper, ...args] if wrapper is available,
        otherwise [nn_path, ...args] as fallback.
        """
        venv_python = self._get_nomadnet_venv_python(nn_path)
        if not venv_python:
            return [nn_path] + nn_args

        wrapper_path = self._create_nomadnet_wrapper()
        if not wrapper_path:
            return [nn_path] + nn_args

        # sys.argv[0] will be the wrapper path, remaining args are
        # forwarded to NomadNet's main() via sys.argv
        return [venv_python, str(wrapper_path)] + nn_args

    # RNS prerequisite checks provided by NomadNetRNSChecksMixin:
    # _check_rns_for_nomadnet, _validate_nomadnet_config
