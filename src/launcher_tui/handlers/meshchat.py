"""
MeshChat Handler — MeshChat client installation, management, and monitoring.

Provides TUI handlers to install, manage, and monitor MeshChat --
an LXMF messaging client with HTTP API and web UI.

MeshChat runs as an external service (systemd or manual) and exposes
a REST API on port 8000. This handler wraps the existing MeshChat plugin
(src/plugins/meshchat/) with TUI menus.

Data flow:
  Meshtastic (Short Turbo) <> meshtasticd <> MeshForge Gateway
  <> LXMF <> rnsd <> LXMF <> MeshChat

Install:  Automated via TUI (git clone + npm + pip + systemd service)
          Or manually: see plugins/meshchat/service.py INSTALL_HINT

LXMF exclusivity:
  MeshChat and NomadNet are both LXMF clients. Only one should run
  at a time to avoid port 37428 conflicts. The _ensure_lxmf_exclusive()
  helper delegates to handlers/_lxmf_utils.py for this check.

Converted from meshchat_client_mixin.py as part of the mixin-to-registry migration (Batch 8).
"""

import logging
import os
import shutil
import subprocess
import time
from pathlib import Path

from backend import clear_screen
from handler_protocol import BaseHandler
from handlers._lxmf_utils import ensure_lxmf_exclusive

from utils.paths import get_real_user_home
from utils.safe_import import safe_import

def _detect_lxmf_available() -> bool:
    """Check if LXMF is importable, trying venv python if direct import fails."""
    _, has_it = safe_import('LXMF')
    if has_it:
        return True
    # Current process may not see venv packages (e.g. sudo python3);
    # check the venv python that the service actually uses.
    venv_python = Path('/opt/meshforge/venv/bin/python3')
    if venv_python.is_file():
        try:
            result = subprocess.run(
                [str(venv_python), '-c', 'import LXMF'],
                capture_output=True, timeout=10,
            )
            return result.returncode == 0
        except (subprocess.SubprocessError, OSError):
            pass
    return False


_HAS_LXMF = _detect_lxmf_available()

logger = logging.getLogger(__name__)

# Import centralized service checking
check_process_running, start_service, stop_service, _HAS_SERVICE_CHECK = safe_import(
    'utils.service_check', 'check_process_running', 'start_service', 'stop_service'
)

check_rns_shared_instance, _HAS_RNS_CHECK = safe_import(
    'utils.service_check', 'check_rns_shared_instance'
)

# Import privilege-elevation helpers for systemd service creation
_sudo_write, enable_service, _HAS_SUDO_WRITE = safe_import(
    'utils.service_check', '_sudo_write', 'enable_service'
)

# Import MeshChat plugin components (optional external dependency)
MeshChatService, ServiceState, _HAS_MESHCHAT_SERVICE = safe_import(
    'plugins.meshchat.service', 'MeshChatService', 'ServiceState'
)

MeshChatClient, MeshChatError, _HAS_MESHCHAT_CLIENT = safe_import(
    'plugins.meshchat.client', 'MeshChatClient', 'MeshChatError'
)


class MeshChatHandler(BaseHandler):
    """TUI handler for MeshChat client management."""

    handler_id = "meshchat"
    menu_section = "mesh_networks"

    MESHCHAT_REPO = "https://github.com/liamcottle/reticulum-meshchat"
    MESHCHAT_SERVICE_NAME = "reticulum-meshchat"

    def menu_items(self):
        return [
            ("meshchat", "MeshChat Client     RNS messaging", "rns"),
        ]

    def execute(self, action):
        if action == "meshchat":
            self._meshchat_menu()

    # ------------------------------------------------------------------
    # Detection helpers
    # ------------------------------------------------------------------

    def _is_meshchat_installed(self) -> bool:
        """Check if MeshChat is installed (binary, service, or process)."""
        # Check for meshchat.py or reticulum-meshchat in PATH
        if shutil.which('meshchat') or shutil.which('meshchat.py'):
            return True

        # Check user local bin
        user_home = get_real_user_home()
        for candidate in [
            user_home / 'reticulum-meshchat' / 'meshchat.py',
            user_home / '.local' / 'bin' / 'meshchat',
        ]:
            if candidate.exists():
                return True

        # Check via service detection if plugin available
        if _HAS_MESHCHAT_SERVICE:
            try:
                svc = MeshChatService()
                status = svc.check_status(blocking=True)
                return status.installed
            except Exception:
                pass

        return False

    def _is_meshchat_running(self) -> bool:
        """Check if MeshChat process is running."""
        # Try unified check first
        if _HAS_SERVICE_CHECK and check_process_running:
            if check_process_running('meshchat'):
                return True

        # Try plugin service check
        if _HAS_MESHCHAT_SERVICE:
            try:
                svc = MeshChatService()
                status = svc.check_status(blocking=True)
                return status.running
            except Exception:
                pass

        # Fallback to pgrep
        try:
            result = subprocess.run(
                ['pgrep', '-f', 'meshchat.py'],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0 and result.stdout.strip():
                for pid in result.stdout.strip().split('\n'):
                    if pid.strip() and pid.strip() != str(os.getpid()):
                        return True
        except (subprocess.SubprocessError, OSError):
            pass

        return False

    def _has_meshchat_systemd_service(self) -> bool:
        """Check if a systemd service file exists for MeshChat."""
        service_path = Path(
            f"/etc/systemd/system/{self.MESHCHAT_SERVICE_NAME}.service"
        )
        if service_path.exists():
            return True
        # Also check via plugin if available
        if _HAS_MESHCHAT_SERVICE:
            try:
                svc = MeshChatService()
                status = svc.check_status(blocking=True)
                return status.service_name is not None
            except Exception:
                pass
        return False

    # ------------------------------------------------------------------
    # LXMF exclusivity (delegates to shared utility)
    # ------------------------------------------------------------------

    def _ensure_lxmf_exclusive(self, starting_app: str) -> bool:
        """Ensure only one LXMF app runs at a time.

        Delegates to handlers/_lxmf_utils.ensure_lxmf_exclusive().
        """
        return ensure_lxmf_exclusive(
            self.ctx.dialog, starting_app,
            is_meshchat_running_fn=self._is_meshchat_running,
        )

    # ------------------------------------------------------------------
    # LXMF installation helper
    # ------------------------------------------------------------------

    def _offer_install_lxmf(self) -> bool:
        """Offer to install the LXMF module when it is missing.

        Returns True if LXMF is now available (was installed or was already
        present), False if the user declined or installation failed.
        """
        global _HAS_LXMF

        choice = self.ctx.dialog.yesno(
            "Missing LXMF Module",
            "The LXMF Python module is not installed.\n\n"
            "LXMF is required for MeshChat messaging.\n\n"
            "Install it now?\n"
            "  (runs: pip install -r requirements/rns.txt)",
        )
        if not choice:
            return False

        # Stop crash-looping service before install attempt
        if self._has_meshchat_systemd_service():
            if _HAS_SERVICE_CHECK and stop_service:
                ok, msg = stop_service(self.MESHCHAT_SERVICE_NAME)
                if ok:
                    logger.info("Stopped %s before LXMF install",
                                self.MESHCHAT_SERVICE_NAME)
            else:
                try:
                    subprocess.run(
                        ['systemctl', 'stop', self.MESHCHAT_SERVICE_NAME],
                        capture_output=True, timeout=15,
                    )
                except (subprocess.SubprocessError, OSError):
                    pass

        clear_screen()
        print("=== Installing LXMF Module ===\n")

        sudo_user = os.environ.get('SUDO_USER')
        run_as_user = sudo_user if sudo_user and sudo_user != 'root' else None
        success, error_detail = self._install_lxmf_package(
            run_as_user=run_as_user,
        )

        if success:
            # Verify using the SERVICE python, not the current process.
            # The current process may not have the venv on sys.path.
            service_python = self._get_service_python()
            if self._check_lxmf_with_python(service_python):
                # Service python confirmed LXMF is importable — set the flag
                _HAS_LXMF = True
                # Ensure systemd service uses the correct Python
                self._verify_service_python_path()
                self.ctx.dialog.msgbox(
                    "LXMF Installed",
                    "LXMF module installed successfully.\n\n"
                    "Continuing with MeshChat startup...",
                )
                return True

        error_msg = (
            "Failed to install the LXMF module.\n\n"
            "Try installing manually:\n"
            "  pip install -r requirements/rns.txt\n\n"
            "Or:\n"
            "  pip install lxmf"
        )
        if error_detail:
            error_msg += f"\n\nError details:\n{error_detail[:300]}"

        self.ctx.dialog.msgbox("Installation Failed", error_msg)
        return False

    def _offer_install_meshchat_deps(self, missing: list) -> bool:
        """Offer to install missing MeshChat Python dependencies.

        Returns True if deps are now available, False if declined or failed.
        """
        missing_str = ', '.join(missing)
        install_dir = self._get_meshchat_install_dir()
        req_file = install_dir / 'requirements.txt'

        if req_file.exists():
            install_hint = f"pip install -r {req_file}"
        else:
            install_hint = f"pip install {' '.join(missing)}"

        choice = self.ctx.dialog.yesno(
            "Missing MeshChat Dependencies",
            f"MeshChat requires Python modules that are not installed:\n"
            f"  {missing_str}\n\n"
            f"Without these, the service will crash-loop.\n\n"
            f"Install now?\n"
            f"  (runs: {install_hint})",
        )
        if not choice:
            return False

        # Stop crash-looping service before install
        if self._has_meshchat_systemd_service():
            if _HAS_SERVICE_CHECK and stop_service:
                ok, msg = stop_service(self.MESHCHAT_SERVICE_NAME)
                if ok:
                    logger.info("Stopped %s before dep install",
                                self.MESHCHAT_SERVICE_NAME)
            else:
                try:
                    subprocess.run(
                        ['systemctl', 'stop', self.MESHCHAT_SERVICE_NAME],
                        capture_output=True, timeout=15,
                    )
                except (subprocess.SubprocessError, OSError):
                    pass

        clear_screen()
        print("=== Installing MeshChat Dependencies ===\n")

        sudo_user = os.environ.get('SUDO_USER')
        run_as_user = sudo_user if sudo_user and sudo_user != 'root' else None

        success = self._install_meshchat_pip(install_dir, run_as_user)

        service_python = self._get_service_python()
        still_missing = self._check_meshchat_deps(service_python)

        if success and not still_missing:
            self.ctx.dialog.msgbox(
                "Dependencies Installed",
                "MeshChat dependencies installed successfully.\n\n"
                "Continuing with MeshChat startup...",
            )
            return True

        # Fallback: install individual missing packages directly
        if still_missing:
            print(f"\nPackages still missing: {', '.join(still_missing)}")
            print("Attempting individual package install...\n")
            pip_cmd = self._get_pip_command()
            venv_pip = Path('/opt/meshforge/venv/bin/pip')
            using_venv = venv_pip.exists()

            if 'install' in pip_cmd:
                base_cmd = pip_cmd
            else:
                base_cmd = pip_cmd + ['install']

            for mod in still_missing:
                cmd = base_cmd + ['--timeout', '60', mod]
                if run_as_user and not using_venv:
                    if '--user' not in cmd:
                        cmd.append('--user')
                    cmd = ['sudo', '-H', '-u', run_as_user] + cmd
                try:
                    r = subprocess.run(
                        cmd, capture_output=True, text=True, timeout=120,
                    )
                    if r.returncode == 0:
                        print(f"  Installed {mod}")
                    else:
                        print(f"  Failed to install {mod}")
                except (subprocess.SubprocessError, OSError) as e:
                    print(f"  Error installing {mod}: {e}")

            # Re-check after fallback
            still_missing = self._check_meshchat_deps(service_python)
            if not still_missing:
                self.ctx.dialog.msgbox(
                    "Dependencies Installed",
                    "MeshChat dependencies installed successfully.\n\n"
                    "Continuing with MeshChat startup...",
                )
                return True

        error_detail = getattr(self, '_last_pip_error', '')
        error_msg = (
            f"Failed to install MeshChat dependencies.\n\n"
            f"Try manually:\n"
            f"  cd {install_dir}\n"
            f"  pip install -r requirements.txt"
        )
        if error_detail:
            error_msg += f"\n\nError:\n{error_detail[:300]}"
        self.ctx.dialog.msgbox("Installation Failed", error_msg)
        return False

    def _install_lxmf_package(self, run_as_user: str = None) -> tuple:
        """Install the LXMF Python package via pip.

        Args:
            run_as_user: If set and not using venv, run pip as this user
                so packages land in the correct site-packages.

        Returns (success: bool, error_detail: str).
        """
        pip_cmd = self._get_pip_command()
        venv_pip = Path('/opt/meshforge/venv/bin/pip')
        using_venv = venv_pip.exists()

        # Build the base install command
        if 'install' in pip_cmd:
            base_cmd = pip_cmd
        else:
            base_cmd = pip_cmd + ['install']

        # Prefer requirements/rns.txt for correct dependency pins
        project_root = Path(__file__).resolve().parents[3]
        rns_req = project_root / 'requirements' / 'rns.txt'

        if rns_req.exists():
            cmd = base_cmd + ['-r', str(rns_req)]
        else:
            # Inline critical pins when requirements file unavailable
            cmd = base_cmd + [
                'lxmf',
                'cryptography>=45.0.7,<47',
                'pyopenssl>=25.3.0',
            ]

        # When NOT using venv and running as root via sudo, install as the
        # service user so packages are visible to the systemd service.
        if run_as_user and not using_venv:
            cmd = ['sudo', '-H', '-u', run_as_user] + cmd

        print(f"  Running: {' '.join(cmd)}\n")

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=120,
            )
            if result.stdout:
                for line in result.stdout.strip().split('\n')[-5:]:
                    print(f"  {line}")
            if result.returncode != 0:
                error_lines = ''
                print(f"\n  pip error (exit {result.returncode}):")
                if result.stderr:
                    tail = result.stderr.strip().split('\n')[-5:]
                    for line in tail:
                        print(f"  {line}")
                    error_lines = '\n'.join(tail)
                return False, error_lines

            print("\n  LXMF installed successfully.")
            return True, ''

        except subprocess.TimeoutExpired:
            msg = 'Installation timed out after 120 seconds.'
            print(f"\n  {msg}")
            return False, msg
        except (subprocess.SubprocessError, OSError) as e:
            msg = str(e)
            print(f"\n  Installation error: {msg}")
            return False, msg

    # ------------------------------------------------------------------
    # Top-level submenu
    # ------------------------------------------------------------------

    def _meshchat_menu(self):
        """MeshChat LXMF client -- install, manage, monitor."""
        while True:
            running = self._is_meshchat_running()
            installed = self._is_meshchat_installed()

            if not installed:
                subtitle = "MeshChat is NOT INSTALLED"
            elif running:
                subtitle = "MeshChat is RUNNING (http://127.0.0.1:8000)"
            else:
                subtitle = "MeshChat is installed (not running)"

            choices = [
                ("status", "MeshChat Status"),
            ]

            if installed:
                if running:
                    choices.append(("stop", "Stop MeshChat"))
                    choices.append(("peers", "View LXMF Peers"))
                    choices.append(("messages", "Recent Messages"))
                    choices.append(("announce", "Send LXMF Announce"))
                    choices.append(("web", "Web UI (show URL)"))
                else:
                    choices.append(("start", "Start MeshChat"))
                if not self._has_meshchat_systemd_service():
                    choices.append(("create_service", "Create Systemd Service"))
                choices.append(("rebuild", "Rebuild Frontend"))
                choices.append(("logs", "View Logs"))
                choices.append(("uninstall", "Disable MeshChat"))
            else:
                choices.append(("install", "Install MeshChat"))

            choices.append(("back", "Back"))

            choice = self.ctx.dialog.menu(
                "MeshChat Client",
                f"LXMF messaging with HTTP API & web UI:\n\n"
                f"{subtitle}",
                choices,
            )

            if choice is None or choice == "back":
                break

            dispatch = {
                "status": ("MeshChat Status", self._meshchat_status),
                "start": ("Start MeshChat", self._launch_meshchat),
                "stop": ("Stop MeshChat", self._stop_meshchat),
                "peers": ("View LXMF Peers", self._meshchat_peers),
                "messages": ("Recent Messages", self._meshchat_messages),
                "announce": ("Send LXMF Announce", self._meshchat_announce),
                "web": ("MeshChat Web UI", self._meshchat_web_ui),
                "logs": ("View MeshChat Logs", self._meshchat_logs),
                "install": ("Install MeshChat", self._install_meshchat),
                "create_service": ("Create Service", self._create_meshchat_service),
                "rebuild": ("Rebuild Frontend", self._rebuild_frontend),
                "uninstall": ("Disable MeshChat", self._uninstall_meshchat),
            }
            entry = dispatch.get(choice)
            if entry:
                self.ctx.safe_call(*entry)

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def _meshchat_status(self):
        """Show comprehensive MeshChat status."""
        clear_screen()
        print("=== MeshChat Status ===\n")

        # Installation
        installed = self._is_meshchat_installed()
        running = self._is_meshchat_running()

        if not installed:
            print("  Installed:  No")
            print(f"\n  Install from: https://github.com/liamcottle/reticulum-meshchat")
            self.ctx.wait_for_enter()
            return

        print(f"  Installed:  Yes")
        print(f"  Running:    {'Yes' if running else 'No'}")

        # LXMF module status
        if _HAS_LXMF:
            try:
                import LXMF
                lxmf_ver = getattr(LXMF, '__version__', 'unknown')
                print(f"  LXMF:       {lxmf_ver}")
            except Exception:
                print("  LXMF:       Installed")
        else:
            print("  LXMF:       NOT INSTALLED (pip install -r requirements/rns.txt)")
            if running:
                print("  WARNING:    Service is crash-looping without LXMF!")
                print("              Stop service and install LXMF first.")

        # MeshChat Python dependencies status
        if installed:
            service_python = self._get_service_python()
            missing_deps = self._check_meshchat_deps(service_python)
            if missing_deps:
                missing_str = ', '.join(missing_deps)
                print(f"  MeshChat deps: MISSING ({missing_str})")
                if running:
                    print("  WARNING:    Service is crash-looping without dependencies!")
                    install_dir = self._get_meshchat_install_dir()
                    print(f"              cd {install_dir} && pip install -r requirements.txt")
            else:
                print("  MeshChat deps: OK")

        # Frontend build status
        if installed:
            install_dir = self._get_meshchat_install_dir()
            public_dir = install_dir / 'public'
            if public_dir.is_dir():
                print("  Frontend:   Built")
            else:
                print("  Frontend:   NOT BUILT (use 'Rebuild Frontend' from menu)")
                if running:
                    print("  WARNING:    Service will crash without frontend!")

        # Service details via plugin
        if _HAS_MESHCHAT_SERVICE:
            try:
                svc = MeshChatService()
                status = svc.check_status(blocking=True)
                if status.service_name:
                    print(f"  Service:    {status.service_name}")
                if status.pid:
                    print(f"  PID:        {status.pid}")
                print(f"  Port 8000:  {'Open' if status.port_open else 'Closed'}")
            except Exception as e:
                logger.debug("MeshChat service check failed: %s", e)

        # API details if running
        if running and _HAS_MESHCHAT_CLIENT:
            try:
                client = MeshChatClient()
                mc_status = client.get_status()
                print()
                if mc_status.version:
                    print(f"  Version:    {mc_status.version}")
                if mc_status.identity_hash:
                    print(f"  Identity:   {mc_status.identity_hash}")
                if mc_status.display_name:
                    print(f"  Name:       {mc_status.display_name}")
                print(f"  Peers:      {mc_status.peer_count}")
                print(f"  Messages:   {mc_status.message_count}")
                print(f"  RNS:        {'Connected' if mc_status.rns_connected else 'Disconnected'}")
                if mc_status.uptime_seconds > 0:
                    hrs = mc_status.uptime_seconds // 3600
                    mins = (mc_status.uptime_seconds % 3600) // 60
                    print(f"  Uptime:     {hrs}h {mins}m")
                print(f"  Propagation: {'Yes' if mc_status.propagation_node else 'No'}")
            except Exception as e:
                print(f"\n  API Error:  {e}")

        # RNS shared instance status
        print()
        rnsd_user = self._get_rnsd_user()
        if rnsd_user:
            print(f"  rnsd:       Running (as {rnsd_user})")
        else:
            print("  rnsd:       Not running")
            if running:
                print("              MeshChat may be running its own RNS instance")

        self.ctx.wait_for_enter()

    # ------------------------------------------------------------------
    # Start / Stop
    # ------------------------------------------------------------------

    def _launch_meshchat(self):
        """Start MeshChat service."""
        # Preflight: ensure NomadNet is not running (one LXMF app at a time)
        if not self._ensure_lxmf_exclusive("meshchat"):
            return

        # Preflight: check RNS availability
        if not self._check_rns_for_meshchat():
            return

        # Preflight: check LXMF availability
        if not _HAS_LXMF:
            if not self._offer_install_lxmf():
                return

        # Preflight: check MeshChat's own Python dependencies (aiohttp, etc.)
        if self._is_meshchat_installed():
            missing_deps = self._check_meshchat_deps(self._get_service_python())
            if missing_deps:
                if not self._offer_install_meshchat_deps(missing_deps):
                    return

        if _HAS_MESHCHAT_SERVICE:
            svc = MeshChatService()
            status = svc.check_status(blocking=True)

            if status.running:
                self.ctx.dialog.msgbox(
                    "Already Running",
                    "MeshChat is already running.\n\n"
                    f"Web UI: http://127.0.0.1:8000",
                )
                return

            if status.service_name:
                # Systemd service available — start it
                self.ctx.dialog.infobox(
                    "Starting MeshChat",
                    f"Starting {status.service_name}...",
                )
                svc.start()
                time.sleep(3)

                # Verify
                new_status = svc.check_status(blocking=True)
                if new_status.running:
                    self.ctx.dialog.msgbox(
                        "MeshChat Started",
                        f"MeshChat is running.\n\n"
                        f"Web UI: http://127.0.0.1:8000",
                    )
                else:
                    # Check journalctl for ModuleNotFoundError
                    self._handle_start_failure(status.service_name)
                return

        # No systemd service — offer to create one
        if self.ctx.dialog.yesno(
            "No Service Found",
            "No systemd service found for MeshChat.\n\n"
            "Would you like to create a systemd service?\n"
            "This enables automatic startup at boot.",
        ):
            self._create_meshchat_service()
        else:
            self.ctx.dialog.msgbox(
                "Manual Start",
                "Start manually:\n"
                "  cd ~/reticulum-meshchat\n"
                "  python meshchat.py\n\n"
                "Or use 'Create Service' from the MeshChat menu.",
            )

    def _create_meshchat_service(self):
        """Create systemd service for an already-installed MeshChat."""
        if not self.ctx.dialog.yesno(
            "Create Service",
            "Create a systemd service for MeshChat?\n\n"
            "This enables automatic startup at boot\n"
            "and management via systemctl.",
        ):
            return

        install_dir = self._get_meshchat_install_dir()
        meshchat_py = install_dir / 'meshchat.py'

        if not meshchat_py.exists():
            self.ctx.dialog.msgbox(
                "Not Found",
                f"MeshChat not found at:\n"
                f"  {meshchat_py}\n\n"
                "Install MeshChat first from the menu.",
            )
            return

        sudo_user = os.environ.get('SUDO_USER')
        run_as_user = sudo_user if sudo_user and sudo_user != 'root' else None

        clear_screen()
        print("=== Creating MeshChat Service ===\n")

        if not self._install_meshchat_service(install_dir, run_as_user):
            self.ctx.dialog.msgbox(
                "Service Creation Failed",
                "Failed to create the systemd service.\n\n"
                "Check permissions and try running with sudo.",
            )
            return

        # Offer to start the service now
        if self.ctx.dialog.yesno(
            "Service Created",
            "Systemd service created and enabled.\n\n"
            "Start MeshChat now?",
        ):
            if _HAS_SERVICE_CHECK and start_service:
                start_service(self.MESHCHAT_SERVICE_NAME)
            else:
                try:
                    subprocess.run(
                        ['systemctl', 'start', self.MESHCHAT_SERVICE_NAME],
                        capture_output=True, timeout=15,
                    )
                except (subprocess.SubprocessError, OSError) as e:
                    logger.debug("Service start failed: %s", e)

            time.sleep(3)

            if self._is_meshchat_running():
                self.ctx.dialog.msgbox(
                    "MeshChat Started",
                    "MeshChat is running.\n\n"
                    "Web UI: http://127.0.0.1:8000",
                )
            else:
                self._handle_start_failure(self.MESHCHAT_SERVICE_NAME)

    def _handle_start_failure(self, service_name: str):
        """Inspect journalctl after a failed start and offer to fix.

        Checks for ModuleNotFoundError and missing frontend directory
        in recent logs and offers automatic remediation.
        """
        import re

        try:
            result = subprocess.run(
                ['journalctl', '-u', service_name, '-n', '20',
                 '--no-pager', '-o', 'cat'],
                capture_output=True, text=True, timeout=10,
            )
            if 'ModuleNotFoundError' in result.stdout:
                match = re.search(
                    r"No module named '(\w+)'", result.stdout,
                )
                mod_name = match.group(1) if match else 'unknown'

                if self.ctx.dialog.yesno(
                    "Missing Python Module",
                    f"MeshChat crashed because '{mod_name}' is not installed.\n\n"
                    f"Install MeshChat dependencies now?",
                ):
                    missing = self._check_meshchat_deps()
                    if not missing:
                        missing = [mod_name]
                    self._offer_install_meshchat_deps(missing)
                return

            if 'does not exist' in result.stdout and 'public' in result.stdout:
                if self.ctx.dialog.yesno(
                    "Frontend Not Built",
                    "MeshChat crashed because the web frontend is missing.\n\n"
                    "The 'public/' directory was not created by npm build.\n\n"
                    "Rebuild the frontend now?",
                ):
                    self._rebuild_frontend()
                return
        except (subprocess.SubprocessError, OSError):
            pass

        self.ctx.dialog.msgbox(
            "Start May Have Failed",
            f"MeshChat does not appear to be running.\n\n"
            f"Check: systemctl status {service_name}\n"
            f"       journalctl -u {service_name} -n 20",
        )

    def _rebuild_frontend(self):
        """Rebuild the MeshChat web frontend (npm build).

        Stops the service if crash-looping, runs npm build, validates
        that public/ was created, and offers to restart.
        """
        install_dir = self._get_meshchat_install_dir()
        if not (install_dir / 'package.json').exists():
            self.ctx.dialog.msgbox(
                "Not Found",
                f"No package.json in {install_dir}.\n\n"
                f"MeshChat may not be installed correctly.",
            )
            return

        # Stop crash-looping service first
        if self._is_meshchat_running():
            if _HAS_SERVICE_CHECK and stop_service:
                stop_service(self.MESHCHAT_SERVICE_NAME)
            else:
                try:
                    subprocess.run(
                        ['systemctl', 'stop', self.MESHCHAT_SERVICE_NAME],
                        capture_output=True, timeout=15,
                    )
                except (subprocess.SubprocessError, OSError):
                    pass

        clear_screen()
        print("=== Rebuilding MeshChat Frontend ===\n")

        sudo_user = os.environ.get('SUDO_USER')
        run_as_user = sudo_user if sudo_user and sudo_user != 'root' else None

        success = self._install_meshchat_npm(install_dir, run_as_user)

        if success:
            self.ctx.dialog.msgbox(
                "Frontend Built",
                "MeshChat web frontend rebuilt successfully.\n\n"
                "You can now start MeshChat from the menu.",
            )
        else:
            self.ctx.dialog.msgbox(
                "Build Failed",
                f"Frontend build failed.\n\n"
                f"Try manually:\n"
                f"  cd {install_dir}\n"
                f"  npm install --omit=dev\n"
                f"  npm run build-frontend",
            )

    def _stop_meshchat(self):
        """Stop MeshChat service."""
        if not self.ctx.dialog.yesno(
            "Stop MeshChat",
            "Stop the MeshChat service?\n\n"
            "LXMF messaging will be unavailable until restarted.",
        ):
            return

        stopped = False

        if _HAS_MESHCHAT_SERVICE:
            svc = MeshChatService()
            status = svc.check_status(blocking=True)
            if status.service_name:
                svc.stop()
                time.sleep(2)
                stopped = True

        if not stopped:
            # Fallback: kill process
            try:
                subprocess.run(
                    ['pkill', '-f', 'meshchat.py'],
                    capture_output=True, timeout=5,
                )
                time.sleep(1)
                stopped = True
            except (subprocess.SubprocessError, OSError):
                pass

        if stopped and not self._is_meshchat_running():
            self.ctx.dialog.msgbox(
                "MeshChat Stopped",
                "MeshChat has been stopped.",
            )
        else:
            self.ctx.dialog.msgbox(
                "Stop May Have Failed",
                "MeshChat may still be running.\n\n"
                "Try: pkill -f meshchat.py",
            )

    # ------------------------------------------------------------------
    # Peers, Messages, Announce
    # ------------------------------------------------------------------

    def _meshchat_peers(self):
        """Show discovered LXMF peers."""
        clear_screen()
        print("=== MeshChat LXMF Peers ===\n")

        if not _HAS_MESHCHAT_CLIENT:
            print("  MeshChat client library not available.")
            self.ctx.wait_for_enter()
            return

        try:
            client = MeshChatClient()
            peers = client.get_peers()

            if not peers:
                print("  No peers discovered yet.")
                print("\n  Peers appear after LXMF announces propagate.")
                print("  Try: Send Announce from the menu.")
                self.ctx.wait_for_enter()
                return

            # Header
            print(f"  {'Name':<20} {'Hash':<18} {'Online':<8} {'Last Announce'}")
            print(f"  {'─' * 20} {'─' * 18} {'─' * 8} {'─' * 20}")

            for peer in peers:
                name = (peer.display_name or "Unknown")[:20]
                short_hash = peer.destination_hash[:16] + ".."
                online = "Yes" if peer.is_online else "No"
                last = ""
                if peer.last_announce:
                    last = peer.last_announce.strftime("%Y-%m-%d %H:%M")
                print(f"  {name:<20} {short_hash:<18} {online:<8} {last}")

            print(f"\n  Total: {len(peers)} peers")

        except Exception as e:
            print(f"  Error fetching peers: {e}")

        self.ctx.wait_for_enter()

    def _meshchat_messages(self):
        """Show recent LXMF messages."""
        clear_screen()
        print("=== MeshChat Recent Messages ===\n")

        if not _HAS_MESHCHAT_CLIENT:
            print("  MeshChat client library not available.")
            self.ctx.wait_for_enter()
            return

        try:
            client = MeshChatClient()
            messages = client.get_messages(limit=20)

            if not messages:
                print("  No messages yet.")
                self.ctx.wait_for_enter()
                return

            for msg in messages:
                direction = "<<" if msg.is_incoming else ">>"
                ts = msg.timestamp.strftime("%H:%M:%S")
                src = msg.source_hash[:12] + ".."
                delivered = "+" if msg.delivered else " "
                content = msg.content[:60]
                if len(msg.content) > 60:
                    content += "..."
                print(f"  {ts} {direction} {src} {delivered} {content}")

            print(f"\n  Showing {len(messages)} most recent messages")

        except Exception as e:
            print(f"  Error fetching messages: {e}")

        self.ctx.wait_for_enter()

    def _meshchat_announce(self):
        """Send LXMF announce to the network."""
        if not self.ctx.dialog.yesno(
            "Send Announce",
            "Send an LXMF announce to the RNS network?\n\n"
            "This advertises MeshChat's presence to other\n"
            "LXMF clients (NomadNet, Sideband, other MeshChat).",
        ):
            return

        if not _HAS_MESHCHAT_CLIENT:
            self.ctx.dialog.msgbox(
                "Not Available",
                "MeshChat client library not available.",
            )
            return

        try:
            client = MeshChatClient()
            if client.send_announce():
                self.ctx.dialog.msgbox(
                    "Announce Sent",
                    "LXMF announce has been sent to the network.\n\n"
                    "Other nodes will discover MeshChat within minutes.",
                )
            else:
                self.ctx.dialog.msgbox(
                    "Announce Failed",
                    "Failed to send LXMF announce.\n\n"
                    "Check that MeshChat is running and RNS is connected.",
                )
        except Exception as e:
            self.ctx.dialog.msgbox(
                "Announce Error",
                f"Error sending announce: {e}",
            )

    # ------------------------------------------------------------------
    # Web UI
    # ------------------------------------------------------------------

    def _meshchat_web_ui(self):
        """Show MeshChat web UI URL."""
        self.ctx.dialog.msgbox(
            "MeshChat Web UI",
            "MeshChat web interface is available at:\n\n"
            "  http://127.0.0.1:8000\n\n"
            "Access from the same machine in a browser,\n"
            "or via SSH tunnel:\n\n"
            "  ssh -L 8000:127.0.0.1:8000 user@host\n"
            "  Then open: http://127.0.0.1:8000",
        )

    # ------------------------------------------------------------------
    # Logs
    # ------------------------------------------------------------------

    def _meshchat_logs(self):
        """View MeshChat logs."""
        clear_screen()
        print("=== MeshChat Logs ===\n")

        shown = False

        # Try systemd journal first
        if _HAS_MESHCHAT_SERVICE:
            try:
                svc = MeshChatService()
                status = svc.check_status(blocking=True)
                if status.service_name:
                    print(f"  Service: {status.service_name}\n")
                    result = subprocess.run(
                        ['journalctl', '-u', status.service_name,
                         '-n', '30', '--no-pager'],
                        capture_output=True, text=True, timeout=10
                    )
                    if result.stdout and result.stdout.strip():
                        for line in result.stdout.strip().split('\n'):
                            print(f"  {line}")
                        shown = True
            except (subprocess.SubprocessError, OSError) as e:
                logger.debug("MeshChat journal read failed: %s", e)

        # Try log file paths
        if not shown:
            user_home = get_real_user_home()
            log_paths = [
                user_home / '.config' / 'meshchat' / 'logs',
                user_home / '.meshchat' / 'logs',
                user_home / 'reticulum-meshchat' / 'logs',
                Path('/var/log/meshchat'),
            ]
            for log_dir in log_paths:
                if log_dir.exists() and log_dir.is_dir():
                    # Find most recent log file
                    log_files = sorted(
                        log_dir.glob('*.log'),
                        key=lambda p: p.stat().st_mtime,
                        reverse=True,
                    )
                    if log_files:
                        import collections
                        print(f"  Log file: {log_files[0]}\n")
                        try:
                            with open(log_files[0], 'r') as f:
                                last_lines = list(
                                    collections.deque(f, maxlen=30)
                                )
                            for line in last_lines:
                                print(f"  {line.rstrip()}")
                            shown = True
                        except (IOError, OSError) as e:
                            print(f"  Error reading log: {e}")
                    break

        if not shown:
            print("  No logs found.")
            print("\n  If MeshChat is running as a systemd service:")
            print("    journalctl -u meshchat -n 30 --no-pager")
            print("    journalctl -u reticulum-meshchat -n 30 --no-pager")

        self.ctx.wait_for_enter()

    # ------------------------------------------------------------------
    # Install (fully automated)
    # ------------------------------------------------------------------

    def _get_meshchat_install_dir(self) -> Path:
        """Return the MeshChat install directory under user home."""
        return get_real_user_home() / 'reticulum-meshchat'

    def _get_pip_command(self) -> list:
        """Return the pip command appropriate for this install.

        Prefers MeshForge's venv pip, falls back to system pip3
        with --break-system-packages for PEP 668 compatibility.
        """
        venv_pip = Path('/opt/meshforge/venv/bin/pip')
        if venv_pip.exists():
            return [str(venv_pip)]

        # Check for PEP 668 (externally-managed Python)
        import glob
        if glob.glob('/usr/lib/python3*/EXTERNALLY-MANAGED'):
            return ['pip3', 'install', '--break-system-packages']

        return ['pip3']

    def _get_service_python(self) -> str:
        """Return the Python interpreter matching where pip installs packages.

        If the MeshForge venv exists, return its python3 so that systemd
        services can find venv-installed packages (LXMF, RNS, cryptography).
        Falls back to system python3.
        """
        venv_python = Path('/opt/meshforge/venv/bin/python3')
        if venv_python.is_file():
            return str(venv_python)
        return shutil.which('python3') or '/usr/bin/python3'

    def _check_meshchat_deps(self, python_path: str = None) -> list:
        """Check if MeshChat's key Python deps are importable by service Python.

        Returns a list of missing module names (empty = all OK).
        """
        if python_path is None:
            python_path = self._get_service_python()

        # Key deps from MeshChat's requirements.txt that cause crash-loops
        required_modules = ['aiohttp', 'cryptography']
        missing = []

        for mod in required_modules:
            try:
                result = subprocess.run(
                    [python_path, '-c', f'import {mod}'],
                    capture_output=True,
                    timeout=10,
                )
                if result.returncode != 0:
                    missing.append(mod)
            except (subprocess.SubprocessError, OSError):
                missing.append(mod)

        return missing

    def _check_lxmf_with_python(self, python_path: str = None) -> bool:
        """Check if LXMF is importable by the service's Python interpreter.

        Uses subprocess to test the import in the correct Python environment,
        avoiding false negatives when the current process's sys.path differs
        from the service's (e.g., sudo python3 vs venv python3).
        """
        if python_path is None:
            python_path = self._get_service_python()
        try:
            result = subprocess.run(
                [python_path, '-c', 'import LXMF'],
                capture_output=True,
                timeout=10,
            )
            return result.returncode == 0
        except (subprocess.SubprocessError, OSError) as e:
            logger.debug("LXMF import check failed for %s: %s", python_path, e)
            return False

    def _verify_service_python_path(self):
        """Check if the systemd service file uses the correct Python.

        If ExecStart uses a different Python than _get_service_python(),
        regenerate the service file so the service can find LXMF.
        """
        service_path = Path(
            f"/etc/systemd/system/{self.MESHCHAT_SERVICE_NAME}.service"
        )
        if not service_path.exists():
            return

        try:
            content = service_path.read_text()
        except (IOError, OSError):
            return

        expected_python = self._get_service_python()

        for line in content.splitlines():
            if line.strip().startswith('ExecStart='):
                exec_start = line.strip().split('=', 1)[1]
                current_python = exec_start.split()[0] if exec_start else ''
                if current_python != expected_python:
                    logger.warning(
                        "Service uses %s but pip target is %s",
                        current_python, expected_python,
                    )
                    install_dir = self._get_meshchat_install_dir()
                    sudo_user = os.environ.get('SUDO_USER')
                    run_as = sudo_user if sudo_user and sudo_user != 'root' else None
                    self._install_meshchat_service(install_dir, run_as)
                break

    def _install_meshchat(self):
        """Automated MeshChat installation.

        Steps:
        1. Confirm with user
        2. Check/install prerequisites (git, nodejs, npm)
        3. git clone reticulum-meshchat
        4. pip install -r requirements.txt
        5. npm install && npm run build-frontend
        6. Create systemd service
        7. Enable + start service
        """
        if self._is_meshchat_installed():
            self.ctx.dialog.msgbox(
                "Already Installed",
                "MeshChat is already installed.\n\n"
                "Use Start/Stop from the menu to manage it.",
            )
            return

        if not self.ctx.dialog.yesno(
            "Install MeshChat",
            "Install MeshChat (Reticulum MeshChat)?\n\n"
            "This will:\n"
            "  1. Install Node.js/npm (if needed)\n"
            "  2. Clone the MeshChat repository\n"
            "  3. Install Python dependencies\n"
            "  4. Build the web frontend (npm)\n"
            "  5. Create a systemd service\n\n"
            "MeshChat provides LXMF messaging with a\n"
            "web UI at http://127.0.0.1:8000\n\n"
            "Source: github.com/liamcottle/reticulum-meshchat\n\n"
            "Install now?",
        ):
            return

        # LXMF exclusivity check — stop NomadNet if running
        if not self._ensure_lxmf_exclusive("meshchat"):
            return

        clear_screen()
        print("=== Installing MeshChat ===\n")

        install_dir = self._get_meshchat_install_dir()
        sudo_user = os.environ.get('SUDO_USER')
        run_as_user = sudo_user if sudo_user and sudo_user != 'root' else None

        try:
            # Step 1: Prerequisites
            if not self._install_meshchat_prerequisites():
                self.ctx.wait_for_enter()
                return

            # Step 2: Git clone
            if not self._install_meshchat_clone(install_dir, run_as_user):
                self.ctx.wait_for_enter()
                return

            # Step 3: pip install
            if not self._install_meshchat_pip(install_dir, run_as_user):
                self.ctx.wait_for_enter()
                return

            # Step 4: npm build
            if not self._install_meshchat_npm(install_dir, run_as_user):
                self.ctx.wait_for_enter()
                return

            # Step 5: systemd service
            if not self._install_meshchat_service(install_dir, run_as_user):
                print("\nSystemd service creation failed.")
                print("You can still run MeshChat manually:")
                print(f"  cd {install_dir} && python3 meshchat.py")

            # Step 6: Start service
            print("\nStarting MeshChat service...")
            try:
                subprocess.run(
                    ['systemctl', 'start', self.MESHCHAT_SERVICE_NAME],
                    capture_output=True, timeout=15,
                )
                time.sleep(3)
            except (subprocess.SubprocessError, OSError) as e:
                logger.debug("Service start failed: %s", e)

            # Verify
            if self._is_meshchat_running():
                print("\nMeshChat is running!")
                print("Web UI: http://127.0.0.1:8000")
            else:
                print("\nMeshChat installed but may not be running yet.")
                print(f"Check: systemctl status {self.MESHCHAT_SERVICE_NAME}")

            print("\nInstallation complete.")

        except KeyboardInterrupt:
            print("\n\nInstallation cancelled.")
        except Exception as e:
            print(f"\nInstallation error: {e}")
            logger.exception("MeshChat install failed")

        try:
            self.ctx.wait_for_enter()
        except (EOFError, KeyboardInterrupt):
            pass

    def _install_meshchat_prerequisites(self) -> bool:
        """Check and install git, nodejs, npm. Returns True on success."""
        # git
        if not shutil.which('git'):
            print("Installing git...")
            result = subprocess.run(
                ['apt-get', 'install', '-y', '-qq', 'git'],
                capture_output=True, timeout=120,
            )
            if result.returncode != 0:
                print("Failed to install git.")
                print("Try: sudo apt install git")
                return False

        # Node.js + npm
        if not shutil.which('node') or not shutil.which('npm'):
            print("Installing Node.js and npm...")
            result = subprocess.run(
                ['apt-get', 'install', '-y', '-qq', 'nodejs', 'npm'],
                capture_output=True, timeout=180,
            )
            if result.returncode != 0:
                print("Failed to install Node.js/npm.")
                print("Try: sudo apt install nodejs npm")
                return False
            print("Node.js and npm installed.")

        # Verify
        for tool in ['git', 'node', 'npm']:
            if not shutil.which(tool):
                print(f"Error: {tool} not found after install.")
                return False

        print("Prerequisites OK (git, node, npm)\n")
        return True

    def _install_meshchat_clone(self, install_dir: Path, run_as_user: str = None) -> bool:
        """Clone the MeshChat repository. Returns True on success."""
        if install_dir.exists():
            print(f"Directory exists: {install_dir}")
            # Pull latest instead of clone
            print("Pulling latest changes...")
            cmd = ['git', '-C', str(install_dir), 'pull', '--ff-only']
            if run_as_user:
                cmd = ['sudo', '-H', '-u', run_as_user] + cmd
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            if result.returncode != 0:
                print(f"Git pull failed: {result.stderr.strip()}")
                print("Continuing with existing checkout.")
            else:
                print("Repository updated.")
            return True

        print(f"Cloning MeshChat to {install_dir}...")
        cmd = ['git', 'clone', self.MESHCHAT_REPO, str(install_dir)]
        if run_as_user:
            cmd = ['sudo', '-H', '-u', run_as_user] + cmd

        result = subprocess.run(cmd, timeout=120)
        if result.returncode != 0:
            print("Git clone failed.")
            print(f"Try: git clone {self.MESHCHAT_REPO} {install_dir}")
            return False

        print("Repository cloned.\n")
        return True

    def _install_meshchat_pip(self, install_dir: Path, run_as_user: str = None) -> bool:
        """Install MeshChat Python dependencies. Returns True on success."""
        req_file = install_dir / 'requirements.txt'
        if not req_file.exists():
            print("No requirements.txt found — skipping pip install.")
            return True

        print("Installing Python dependencies...")
        pip_cmd = self._get_pip_command()
        venv_pip = Path('/opt/meshforge/venv/bin/pip')
        using_venv = venv_pip.exists()

        # Build install command
        if 'install' in pip_cmd:
            cmd = pip_cmd + ['--timeout', '60', '-r', str(req_file)]
        else:
            cmd = pip_cmd + ['install', '--timeout', '60', '-r', str(req_file)]

        # When NOT using venv and running as root via sudo, install as the
        # service user so packages are visible to the systemd service.
        # When using venv, skip sudo -u: the venv is root-owned and the
        # TUI already runs as root; the service reads from the venv.
        if run_as_user and not using_venv:
            if '--user' not in cmd:
                cmd.insert(cmd.index('-r'), '--user')
            cmd = ['sudo', '-H', '-u', run_as_user] + cmd

        print(f"  Running: {' '.join(cmd)}\n")

        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=300,
            )
            if result.stdout:
                for line in result.stdout.strip().split('\n')[-10:]:
                    print(f"  {line}")
            if result.returncode != 0:
                print(f"\n  pip install failed (exit {result.returncode}):")
                if result.stderr:
                    tail = result.stderr.strip().split('\n')[-5:]
                    for line in tail:
                        print(f"  {line}")
                    self._last_pip_error = '\n'.join(tail)
                return False
        except subprocess.TimeoutExpired:
            print("\n  pip install timed out after 300 seconds.")
            self._last_pip_error = 'Installation timed out after 300 seconds.'
            return False
        except (subprocess.SubprocessError, OSError) as e:
            print(f"\n  pip install error: {e}")
            self._last_pip_error = str(e)
            return False

        print("\nPython dependencies installed.\n")
        return True

    def _install_meshchat_npm(self, install_dir: Path, run_as_user: str = None) -> bool:
        """Build MeshChat web frontend with npm. Returns True on success."""
        pkg_json = install_dir / 'package.json'
        if not pkg_json.exists():
            print("No package.json found — skipping npm build.")
            return True

        print("Installing npm dependencies...")
        cmd = ['npm', 'install', '--omit=dev']
        if run_as_user:
            cmd = ['sudo', '-H', '-u', run_as_user] + cmd

        result = subprocess.run(cmd, cwd=str(install_dir), timeout=300)
        if result.returncode != 0:
            print("npm install failed.")
            print(f"Try: cd {install_dir} && npm install --omit=dev")
            return False

        print("Building web frontend...")
        cmd = ['npm', 'run', 'build-frontend']
        if run_as_user:
            cmd = ['sudo', '-H', '-u', run_as_user] + cmd

        result = subprocess.run(cmd, cwd=str(install_dir), timeout=300)
        if result.returncode != 0:
            print("npm build failed.")
            print(f"Try: cd {install_dir} && npm run build-frontend")
            return False

        # Validate that public/ was actually created
        public_dir = install_dir / 'public'
        if not public_dir.is_dir():
            print("WARNING: npm build succeeded but public/ directory not found.")
            print(f"Try: cd {install_dir} && npm run build-frontend")
            return False

        print("Web frontend built.\n")
        return True

    def _install_meshchat_service(self, install_dir: Path, run_as_user: str = None) -> bool:
        """Create systemd service for MeshChat. Returns True on success."""
        service_user = run_as_user or 'root'
        user_home = get_real_user_home()
        python_path = self._get_service_python()
        meshchat_py = install_dir / 'meshchat.py'

        service_content = (
            f"[Unit]\n"
            f"Description=Reticulum MeshChat LXMF Client\n"
            f"After=network.target rnsd.service\n"
            f"Wants=rnsd.service\n"
            f"StartLimitIntervalSec=60\n"
            f"StartLimitBurst=5\n"
            f"ConditionPathIsDirectory={install_dir}/public\n"
            f"\n"
            f"[Service]\n"
            f"Type=simple\n"
            f"User={service_user}\n"
            f"WorkingDirectory={install_dir}\n"
            f"ExecStartPre=/bin/sleep 2\n"
            f"ExecStart={python_path} {meshchat_py}\n"
            f"Restart=on-failure\n"
            f"RestartSec=5\n"
            f"Environment=HOME={user_home}\n"
            f"\n"
            f"[Install]\n"
            f"WantedBy=multi-user.target\n"
        )

        service_path = f"/etc/systemd/system/{self.MESHCHAT_SERVICE_NAME}.service"
        print(f"Creating systemd service: {service_path}")

        try:
            # Write service file with privilege elevation
            if _HAS_SUDO_WRITE and _sudo_write:
                write_ok, write_msg = _sudo_write(service_path, service_content)
                if not write_ok:
                    print(f"Failed to write service file: {write_msg}")
                    return False
            else:
                logger.warning("_sudo_write unavailable, falling back to direct write")
                with open(service_path, 'w') as f:
                    f.write(service_content)

            # Reload systemd daemon and enable service
            # enable_service() calls daemon_reload() internally
            if _HAS_SUDO_WRITE and enable_service:
                ok, msg = enable_service(self.MESHCHAT_SERVICE_NAME)
                if not ok:
                    print(f"Failed to enable service: {msg}")
                    return False
            else:
                subprocess.run(
                    ['systemctl', 'daemon-reload'],
                    capture_output=True, timeout=15,
                )
                subprocess.run(
                    ['systemctl', 'enable', self.MESHCHAT_SERVICE_NAME],
                    capture_output=True, timeout=15,
                )

            print("Service created and enabled.\n")
            return True

        except (IOError, OSError, subprocess.SubprocessError) as e:
            print(f"Failed to create service: {e}")
            return False

    # ------------------------------------------------------------------
    # Uninstall (stop + disable)
    # ------------------------------------------------------------------

    def _uninstall_meshchat(self):
        """Stop and disable MeshChat service.

        Leaves files in place for easy re-enable. Does not delete
        the cloned repository or configuration.
        """
        if not self.ctx.dialog.yesno(
            "Disable MeshChat",
            "Stop and disable the MeshChat service?\n\n"
            "This will:\n"
            "  - Stop MeshChat if running\n"
            "  - Disable auto-start on boot\n\n"
            "Files remain at ~/reticulum-meshchat\n"
            "for easy re-enable later.\n\n"
            "Disable now?",
        ):
            return

        clear_screen()
        print("=== Disabling MeshChat ===\n")

        # Stop service
        try:
            print("Stopping MeshChat...")
            subprocess.run(
                ['systemctl', 'stop', self.MESHCHAT_SERVICE_NAME],
                capture_output=True, timeout=15,
            )
        except (subprocess.SubprocessError, OSError) as e:
            logger.debug("Service stop: %s", e)

        # Kill any running process
        try:
            subprocess.run(
                ['pkill', '-f', 'meshchat.py'],
                capture_output=True, timeout=5,
            )
        except (subprocess.SubprocessError, OSError):
            pass

        time.sleep(1)

        # Disable service
        try:
            print("Disabling auto-start...")
            subprocess.run(
                ['systemctl', 'disable', self.MESHCHAT_SERVICE_NAME],
                capture_output=True, timeout=15,
            )
        except (subprocess.SubprocessError, OSError) as e:
            logger.debug("Service disable: %s", e)

        install_dir = self._get_meshchat_install_dir()
        if self._is_meshchat_running():
            print("\nMeshChat may still be running.")
            print("Try: sudo pkill -f meshchat.py")
        else:
            print("\nMeshChat stopped and disabled.")

        print(f"\nFiles remain at: {install_dir}")
        print("To re-enable: systemctl enable --now reticulum-meshchat")

        self.ctx.wait_for_enter()

    # ------------------------------------------------------------------
    # Cross-handler helpers
    # ------------------------------------------------------------------

    def _get_rnsd_user(self):
        """Get the OS user running rnsd, via the rns_diagnostics handler.

        Returns None if the handler is unavailable or rnsd is not running.
        """
        if self.ctx.registry:
            diag = self.ctx.registry.get_handler("rns_diagnostics")
            if diag and hasattr(diag, '_get_rnsd_user'):
                return diag._get_rnsd_user()
        return None

    def _fix_rnsd_user(self, target_user: str) -> bool:
        """Fix rnsd to run as the specified user, via the rns_diagnostics handler.

        Returns False if the handler is unavailable.
        """
        if self.ctx.registry:
            diag = self.ctx.registry.get_handler("rns_diagnostics")
            if diag and hasattr(diag, '_fix_rnsd_user'):
                return diag._fix_rnsd_user(target_user)
        return False

    # ------------------------------------------------------------------
    # Preflight: RNS check
    # ------------------------------------------------------------------

    def _check_rns_for_meshchat(self) -> bool:
        """Check that RNS is available for MeshChat.

        MeshChat can run with or without rnsd:
        - With rnsd: connects as shared instance client (recommended)
        - Without: starts its own RNS instance

        Returns True to proceed, False if user cancelled.
        """
        rnsd_user = self._get_rnsd_user()

        if not rnsd_user:
            # rnsd not running — warn but allow proceeding
            return self.ctx.dialog.yesno(
                "rnsd Not Running",
                "The RNS daemon (rnsd) is not running.\n\n"
                "MeshChat can start its own RNS instance,\n"
                "but for Meshtastic bridging you should run rnsd\n"
                "with share_instance = Yes in the Reticulum config.\n\n"
                "Continue anyway?",
            )

        # rnsd is running — check for root mismatch
        sudo_user = os.environ.get('SUDO_USER', '')
        if rnsd_user == 'root' and sudo_user and sudo_user != 'root':
            choice = self.ctx.dialog.menu(
                "rnsd Running as Root",
                f"rnsd is running as root, but MeshChat should\n"
                f"use the same RNS identity as '{sudo_user}'.\n\n"
                "This may cause RPC authentication failures.",
                [
                    ("continue", "Continue anyway"),
                    ("fix", f"Fix rnsd to run as {sudo_user}"),
                    ("cancel", "Cancel"),
                ],
            )
            if choice == "fix":
                self._fix_rnsd_user(sudo_user)
                return True
            elif choice == "cancel" or choice is None:
                return False
            # "continue" falls through

        return True
