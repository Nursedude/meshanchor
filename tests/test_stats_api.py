"""Tests for utils.stats_api — daemon /api/stats HTTP handler.

Mirrors the test shape of test_lxmf_broadcast_api: stub the bridge
accessor, drive `handle_get` against a fake handler that records
`_send_json` / `_send_error_json`, and pin the contract.

Key behaviours pinned:
- 503 when bridge inactive (the operator can poll during startup)
- 200 + JSON shape when bridge active
- Localhost gate enforced (returns 403 from remote IP)
- Privacy-class drop counters (Issue #35 / #37) appear in the payload
- start_time datetime is serialized as ISO + uptime_seconds derived
"""

from __future__ import annotations

import sys
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Optional
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from utils import stats_api


class _FakeHandler:
    """Records `_send_json` / `_send_error_json` calls. Mimics the
    minimal `ConfigAPIHandler` surface that stats_api.handle_get uses."""

    def __init__(self, *, path: str = "/api/stats", localhost: bool = True):
        self.path = path
        self._localhost = localhost
        self.sent_json: Optional[Any] = None
        self.sent_status: Optional[int] = None
        self.sent_error: Optional[str] = None
        self.sent_error_status: Optional[int] = None

    def _send_json(self, data: Any, status: int = 200) -> None:
        self.sent_json = data
        self.sent_status = status

    def _send_error_json(self, status: int, error: str) -> None:
        self.sent_error_status = status
        self.sent_error = error

    def _check_localhost(self) -> bool:
        return self._localhost


def _make_bridge(*, stats: Dict[str, Any], running: bool = True,
                 mesh_connected: bool = False,
                 rns_connected: bool = False,
                 meshcore_connected: bool = False) -> MagicMock:
    """Build a stub bridge whose .stats dict matches `stats`, with a
    stats lock and the connection booleans the handler reads."""
    bridge = MagicMock()
    bridge.stats = dict(stats)
    bridge._stats_lock = threading.Lock()
    bridge._running = running
    bridge._connected_rns = rns_connected
    bridge._mesh_handler = MagicMock(is_connected=mesh_connected)
    bridge._meshcore_handler = MagicMock(is_connected=meshcore_connected)
    return bridge


class TestHandleGetLocalhost:
    def test_remote_ip_returns_403(self):
        handler = _FakeHandler(localhost=False)
        stats_api.handle_get(handler)
        assert handler.sent_error_status == 403
        assert "localhost" in (handler.sent_error or "").lower()

    def test_remote_ip_does_not_touch_bridge(self):
        """Localhost gate must short-circuit BEFORE the bridge accessor
        runs. Stub the accessor to raise if called."""
        handler = _FakeHandler(localhost=False)
        with patch(
            "gateway.gateway_cli.get_active_bridge",
            side_effect=AssertionError("accessor must not run from remote IP"),
        ):
            stats_api.handle_get(handler)
        assert handler.sent_error_status == 403


class TestHandleGetBridgeNotActive:
    def test_no_bridge_returns_503(self):
        handler = _FakeHandler()
        with patch(
            "gateway.gateway_cli.get_active_bridge",
            return_value=None,
        ):
            stats_api.handle_get(handler)
        assert handler.sent_error_status == 503
        assert "not active" in (handler.sent_error or "").lower()


class TestHandleGetSuccessShape:
    def test_returns_running_state_and_stats_dict(self):
        bridge = _make_bridge(
            stats={"messages_mesh_to_rns": 7, "errors": 0},
            running=True,
            mesh_connected=True,
            rns_connected=True,
        )
        handler = _FakeHandler()
        with patch(
            "gateway.gateway_cli.get_active_bridge",
            return_value=bridge,
        ):
            stats_api.handle_get(handler)
        assert handler.sent_status == 200
        body = handler.sent_json
        assert body["running"] is True
        assert body["meshtastic_connected"] is True
        assert body["rns_connected"] is True
        assert body["meshcore_connected"] is False  # default in fixture
        assert body["stats"]["messages_mesh_to_rns"] == 7
        assert body["stats"]["errors"] == 0

    def test_start_time_serialized_as_iso_with_uptime(self):
        """start_time is a datetime; handler must drop it from the
        nested stats dict and surface it as ISO + uptime_seconds."""
        st = datetime.now() - timedelta(seconds=42)
        bridge = _make_bridge(stats={"start_time": st, "errors": 0})
        handler = _FakeHandler()
        with patch(
            "gateway.gateway_cli.get_active_bridge",
            return_value=bridge,
        ):
            stats_api.handle_get(handler)
        body = handler.sent_json
        # ISO string at top level
        assert body["start_time"] is not None
        assert isinstance(body["start_time"], str)
        assert "T" in body["start_time"]  # ISO 8601
        # Uptime is ~42 seconds (allow small clock drift)
        assert 40 < body["uptime_seconds"] < 60
        # start_time MUST NOT remain in stats sub-dict (JSON safety)
        assert "start_time" not in body["stats"]

    def test_missing_start_time_is_null(self):
        """Before bridge.start() runs, stats['start_time'] is None.
        Handler must serialize null rather than raise."""
        bridge = _make_bridge(stats={"start_time": None, "errors": 0})
        handler = _FakeHandler()
        with patch(
            "gateway.gateway_cli.get_active_bridge",
            return_value=bridge,
        ):
            stats_api.handle_get(handler)
        body = handler.sent_json
        assert body["start_time"] is None
        assert body["uptime_seconds"] is None


class TestHandleGetPrivacyCounters:
    """Issues #35 + #37 — privacy-class drop counters must surface
    on /api/stats so operators can monitor without parsing journalctl."""

    def test_issue_37_drop_counter_visible(self):
        """meshcore_bridge_default_channel_drop (Issue #37) appears in
        the stats dict when the bridge has refused channel-0 broadcasts."""
        bridge = _make_bridge(
            stats={"meshcore_bridge_default_channel_drop": 3, "errors": 0}
        )
        handler = _FakeHandler()
        with patch(
            "gateway.gateway_cli.get_active_bridge",
            return_value=bridge,
        ):
            stats_api.handle_get(handler)
        assert handler.sent_json["stats"]["meshcore_bridge_default_channel_drop"] == 3

    def test_issue_35_drop_counter_visible(self):
        """meshcore_dm_dropped_contact_not_found (Issue #35) appears
        in the stats dict — same dict, handler shares it."""
        bridge = _make_bridge(
            stats={"meshcore_dm_dropped_contact_not_found": 5, "errors": 0}
        )
        handler = _FakeHandler()
        with patch(
            "gateway.gateway_cli.get_active_bridge",
            return_value=bridge,
        ):
            stats_api.handle_get(handler)
        assert handler.sent_json["stats"]["meshcore_dm_dropped_contact_not_found"] == 5


class TestHandleGetExceptionPath:
    def test_stats_read_exception_returns_500(self):
        """If the stats lock or dict copy raises (e.g. during a tear-
        down race), the handler returns 500 with the error text."""
        bridge = MagicMock()
        bridge._stats_lock = threading.Lock()

        # bridge.stats access raises on the dict() copy attempt
        def _raise(self):
            raise RuntimeError("torn down")

        type(bridge).stats = property(_raise)

        handler = _FakeHandler()
        with patch(
            "gateway.gateway_cli.get_active_bridge",
            return_value=bridge,
        ):
            stats_api.handle_get(handler)
        assert handler.sent_error_status == 500
        assert "stats read failed" in (handler.sent_error or "").lower()
