"""
Phase 8.2 — chat pane + MeshChatX regression tests.

Verifies:

1. ``utils.chat_client.ChatClient`` — slash-command parsing, message
   formatting, and the polling loop's last-id bookkeeping.
2. ``ChatPaneHandler`` registration + menu shape.
3. ``_chat_pane_service_ops`` state SSOT (no live systemctl calls).
4. ``MeshChatXHandler`` registration + correct ``menu_section``.
5. ``MeshChatXPaths`` returns sensible paths.
6. RNS submenu surfaces both NomadNet and MeshChatX as launch options.
7. Templates and installer exist and contain the expected
   placeholders.
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
# 1. utils.chat_client
# ---------------------------------------------------------------------------

def test_chat_client_format_entry_rx_channel():
    from utils.chat_client import _format_entry
    entry = {
        "id": 1, "ts": 1714792800, "direction": "rx",
        "channel": 2, "sender": "KH6XYZ", "text": "hello",
    }
    out = _format_entry(entry)
    assert "KH6XYZ" in out
    assert "hello" in out
    assert "ch2" in out


def test_chat_client_format_entry_tx_dm():
    from utils.chat_client import _format_entry
    entry = {
        "id": 2, "ts": 1714792800, "direction": "tx",
        "destination": "abc12345xyz", "sender": "me", "text": "ack",
    }
    out = _format_entry(entry)
    assert "DM[abc12345" in out
    assert "ack" in out


def test_chat_client_handle_command_quit():
    from utils.chat_client import ChatClient
    c = ChatClient()
    handled = c._handle_command("/quit")
    assert handled is True
    assert c._stop.is_set()


def test_chat_client_handle_command_ch_switch(capsys):
    from utils.chat_client import ChatClient
    c = ChatClient()
    c._handle_command("/ch 5")
    assert c._channel == 5
    out = capsys.readouterr().out
    assert "5" in out


def test_chat_client_handle_command_invalid_ch(capsys):
    from utils.chat_client import ChatClient
    c = ChatClient()
    c._handle_command("/ch foo")
    assert c._channel == 0  # unchanged
    assert "usage" in capsys.readouterr().out.lower()


def test_chat_client_help_command(capsys):
    from utils.chat_client import ChatClient
    c = ChatClient()
    handled = c._handle_command("/help")
    assert handled
    assert "/ch" in capsys.readouterr().out


def test_chat_client_poll_once_updates_last_id():
    from utils.chat_client import ChatClient
    c = ChatClient()
    fake_payload = {
        "count": 2,
        "messages": [
            {"id": 5, "ts": 0, "direction": "rx", "channel": 0,
             "sender": "x", "text": "a"},
            {"id": 7, "ts": 0, "direction": "rx", "channel": 0,
             "sender": "y", "text": "b"},
        ],
    }
    with patch.object(c, "_http_get", return_value=fake_payload):
        msgs = c._poll_once()
    assert len(msgs) == 2
    assert c._last_id == 7  # max id


# ---------------------------------------------------------------------------
# 2. ChatPaneHandler registration + menu shape
# ---------------------------------------------------------------------------

def test_chat_pane_handler_registered():
    from handlers import get_all_handlers
    names = {h.__name__ for h in get_all_handlers()}
    assert "ChatPaneHandler" in names


def test_chat_pane_section_meshcore():
    from handlers.chat_pane import ChatPaneHandler
    assert ChatPaneHandler.menu_section == "meshcore"


def test_chat_pane_menu_items_tag():
    from handlers.chat_pane import ChatPaneHandler
    items = ChatPaneHandler().menu_items()
    tags = [t for t, *_ in items]
    assert "chat_pane" in tags


# ---------------------------------------------------------------------------
# 3. _chat_pane_service_ops SSOT
# ---------------------------------------------------------------------------

def test_chat_pane_state_no_unit(tmp_path):
    """When the unit + wrapper aren't installed, state reflects that.

    Patches `_unit_dest` / `_wrapper_dest` directly because dev boxes
    may have a real install at ``~/.config/systemd/user/meshcore-chat.service``
    — `get_real_user_home()` consults LOGNAME so HOME monkeypatching
    isn't sufficient to isolate.
    """
    from handlers.chat_pane import ChatPaneHandler
    from handlers import _chat_pane_service_ops as ops_mod

    fake_unit = tmp_path / "systemd" / "user" / "meshcore-chat.service"
    fake_wrapper = tmp_path / "config" / "meshanchor" / "chat_client_wrapper.sh"

    h = ChatPaneHandler()
    h.set_context(make_handler_context())

    # Fake systemctl --user returning -1 (unavailable) so we don't
    # depend on a live user manager during the test sweep.
    with patch.object(ops_mod, "_unit_dest", return_value=fake_unit), \
            patch.object(ops_mod, "_wrapper_dest", return_value=fake_wrapper), \
            patch.object(h, "_user_systemctl_text", return_value=(-1, "")):
        s = h._chat_pane_state()
    assert s["unit_installed"] is False
    assert s["wrapper_installed"] is False
    assert s["active"] is False


def test_chat_pane_service_state_line_no_unit():
    from handlers.chat_pane import ChatPaneHandler
    h = ChatPaneHandler()
    h.set_context(make_handler_context())
    line = h._service_state_line({
        "unit_installed": False, "wrapper_installed": False,
        "tmux_present": True, "tmux_session": False,
        "active": False, "enabled": False, "sub_state": "",
        "main_pid": 0, "n_restarts": 0, "error": None,
    })
    assert "not installed" in line.lower()


def test_chat_pane_service_state_line_active():
    from handlers.chat_pane import ChatPaneHandler
    h = ChatPaneHandler()
    h.set_context(make_handler_context())
    line = h._service_state_line({
        "unit_installed": True, "wrapper_installed": True,
        "tmux_present": True, "tmux_session": True,
        "active": True, "enabled": True, "sub_state": "running",
        "main_pid": 1234, "n_restarts": 0, "error": None,
    })
    assert "active" in line.lower()
    assert "tmux up" in line.lower()


# ---------------------------------------------------------------------------
# 4. MeshChatXHandler registration + section
# ---------------------------------------------------------------------------

def test_meshchatx_handler_registered():
    from handlers import get_all_handlers
    names = {h.__name__ for h in get_all_handlers()}
    assert "MeshChatXHandler" in names


def test_meshchatx_section_rns():
    """Per the plan: MeshChatX lives under the RNS submenu, not Optional Gateways."""
    from handlers.meshchatx import MeshChatXHandler
    assert MeshChatXHandler.menu_section == "rns"


def test_meshchatx_menu_item_tag():
    from handlers.meshchatx import MeshChatXHandler
    items = MeshChatXHandler().menu_items()
    tags = [t for t, *_ in items]
    assert "meshchatx" in tags


# ---------------------------------------------------------------------------
# 5. MeshChatXPaths
# ---------------------------------------------------------------------------

def test_meshchatx_paths_storage_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("SUDO_USER", raising=False)
    from utils.paths import MeshChatXPaths
    p = MeshChatXPaths.get_storage_dir()
    assert str(p).endswith(".local/share/meshchatx")


def test_meshchatx_paths_unit_path(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("SUDO_USER", raising=False)
    from utils.paths import MeshChatXPaths
    p = MeshChatXPaths.get_unit_path()
    assert str(p).endswith(".config/systemd/user/meshchatx.service")


def test_meshchatx_paths_wrapper_meshanchor_namespace(monkeypatch, tmp_path):
    """Wrapper lives under ~/.config/meshanchor/ — never meshforge."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("SUDO_USER", raising=False)
    from utils.paths import MeshChatXPaths
    p = MeshChatXPaths.get_wrapper_path()
    assert "meshforge" not in str(p)
    assert ".config/meshanchor/meshchatx_wrapper.sh" in str(p)


# ---------------------------------------------------------------------------
# 6. RNS submenu surfaces both clients
# ---------------------------------------------------------------------------

def test_rns_menu_orders_clients_first():
    """nomadnet + meshchatx come before status/diagnostics in the menu."""
    from handlers.rns_menu import _RNS_ORDERING
    assert "nomadnet" in _RNS_ORDERING
    assert "meshchatx" in _RNS_ORDERING
    nomad_idx = _RNS_ORDERING.index("nomadnet")
    chatx_idx = _RNS_ORDERING.index("meshchatx")
    status_idx = _RNS_ORDERING.index("status")
    assert nomad_idx < status_idx
    assert chatx_idx < status_idx


def test_rns_menu_offers_nomadnet_launch():
    """The own_items dict in _rns_submenu includes a NomadNet launcher."""
    src = (LAUNCHER_TUI / "handlers" / "rns_menu.py").read_text()
    # Walk the function body to find the own_items dict.
    assert '"nomadnet": "Launch NomadNet' in src
    # Cross-section dispatch wiring
    assert 'self.ctx.registry.dispatch("mesh_networks", "nomadnet")' in src


# ---------------------------------------------------------------------------
# 7. Templates + installer exist with placeholders
# ---------------------------------------------------------------------------

def test_chat_client_wrapper_template_exists():
    p = REPO_ROOT / "templates" / "python" / "chat_client_wrapper.sh"
    assert p.is_file()
    text = p.read_text()
    assert "EXIT_API_UNREACHABLE" in text
    assert "MESHANCHOR_CHAT_API" in text
    assert "utils.chat_client" in text


def test_meshcore_chat_unit_template_has_placeholder():
    p = REPO_ROOT / "templates" / "systemd" / "meshcore-chat-user.service"
    assert p.is_file()
    text = p.read_text()
    assert "__CHAT_PANE_EXEC__" in text
    assert "tmux new-session -d -s meshcore-chat" in text


def test_meshchatx_wrapper_template_no_meshforge_path():
    p = REPO_ROOT / "templates" / "python" / "meshchatx_wrapper.sh"
    assert p.is_file()
    text = p.read_text()
    assert "/opt/meshforge" not in text
    assert "/opt/meshanchor" in text
    assert "EXIT_AUTH_MISMATCH" in text


def test_meshchatx_unit_template_has_placeholder():
    p = REPO_ROOT / "templates" / "systemd" / "meshchatx-user.service"
    assert p.is_file()
    text = p.read_text()
    assert "__MESHCHATX_EXEC__" in text


def test_meshchatx_installer_no_meshforge_refs():
    p = REPO_ROOT / "scripts" / "install_meshchatx.sh"
    assert p.is_file()
    text = p.read_text()
    assert "/opt/meshforge" not in text
    assert "meshanchor" in text
    # rns_alignment is MeshForge-specific and shouldn't be referenced
    assert "rns_alignment" not in text


def test_meshchatx_installer_executable():
    p = REPO_ROOT / "scripts" / "install_meshchatx.sh"
    assert os.access(str(p), os.X_OK), f"{p} is not executable"
