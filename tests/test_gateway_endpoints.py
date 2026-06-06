"""Tests for /api/gateway/queue + /api/gateway/delivery (MF Issue #74
probe port, S4).

Parity/federation surfaces: same shapes MeshForge's map daemon serves,
so dashboards and MF-side federation can read either NOC identically.
The in-process ActiveHealthProbe checks do NOT depend on these — they
read get_stats()/snapshot() directly.
"""

import io
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from utils.map_http_handler import MapRequestHandler  # noqa: E402
from utils import map_http_handler as mh  # noqa: E402


def _handler(path):
    h = MapRequestHandler.__new__(MapRequestHandler)
    h.path = path
    h.headers = {}
    h.client_address = ("127.0.0.1", 50000)
    h.wfile = io.BytesIO()
    h.send_response = MagicMock()
    h.send_header = MagicMock()
    h.end_headers = MagicMock()
    h.collector = MagicMock()
    return h


def _read_body(handler):
    return json.loads(handler.wfile.getvalue().decode("utf-8"))


class TestGatewayQueueEndpoint:
    def test_serves_queue_stats(self):
        h = _handler("/api/gateway/queue")
        fake_queue = MagicMock()
        fake_queue.get_stats.return_value = {
            "queue_depth": 4, "max_queue_size": 1000,
            "dead_letter": 0, "pending": 4, "in_progress": 0,
        }
        with patch.object(mh, "_HAS_MSG_QUEUE", True), \
             patch.object(mh, "_MessageQueue", return_value=fake_queue):
            h._serve_gateway_queue()
        body = _read_body(h)
        assert body["queue_depth"] == 4
        assert body["max_queue_size"] == 1000
        assert "timestamp" in body
        h.send_response.assert_called_with(200)

    def test_503_when_queue_module_unavailable(self):
        h = _handler("/api/gateway/queue")
        with patch.object(mh, "_HAS_MSG_QUEUE", False):
            h._serve_gateway_queue()
        body = _read_body(h)
        assert body["error"] == "message_queue_unavailable"
        h.send_response.assert_called_with(503)

    def test_500_on_stats_error(self):
        h = _handler("/api/gateway/queue")
        with patch.object(mh, "_HAS_MSG_QUEUE", True), \
             patch.object(mh, "_MessageQueue",
                          side_effect=RuntimeError("db locked")):
            h._serve_gateway_queue()
        body = _read_body(h)
        assert body["error"] == "queue_stats_unavailable"
        h.send_response.assert_called_with(500)


class TestGatewayDeliveryEndpoint:
    @pytest.fixture(autouse=True)
    def _isolated_counters(self, tmp_path, monkeypatch):
        from gateway import delivery_counters as _dc
        monkeypatch.setenv(
            "MESHANCHOR_DELIVERY_COUNTERS_DB",
            str(tmp_path / "counters.db"),
        )
        _dc._reset_singleton_for_tests()
        yield
        _dc._reset_singleton_for_tests()

    def test_serves_snapshot_shape(self):
        from gateway import delivery_counters as _dc
        _dc.record(_dc.DeliveryState.SENT, "m1", protocol="rns")
        h = _handler("/api/gateway/delivery")
        h._serve_gateway_delivery()
        body = _read_body(h)
        assert body["state_totals"]["sent"] == 1
        assert "drop_reasons" in body
        assert "recent" in body
        assert body["health"]["preflight_ok"] is True
        h.send_response.assert_called_with(200)

    def test_confirmation_rate_null_at_zero_traffic(self):
        h = _handler("/api/gateway/delivery")
        h._serve_gateway_delivery()
        body = _read_body(h)
        assert body["confirmation_rate"] is None
