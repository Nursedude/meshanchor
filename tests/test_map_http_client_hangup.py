"""A client that closes mid-response must not produce a traceback.

Ported from MeshForge's ``test_map_http_handler.py`` on 2026-08-13, together
with the guards themselves — MeshAnchor's handler never had them, and the bill
came due on meshanchor-server:

``socketserver``'s default ``handle_error`` prints a ~22-line Python traceback
for every ``BrokenPipeError``, and a client polling ``/fleet/slo`` hangs up
constantly. Measured that day: **13,133 handler broken pipes in TEN MINUTES**
(~19,800 journal lines/minute, plus ~8,000 more per 30 s suppressed by
journald's rate limiter). The box's journal is volatile — vendor drop-in
``40-rpi-volatile-storage.conf`` — so that spam rotated the ENTIRE system
journal every ~10 minutes, taking every other unit's history with it. Two of
four enrolled user timers were unjudgeable there purely because their last
firing had already scrolled out of existence.

MeshForge measured 0 handler broken pipes over the same 24h with the guards in
place. The guards were nonetheless unpinned in BOTH repos until this file: "it
works" and "it is protected" are different claims, and nothing would have
failed if someone deleted the ``try``. These plant the violation rather than
read the code.
"""
from io import BytesIO
from unittest.mock import MagicMock

import pytest

from utils.map_http_handler import MapRequestHandler


def _make_handler() -> MapRequestHandler:
    """Build a handler with just enough state to call ``_serve_json``.

    The handler's real ``__init__`` wants a live socket, so instances are
    built via ``__new__`` with the minimum surface stubbed — the same
    technique the MeshForge twin's tests use.
    """
    h = MapRequestHandler.__new__(MapRequestHandler)
    h.headers = {}
    h.wfile = BytesIO()
    h.send_response = MagicMock()
    h.end_headers = MagicMock()
    h.send_header = MagicMock()
    h._send_cors_header = MagicMock()
    return h


class TestClientHangupIsNotAnError:

    @pytest.mark.parametrize("exc", [
        BrokenPipeError, ConnectionResetError, ConnectionAbortedError,
    ])
    def test_serve_json_swallows_client_teardown(self, exc):
        h = _make_handler()
        h.wfile = MagicMock()
        h.wfile.write.side_effect = exc("client went away")
        h._serve_json({"ok": True})          # must not raise
        assert h.wfile.write.called

    def test_a_real_write_error_is_still_raised(self):
        """The guard must stay narrow. Swallowing every OSError would hide a
        genuinely broken response path behind the same silence — the
        degraded-state-reads-as-normal class this tree keeps paying for."""
        h = _make_handler()
        h.wfile = MagicMock()
        h.wfile.write.side_effect = OSError("disk on fire")
        with pytest.raises(OSError):
            h._serve_json({"ok": True})
