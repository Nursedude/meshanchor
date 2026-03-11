"""RNS prerequisite checks and NomadNet config validation.

Validates RNS/rnsd availability, permissions, and user matching
before launching NomadNet. Also validates NomadNet config for
required sections.

Extracted from nomadnet.py for file size compliance (CLAUDE.md #6).
"""

import logging
import os
import stat
import subprocess
import time
from pathlib import Path

from utils.paths import ReticulumPaths

from utils.safe_import import safe_import

stop_service, _HAS_SERVICE_CHECK = safe_import(
    'utils.service_check', 'stop_service'
)
restart_service, _HAS_RESTART = safe_import(
    'utils.service_check', 'restart_service'
)

logger = logging.getLogger(__name__)


class NomadNetRNSChecksMixin:
    """Mixin providing RNS prerequisite checks for NomadNet.

    Expects the host class to provide:
        self.ctx.dialog   — DialogBackend for TUI dialogs
        self._get_rnsd_user() -> Optional[str]
        self._wait_for_rns_port(max_wait) -> bool
        self._find_blocking_interfaces() -> list
        self._fix_rnsd_user(user) -> bool
        self._get_nomadnet_config_path() -> Optional[Path]
    """

    def _check_rns_for_nomadnet(self) -> bool:
        """Check that RNS/rnsd is available and properly configured.

        Checks:
        1. Is /etc/reticulum blocking user access?
        2. Is rnsd running?
        3. Is rnsd running as root? (causes RPC auth failures with user NomadNet)

        Returns True if OK to proceed, False if user cancelled.
        """
        sudo_user = os.environ.get('SUDO_USER')

        # Check for /etc/reticulum permission issues first.
        # IMPORTANT: MeshForge runs as root (sudo) but NomadNet launches as
        # the real user. Check permissions for the REAL USER, not root.
        etc_rns = Path('/etc/reticulum')
        if etc_rns.exists():
            storage_dir = etc_rns / 'storage'
            can_write = False
            try:
                if storage_dir.exists():
                    if sudo_user and sudo_user != 'root':
                        # Running via sudo -- check mode bits for real user
                        mode = storage_dir.stat().st_mode
                        can_write = bool(mode & stat.S_IWOTH)
                    else:
                        # Not running via sudo -- direct write test is valid
                        test_file = storage_dir / '.write_test'
                        try:
                            test_file.touch()
                            test_file.unlink()
                            can_write = True
                        except (OSError, PermissionError):
                            pass
                else:
                    try:
                        storage_dir.mkdir(parents=True, exist_ok=True)
                        can_write = True
                    except (OSError, PermissionError):
                        pass
            except (OSError, ValueError) as e:
                logger.debug("RNS storage dir check failed: %s", e)

            if not can_write:
                # /etc/reticulum storage not writable -- fix it immediately.
                # We're running as root (sudo), so we can fix permissions.
                # NEVER fall back to ~/.reticulum -- that creates config drift
                # (different identity/auth tokens than rnsd -> auth failures).
                target_user = sudo_user if sudo_user and sudo_user != 'root' else 'current user'
                logger.info(
                    f"/etc/reticulum/storage not writable by {target_user}, "
                    "fixing permissions to 0o777"
                )
                try:
                    old_umask = os.umask(0)
                    try:
                        storage_dir.chmod(0o777)
                        # Also fix subdirectories and files
                        ReticulumPaths._fix_storage_file_permissions()
                    finally:
                        os.umask(old_umask)
                    self.ctx.dialog.msgbox(
                        "Storage Permissions Fixed",
                        f"/etc/reticulum/storage/ permissions have been fixed.\n\n"
                        f"NomadNet will use the system config (same as rnsd).",
                    )
                except (OSError, PermissionError) as e:
                    self.ctx.dialog.msgbox(
                        "Permission Fix Failed",
                        f"Could not fix /etc/reticulum/storage permissions:\n"
                        f"  {e}\n\n"
                        f"Try manually:\n"
                        f"  sudo chmod 777 /etc/reticulum/storage"
                    )
                    return False

        # Check if rnsd is running and get its user
        rnsd_user = self._get_rnsd_user()

        if not rnsd_user:
            # rnsd not running -- warn but allow proceeding
            return self.ctx.dialog.yesno(
                "rnsd Not Running",
                "The RNS daemon (rnsd) is not running.\n\n"
                "NomadNet can start its own RNS instance,\n"
                "but for Meshtastic bridging you should run rnsd\n"
                "with share_instance = Yes in the Reticulum config.\n\n"
                "Continue anyway?",
            )

        # rnsd is running -- wait for it to bind port 37428.
        # rnsd initializes crypto and interfaces BEFORE binding the shared
        # instance port, so we give it time before declaring failure.
        self.ctx.dialog.infobox(
            "Checking rnsd",
            "Verifying rnsd shared instance (port 37428)...",
        )
        port_listening = self._wait_for_rns_port(max_wait=10)

        if not port_listening:
            # rnsd running but not listening -- check for blocking interfaces
            blocking = []
            try:
                blocking = self._find_blocking_interfaces()
            except Exception as e:
                logger.debug("Blocking interface check failed: %s", e)

            if blocking:
                lines = ["rnsd is running but NOT listening on port 37428.\n"]
                lines.append("Cause: an enabled interface is blocking startup:\n")
                for iface_name, reason, fix in blocking:
                    lines.append(f"  [{iface_name}] {reason}")
                    lines.append(f"  Fix: {fix}\n")
                lines.append("NomadNet will fail to connect until this is resolved.")
                return self.ctx.dialog.yesno(
                    "rnsd Not Ready",
                    "\n".join(lines) + "\n\nContinue anyway?",
                )
            else:
                # No blocking interfaces found -- may still be initializing
                return self.ctx.dialog.yesno(
                    "rnsd Not Ready",
                    "rnsd is running but not yet listening on port 37428.\n\n"
                    "It may still be initializing (crypto, interfaces).\n"
                    "NomadNet may fail to connect.\n\n"
                    "Continue anyway?",
                )

        # Brief stability check — catch rnsd crash-looping after restart.
        # If the user just restarted rnsd with a bad config, it may pass
        # the initial check but crash moments later.
        time.sleep(1)
        rnsd_still_running = self._get_rnsd_user()
        if not rnsd_still_running:
            self.ctx.dialog.msgbox(
                "rnsd Crashed",
                "rnsd was running but crashed shortly after.\n\n"
                "This often happens after a config change that has\n"
                "syntax errors or missing dependencies.\n\n"
                "Check: sudo journalctl -u rnsd -n 30\n\n"
                "Fix the config issue and restart rnsd before\n"
                "launching NomadNet.",
            )
            return False

        # Check for user mismatches BEFORE RPC — user mismatch is the
        # #1 cause of RPC failure (different identities = different auth keys).
        # Fixing the user first avoids a misleading "RPC not ready" warning.
        current_uid = os.getuid()
        we_are_root = current_uid == 0

        if rnsd_user == 'root' and sudo_user and sudo_user != 'root':
            # Case 1: rnsd as root, NomadNet wants to run as user
            choice = self.ctx.dialog.menu(
                "rnsd Running as Root",
                "rnsd is running as root, but NomadNet needs to\n"
                "run as your user for RPC authentication.\n\n"
                "Different users = different RNS identities = auth failure.\n\n"
                "How do you want to fix this?",
                [
                    ("fix", f"Fix rnsd to run as {sudo_user} (recommended)"),
                    ("stop", "Stop rnsd (NomadNet will use its own RNS)"),
                    ("cancel", "Cancel"),
                ],
            )

            if choice == "fix":
                return self._fix_rnsd_user(sudo_user)
            elif choice == "stop":
                # Just stop rnsd
                self.ctx.dialog.infobox("Stopping rnsd", "Stopping rnsd service...")
                try:
                    stop_service('rnsd')
                    subprocess.run(['pkill', '-f', 'rnsd'], capture_output=True, timeout=5)
                    time.sleep(1)
                    self.ctx.dialog.msgbox(
                        "rnsd Stopped",
                        "rnsd has been stopped.\n\n"
                        "NomadNet will start its own RNS instance.",
                    )
                    return True
                except Exception as e:
                    self.ctx.dialog.msgbox("Stop Failed", f"Could not stop rnsd: {e}")
                    return False
            else:
                return False  # User cancelled

        elif we_are_root and rnsd_user and rnsd_user != 'root' and not sudo_user:
            # Case 2: We're root but SUDO_USER not set, rnsd runs as user
            # This is a fresh install issue - NomadNet would run as root
            # Store the rnsd user so we can run NomadNet as that user
            choice = self.ctx.dialog.menu(
                "User Mismatch Detected",
                f"rnsd is running as '{rnsd_user}', but SUDO_USER is not set.\n\n"
                f"NomadNet would run as root, causing RPC auth failure.\n\n"
                f"Different users = different RNS identities = auth failure.\n\n"
                f"How do you want to proceed?",
                [
                    ("run_as_user", f"Run NomadNet as '{rnsd_user}' (recommended)"),
                    ("stop", "Stop rnsd (NomadNet will use its own RNS)"),
                    ("cancel", "Cancel"),
                ],
            )

            if choice == "run_as_user":
                # Set SUDO_USER temporarily so _launch_nomadnet_textui uses it
                os.environ['SUDO_USER'] = rnsd_user
                self.ctx.dialog.msgbox(
                    "User Set",
                    f"NomadNet will run as '{rnsd_user}'.\n\n"
                    f"This matches the user running rnsd.",
                )
                return True
            elif choice == "stop":
                self.ctx.dialog.infobox("Stopping rnsd", "Stopping rnsd service...")
                try:
                    stop_service('rnsd')
                    subprocess.run(['pkill', '-f', 'rnsd'], capture_output=True, timeout=5)
                    time.sleep(1)
                    self.ctx.dialog.msgbox(
                        "rnsd Stopped",
                        "rnsd has been stopped.\n\n"
                        "NomadNet will start its own RNS instance.",
                    )
                    return True
                except Exception as e:
                    self.ctx.dialog.msgbox("Stop Failed", f"Could not stop rnsd: {e}")
                    return False
            else:
                return False  # User cancelled

        # Users match — now verify RPC connectivity.
        # NomadNet's TextUI calls get_interface_stats() via RPC on startup.
        # If rnsd's RPC socket isn't ready, NomadNet crashes with
        # ConnectionRefusedError [Errno 111].
        rpc_ok = self._check_rnsd_rpc(sudo_user)
        if not rpc_ok:
            return self._handle_rpc_failure(sudo_user)

        # rnsd running as correct user with working RPC
        return True

    def _handle_rpc_failure(self, sudo_user: str = None) -> bool:
        """Handle RPC check failure with diagnosis and auto-restart.

        Called when _check_rnsd_rpc returns False and user mismatch has
        already been ruled out. Determines WHY RPC is failing and offers
        to fix it.

        Returns:
            True to proceed with NomadNet launch, False to abort.
        """
        uptime = self._get_rnsd_uptime()

        # If rnsd just started, wait and retry — RPC listener starts
        # after interfaces are initialized
        if uptime is not None and uptime < 10:
            self.ctx.dialog.infobox(
                "Waiting for rnsd RPC",
                f"rnsd started {uptime}s ago — RPC may still be initializing.\n"
                "Waiting up to 10 seconds...",
            )
            # Wait in 2-second increments, checking RPC each time
            for _ in range(5):
                time.sleep(2)
                if self._check_rnsd_rpc(sudo_user):
                    self.ctx.dialog.msgbox(
                        "RPC Ready",
                        "rnsd RPC is now accepting connections.\n\n"
                        "NomadNet should start normally.",
                    )
                    return True

        # RPC still failing — offer to restart rnsd
        uptime_info = f" (uptime: {uptime}s)" if uptime is not None else ""
        choice = self.ctx.dialog.menu(
            "rnsd RPC Not Ready",
            f"rnsd is running{uptime_info} and listening on port 37428,\n"
            "but its RPC socket is not accepting connections.\n\n"
            "NomadNet needs RPC to query interface stats on startup.\n"
            "Without it, NomadNet will crash with:\n"
            "  ConnectionRefusedError: [Errno 111]\n\n"
            "How do you want to proceed?",
            [
                ("restart", "Restart rnsd and retry (recommended)"),
                ("continue", "Continue anyway (may crash)"),
                ("cancel", "Cancel"),
            ],
        )

        if choice == "restart":
            return self._restart_rnsd_and_verify_rpc(sudo_user)
        elif choice == "continue":
            return True
        else:
            return False

    def _restart_rnsd_and_verify_rpc(self, sudo_user: str = None) -> bool:
        """Restart rnsd and verify RPC becomes available.

        Returns:
            True if RPC is working after restart, or user wants to
            continue anyway. False if restart failed or user cancelled.
        """
        self.ctx.dialog.infobox(
            "Restarting rnsd",
            "Restarting rnsd service...",
        )

        try:
            if restart_service:
                ok, msg = restart_service('rnsd')
                if not ok:
                    self.ctx.dialog.msgbox(
                        "Restart Failed",
                        f"Could not restart rnsd:\n  {msg}\n\n"
                        "Try manually: sudo systemctl restart rnsd",
                    )
                    return False
            else:
                # Fallback if service_check not available
                subprocess.run(
                    ['sudo', 'systemctl', 'restart', 'rnsd'],
                    capture_output=True, timeout=30
                )
        except (subprocess.SubprocessError, OSError) as e:
            self.ctx.dialog.msgbox(
                "Restart Failed",
                f"Could not restart rnsd:\n  {e}",
            )
            return False

        # Wait for port to come up
        self.ctx.dialog.infobox(
            "Waiting for rnsd",
            "Waiting for rnsd to initialize (port 37428)...",
        )
        if not self._wait_for_rns_port(max_wait=15):
            self.ctx.dialog.msgbox(
                "rnsd Not Ready",
                "rnsd restarted but port 37428 not listening after 15s.\n\n"
                "Check: sudo journalctl -u rnsd -n 30",
            )
            return False

        # Wait a moment for RPC to come up after port
        time.sleep(2)

        # Verify RPC
        if self._check_rnsd_rpc(sudo_user):
            self.ctx.dialog.msgbox(
                "rnsd RPC Ready",
                "rnsd has been restarted and RPC is working.\n\n"
                "NomadNet should start normally.",
            )
            return True

        # RPC still not working after restart
        return self.ctx.dialog.yesno(
            "RPC Still Not Ready",
            "rnsd was restarted but RPC is still not responding.\n\n"
            "This may indicate a deeper issue.\n"
            "Check: rnstatus\n"
            "Check: sudo journalctl -u rnsd -n 30\n\n"
            "Continue anyway?",
        )

    def _get_rnsd_uptime(self) -> int | None:
        """Get rnsd process uptime in seconds.

        Returns:
            Uptime in seconds, or None if cannot determine.
        """
        try:
            result = subprocess.run(
                ['ps', '-C', 'rnsd', '-o', 'etimes='],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0 and result.stdout.strip():
                # May have multiple lines if multiple processes; take minimum
                uptimes = []
                for line in result.stdout.strip().split('\n'):
                    line = line.strip()
                    if line.isdigit():
                        uptimes.append(int(line))
                return min(uptimes) if uptimes else None
        except (subprocess.SubprocessError, OSError, ValueError):
            pass
        return None

    def _check_rnsd_rpc(self, sudo_user: str = None) -> bool:
        """Check if rnsd's RPC socket is accepting connections.

        NomadNet's TextUI calls ``get_interface_stats()`` during init,
        which connects to rnsd via ``multiprocessing.connection.Client``.
        If the RPC socket isn't ready, NomadNet crashes with
        ``ConnectionRefusedError: [Errno 111]``.

        We probe the same RPC path by running ``rnstatus`` as the target
        user.  If it succeeds, RPC is live.

        Returns:
            True if RPC is available (or we can't determine), False if
            definitely refusing connections.
        """
        # Build command: run rnstatus as the target user if we're root
        cmd = ['rnstatus', '-p']
        if sudo_user and os.getuid() == 0 and sudo_user != 'root':
            cmd = ['sudo', '-u', sudo_user] + cmd

        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0:
                return True
            # Check stderr for connection refused
            stderr = (result.stderr or '').lower()
            if 'connection refused' in stderr or 'errno 111' in stderr:
                logger.warning("rnsd RPC connection refused (rnstatus failed)")
                return False
            # Other failures (rnstatus not installed, etc.) — don't block
            return True
        except FileNotFoundError:
            # rnstatus not installed — can't check, allow proceeding
            return True
        except subprocess.TimeoutExpired:
            logger.warning("rnstatus timed out checking RPC")
            return False
        except (subprocess.SubprocessError, OSError) as e:
            logger.debug("RPC check failed: %s", e)
            return True  # Don't block on unexpected errors

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

        if not self.ctx.dialog.yesno(
            "Config Repair Needed",
            f"Your NomadNet config is missing the [textui] section\n"
            f"required for text UI mode.\n\n"
            f"Config: {config_path}\n\n"
            f"Add a default [textui] section now?",
        ):
            return self.ctx.dialog.yesno(
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
                subprocess.run(
                    ['chown', f'{sudo_user}:{sudo_user}', str(config_path)],
                    capture_output=True, timeout=10
                )

            self.ctx.dialog.msgbox(
                "Config Updated",
                f"Added [textui] section to config.\n\n"
                f"NomadNet text UI should now work.",
            )
            return True
        except (OSError, PermissionError) as e:
            self.ctx.dialog.msgbox(
                "Config Update Failed",
                f"Could not update config:\n  {config_path}\n\n"
                f"Error: {e}\n\n"
                f"Add [textui] section manually or delete config\n"
                f"and let NomadNet recreate it.",
            )
            return False
