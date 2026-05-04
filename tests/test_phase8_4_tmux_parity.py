"""
Phase 8.4 — tmux configurability + NomadNet tmux port regression tests.

Verifies:

1. ``utils.chat_client`` honors MESHANCHOR_CHAT_CHANNEL and
   MESHANCHOR_CHAT_POLL env vars (parsed via ``_env_int`` /
   ``_env_float`` with sane fallbacks and a poll-interval floor).

2. ``ChatPaneServiceOpsMixin`` installs ``chat_pane.env`` on first
   install and preserves operator edits on subsequent installs.

3. ``ChatPaneHandler`` Service Control submenu offers Edit config /
   Reset config / Enable linger entries.

4. ``chat_client_wrapper.sh`` sources the env file before exec.

5. ``NomadNetTmuxServiceOpsMixin`` is wired into ``NomadNetHandler``'s
   MRO and exposes the Service Control submenu.

6. Templates exist with correct placeholders and no /opt/meshforge
   leakage.
"""

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
LAUNCHER_TUI = SRC / "launcher_tui"

sys.path.insert(0, str(LAUNCHER_TUI))
sys.path.insert(0, str(SRC))

sys.path.insert(0, os.path.dirname(__file__))
from handler_test_utils import make_handler_context  # noqa: E402


# ---------------------------------------------------------------------------
# 1. Chat client env-var parsing
# ---------------------------------------------------------------------------

def test_env_int_default_when_unset(monkeypatch):
    monkeypatch.delenv("MESHANCHOR_CHAT_CHANNEL", raising=False)
    from utils.chat_client import _env_int
    assert _env_int("MESHANCHOR_CHAT_CHANNEL", 7) == 7


def test_env_int_parses_valid(monkeypatch):
    monkeypatch.setenv("MESHANCHOR_CHAT_CHANNEL", "3")
    from utils.chat_client import _env_int
    assert _env_int("MESHANCHOR_CHAT_CHANNEL", 0) == 3


def test_env_int_falls_back_on_garbage(monkeypatch):
    monkeypatch.setenv("MESHANCHOR_CHAT_CHANNEL", "not-a-number")
    from utils.chat_client import _env_int
    assert _env_int("MESHANCHOR_CHAT_CHANNEL", 0) == 0


def test_env_float_floor_enforced(monkeypatch):
    monkeypatch.setenv("MESHANCHOR_CHAT_POLL", "0.1")
    from utils.chat_client import _env_float
    # Floor 0.5 means 0.1 is clamped up
    assert _env_float("MESHANCHOR_CHAT_POLL", 2.0, minimum=0.5) == 0.5


def test_chat_client_honors_constructor_args():
    from utils.chat_client import ChatClient, POLL_INTERVAL_S_MIN
    c = ChatClient(channel=4, poll_interval=3.5)
    assert c._channel == 4
    assert c._poll_interval == 3.5
    # Floor enforced even when caller passes too-low value
    c2 = ChatClient(channel=0, poll_interval=0.1)
    assert c2._poll_interval == POLL_INTERVAL_S_MIN


def test_chat_client_main_reads_env(monkeypatch):
    """``main`` reads CHANNEL and POLL from env."""
    monkeypatch.setenv("MESHANCHOR_CHAT_CHANNEL", "5")
    monkeypatch.setenv("MESHANCHOR_CHAT_POLL", "1.5")
    from utils.chat_client import ChatClient
    captured = {}

    real_init = ChatClient.__init__

    def spy(self, *a, **kw):
        captured.update(kw)
        # Intercept run() so we don't actually start polling
        self._stop = MagicMock()
        self._stop.is_set = lambda: True
        real_init(self, *a, **kw)

    with patch.object(ChatClient, "__init__", spy), \
            patch.object(ChatClient, "run", return_value=0):
        from utils.chat_client import main
        main([])
    assert captured.get("channel") == 5
    assert captured.get("poll_interval") == 1.5


# ---------------------------------------------------------------------------
# 2. Chat pane env file install + preservation
# ---------------------------------------------------------------------------

def test_chat_pane_env_template_exists():
    p = REPO_ROOT / "templates" / "python" / "chat_pane.env"
    assert p.is_file()
    text = p.read_text()
    assert "MESHANCHOR_CHAT_API" in text
    assert "MESHANCHOR_CHAT_CHANNEL" in text
    assert "MESHANCHOR_CHAT_POLL" in text


def test_chat_pane_state_includes_env_installed_key(monkeypatch, tmp_path):
    """``_chat_pane_state`` reports env_installed.

    Patches `_unit_dest` / `_wrapper_dest` / `_env_dest` directly to a
    clean tmp_path so the test doesn't see a leftover install on the
    dev box (get_real_user_home() consults LOGNAME so HOME-monkeypatching
    isn't sufficient on its own).
    """
    from handlers.chat_pane import ChatPaneHandler
    from handlers import _chat_pane_service_ops as ops_mod

    fake_unit = tmp_path / "systemd" / "user" / "meshcore-chat.service"
    fake_wrapper = tmp_path / "config" / "meshanchor" / "chat_client_wrapper.sh"
    fake_env = tmp_path / "config" / "meshanchor" / "chat_pane.env"

    h = ChatPaneHandler()
    h.set_context(make_handler_context())
    with patch.object(ops_mod, "_unit_dest", return_value=fake_unit), \
            patch.object(ops_mod, "_wrapper_dest", return_value=fake_wrapper), \
            patch.object(ops_mod, "_env_dest", return_value=fake_env), \
            patch.object(h, "_user_systemctl_text", return_value=(-1, "")):
        s = h._chat_pane_state()
    assert "env_installed" in s
    assert s["env_installed"] is False


def test_chat_pane_install_seeds_env_file(tmp_path):
    """First install copies chat_pane.env into ~/.config/meshanchor.

    Redirects the real-home destination paths into tmp_path so the
    test never touches the operator's actual config dir.
    """
    from handlers import _chat_pane_service_ops as ops_mod
    from handlers._chat_pane_service_ops import _ENV_TEMPLATE
    from handlers.chat_pane import ChatPaneHandler

    fake_unit = tmp_path / "systemd" / "user" / "meshcore-chat.service"
    fake_wrapper = tmp_path / "config" / "meshanchor" / "chat_client_wrapper.sh"
    fake_env = tmp_path / "config" / "meshanchor" / "chat_pane.env"

    h = ChatPaneHandler()
    h.set_context(make_handler_context())
    with patch.object(ops_mod, "_unit_dest", return_value=fake_unit), \
            patch.object(ops_mod, "_wrapper_dest", return_value=fake_wrapper), \
            patch.object(ops_mod, "_env_dest", return_value=fake_env), \
            patch("shutil.which", lambda name: f"/usr/bin/{name}"), \
            patch.object(h, "_systemctl_user", return_value=(True, "")), \
            patch.object(h, "_enable_linger_quiet"):
        ok, _msg = h._install_user_unit()

    assert ok
    assert fake_env.exists()
    assert fake_env.read_text() == _ENV_TEMPLATE.read_text()


def test_chat_pane_install_preserves_operator_env_edits(tmp_path):
    """Subsequent install must NOT overwrite operator edits to chat_pane.env."""
    from handlers import _chat_pane_service_ops as ops_mod
    from handlers.chat_pane import ChatPaneHandler

    fake_unit = tmp_path / "systemd" / "user" / "meshcore-chat.service"
    fake_wrapper = tmp_path / "config" / "meshanchor" / "chat_client_wrapper.sh"
    fake_env = tmp_path / "config" / "meshanchor" / "chat_pane.env"

    custom = (
        "MESHANCHOR_CHAT_API=http://example.test:9999\n"
        "MESHANCHOR_CHAT_CHANNEL=7\n"
        "MESHANCHOR_CHAT_POLL=10\n"
    )
    fake_env.parent.mkdir(parents=True, exist_ok=True)
    fake_env.write_text(custom)

    h = ChatPaneHandler()
    h.set_context(make_handler_context())
    with patch.object(ops_mod, "_unit_dest", return_value=fake_unit), \
            patch.object(ops_mod, "_wrapper_dest", return_value=fake_wrapper), \
            patch.object(ops_mod, "_env_dest", return_value=fake_env), \
            patch("shutil.which", lambda name: f"/usr/bin/{name}"), \
            patch.object(h, "_systemctl_user", return_value=(True, "")), \
            patch.object(h, "_enable_linger_quiet"):
        ok, _msg = h._install_user_unit()

    assert ok
    # Operator edits preserved verbatim.
    assert fake_env.read_text() == custom


# ---------------------------------------------------------------------------
# 3. Chat pane Service Control offers new menu items
# ---------------------------------------------------------------------------

def test_chat_pane_service_menu_offers_edit_config():
    src = (LAUNCHER_TUI / "handlers" / "chat_pane.py").read_text()
    assert '"edit_config"' in src
    assert '"reset_config"' in src
    assert '"linger"' in src
    assert "self._edit_env_config" in src
    assert "self._reset_env_config" in src
    assert "self._enable_linger_action" in src


# ---------------------------------------------------------------------------
# 4. Wrapper sources env file
# ---------------------------------------------------------------------------

def test_chat_client_wrapper_sources_env_file():
    p = REPO_ROOT / "templates" / "python" / "chat_client_wrapper.sh"
    text = p.read_text()
    assert ".config/meshanchor/chat_pane.env" in text
    # The wrapper must use ``set -a`` / ``. <file>`` / ``set +a``
    assert "set -a" in text
    assert "set +a" in text


# ---------------------------------------------------------------------------
# 5. NomadNet tmux mixin wired into the handler
# ---------------------------------------------------------------------------

def test_nomadnet_handler_mro_includes_tmux_mixin():
    from handlers.nomadnet import NomadNetHandler
    from handlers._nomadnet_tmux_service_ops import (
        NomadNetTmuxServiceOpsMixin,
    )
    assert NomadNetTmuxServiceOpsMixin in NomadNetHandler.__mro__


def test_nomadnet_handler_exposes_tmux_service_menu():
    from handlers.nomadnet import NomadNetHandler
    h = NomadNetHandler()
    assert hasattr(h, "_nomadnet_tmux_service_menu")
    assert hasattr(h, "_nomadnet_tmux_state")
    assert hasattr(h, "_attach_nomadnet_tmux")
    assert hasattr(h, "_install_nomadnet_tmux_unit")


def test_nomadnet_menu_offers_tmux_service_action():
    src = (LAUNCHER_TUI / "handlers" / "nomadnet.py").read_text()
    assert '"tmux_service"' in src
    assert "_nomadnet_tmux_service_menu" in src


def test_nomadnet_tmux_state_no_unit(monkeypatch, tmp_path):
    """When the unit + wrapper aren't installed, state reflects that.

    The dev box may have a real ``~/.config/systemd/user/nomadnet.service``
    from prior installs, so we redirect ``_unit_dest`` and ``_wrapper_dest``
    to a clean tmp_path rather than relying on HOME monkeypatching
    (``get_real_user_home`` consults LOGNAME first, which still resolves
    to the real user).
    """
    from handlers.nomadnet import NomadNetHandler
    from handlers import _nomadnet_tmux_service_ops as ops_mod

    fake_unit = tmp_path / "systemd" / "user" / "nomadnet.service"
    fake_wrapper = tmp_path / "config" / "meshanchor" / "nomadnet_wrapper.py"

    h = NomadNetHandler()
    h.set_context(make_handler_context())
    with patch.object(ops_mod, "_unit_dest", return_value=fake_unit), \
            patch.object(ops_mod, "_wrapper_dest", return_value=fake_wrapper), \
            patch.object(h, "_user_systemctl_text_nn", return_value=(-1, "")):
        s = h._nomadnet_tmux_state()
    assert s["unit_installed"] is False
    assert s["wrapper_installed"] is False
    assert s["active"] is False


def test_nomadnet_tmux_state_line_active_format():
    from handlers.nomadnet import NomadNetHandler
    h = NomadNetHandler()
    h.set_context(make_handler_context())
    line = h._nomadnet_tmux_service_state_line({
        "unit_installed": True, "wrapper_installed": True,
        "tmux_present": True, "tmux_session": True,
        "active": True, "enabled": True, "sub_state": "running",
        "main_pid": 1234, "n_restarts": 0, "error": None,
    })
    assert "active" in line.lower()
    assert "tmux up" in line.lower()


# ---------------------------------------------------------------------------
# 6. Templates exist + no MeshForge leakage
# ---------------------------------------------------------------------------

def test_nomadnet_tmux_unit_template_exists_with_placeholder():
    p = REPO_ROOT / "templates" / "systemd" / "nomadnet-tmux-user.service"
    assert p.is_file()
    text = p.read_text()
    assert "__NOMADNET_EXEC__" in text
    assert "tmux new-session -d -s nomadnet" in text


def test_nomadnet_wrapper_template_exists_no_meshforge_path():
    p = REPO_ROOT / "templates" / "python" / "nomadnet_wrapper.py"
    assert p.is_file()
    text = p.read_text()
    assert "/opt/meshforge" not in text
    assert "MeshForge" not in text or "Ported from MeshForge" in text
    assert "_EXIT_AUTH_MISMATCH = 87" in text
    assert "AuthenticationError" in text


def test_nomadnet_tmux_unit_no_meshforge_leakage():
    p = REPO_ROOT / "templates" / "systemd" / "nomadnet-tmux-user.service"
    text = p.read_text()
    # We tolerate "Ported from MeshForge" mentions in comments but no
    # /opt/meshforge or MeshForge-managed strings should reach the user.
    assert "/opt/meshforge" not in text
    assert "MeshForge-managed" not in text
    assert "MeshAnchor-managed" in text


def test_existing_simple_unit_template_preserved():
    """The legacy non-tmux template stays in place for install_noc.sh."""
    p = REPO_ROOT / "templates" / "systemd" / "nomadnet-user.service"
    assert p.is_file()
    text = p.read_text()
    # The simple-daemon unit doesn't use tmux
    assert "tmux new-session" not in text
