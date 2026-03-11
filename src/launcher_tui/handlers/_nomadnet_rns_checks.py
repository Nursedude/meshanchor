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

from utils.paths import ReticulumPaths, get_real_user_home

from utils.safe_import import safe_import

stop_service, start_service, _HAS_SERVICE_CHECK = safe_import(
    'utils.service_check', 'stop_service', 'start_service'
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

    def _check_rns_for_nomadnet(
        self,
        nn_path: str = None,
        rns_config_path: str = None,
    ) -> bool:
        """Check that RNS/rnsd is available and properly configured.

        Args:
            nn_path: Path to NomadNet binary (for venv-aware RPC check).
            rns_config_path: RNS config directory NomadNet will use.

        Checks:
        1. Is /etc/reticulum blocking user access?
        2. Is rnsd running?
        3. User mismatch? (rnsd as root vs NomadNet as user)
        4. RPC connectivity (using NomadNet's own RNS library)

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
        #
        # IMPORTANT: Use NomadNet's own RNS library for the check, not
        # system rnstatus. NomadNet installed via pipx has its own venv
        # with a potentially different RNS version — the system rnstatus
        # may pass while NomadNet's RNS fails (version/protocol mismatch).
        rpc_ok = self._check_rnsd_rpc(sudo_user, nn_path, rns_config_path)
        if not rpc_ok:
            return self._handle_rpc_failure(
                sudo_user, nn_path, rns_config_path
            )

        # rnsd running as correct user with working RPC
        return True

    def _handle_rpc_failure(
        self,
        sudo_user: str = None,
        nn_path: str = None,
        rns_config_path: str = None,
    ) -> bool:
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
                if self._check_rnsd_rpc(sudo_user, nn_path, rns_config_path):
                    self.ctx.dialog.msgbox(
                        "RPC Ready",
                        "rnsd RPC is now accepting connections.\n\n"
                        "NomadNet should start normally.",
                    )
                    return True

        # Check for version mismatch: NomadNet's RNS fails but system
        # rnstatus works — indicates different RNS installations
        mismatch_hint = ""
        nn_python = self._get_nn_python(nn_path)
        if nn_python:
            # NomadNet has its own venv — check if system rnstatus works
            sys_rpc_ok = self._check_rnsd_rpc_via_rnstatus(sudo_user)
            if sys_rpc_ok:
                nn_ver, sys_ver = self._get_rns_versions(
                    nn_python, sudo_user
                )
                mismatch_hint = (
                    f"\n\nNOTE: System rnstatus can connect (RNS {sys_ver}),\n"
                    f"but NomadNet's RNS ({nn_ver}) cannot.\n"
                    f"Fix: pipx inject nomadnet rns=={sys_ver}"
                )

        # RPC still failing — offer to restart rnsd
        uptime_info = f" (uptime: {uptime}s)" if uptime is not None else ""
        choice = self.ctx.dialog.menu(
            "rnsd RPC Not Ready",
            f"rnsd is running{uptime_info} and listening on port 37428,\n"
            "but its RPC socket is not accepting connections.\n\n"
            "NomadNet needs RPC to query interface stats on startup.\n"
            "Without it, NomadNet will crash with:\n"
            f"  ConnectionRefusedError: [Errno 111]{mismatch_hint}\n\n"
            "How do you want to proceed?",
            [
                ("restart", "Restart rnsd and retry (recommended)"),
                ("continue", "Continue anyway (may crash)"),
                ("cancel", "Cancel"),
            ],
        )

        if choice == "restart":
            return self._restart_rnsd_and_verify_rpc(
                sudo_user, nn_path, rns_config_path
            )
        elif choice == "continue":
            return True
        else:
            return False

    def _restart_rnsd_and_verify_rpc(
        self,
        sudo_user: str = None,
        nn_path: str = None,
        rns_config_path: str = None,
    ) -> bool:
        """Restart rnsd and verify RPC becomes available.

        Returns:
            True if RPC is working after restart, or user wants to
            continue anyway. False if restart failed or user cancelled.
        """
        self.ctx.dialog.infobox(
            "Restarting rnsd",
            "Stopping rnsd and clearing stale auth tokens...",
        )

        # Stop → clear stale auth tokens → start (not just restart).
        # Stale shared_instance_* files from the previous rnsd session
        # cause RPC auth failures even after restart. Same pattern used
        # in _rns_repair.py:238 and rns_diagnostics.py:234.
        try:
            if stop_service:
                stop_service('rnsd')
            else:
                subprocess.run(
                    ['sudo', 'systemctl', 'stop', 'rnsd'],
                    capture_output=True, timeout=30
                )
            time.sleep(1)
        except (subprocess.SubprocessError, OSError):
            pass  # Best-effort stop; start below will handle it

        # Clear stale shared_instance_* auth token files
        user_home = get_real_user_home()
        storage_dirs = [
            Path('/etc/reticulum/storage'),
            Path('/root/.reticulum/storage'),
            user_home / '.reticulum' / 'storage',
            user_home / '.config' / 'reticulum' / 'storage',
        ]
        for storage_dir in storage_dirs:
            if storage_dir.exists():
                for auth_file in storage_dir.glob('shared_instance_*'):
                    try:
                        auth_file.unlink()
                        logger.debug("Cleared stale auth file: %s", auth_file)
                    except (OSError, PermissionError):
                        pass

        # Verify port 37428 is free before starting rnsd.
        # A stale rnsd process (not cleaned by systemctl stop) or another
        # LXMF client may still hold the port, causing the new rnsd to
        # either fail to bind or connect as a client instead of master.
        import socket
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(1)
            port_busy = s.connect_ex(('127.0.0.1', 37428)) == 0
            s.close()
        except OSError:
            port_busy = False
        if port_busy:
            logger.warning("Port 37428 still occupied after rnsd stop — "
                           "killing stale rnsd processes")
            try:
                subprocess.run(
                    ['pkill', '-f', 'rnsd'],
                    capture_output=True, timeout=5
                )
            except (subprocess.SubprocessError, OSError):
                pass
            time.sleep(2)  # Let socket enter TIME_WAIT and close

        # Start rnsd with fresh auth tokens
        self.ctx.dialog.infobox(
            "Starting rnsd",
            "Starting rnsd with fresh auth tokens...",
        )
        try:
            if start_service:
                ok, msg = start_service('rnsd')
                if not ok:
                    self.ctx.dialog.msgbox(
                        "Start Failed",
                        f"Could not start rnsd:\n  {msg}\n\n"
                        "Try manually: sudo systemctl start rnsd",
                    )
                    return False
            else:
                subprocess.run(
                    ['sudo', 'systemctl', 'start', 'rnsd'],
                    capture_output=True, timeout=30
                )
        except (subprocess.SubprocessError, OSError) as e:
            self.ctx.dialog.msgbox(
                "Start Failed",
                f"Could not start rnsd:\n  {e}",
            )
            return False

        # Verify rnsd process is actually running (not just systemctl OK).
        # A service can report "started" then crash during initialization.
        time.sleep(1)
        if not self._get_rnsd_user():
            # rnsd started but crashed immediately — get journal
            crash_info = ""
            try:
                jr = subprocess.run(
                    ['journalctl', '-u', 'rnsd', '-n', '10',
                     '--no-pager', '-q', '--no-hostname'],
                    capture_output=True, text=True, timeout=5
                )
                if jr.returncode == 0 and jr.stdout.strip():
                    crash_info = "\n\n" + jr.stdout.strip()[:600]
            except (subprocess.SubprocessError, OSError):
                pass
            self.ctx.dialog.msgbox(
                "rnsd Crashed on Start",
                "rnsd was started but is no longer running.\n"
                "It likely crashed during initialization.\n\n"
                f"Check: sudo journalctl -u rnsd -n 30{crash_info}",
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
                "rnsd is running but port 37428 not listening after 15s.\n\n"
                "An interface may be blocking startup.\n"
                "Check: sudo journalctl -u rnsd -n 30",
            )
            return False

        # Wait a moment for RPC to come up after port
        time.sleep(2)

        # Verify RPC
        if self._check_rnsd_rpc(sudo_user, nn_path, rns_config_path):
            self.ctx.dialog.msgbox(
                "rnsd RPC Ready",
                "rnsd has been restarted and RPC is working.\n\n"
                "NomadNet should start normally.",
            )
            return True

        # RPC still not working after restart — diagnose WHY instead of
        # showing a generic message. The most common persistent cause is
        # an RNS version mismatch between NomadNet's pipx venv and rnsd.
        # Same check as _handle_rpc_failure() lines 337-350.
        mismatch_hint = ""
        nn_python = self._get_nn_python(nn_path)
        if nn_python:
            sys_rpc_ok = self._check_rnsd_rpc_via_rnstatus(sudo_user)
            if sys_rpc_ok:
                mismatch_hint = (
                    "\n\nDiagnosis: System rnstatus CAN connect to rnsd,\n"
                    "but NomadNet's RNS library CANNOT.\n"
                    "This is an RNS version mismatch.\n\n"
                    "Fix: pipx upgrade nomadnet\n"
                    "Then retry launching NomadNet."
                )

        journal_hint = ""
        try:
            jr = subprocess.run(
                ['journalctl', '-u', 'rnsd', '-n', '10',
                 '--no-pager', '-q', '--no-hostname'],
                capture_output=True, text=True, timeout=5
            )
            if jr.returncode == 0 and jr.stdout.strip():
                journal_hint = (
                    "\n\nRecent rnsd log:\n" + jr.stdout.strip()[:600]
                )
        except (subprocess.SubprocessError, OSError):
            pass

        if mismatch_hint:
            # Version mismatch — show actual versions and targeted fix
            nn_ver, sys_ver = self._get_rns_versions(nn_python, sudo_user)
            self.ctx.dialog.msgbox(
                "RNS Version Mismatch",
                "rnsd is running and system tools can connect,\n"
                "but NomadNet's bundled RNS library cannot.\n\n"
                f"  NomadNet RNS: {nn_ver}\n"
                f"  System RNS:   {sys_ver}\n\n"
                "Fix (match NomadNet's RNS to system):\n"
                f"  pipx inject nomadnet rns=={sys_ver}\n\n"
                "Or upgrade everything:\n"
                "  pipx upgrade nomadnet\n\n"
                "Then retry launching NomadNet.",
            )
            return False

        return self.ctx.dialog.yesno(
            "RPC Still Not Ready",
            "rnsd was restarted (with stale auth tokens cleared)\n"
            "but RPC is still not responding.\n\n"
            "This may indicate rnsd is misconfigured or crashing.\n"
            f"Check: sudo journalctl -u rnsd -n 30{journal_hint}\n\n"
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

    def _check_rnsd_rpc(
        self,
        sudo_user: str = None,
        nn_path: str = None,
        rns_config_path: str = None,
    ) -> bool:
        """Check if rnsd's RPC socket is accepting connections.

        NomadNet's TextUI calls ``get_interface_stats()`` during init,
        which connects to rnsd via ``multiprocessing.connection.Client``.
        If the RPC socket isn't ready, NomadNet crashes with
        ``ConnectionRefusedError: [Errno 111]``.

        IMPORTANT: When nn_path is provided, uses NomadNet's own Python
        interpreter to test RPC. NomadNet installed via pipx has its own
        venv with a potentially different RNS version — system ``rnstatus``
        may succeed while NomadNet's RNS fails (version/protocol mismatch).

        Falls back to system ``rnstatus -p`` if no venv Python is found.

        Returns:
            True if RPC is available (or we can't determine), False if
            definitely refusing connections.
        """
        # Prefer NomadNet's own Python for the RPC check — this tests
        # the exact same code path that crashes in NomadNet.
        nn_python = self._get_nn_python(nn_path)
        if nn_python:
            configdir = rns_config_path or '/etc/reticulum'
            # Minimal script that does exactly what NomadNet does:
            # RNS.Reticulum() → get_interface_stats() → RPC Client()
            rpc_test = (
                "import RNS; import sys; "
                f"r = RNS.Reticulum(configdir='{configdir}'); "
                "r.get_interface_stats(); "
                "print('RPC_OK')"
            )
            cmd = [nn_python, '-c', rpc_test]
            if sudo_user and os.getuid() == 0 and sudo_user != 'root':
                cmd = ['sudo', '-u', sudo_user, '-H'] + cmd

            try:
                result = subprocess.run(
                    cmd, capture_output=True, text=True, timeout=15
                )
                if result.returncode == 0 and 'RPC_OK' in result.stdout:
                    return True
                # Check for connection refused in output
                combined = (
                    (result.stderr or '') + (result.stdout or '')
                ).lower()
                if 'connection refused' in combined or 'errno 111' in combined:
                    logger.warning(
                        "RPC check via NomadNet's RNS failed: "
                        "ConnectionRefusedError"
                    )
                    return False
                # Other failure (import error, etc.) — fall through to rnstatus
                logger.debug(
                    "RPC check via NomadNet Python failed (rc=%d): %s",
                    result.returncode,
                    (result.stderr or '').strip()[:200],
                )
            except FileNotFoundError:
                logger.debug("NomadNet Python not found: %s", nn_python)
            except subprocess.TimeoutExpired:
                logger.warning("RPC check via NomadNet Python timed out")
                return False
            except (subprocess.SubprocessError, OSError) as e:
                logger.debug("RPC check via NomadNet Python failed: %s", e)

        # Fallback: use system rnstatus
        return self._check_rnsd_rpc_via_rnstatus(sudo_user)

    def _check_rnsd_rpc_via_rnstatus(self, sudo_user: str = None) -> bool:
        """Check RPC using system rnstatus (fallback).

        This may use a different RNS version than NomadNet's pipx venv,
        so it can give false positives. Prefer _check_rnsd_rpc() which
        uses NomadNet's own Python when available.

        Returns:
            True if RPC is available (or we can't determine), False if
            definitely refusing connections.
        """
        cmd = ['rnstatus', '-p']
        if sudo_user and os.getuid() == 0 and sudo_user != 'root':
            cmd = ['sudo', '-u', sudo_user] + cmd

        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0:
                return True
            stderr = (result.stderr or '').lower()
            if 'connection refused' in stderr or 'errno 111' in stderr:
                logger.warning("rnsd RPC connection refused (rnstatus failed)")
                return False
            # Other failures (rnstatus not installed, etc.) — don't block
            return True
        except FileNotFoundError:
            return True
        except subprocess.TimeoutExpired:
            logger.warning("rnstatus timed out checking RPC")
            return False
        except (subprocess.SubprocessError, OSError) as e:
            logger.debug("RPC check failed: %s", e)
            return True

    def _get_nn_python(self, nn_path: str = None) -> str | None:
        """Get the Python interpreter from NomadNet's venv.

        NomadNet installed via pipx lives in a venv. The Python binary
        is in the same bin/ directory as the nomadnet script.

        Returns:
            Path to Python interpreter, or None if not a venv install.
        """
        if not nn_path:
            return None
        nn_bin_dir = Path(nn_path).resolve().parent
        for candidate in ('python3', 'python'):
            py = nn_bin_dir / candidate
            if py.exists():
                return str(py)
        return None

    def _get_rns_versions(
        self, nn_python: str, sudo_user: str = None
    ) -> tuple[str, str]:
        """Get RNS versions from NomadNet's venv and system.

        Returns:
            (nn_rns_version, sys_rns_version) — either may be "unknown".
        """
        nn_ver = "unknown"
        sys_ver = "unknown"
        ver_cmd = "import RNS; print(RNS.__version__)"

        # NomadNet's venv RNS version
        cmd = [nn_python, '-c', ver_cmd]
        if sudo_user and os.getuid() == 0 and sudo_user != 'root':
            cmd = ['sudo', '-u', sudo_user, '-H'] + cmd
        try:
            r = subprocess.run(
                cmd, capture_output=True, text=True, timeout=5
            )
            if r.returncode == 0 and r.stdout.strip():
                nn_ver = r.stdout.strip().split('\n')[0]
        except (subprocess.SubprocessError, OSError):
            pass

        # System RNS version
        try:
            r = subprocess.run(
                ['python3', '-c', ver_cmd],
                capture_output=True, text=True, timeout=5
            )
            if r.returncode == 0 and r.stdout.strip():
                sys_ver = r.stdout.strip().split('\n')[0]
        except (subprocess.SubprocessError, OSError):
            pass

        return nn_ver, sys_ver

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
