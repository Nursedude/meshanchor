"""Phase 6 — meshforge-maps :8808 plugin scaffold.

Three layers under test:

1. `utils.meshforge_maps_client.MeshforgeMapsClient` — single-shot probe
   that never raises. /api/status reachable → AVAILABLE; offline / timeout
   / non-JSON → AVAILABLE=False with a populated `error` string. Optional
   /api/health and /api/sources fold in best-effort.
2. `_extract_source_names` — tolerant of two payload shapes the upstream
   API has used (list of strings vs list of dicts).
3. `launcher_tui.handlers.meshforge_maps.MeshforgeMapsHandler` — menu
   shape, `_format_status` rendering for both available + offline cases,
   `_open_browser` calls `webbrowser.open` with the right URL.
"""

import io
import json
import os
import sys
import urllib.error
from unittest.mock import MagicMock, patch

import pytest

SRC_DIR = os.path.join(os.path.dirname(__file__), "..", "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from utils.meshforge_maps_client import (
    DEFAULT_HOST,
    DEFAULT_PORT,
    MapsServiceStatus,
    MeshforgeMapsClient,
    _extract_source_names,
)
from launcher_tui.handlers.meshforge_maps import (
    INSTALL_HINT,
    MeshforgeMapsHandler,
    _format_status,
    _format_uptime,
)


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────


def _fake_response(payload: dict, status: int = 200) -> MagicMock:
    """Mock urllib.request.urlopen()'s context-manager response."""
    body = json.dumps(payload).encode("utf-8")
    cm = MagicMock()
    resp = MagicMock()
    resp.status = status
    resp.read.return_value = body
    cm.__enter__.return_value = resp
    cm.__exit__.return_value = False
    return cm


# ─────────────────────────────────────────────────────────────────────
# 1. Discovery client
# ─────────────────────────────────────────────────────────────────────


class TestMeshforgeMapsClient:
    def test_web_url_is_built_from_host_port(self):
        client = MeshforgeMapsClient(host="example.local", port=9999)
        assert client.web_url == "http://example.local:9999"

    def test_default_host_and_port(self):
        client = MeshforgeMapsClient()
        assert client.host == "localhost"
        assert client.port == 8808
        assert client.web_url == "http://localhost:8808"

    def test_probe_returns_unreachable_on_url_error(self):
        client = MeshforgeMapsClient()
        with patch(
            "utils.meshforge_maps_client.urllib.request.urlopen",
            side_effect=urllib.error.URLError("connection refused"),
        ):
            status = client.probe()
        assert status.available is False
        assert status.host == "localhost"
        assert status.port == 8808
        assert "unreachable" in (status.error or "")
        assert status.version is None
        assert status.health_score is None
        assert status.sources == []

    def test_probe_returns_unreachable_on_timeout(self):
        client = MeshforgeMapsClient()
        with patch(
            "utils.meshforge_maps_client.urllib.request.urlopen",
            side_effect=TimeoutError("timeout"),
        ):
            status = client.probe()
        assert status.available is False
        assert status.error  # populated with a hint

    def test_probe_returns_unreachable_on_oserror(self):
        """OSError covers DNS resolution failures and ENETUNREACH."""
        client = MeshforgeMapsClient()
        with patch(
            "utils.meshforge_maps_client.urllib.request.urlopen",
            side_effect=OSError("Network is unreachable"),
        ):
            status = client.probe()
        assert status.available is False

    def test_probe_returns_available_with_full_payload(self):
        client = MeshforgeMapsClient()
        responses = {
            "/api/status": {"version": "0.7.0-beta", "uptime_seconds": 12345.6},
            "/api/health": {"score": 87},
            "/api/sources": {"sources": ["meshtastic", "reticulum", "meshcore"]},
        }

        def fake_urlopen(url, timeout=None):
            for path, body in responses.items():
                if url.endswith(path):
                    return _fake_response(body)
            raise urllib.error.URLError(f"unexpected url {url}")

        with patch(
            "utils.meshforge_maps_client.urllib.request.urlopen",
            side_effect=fake_urlopen,
        ):
            status = client.probe()
        assert status.available is True
        assert status.version == "0.7.0-beta"
        assert status.health_score == 87
        assert status.sources == ["meshtastic", "reticulum", "meshcore"]
        assert status.uptime_seconds == pytest.approx(12345.6)
        assert status.error is None

    def test_probe_tolerates_missing_optional_endpoints(self):
        """If /api/status responds but /api/health 404s, we still report
        the service as available — health is best-effort."""
        client = MeshforgeMapsClient()

        def fake_urlopen(url, timeout=None):
            if url.endswith("/api/status"):
                return _fake_response({"version": "0.7.0-beta"})
            raise urllib.error.URLError("not found")

        with patch(
            "utils.meshforge_maps_client.urllib.request.urlopen",
            side_effect=fake_urlopen,
        ):
            status = client.probe()
        assert status.available is True
        assert status.version == "0.7.0-beta"
        assert status.health_score is None
        assert status.sources == []

    def test_probe_handles_non_200_status(self):
        client = MeshforgeMapsClient()
        with patch(
            "utils.meshforge_maps_client.urllib.request.urlopen",
            return_value=_fake_response({}, status=503),
        ):
            status = client.probe()
        assert status.available is False

    def test_probe_handles_non_json_body(self):
        client = MeshforgeMapsClient()

        def fake_urlopen(url, timeout=None):
            cm = MagicMock()
            resp = MagicMock()
            resp.status = 200
            resp.read.return_value = b"<html>not json</html>"
            cm.__enter__.return_value = resp
            cm.__exit__.return_value = False
            return cm

        with patch(
            "utils.meshforge_maps_client.urllib.request.urlopen",
            side_effect=fake_urlopen,
        ):
            status = client.probe()
        assert status.available is False
        assert status.error  # populated

    def test_probe_uses_configured_timeout(self):
        client = MeshforgeMapsClient(timeout=1.5)
        with patch(
            "utils.meshforge_maps_client.urllib.request.urlopen"
        ) as mock_open:
            mock_open.return_value = _fake_response({"version": "x"})
            client.probe()
        # First call (status) is the load-bearing one; assert it carries
        # our configured timeout.
        first_call = mock_open.call_args_list[0]
        assert first_call.kwargs.get("timeout") == 1.5

    def test_probe_string_uptime_is_coerced(self):
        """meshforge-maps has historically returned uptime as a string in
        some responses. The coerce helper should swallow that."""
        client = MeshforgeMapsClient()

        def fake_urlopen(url, timeout=None):
            if url.endswith("/api/status"):
                return _fake_response({"version": "0.5", "uptime_seconds": "42.5"})
            raise urllib.error.URLError("skip")

        with patch(
            "utils.meshforge_maps_client.urllib.request.urlopen",
            side_effect=fake_urlopen,
        ):
            status = client.probe()
        assert status.uptime_seconds == pytest.approx(42.5)

    def test_probe_unparseable_uptime_becomes_none(self):
        client = MeshforgeMapsClient()

        def fake_urlopen(url, timeout=None):
            if url.endswith("/api/status"):
                return _fake_response({"version": "0.5", "uptime_seconds": "not-a-number"})
            raise urllib.error.URLError("skip")

        with patch(
            "utils.meshforge_maps_client.urllib.request.urlopen",
            side_effect=fake_urlopen,
        ):
            status = client.probe()
        assert status.available is True
        assert status.uptime_seconds is None


# ─────────────────────────────────────────────────────────────────────
# 2. Sources extraction (tolerance for two payload shapes)
# ─────────────────────────────────────────────────────────────────────


class TestExtractSourceNames:
    def test_list_of_strings(self):
        assert _extract_source_names(
            {"sources": ["meshtastic", "reticulum"]}
        ) == ["meshtastic", "reticulum"]

    def test_list_of_dicts_with_enabled_true(self):
        payload = {"sources": [
            {"name": "meshtastic", "enabled": True},
            {"name": "reticulum", "enabled": True},
        ]}
        assert _extract_source_names(payload) == ["meshtastic", "reticulum"]

    def test_list_of_dicts_filters_disabled(self):
        payload = {"sources": [
            {"name": "meshtastic", "enabled": True},
            {"name": "aredn", "enabled": False},
        ]}
        assert _extract_source_names(payload) == ["meshtastic"]

    def test_list_of_dicts_default_enabled_when_field_missing(self):
        payload = {"sources": [{"name": "meshtastic"}]}
        assert _extract_source_names(payload) == ["meshtastic"]

    def test_missing_sources_key(self):
        assert _extract_source_names({}) == []

    def test_non_list_sources(self):
        assert _extract_source_names({"sources": "not a list"}) == []

    def test_mixed_shapes(self):
        payload = {"sources": ["meshtastic", {"name": "rns", "enabled": True}]}
        assert _extract_source_names(payload) == ["meshtastic", "rns"]


# ─────────────────────────────────────────────────────────────────────
# 3. TUI handler
# ─────────────────────────────────────────────────────────────────────


class TestMeshforgeMapsHandler:
    def test_handler_metadata(self):
        h = MeshforgeMapsHandler()
        assert h.handler_id == "meshforge_maps"
        assert h.menu_section == "maps_viz"

    def test_menu_items(self):
        h = MeshforgeMapsHandler()
        items = h.menu_items()
        keys = [item[0] for item in items]
        # Phase 6 shipped mf_status + mf_open; Phase 6.3 added mf_endpoint;
        # Phase 6.2 added mf_lifecycle. Lifecycle is asserted by Phase 6.2's
        # own test file — this test guards the prior contract only.
        assert keys[:2] == ["mf_status", "mf_open"]
        assert "mf_endpoint" in keys
        # Section-level "maps" gating handles flagging — per-row stays None.
        for item in items:
            assert item[2] is None

    def test_execute_dispatches_status(self):
        h = MeshforgeMapsHandler()
        ctx = MagicMock()
        h.ctx = ctx
        h.execute("mf_status")
        ctx.safe_call.assert_called_once()
        args = ctx.safe_call.call_args.args
        assert args[0] == "Meshforge Maps Status"
        assert args[1] == h._show_status

    def test_execute_dispatches_open(self):
        h = MeshforgeMapsHandler()
        ctx = MagicMock()
        h.ctx = ctx
        h.execute("mf_open")
        ctx.safe_call.assert_called_once()
        args = ctx.safe_call.call_args.args
        assert args[0] == "Open Meshforge Maps"
        assert args[1] == h._open_browser

    def test_execute_unknown_action_is_noop(self):
        h = MeshforgeMapsHandler()
        ctx = MagicMock()
        h.ctx = ctx
        h.execute("mf_bogus")
        ctx.safe_call.assert_not_called()

    def test_open_browser_calls_webbrowser_with_url(self):
        h = MeshforgeMapsHandler()
        ctx = MagicMock()
        ctx.wait_for_enter = MagicMock()
        h.ctx = ctx
        with patch(
            "launcher_tui.handlers.meshforge_maps.webbrowser.open"
        ) as mock_open, patch(
            "launcher_tui.handlers.meshforge_maps.clear_screen", create=True
        ):
            h._open_browser()
        mock_open.assert_called_once_with(f"http://{DEFAULT_HOST}:{DEFAULT_PORT}")


class TestFormatStatus:
    def test_unavailable_renders_install_hint(self):
        s = MapsServiceStatus(
            available=False, host="localhost", port=8808,
            error="connection refused",
        )
        out = _format_status(s)
        assert "NOT REACHABLE" in out
        assert "connection refused" in out
        assert INSTALL_HINT in out
        assert "systemctl start meshforge-maps" in out

    def test_unavailable_with_no_error_still_renders(self):
        s = MapsServiceStatus(available=False, host="localhost", port=8808)
        out = _format_status(s)
        assert "NOT REACHABLE" in out
        assert "unknown error" in out

    def test_available_renders_url_and_version(self):
        s = MapsServiceStatus(
            available=True, host="localhost", port=8808,
            version="0.7.0-beta", health_score=92,
            sources=["meshtastic", "reticulum"],
            uptime_seconds=3725,
        )
        out = _format_status(s)
        assert "RUNNING" in out
        assert "http://localhost:8808" in out
        assert "0.7.0-beta" in out
        assert "92/100" in out
        assert "meshtastic, reticulum" in out
        assert "1h" in out  # uptime formatting

    def test_available_with_partial_data(self):
        """When optional fields are missing, the renderer skips them
        rather than printing 'None'."""
        s = MapsServiceStatus(
            available=True, host="localhost", port=8808,
            version="0.5",
        )
        out = _format_status(s)
        assert "0.5" in out
        assert "None" not in out
        assert "Health" not in out
        assert "Sources" not in out


class TestFormatUptime:
    def test_seconds(self):
        assert _format_uptime(45) == "45s"

    def test_minutes(self):
        assert _format_uptime(180) == "3m"

    def test_hours(self):
        assert _format_uptime(3725) == "1h 2m"

    def test_days(self):
        assert _format_uptime(90061) == "1d 1h"


# ─────────────────────────────────────────────────────────────────────
# 4. Registration smoke — handler is wired into get_all_handlers()
# ─────────────────────────────────────────────────────────────────────


class TestHandlerRegistration:
    def test_registered_in_get_all_handlers(self):
        # Late import — avoid module-level handler dependency loading
        # before sys.path is set up.
        from handlers import get_all_handlers
        all_handlers = get_all_handlers()
        names = [h.__name__ for h in all_handlers]
        assert "MeshforgeMapsHandler" in names
