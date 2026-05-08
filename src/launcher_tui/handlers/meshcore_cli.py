"""
MeshCore CLI Handler — TUI passthrough to the upstream ``meshcore-cli`` tool.

The companion radio is owned by ``meshanchor-daemon.service`` whenever the
daemon is running (it holds the persistent owner via
``utils.meshcore_connection`` — see Issue #33). MeshCore has no host-side
daemon like meshtasticd, so the first process to open the device wins
exclusive ownership. ``meshcore-cli`` is a separate process; if we let
both fight for the wire the running session breaks silently.

v1 ownership model (Option A from
``project_meshcore_cli_tui_followup`` memory):
  1. Detect daemon state via ``service_check.check_service``.
  2. If active, ask the operator to stop it; otherwise the CLI will fail
     to open the radio while the daemon is alive.
  3. Run ``meshcore-cli <connection-args> <command>`` (or interactively).
  4. Restart the daemon when the CLI exits, even on Ctrl-C.

Connection args are derived from ``GatewayConfig.meshcore``:
  * ``serial`` → ``-s <device_path> -b <baud_rate>``
  * ``tcp``    → ``-t <tcp_host> -p <tcp_port>``
  * ``ble``    → ``-a <device_path>``  (device_path holds the BLE name/MAC)

See https://github.com/meshcore-dev/meshcore-cli for upstream flag/command
reference, and https://docs.meshcore.io/cli_commands/ for the on-radio
admin command set reachable via ``cmd <name> <admin-command>``.
"""

import logging
import shlex
import subprocess
from typing import List, Optional

from backend import clear_screen
from handler_protocol import BaseHandler
from utils.safe_import import safe_import

logger = logging.getLogger(__name__)

_GatewayConfig, _MeshCoreConfig, _HAS_GW_CONFIG = safe_import(
    'gateway.config', 'GatewayConfig', 'MeshCoreConfig'
)


class MeshCoreCLIHandler(BaseHandler):
    """Operator passthrough to ``meshcore-cli`` with daemon ownership handoff."""

    handler_id = "meshcore_cli"
    menu_section = "meshcore"

    DAEMON_SERVICE = "meshanchor-daemon.service"
    CLI_TIMEOUT = 30

    # Curated list of safe one-shot verbs from upstream meshcore-cli.
    # Any command not in this list falls through the "raw command" path
    # so the operator stays in control of what's sent to the radio.
    COMMON_VERBS = [
        ("infos", "infos               Show node info"),
        ("contacts", "contacts            List known contacts"),
        ("ver", "ver                 Firmware version"),
        ("self_telemetry", "self_telemetry      Local node telemetry"),
        ("req_status", "req_status          Request status from radio"),
        ("advert", "advert              Broadcast an advertisement"),
        ("recv", "recv                Drain pending RX once"),
        ("clock_sync", "clock sync          Sync radio clock to host time"),
    ]

    def menu_items(self):
        # 3rd slot is the deployment-profile feature flag — must be
        # "meshcore" (or None) per Phase 8.3 section invariants. The
        # CLI surface is meshcore-specific, so gate it on "meshcore".
        return [
            ("meshcore_cli", "MeshCore CLI        meshcore-cli passthrough", "meshcore"),
        ]

    def execute(self, action):
        if action == "meshcore_cli":
            self._cli_menu()

    # ─────────────────────────────────────────────────────────────────
    # Top-level menu
    # ─────────────────────────────────────────────────────────────────

    def _cli_menu(self):
        while True:
            subtitle = self._build_subtitle()
            choices = [
                ("verb", "Common Command      Pick from infos/contacts/advert/..."),
                ("dm", "Send DM             msg <name> <text>"),
                ("chan", "Send to Channel     chan <slot> <text>"),
                ("admin", "Remote Admin Cmd    cmd <name> <command>"),
                ("raw", "Raw Command         Free-form (advanced)"),
                ("shell", "Interactive Shell   Drop into meshcore-cli REPL"),
                ("install", "Install / Locate    Find or install meshcore-cli"),
                ("back", "Back"),
            ]
            choice = self.ctx.dialog.menu(
                "MeshCore CLI",
                subtitle,
                choices,
            )
            if choice is None or choice == "back":
                return

            dispatch = {
                "verb": ("Common Command", self._run_common_verb),
                "dm": ("Send DM", self._send_dm),
                "chan": ("Send to Channel", self._send_channel),
                "admin": ("Remote Admin", self._run_admin_command),
                "raw": ("Raw Command", self._run_raw_command),
                "shell": ("Interactive Shell", self._drop_to_shell),
                "install": ("Install / Locate", self._install_or_locate),
            }
            entry = dispatch.get(choice)
            if entry:
                self.ctx.safe_call(*entry)

    def _build_subtitle(self) -> str:
        cli_path = self._cli_path()
        cli_label = cli_path or "(meshcore-cli not installed)"
        mc = self._meshcore_config()
        if mc is None:
            radio = "(no MeshCore config)"
        elif mc.connection_type == "serial":
            radio = f"serial → {mc.device_path} @ {mc.baud_rate}"
        elif mc.connection_type == "tcp":
            radio = f"tcp → {mc.tcp_host or '(no host)'}:{mc.tcp_port}"
        elif mc.connection_type == "ble":
            radio = f"ble → {mc.device_path or '(no name/addr)'}"
        else:
            radio = f"{mc.connection_type} (unknown)"
        return (
            f"CLI: {cli_label}\n"
            f"Radio: {radio}\n"
            f"Daemon: {self.DAEMON_SERVICE} ({self._daemon_state()})"
        )

    # ─────────────────────────────────────────────────────────────────
    # Verb runners
    # ─────────────────────────────────────────────────────────────────

    def _run_common_verb(self):
        choice = self.ctx.dialog.menu(
            "Common MeshCore Commands",
            "Pick a one-shot command to run via meshcore-cli.",
            self.COMMON_VERBS + [("back", "Back")],
        )
        if choice is None or choice == "back":
            return
        # 'clock_sync' maps to the two-token "clock sync" command that
        # upstream meshcore-cli expects.
        cmd = "clock sync" if choice == "clock_sync" else choice
        self._run_cli_command(cmd, title=f"meshcore-cli {cmd}")

    def _send_dm(self):
        name = self.ctx.dialog.inputbox(
            "Send DM",
            "Contact name or pubkey-hex prefix:",
            "",
        )
        if not name:
            return
        name = name.strip()
        text = self.ctx.dialog.inputbox(
            "Send DM",
            f"Message text (DM to {name}):",
            "",
        )
        if not text:
            return
        text = text.strip()
        if not self._validate_text(text):
            return
        # meshcore-cli's `msg` takes "<name> <text>" — name is one token,
        # the rest is the message body. Quote name if it contains spaces.
        cmd = f"msg {shlex.quote(name)} {text}"
        self._run_cli_command(cmd, title=f"DM → {name}")

    def _send_channel(self):
        slot_str = self.ctx.dialog.inputbox(
            "Send to Channel",
            "Channel slot (0=Public, 1+=named slots):",
            "1",
        )
        if not slot_str:
            return
        try:
            slot = int(slot_str.strip())
        except ValueError:
            self.ctx.dialog.msgbox("Bad Input", "Channel slot must be an integer.")
            return
        if slot < 0 or slot > 32:
            self.ctx.dialog.msgbox("Bad Input", "Channel slot out of range (0..32).")
            return
        text = self.ctx.dialog.inputbox(
            "Send to Channel",
            f"Message text (broadcast on slot {slot}):",
            "",
        )
        if not text:
            return
        text = text.strip()
        if not self._validate_text(text):
            return
        cmd = f"chan {slot} {text}"
        self._run_cli_command(cmd, title=f"Channel {slot}")

    def _run_admin_command(self):
        name = self.ctx.dialog.inputbox(
            "Remote Admin Command",
            "Repeater/room-server contact name to address:",
            "",
        )
        if not name:
            return
        name = name.strip()
        admin = self.ctx.dialog.inputbox(
            "Remote Admin Command",
            "Admin command to send (see docs.meshcore.io/cli_commands/).\n"
            "Examples: 'reboot', 'neighbors', 'stats-core', 'set radio …'",
            "",
        )
        if not admin:
            return
        admin = admin.strip()
        if not self._validate_text(admin):
            return
        # meshcore-cli's `cmd` takes "<name> <quoted command line>".
        cmd = f"cmd {shlex.quote(name)} {shlex.quote(admin)}"
        self._run_cli_command(cmd, title=f"admin → {name}")

    def _run_raw_command(self):
        warn = self.ctx.dialog.yesno(
            "Raw Command — Heads Up",
            "Free-form meshcore-cli input is forwarded as-is. Wrong frequency "
            "or radio writes can violate licence terms or brick the radio for "
            "a region. Continue?",
            default_no=True,
        )
        if not warn:
            return
        cmd = self.ctx.dialog.inputbox(
            "Raw meshcore-cli Command",
            "Single command (quoting follows shlex rules). "
            "Example: msg KH6XYZ hello",
            "",
        )
        if cmd is None:
            return
        cmd = cmd.strip()
        if not cmd:
            return
        if not self._validate_text(cmd):
            return
        self._run_cli_command(cmd, title="raw")

    # ─────────────────────────────────────────────────────────────────
    # Drop-to-shell flow (no -c, inherits terminal)
    # ─────────────────────────────────────────────────────────────────

    def _drop_to_shell(self):
        cli = self._cli_path()
        if not cli:
            self._missing_cli()
            return
        conn_args = self._connection_args()
        if conn_args is None:
            return  # Validation failed; user already saw the dialog.

        paused = self._pause_daemon_for_cli("interactive shell")
        if paused is None:
            return  # User declined or stop failed.
        try:
            clear_screen()
            print("=== meshcore-cli (interactive) ===")
            print(f"  {' '.join([cli] + conn_args)}\n")
            print("  Type 'exit' or Ctrl-D to leave the shell.\n")
            try:
                # Inherit the terminal so the operator gets the full REPL.
                # timeout=None — interactive sessions can run as long as
                # needed (mirrors _daemon_journal_tail's `journalctl -f`).
                subprocess.run([cli] + conn_args, check=False, timeout=None)
            except FileNotFoundError:
                print("  meshcore-cli vanished mid-launch.")
            except KeyboardInterrupt:
                print("\n  (interrupted)")
            self.ctx.wait_for_enter()
        finally:
            if paused:
                self._resume_daemon()

    # ─────────────────────────────────────────────────────────────────
    # Install / locate flow
    # ─────────────────────────────────────────────────────────────────

    def _install_or_locate(self):
        clear_screen()
        print("=== meshcore-cli — Install / Locate ===\n")
        cli = self._cli_path()
        if cli:
            print(f"  Found: {cli}")
            print("  No install needed.")
            self.ctx.wait_for_enter()
            return
        print("  meshcore-cli is not on PATH and not in any known location.\n")
        print("  Recommended install (matches install_noc.sh):")
        print("    /opt/meshanchor/venv/bin/pip install meshcore-cli")
        print("\n  Or via pipx:")
        print("    pipx install meshcore-cli\n")
        do_install = self.ctx.dialog.yesno(
            "Install meshcore-cli?",
            "Run 'pip install meshcore-cli' inside the MeshAnchor venv?\n\n"
            "(Falls back to pipx if the venv is missing.)",
            default_no=False,
        )
        if do_install:
            self._attempt_install()
        else:
            self.ctx.wait_for_enter()

    def _attempt_install(self):
        venv_pip = "/opt/meshanchor/venv/bin/pip"
        import os
        cmd: Optional[List[str]] = None
        if os.path.isfile(venv_pip) and os.access(venv_pip, os.X_OK):
            cmd = [venv_pip, "install", "meshcore-cli"]
        else:
            import shutil
            pipx = shutil.which("pipx")
            if pipx:
                cmd = [pipx, "install", "meshcore-cli"]
        if not cmd:
            print("\n  No suitable installer (venv pip / pipx) found.")
            print("  Install Python tooling first, then retry.")
            self.ctx.wait_for_enter()
            return
        print(f"\n  Running: {' '.join(cmd)}\n")
        try:
            subprocess.run(cmd, timeout=300, check=False)
        except subprocess.TimeoutExpired:
            print("\n  Install timed out (5 min). Try manually.")
        except FileNotFoundError as e:
            print(f"\n  Installer missing: {e}")
        except KeyboardInterrupt:
            print("\n  (cancelled)")
        self.ctx.wait_for_enter()

    # ─────────────────────────────────────────────────────────────────
    # Core run path — daemon handoff + subprocess invocation
    # ─────────────────────────────────────────────────────────────────

    def _run_cli_command(self, command: str, title: str) -> None:
        """Run ``meshcore-cli <conn-args> <command>`` with daemon handoff.

        ``command`` is a single command string in upstream meshcore-cli's
        REPL syntax (e.g. ``"msg alice hi"``). It is split with ``shlex``
        so quoting is honored; we never pass ``shell=True``.
        """
        cli = self._cli_path()
        if not cli:
            self._missing_cli()
            return
        conn_args = self._connection_args()
        if conn_args is None:
            return

        try:
            args = shlex.split(command)
        except ValueError as e:
            self.ctx.dialog.msgbox("Bad Quoting", f"Could not parse command: {e}")
            return
        if not args:
            return

        full_cmd = [cli] + conn_args + args

        paused = self._pause_daemon_for_cli(title)
        if paused is None:
            return  # User declined or stop failed.
        try:
            clear_screen()
            print(f"=== {title} ===")
            print(f"  {' '.join(full_cmd)}\n")
            try:
                result = subprocess.run(
                    full_cmd,
                    timeout=self.CLI_TIMEOUT,
                    capture_output=True,
                    text=True,
                )
                stdout = (result.stdout or "").rstrip()
                stderr = (result.stderr or "").rstrip()
                if stdout:
                    print(stdout)
                if stderr:
                    print(f"\n[stderr]\n{stderr}")
                print(f"\n  exit code: {result.returncode}")
            except subprocess.TimeoutExpired:
                print(f"\n  Command timed out after {self.CLI_TIMEOUT}s.")
                print("  Radio busy or device unreachable.")
            except FileNotFoundError:
                print("  meshcore-cli vanished mid-launch.")
            except KeyboardInterrupt:
                print("\n  (interrupted)")
            self.ctx.wait_for_enter()
        finally:
            if paused:
                self._resume_daemon()

    # ─────────────────────────────────────────────────────────────────
    # Daemon handoff helpers — pause/resume around a CLI invocation.
    # Tristate return from _pause_daemon_for_cli:
    #   True  → daemon was active, we stopped it; caller MUST resume on exit
    #   False → daemon was already inactive; caller does nothing on exit
    #   None  → user declined or stop failed; caller MUST abort
    # ─────────────────────────────────────────────────────────────────

    def _pause_daemon_for_cli(self, reason: str) -> Optional[bool]:
        if not self._is_daemon_active():
            return False
        confirmed = self.ctx.dialog.yesno(
            "Stop Daemon for CLI?",
            f"{self.DAEMON_SERVICE} currently owns the MeshCore radio. "
            f"Running meshcore-cli ({reason}) requires exclusive access.\n\n"
            "Stop the daemon now and restart it when the CLI exits?",
            default_no=False,
        )
        if not confirmed:
            return None
        ok, msg = self._stop_daemon()
        if not ok:
            self.ctx.dialog.msgbox(
                "Daemon Stop Failed",
                f"Could not stop {self.DAEMON_SERVICE}:\n\n{msg}\n\n"
                "Aborting CLI run — the radio is still owned by the daemon.",
            )
            return None
        return True

    def _resume_daemon(self) -> None:
        ok, msg = self._start_daemon()
        if not ok:
            # Don't swallow — the CLI run already finished, but the
            # operator needs to know the daemon didn't come back.
            print(f"\n  WARN: failed to restart daemon: {msg}")
            print(f"  Restart manually: sudo systemctl start {self.DAEMON_SERVICE}")

    # ─────────────────────────────────────────────────────────────────
    # Service / config helpers
    # ─────────────────────────────────────────────────────────────────

    def _is_daemon_active(self) -> bool:
        try:
            from utils.service_check import check_service, ServiceState
        except ImportError:
            return False
        try:
            status = check_service(self.DAEMON_SERVICE)
            return status.state == ServiceState.RUNNING
        except Exception as e:
            logger.debug("check_service failed: %s", e)
            return False

    def _daemon_state(self) -> str:
        try:
            from utils.service_check import check_service
            status = check_service(self.DAEMON_SERVICE)
            return status.state.value if hasattr(status.state, "value") else str(status.state)
        except Exception as e:
            return f"unknown ({e})"

    def _stop_daemon(self):
        from utils.service_check import stop_service
        return stop_service(self.DAEMON_SERVICE, timeout=20)

    def _start_daemon(self):
        from utils.service_check import start_service
        return start_service(self.DAEMON_SERVICE, timeout=30)

    def _cli_path(self) -> Optional[str]:
        try:
            from utils.cli import find_meshcore_cli
        except ImportError:
            return None
        return find_meshcore_cli()

    def _meshcore_config(self):
        if not _HAS_GW_CONFIG:
            return None
        try:
            cfg = _GatewayConfig.load()
        except Exception as e:
            logger.debug("GatewayConfig.load failed: %s", e)
            return None
        return getattr(cfg, "meshcore", None)

    def _connection_args(self) -> Optional[List[str]]:
        """Build meshcore-cli connection flags from MeshCoreConfig.

        Returns None and shows an error dialog when config is unusable.
        """
        mc = self._meshcore_config()
        if mc is None:
            self.ctx.dialog.msgbox(
                "MeshCore Config Missing",
                "Could not read MeshCore connection settings from "
                "GatewayConfig. Configure MeshCore first "
                "(MeshCore → Configure).",
            )
            return None
        ctype = mc.connection_type
        if ctype == "serial":
            if not mc.device_path:
                self.ctx.dialog.msgbox(
                    "Bad Config",
                    "MeshCore is set to serial but device_path is empty.",
                )
                return None
            args = ["-s", mc.device_path]
            if mc.baud_rate:
                args += ["-b", str(int(mc.baud_rate))]
            return args
        if ctype == "tcp":
            if not mc.tcp_host:
                self.ctx.dialog.msgbox(
                    "Bad Config",
                    "MeshCore is set to tcp but tcp_host is empty.",
                )
                return None
            args = ["-t", mc.tcp_host]
            if mc.tcp_port:
                args += ["-p", str(int(mc.tcp_port))]
            return args
        if ctype == "ble":
            if not mc.device_path:
                self.ctx.dialog.msgbox(
                    "Bad Config",
                    "MeshCore is set to BLE but device_path "
                    "(BLE name/MAC) is empty.",
                )
                return None
            return ["-a", mc.device_path]
        self.ctx.dialog.msgbox(
            "Bad Config",
            f"Unknown MeshCore connection_type: {ctype!r}",
        )
        return None

    def _missing_cli(self):
        self.ctx.dialog.msgbox(
            "meshcore-cli Not Found",
            "meshcore-cli is not installed or not on PATH.\n\n"
            "Use 'Install / Locate' from this menu, or install manually:\n"
            "  /opt/meshanchor/venv/bin/pip install meshcore-cli",
        )

    @staticmethod
    def _validate_text(text: str) -> bool:
        # Reject control characters (newlines/tabs/etc) — they confuse
        # the meshcore-cli REPL parser. Printable Unicode is fine.
        if any(ord(c) < 0x20 and c != " " for c in text):
            return False
        return True
