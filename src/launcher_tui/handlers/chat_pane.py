"""ChatPane Handler — persistent MeshCore chat console (tmux-wrapped).

Provides a long-lived chat pane backed by the gateway daemon's HTTP
chat API on 127.0.0.1:8081. The chat client (utils/chat_client.py)
runs inside a detached tmux session managed by a systemd-user unit
(``meshcore-chat.service``); the operator attaches and detaches at
will without losing scroll-back.

The MeshCore primary submenu in main.py exposes this via the
``chat_pane`` action, registered in section ``meshcore``. The pane
complements the existing batch chat actions in ``meshcore.py`` (view
recent / send channel / send DM / watch tail) — those are still
available for one-shot ops. The tmux pane is for sustained operator
sessions.

Authority chain:
  user input → tmux session 'meshcore-chat' → utils.chat_client
    → POST :8081/chat/send → gateway.meshcore_handler.send_text()
    → MeshCore radio frame
"""

import logging
import shutil
import subprocess
from pathlib import Path
from typing import List, Tuple

from backend import clear_screen
from handler_protocol import BaseHandler

from handlers._chat_pane_service_ops import (
    ChatPaneServiceOpsMixin,
    _TMUX_SESSION,
    _unit_dest,
    _wrapper_dest,
)
from utils.paths import get_real_user_home

logger = logging.getLogger(__name__)


class ChatPaneHandler(ChatPaneServiceOpsMixin, BaseHandler):
    """TUI handler for the persistent MeshCore chat pane."""

    handler_id = "chat_pane"
    menu_section = "meshcore"

    def menu_items(self):
        # Gated by ``meshcore`` so it disappears on the MONITOR profile
        # (no MeshCore radio); shows on MESHCORE / RADIO_MAPS / GATEWAY /
        # FULL.
        return [
            (
                "chat_pane",
                "Chat (tmux pane)        Persistent MeshCore console",
                "meshcore",
            ),
        ]

    def execute(self, action):
        if action == "chat_pane":
            self._chat_pane_menu()

    # ------------------------------------------------------------------
    # Main menu
    # ------------------------------------------------------------------

    def _chat_pane_menu(self) -> None:
        while True:
            state = self._chat_pane_state()
            subtitle = self._service_state_line(state)

            choices: List[Tuple[str, str]] = [
                ("status", "Status                  install + service + tmux"),
            ]
            if state["unit_installed"] and state["tmux_session"]:
                choices.append(("attach", "Attach pane             tmux attach -t " + _TMUX_SESSION))
            choices.extend([
                ("service", "Service Control         start / stop / install"),
                ("logs", "View Logs               journalctl --user -u meshcore-chat"),
                ("back", "Back"),
            ])

            choice = self.ctx.dialog.menu(
                "MeshCore Chat (tmux pane)",
                subtitle,
                choices,
            )
            if choice is None or choice == "back":
                return

            dispatch = {
                "status": ("ChatPane status", self._show_status),
                "attach": ("Attach chat pane", self._attach_tmux_session),
                "service": ("Service control", self._service_control_menu),
                "logs": ("View logs", self._view_logs),
            }
            entry = dispatch.get(choice)
            if entry:
                self.ctx.safe_call(*entry)

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def _show_status(self) -> None:
        clear_screen()
        print("=== MeshCore Chat Pane Status ===\n")

        # tmux availability
        tmux = shutil.which("tmux")
        if tmux:
            print(f"  tmux:      {tmux}")
        else:
            print("  tmux:      NOT INSTALLED (sudo apt-get install -y tmux)")

        # Wrapper / unit on disk
        wd = _wrapper_dest()
        ud = _unit_dest()
        print(f"  wrapper:   {wd} {'(present)' if wd.exists() else '(MISSING)'}")
        print(f"  unit:      {ud} {'(present)' if ud.exists() else '(MISSING)'}")

        # Chat client module
        client_py = (
            Path(__file__).resolve().parents[2] / "utils" / "chat_client.py"
        )
        if client_py.is_file():
            print(f"  client:    {client_py}")
        else:
            print(f"  client:    \033[0;31mMISSING\033[0m ({client_py})")

        # Gateway daemon API liveness
        ok = self._chat_api_reachable()
        api_label = "reachable" if ok else "\033[0;31mUNREACHABLE\033[0m"
        print(f"  chat API:  http://127.0.0.1:8081  {api_label}")
        if not ok:
            print("             (start meshanchor-daemon.service first)")

        print("\n--- Service State ---")
        self._print_service_state_block()

        # Storage / config dir hint
        config_dir = get_real_user_home() / ".config" / "meshanchor"
        print(f"\n  config:    {config_dir}")
        print(
            "\n  Note: the chat client talks to the gateway daemon's HTTP API,\n"
            "  not the radio directly. No serial-port contention with\n"
            "  meshanchor-daemon.service."
        )

        self.ctx.wait_for_enter()

    def _chat_api_reachable(self) -> bool:
        from urllib import error as urllib_error
        from urllib import request as urllib_request

        url = "http://127.0.0.1:8081/chat/messages?since=0"
        try:
            with urllib_request.urlopen(url, timeout=2) as resp:
                return resp.status < 500
        except (urllib_error.URLError, urllib_error.HTTPError, OSError):
            return False

    # ------------------------------------------------------------------
    # Service control submenu
    # ------------------------------------------------------------------

    def _service_control_menu(self) -> None:
        while True:
            state = self._chat_pane_state()
            subtitle = self._service_state_line(state)

            choices: List[Tuple[str, str]] = []
            if not state["unit_installed"]:
                choices.append(
                    ("install", "Install user unit       wrapper + systemd unit")
                )
            else:
                if state["active"]:
                    choices.append(("restart", "Restart service"))
                    choices.append(("stop", "Stop service"))
                else:
                    choices.append(("start", "Start service"))
                if state["enabled"]:
                    choices.append(("disable", "Disable at login (keeps running)"))
                else:
                    choices.append(("enable", "Enable at login"))
                choices.append(("reinstall", "Reinstall user unit (refresh templates)"))
                choices.append(("uninstall", "Uninstall user unit"))
            choices.append(("back", "Back"))

            choice = self.ctx.dialog.menu(
                "ChatPane Service Control",
                subtitle,
                choices,
            )
            if choice is None or choice == "back":
                return

            dispatch = {
                "install": ("Install user unit", self._do_install),
                "reinstall": ("Reinstall user unit", self._do_install),
                "start": (
                    "Start service",
                    lambda: self._run_systemctl_and_report("start"),
                ),
                "stop": (
                    "Stop service",
                    lambda: self._run_systemctl_and_report("stop"),
                ),
                "restart": (
                    "Restart service",
                    lambda: self._run_systemctl_and_report("restart"),
                ),
                "enable": (
                    "Enable service",
                    lambda: self._run_systemctl_and_report("enable"),
                ),
                "disable": (
                    "Disable service",
                    lambda: self._run_systemctl_and_report("disable"),
                ),
                "uninstall": ("Uninstall user unit", self._do_uninstall),
            }
            entry = dispatch.get(choice)
            if entry:
                self.ctx.safe_call(*entry)

    def _do_install(self) -> None:
        if not shutil.which("tmux"):
            if self.ctx.dialog.yesno(
                "Install tmux?",
                "tmux is required for the chat pane and is not installed.\n\n"
                "Run: sudo apt-get install -y tmux\n\nProceed?",
            ):
                ok, out = self._apt_install("tmux")
                if not ok:
                    self.ctx.dialog.msgbox(
                        "tmux install failed",
                        out or "(no output)",
                    )
                    return
            else:
                return

        ok, out = self._install_user_unit()
        title = "ChatPane install: OK" if ok else "ChatPane install: FAILED"
        self.ctx.dialog.msgbox(title, out)

    def _do_uninstall(self) -> None:
        if not self.ctx.dialog.yesno(
            "Uninstall ChatPane user unit?",
            "This will:\n\n"
            "  - Stop and disable the meshcore-chat user service\n"
            "  - Remove the systemd unit file + wrapper\n\n"
            "It does NOT touch the gateway daemon or the chat ring buffer.\n\n"
            "Proceed?",
        ):
            return
        self._systemctl_user("stop")
        self._systemctl_user("disable")
        for path in (_unit_dest(), _wrapper_dest()):
            try:
                if path.exists():
                    path.unlink()
            except OSError as e:
                logger.warning("Failed to remove %s: %s", path, e)
        self._systemctl_user("daemon-reload")
        self.ctx.dialog.msgbox(
            "ChatPane uninstalled",
            "Service disabled, unit + wrapper removed.\n\n"
            "Re-install via: Service Control > Install user unit",
        )

    def _run_systemctl_and_report(self, verb: str) -> None:
        ok, out = self._systemctl_user(verb)
        title = f"{verb.capitalize()}: " + ("OK" if ok else "FAILED")
        body = out if out else (
            f"systemctl --user {verb} meshcore-chat completed."
        )
        if verb in ("start", "stop", "restart"):
            _, active = self._user_systemctl_text(["is-active", "meshcore-chat"])
            body += f"\n\nis-active: {active or '(empty)'}"
        self.ctx.dialog.msgbox(title, body)

    # ------------------------------------------------------------------
    # Logs
    # ------------------------------------------------------------------

    def _view_logs(self) -> None:
        argv = self._user_systemctl_argv([])
        argv = [a for a in argv if a not in ("systemctl", "--user")]
        argv += [
            "journalctl", "--user", "-u", "meshcore-chat",
            "-n", "100", "--no-pager",
        ]
        try:
            proc = subprocess.run(
                argv, capture_output=True, text=True, timeout=10,
            )
            out = (proc.stdout or "") + (proc.stderr or "")
        except (subprocess.SubprocessError, OSError) as e:
            self.ctx.dialog.msgbox(
                "Logs unavailable",
                f"Could not read journal: {e}",
            )
            return
        body = out[-3500:] if len(out) > 3500 else (out or "(no output)")
        self.ctx.dialog.msgbox("ChatPane journal (last 100)", body)

    # ------------------------------------------------------------------
    # apt-install helper (mirrors NomadNet handler pattern)
    # ------------------------------------------------------------------

    def _apt_install(self, package: str, timeout: int = 180) -> Tuple[bool, str]:
        import os as _os
        argv = (
            ["apt-get", "install", "-y", package]
            if _os.geteuid() == 0
            else ["sudo", "apt-get", "install", "-y", package]
        )
        try:
            proc = subprocess.run(
                argv, capture_output=True, text=True, timeout=timeout,
            )
            out = (proc.stdout or "") + (proc.stderr or "")
            return proc.returncode == 0, out.strip()
        except subprocess.TimeoutExpired:
            return False, f"apt-get install -y {package} timed out"
        except (subprocess.SubprocessError, OSError, FileNotFoundError) as e:
            return False, f"apt-get install failed: {e}"
