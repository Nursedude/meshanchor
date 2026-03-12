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

start_service, stop_service, _HAS_SERVICE_CHECK = safe_import(
    'utils.service_check', 'start_service', 'stop_service'
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

    def _get_nomadnet_venv_python(self, nn_path: str) -> str:
        """Derive NomadNet's pipx venv Python path from the binary.

        NomadNet installed via pipx lives in a venv like:
          ~/.local/pipx/venvs/nomadnet/bin/nomadnet
        The Python interpreter is at:
          ~/.local/pipx/venvs/nomadnet/bin/python3

        Returns the path string, or None if not found.
        """
        try:
            nn_resolved = Path(nn_path).resolve()
            venv_bin = nn_resolved.parent
            candidate = venv_bin / 'python3'
            if candidate.exists():
                return str(candidate)
            # Try python (no version suffix)
            candidate = venv_bin / 'python'
            if candidate.exists():
                return str(candidate)
        except (OSError, ValueError) as e:
            logger.debug("Cannot resolve NomadNet venv Python: %s", e)
        return None

    def _check_rpc_with_nomadnet_venv(self, nn_path: str) -> bool:
        """Test RNS RPC connectivity using NomadNet's own Python/RNS.

        This catches version mismatches where system rnstatus works
        (using system RNS) but NomadNet's bundled RNS (in pipx venv)
        cannot connect due to RPC protocol differences.

        Returns True if RPC check passes or is skipped (no venv found).
        Returns False if user cancelled after seeing the error dialog.
        """
        venv_python = self._get_nomadnet_venv_python(nn_path)
        if not venv_python:
            logger.debug("No venv Python found for NomadNet, skipping RPC check")
            return True

        # Determine config dir — prefer /etc/reticulum if it exists
        config_dir = '/etc/reticulum'
        if not Path(config_dir).exists():
            config_dir = str(get_real_user_home() / '.reticulum')

        # Test RPC using NomadNet's own Python interpreter.
        # Must call get_interface_stats() — this exercises the actual RPC path
        # (multiprocessing.connection.Client) that crashes NomadNet, not just
        # the shared instance connection which can succeed independently.
        rpc_snippet = (
            "import RNS; "
            f"r = RNS.Reticulum(configdir='{config_dir}'); "
            "stats = r.get_interface_stats(); "
            "print('connected' if stats is not None else 'standalone')"
        )

        sudo_user = os.environ.get('SUDO_USER')
        try:
            if sudo_user and sudo_user != 'root':
                result = subprocess.run(
                    ['sudo', '-u', sudo_user, '-H', venv_python, '-c', rpc_snippet],
                    capture_output=True, text=True, timeout=15,
                )
            else:
                result = subprocess.run(
                    [venv_python, '-c', rpc_snippet],
                    capture_output=True, text=True, timeout=15,
                )
        except subprocess.TimeoutExpired:
            logger.warning("NomadNet venv RPC check timed out")
            return self.ctx.dialog.yesno(
                "RPC Check Timeout",
                "The RNS RPC connectivity check timed out.\n\n"
                "rnsd may be overloaded or initializing slowly.\n\n"
                "Continue anyway?",
            )
        except (subprocess.SubprocessError, OSError) as e:
            logger.debug("NomadNet venv RPC check failed to run: %s", e)
            return True  # Can't run check, don't block

        if result.returncode == 0 and 'connected' in result.stdout:
            logger.debug("NomadNet venv RPC check passed")
            return True

        # RPC failed via NomadNet's Python — check if system rnstatus works
        logger.warning(
            "NomadNet venv RPC check failed: rc=%d stderr=%s",
            result.returncode, result.stderr.strip()[:200],
        )
        system_rpc_ok = False
        try:
            sys_result = subprocess.run(
                ['rnstatus'], capture_output=True, text=True, timeout=10,
            )
            system_rpc_ok = sys_result.returncode == 0
        except (subprocess.SubprocessError, OSError, FileNotFoundError):
            pass

        if system_rpc_ok:
            # System RNS works but NomadNet's doesn't → version mismatch
            choice = self.ctx.dialog.menu(
                "RNS Version Mismatch",
                "System rnstatus connects to rnsd, but NomadNet's\n"
                "bundled RNS library cannot (version mismatch).\n\n"
                "NomadNet will crash with ConnectionRefusedError.",
                [
                    ("upgrade", "Upgrade NomadNet (recommended)"),
                    ("restart", "Restart rnsd and retry"),
                    ("continue", "Continue anyway (will likely crash)"),
                    ("cancel", "Cancel"),
                ],
            )
        else:
            # Both fail — rnsd RPC issue
            choice = self.ctx.dialog.menu(
                "RNS RPC Unavailable",
                "Neither NomadNet's RNS nor system rnstatus can\n"
                "connect to the rnsd shared instance.\n\n"
                "The rnsd RPC socket may be broken or not ready.",
                [
                    ("upgrade", "Upgrade NomadNet (recommended)"),
                    ("restart", "Restart rnsd and retry"),
                    ("continue", "Continue anyway"),
                    ("cancel", "Cancel"),
                ],
            )

        if choice == "upgrade":
            if self._upgrade_nomadnet():
                # Re-check after upgrade
                return self._restart_rnsd_and_verify_rpc(nn_path)
            return False
        elif choice == "restart":
            return self._restart_rnsd_and_verify_rpc(nn_path)
        elif choice == "continue":
            return True
        else:
            return False

    def _restart_rnsd_and_verify_rpc(self, nn_path: str = None) -> bool:
        """Restart rnsd and verify RPC becomes available.

        Stops rnsd, restarts it, waits for port 37428, then re-runs
        the venv-aware RPC check. Returns True if RPC is now working.
        """
        self.ctx.dialog.infobox("Restarting rnsd", "Stopping rnsd...")
        try:
            if _HAS_SERVICE_CHECK and stop_service:
                stop_service('rnsd')
            subprocess.run(
                ['pkill', '-f', 'rnsd'], capture_output=True, timeout=5
            )
            time.sleep(1)
        except Exception as e:
            logger.warning("Failed to stop rnsd: %s", e)

        self.ctx.dialog.infobox("Restarting rnsd", "Starting rnsd...")
        try:
            if _HAS_SERVICE_CHECK and start_service:
                start_service('rnsd')
            else:
                subprocess.run(
                    ['sudo', 'systemctl', 'start', 'rnsd'],
                    capture_output=True, timeout=10,
                )
        except Exception as e:
            self.ctx.dialog.msgbox(
                "Restart Failed", f"Could not start rnsd: {e}"
            )
            return False

        # Wait for port 37428
        self.ctx.dialog.infobox(
            "Waiting for rnsd",
            "Waiting for rnsd shared instance (port 37428)...",
        )
        if not self._wait_for_rns_port(max_wait=20):
            self.ctx.dialog.msgbox(
                "rnsd Not Ready",
                "rnsd did not bind port 37428 after restart.\n\n"
                "Check: sudo journalctl -u rnsd -n 30",
            )
            return False

        # Re-verify RPC silently (no dialogs) to avoid recursive dialog loop.
        # _check_rpc_with_nomadnet_venv shows its own restart/continue/cancel
        # dialog which would recurse back here if the user picks restart.
        if nn_path:
            rpc_ok = self._test_rpc_silent(nn_path)
            if not rpc_ok:
                choice = self.ctx.dialog.menu(
                    "RPC Still Failing",
                    "rnsd restarted but NomadNet's RNS still cannot\n"
                    "connect via RPC.\n\n"
                    "This is usually an RNS version mismatch.",
                    [
                        ("upgrade", "Upgrade NomadNet (recommended)"),
                        ("continue", "Launch NomadNet anyway"),
                        ("cancel", "Cancel"),
                    ],
                )
                if choice == "upgrade":
                    if self._upgrade_nomadnet():
                        # Re-test after upgrade
                        rpc_ok = self._test_rpc_silent(nn_path)
                        if rpc_ok:
                            self.ctx.dialog.msgbox(
                                "RPC Fixed",
                                "NomadNet upgrade fixed the RPC connection.\n\n"
                                "NomadNet should now connect successfully.",
                            )
                            return True
                        # Still failing after upgrade — let user decide
                        return self.ctx.dialog.yesno(
                            "Still Failing",
                            "RPC still fails after upgrade.\n\n"
                            "Launch NomadNet anyway?",
                        )
                    return False
                elif choice == "continue":
                    return True
                else:
                    return False

        self.ctx.dialog.msgbox(
            "rnsd Restarted",
            "rnsd has been restarted and RPC is working.\n\n"
            "NomadNet should now connect successfully.",
        )
        return True

    def _test_rpc_silent(self, nn_path: str) -> bool:
        """Silent RPC connectivity test — no dialogs, just pass/fail.

        Used after rnsd restart to avoid recursive dialog loops.
        Returns True if NomadNet's venv RNS can connect to rnsd RPC.
        """
        venv_python = self._get_nomadnet_venv_python(nn_path)
        if not venv_python:
            return True  # Can't test, assume OK

        config_dir = '/etc/reticulum'
        if not Path(config_dir).exists():
            config_dir = str(get_real_user_home() / '.reticulum')

        rpc_snippet = (
            "import RNS; "
            f"r = RNS.Reticulum(configdir='{config_dir}'); "
            "stats = r.get_interface_stats(); "
            "print('connected' if stats is not None else 'standalone')"
        )

        sudo_user = os.environ.get('SUDO_USER')
        try:
            if sudo_user and sudo_user != 'root':
                result = subprocess.run(
                    ['sudo', '-u', sudo_user, '-H', venv_python, '-c', rpc_snippet],
                    capture_output=True, text=True, timeout=15,
                )
            else:
                result = subprocess.run(
                    [venv_python, '-c', rpc_snippet],
                    capture_output=True, text=True, timeout=15,
                )
            return result.returncode == 0 and 'connected' in result.stdout
        except (subprocess.SubprocessError, OSError) as e:
            logger.debug("Silent RPC check failed: %s", e)
            return False

    def _check_rns_for_nomadnet(self, nn_path: str = None) -> bool:
        """Check that RNS/rnsd is available and properly configured.

        Checks:
        1. Is /etc/reticulum blocking user access?
        2. Is rnsd running?
        3. Is rnsd running as root? (causes RPC auth failures with user NomadNet)
        4. Can NomadNet's own RNS library connect via RPC? (version mismatch check)

        Args:
            nn_path: Path to NomadNet binary (for venv-aware RPC check).

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
            "Verifying rnsd shared instance (port 37428)...\n"
            "This may take up to 20s if interfaces are initializing.",
        )
        port_listening = self._wait_for_rns_port(max_wait=20)

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

        # rnsd is running and listening - verify RPC with NomadNet's own RNS
        if nn_path:
            if not self._check_rpc_with_nomadnet_venv(nn_path):
                return False

        # Check for user mismatches
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

        # rnsd running as correct user (or no sudo context)
        return True

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
