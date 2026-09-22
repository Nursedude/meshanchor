"""Contacts pane (roadmap 1b) — honest rendering of the radio's table.

The pane's whole reason to exist carefully is that TWO fields on every
contact row look like a receipt clock and neither is one. These tests pin
that distinction, because a future reader looking only at the field names
will reach for the wrong one — the roadmap itself did, and so did a comment
in meshcore_radio_ops_mixin.
"""

import json
import os
import sys
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src', 'launcher_tui'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.dirname(__file__))

from handler_test_utils import make_handler_context  # noqa: E402
from handlers.meshcore import MeshCoreHandler  # noqa: E402


def _row(**over):
    row = {
        "name": "meshanchor p4",
        "public_key": "7eb0fa289c11" + "0" * 52,
        "prefix": "7eb0fa289c11",
        "type": 1,
        "role": "companion",
        "last_advert": 2100000000,      # year 2036 — sender's clock, absurd
        "last_advert_iso": "2036-08-22T01:20:00",
        "lastmod": 1789336369,          # 2026-09-13 — record-modified cursor
        "lastmod_iso": "2026-09-13T11:52:49",
        "out_path_len": -1,
        "adv_lat": 0.0,
        "adv_lon": 0.0,
        "flags": 0,
    }
    row.update(over)
    return row


@pytest.fixture
def handler():
    h = MeshCoreHandler()
    h.set_context(make_handler_context())
    h.ctx.wait_for_enter = MagicMock()
    return h


class TestFieldRendering:
    """Each degraded value must be distinguishable from a healthy one."""

    @pytest.mark.parametrize("raw,expected", [
        (-1, "flood"),      # no stored route — NOT "-1 hops"
        (0, "direct"),
        (1, "1 hop"),
        (3, "3 hops"),
        (None, "?"),
    ])
    def test_path_rendering(self, handler, raw, expected):
        assert handler._contacts_path(raw) == expected

    def test_null_island_is_absence_not_a_coordinate(self, handler):
        """(0,0) is the firmware's no-position sentinel — 29 of 68 live rows.

        Rendering it as a coordinate puts a Hawaii mesh node in the Gulf of
        Guinea (the 2026-09-02 sentinel-leak class).
        """
        assert handler._contacts_position(0.0, 0.0) == "-"
        assert handler._contacts_position(None, None) == "-"
        assert handler._contacts_position(20.87, -156.47) == "20.8700,-156.4700"

    def test_age_never_renders_a_negative_duration(self, handler):
        future = datetime.now() + timedelta(hours=2)
        assert handler._contacts_age(future) == "clock skew"
        assert handler._contacts_age(None) == "?"


class TestReceiptJoin:
    """The tracker keys on advert width; the row carries exactly 12 hex."""

    def test_matches_in_both_directions(self, handler):
        receipts = {"7eb0fa289c11aabb": datetime(2026, 9, 21, 18, 0)}
        known, seen = handler._contacts_lookup(receipts, "7eb0fa289c11")
        assert known and seen is not None

        receipts = {"7eb0fa": datetime(2026, 9, 21, 18, 0)}
        known, _ = handler._contacts_lookup(receipts, "7eb0fa289c11")
        assert known

    def test_absent_prefix_does_not_match(self, handler):
        receipts = {"deadbeefcafe": datetime.now()}
        known, seen = handler._contacts_lookup(receipts, "7eb0fa289c11")
        assert not known and seen is None


class TestFetchDegradesHonestly:
    def test_unreachable_returns_error_not_empty_list(self, handler):
        """([], None) would render "the radio knows nobody" for "we could
        not ask" — honest_failure_modes #1."""
        with patch("urllib.request.urlopen", side_effect=OSError("refused")):
            rows, err = handler._contacts_fetch()
        assert rows is None
        assert "refused" in err

    def test_malformed_payload_is_an_error_not_silence(self, handler):
        resp = MagicMock()
        resp.read.return_value = json.dumps({"ts": 1}).encode()
        resp.__enter__ = lambda s: s
        resp.__exit__ = lambda *a: False
        with patch("urllib.request.urlopen", return_value=resp):
            rows, err = handler._contacts_fetch()
        assert rows is None and "contacts" in err


class TestLastHeardSource:
    """The pin that matters: last-heard comes from OUR clock, or says so."""

    def _render(self, handler, rows, receipts, capsys, declared=None):
        with patch.object(type(handler), "_contacts_fetch",
                          return_value=(rows, None)), \
             patch.object(type(handler), "_contacts_receipts",
                          return_value=receipts), \
             patch.object(type(handler), "_contacts_declared",
                          return_value=declared or []):
            handler._meshcore_contacts()
        return capsys.readouterr().out

    def test_never_borrows_the_senders_clock_or_the_sync_cursor(
            self, handler, capsys):
        """A contact the tracker has not heard reads "not since boot".

        Its row carries last_advert=2036 and lastmod=2026-09-13. If either
        ever leaks into this column, the year or that date shows up here
        and this test fails. That is the whole point of the pane.
        """
        out = self._render(handler, [_row()], {}, capsys)
        assert "not since boot" in out
        assert "2036" not in out
        assert "09-13" not in out and "2026-09-13" not in out

    def test_uses_our_receipt_when_we_have_one(self, handler, capsys):
        seen = datetime.now() - timedelta(minutes=5)
        out = self._render(handler, [_row()], {"7eb0fa289c11": seen}, capsys)
        assert "5m ago" in out

    def test_heard_but_unstamped_is_not_never(self, handler, capsys):
        """Pre-fix nodes sat in the tracker with last_seen=None (14 of 22
        live). "never" would be a lie and a blank reads as one."""
        out = self._render(handler, [_row()], {"7eb0fa289c11": None}, capsys)
        assert "heard, no ts" in out
        assert "not since boot" not in out

    def test_unreadable_tracker_is_unknown_not_never(self, handler, capsys):
        out = self._render(handler, [_row()], None, capsys)
        assert "unknown" in out
        assert "not since boot" not in out


class TestDeclaredOwnership:
    def test_declared_absence_is_a_row_not_silence(self, handler, capsys):
        rows = [_row()]
        with patch.object(type(handler), "_contacts_fetch",
                          return_value=(rows, None)), \
             patch.object(type(handler), "_contacts_receipts",
                          return_value={}), \
             patch.object(type(handler), "_contacts_declared",
                          return_value=["meshanchor p4", "meshanchor p2"]):
            handler._meshcore_contacts()
        out = capsys.readouterr().out
        assert "ABSENT" in out
        assert "meshanchor p2" in out

    def test_undeclared_says_so_rather_than_claiming_none_are_ours(
            self, handler, capsys):
        with patch.object(type(handler), "_contacts_fetch",
                          return_value=([_row()], None)), \
             patch.object(type(handler), "_contacts_receipts",
                          return_value={}), \
             patch.object(type(handler), "_contacts_declared",
                          return_value=[]):
            handler._meshcore_contacts()
        assert "No contacts declared as ours" in capsys.readouterr().out

    def test_matches_on_name_or_pubkey_prefix(self, handler):
        row = _row()
        assert handler._contacts_is_ours(["meshanchor p4"], row)
        assert handler._contacts_is_ours(["7eb0fa"], row)
        assert not handler._contacts_is_ours(["somebody else"], row)
