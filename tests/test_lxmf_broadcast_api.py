"""Tests for utils.lxmf_broadcast_api — daemon HTTP handlers.

Mirrors the test shape of test_radio_api / test_handlers_meshcore_cli:
stub the bridge accessor, drive `handle_get` / `handle_post` against a
fake handler that records `_send_json` / `_send_error_json` calls, and
pin the contract.

The bridge actuates fan-out, so subscribe is localhost-only — but the
caller (`config_api.do_POST`) gates that. The API module under test
trusts the gate and just validates the body / writes through.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, Optional
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from utils import lxmf_broadcast_api


class _FakeHandler:
    """Records `_send_json` / `_send_error_json` / `_read_body` calls."""

    def __init__(self, *, path: str, body: Optional[Any] = None):
        self.path = path
        self._body = body
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

    def _read_body(self) -> Any:
        return self._body


def _make_bridge_with_status(status: Dict[str, Any]) -> MagicMock:
    """Build a stub bridge whose get_status() returns `status` and whose
    `_subs.add` / `_subs.list_all` mimic SubscriberStore."""
    bridge = MagicMock()
    bridge.get_status.return_value = status

    fake_store = MagicMock()
    fake_store.add.return_value = True
    fake_store.list_all.return_value = [
        MagicMock(lxmf_hash=row["lxmf_hash"])
        for row in status.get("subscribers", [])
    ]
    bridge._subs = fake_store
    return bridge


# ── handle_get ────────────────────────────────────────────────────────


class TestHandleGet:
    def test_status_returns_full_payload(self):
        bridge = _make_bridge_with_status({
            "running": True,
            "destination_hash": "abc123def4567890",
            "channels": [0],
            "subscribers": [
                {"lxmf_hash": "deadbeef00112233",
                 "added_at": "2026-05-12T00:00:00",
                 "last_delivery": None}
            ],
            "stats": {"fanouts": 5},
        })
        handler = _FakeHandler(path="/lxmf-broadcast/status")

        with patch(
            "gateway.lxmf_broadcast_bridge.get_active_broadcast_bridge",
            return_value=bridge,
        ):
            lxmf_broadcast_api.handle_get(handler)

        assert handler.sent_status == 200
        assert handler.sent_json["destination_hash"] == "abc123def4567890"
        assert handler.sent_json["stats"]["fanouts"] == 5

    def test_subscribers_returns_count_and_rows(self):
        rows = [
            {"lxmf_hash": "deadbeef00112233", "added_at": "x", "last_delivery": None},
            {"lxmf_hash": "cafebabe11223344", "added_at": "y", "last_delivery": "z"},
        ]
        bridge = _make_bridge_with_status({"subscribers": rows})
        handler = _FakeHandler(path="/lxmf-broadcast/subscribers")

        with patch(
            "gateway.lxmf_broadcast_bridge.get_active_broadcast_bridge",
            return_value=bridge,
        ):
            lxmf_broadcast_api.handle_get(handler)

        assert handler.sent_status == 200
        assert handler.sent_json == {"count": 2, "subscribers": rows}

    def test_503_when_bridge_inactive(self):
        handler = _FakeHandler(path="/lxmf-broadcast/status")
        with patch(
            "gateway.lxmf_broadcast_bridge.get_active_broadcast_bridge",
            return_value=None,
        ):
            lxmf_broadcast_api.handle_get(handler)
        assert handler.sent_error_status == 503
        assert "not active" in handler.sent_error

    def test_unknown_path_returns_404(self):
        bridge = _make_bridge_with_status({})
        handler = _FakeHandler(path="/lxmf-broadcast/garbage")
        with patch(
            "gateway.lxmf_broadcast_bridge.get_active_broadcast_bridge",
            return_value=bridge,
        ):
            lxmf_broadcast_api.handle_get(handler)
        assert handler.sent_error_status == 404


# ── handle_post ───────────────────────────────────────────────────────


class TestHandlePost:
    def test_subscribe_happy_path(self):
        bridge = _make_bridge_with_status({"subscribers": []})
        handler = _FakeHandler(
            path="/lxmf-broadcast/subscribers",
            body={"lxmf_hash": "DeadBeef00112233"},
        )
        with patch(
            "gateway.lxmf_broadcast_bridge.get_active_broadcast_bridge",
            return_value=bridge,
        ):
            lxmf_broadcast_api.handle_post(handler)

        assert handler.sent_status == 200
        assert handler.sent_json["added"] is True
        # Hash normalised to lowercase
        assert handler.sent_json["lxmf_hash"] == "deadbeef00112233"
        bridge._subs.add.assert_called_once_with("deadbeef00112233")

    def test_subscribe_idempotent_returns_added_false(self):
        bridge = _make_bridge_with_status({"subscribers": []})
        bridge._subs.add.return_value = False
        handler = _FakeHandler(
            path="/lxmf-broadcast/subscribers",
            body={"lxmf_hash": "deadbeef00112233"},
        )
        with patch(
            "gateway.lxmf_broadcast_bridge.get_active_broadcast_bridge",
            return_value=bridge,
        ):
            lxmf_broadcast_api.handle_post(handler)
        assert handler.sent_status == 200
        assert handler.sent_json["added"] is False

    def test_rejects_missing_hash(self):
        bridge = _make_bridge_with_status({})
        handler = _FakeHandler(
            path="/lxmf-broadcast/subscribers", body={"wrong_key": "x"}
        )
        with patch(
            "gateway.lxmf_broadcast_bridge.get_active_broadcast_bridge",
            return_value=bridge,
        ):
            lxmf_broadcast_api.handle_post(handler)
        assert handler.sent_error_status == 400
        bridge._subs.add.assert_not_called()

    def test_rejects_non_hex_hash(self):
        bridge = _make_bridge_with_status({})
        handler = _FakeHandler(
            path="/lxmf-broadcast/subscribers",
            body={"lxmf_hash": "nothex!!nothex!!"},
        )
        with patch(
            "gateway.lxmf_broadcast_bridge.get_active_broadcast_bridge",
            return_value=bridge,
        ):
            lxmf_broadcast_api.handle_post(handler)
        assert handler.sent_error_status == 400
        bridge._subs.add.assert_not_called()

    def test_rejects_wrong_length_hash(self):
        bridge = _make_bridge_with_status({})
        handler = _FakeHandler(
            path="/lxmf-broadcast/subscribers",
            body={"lxmf_hash": "abcd"},  # 4 chars — too short
        )
        with patch(
            "gateway.lxmf_broadcast_bridge.get_active_broadcast_bridge",
            return_value=bridge,
        ):
            lxmf_broadcast_api.handle_post(handler)
        assert handler.sent_error_status == 400

    def test_rejects_non_object_body(self):
        bridge = _make_bridge_with_status({})
        handler = _FakeHandler(
            path="/lxmf-broadcast/subscribers", body="not a dict"
        )
        with patch(
            "gateway.lxmf_broadcast_bridge.get_active_broadcast_bridge",
            return_value=bridge,
        ):
            lxmf_broadcast_api.handle_post(handler)
        assert handler.sent_error_status == 400

    def test_503_when_bridge_inactive(self):
        handler = _FakeHandler(
            path="/lxmf-broadcast/subscribers",
            body={"lxmf_hash": "deadbeef00112233"},
        )
        with patch(
            "gateway.lxmf_broadcast_bridge.get_active_broadcast_bridge",
            return_value=None,
        ):
            lxmf_broadcast_api.handle_post(handler)
        assert handler.sent_error_status == 503

    def test_unknown_path_returns_404(self):
        bridge = _make_bridge_with_status({})
        handler = _FakeHandler(
            path="/lxmf-broadcast/somethingelse",
            body={"lxmf_hash": "deadbeef00112233"},
        )
        with patch(
            "gateway.lxmf_broadcast_bridge.get_active_broadcast_bridge",
            return_value=bridge,
        ):
            lxmf_broadcast_api.handle_post(handler)
        assert handler.sent_error_status == 404


# ── handle_delete ─────────────────────────────────────────────────────


class TestHandleDelete:
    def test_remove_happy_path(self):
        bridge = _make_bridge_with_status({"subscribers": []})
        bridge._subs.remove = MagicMock(return_value=True)
        handler = _FakeHandler(
            path="/lxmf-broadcast/subscribers/DeadBeef00112233"
        )
        with patch(
            "gateway.lxmf_broadcast_bridge.get_active_broadcast_bridge",
            return_value=bridge,
        ):
            lxmf_broadcast_api.handle_delete(handler)
        assert handler.sent_status == 200
        assert handler.sent_json["removed"] is True
        assert handler.sent_json["lxmf_hash"] == "deadbeef00112233"
        bridge._subs.remove.assert_called_once_with("deadbeef00112233")

    def test_remove_idempotent_returns_removed_false(self):
        bridge = _make_bridge_with_status({"subscribers": []})
        bridge._subs.remove = MagicMock(return_value=False)
        handler = _FakeHandler(
            path="/lxmf-broadcast/subscribers/deadbeef00112233"
        )
        with patch(
            "gateway.lxmf_broadcast_bridge.get_active_broadcast_bridge",
            return_value=bridge,
        ):
            lxmf_broadcast_api.handle_delete(handler)
        assert handler.sent_status == 200
        assert handler.sent_json["removed"] is False

    def test_rejects_invalid_hash_in_url(self):
        bridge = _make_bridge_with_status({})
        bridge._subs.remove = MagicMock()
        handler = _FakeHandler(
            path="/lxmf-broadcast/subscribers/nothex!!!!"
        )
        with patch(
            "gateway.lxmf_broadcast_bridge.get_active_broadcast_bridge",
            return_value=bridge,
        ):
            lxmf_broadcast_api.handle_delete(handler)
        assert handler.sent_error_status == 400
        bridge._subs.remove.assert_not_called()

    def test_rejects_short_hash_in_url(self):
        bridge = _make_bridge_with_status({})
        bridge._subs.remove = MagicMock()
        handler = _FakeHandler(
            path="/lxmf-broadcast/subscribers/abcd"
        )
        with patch(
            "gateway.lxmf_broadcast_bridge.get_active_broadcast_bridge",
            return_value=bridge,
        ):
            lxmf_broadcast_api.handle_delete(handler)
        assert handler.sent_error_status == 400

    def test_unknown_path_prefix_returns_404(self):
        bridge = _make_bridge_with_status({})
        handler = _FakeHandler(
            path="/lxmf-broadcast/notsubscribers/deadbeef00112233"
        )
        with patch(
            "gateway.lxmf_broadcast_bridge.get_active_broadcast_bridge",
            return_value=bridge,
        ):
            lxmf_broadcast_api.handle_delete(handler)
        assert handler.sent_error_status == 404

    def test_503_when_bridge_inactive(self):
        handler = _FakeHandler(
            path="/lxmf-broadcast/subscribers/deadbeef00112233"
        )
        with patch(
            "gateway.lxmf_broadcast_bridge.get_active_broadcast_bridge",
            return_value=None,
        ):
            lxmf_broadcast_api.handle_delete(handler)
        assert handler.sent_error_status == 503
