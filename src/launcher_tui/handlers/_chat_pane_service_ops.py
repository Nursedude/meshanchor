"""ChatPane systemd-user service control + state SSOT.

Manages the ``meshcore-chat`` user unit that wraps utils/chat_client.py
inside a detached tmux session. Mirrors the MeshForge NomadNet tmux
pattern (Issue #38 in MeshForge's persistent_issues): one chat client
per box, persistent across logins, attach/detach without losing
scroll-back.

Privilege model: the TUI may run under sudo (root) or as a normal user.
``systemctl --user`` only addresses the invoking user's manager, so
when SUDO_USER is set we exec systemctl through ``sudo -u <user> -H env
XDG_RUNTIME_DIR=... DBUS_SESSION_BUS_ADDRESS=... systemctl --user``.
This is the same incantation MeshForge uses for its tmux-wrapped user
units.
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
_UNIT_TEMPLATE = _TEMPLATES_ROOT / "systemd" / "meshcore-chat-user.service"
_WRAPPER_TEMPLATE = _TEMPLATES_ROOT / "python" / "chat_client_wrapper.sh"
_ENV_TEMPLATE = _TEMPLATES_ROOT / "python" / "chat_pane.env"

_UNIT_FILENAME = "meshcore-chat.service"
_WRAPPER_FILENAME = "chat_client_wrapper.sh"
_ENV_FILENAME = "chat_pane.env"
_TMUX_SESSION = "meshcore-chat"
_UNIT_PLACEHOLDER = "__CHAT_PANE_EXEC__"


def _config_dir() -> Path:
    return get_real_user_home() / ".config" / "meshanchor"


def _wrapper_dest() -> Path:
    return _config_dir() / _WRAPPER_FILENAME


def _env_dest() -> Path:
    return _config_dir() / _ENV_FILENAME


def _unit_dest() -> Path:
    return get_real_user_home() / ".config" / "systemd" / "user" / _UNIT_FILENAME


class ChatPaneServiceOpsMixin:
    """Service-state + control for the meshcore-chat user unit."""

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

    def _systemctl_user(self, verb: str, timeout: int = 15) -> Tuple[bool, str]:
        verbs = [verb] if verb in self._MANAGER_VERBS else [verb, "meshcore-chat"]
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

    def _user_systemctl_text(
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

    def _tmux_has_session(self, name: str = _TMUX_SESSION) -> bool:
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
                tmux_bin, "has-session", "-t", name,
            ]
        else:
            argv = [tmux_bin, "has-session", "-t", name]
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

    def _chat_pane_state(self) -> dict:
        """Return the authoritative state of the meshcore-chat unit."""
        state = {
            "unit_installed": _unit_dest().exists(),
            "wrapper_installed": _wrapper_dest().exists(),
            "env_installed": _env_dest().exists(),
            "tmux_present": shutil.which("tmux") is not None,
            "tmux_session": False,
            "active": False,
            "enabled": False,
            "sub_state": "",
            "main_pid": 0,
            "n_restarts": 0,
            "error": None,
        }

        rc, out = self._user_systemctl_text(["is-active", "meshcore-chat"])
        if rc == 0 and out == "active":
            state["active"] = True
        elif rc == -1:
            state["error"] = "systemctl --user unreachable"
            return state

        rc, out = self._user_systemctl_text(["is-enabled", "meshcore-chat"])
        if rc == 0 and out in ("enabled", "enabled-runtime", "static", "alias"):
            state["enabled"] = True

        rc, out = self._user_systemctl_text([
            "show", "meshcore-chat",
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
            state["tmux_session"] = self._tmux_has_session()

        return state

    def _service_state_line(self, state: Optional[dict] = None) -> str:
        """One-line summary for menu subtitles."""
        s = state if state is not None else self._chat_pane_state()
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

    def _print_service_state_block(self) -> None:
        """Print the --- Service State --- block of the status screen."""
        svc = self._chat_pane_state()
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
                print(f"  tmux:      \033[0;33msession missing — service active\033[0m")
                print("             (the wrapper may have just exited; check journal)")
        if svc["n_restarts"] and svc["n_restarts"] > 0:
            print(f"  Restarts:  \033[0;33m{svc['n_restarts']}\033[0m")
        if svc["error"]:
            print(f"  Note:      {svc['error']}")

    # ------------------------------------------------------------------
    # Install / refresh
    # ------------------------------------------------------------------

    def _install_user_unit(self, force: bool = False) -> Tuple[bool, str]:
        """Render the unit + wrapper + env for THIS box and activate them.

        Steps:
          1. Verify tmux is available (offer to apt-install via yesno).
          2. Verify utils/chat_client.py exists in the source tree.
          3. Copy the wrapper template into ~/.config/meshanchor/.
          4. Seed the env file (chat_pane.env) on first install only —
             never overwrite the operator's edits on subsequent installs.
          5. Render the unit template (substitute the wrapper path into
             __CHAT_PANE_EXEC__) and write to ~/.config/systemd/user/.
          6. daemon-reload + enable + start, with best-effort linger.
        """
        if not _UNIT_TEMPLATE.is_file():
            return False, f"Unit template missing: {_UNIT_TEMPLATE}"
        if not _WRAPPER_TEMPLATE.is_file():
            return False, f"Wrapper template missing: {_WRAPPER_TEMPLATE}"
        if not _ENV_TEMPLATE.is_file():
            return False, f"Env template missing: {_ENV_TEMPLATE}"

        if not shutil.which("tmux"):
            return False, "tmux not installed (sudo apt-get install -y tmux)"

        # Check utils.chat_client is importable from the source layout
        client_py = Path(__file__).resolve().parents[2] / "utils" / "chat_client.py"
        if not client_py.is_file():
            return False, f"chat_client.py missing: {client_py}"

        wrapper_dest = _wrapper_dest()
        env_dest = _env_dest()
        unit_dest = _unit_dest()
        username = get_real_username() or os.environ.get("USER") or ""
        env_was_seeded = False

        # 1. Wrapper (always overwrite — template is the SSOT)
        wrapper_dest.parent.mkdir(parents=True, exist_ok=True)
        wrapper_text = _WRAPPER_TEMPLATE.read_text()
        wrapper_dest.write_text(wrapper_text)
        os.chmod(wrapper_dest, 0o755)
        self._chown_to_real_user(wrapper_dest)
        self._chown_to_real_user(wrapper_dest.parent)

        # 2. Env file (seed only — preserve operator edits across refresh)
        if not env_dest.exists():
            env_dest.write_text(_ENV_TEMPLATE.read_text())
            os.chmod(env_dest, 0o644)
            self._chown_to_real_user(env_dest)
            env_was_seeded = True

        # 3. Unit
        unit_dest.parent.mkdir(parents=True, exist_ok=True)
        unit_text = _UNIT_TEMPLATE.read_text()
        if _UNIT_PLACEHOLDER not in unit_text:
            return False, (
                f"Unit template lacks {_UNIT_PLACEHOLDER} placeholder — "
                f"refusing to install."
            )
        rendered = unit_text.replace(_UNIT_PLACEHOLDER, str(wrapper_dest))
        unit_dest.write_text(rendered)
        self._chown_to_real_user(unit_dest)
        self._chown_to_real_user(unit_dest.parent)

        # 4. Activate
        ok_reload, _ = self._systemctl_user("daemon-reload")
        if not ok_reload:
            return False, "systemctl --user daemon-reload failed"
        ok_enable, _ = self._systemctl_user("enable")
        ok_start, out_start = self._systemctl_user("start")
        # Best-effort linger so the unit survives logout on headless boxes.
        if username and username != "root":
            self._enable_linger_quiet(username)

        if not ok_start:
            return False, f"start failed: {out_start}"
        env_note = (
            f"  {env_dest}  (seeded — edit via Service Control)"
            if env_was_seeded else
            f"  {env_dest}  (preserved)"
        )
        return True, (
            f"Installed:\n  {unit_dest}\n  {wrapper_dest}\n{env_note}\n\n"
            f"Service state: started"
            + (", enabled" if ok_enable else "")
        )

    @staticmethod
    def _chown_to_real_user(path: Path) -> None:
        """If running under sudo, chown ``path`` back to the real user."""
        sudo_user = os.environ.get("SUDO_USER")
        if not sudo_user or sudo_user == "root":
            return
        try:
            pw = pwd.getpwnam(sudo_user)
            os.chown(str(path), pw.pw_uid, pw.pw_gid)
        except (KeyError, OSError) as e:
            logger.debug("chown %s to %s failed: %s", path, sudo_user, e)

    def _enable_linger_quiet(self, username: str) -> None:
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

    def _attach_tmux_session(self) -> None:
        """Drop the operator into ``tmux attach -t meshcore-chat``.

        Blocks until the operator detaches (Ctrl-b d) or the session
        exits. We tear down the dialog state so tmux owns the terminal
        cleanly while attached.
        """
        from backend import clear_screen

        if not shutil.which("tmux"):
            self.ctx.dialog.msgbox(
                "tmux not installed",
                "tmux is required for the chat pane.\n\n"
                "Install with:\n  sudo apt-get install -y tmux",
            )
            return
        if not self._tmux_has_session():
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
