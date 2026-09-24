"""MeshCore egress reaches delivery_counters (2026-09-23).

Before this, every MeshCore send (bridge →MC, Meshtastic re-emit, DM reply,
chat) bumped only in-memory health stats, so the Delivery screen showed the
primary radio's busy leg as silence — the newest delivery event on
meshanchor-server was 12h old while the daemon transmitted all evening.
These tests run against the suite-isolated counters DB (conftest
``_isolate_delivery_counters_db``) and read back the real snapshot.
"""

import asyncio
import os
import sys
import threading
from queue import Queue
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from gateway import delivery_counters as dc
from gateway.canonical_message import CanonicalMessage
from gateway.meshcore_handler import MeshCoreHandler
from utils import tx_guard
from utils.active_health_checks_delivery import confirmation_window


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
    h = MeshCoreHandler(
        config=config, node_tracker=MagicMock(), health=MagicMock(),
        stop_event=threading.Event(), stats={'errors': 0},
        stats_lock=threading.Lock(), message_queue=Queue(maxsize=100),
    )
    return h


@pytest.fixture(autouse=True)
def fresh_counters():
    dc._reset_singleton_for_tests()
    dc.get_singleton()._reset_for_tests()
    with tx_guard.allow_meshcore_egress():  # mock radio, not RF
        yield
    dc._reset_singleton_for_tests()


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _commands(contacts=None):
    cmds = MagicMock()
    cmds.send_chan_msg = AsyncMock(return_value=None)
    cmds.send_msg = AsyncMock(return_value=None)
    cmds.get_contacts = AsyncMock(
        return_value=SimpleNamespace(payload=contacts or {}))
    return cmds


def _meshcore_events():
    return [e for e in dc.get_singleton().recent() if e.protocol == "meshcore"]


def test_channel_send_records_sent_with_message_id(handler):
    handler._connected = True
    handler._meshcore = MagicMock(commands=_commands())
    msg = CanonicalMessage(content="hi", is_broadcast=True)
    msg.metadata['channel'] = 1
    handler._send_queue.put_nowait(msg)

    _run(handler._process_outbound())

    ev = _meshcore_events()
    assert [(e.state, e.id, e.note) for e in ev] == [
        (dc.DeliveryState.SENT, msg.id, "ch1")]


def test_dm_contact_missing_records_destination_unreachable(handler):
    handler._connected = True
    handler._meshcore = MagicMock(commands=_commands())

    assert _run(handler._send_message("ping", destination="missing-id")) is False

    (ev,) = _meshcore_events()
    assert ev.state is dc.DeliveryState.DROPPED
    assert ev.drop_reason is dc.DropReason.DESTINATION_UNREACHABLE


def test_not_connected_records_drop_not_silence(handler):
    handler._connected = False
    assert _run(handler._send_message("hi")) is False
    (ev,) = _meshcore_events()
    assert ev.state is dc.DeliveryState.DROPPED
    assert ev.note == "not connected"


def test_radio_exception_records_drop(handler):
    cmds = _commands()
    cmds.send_chan_msg = AsyncMock(side_effect=OSError("serial gone"))
    handler._connected = True
    handler._meshcore = MagicMock(commands=cmds)

    assert _run(handler._send_message("hi", channel=1)) is False
    (ev,) = _meshcore_events()
    assert ev.drop_reason is dc.DropReason.NON_RETRIABLE_ERROR
    assert "serial gone" in ev.note


def test_meshcore_sends_stay_out_of_the_stall_judgement(handler):
    """Channel sends have no confirmation mechanism: they must surface as
    unconfirmable, never become a 'confirmable' protocol the stall check
    judges (that would read every channel send as an unconfirmed failure)."""
    handler._connected = True
    handler._meshcore = MagicMock(commands=_commands())
    for _ in range(5):
        assert _run(handler._send_message("hi", channel=1)) is True

    snap = dc.get_singleton().snapshot()
    assert snap["state_by_protocol"]["sent"]["meshcore"] == 5
    assert "meshcore" not in confirmation_window(snap)["confirmable"]
