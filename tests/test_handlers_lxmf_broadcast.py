"""Tests for LXMFBroadcastHandler — TUI surface for the LXMF bridge.

The handler is HTTP-only: it never touches the SubscriberStore directly.
All tests stub `urllib.request.urlopen` to canned responses and assert
the dialog calls + status code handling.
"""

from __future__ import annotations

import io
import json
import os
import sys
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(SRC / "launcher_tui"))
sys.path.insert(0, os.path.dirname(__file__))

from handler_test_utils import FakeDialog, make_handler_context  # noqa: E402


def _make_handler():
    from handlers.lxmf_broadcast import LXMFBroadcastHandler

    h = LXMFBroadcastHandler()
    ctx = make_handler_context(src_dir=str(SRC))
    h.set_context(ctx)
    return h


class _FakeResponse:
    """urllib.request.urlopen() context-manager stub."""

    def __init__(self, payload):
        self._payload = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _http_error(code: int, payload):
    body = json.dumps(payload).encode("utf-8")
    err = urllib.error.HTTPError(
        url="http://test/", code=code, msg="err", hdrs=None, fp=io.BytesIO(body),
    )
    err.read = lambda: body
    return err


# ── _show_status ─────────────────────────────────────────────────────


class TestShowStatus:
    def test_renders_full_status(self):
        h = _make_handler()
        payload = {
            "running": True,
            "destination_hash": "abc123def4567890",
            "display_name": "Test Bridge",
            "channels": [0, 1],
            "announce_interval_sec": 180,
            "autosubscribe": False,
            "started_at_iso": "2026-05-12T00:00:00",
            "subscribers": [
                {"lxmf_hash": "deadbeef00112233",
                 "added_at": "2026-05-12T00:01:00",
                 "last_delivery": None,
                 "tier": "external", "state": "healthy",
                 "consecutive_failures": 0,
                 "last_failure_at": None},
            ],
            "stats": {"fanouts": 3, "errors": 0},
        }
        with patch("urllib.request.urlopen", return_value=_FakeResponse(payload)):
            h._show_status()

        assert h.ctx.dialog.last_msgbox_title == "LXMF Broadcast Bridge — Status"
        body = h.ctx.dialog.last_msgbox_text
        assert "abc123def4567890" in body
        assert "[0, 1]" in body
        assert "deadbeef00112233" in body
        assert "fanouts:                3" in body

    def test_renders_s1_tier_state_columns(self):
        """S1: per-subscriber rows expose tier / state / failure counter."""
        h = _make_handler()
        payload = {
            "running": True,
            "destination_hash": "abc123def4567890",
            "display_name": "B",
            "channels": [0],
            "announce_interval_sec": 60,
            "autosubscribe": False,
            "started_at_iso": "2026-05-12T00:00:00",
            "subscribers": [
                {"lxmf_hash": "aaaa000011112222",
                 "added_at": "2026-05-12T00:01:00",
                 "last_delivery": "2026-05-12T00:05:00",
                 "tier": "local", "state": "healthy",
                 "consecutive_failures": 0,
                 "last_failure_at": None},
                {"lxmf_hash": "bbbb000011112222",
                 "added_at": "2026-05-12T00:01:00",
                 "last_delivery": None,
                 "tier": "external", "state": "healthy",
                 "consecutive_failures": 7,
                 "last_failure_at": "2026-05-12T00:08:00"},
            ],
            "stats": {"fanouts": 0, "errors": 0},
        }
        with patch("urllib.request.urlopen", return_value=_FakeResponse(payload)):
            h._show_status()
        body = h.ctx.dialog.last_msgbox_text
        # First subscriber: local, healthy, no failures
        assert "tier=local" in body
        assert "state=healthy" in body
        # Second: external, 7 failures
        assert "tier=external" in body
        assert "fails=7" in body
        assert "2026-05-12T00:08:00" in body  # last_fail surfaced

    def test_503_renders_unreachable_message(self):
        h = _make_handler()
        with patch(
            "urllib.request.urlopen",
            side_effect=_http_error(503, {"error": "bridge not active"}),
        ):
            h._show_status()
        body = h.ctx.dialog.last_msgbox_text
        assert "503" in body or "not active" in body.lower()

    def test_no_daemon_renders_unreachable(self):
        h = _make_handler()
        with patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.URLError("Connection refused"),
        ):
            h._show_status()
        body = h.ctx.dialog.last_msgbox_text
        assert "daemon" in body.lower()


# ── _subscribe_local ─────────────────────────────────────────────────


class TestSubscribeLocal:
    def test_no_clients_shows_actionable_message(self):
        h = _make_handler()
        from handlers._lxmf_clients_discovery import LocalLXMFClient  # noqa: F401
        with patch(
            "handlers.lxmf_broadcast.discover_local_lxmf_clients",
            return_value=[],
        ):
            h._subscribe_local()
        assert h.ctx.dialog.last_msgbox_title == "No Local LXMF Clients Found"
        assert "local_lxmf_clients.json" in h.ctx.dialog.last_msgbox_text

    def test_happy_path_posts_to_daemon(self):
        from handlers._lxmf_clients_discovery import LocalLXMFClient

        h = _make_handler()
        clients = [
            LocalLXMFClient(
                name="NomadNet", hash="deadbeef00112233", source="/tmp/log",
            ),
        ]
        h.ctx.dialog._menu_returns = ["deadbeef00112233"]

        post_response = _FakeResponse(
            {"added": True, "lxmf_hash": "deadbeef00112233", "total": 1}
        )
        with patch(
            "handlers.lxmf_broadcast.discover_local_lxmf_clients",
            return_value=clients,
        ), patch("urllib.request.urlopen", return_value=post_response) as urlopen:
            h._subscribe_local()

        assert urlopen.called
        # Confirm POST body
        req = urlopen.call_args.args[0]
        assert req.get_method() == "POST"
        body = json.loads(req.data.decode("utf-8"))
        assert body == {"lxmf_hash": "deadbeef00112233"}

        assert h.ctx.dialog.last_msgbox_title == "Subscription Updated"
        assert "Added NomadNet" in h.ctx.dialog.last_msgbox_text

    def test_idempotent_already_subscribed(self):
        from handlers._lxmf_clients_discovery import LocalLXMFClient

        h = _make_handler()
        clients = [
            LocalLXMFClient(
                name="NomadNet", hash="deadbeef00112233", source="/tmp/log",
            ),
        ]
        h.ctx.dialog._menu_returns = ["deadbeef00112233"]
        post_response = _FakeResponse(
            {"added": False, "lxmf_hash": "deadbeef00112233", "total": 3}
        )
        with patch(
            "handlers.lxmf_broadcast.discover_local_lxmf_clients",
            return_value=clients,
        ), patch("urllib.request.urlopen", return_value=post_response):
            h._subscribe_local()

        assert "already subscribed" in h.ctx.dialog.last_msgbox_text
        assert "Total subscribers: 3" in h.ctx.dialog.last_msgbox_text

    def test_back_in_picker_is_noop(self):
        from handlers._lxmf_clients_discovery import LocalLXMFClient

        h = _make_handler()
        clients = [
            LocalLXMFClient(
                name="NomadNet", hash="deadbeef00112233", source="/tmp/log",
            ),
        ]
        h.ctx.dialog._menu_returns = ["back"]
        with patch(
            "handlers.lxmf_broadcast.discover_local_lxmf_clients",
            return_value=clients,
        ), patch("urllib.request.urlopen") as urlopen:
            h._subscribe_local()
        urlopen.assert_not_called()

    def test_post_failure_surfaces_error(self):
        from handlers._lxmf_clients_discovery import LocalLXMFClient

        h = _make_handler()
        clients = [
            LocalLXMFClient(
                name="NomadNet", hash="deadbeef00112233", source="/tmp/log",
            ),
        ]
        h.ctx.dialog._menu_returns = ["deadbeef00112233"]
        with patch(
            "handlers.lxmf_broadcast.discover_local_lxmf_clients",
            return_value=clients,
        ), patch(
            "urllib.request.urlopen",
            side_effect=_http_error(503, {"error": "bridge not active"}),
        ):
            h._subscribe_local()
        assert h.ctx.dialog.last_msgbox_title == "Subscribe Failed"


# ── _stale_subscribers ───────────────────────────────────────────────


def _status_payload(subscribers):
    return {
        "running": True,
        "destination_hash": "abc123def4567890",
        "display_name": "B",
        "channels": [0],
        "announce_interval_sec": 60,
        "autosubscribe": False,
        "started_at_iso": "2026-05-12T00:00:00",
        "subscribers": subscribers,
        "stats": {"fanouts": 0, "errors": 0},
    }


class TestStaleSubscribers:
    def test_no_stale_shows_actionable_message(self):
        h = _make_handler()
        payload = _status_payload([
            {"lxmf_hash": "deadbeef00112233", "tier": "external",
             "state": "healthy", "consecutive_failures": 0,
             "added_at": "x", "last_delivery": "y", "last_failure_at": None},
        ])
        with patch("urllib.request.urlopen", return_value=_FakeResponse(payload)):
            h._stale_subscribers()
        assert h.ctx.dialog.last_msgbox_title == "No Stale Subscribers"

    def test_lists_stale_and_dead_only(self):
        h = _make_handler()
        rows = [
            {"lxmf_hash": "aaaa000000000001", "tier": "local",
             "state": "healthy", "consecutive_failures": 0,
             "added_at": "x", "last_delivery": "y", "last_failure_at": None},
            {"lxmf_hash": "bbbb000000000002", "tier": "external",
             "state": "stale", "consecutive_failures": 8,
             "added_at": "x", "last_delivery": "y", "last_failure_at": "z"},
            {"lxmf_hash": "cccc000000000003", "tier": "external",
             "state": "dead", "consecutive_failures": 30,
             "added_at": "x", "last_delivery": None, "last_failure_at": "z"},
            {"lxmf_hash": "dddd000000000004", "tier": "local",
             "state": "degraded", "consecutive_failures": 3,
             "added_at": "x", "last_delivery": "y", "last_failure_at": "z"},
        ]
        h.ctx.dialog._menu_returns = ["back"]  # don't actually prune
        with patch(
            "urllib.request.urlopen", return_value=_FakeResponse(_status_payload(rows)),
        ):
            h._stale_subscribers()
        # Verify exactly the stale+dead rows were offered (and a back).
        menu_call = next(c for c in h.ctx.dialog.calls if c[0] == "menu")
        offered = menu_call[1][2]
        offered_hashes = {tag for tag, _desc in offered}
        assert offered_hashes == {
            "bbbb000000000002", "cccc000000000003", "back",
        }

    def test_prune_happy_path_calls_delete(self):
        h = _make_handler()
        rows = [
            {"lxmf_hash": "bbbb000000000002", "tier": "external",
             "state": "dead", "consecutive_failures": 30,
             "added_at": "x", "last_delivery": None, "last_failure_at": "z"},
        ]
        h.ctx.dialog._menu_returns = ["bbbb000000000002"]
        h.ctx.dialog._yesno_returns = [True]

        get_resp = _FakeResponse(_status_payload(rows))
        delete_resp = _FakeResponse(
            {"removed": True, "lxmf_hash": "bbbb000000000002", "total": 0}
        )

        with patch(
            "urllib.request.urlopen",
            side_effect=[get_resp, delete_resp],
        ) as urlopen:
            h._stale_subscribers()

        delete_req = urlopen.call_args_list[1].args[0]
        assert delete_req.get_method() == "DELETE"
        assert "bbbb000000000002" in delete_req.full_url
        assert h.ctx.dialog.last_msgbox_title == "Subscriber Pruned"
        assert "Remaining subscribers: 0" in h.ctx.dialog.last_msgbox_text

    def test_prune_double_confirm_no_cancels(self):
        h = _make_handler()
        rows = [
            {"lxmf_hash": "bbbb000000000002", "tier": "external",
             "state": "dead", "consecutive_failures": 30,
             "added_at": "x", "last_delivery": None, "last_failure_at": "z"},
        ]
        h.ctx.dialog._menu_returns = ["bbbb000000000002"]
        h.ctx.dialog._yesno_returns = [False]  # operator cancels

        with patch(
            "urllib.request.urlopen",
            return_value=_FakeResponse(_status_payload(rows)),
        ) as urlopen:
            h._stale_subscribers()

        # Only the GET fired — no DELETE.
        assert urlopen.call_count == 1


class TestStaleSubscribersInMenu:
    def test_stale_choice_in_top_menu(self):
        from handlers.lxmf_broadcast import LXMFBroadcastHandler

        h = _make_handler()
        h.ctx.dialog._menu_returns = ["back"]
        h._bridge_menu()
        menu_call = next(c for c in h.ctx.dialog.calls if c[0] == "menu")
        offered = menu_call[1][2]
        tags = {tag for tag, _desc in offered}
        assert "stale" in tags


# ── menu_items contract ──────────────────────────────────────────────


class TestMenuItems:
    def test_single_item_under_meshcore_section(self):
        from handlers.lxmf_broadcast import LXMFBroadcastHandler

        items = LXMFBroadcastHandler().menu_items()
        assert len(items) == 1
        action, label, feature = items[0]
        assert action == "lxmf_broadcast"
        assert feature == "meshcore"
        assert "LXMF" in label
