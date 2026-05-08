"""Tests for MeshCorePositionsHandler._pin_self.

The "Pin this radio" entry pulls pubkey + name + advertised lat/lon from
GET /radio and writes a placement. Three branches must stay green:

1. Happy path — pubkey + valid coords → placement written.
2. Pubkey set but coords missing — fall through to manual entry.
3. Daemon unreachable / pubkey missing — friendly msgbox, no placement.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(SRC / "launcher_tui"))
sys.path.insert(0, os.path.dirname(__file__))

from handler_test_utils import FakeDialog, make_handler_context  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated_position_store(tmp_path: Path, monkeypatch):
    """Per-test position store backed by a fresh ``tmp_path`` + reset singleton."""
    cfg_dir = tmp_path / ".config" / "meshanchor"
    cfg_dir.mkdir(parents=True)
    # The store's module already has its own ``get_real_user_home`` binding
    # from the top-level ``from utils.paths import get_real_user_home`` —
    # patch THAT, not the source. Same applies to ``utils.paths`` for any
    # consumer that binds late.
    monkeypatch.setattr("utils.paths.get_real_user_home", lambda: tmp_path)
    monkeypatch.setattr(
        "utils.meshcore_positions.get_real_user_home", lambda: tmp_path
    )
    from utils.meshcore_positions import reset_default_store
    reset_default_store()
    yield
    reset_default_store()


def _make_handler():
    """Build a MeshCorePositionsHandler. Position store is isolated by the fixture."""
    from handlers.meshcore_positions import MeshCorePositionsHandler
    h = MeshCorePositionsHandler()
    ctx = make_handler_context(src_dir=str(SRC))
    h.set_context(ctx)
    return h


def _radio_response(**radio_overrides):
    """Build a fake JSON body the way ``GET /radio`` returns it."""
    radio = {
        "node_name": "meshanchorRAK1",
        "public_key": "ab" * 32,
        "radio_lat": 19.435,
        "radio_lon": -155.213,
        "radio_freq_mhz": 915.0,
    }
    radio.update(radio_overrides)
    return BytesIO(json.dumps({"radio": radio}).encode("utf-8"))


def _patched_urlopen(body):
    """Build a MagicMock-driven urlopen patch context.

    ``body`` may be a BytesIO (success) or an Exception class/instance.
    """
    if isinstance(body, Exception) or (
        isinstance(body, type) and issubclass(body, Exception)
    ):
        return patch(
            "urllib.request.urlopen",
            side_effect=body if isinstance(body, Exception) else body("boom"),
        )

    cm = MagicMock()
    cm.__enter__ = lambda self: body
    cm.__exit__ = lambda self, *a: False
    return patch("urllib.request.urlopen", return_value=cm)


class TestPinSelf:
    def test_happy_path_writes_placement(self, tmp_path: Path):
        h = _make_handler()
        h.ctx.dialog._yesno_returns = [True]  # confirm pin

        with _patched_urlopen(_radio_response()):
            h._pin_self()

        from utils.meshcore_positions import get_position_store
        store = get_position_store()
        rec = store.get("ab" * 32)
        assert rec is not None
        assert rec["lat"] == pytest.approx(19.435)
        assert rec["lon"] == pytest.approx(-155.213)
        assert rec["name"] == "meshanchorRAK1"
        assert h.ctx.dialog.last_msgbox_title == "Pinned"

    def test_user_can_decline_confirmation(self, tmp_path: Path):
        h = _make_handler()
        h.ctx.dialog._yesno_returns = [False]  # decline

        with _patched_urlopen(_radio_response()):
            h._pin_self()

        from utils.meshcore_positions import get_position_store
        assert get_position_store().get("ab" * 32) is None

    def test_no_pubkey_shows_friendly_error(self, tmp_path: Path):
        h = _make_handler()

        with _patched_urlopen(_radio_response(public_key=None)):
            h._pin_self()

        assert h.ctx.dialog.last_msgbox_title == "No public key"
        from utils.meshcore_positions import get_position_store
        assert len(get_position_store().list()) == 0

    def test_coords_unset_falls_through_to_manual_prompt(self, tmp_path: Path):
        h = _make_handler()
        # User cancels at the first lat input → no placement written.
        h.ctx.dialog._inputbox_returns = [None]

        with _patched_urlopen(_radio_response(radio_lat=None, radio_lon=None)):
            h._pin_self()

        # First msgbox is the "coords not set" hint.
        msgbox_titles = [c[1][0] for c in h.ctx.dialog.calls if c[0] == "msgbox"]
        assert "Coords not set on radio" in msgbox_titles
        from utils.meshcore_positions import get_position_store
        assert len(get_position_store().list()) == 0

    def test_zero_zero_coords_treated_as_unset(self, tmp_path: Path):
        """lat=0, lon=0 is the default-zero unset state (matches collector check)."""
        h = _make_handler()
        h.ctx.dialog._inputbox_returns = [None]  # cancel manual prompt

        with _patched_urlopen(_radio_response(radio_lat=0.0, radio_lon=0.0)):
            h._pin_self()

        msgbox_titles = [c[1][0] for c in h.ctx.dialog.calls if c[0] == "msgbox"]
        assert "Coords not set on radio" in msgbox_titles

    def test_daemon_unreachable_shows_error(self, tmp_path: Path):
        h = _make_handler()

        with _patched_urlopen(urllib.error.URLError("connection refused")):
            h._pin_self()

        assert h.ctx.dialog.last_msgbox_title == "Daemon unreachable"
        from utils.meshcore_positions import get_position_store
        assert len(get_position_store().list()) == 0

    def test_main_menu_includes_self_entry(self, tmp_path: Path):
        """The ``self`` choice exists in the top-level menu so dispatch works."""
        h = _make_handler()
        # Drive the menu: pick "back" immediately, then capture choices.
        h.ctx.dialog._menu_returns = ["back"]
        h._main_menu()
        menu_call = next(c for c in h.ctx.dialog.calls if c[0] == "menu")
        choices = menu_call[1][2]
        keys = [c[0] for c in choices]
        assert "self" in keys
        # Ordered first (so the operator sees it before generic "place").
        assert keys.index("self") < keys.index("place")
