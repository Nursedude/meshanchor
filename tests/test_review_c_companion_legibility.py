"""Review C (third-party, 2026-09-23) on e40f66cd's companion_error():
(a) a device ERR frame is dispatched by meshcore_py 2.3.14 reader.py as
    Event(ERROR, {"error_code": n, "code_string": "ERR_CODE_..."}) — no
    "reason" key — so the drop note read "companion error", losing the code;
(b) a companion-refused DM carrying a reply_ctx emitted no negative notice,
    while the contact-not-found branch beside it does — the addressed reply
    vanished from the originating mesh's view.
"""
import asyncio
import os
import sys
import threading
from queue import Queue
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from gateway import delivery_counters as dc  # noqa: E402
from gateway.meshcore_dm_reply import companion_error  # noqa: E402
from gateway.meshcore_handler import MeshCoreHandler  # noqa: E402
from utils import tx_guard  # noqa: E402


class _Evt:
    def __init__(self, typ, payload):
        self.type = typ
        self.payload = payload

    def is_error(self):
        return self.type == "command_error"


@pytest.fixture
def handler():
    config = SimpleNamespace(
        meshcore=SimpleNamespace(
            enabled=True, device_path='/dev/ttyUSB1', baud_rate=115200,
            connection_type='serial', tcp_host='localhost', tcp_port=4000,
            auto_fetch_messages=True, bridge_channels=True, bridge_dms=True,
            simulation_mode=True, channel_poll_interval_sec=5),
        meshtastic=SimpleNamespace(host='localhost', port=4403),
    )
    return MeshCoreHandler(
        config=config, node_tracker=MagicMock(), health=MagicMock(),
        stop_event=threading.Event(), stats={'errors': 0},
        stats_lock=threading.Lock(), message_queue=Queue(maxsize=100),
    )


@pytest.fixture(autouse=True)
def fresh_counters():
    dc._reset_singleton_for_tests()
    dc.get_singleton()._reset_for_tests()
    with tx_guard.allow_meshcore_egress():
        yield
    dc._reset_singleton_for_tests()


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def test_device_err_frame_keeps_its_code_string():
    assert companion_error(_Evt("command_error",
                                {"error_code": 5, "code_string": "ERR_CODE_FILE_IO_ERROR"})
                           ) == "ERR_CODE_FILE_IO_ERROR"


def test_companion_refused_dm_with_reply_ctx_emits_a_negative_notice(handler):
    contact = {"public_key": b"\xab\xcd", "adv_name": "p3"}
    cmds = MagicMock()
    cmds.get_contacts = AsyncMock(return_value=SimpleNamespace(payload=[contact]))
    cmds.send_msg = AsyncMock(return_value=_Evt("command_error", {"reason": "timeout"}))
    handler._connected = True
    handler._meshcore = MagicMock(commands=cmds)
    handler._emit_dm_notice = MagicMock()
    ctx = {"origin": "rns", "reply_to": "deadbeef"}
    assert _run(handler._send_message("hi", destination="abcd", reply_ctx=ctx)) is False
    handler._emit_dm_notice.assert_called_once()
    text, got_ctx = handler._emit_dm_notice.call_args[0]
    assert got_ctx is ctx and "timeout" in text and "not delivered" in text
    (ev,) = [e for e in dc.get_singleton().recent() if e.protocol == "meshcore"]
    assert ev.state is dc.DeliveryState.DROPPED
