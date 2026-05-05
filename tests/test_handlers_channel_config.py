"""Tests for ChannelConfigHandler (MN-1a).

Smoke coverage for handler structure, gating, the connection-check gate
on the main menu, dispatch routing, the PSK generator, and the gateway-
template flow. The meshtastic CLI calls are mocked so tests run offline.
"""

import base64
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(SRC / "launcher_tui"))
sys.path.insert(0, os.path.dirname(__file__))

from handler_test_utils import FakeDialog, make_handler_context


def _make_handler(**ctx_overrides):
    from handlers.channel_config import ChannelConfigHandler
    h = ChannelConfigHandler()
    defaults = dict(
        src_dir=str(SRC),
        get_meshtastic_cli=lambda: "meshtastic",
    )
    defaults.update(ctx_overrides)
    # TUIContext doesn't accept get_meshtastic_cli as a constructor kwarg,
    # so build via factory then attach the helper.
    cli_fn = defaults.pop("get_meshtastic_cli")
    src = defaults.pop("src_dir")
    ctx = make_handler_context(src_dir=src, **defaults)
    ctx.get_meshtastic_cli = cli_fn
    h.set_context(ctx)
    return h


def _ok(message="ok", data=None, raw=""):
    """Build a CommandResult-shaped object for mocked meshtastic calls."""
    return SimpleNamespace(
        success=True, message=message, data=data or {}, raw=raw,
    )


def _fail(message="boom", data=None):
    return SimpleNamespace(
        success=False, message=message, data=data or {}, raw="",
    )


# ── Structure / registration ────────────────────────────────────────


class TestStructure:

    def test_handler_registered(self):
        from handlers import get_all_handlers
        names = [c.__name__ for c in get_all_handlers()]
        assert "ChannelConfigHandler" in names

    def test_handler_id_and_section(self):
        from handlers.channel_config import ChannelConfigHandler
        h = ChannelConfigHandler()
        assert h.handler_id == "channel_config"
        assert h.menu_section == "configuration"

    def test_menu_items_gated_on_meshtastic_flag(self):
        from handlers.channel_config import ChannelConfigHandler
        h = ChannelConfigHandler()
        items = h.menu_items()
        assert len(items) == 1
        tag, _desc, flag = items[0]
        assert tag == "channels"
        assert flag == "meshtastic"

    def test_execute_unknown_action_is_safe(self):
        h = _make_handler()
        h.execute("nope")  # must not raise


# ── Connection gate ─────────────────────────────────────────────────


class TestConnectionGate:

    def test_menu_aborts_when_connection_fails(self):
        """If ensure_connection() fails, _channel_config_menu must NOT
        proceed to the choice dialog (one .menu call would fire an empty
        prompt the user can't escape)."""
        h = _make_handler()

        with patch("commands.meshtastic.ensure_connection", return_value=_fail()):
            h._channel_config_menu()

        menu_calls = [c for c in h.ctx.dialog.calls if c[0] == "menu"]
        assert menu_calls == []
        # Must show the failure msgbox
        assert h.ctx.dialog.last_msgbox_title == "Connection Failed"

    def test_menu_proceeds_when_connection_succeeds(self):
        h = _make_handler()
        h.ctx.dialog._menu_returns = ["back"]

        with patch(
            "commands.meshtastic.ensure_connection",
            return_value=_ok(data={"method": "tcp", "value": "127.0.0.1"}),
        ):
            h._channel_config_menu()

        menu_calls = [c for c in h.ctx.dialog.calls if c[0] == "menu"]
        assert len(menu_calls) == 1


# ── Dispatch routing ────────────────────────────────────────────────


class TestDispatch:

    @pytest.mark.parametrize("tag,method", [
        ("list", "_view_all_channels"),
        ("edit", "_edit_channel_menu"),
        ("add", "_add_channel"),
        ("disable", "_disable_channel"),
        ("primary", "_set_primary_channel"),
        ("gateway", "_set_gateway_channel"),
        ("psk", "_generate_psk"),
    ])
    def test_main_menu_routes_to_correct_method(self, tag, method):
        h = _make_handler()
        h.ctx.dialog._menu_returns = [tag, "back"]

        with patch(
            "commands.meshtastic.ensure_connection",
            return_value=_ok(data={"method": "tcp", "value": "127.0.0.1"}),
        ), patch.object(h, method) as mock:
            h._channel_config_menu()
            mock.assert_called_once()


# ── _generate_psk ──────────────────────────────────────────────────


class TestGeneratePsk:

    def test_generates_valid_base64_psk(self):
        h = _make_handler()
        h._generate_psk()
        # Should have shown exactly one msgbox with the PSK
        msgbox_calls = [c for c in h.ctx.dialog.calls if c[0] == "msgbox"]
        assert len(msgbox_calls) == 1
        title, body = msgbox_calls[0][1][0], msgbox_calls[0][1][1]
        assert title == "Generated PSK"
        assert "Base64:" in body
        assert "Hex:" in body
        # Pull the base64 line and verify it decodes to 32 bytes (256-bit key)
        for line in body.splitlines():
            line = line.strip()
            if line and not line.endswith(":") and "=" in line and "..." not in line:
                # First non-trivial base64-looking line is the b64 value
                try:
                    decoded = base64.b64decode(line)
                except Exception:
                    continue
                if len(decoded) == 32:
                    return
        pytest.fail("Generated PSK msgbox did not contain a 32-byte base64 key")


# ── _set_channel_role ──────────────────────────────────────────────


class TestSetChannelRole:

    def test_channel_zero_refused(self):
        """Channel 0 is always PRIMARY and must not be reassigned."""
        h = _make_handler()
        with patch("commands.meshtastic._run_command") as mock:
            h._set_channel_role(0)
            mock.assert_not_called()
        # Must show the explanatory msgbox
        assert "Channel 0" in (h.ctx.dialog.last_msgbox_text or "")

    def test_secondary_role_passes_module_settings_role(self):
        h = _make_handler()
        h.ctx.dialog._menu_returns = ["SECONDARY"]
        with patch(
            "commands.meshtastic._run_command", return_value=_ok()
        ) as mock:
            h._set_channel_role(2)
            mock.assert_called_once()
            args = mock.call_args[0][0]
            assert args == [
                "--ch-index", "2",
                "--ch-set", "module_settings.role", "SECONDARY",
            ]


# ── _set_channel_psk ──────────────────────────────────────────────


class TestSetChannelPsk:

    def test_random_passes_random_to_cli(self):
        h = _make_handler()
        h.ctx.dialog._menu_returns = ["random"]
        with patch(
            "commands.meshtastic.set_channel_psk", return_value=_ok()
        ) as mock:
            h._set_channel_psk(2)
            mock.assert_called_once_with(2, "random")

    def test_none_passes_none_to_cli(self):
        h = _make_handler()
        h.ctx.dialog._menu_returns = ["none"]
        with patch(
            "commands.meshtastic.set_channel_psk", return_value=_ok()
        ) as mock:
            h._set_channel_psk(2)
            mock.assert_called_once_with(2, "none")

    def test_default_passes_aq_token(self):
        h = _make_handler()
        h.ctx.dialog._menu_returns = ["default"]
        with patch(
            "commands.meshtastic.set_channel_psk", return_value=_ok()
        ) as mock:
            h._set_channel_psk(2)
            mock.assert_called_once_with(2, "AQ==")

    def test_custom_blank_input_aborts(self):
        h = _make_handler()
        h.ctx.dialog._menu_returns = ["custom"]
        h.ctx.dialog._inputbox_returns = [""]
        with patch("commands.meshtastic.set_channel_psk") as mock:
            h._set_channel_psk(2)
            mock.assert_not_called()


# ── _set_channel_name ──────────────────────────────────────────────


class TestSetChannelName:

    def test_truncates_name_to_12_chars(self):
        h = _make_handler()
        h.ctx.dialog._inputbox_returns = ["a" * 30]
        with patch(
            "commands.meshtastic.set_channel_name", return_value=_ok()
        ) as mock:
            h._set_channel_name(3)
            mock.assert_called_once_with(3, "a" * 12)

    def test_empty_input_aborts(self):
        h = _make_handler()
        h.ctx.dialog._inputbox_returns = [""]
        with patch("commands.meshtastic.set_channel_name") as mock:
            h._set_channel_name(3)
            mock.assert_not_called()


# ── _add_channel ──────────────────────────────────────────────────


class TestAddChannel:

    def test_with_encryption_calls_random_psk(self):
        h = _make_handler()
        h.ctx.dialog._menu_returns = ["3"]
        h.ctx.dialog._inputbox_returns = ["myname"]
        h.ctx.dialog._yesno_returns = [True]  # encryption yes

        with patch(
            "commands.meshtastic.set_channel_name", return_value=_ok()
        ), patch(
            "commands.meshtastic.set_channel_psk", return_value=_ok()
        ) as mock_psk:
            h._add_channel()
            mock_psk.assert_called_once_with(3, "random")

    def test_without_encryption_calls_psk_none(self):
        h = _make_handler()
        h.ctx.dialog._menu_returns = ["3"]
        h.ctx.dialog._inputbox_returns = ["myname"]
        h.ctx.dialog._yesno_returns = [False]

        with patch(
            "commands.meshtastic.set_channel_name", return_value=_ok()
        ), patch(
            "commands.meshtastic.set_channel_psk", return_value=_ok()
        ) as mock_psk:
            h._add_channel()
            mock_psk.assert_called_once_with(3, "none")

    def test_back_returns_without_calls(self):
        h = _make_handler()
        h.ctx.dialog._menu_returns = ["back"]
        with patch("commands.meshtastic.set_channel_name") as mock:
            h._add_channel()
            mock.assert_not_called()


# ── _set_gateway_channel ───────────────────────────────────────────


class TestSetGatewayChannel:

    def test_gateway_writes_slot_7(self):
        """Gateway channel must always land on index 7 (display slot 8)."""
        h = _make_handler()
        h.ctx.dialog._yesno_returns = [True]
        h.ctx.dialog._menu_returns = ["random"]

        captured_names = []
        captured_psks = []
        with patch(
            "commands.meshtastic.set_channel_name",
            side_effect=lambda i, n: captured_names.append((i, n)) or _ok(),
        ), patch(
            "commands.meshtastic.set_channel_psk",
            side_effect=lambda i, p: captured_psks.append((i, p)) or _ok(),
        ):
            h._set_gateway_channel()
        assert (7, "Gateway") in captured_names
        assert any(idx == 7 and psk == "random" for idx, psk in captured_psks)

    def test_user_can_decline(self):
        h = _make_handler()
        h.ctx.dialog._yesno_returns = [False]
        with patch("commands.meshtastic.set_channel_name") as mock:
            h._set_gateway_channel()
            mock.assert_not_called()


# ── Brand rename regression ─────────────────────────────────────────


class TestBrandRenames:

    def test_primary_default_uses_meshanchor(self):
        """Regression guard: the primary-channel default placeholder must
        say 'MeshAnchor' — a future copy-paste from MeshForge that forgets
        the rename gets caught here."""
        h = _make_handler()
        h.ctx.dialog._inputbox_returns = ["overridden"]
        with patch("commands.meshtastic.set_channel_name", return_value=_ok()):
            h._set_primary_channel()
        # The inputbox was called with init="MeshAnchor"
        inputbox_calls = [c for c in h.ctx.dialog.calls if c[0] == "inputbox"]
        assert inputbox_calls
        kwargs = inputbox_calls[0][2]
        assert kwargs.get("init") == "MeshAnchor"

    def test_gateway_yesno_mentions_meshanchor(self):
        """The gateway-channel explainer text must say MeshAnchor, not
        MeshForge — it tells the operator what the channel is for."""
        h = _make_handler()
        h.ctx.dialog._yesno_returns = [False]  # decline so no actual cli calls
        h._set_gateway_channel()
        yesno_calls = [c for c in h.ctx.dialog.calls if c[0] == "yesno"]
        body = yesno_calls[0][1][1]
        assert "MeshAnchor" in body
        assert "MeshForge" not in body


# ── Registry integration ──────────────────────────────────────────


class TestRegistry:

    def test_visible_when_meshtastic_flag_on(self):
        from handler_protocol import TUIContext
        from handler_registry import HandlerRegistry
        from handlers import get_all_handlers
        ctx = TUIContext(dialog=FakeDialog(), feature_flags={"meshtastic": True})
        reg = HandlerRegistry(ctx)
        for cls in get_all_handlers():
            reg.register(cls())
        tags = [tag for tag, _ in reg.get_menu_items("configuration")]
        assert "channels" in tags

    def test_hidden_when_meshtastic_flag_off(self):
        from handler_protocol import TUIContext
        from handler_registry import HandlerRegistry
        from handlers import get_all_handlers
        ctx = TUIContext(dialog=FakeDialog(), feature_flags={"meshtastic": False})
        reg = HandlerRegistry(ctx)
        for cls in get_all_handlers():
            reg.register(cls())
        tags = [tag for tag, _ in reg.get_menu_items("configuration")]
        assert "channels" not in tags
