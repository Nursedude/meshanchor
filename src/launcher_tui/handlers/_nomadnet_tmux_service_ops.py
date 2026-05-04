"""NomadNet tmux/systemd-user service control + state SSOT.

Ported from MeshForge's ``_nomadnet_service_ops.py`` (Phase 8.4) for
MeshAnchor with the rns_alignment integration stripped — MeshAnchor
doesn't ship that audit/normalize script. The rpc_key precondition
lives in the wrapper's exit-87 path (see
``templates/python/nomadnet_wrapper.py``); operators repair drift
manually following the hint printed there.

Pattern: NomadNet runs inside a detached tmux session
(``meshcore-chat`` siblings) managed by a systemd-user unit at
``~/.config/systemd/user/nomadnet.service``. Operators attach via
``tmux attach -t nomadnet`` (or the TUI's Attach action), detach with
``Ctrl-b d``, and the service keeps running.

Privilege model: when the TUI runs under sudo, ``systemctl --user``
needs the real user's manager — we resolve via
``sudo -u <user> -H env XDG_RUNTIME_DIR=... DBUS_SESSION_BUS_ADDRESS=...
systemctl --user`` (same incantation as ChatPaneServiceOpsMixin and
MeshChatXServiceOpsMixin).

This mixin is additive: the existing direct-launch flow in
``nomadnet.py`` (``_launch_nomadnet_textui`` / ``_launch_nomadnet_daemon``)
stays available for one-shot ops. The Service Control submenu below
is the new persistent-session option.
"""

import logging
import os
import pwd
import shutil
import subprocess
from pathlib import Path
from typing import List, Optional, Tuple

from utils.paths import get_real_user_home, get_real_username

logger = logging.getLogger(__name__)

_TEMPLATES_ROOT = Path(__file__).resolve().parents[3] / "templates"
_UNIT_TEMPLATE = _TEMPLATES_ROOT / "systemd" / "nomadnet-tmux-user.service"
_WRAPPER_TEMPLATE = _TEMPLATES_ROOT / "python" / "nomadnet_wrapper.py"

_UNIT_FILENAME = "nomadnet.service"
_WRAPPER_FILENAME = "nomadnet_wrapper.py"
_TMUX_SESSION = "nomadnet"
_UNIT_PLACEHOLDER = "__NOMADNET_EXEC__"


def _config_dir() -> Path:
    return get_real_user_home() / ".config" / "meshanchor"


def _wrapper_dest() -> Path:
    return _config_dir() / _WRAPPER_FILENAME


def _unit_dest() -> Path:
    return get_real_user_home() / ".config" / "systemd" / "user" / _UNIT_FILENAME


class NomadNetTmuxServiceOpsMixin:
    """Service-state + control for the tmux-wrapped NomadNet user unit."""

    # ------------------------------------------------------------------
    # Low-level: user-scope systemctl invocation
    # ------------------------------------------------------------------

    def _user_systemctl_argv(self, verbs: List[str]) -> List[str]:
        sudo_user = os.environ.get("SUDO_USER")
        if not sudo_user or sudo_user == "root":
            return ["systemctl", "--user"] + verbs
        try:
            uid = pwd.getpwnam(sudo_user).pw_uid
        except KeyError:
            logger.warning(
                "SUDO_USER=%s not found in passwd; falling back to "
                "plain systemctl --user",
                sudo_user,
            )
            return ["systemctl", "--user"] + verbs
        runtime_dir = f"/run/user/{uid}"
        dbus = f"unix:path={runtime_dir}/bus"
        return [
            "sudo", "-u", sudo_user, "-H", "env",
            f"XDG_RUNTIME_DIR={runtime_dir}",
            f"DBUS_SESSION_BUS_ADDRESS={dbus}",
            "systemctl", "--user",
        ] + verbs

    _MANAGER_VERBS = frozenset({"daemon-reload", "daemon-reexec"})

    def _systemctl_user_nn(self, verb: str, timeout: int = 15) -> Tuple[bool, str]:
        verbs = [verb] if verb in self._MANAGER_VERBS else [verb, "nomadnet"]
        argv = self._user_systemctl_argv(verbs)
        try:
            proc = subprocess.run(
                argv, capture_output=True, text=True, timeout=timeout,
            )
            out = (proc.stdout or "") + (proc.stderr or "")
            return proc.returncode == 0, out.strip()
        except subprocess.TimeoutExpired:
            return False, f"systemctl --user {verb} timed out"
        except FileNotFoundError:
            return False, "systemctl not found — not a systemd system?"
        except (subprocess.SubprocessError, OSError) as e:
            return False, f"systemctl --user {verb} failed: {e}"

    def _user_systemctl_text_nn(
        self, verbs: List[str], timeout: int = 10,
    ) -> Tuple[int, str]:
        argv = self._user_systemctl_argv(verbs)
        try:
            proc = subprocess.run(
                argv, capture_output=True, text=True, timeout=timeout,
            )
            return proc.returncode, (proc.stdout or "").strip()
        except (subprocess.TimeoutExpired, subprocess.SubprocessError,
                FileNotFoundError, OSError) as e:
            logger.debug("systemctl --user %s failed: %s", verbs, e)
            return -1, ""

    # ------------------------------------------------------------------
    # tmux helpers
    # ------------------------------------------------------------------

    def _tmux_has_nomadnet_session(self) -> bool:
        tmux_bin = shutil.which("tmux")
        if not tmux_bin:
            return False
        sudo_user = os.environ.get("SUDO_USER")
        if sudo_user and sudo_user != "root":
            try:
                uid = pwd.getpwnam(sudo_user).pw_uid
            except KeyError:
                return False
            argv = [
                "sudo", "-u", sudo_user, "-H", "env",
                f"XDG_RUNTIME_DIR=/run/user/{uid}",
                tmux_bin, "has-session", "-t", _TMUX_SESSION,
            ]
        else:
            argv = [tmux_bin, "has-session", "-t", _TMUX_SESSION]
        try:
            proc = subprocess.run(
                argv, capture_output=True, text=True, timeout=5,
            )
            return proc.returncode == 0
        except (subprocess.SubprocessError, OSError):
            return False

    # ------------------------------------------------------------------
    # State SSOT
    # ------------------------------------------------------------------

    def _nomadnet_tmux_state(self) -> dict:
        """Return the authoritative state of the tmux-wrapped nomadnet unit."""
        state = {
            "unit_installed": _unit_dest().exists(),
            "wrapper_installed": _wrapper_dest().exists(),
            "tmux_present": shutil.which("tmux") is not None,
            "tmux_session": False,
            "active": False,
            "enabled": False,
            "sub_state": "",
            "main_pid": 0,
            "n_restarts": 0,
            "error": None,
        }

        rc, out = self._user_systemctl_text_nn(["is-active", "nomadnet"])
        if rc == 0 and out == "active":
            state["active"] = True
        elif rc == -1:
            state["error"] = "systemctl --user unreachable"
            return state

        rc, out = self._user_systemctl_text_nn(["is-enabled", "nomadnet"])
        if rc == 0 and out in (
            "enabled", "enabled-runtime", "static", "alias",
        ):
            state["enabled"] = True

        rc, out = self._user_systemctl_text_nn([
            "show", "nomadnet",
            "-p", "SubState", "-p", "MainPID", "-p", "NRestarts",
        ])
        if rc == 0:
            for line in out.splitlines():
                if "=" not in line:
                    continue
                k, v = line.split("=", 1)
                v = v.strip()
                if k == "SubState":
                    state["sub_state"] = v
                elif k == "MainPID":
                    try:
                        state["main_pid"] = int(v)
                    except ValueError:
                        pass
                elif k == "NRestarts":
                    try:
                        state["n_restarts"] = int(v)
                    except ValueError:
                        pass

        if state["tmux_present"]:
            state["tmux_session"] = self._tmux_has_nomadnet_session()

        return state

    def _nomadnet_tmux_service_state_line(
        self, state: Optional[dict] = None,
    ) -> str:
        """One-line summary for menu subtitles."""
        s = state if state is not None else self._nomadnet_tmux_state()
        if not s["tmux_present"]:
            return "tmux: NOT INSTALLED  (sudo apt-get install -y tmux)"
        if not s["unit_installed"]:
            return "Service: not installed (run Install user unit)"
        if s["error"]:
            return f"Service: unknown ({s['error']})"
        if s["active"]:
            parts = [f"active ({s['sub_state'] or 'running'})"]
            if s["main_pid"]:
                parts.append(f"PID {s['main_pid']}")
            parts.append("tmux up" if s["tmux_session"] else "tmux MISSING")
            if s["n_restarts"] > 0:
                parts.append(f"{s['n_restarts']} restarts")
            return "Service: " + ", ".join(parts)
        if s["sub_state"] == "failed":
            return "Service: failed (see journal)"
        word = "inactive"
        if s["enabled"]:
            word += ", enabled"
        return f"Service: {word}"

    def _print_nomadnet_tmux_state_block(self) -> None:
        """Print the --- Service State --- block of the status screen."""
        svc = self._nomadnet_tmux_state()
        if not svc["tmux_present"]:
            print("  tmux:      NOT INSTALLED")
            print("  Install:   sudo apt-get install -y tmux")
            return
        if not svc["unit_installed"]:
            print(f"  Unit:      NOT INSTALLED ({_unit_dest()})")
            print("  Install:   Service Control > Install user unit")
            return
        active_word = "ACTIVE" if svc["active"] else "inactive"
        enabled_word = "enabled" if svc["enabled"] else "disabled"
        line = f"  Unit:      {active_word} / {enabled_word}"
        if svc["sub_state"]:
            line += f" ({svc['sub_state']})"
        print(line)
        if svc["main_pid"]:
            print(f"  MainPID:   {svc['main_pid']}")
        if svc["active"]:
            if svc["tmux_session"]:
                print(f"  tmux:      session '{_TMUX_SESSION}' up")
                print(f"  Attach:    tmux attach -t {_TMUX_SESSION}")
            else:
                print(
                    "  tmux:      \033[0;33msession missing — "
                    "service active\033[0m"
                )
                print("             (the wrapper may have just exited; "
                      "check journal)")
        if svc["n_restarts"] and svc["n_restarts"] > 0:
            print(f"  Restarts:  \033[0;33m{svc['n_restarts']}\033[0m")
        if svc["error"]:
            print(f"  Note:      {svc['error']}")

    # ------------------------------------------------------------------
    # Install / refresh
    # ------------------------------------------------------------------

    def _resolve_nomadnet_wrapper_argv(self) -> List[str]:
        """Compute the wrapper invocation for THIS box's install layout.

        Returns a list of argv tokens that becomes the inner ExecStart
        of the tmux unit. We prefer the python interpreter from a pipx
        venv (it has nomadnet importable in the same env), falling
        back to the system python3 with PYTHONPATH unset (assumes
        nomadnet is on the system python's import path via pip --user
        or apt).
        """
        wrapper_dest = _wrapper_dest()
        # Search order: pipx venv next to nomadnet binary, then fall
        # back to system python3.
        nn_local = get_real_user_home() / ".local" / "bin" / "nomadnet"
        nn_path = (
            nn_local if nn_local.exists()
            else (Path(shutil.which("nomadnet")) if shutil.which("nomadnet") else None)
        )
        if nn_path and nn_path.is_symlink():
            real = nn_path.resolve()
            # pipx venv layout: <venv>/bin/python adjacent to <venv>/bin/nomadnet
            venv_python = real.parent / "python"
            if venv_python.exists():
                return [str(venv_python), str(wrapper_dest), "--rnsconfig", "/etc/reticulum"]
        # Fall back to system python3
        py = shutil.which("python3") or "/usr/bin/python3"
        return [py, str(wrapper_dest), "--rnsconfig", "/etc/reticulum"]

    def _install_nomadnet_tmux_unit(self) -> Tuple[bool, str]:
        """Render the wrapper + unit for THIS box and activate them."""
        if not _UNIT_TEMPLATE.is_file():
            return False, f"Unit template missing: {_UNIT_TEMPLATE}"
        if not _WRAPPER_TEMPLATE.is_file():
            return False, f"Wrapper template missing: {_WRAPPER_TEMPLATE}"

        if not shutil.which("tmux"):
            return False, "tmux not installed (sudo apt-get install -y tmux)"

        wrapper_dest = _wrapper_dest()
        unit_dest = _unit_dest()
        username = get_real_username() or os.environ.get("USER") or ""

        # 1. Wrapper (always overwrite — template is the SSOT)
        wrapper_dest.parent.mkdir(parents=True, exist_ok=True)
        wrapper_dest.write_text(_WRAPPER_TEMPLATE.read_text())
        os.chmod(wrapper_dest, 0o644)
        self._chown_to_real_user_nn(wrapper_dest)
        self._chown_to_real_user_nn(wrapper_dest.parent)

        # 2. Resolve per-box ExecStart and substitute into unit template
        unit_dest.parent.mkdir(parents=True, exist_ok=True)
        unit_text = _UNIT_TEMPLATE.read_text()
        if _UNIT_PLACEHOLDER not in unit_text:
            return False, (
                f"Unit template lacks {_UNIT_PLACEHOLDER} placeholder — "
                f"refusing to install."
            )
        # Quote the inner argv with shlex.join so tmux's outer single-
        # quoting wraps the whole thing as a single shell command.
        import shlex
        argv = self._resolve_nomadnet_wrapper_argv()
        exec_inner = shlex.join(argv)
        rendered = unit_text.replace(_UNIT_PLACEHOLDER, exec_inner)
        unit_dest.write_text(rendered)
        self._chown_to_real_user_nn(unit_dest)
        self._chown_to_real_user_nn(unit_dest.parent)

        # 3. Activate
        ok_reload, _ = self._systemctl_user_nn("daemon-reload")
        if not ok_reload:
            return False, "systemctl --user daemon-reload failed"
        ok_enable, _ = self._systemctl_user_nn("enable")
        ok_start, out_start = self._systemctl_user_nn("start")
        if username and username != "root":
            self._enable_linger_quiet_nn(username)

        if not ok_start:
            return False, f"start failed: {out_start}"
        return True, (
            f"Installed:\n  {unit_dest}\n  {wrapper_dest}\n\n"
            f"ExecStart: {exec_inner}\n\n"
            f"Service state: started"
            + (", enabled" if ok_enable else "")
            + "\n\nAttach the live session:\n  tmux attach -t nomadnet\n"
            "  (or use Service Control > Attach tmux session)"
        )

    @staticmethod
    def _chown_to_real_user_nn(path: Path) -> None:
        """If running under sudo, chown ``path`` back to the real user."""
        sudo_user = os.environ.get("SUDO_USER")
        if not sudo_user or sudo_user == "root":
            return
        try:
            pw = pwd.getpwnam(sudo_user)
            os.chown(str(path), pw.pw_uid, pw.pw_gid)
        except (KeyError, OSError) as e:
            logger.debug("chown %s to %s failed: %s", path, sudo_user, e)

    def _enable_linger_quiet_nn(self, username: str) -> None:
        argv = (
            ["loginctl", "enable-linger", username]
            if os.geteuid() == 0
            else ["sudo", "loginctl", "enable-linger", username]
        )
        try:
            subprocess.run(
                argv, capture_output=True, text=True, timeout=10,
            )
        except (subprocess.SubprocessError, OSError):
            pass

    # ------------------------------------------------------------------
    # Attach
    # ------------------------------------------------------------------

    def _attach_nomadnet_tmux(self) -> None:
        """Drop the operator into ``tmux attach -t nomadnet``."""
        from backend import clear_screen

        if not shutil.which("tmux"):
            self.ctx.dialog.msgbox(
                "tmux not installed",
                "tmux is required for the persistent NomadNet pane.\n\n"
                "Install with:\n  sudo apt-get install -y tmux",
            )
            return
        if not self._tmux_has_nomadnet_session():
            self.ctx.dialog.msgbox(
                "No tmux session",
                f"No tmux session named '{_TMUX_SESSION}' is running.\n\n"
                "Start the service first (Service Control > Start), then\n"
                "wait a second for the session to come up.",
            )
            return

        clear_screen()
        print(f"=== Attaching to {_TMUX_SESSION} tmux session ===")
        print("Detach with Ctrl-b d to return to MeshAnchor.\n")

        sudo_user = os.environ.get("SUDO_USER")
        tmux_bin = shutil.which("tmux") or "tmux"
        if sudo_user and sudo_user != "root":
            try:
                uid = pwd.getpwnam(sudo_user).pw_uid
            except KeyError:
                self.ctx.dialog.msgbox(
                    "User Lookup Failed",
                    f"Cannot resolve SUDO_USER={sudo_user} in passwd.",
                )
                return
            argv = [
                "sudo", "-u", sudo_user, "-H",
                "env", f"XDG_RUNTIME_DIR=/run/user/{uid}",
                tmux_bin, "attach", "-t", _TMUX_SESSION,
            ]
        else:
            argv = [tmux_bin, "attach", "-t", _TMUX_SESSION]

        try:
            subprocess.run(argv, timeout=None)
        except (subprocess.SubprocessError, OSError) as e:
            print(f"\ntmux attach failed: {e}")
            self.ctx.wait_for_enter()
            return
        except KeyboardInterrupt:
            pass
        clear_screen()

    # ------------------------------------------------------------------
    # Service Control submenu (operator-facing)
    # ------------------------------------------------------------------

    def _nomadnet_tmux_service_menu(self) -> None:
        """Operator-facing submenu for the tmux-wrapped NomadNet service."""
        while True:
            state = self._nomadnet_tmux_state()
            subtitle = self._nomadnet_tmux_service_state_line(state)

            choices: List[Tuple[str, str]] = []
            if state["tmux_session"]:
                choices.append(("attach", "Attach tmux session"))
            if not state["unit_installed"]:
                choices.append(("install", "Install user unit       wrapper + systemd unit"))
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
                choices.append(("linger", "Enable linger           survive logout"))
                choices.append(("reinstall", "Reinstall user unit (refresh templates)"))
                choices.append(("uninstall", "Uninstall user unit"))
            choices.append(("logs", "View Logs               journalctl --user -u nomadnet"))
            choices.append(("back", "Back"))

            choice = self.ctx.dialog.menu(
                "NomadNet Service Control (tmux)",
                subtitle,
                choices,
            )
            if choice is None or choice == "back":
                return

            dispatch = {
                "attach": ("Attach tmux", self._attach_nomadnet_tmux),
                "install": ("Install user unit", self._do_nomadnet_install),
                "reinstall": ("Reinstall user unit", self._do_nomadnet_install),
                "start": (
                    "Start service",
                    lambda: self._run_nomadnet_systemctl("start"),
                ),
                "stop": (
                    "Stop service",
                    lambda: self._run_nomadnet_systemctl("stop"),
                ),
                "restart": (
                    "Restart service",
                    lambda: self._run_nomadnet_systemctl("restart"),
                ),
                "enable": (
                    "Enable service",
                    lambda: self._run_nomadnet_systemctl("enable"),
                ),
                "disable": (
                    "Disable service",
                    lambda: self._run_nomadnet_systemctl("disable"),
                ),
                "linger": ("Enable linger", self._enable_linger_action_nn),
                "uninstall": ("Uninstall user unit", self._do_nomadnet_uninstall),
                "logs": ("View logs", self._view_nomadnet_tmux_logs),
            }
            entry = dispatch.get(choice)
            if entry:
                self.ctx.safe_call(*entry)

    def _do_nomadnet_install(self) -> None:
        if not shutil.which("tmux"):
            if self.ctx.dialog.yesno(
                "Install tmux?",
                "tmux is required for the persistent NomadNet pane and "
                "is not installed.\n\n"
                "Run: sudo apt-get install -y tmux\n\nProceed?",
            ):
                ok, out = self._apt_install_nn("tmux")
                if not ok:
                    self.ctx.dialog.msgbox(
                        "tmux install failed",
                        out or "(no output)",
                    )
                    return
            else:
                return

        ok, out = self._install_nomadnet_tmux_unit()
        title = "NomadNet install: OK" if ok else "NomadNet install: FAILED"
        self.ctx.dialog.msgbox(title, out)

    def _do_nomadnet_uninstall(self) -> None:
        if not self.ctx.dialog.yesno(
            "Uninstall NomadNet user unit?",
            "This will:\n\n"
            "  - Stop and disable the nomadnet user service\n"
            "  - Remove the systemd unit file + wrapper\n\n"
            "It does NOT touch:\n"
            "  - The nomadnet binary (still installed)\n"
            "  - Your NomadNet identity at ~/.nomadnetwork/\n\n"
            "Proceed?",
        ):
            return
        self._systemctl_user_nn("stop")
        self._systemctl_user_nn("disable")
        for path in (_unit_dest(), _wrapper_dest()):
            try:
                if path.exists():
                    path.unlink()
            except OSError as e:
                logger.warning("Failed to remove %s: %s", path, e)
        self._systemctl_user_nn("daemon-reload")
        self.ctx.dialog.msgbox(
            "NomadNet user unit uninstalled",
            "Service disabled, unit + wrapper removed.\n\n"
            "Re-install via: NomadNet > Service Control > Install user unit",
        )

    def _run_nomadnet_systemctl(self, verb: str) -> None:
        ok, out = self._systemctl_user_nn(verb)
        title = f"{verb.capitalize()}: " + ("OK" if ok else "FAILED")
        body = out if out else (
            f"systemctl --user {verb} nomadnet completed."
        )
        if verb in ("start", "stop", "restart"):
            _, active = self._user_systemctl_text_nn(["is-active", "nomadnet"])
            body += f"\n\nis-active: {active or '(empty)'}"
        self.ctx.dialog.msgbox(title, body)

    def _view_nomadnet_tmux_logs(self) -> None:
        argv = self._user_systemctl_argv([])
        argv = [a for a in argv if a not in ("systemctl", "--user")]
        argv += [
            "journalctl", "--user", "-u", "nomadnet",
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
        self.ctx.dialog.msgbox("NomadNet journal (last 100)", body)

    def _enable_linger_action_nn(self) -> None:
        username = (
            os.environ.get("SUDO_USER")
            or get_real_username()
            or os.environ.get("USER")
            or ""
        )
        if not username or username == "root":
            self.ctx.dialog.msgbox(
                "Linger not applicable",
                "Linger only applies to non-root users. Run MeshAnchor\n"
                "via 'sudo' from your normal account.",
            )
            return
        argv = (
            ["loginctl", "enable-linger", username]
            if os.geteuid() == 0
            else ["sudo", "loginctl", "enable-linger", username]
        )
        try:
            proc = subprocess.run(
                argv, capture_output=True, text=True, timeout=10,
            )
            ok = proc.returncode == 0
            self.ctx.dialog.msgbox(
                "Linger: " + ("OK" if ok else "FAILED"),
                (proc.stdout + proc.stderr).strip()
                or f"loginctl enable-linger {username} completed.",
            )
        except (subprocess.SubprocessError, OSError) as e:
            self.ctx.dialog.msgbox("Linger FAILED", str(e))

    def _apt_install_nn(self, package: str, timeout: int = 180) -> Tuple[bool, str]:
        argv = (
            ["apt-get", "install", "-y", package]
            if os.geteuid() == 0
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
