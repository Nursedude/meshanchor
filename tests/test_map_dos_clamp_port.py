"""Ported MeshForge maps-QA DoS/clamp findings (2026-07-06). MeshAnchor's map was
extracted from MeshForge and shared these input-handling defects."""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

from utils.map_http_handler import MapRequestHandler  # noqa: E402


def _base_handler(path: str = "/"):
    h = MapRequestHandler.__new__(MapRequestHandler)
    h.path = path
    h.headers = {}
    h.client_address = ("127.0.0.1", 50000)
    h.wfile = io.BytesIO()
    h.send_response = MagicMock()
    h.send_header = MagicMock()
    h.end_headers = MagicMock()
    return h


class TestSnapshotWindowClamp:
    def _run(self, path):
        h = _base_handler(path)
        cap = {}
        hist = MagicMock()
        hist.get_snapshot.side_effect = lambda timestamp, window_seconds: cap.setdefault(
            "window", window_seconds) or []
        coll = MagicMock()
        coll._history = hist
        h.collector = coll
        h._serve_json = lambda payload, status=200: None
        h._serve_snapshot()
        return cap.get("window")

    def test_huge_window_clamped_to_hour(self):
        assert self._run("/api/nodes/snapshot?window=999999999") == 3600

    def test_nonnumeric_window_defaults(self):
        assert self._run("/api/nodes/snapshot?window=abc") == 300

    def test_normal_window_preserved(self):
        assert self._run("/api/nodes/snapshot?window=300") == 300


class TestReceivedMessagesLimitClamp:
    def _limit_seen(self, path, monkeypatch):
        import utils.map_http_handler as mod
        cap = {}
        res = MagicMock()
        res.success = True
        res.data = {"messages": []}
        monkeypatch.setattr(mod.messaging, "get_messages",
                            lambda limit, network: cap.setdefault("limit", limit) or res)
        h = _base_handler(path)
        h._serve_json = lambda payload, status=200: None
        h._serve_received_messages()
        return cap.get("limit")

    def test_negative_limit_clamped_to_one(self, monkeypatch):
        assert self._limit_seen("/api/messages/received?limit=-1", monkeypatch) == 1

    def test_nonnumeric_limit_defaults(self, monkeypatch):
        assert self._limit_seen("/api/messages/received?limit=abc", monkeypatch) == 50

    def test_huge_limit_capped(self, monkeypatch):
        assert self._limit_seen("/api/messages/received?limit=99999", monkeypatch) == 500


class TestSendMessageDestinationClamp:
    def test_out_of_range_destination_rejected(self):
        body = json.dumps({"text": "hi", "destination": "99999999999"}).encode()
        h = _base_handler("/api/radio/message")
        h.headers = {"Content-Length": str(len(body))}
        h.rfile = io.BytesIO(body)
        cap = {}
        h._serve_json = lambda payload, status=200: cap.update(status=status, payload=payload)
        h._handle_send_message()
        assert cap["status"] == 400
        assert "out of range" in cap["payload"].get("error", "")


class TestStaticHtmlPathTraversal:
    def test_traversal_forbidden(self, tmp_path):
        web = tmp_path / "web"
        web.mkdir()
        h = _base_handler("/../../etc/passwd")
        h.web_dir = str(web)
        errs = {}
        h.send_error = lambda code, msg=None: errs.update(code=code)
        h._serve_static_html()
        assert errs.get("code") == 403


class TestMeshRedirectHostValidation:
    def _location(self, host):
        h = _base_handler("/mesh")
        h.api_proxy = None
        h.headers = {"Host": host}
        loc = {}
        h.send_header = lambda k, v: loc.update({k: v})
        h._serve_mesh_web_client()
        return loc.get("Location", "")

    def test_hostile_host_rejected(self):
        loc = self._location("evil.com/@x.attacker")
        assert "evil.com/@x" not in loc
        assert loc.startswith("https://") and loc.endswith(":9443/")

    def test_valid_host_used(self):
        assert self._location("192.168.1.5:5000") == "https://192.168.1.5:9443/"


class TestWeatherCacheLock:
    def test_lock_exists_and_is_lock(self):
        import threading
        assert isinstance(MapRequestHandler._weather_cache_lock, type(threading.Lock()))
