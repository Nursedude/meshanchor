"""Fleet Monitor must point at the map service's HTTP port, not its WebSocket port.

Regression pin for the 2026-07-25 finding: `DEFAULT_MAP_URL` was
`http://127.0.0.1:5001`, but :5001 is the map service's **WebSocket** port
(`websockets` answers plain HTTP with `426 Upgrade Required`). Every Fleet
Monitor panel therefore failed on every stock box, and the handler's error
path blamed a service that was running fine:

    Could not reach http://127.0.0.1:5001/fleet/slo
    HTTP Error 426: Upgrade Required
    Make sure meshanchor-map.service is running.

Two consumers of one constant, independently hardcoded — honest_failure_modes
#5. So these tests DERIVE the expected port from `MapServer.__init__`, the
signature the map service actually binds from, instead of writing 5000 down a
second time. If the map service ever moves its HTTP port, this fails loudly
rather than drifting.
"""

import inspect
import os
import sys
from urllib.parse import urlparse

import pytest

# Ensure src and launcher_tui directories are importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src', 'launcher_tui'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from launcher_tui.handlers.fleet_monitor import (  # noqa: E402
    DEFAULT_MAP_URL,
    MAP_URL_ENV,
    FleetMonitorHandler,
)
from utils.map_data_service import MapServer  # noqa: E402


def _map_server_default(param: str) -> int:
    """The port MapServer actually defaults to — the single source of truth."""
    default = inspect.signature(MapServer.__init__).parameters[param].default
    assert isinstance(default, int), f"MapServer.{param} default is not an int"
    return default


@pytest.fixture
def handler():
    from unittest.mock import MagicMock
    h = FleetMonitorHandler()
    h.ctx = MagicMock()
    return h


class TestFleetMonitorMapUrl:
    """DEFAULT_MAP_URL must track the map service's HTTP listener."""

    def test_default_url_uses_map_server_http_port(self):
        """The handler's default port == MapServer's HTTP port default."""
        port = urlparse(DEFAULT_MAP_URL).port
        assert port == _map_server_default("port"), (
            f"DEFAULT_MAP_URL is {DEFAULT_MAP_URL} but MapServer serves HTTP on "
            f"{_map_server_default('port')}"
        )

    def test_default_url_is_not_the_websocket_port(self):
        """:5001 speaks WebSocket and answers HTTP with 426 — never fetch JSON there."""
        port = urlparse(DEFAULT_MAP_URL).port
        ws_port = _map_server_default("websocket_port")
        assert port != ws_port, (
            f"DEFAULT_MAP_URL points at the WebSocket port {ws_port}; "
            "every /fleet/* fetch will fail with HTTP 426 Upgrade Required"
        )

    def test_default_url_is_loopback_http(self):
        """The TUI is a thin local client — loopback HTTP, no radio dependency."""
        parsed = urlparse(DEFAULT_MAP_URL)
        assert parsed.scheme == "http"
        assert parsed.hostname == "127.0.0.1"


class TestFleetMonitorUrlOverride:
    """The env override stays the portability escape hatch."""

    def test_env_override_wins(self, handler, monkeypatch):
        monkeypatch.setenv(MAP_URL_ENV, "http://127.0.0.1:5002")
        captured = {}

        def fake_urlopen(req, timeout=None):
            captured["url"] = req.full_url
            raise AssertionError("stop after URL capture")

        import urllib.request
        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        with pytest.raises(AssertionError):
            handler._fetch_json("/fleet/slo")
        assert captured["url"] == "http://127.0.0.1:5002/fleet/slo"

    def test_http_error_does_not_blame_a_running_service(self, handler, monkeypatch):
        """A 426 means the server ANSWERED — don't send the operator after a
        daemon that just replied. This is the misdirection that made the
        original defect read as 'the map service is flaky'."""
        import urllib.error
        import urllib.request

        def fake_urlopen(req, timeout=None):
            raise urllib.error.HTTPError(
                req.full_url, 426, "Upgrade Required", {}, None)

        monkeypatch.delenv(MAP_URL_ENV, raising=False)
        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        assert handler._fetch_json("/fleet/slo") is None

        _title, body = handler.ctx.dialog.msgbox.call_args[0]
        assert "426" in body
        assert "IS running" in body, "must not imply the service is down"
        assert "Make sure meshanchor-map.service is running" not in body

    def test_connection_refused_still_points_at_the_service(self, handler, monkeypatch):
        """A genuine connect failure keeps the original 'is it running?' hint."""
        import urllib.error
        import urllib.request

        def fake_urlopen(req, timeout=None):
            raise urllib.error.URLError(ConnectionRefusedError(111, "refused"))

        monkeypatch.delenv(MAP_URL_ENV, raising=False)
        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        assert handler._fetch_json("/fleet/slo") is None

        _title, body = handler.ctx.dialog.msgbox.call_args[0]
        assert "Make sure meshanchor-map.service is running" in body

    def test_default_used_when_env_absent(self, handler, monkeypatch):
        monkeypatch.delenv(MAP_URL_ENV, raising=False)
        captured = {}

        def fake_urlopen(req, timeout=None):
            captured["url"] = req.full_url
            raise AssertionError("stop after URL capture")

        import urllib.request
        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        with pytest.raises(AssertionError):
            handler._fetch_json("/fleet/slo")
        assert captured["url"] == f"{DEFAULT_MAP_URL}/fleet/slo"
