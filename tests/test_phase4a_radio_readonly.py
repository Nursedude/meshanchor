"""Phase 4a — read-only MeshCore radio config.

Three layers under test:

1. MeshCoreHandler radio-state cache — _refresh_radio_state populates a
   plausible snapshot in simulation mode; get_radio_state returns it.
2. ConfigAPIHandler /radio dispatch — 503 when no handler, JSON envelope
   on success, refresh query param threaded through.
3. TUI MeshCoreHandler — _radio_fetch_state returns shaped dict on each
   wire outcome; preset-name mapping covers the known table.
"""
import asyncio
import io
import json
import os
import sys
import threading
import urllib.error
from queue import Queue
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

SRC_DIR = os.path.join(os.path.dirname(__file__), "..", "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from utils.config_api import ConfigAPIHandler
from gateway.meshcore_handler import (
    MeshCoreHandler,
    MeshCoreSimulator,
    _clear_active_handler,
    _empty_radio_state,
    get_active_handler,
)
from launcher_tui.handlers.meshcore import MeshCoreHandler as TUIMeshCoreHandler


# ─────────────────────────────────────────────────────────────────────
# Shared fixtures (mirrors test_config_api_chat.py)
# ─────────────────────────────────────────────────────────────────────


def _make_api_stub(path: str, method: str = "GET", body: bytes = b""):
    h = ConfigAPIHandler.__new__(ConfigAPIHandler)
    h.path = path
    h.command = method
    h.headers = {"Content-Length": str(len(body))}
    h.rfile = io.BytesIO(body)
    h.wfile = io.BytesIO()
    h.client_address = ("127.0.0.1", 50000)
    h.api = None
    h.send_response = MagicMock()
    h.send_header = MagicMock()
    h.end_headers = MagicMock()
    return h


def _read_response(handler) -> dict:
    raw = handler.wfile.getvalue().decode()
    return json.loads(raw) if raw else {}


@pytest.fixture
def mock_meshcore_config():
    meshcore = SimpleNamespace(
        enabled=True, device_path="/dev/ttyUSB1", baud_rate=115200,
        connection_type="serial", tcp_host="localhost", tcp_port=4000,
        auto_fetch_messages=True, bridge_channels=True, bridge_dms=True,
        simulation_mode=True, channel_poll_interval_sec=5,
    )
    return SimpleNamespace(
        meshcore=meshcore,
        meshtastic=SimpleNamespace(host="localhost", port=4403),
    )


@pytest.fixture
def daemon_handler(mock_meshcore_config):
    h = MeshCoreHandler(
        config=mock_meshcore_config,
        node_tracker=MagicMock(),
        health=MagicMock(record_error=MagicMock(return_value="transient")),
        stop_event=threading.Event(),
        stats={},
        stats_lock=threading.Lock(),
        message_queue=Queue(maxsize=10),
    )
    yield h
    _clear_active_handler(h)


# ─────────────────────────────────────────────────────────────────────
# 1. Daemon-side cache
# ─────────────────────────────────────────────────────────────────────


class TestRadioStateCache:
    def test_empty_state_shape(self):
        state = _empty_radio_state()
        # Required keys for the HTTP/TUI layers
        for key in (
            "radio_freq_mhz", "radio_bw_khz", "radio_sf", "radio_cr",
            "tx_power_dbm", "max_tx_power_dbm", "max_channels", "channels",
            "node_name", "fw_build", "model", "fw_ver",
            "last_refresh_ts", "source", "error",
        ):
            assert key in state
        assert state["channels"] == []
        assert state["error"] is None

    def test_get_radio_state_returns_empty_before_refresh(self, daemon_handler):
        snap = daemon_handler.get_radio_state(refresh=False)
        assert snap["radio_freq_mhz"] is None
        assert snap["channels"] == []
        assert snap["source"] is None

    def test_refresh_with_simulator_populates_snapshot(self, daemon_handler):
        daemon_handler._meshcore = MeshCoreSimulator()
        asyncio.run(daemon_handler._refresh_radio_state())
        snap = daemon_handler.get_radio_state(refresh=False)
        assert snap["source"] == "simulator"
        assert snap["radio_freq_mhz"] == 869.525
        assert snap["radio_sf"] == 11
        assert snap["max_channels"] == 4
        assert snap["channels"] and snap["channels"][0]["name"] == "public"
        assert snap["error"] is None
        assert snap["last_refresh_ts"] is not None

    def test_refresh_when_disconnected_records_error(self, daemon_handler):
        daemon_handler._meshcore = None
        asyncio.run(daemon_handler._refresh_radio_state())
        snap = daemon_handler.get_radio_state(refresh=False)
        assert snap["error"] == "MeshCore not connected"
        assert snap["source"] is None

    def test_set_radio_error_does_not_clobber_prior_snapshot(self, daemon_handler):
        # Populate first via simulator
        daemon_handler._meshcore = MeshCoreSimulator()
        asyncio.run(daemon_handler._refresh_radio_state())
        # Then force an error stamp
        daemon_handler._set_radio_error("simulated read failure")
        snap = daemon_handler.get_radio_state(refresh=False)
        assert snap["error"] == "simulated read failure"
        # Prior values still present
        assert snap["radio_freq_mhz"] == 869.525
        assert snap["source"] == "simulator"

    def test_refresh_with_no_meshcore_py_records_error(self, daemon_handler):
        daemon_handler._meshcore = object()  # not a simulator, not None
        with patch("gateway.meshcore_handler._HAS_MESHCORE", False):
            asyncio.run(daemon_handler._refresh_radio_state())
        snap = daemon_handler.get_radio_state(refresh=False)
        assert snap["error"] == "meshcore_py not installed"


# ─────────────────────────────────────────────────────────────────────
# 2. HTTP /radio endpoint
# ─────────────────────────────────────────────────────────────────────


class TestRadioEndpoint:
    def test_returns_503_when_no_active_handler(self):
        active = get_active_handler()
        if active is not None:
            _clear_active_handler(active)
        h = _make_api_stub("/radio")
        h._handle_radio_get()
        body = _read_response(h)
        assert body == {"error": "MeshCore handler not active"}
        h.send_response.assert_called_with(503)

    def test_returns_radio_envelope_on_success(self, daemon_handler):
        # Pre-populate cache via simulator path
        daemon_handler._meshcore = MeshCoreSimulator()
        asyncio.run(daemon_handler._refresh_radio_state())
        h = _make_api_stub("/radio")
        h._handle_radio_get()
        body = _read_response(h)
        assert "radio" in body
        assert body["radio"]["radio_freq_mhz"] == 869.525
        assert body["radio"]["source"] == "simulator"

    def test_refresh_query_param_threads_through(self, daemon_handler):
        with patch.object(daemon_handler, "get_radio_state") as mock_get:
            mock_get.return_value = _empty_radio_state()
            h = _make_api_stub("/radio?refresh=1")
            h._handle_radio_get()
            mock_get.assert_called_once_with(refresh=True)

    def test_refresh_true_alias(self, daemon_handler):
        with patch.object(daemon_handler, "get_radio_state") as mock_get:
            mock_get.return_value = _empty_radio_state()
            h = _make_api_stub("/radio?refresh=true")
            h._handle_radio_get()
            mock_get.assert_called_once_with(refresh=True)

    def test_no_refresh_param_means_no_refresh(self, daemon_handler):
        with patch.object(daemon_handler, "get_radio_state") as mock_get:
            mock_get.return_value = _empty_radio_state()
            h = _make_api_stub("/radio")
            h._handle_radio_get()
            mock_get.assert_called_once_with(refresh=False)

    def test_handler_exception_returns_500(self, daemon_handler):
        with patch.object(
            daemon_handler, "get_radio_state",
            side_effect=RuntimeError("boom"),
        ):
            h = _make_api_stub("/radio")
            h._handle_radio_get()
            body = _read_response(h)
            assert "Radio state read failed" in body["error"]
            h.send_response.assert_called_with(500)

    def test_get_dispatches_radio_path(self, daemon_handler):
        # Confirm do_GET routes /radio paths to _handle_radio_get
        # without falling through to the generic config API.
        h = _make_api_stub("/radio")
        with patch.object(h, "_handle_radio_get") as routed:
            h.do_GET()
            routed.assert_called_once()


# ─────────────────────────────────────────────────────────────────────
# 3. TUI handler — formatting + HTTP client + preset table
# ─────────────────────────────────────────────────────────────────────


class TestTUIRadioFetch:
    def _make_tui(self):
        # Lightweight TUI handler instance — _radio_fetch_state only
        # needs CHAT_API_BASE on the class (already set), no ctx.
        return TUIMeshCoreHandler.__new__(TUIMeshCoreHandler)

    def test_fetch_returns_radio_on_200(self):
        tui = self._make_tui()
        payload = {"radio": {"radio_freq_mhz": 915.0, "source": "radio"}}
        fake_resp = MagicMock()
        fake_resp.read.return_value = json.dumps(payload).encode()
        fake_resp.__enter__ = lambda self_: self_
        fake_resp.__exit__ = lambda *a: False
        with patch("urllib.request.urlopen", return_value=fake_resp):
            result = tui._radio_fetch_state(refresh=True)
        assert result["ok"] is True
        assert result["radio"]["radio_freq_mhz"] == 915.0

    def test_fetch_returns_503_on_http_error(self):
        tui = self._make_tui()
        err = urllib.error.HTTPError(
            "http://x/radio", 503,
            "Service Unavailable", {},
            io.BytesIO(b'{"error": "not active"}'),
        )
        with patch("urllib.request.urlopen", side_effect=err):
            result = tui._radio_fetch_state()
        assert result["ok"] is False
        assert result["status"] == 503
        assert "not active" in result["error"]

    def test_fetch_returns_status_none_on_connection_error(self):
        tui = self._make_tui()
        with patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.URLError("Connection refused"),
        ):
            result = tui._radio_fetch_state()
        assert result["ok"] is False
        assert result["status"] is None
        assert "Connection refused" in result["error"]

    def test_fetch_appends_refresh_when_requested(self):
        tui = self._make_tui()
        captured = {}

        def fake_urlopen(req, timeout=None):
            captured["url"] = req.full_url
            resp = MagicMock()
            resp.read.return_value = b'{"radio": {}}'
            resp.__enter__ = lambda self_: self_
            resp.__exit__ = lambda *a: False
            return resp

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            tui._radio_fetch_state(refresh=True)
        assert captured["url"].endswith("/radio?refresh=1")


class TestTUIPresetMapping:
    def test_known_eu_preset(self):
        name = TUIMeshCoreHandler._radio_preset_name(869.525, 250.0, 11, 5)
        assert name and "EU 869" in name

    def test_known_us_preset(self):
        name = TUIMeshCoreHandler._radio_preset_name(915.000, 250.0, 11, 5)
        assert name and "US 915" in name

    def test_unknown_tuple_returns_none(self):
        assert TUIMeshCoreHandler._radio_preset_name(902.0, 125.0, 7, 5) is None

    def test_none_inputs_return_none(self):
        assert TUIMeshCoreHandler._radio_preset_name(None, 250.0, 11, 5) is None
        assert TUIMeshCoreHandler._radio_preset_name(869.525, 250.0, None, 5) is None


class TestTUIFormatters:
    def test_fmt_freq_with_value(self):
        assert TUIMeshCoreHandler._fmt_freq(869.525) == "869.525 MHz"

    def test_fmt_freq_with_none(self):
        assert TUIMeshCoreHandler._fmt_freq(None) == "? MHz"

    def test_fmt_bw_drops_trailing_zeros(self):
        assert TUIMeshCoreHandler._fmt_bw(250.0) == "250 kHz"

    def test_fmt_bw_with_none(self):
        assert TUIMeshCoreHandler._fmt_bw(None) == "? kHz"
