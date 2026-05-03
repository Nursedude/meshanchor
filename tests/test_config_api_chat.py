"""Tests for /chat/* endpoints on the daemon's config_api server.

These exercise ConfigAPIHandler's branching for chat paths without
spinning up a real HTTP server — we mock just enough of the
BaseHTTPRequestHandler I/O surface to drive _handle_chat_get and
_handle_chat_send.
"""
import io
import json
import os
import sys
import threading
from queue import Queue
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

SRC_DIR = os.path.join(os.path.dirname(__file__), '..', 'src')
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from utils.config_api import ConfigAPIHandler
from gateway.meshcore_handler import (
    MeshCoreHandler,
    _set_active_handler,
    _clear_active_handler,
    get_active_handler,
)


def _make_handler_stub(path: str, method: str = "GET", body: bytes = b""):
    """Stub ConfigAPIHandler with the I/O surface tests need.

    Builds the instance via __new__ to skip BaseHTTPRequestHandler's
    socket-binding constructor; then wires up the bytes-level rfile /
    wfile and the request line attrs the routing methods read.
    """
    h = ConfigAPIHandler.__new__(ConfigAPIHandler)
    h.path = path
    h.command = method
    h.headers = {"Content-Length": str(len(body))}
    h.rfile = io.BytesIO(body)
    h.wfile = io.BytesIO()
    h.client_address = ("127.0.0.1", 50000)
    h.api = None  # chat paths don't use config api
    h.send_response = MagicMock()
    h.send_header = MagicMock()
    h.end_headers = MagicMock()
    return h


@pytest.fixture
def mock_meshcore_config():
    meshcore = SimpleNamespace(
        enabled=True, device_path='/dev/ttyUSB1', baud_rate=115200,
        connection_type='serial', tcp_host='localhost', tcp_port=4000,
        auto_fetch_messages=True, bridge_channels=True, bridge_dms=True,
        simulation_mode=True, channel_poll_interval_sec=5,
    )
    return SimpleNamespace(
        meshcore=meshcore,
        meshtastic=SimpleNamespace(host='localhost', port=4403),
    )


@pytest.fixture
def active_handler(mock_meshcore_config):
    """Spin up a real MeshCoreHandler in simulation mode and ensure it's
    registered as the active handler for the duration of the test."""
    h = MeshCoreHandler(
        config=mock_meshcore_config,
        node_tracker=MagicMock(),
        health=MagicMock(record_error=MagicMock(return_value='transient')),
        stop_event=threading.Event(),
        stats={},
        stats_lock=threading.Lock(),
        message_queue=Queue(maxsize=10),
    )
    yield h
    _clear_active_handler(h)


def _read_response(handler) -> dict:
    """Decode the JSON the handler wrote to wfile."""
    raw = handler.wfile.getvalue().decode()
    return json.loads(raw) if raw else {}


class TestChatGetMessages:
    def test_returns_503_when_no_active_handler(self):
        # No handler registered.
        h = _make_handler_stub("/chat/messages")
        with patch.object(MeshCoreHandler, '__init__', lambda *a, **kw: None):
            pass  # ensure import works without instantiation
        # Force-clear in case another test left state behind.
        for active in (get_active_handler(),):
            if active is not None:
                _clear_active_handler(active)
        h._handle_chat_get()
        body = _read_response(h)
        assert body == {"error": "MeshCore handler not active"}
        h.send_response.assert_called_with(503)

    def test_returns_messages_with_count(self, active_handler):
        active_handler.record_chat_message(direction="rx", text="alpha", channel=2)
        active_handler.record_chat_message(direction="tx", text="beta", channel=2)
        h = _make_handler_stub("/chat/messages")
        h._handle_chat_get()
        body = _read_response(h)
        assert body["count"] == 2
        assert [m["text"] for m in body["messages"]] == ["alpha", "beta"]

    def test_since_filter_works(self, active_handler):
        active_handler.record_chat_message(direction="rx", text="a")
        active_handler.record_chat_message(direction="rx", text="b")
        first_id = active_handler.get_recent_chat()[0]["id"]
        h = _make_handler_stub(f"/chat/messages?since={first_id}")
        h._handle_chat_get()
        body = _read_response(h)
        assert [m["text"] for m in body["messages"]] == ["b"]

    def test_channels_endpoint(self, active_handler):
        active_handler.record_chat_message(direction="rx", text="x", channel=1)
        active_handler.record_chat_message(direction="tx", text="y", channel=2)
        h = _make_handler_stub("/chat/channels")
        h._handle_chat_get()
        body = _read_response(h)
        chans = {c["channel"] for c in body["channels"]}
        assert chans == {1, 2}


class TestChatSend:
    def test_send_calls_handler(self, active_handler):
        body = json.dumps({"text": "hi", "channel": 2}).encode()
        h = _make_handler_stub("/chat/send", method="POST", body=body)
        with patch.object(active_handler, 'send_text', return_value=True) as mock_send:
            h._handle_chat_send()
        mock_send.assert_called_once_with("hi", destination=None, channel=2)
        result = _read_response(h)
        assert result["queued"] is True
        assert result["text"] == "hi"
        assert result["channel"] == 2

    def test_send_dm(self, active_handler):
        body = json.dumps({"text": "hi", "destination": "abc12345"}).encode()
        h = _make_handler_stub("/chat/send", method="POST", body=body)
        with patch.object(active_handler, 'send_text', return_value=True) as mock_send:
            h._handle_chat_send()
        mock_send.assert_called_once_with("hi", destination="abc12345", channel=0)

    def test_send_rejects_empty_text(self, active_handler):
        body = json.dumps({"text": "  ", "channel": 1}).encode()
        h = _make_handler_stub("/chat/send", method="POST", body=body)
        h._handle_chat_send()
        result = _read_response(h)
        assert result == {"error": "`text` is required"}
        h.send_response.assert_called_with(400)

    def test_send_rejects_non_string_text(self, active_handler):
        body = json.dumps({"text": 42}).encode()
        h = _make_handler_stub("/chat/send", method="POST", body=body)
        h._handle_chat_send()
        result = _read_response(h)
        assert result == {"error": "`text` is required"}

    def test_send_rejects_bad_channel(self, active_handler):
        body = json.dumps({"text": "hi", "channel": "not-a-number"}).encode()
        h = _make_handler_stub("/chat/send", method="POST", body=body)
        h._handle_chat_send()
        result = _read_response(h)
        assert result == {"error": "`channel` must be an integer"}

    def test_send_503_when_handler_returns_false(self, active_handler):
        body = json.dumps({"text": "hi", "channel": 2}).encode()
        h = _make_handler_stub("/chat/send", method="POST", body=body)
        with patch.object(active_handler, 'send_text', return_value=False):
            h._handle_chat_send()
        result = _read_response(h)
        assert result == {"error": "MeshCore send queue full or not connected"}
        h.send_response.assert_called_with(503)
