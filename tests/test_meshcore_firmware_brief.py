"""Firmware brief on the MeshCore landing subtitle (roadmap 1c).

The radio reports a BUILD DATE and a COMPANION PROTOCOL version, and no
release string at all (measured on meshanchorRAK1 2026-09-21: fw_build
'19-Apr-2026', fw_ver 11, while the actual release is 1.15.0). These tests
pin that the brief never dresses either field up as a release number, and
that an unreachable daemon reads UNKNOWN rather than as an answer.
"""

import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src', 'launcher_tui'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.dirname(__file__))

from handler_test_utils import make_handler_context  # noqa: E402
from handlers.meshcore import MeshCoreHandler  # noqa: E402

LIVE = {"model": "RAK 4631", "fw_build": "19-Apr-2026", "fw_ver": 11,
        "node_name": "meshanchorRAK1", "source": "radio"}


@pytest.fixture
def handler():
    h = MeshCoreHandler()
    h.set_context(make_handler_context())
    h.ctx.wait_for_enter = MagicMock()
    return h


def _with_state(handler, result):
    return patch.object(type(handler), "_radio_fetch_state", return_value=result)


class TestBriefContent:
    def test_renders_the_live_shape(self, handler):
        with _with_state(handler, {"ok": True, "radio": LIVE}):
            brief = handler._meshcore_fw_brief()
        assert brief == "RAK 4631 build 19-Apr-2026 proto v11"

    def test_protocol_byte_is_never_dressed_up_as_a_release(self, handler):
        """fw_ver 11 must read as a PROTOCOL version, never as firmware 11.

        The radio's real release that day was 1.15.0. Anything that labels
        11 as the firmware version is a confident wrong answer to the exact
        question an operator opens this menu to settle before a flash.
        """
        with _with_state(handler, {"ok": True, "radio": LIVE}):
            brief = handler._meshcore_fw_brief()
        assert "proto v11" in brief
        low = brief.lower()
        assert "firmware 11" not in low
        assert "version 11" not in low
        assert "v1.15" not in low      # never invent the release either

    def test_simulator_is_flagged(self, handler):
        sim = dict(LIVE, source="simulator")
        with _with_state(handler, {"ok": True, "radio": sim}):
            assert handler._meshcore_fw_brief().startswith("[SIM]")


class TestBriefDegradesHonestly:
    def test_unreachable_daemon_is_unknown_not_absent(self, handler):
        with _with_state(handler, {"ok": False, "status": None,
                                   "error": "refused"}):
            brief = handler._meshcore_fw_brief()
        assert "?" in brief and "unreachable" in brief

    def test_reachable_but_unread_radio_is_distinct_from_unreachable(self, handler):
        """Two different blindnesses must not render identically."""
        with _with_state(handler, {"ok": True, "radio": {}}):
            unread = handler._meshcore_fw_brief()
        handler._fw_brief_cache = None
        with _with_state(handler, {"ok": False, "status": None, "error": "x"}):
            unreachable = handler._meshcore_fw_brief()
        assert unread != unreachable
        assert "not read yet" in unread

    def test_a_raising_fetch_never_breaks_the_menu(self, handler):
        with patch.object(type(handler), "_radio_fetch_state",
                          side_effect=RuntimeError("boom")):
            assert "?" in handler._meshcore_fw_brief()


class TestBriefCaching:
    def test_second_call_inside_ttl_does_not_refetch(self, handler):
        """The subtitle is rebuilt on every redraw; without the cache,
        sitting on the menu polls the daemon once per keystroke."""
        with patch.object(type(handler), "_radio_fetch_state",
                          return_value={"ok": True, "radio": LIVE}) as m:
            handler._meshcore_fw_brief()
            handler._meshcore_fw_brief()
            handler._meshcore_fw_brief()
        assert m.call_count == 1

    def test_fetch_uses_a_short_timeout_not_the_interactive_default(self, handler):
        """A 10s default would hang the MENU on an unreachable daemon."""
        with patch.object(type(handler), "_radio_fetch_state",
                          return_value={"ok": True, "radio": LIVE}) as m:
            handler._meshcore_fw_brief()
        assert m.call_args.kwargs.get("timeout", 10) <= 2


class TestStatusLineCarriesIt:
    def test_landing_subtitle_shows_the_firmware_fact(self, handler):
        """Config is PINNED, not read from whatever box runs the suite.

        The first draft guarded the assert with `if "ENABLED" in line`,
        which passes while asserting nothing on any box without a gateway
        config — including CI, where it matters most. A verdict that
        depends on un-pinned ambient state pins nothing.
        """
        cfg = MagicMock()
        cfg.meshcore.enabled = True
        cfg.meshcore.connection_type = "serial"
        cfg.meshcore.device_path = "/dev/ttyUSB0"
        loader = MagicMock()
        loader.load.return_value = cfg
        with patch("handlers.meshcore._HAS_GW_CONFIG", True), \
             patch("handlers.meshcore._GatewayConfig", loader), \
             patch.object(type(handler), "_meshcore_fw_brief",
                          return_value="RAK 4631 build 19-Apr-2026 proto v11"):
            line = handler._meshcore_status_line()
        assert "ENABLED" in line
        assert "19-Apr-2026" in line and "proto v11" in line

    def test_a_config_read_failure_still_names_itself(self, handler):
        """A failed config read must not masquerade as 'not enabled'."""
        loader = MagicMock()
        loader.load.side_effect = RuntimeError("unreadable")
        with patch("handlers.meshcore._HAS_GW_CONFIG", True), \
             patch("handlers.meshcore._GatewayConfig", loader):
            line = handler._meshcore_status_line()
        assert "unavailable" in line.lower()
