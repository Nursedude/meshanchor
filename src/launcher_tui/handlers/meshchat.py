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
import re
import shutil
import socket
import subprocess
import time
from pathlib import Path

from backend import clear_screen
from handler_protocol import BaseHandler
from handlers._lxmf_utils import ensure_lxmf_exclusive

from utils.common import SettingsManager
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
check_process_running, start_service, stop_service, restart_service, _HAS_SERVICE_CHECK = safe_import(
    'utils.service_check', 'check_process_running', 'start_service', 'stop_service', 'restart_service'
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


# Regex for upstream meshchat.py strptime bug (Peewee returns datetime, not str)
_STRPTIME_ASSIGNMENT_RE = re.compile(
    r'^( +)(\w+) = datetime\.strptime\((\w+\.\w+), (".*?")\)$',
    re.MULTILINE,
)


def _build_strptime_replacement(match: re.Match) -> str:
    """Build isinstance-guarded replacement for a strptime assignment."""
    indent, var, field_expr, fmt = match.group(1, 2, 3, 4)
    return (
        f"{indent}{var} = {field_expr} if isinstance({field_expr}, datetime) "
        f"else datetime.strptime({field_expr}, {fmt})"
    )


# Regex for upstream meshchat.py fromisoformat bug (same Peewee issue)
_FROMISOFORMAT_ASSIGNMENT_RE = re.compile(
    r'^( +)(\w+) = datetime\.fromisoformat\((\w+\.\w+)\)$',
    re.MULTILINE,
)


def _build_fromisoformat_replacement(match: re.Match) -> str:
    """Build isinstance-guarded replacement for a fromisoformat assignment."""
    indent, var, field_expr = match.group(1, 2, 3)
    return (
        f"{indent}{var} = {field_expr} if isinstance({field_expr}, datetime) "
        f"else datetime.fromisoformat({field_expr})"
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
            if self._check_lxmf_with_python(service_python, run_as_user):
                # Service python confirmed LXMF is importable — set the flag
                _HAS_LXMF = True
                # Ensure systemd service uses the correct Python
                self._verify_service_file()
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
        still_missing = self._check_meshchat_deps(service_python, run_as_user)

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
            still_missing = self._check_meshchat_deps(service_python, run_as_user)
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
                subtitle = f"MeshChat is RUNNING ({self._get_meshchat_url()})"
            else:
                subtitle = "MeshChat is installed (not running)"

            choices = [
                ("status", "MeshChat Status"),
            ]

            if installed:
                if running:
                    choices.append(("stop", "Stop MeshChat"))
                    choices.append(("restart", "Restart MeshChat"))
                    choices.append(("peers", "View LXMF Peers"))
                    choices.append(("messages", "Recent Messages"))
                    choices.append(("announce", "Send LXMF Announce"))
                    choices.append(("web", "Web UI (show URL)"))
                else:
                    choices.append(("start", "Start MeshChat"))
                if not self._has_meshchat_systemd_service():
                    choices.append(("create_service", "Create Systemd Service"))
                choices.append(("network", "Network Access (LAN/Local)"))
                choices.append(("npm", "NPM Management"))
                choices.append(("rebuild", "Rebuild Frontend"))
                choices.append(("fix-upstream", "Apply Upstream Fixes"))
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
                "restart": ("Restart MeshChat", self._restart_meshchat),
                "peers": ("View LXMF Peers", self._meshchat_peers),
                "messages": ("Recent Messages", self._meshchat_messages),
                "announce": ("Send LXMF Announce", self._meshchat_announce),
                "web": ("MeshChat Web UI", self._meshchat_web_ui),
                "logs": ("View MeshChat Logs", self._meshchat_logs),
                "install": ("Install MeshChat", self._install_meshchat),
                "create_service": ("Create Service", self._create_meshchat_service),
                "network": ("Network Access", self._configure_network_access),
                "npm": ("NPM Management", self._npm_management_menu),
                "rebuild": ("Rebuild Frontend", self._rebuild_frontend),
                "fix-upstream": ("Apply Upstream Fixes", self._apply_upstream_fixes_interactive),
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

        bind_host = self._get_meshchat_bind_host()
        bind_label = "LAN (0.0.0.0)" if bind_host == "0.0.0.0" else "Local (127.0.0.1)"

        # RNS config status
        user_home = get_real_user_home()
        rns_config = user_home / '.reticulum' / 'config'
        if rns_config.exists():
            rns_label = f"~/.reticulum (shared instance client)"
        else:
            rns_label = "Not configured (will use system default)"

        print(f"  Installed:  Yes")
        print(f"  Running:    {'Yes' if running else 'No'}")
        print(f"  Network:    {bind_label}")
        print(f"  RNS Config: {rns_label}")

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
            service_user = self._get_service_user()
            missing_deps = self._check_meshchat_deps(service_python, service_user)
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
                if status.port_open:
                    print(f"  Web UI:     {self._get_meshchat_url()}")
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

        # Preflight: ensure user-space RNS config for shared instance
        self._ensure_meshchat_rns_config()

        # Preflight: check LXMF availability
        if not _HAS_LXMF:
            if not self._offer_install_lxmf():
                return

        # Preflight: check MeshChat's own Python dependencies (aiohttp, etc.)
        if self._is_meshchat_installed():
            missing_deps = self._check_meshchat_deps(
                self._get_service_python(), self._get_service_user()
            )
            if missing_deps:
                if not self._offer_install_meshchat_deps(missing_deps):
                    return

        # Preflight: deploy upstream fixes (wrapper) and verify service file
        self._apply_upstream_fixes()
        self._verify_service_file()

        # Preflight: wait for RNS shared instance socket if rnsd is running
        self._wait_for_rns_shared_instance()

        if _HAS_MESHCHAT_SERVICE:
            svc = MeshChatService()
            status = svc.check_status(blocking=True)

            if status.running:
                self.ctx.dialog.msgbox(
                    "Already Running",
                    "MeshChat is already running.\n\n"
                    f"Web UI: {self._get_meshchat_url()}",
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
                        f"Web UI: {self._get_meshchat_url()}",
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

        # Ask about network access if not yet configured
        settings = self._get_meshchat_settings()
        if not settings.file_path.exists():
            self._prompt_network_access_during_install()

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
                    f"Web UI: {self._get_meshchat_url()}",
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
                    missing = self._check_meshchat_deps(
                        run_as_user=self._get_service_user()
                    )
                    if not missing:
                        missing = [mod_name]
                    self._offer_install_meshchat_deps(missing)
                return

            if 'datetime' in result.stdout and 'not JSON serializable' in result.stdout:
                if self.ctx.dialog.yesno(
                    "JSON Serialization Error",
                    "MeshChat crashed due to a datetime serialization bug.\n\n"
                    "This is a known upstream issue. Apply fix now?",
                ):
                    fixes = self._apply_upstream_fixes()
                    if fixes:
                        if _HAS_SERVICE_CHECK and start_service:
                            start_service(service_name)
                            time.sleep(3)
                            if self._is_meshchat_running():
                                self.ctx.dialog.msgbox(
                                    "Fixed & Restarted",
                                    f"Fix applied and MeshChat restarted.\n\n"
                                    f"Web UI: {self._get_meshchat_url()}",
                                )
                                return
                return

            if 'ConnectionRefusedError' in result.stdout:
                # RNS RPC socket not reachable
                rnsd_user = self._get_rnsd_user()
                rns_ready = (
                    _HAS_RNS_CHECK
                    and check_rns_shared_instance
                    and check_rns_shared_instance()
                )

                if not rnsd_user:
                    diag = (
                        "rnsd is NOT running.\n\n"
                        "MeshChat needs rnsd for RNS interface stats.\n"
                        "Start rnsd first, then retry MeshChat."
                    )
                elif not rns_ready:
                    diag = (
                        f"rnsd is running (as {rnsd_user}) but\n"
                        "the shared instance socket is not ready.\n\n"
                        "This usually means rnsd just restarted.\n"
                        "Retry MeshChat start?"
                    )
                else:
                    diag = (
                        f"rnsd is running (as {rnsd_user}) and the\n"
                        "shared instance socket is available now.\n\n"
                        "The error may have been transient.\n"
                        "Retry MeshChat start?"
                    )

                if self.ctx.dialog.yesno(
                    "RNS Connection Refused",
                    "MeshChat crashed: ConnectionRefusedError\n"
                    "on RNS RPC (get_interface_stats).\n\n"
                    f"{diag}",
                ):
                    if not rnsd_user and _HAS_SERVICE_CHECK and start_service:
                        start_service('rnsd')
                        time.sleep(3)
                    self._wait_for_rns_shared_instance()
                    if _HAS_SERVICE_CHECK and start_service:
                        start_service(service_name)
                        time.sleep(3)
                        if self._is_meshchat_running():
                            self.ctx.dialog.msgbox(
                                "MeshChat Started",
                                f"MeshChat is running.\n\n"
                                f"Web UI: {self._get_meshchat_url()}",
                            )
                            return
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

    def _stop_meshchat_process(self) -> bool:
        """Stop MeshChat process. Returns True if stopped successfully."""
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

        return stopped and not self._is_meshchat_running()

    def _stop_meshchat(self):
        """Stop MeshChat service."""
        if not self.ctx.dialog.yesno(
            "Stop MeshChat",
            "Stop the MeshChat service?\n\n"
            "LXMF messaging will be unavailable until restarted.",
        ):
            return

        if self._stop_meshchat_process():
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

    def _restart_meshchat(self):
        """Restart MeshChat service to apply config changes."""
        # Deploy upstream fixes before restart so the wrapper is in place
        self._apply_upstream_fixes()
        self._verify_service_file()

        self.ctx.dialog.infobox("Restarting", "Restarting MeshChat...")

        restarted = False

        if _HAS_MESHCHAT_SERVICE:
            svc = MeshChatService()
            status = svc.check_status(blocking=True)
            if status.service_name:
                success, msg = restart_service(status.service_name)
                if success:
                    time.sleep(3)
                    restarted = True

        if not restarted:
            # Fallback: manual stop/start cycle
            self._stop_meshchat_process()
            time.sleep(2)
            self._launch_meshchat()
            return  # _launch_meshchat shows its own result dialogs

        # Verify
        if self._is_meshchat_running():
            self.ctx.dialog.msgbox(
                "MeshChat Restarted",
                f"MeshChat has been restarted.\n\n"
                f"Web UI: {self._get_meshchat_url()}",
            )
        else:
            self._handle_start_failure("reticulum-meshchat")

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
        url = self._get_meshchat_url()
        self.ctx.dialog.msgbox(
            "MeshChat Web UI",
            f"MeshChat web interface is available at:\n\n"
            f"  {url}\n\n"
            f"Accessible from any device on the network.\n"
            f"Local access: http://127.0.0.1:8000",
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

    def _get_meshchat_settings(self) -> SettingsManager:
        """Return SettingsManager for MeshChat configuration."""
        if not hasattr(self, '_meshchat_settings'):
            self._meshchat_settings = SettingsManager(
                "meshchat",
                defaults={"bind_host": "127.0.0.1"},
            )
        return self._meshchat_settings

    def _get_meshchat_bind_host(self) -> str:
        """Return configured bind host, default 127.0.0.1."""
        return self._get_meshchat_settings().get("bind_host") or "127.0.0.1"

    def _ensure_meshchat_rns_config(self) -> Path:
        """Ensure a client-only RNS config exists for MeshChat.

        Creates ~/.reticulum/config with share_instance=Yes and no
        interfaces, so MeshChat connects to rnsd as a shared instance
        client rather than starting its own RNS instance.

        Returns the RNS config directory path.
        """
        user_home = get_real_user_home()
        rns_dir = user_home / '.reticulum'
        rns_config = rns_dir / 'config'

        if rns_config.exists():
            return rns_dir

        logger.info("Creating client-only RNS config at %s", rns_config)
        rns_dir.mkdir(parents=True, exist_ok=True)
        rns_config.write_text(
            "[reticulum]\n"
            "  share_instance = Yes\n"
            "  shared_instance_port = 37428\n"
            "  instance_control_port = 37429\n"
            "\n"
            "[interfaces]\n"
            "  # No interfaces - rnsd manages hardware\n"
        )

        # Ensure owned by service user (not root when running under sudo)
        service_user = self._get_service_user()
        if service_user and service_user != 'root':
            try:
                subprocess.run(
                    ['chown', '-R', f'{service_user}:{service_user}', str(rns_dir)],
                    capture_output=True, timeout=10,
                )
            except (subprocess.SubprocessError, OSError) as e:
                logger.debug("chown on %s failed: %s", rns_dir, e)

        return rns_dir

    def _get_meshchat_url(self) -> str:
        """Get MeshChat web UI URL using hostname for display."""
        bind_host = self._get_meshchat_bind_host()
        if bind_host == "127.0.0.1":
            return "http://localhost:8000"
        try:
            hostname = socket.gethostname()
            return f"http://{hostname}:8000"
        except Exception:
            return "http://localhost:8000"

    def _configure_network_access(self):
        """Configure MeshChat bind address (LAN vs local-only)."""
        current = self._get_meshchat_bind_host()
        current_label = "LAN (0.0.0.0)" if current == "0.0.0.0" else "Local (127.0.0.1)"

        choices = [
            ("local", "Local only (127.0.0.1)  — secure, this machine only"),
            ("lan", "LAN accessible (0.0.0.0) — access from other devices"),
        ]

        choice = self.ctx.dialog.menu(
            "Network Access",
            f"Current: {current_label}\n\n"
            "Choose how MeshChat binds its web UI port (8000).\n"
            "'LAN accessible' lets you reach it from other devices\n"
            "on your network (e.g. 192.168.x.x:8000).",
            choices,
        )

        if choice is None:
            return

        new_host = "127.0.0.1" if choice == "local" else "0.0.0.0"
        if new_host == current:
            self.ctx.dialog.msgbox("No Change", f"Already set to {current_label}.")
            return

        # Save setting
        settings = self._get_meshchat_settings()
        settings.set("bind_host", new_host)
        settings.save()

        new_label = "LAN (0.0.0.0)" if new_host == "0.0.0.0" else "Local (127.0.0.1)"

        # Regenerate service file if it exists
        if self._has_meshchat_systemd_service():
            install_dir = self._get_meshchat_install_dir()
            run_as = self._get_service_user()
            self._install_meshchat_service(install_dir, run_as)

            # Restart if running
            if self._is_meshchat_running():
                self._stop_meshchat_process()
                time.sleep(1)
                self._launch_meshchat()
                self.ctx.dialog.msgbox(
                    "Updated",
                    f"Bind changed to {new_label}.\n"
                    f"Service restarted.\n\n"
                    f"URL: {self._get_meshchat_url()}",
                )
            else:
                self.ctx.dialog.msgbox(
                    "Updated",
                    f"Bind changed to {new_label}.\n"
                    "Start MeshChat to apply.",
                )
        else:
            self.ctx.dialog.msgbox(
                "Updated",
                f"Bind set to {new_label}.\n"
                "Create a systemd service to apply.",
            )

    def _prompt_network_access_during_install(self):
        """Ask user about network access during initial install.

        Presents a simple choice and persists the setting so the
        service file is generated with the correct bind host.
        """
        choices = [
            ("lan", "LAN accessible (0.0.0.0) — access from any device"),
            ("local", "Local only (127.0.0.1)  — this machine only"),
        ]

        choice = self.ctx.dialog.menu(
            "Network Access",
            "How should the MeshChat web UI be accessible?\n\n"
            "LAN: Access from other devices (e.g. 192.168.x.x:8000)\n"
            "Local: Only from this machine (localhost:8000)",
            choices,
        )

        if choice is None:
            # Default to LAN — most common use case for mesh setups
            choice = "lan"

        new_host = "127.0.0.1" if choice == "local" else "0.0.0.0"
        settings = self._get_meshchat_settings()
        settings.set("bind_host", new_host)
        settings.save()

        label = "LAN (0.0.0.0)" if new_host == "0.0.0.0" else "Local (127.0.0.1)"
        print(f"Network access: {label}\n")

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

    def _get_service_user(self) -> str:
        """Return the user the MeshChat service runs as.

        Checks the systemd service file first, falls back to SUDO_USER.
        Returns None if running as root without sudo.
        """
        service_path = Path(
            f"/etc/systemd/system/{self.MESHCHAT_SERVICE_NAME}.service"
        )
        if service_path.exists():
            try:
                for line in service_path.read_text().splitlines():
                    stripped = line.strip()
                    if stripped.startswith('User='):
                        user = stripped.split('=', 1)[1].strip()
                        if user and user != 'root':
                            return user
            except (IOError, OSError):
                pass
        sudo_user = os.environ.get('SUDO_USER')
        if sudo_user and sudo_user != 'root':
            return sudo_user
        return None

    def _check_meshchat_deps(self, python_path: str = None,
                             run_as_user: str = None) -> list:
        """Check if MeshChat's key Python deps are importable by service Python.

        When run_as_user is set and no venv is in use, runs the import check
        as that user so user-site-packages (--user installs) are visible.

        Returns a list of missing module names (empty = all OK).
        """
        if python_path is None:
            python_path = self._get_service_python()

        # Key deps from MeshChat's requirements.txt that cause crash-loops
        required_modules = ['aiohttp', 'cryptography']
        missing = []
        using_venv = Path('/opt/meshforge/venv/bin/python3').is_file()

        for mod in required_modules:
            try:
                cmd = [python_path, '-c', f'import {mod}']
                # When not using venv, run as the service user so that
                # user-site-packages (~user/.local/lib/...) are visible.
                if run_as_user and not using_venv:
                    cmd = ['sudo', '-H', '-u', run_as_user] + cmd
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    timeout=10,
                )
                if result.returncode != 0:
                    missing.append(mod)
            except (subprocess.SubprocessError, OSError):
                missing.append(mod)

        return missing

    def _check_lxmf_with_python(self, python_path: str = None,
                                run_as_user: str = None) -> bool:
        """Check if LXMF is importable by the service's Python interpreter.

        Uses subprocess to test the import in the correct Python environment,
        avoiding false negatives when the current process's sys.path differs
        from the service's (e.g., sudo python3 vs venv python3).

        When run_as_user is set and no venv is in use, runs the import check
        as that user so user-site-packages are visible.
        """
        if python_path is None:
            python_path = self._get_service_python()
        try:
            cmd = [python_path, '-c', 'import LXMF']
            using_venv = Path('/opt/meshforge/venv/bin/python3').is_file()
            if run_as_user and not using_venv:
                cmd = ['sudo', '-H', '-u', run_as_user] + cmd
            result = subprocess.run(
                cmd,
                capture_output=True,
                timeout=10,
            )
            return result.returncode == 0
        except (subprocess.SubprocessError, OSError) as e:
            logger.debug("LXMF import check failed for %s: %s", python_path, e)
            return False

    def _verify_service_file(self):
        """Check if the systemd service file is current and correct.

        Detects and fixes:
        - Wrong Python path in ExecStart
        - StartLimitIntervalSec in [Service] instead of [Unit] (stale file)
        - Bind host mismatch vs configured setting
        - ExecStart not using wrapper (datetime/RPC fixes)
        - Missing --reticulum-config-dir (RNS shared instance)

        Regenerates the service file if any issues are found.
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

        needs_regen = False
        expected_python = self._get_service_python()
        lines = content.splitlines()

        # Check Python path in ExecStart
        for line in lines:
            if line.strip().startswith('ExecStart='):
                exec_start = line.strip().split('=', 1)[1]
                current_python = exec_start.split()[0] if exec_start else ''
                if current_python != expected_python:
                    logger.warning(
                        "Service uses %s but pip target is %s",
                        current_python, expected_python,
                    )
                    needs_regen = True
                break

        # Check for StartLimitIntervalSec in [Service] (belongs in [Unit])
        in_service_section = False
        for line in lines:
            stripped = line.strip()
            if stripped == '[Service]':
                in_service_section = True
            elif stripped.startswith('[') and stripped.endswith(']'):
                in_service_section = False
            elif in_service_section and 'StartLimitIntervalSec' in stripped:
                logger.warning("StartLimitIntervalSec in [Service] — stale file")
                needs_regen = True
                break

        # Check bind host matches configured setting
        configured_host = self._get_meshchat_bind_host()
        other_host = "0.0.0.0" if configured_host == "127.0.0.1" else "127.0.0.1"
        if f'--host {other_host}' in content:
            logger.warning(
                "Service binds to %s — updating to %s",
                other_host, configured_host,
            )
            needs_regen = True

        # Check if ExecStart uses the wrapper (datetime/RPC resilience fixes)
        wrapper_path = self._get_meshchat_install_dir() / self.WRAPPER_FILENAME
        if wrapper_path.exists() and self.WRAPPER_FILENAME not in content:
            logger.warning(
                "Service uses meshchat.py directly — updating to wrapper"
            )
            needs_regen = True

        # Check for missing --reticulum-config-dir (RNS shared instance)
        if '--reticulum-config-dir' not in content:
            logger.warning("Service missing --reticulum-config-dir — adding")
            needs_regen = True

        if needs_regen:
            install_dir = self._get_meshchat_install_dir()
            run_as = self._get_service_user()
            self._install_meshchat_service(install_dir, run_as)

    def _install_meshchat(self):
        """Automated MeshChat installation.

        Steps:
        1. Confirm with user
        2. Check/install prerequisites (git, nodejs, npm)
        3. git clone reticulum-meshchat
        4. pip install -r requirements.txt
        5. npm install && npm run build-frontend
        6. Choose network access (LAN/Local)
        7. Create systemd service
        8. Enable + start service
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
            "  5. Choose network access (LAN/Local)\n"
            "  6. Create a systemd service\n\n"
            "MeshChat provides LXMF messaging with a\n"
            "web UI on port 8000\n\n"
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

            # Step 2.5: Apply known upstream fixes
            fixes = self._apply_upstream_fixes(install_dir)
            if fixes:
                print(f"Applied upstream fixes: {', '.join(fixes)}")

            # Step 3: pip install
            if not self._install_meshchat_pip(install_dir, run_as_user):
                self.ctx.wait_for_enter()
                return

            # Step 4: npm build
            if not self._install_meshchat_npm(install_dir, run_as_user):
                self.ctx.wait_for_enter()
                return

            # Step 5: Network access choice (before service creation)
            self._prompt_network_access_during_install()

            # Step 6: systemd service
            if not self._install_meshchat_service(install_dir, run_as_user):
                print("\nSystemd service creation failed.")
                print("You can still run MeshChat manually:")
                print(f"  cd {install_dir} && python3 meshchat.py")

            # Step 7: Start service
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
                print(f"Web UI: {self._get_meshchat_url()}")
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
            # Re-apply upstream fixes after pull (they may get overwritten)
            fixes = self._apply_upstream_fixes(install_dir)
            if fixes:
                print(f"Applied upstream fixes: {', '.join(fixes)}")
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

    # ------------------------------------------------------------------
    # Upstream Fixes
    # ------------------------------------------------------------------

    WRAPPER_FILENAME = 'meshforge_wrapper.py'

    _WRAPPER_VERSION = 8  # Bump when _WRAPPER_CONTENT changes

    _WRAPPER_CONTENT = '''\
#!/usr/bin/env python3
# meshforge_wrapper_version: 8
"""MeshForge wrapper - patches and pre-checks before MeshChat starts.

Fixes applied:
1. Monkey-patches json.JSONEncoder.default to handle datetime objects
   (upstream MeshChat passes datetime to aiohttp json_response without handler)
2. Waits for RNS shared instance socket before starting MeshChat
   (prevents ConnectionRefusedError on get_interface_stats RPC calls at startup)
3. Monkey-patches RNS RPC methods to catch ConnectionRefusedError at runtime
   (returns safe empty values instead of crashing the web UI)
4. Monkey-patches datetime.strptime and datetime.fromisoformat to handle
   Peewee datetime objects (prevents TypeError in meshchat.py)

Safe to remove once upstream fixes the issues.
Created by MeshForge. Do not edit — it will be regenerated on update.
"""
import datetime
import json
import os
import runpy
import sys
import time

# --- Fix 1: datetime JSON serialization ---
_original_default = json.JSONEncoder.default


def _patched_default(self, obj):
    if isinstance(obj, (datetime.datetime, datetime.date, datetime.time)):
        return obj.isoformat()
    return _original_default(self, obj)


json.JSONEncoder.default = _patched_default


# --- Fix 2: Wait for RNS shared instance (best-effort, 10s max) ---
def _rns_shared_instance_ready():
    """Check /proc/net/unix for @rns/default abstract socket."""
    try:
        with open('/proc/net/unix', 'r') as f:
            return any('@rns/default' in line for line in f)
    except OSError:
        return False


# Only wait if rnsd appears to be running (don't block standalone mode)
def _rnsd_running():
    """Quick check if rnsd process exists."""
    try:
        for entry in os.listdir('/proc'):
            if entry.isdigit():
                try:
                    cmdline = open(f'/proc/{entry}/cmdline', 'rb').read()
                    if b'rnsd' in cmdline:
                        return True
                except (OSError, PermissionError):
                    pass
    except OSError:
        pass
    return False


if _rnsd_running() and not _rns_shared_instance_ready():
    for _i in range(10):
        if _rns_shared_instance_ready():
            break
        time.sleep(1)


# --- Fix 3: Resilient RNS RPC calls (runtime failures) ---
# MeshChat calls get_interface_stats() on every web request (index handler).
# If rnsd drops, restarts (new auth key), or is unreachable, the RPC call
# crashes the entire web UI. Catch all exceptions from RPC calls and return
# safe empty values. KeyboardInterrupt/SystemExit are not Exception subclasses.
try:
    import RNS

    _original_get_interface_stats = RNS.Reticulum.get_interface_stats
    _original_get_path_table = RNS.Reticulum.get_path_table

    def _safe_get_interface_stats(self):
        try:
            return _original_get_interface_stats(self)
        except Exception:
            return {"interfaces": []}

    def _safe_get_path_table(self):
        try:
            return _original_get_path_table(self)
        except Exception:
            return []

    RNS.Reticulum.get_interface_stats = _safe_get_interface_stats
    RNS.Reticulum.get_path_table = _safe_get_path_table
except ImportError:
    pass  # RNS not installed — meshchat.py will fail on its own


# --- Fix 4: Resilient datetime parsing (handles Peewee datetime objects) ---
# datetime.datetime is a C type — can't set attributes on it directly.
# Instead, replace it in the module with a subclass that overrides parsing methods.
# meshchat.py's "from datetime import datetime" picks up the patched version.
class _PatchedDatetime(datetime.datetime):
    @classmethod
    def strptime(cls, date_string, format_str):
        if isinstance(date_string, datetime.datetime):
            return date_string
        if isinstance(date_string, datetime.date):
            return datetime.datetime(
                date_string.year, date_string.month, date_string.day
            )
        return super().strptime(date_string, format_str)

    @classmethod
    def fromisoformat(cls, date_string):
        if isinstance(date_string, datetime.datetime):
            return date_string
        if isinstance(date_string, datetime.date):
            return datetime.datetime(
                date_string.year, date_string.month, date_string.day
            )
        return super().fromisoformat(date_string)


datetime.datetime = _PatchedDatetime

# Run meshchat.py in this process, preserving __main__ semantics
_script_dir = os.path.dirname(os.path.abspath(__file__))
_meshchat_path = os.path.join(_script_dir, "meshchat.py")
sys.argv[0] = _meshchat_path
runpy.run_path(_meshchat_path, run_name="__main__")
'''

    def _create_meshchat_wrapper(self, install_dir: Path = None) -> Path:
        """Create a wrapper script that patches datetime serialization.

        The wrapper monkey-patches json.JSONEncoder.default before
        executing meshchat.py, so datetime objects serialize to ISO strings.
        """
        if install_dir is None:
            install_dir = self._get_meshchat_install_dir()

        wrapper_path = install_dir / self.WRAPPER_FILENAME
        wrapper_path.write_text(self._WRAPPER_CONTENT)
        wrapper_path.chmod(0o755)

        # Preserve ownership to match meshchat.py
        meshchat_py = install_dir / 'meshchat.py'
        if meshchat_py.exists():
            stat_info = meshchat_py.stat()
            try:
                os.chown(wrapper_path, stat_info.st_uid, stat_info.st_gid)
            except OSError:
                pass
        return wrapper_path

    def _wrapper_needs_update(self, wrapper_path: Path) -> bool:
        """Check if existing wrapper is outdated and needs regeneration."""
        try:
            content = wrapper_path.read_text()
            for line in content.splitlines():
                if line.startswith('# meshforge_wrapper_version:'):
                    version = int(line.split(':')[1].strip())
                    return version < self._WRAPPER_VERSION
            # No version marker → old wrapper before versioning was added
            return True
        except (OSError, ValueError):
            return True

    def _update_service_to_wrapper(self, install_dir: Path = None) -> bool:
        """Update systemd ExecStart to use the wrapper script.

        Reads the existing service file, replaces meshchat.py with
        meshforge_wrapper.py in ExecStart, writes back, and reloads.
        """
        if install_dir is None:
            install_dir = self._get_meshchat_install_dir()

        service_path = (
            f"/etc/systemd/system/{self.MESHCHAT_SERVICE_NAME}.service"
        )
        try:
            content = Path(service_path).read_text()
        except OSError:
            return False

        meshchat_py = str(install_dir / 'meshchat.py')
        wrapper_str = str(install_dir / self.WRAPPER_FILENAME)

        if wrapper_str in content:
            return False  # Already using wrapper

        if meshchat_py not in content:
            return False  # Can't find target to replace

        new_content = content.replace(meshchat_py, wrapper_str)
        if _HAS_SUDO_WRITE and _sudo_write:
            ok, msg = _sudo_write(service_path, new_content)
            if ok:
                subprocess.run(
                    ['systemctl', 'daemon-reload'],
                    capture_output=True, timeout=15,
                )
                return True
        return False

    def _apply_upstream_fixes(self, install_dir: Path = None) -> list:
        """Apply known upstream fixes to MeshChat. Returns list of fixes applied.

        Creates a wrapper script that monkey-patches datetime serialization,
        waits for RNS, and patches RPC methods for resilience. Updates the
        systemd service to use the wrapper. Safe and idempotent — regenerates
        the wrapper when a new version is available.
        """
        if install_dir is None:
            install_dir = self._get_meshchat_install_dir()

        if not (install_dir / 'meshchat.py').exists():
            return []

        fixes = []

        # Create or update wrapper (versioned — regenerates when outdated)
        wrapper = install_dir / self.WRAPPER_FILENAME
        if not wrapper.exists():
            self._create_meshchat_wrapper(install_dir)
            fixes.append('upstream fixes (wrapper created)')
        elif self._wrapper_needs_update(wrapper):
            self._create_meshchat_wrapper(install_dir)
            fixes.append('upstream fixes (wrapper updated)')

        # Patch strptime calls in meshchat.py source (idempotent)
        if self._apply_strptime_patch(install_dir):
            fixes.append('strptime datetime fix applied')

        # Patch fromisoformat calls in meshchat.py source (idempotent)
        if self._apply_fromisoformat_patch(install_dir):
            fixes.append('fromisoformat datetime fix applied')

        # Ensure systemd service points to wrapper
        if self._update_service_to_wrapper(install_dir):
            fixes.append('systemd service updated')

        return fixes

    def _apply_strptime_patch(self, install_dir: Path) -> bool:
        """Patch meshchat.py strptime calls to handle datetime objects.

        Upstream bug: Peewee DateTimeField returns datetime objects, but
        meshchat.py calls datetime.strptime() on them -> TypeError.
        Idempotent -- regex won't match already-patched lines.
        """
        meshchat_py = install_dir / 'meshchat.py'
        if not meshchat_py.exists():
            return False

        try:
            source = meshchat_py.read_text(encoding='utf-8')
        except OSError:
            return False

        patched = _STRPTIME_ASSIGNMENT_RE.sub(
            _build_strptime_replacement, source
        )
        if patched == source:
            return False  # No changes (already patched or no matches)

        try:
            meshchat_py.write_text(patched, encoding='utf-8')
        except OSError:
            return False
        return True

    def _apply_fromisoformat_patch(self, install_dir: Path) -> bool:
        """Patch meshchat.py fromisoformat calls to handle datetime objects.

        Same upstream bug as strptime: Peewee DateTimeField returns datetime,
        but meshchat.py calls datetime.fromisoformat() on them -> TypeError.
        Idempotent -- regex won't match already-patched lines.
        """
        meshchat_py = install_dir / 'meshchat.py'
        if not meshchat_py.exists():
            return False

        try:
            source = meshchat_py.read_text(encoding='utf-8')
        except OSError:
            return False

        patched = _FROMISOFORMAT_ASSIGNMENT_RE.sub(
            _build_fromisoformat_replacement, source
        )
        if patched == source:
            return False

        try:
            meshchat_py.write_text(patched, encoding='utf-8')
        except OSError:
            return False
        return True

    def _apply_upstream_fixes_interactive(self):
        """Apply known upstream fixes with user feedback."""
        fixes = self._apply_upstream_fixes()
        if fixes:
            msg = "Applied fixes:\n" + "\n".join(f"  - {f}" for f in fixes)
            if self._is_meshchat_running():
                if self.ctx.dialog.yesno(
                    "Fixes Applied",
                    msg + "\n\nRestart MeshChat to apply changes?",
                ):
                    self._restart_meshchat()
                    return
            self.ctx.dialog.msgbox("Fixes Applied", msg)
        else:
            self.ctx.dialog.msgbox(
                "No Fixes Needed",
                "MeshChat is up to date — no known upstream\n"
                "fixes to apply.",
            )

    # ------------------------------------------------------------------
    # NPM Management
    # ------------------------------------------------------------------

    def _run_npm_command(self, args: list) -> subprocess.CompletedProcess:
        """Run an npm command in the MeshChat install directory.

        Handles sudo elevation and install directory resolution.
        Returns the CompletedProcess result.
        """
        install_dir = self._get_meshchat_install_dir()
        sudo_user = os.environ.get('SUDO_USER')
        run_as_user = sudo_user if sudo_user and sudo_user != 'root' else None

        cmd = ['npm'] + args
        if run_as_user:
            cmd = ['sudo', '-H', '-u', run_as_user] + cmd

        return subprocess.run(
            cmd, cwd=str(install_dir), capture_output=False, timeout=300,
        )

    def _npm_check_installed(self) -> bool:
        """Verify MeshChat has a package.json. Shows dialog if not."""
        install_dir = self._get_meshchat_install_dir()
        if not (install_dir / 'package.json').exists():
            self.ctx.dialog.msgbox(
                "Not Found",
                f"No package.json in {install_dir}.\n\n"
                "MeshChat may not be installed correctly.",
            )
            return False
        return True

    def _npm_management_menu(self):
        """NPM package management for MeshChat frontend."""
        if not self._npm_check_installed():
            return

        while True:
            choices = [
                ("audit", "Security Audit      npm audit"),
                ("audit-fix", "Auto-fix Vulns      npm audit fix"),
                ("outdated", "Check Outdated      npm outdated"),
                ("update", "Update Packages     npm update"),
                ("logs", "View npm Logs"),
                ("rebuild", "Rebuild Frontend    npm install + build"),
                ("back", "Back"),
            ]

            choice = self.ctx.dialog.menu(
                "NPM Management",
                "Manage MeshChat frontend dependencies:",
                choices,
            )

            if choice is None or choice == "back":
                break

            dispatch = {
                "audit": ("Security Audit", self._npm_audit),
                "audit-fix": ("Auto-fix Vulnerabilities", self._npm_audit_fix),
                "outdated": ("Check Outdated", self._npm_outdated),
                "update": ("Update Packages", self._npm_update),
                "logs": ("View npm Logs", self._npm_view_logs),
                "rebuild": ("Rebuild Frontend", self._rebuild_frontend),
            }
            entry = dispatch.get(choice)
            if entry:
                self.ctx.safe_call(*entry)

    def _npm_audit(self):
        """Run npm audit to check for vulnerabilities."""
        clear_screen()
        print("=== NPM Security Audit ===\n")
        result = self._run_npm_command(['audit'])
        # npm audit returns non-zero when vulnerabilities found (not an error)
        if result.returncode > 1:
            print("\nnpm audit encountered an error.")
        print()
        self.ctx.wait_for_enter()

    def _npm_audit_fix(self):
        """Run npm audit fix to auto-resolve vulnerabilities."""
        if not self.ctx.dialog.yesno(
            "Auto-fix Vulnerabilities",
            "Run 'npm audit fix' to automatically resolve\n"
            "known vulnerabilities?\n\n"
            "This modifies package-lock.json and node_modules.",
        ):
            return

        was_running = self._is_meshchat_running()
        if was_running:
            self.ctx.dialog.infobox("Stopping", "Stopping MeshChat for npm fix...")
            self._stop_meshchat_process()
            time.sleep(1)

        clear_screen()
        print("=== NPM Audit Fix ===\n")
        result = self._run_npm_command(['audit', 'fix'])

        if result.returncode == 0:
            print("\nAudit fix complete.")
            # Rebuild frontend after dependency changes
            print("\nRebuilding frontend...\n")
            install_dir = self._get_meshchat_install_dir()
            sudo_user = os.environ.get('SUDO_USER')
            run_as_user = sudo_user if sudo_user and sudo_user != 'root' else None
            self._install_meshchat_npm(install_dir, run_as_user)
        else:
            print("\nnpm audit fix encountered issues.")
            print("Try: npm audit fix --force  (may introduce breaking changes)")

        if was_running:
            print("\nRestarting MeshChat...")
            self._launch_meshchat()
        else:
            print()
            self.ctx.wait_for_enter()

    def _npm_outdated(self):
        """Run npm outdated to check for outdated packages."""
        clear_screen()
        print("=== Outdated NPM Packages ===\n")
        result = self._run_npm_command(['outdated'])
        # npm outdated returns 1 when outdated packages exist (not an error)
        if result.returncode == 0:
            print("All packages are up to date.")
        print()
        self.ctx.wait_for_enter()

    def _npm_update(self):
        """Run npm update to update packages within semver ranges."""
        if not self.ctx.dialog.yesno(
            "Update Packages",
            "Run 'npm update' to update packages within\n"
            "their allowed semver ranges?\n\n"
            "This modifies package-lock.json and node_modules.\n"
            "Frontend will be rebuilt after update.",
        ):
            return

        was_running = self._is_meshchat_running()
        if was_running:
            self.ctx.dialog.infobox("Stopping", "Stopping MeshChat for update...")
            self._stop_meshchat_process()
            time.sleep(1)

        clear_screen()
        print("=== NPM Update ===\n")
        result = self._run_npm_command(['update'])

        if result.returncode == 0:
            print("\nPackages updated.")
            # Rebuild frontend after dependency changes
            print("\nRebuilding frontend...\n")
            install_dir = self._get_meshchat_install_dir()
            sudo_user = os.environ.get('SUDO_USER')
            run_as_user = sudo_user if sudo_user and sudo_user != 'root' else None
            self._install_meshchat_npm(install_dir, run_as_user)
        else:
            print("\nnpm update failed.")

        if was_running:
            print("\nRestarting MeshChat...")
            self._launch_meshchat()
        else:
            print()
            self.ctx.wait_for_enter()

    def _npm_view_logs(self):
        """View recent npm debug logs."""
        clear_screen()
        print("=== NPM Logs ===\n")

        # npm stores logs in ~/.npm/_logs/
        npm_log_dir = get_real_user_home() / '.npm' / '_logs'

        if not npm_log_dir.is_dir():
            print(f"No npm log directory found at {npm_log_dir}")
            print()
            self.ctx.wait_for_enter()
            return

        # List log files sorted by modification time (newest first)
        try:
            log_files = sorted(
                npm_log_dir.glob('*.log'),
                key=lambda f: f.stat().st_mtime,
                reverse=True,
            )
        except OSError as e:
            print(f"Error reading log directory: {e}")
            print()
            self.ctx.wait_for_enter()
            return

        if not log_files:
            print("No npm log files found.")
            print()
            self.ctx.wait_for_enter()
            return

        print(f"Found {len(log_files)} log file(s).\n")

        # Show list of recent logs
        recent = log_files[:10]
        choices = []
        for i, lf in enumerate(recent):
            # Format: timestamp from filename + size
            size_kb = lf.stat().st_size / 1024
            choices.append((str(i), f"{lf.name}  ({size_kb:.1f} KB)"))
        choices.append(("back", "Back"))

        choice = self.ctx.dialog.menu(
            "NPM Logs",
            f"{len(log_files)} log file(s) in {npm_log_dir}\n\n"
            "Select a log to view:",
            choices,
        )

        if choice is None or choice == "back":
            return

        try:
            idx = int(choice)
            log_path = recent[idx]
            content = log_path.read_text(errors='replace')
            # Truncate if very large
            if len(content) > 16000:
                content = content[-16000:]
                content = f"... (truncated, showing last 16KB) ...\n{content}"
            clear_screen()
            print(f"=== {log_path.name} ===\n")
            print(content)
            print()
            self.ctx.wait_for_enter()
        except (ValueError, IndexError, OSError) as e:
            self.ctx.dialog.msgbox("Error", f"Could not read log: {e}")

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
        bind_host = self._get_meshchat_bind_host()
        rns_config_dir = self._ensure_meshchat_rns_config()

        # Use wrapper if available (patches datetime serialization)
        wrapper_path = install_dir / self.WRAPPER_FILENAME
        exec_target = wrapper_path if wrapper_path.exists() else meshchat_py

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
            f"ExecStartPre=/bin/sh -c '"
            f"for i in 1 2 3 4 5; do "
            f"grep -q rns/default /proc/net/unix 2>/dev/null && exit 0; "
            f"sleep 1; done; exit 0'\n"
            f"ExecStart={python_path} {exec_target}"
            f" --headless --host {bind_host}"
            f" --reticulum-config-dir {rns_config_dir}\n"
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

    def _wait_for_rns_shared_instance(self, timeout: int = 10):
        """Wait for the RNS shared instance socket to become available.

        When rnsd is running but just started (or restarted), there's a
        race window where the process exists but the RPC socket isn't
        ready yet.  This causes ConnectionRefusedError in MeshChat when
        it tries to call get_interface_stats().

        Polls passively (reads /proc/net/unix) for up to *timeout* seconds.
        If rnsd isn't running at all, returns immediately (MeshChat can
        start its own RNS instance).
        """
        if not self._get_rnsd_user():
            return  # rnsd not running — nothing to wait for

        if not (_HAS_RNS_CHECK and check_rns_shared_instance):
            return  # can't check — skip

        if check_rns_shared_instance():
            return  # already ready

        self.ctx.dialog.infobox(
            "Waiting for RNS",
            "rnsd is starting up — waiting for shared instance...",
        )

        for _ in range(timeout):
            time.sleep(1)
            if check_rns_shared_instance():
                return

        # Timed out — proceed anyway (MeshChat may still work or
        # _handle_start_failure will catch the error)
        logger.warning(
            "RNS shared instance not ready after %ds, proceeding anyway",
            timeout,
        )
