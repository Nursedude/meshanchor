"""Tests for the MeshCore TUI chat menu — covers HTTP plumbing and
formatting without requiring a running daemon. Real end-to-end
exercise is via running the daemon + TUI together against the chat API.
"""
import json
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


class TestChatPostSend:
    def test_post_send_serializes_correctly(self, tui_handler):
        captured = {}

        class FakeResp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return b'{"queued": true}'

        def fake_urlopen(req, timeout=None):
            captured["url"] = req.full_url
            captured["body"] = req.data
            captured["headers"] = dict(req.headers)
            return FakeResp()

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            result = tui_handler._chat_post_send("hello", channel=2, destination=None)

        assert result == {"queued": True}
        assert captured["url"].endswith("/chat/send")
        body = json.loads(captured["body"])
        assert body == {"text": "hello", "channel": 2, "destination": None}
        # Header keys are normalized — tolerate either case.
        ct = captured["headers"].get("Content-type") or captured["headers"].get("Content-Type")
        assert ct == "application/json"

    def test_post_send_returns_error_on_http_400(self, tui_handler):
        from io import BytesIO
        err = urllib.error.HTTPError(
            url="...", code=400, msg="Bad",
            hdrs=None, fp=BytesIO(b'{"error": "missing text"}'),
        )
        with patch("urllib.request.urlopen", side_effect=err):
            result = tui_handler._chat_post_send("", channel=0)
        assert "error" in result
        assert "HTTP 400" in result["error"]

    def test_fetch_messages_handles_connection_failure(self, tui_handler):
        with patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.URLError("refused"),
        ):
            result = tui_handler._chat_fetch_messages(since_id=0)
        assert "error" in result
        assert "connection" in result["error"]
