"""Tests for the MeshCore TUI chat menu — covers HTTP plumbing and
formatting without requiring a running daemon. Real end-to-end
exercise is via running the daemon + TUI together against the chat API.
"""
import os
import sys
import urllib.error
from unittest.mock import MagicMock, patch

import pytest

# The TUI handlers live under src/launcher_tui — sys.path needs both
# the src root and src/launcher_tui (per how launcher_tui/main.py
# imports them as bare module names).
SRC_DIR = os.path.join(os.path.dirname(__file__), '..', 'src')
LAUNCHER_TUI_DIR = os.path.join(SRC_DIR, 'launcher_tui')
for p in (LAUNCHER_TUI_DIR, SRC_DIR):
    if p not in sys.path:
        sys.path.insert(0, p)


@pytest.fixture
def tui_handler():
    """A MeshCoreHandler (TUI) with a mock context."""
    from handlers.meshcore import MeshCoreHandler as TUIMeshCoreHandler

    h = TUIMeshCoreHandler.__new__(TUIMeshCoreHandler)
    h.ctx = MagicMock()
    return h


class TestChatFormatting:
    def test_rx_channel_message(self, tui_handler):
        entry = {
            "id": 1, "ts": 1714694400.0, "direction": "rx",
            "channel": 2, "sender": "abc12345", "text": "hello",
        }
        line = tui_handler._chat_format_entry(entry)
        assert "<<" in line
        assert "CHAN2" in line
        assert "abc12345" in line
        assert "hello" in line

    def test_tx_dm(self, tui_handler):
        entry = {
            "id": 2, "ts": 1714694400.0, "direction": "tx",
            "channel": None, "destination": "deadbeef", "text": "yo",
        }
        line = tui_handler._chat_format_entry(entry)
        assert ">>" in line
        assert "DM" in line
        assert "deadbeef" in line

    def test_missing_timestamp_is_safe(self, tui_handler):
        entry = {"id": 3, "direction": "rx", "text": "x"}
        line = tui_handler._chat_format_entry(entry)
        assert "??:??:??" in line


class TestChatApiReachable:
    def test_reachable_on_200(self, tui_handler):
        mock_resp = MagicMock()
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        with patch("urllib.request.urlopen", return_value=mock_resp):
            assert tui_handler._chat_api_reachable() is True

    def test_reachable_on_503_meshcore_inactive(self, tui_handler):
        """Daemon is up but MeshCore handler isn't active — chat menu
        should still open so operators can see why."""
        err = urllib.error.HTTPError(
            url="...", code=503, msg="MeshCore inactive",
            hdrs=None, fp=None,
        )
        with patch("urllib.request.urlopen", side_effect=err):
            assert tui_handler._chat_api_reachable() is True

    def test_unreachable_on_connection_refused(self, tui_handler):
        with patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.URLError("refused"),
        ):
            assert tui_handler._chat_api_reachable() is False


class TestChatFetchMessages:
    def test_fetch_messages_handles_connection_failure(self, tui_handler):
        with patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.URLError("refused"),
        ):
            result = tui_handler._chat_fetch_messages(since_id=0)
        assert "error" in result
        assert "connection" in result["error"]


class TestDaemonControl:
    """The daemon-control submenu shells out to systemctl/journalctl and
    delegates start/stop/restart to utils.service_check helpers."""

    def test_status_summary_uses_is_active(self, tui_handler):
        import subprocess as _sp
        completed = MagicMock(stdout="active\n", stderr="")
        with patch.object(_sp, "run", return_value=completed) as mock_run:
            line = tui_handler._daemon_status_summary()
        # Confirm it called the cheap is-active probe, not a full status dump.
        cmd = mock_run.call_args[0][0]
        assert cmd[:2] == ["systemctl", "is-active"]
        assert "meshanchor-daemon" in cmd[2]
        assert "active" in line

    def test_status_summary_handles_missing_systemctl(self, tui_handler):
        import subprocess as _sp
        with patch.object(_sp, "run", side_effect=FileNotFoundError()):
            line = tui_handler._daemon_status_summary()
        assert "error" in line.lower()

    def test_run_action_dispatches_to_service_check(self, tui_handler):
        """start/stop/restart must call the service_check helpers — that's
        the SSOT per CLAUDE.md (never raw systemctl from a handler)."""
        fake_module = MagicMock()
        fake_module.start_service = MagicMock(return_value=(True, "Started"))
        fake_module.stop_service = MagicMock(return_value=(True, "Stopped"))
        fake_module.restart_service = MagicMock(return_value=(True, "Restarted"))

        with patch.dict(sys.modules, {"utils.service_check": fake_module}):
            tui_handler._daemon_run_action("start")
            fake_module.start_service.assert_called_once_with(
                "meshanchor-daemon.service"
            )
            tui_handler._daemon_run_action("restart")
            fake_module.restart_service.assert_called_once_with(
                "meshanchor-daemon.service"
            )
            tui_handler._daemon_run_action("stop")
            fake_module.stop_service.assert_called_once_with(
                "meshanchor-daemon.service"
            )

    def test_stop_warns_before_acting(self, tui_handler):
        """Stopping the daemon kills the chat API + gateway. The TUI must
        prompt before calling the helper."""
        fake_module = MagicMock()
        fake_module.stop_service = MagicMock(return_value=(True, "Stopped"))

        # Operator declines the confirm dialog.
        tui_handler.ctx.dialog.yesno = MagicMock(return_value=False)
        with patch.dict(sys.modules, {"utils.service_check": fake_module}):
            tui_handler._daemon_stop()
        fake_module.stop_service.assert_not_called()

        # Operator confirms.
        tui_handler.ctx.dialog.yesno = MagicMock(return_value=True)
        with patch.dict(sys.modules, {"utils.service_check": fake_module}):
            tui_handler._daemon_stop()
        fake_module.stop_service.assert_called_once_with(
            "meshanchor-daemon.service"
        )

    def test_journal_recent_calls_journalctl(self, tui_handler):
        import subprocess as _sp
        completed = MagicMock(stdout="line1\nline2\n", stderr="")
        with patch.object(_sp, "run", return_value=completed) as mock_run:
            tui_handler._daemon_journal_recent()
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "journalctl"
        assert "-u" in cmd
        assert "meshanchor-daemon.service" in cmd
        assert "-n" in cmd
        assert "50" in cmd
