"""A requeued MeshCore channel row replays on the slot the bridge RESOLVED,
or not at all — never on a slot read from metadata or defaulted to 0.

Found 2026-10-24 by the contextless review of the BIC-2 slice-2b revert
(finding 4), then measured in code before writing a test:

* ``_resolve_bridge_target_channel`` is config-only (Issue #37 final
  resolution, 2026-05-20): ``metadata['channel']`` is the SOURCE protocol's
  index and has no mapping to a MeshCore slot.
* Meshtastic ingress always sets it (``meshtastic_handler.py`` and
  ``mqtt_bridge_handler.py``: ``packet.get('channel', 0)``) — so a primary-
  channel broadcast carries 0.
* The failed-send requeue in ``_process_bridge_to_meshcore`` passed no
  ``channel_override``, so ``_requeue_failed_message`` lifted that source
  index into the row, and the worker replayed it on MeshCore slot 0
  (Public). An RNS message carries no channel at all, so its row reached
  the worker's own fallback and ``_resolve_channel``'s default: slot 0.

Two chokepoints close the class: enqueue refuses a MeshCore row with no
resolved slot, and the worker refuses a queued channel row with no explicit
top-level slot. Both refusals leave a witness. Live exposure measured
10-24: 1 MeshCore row in 18 days on meshanchor-server, on the right slot
(it came through the subsystem-down path, which passes the override).
"""
from __future__ import annotations

import asyncio
import threading
from queue import Queue
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from gateway import delivery_counters as dc
from gateway.message_queue import PersistentMessageQueue
from tests.test_rns_bridge import bridge  # noqa: F401 — fixture


@pytest.fixture(autouse=True)
def _isolate_delivery_counters(tmp_path, monkeypatch):
    monkeypatch.setenv("MESHANCHOR_DELIVERY_COUNTERS_DB",
                       str(tmp_path / "delivery.db"))
    dc._reset_singleton_for_tests()
    yield
    dc._reset_singleton_for_tests()


@pytest.fixture
def queue(tmp_path):
    q = PersistentMessageQueue(db_path=str(tmp_path / "q.db"))
    q.register_sender("meshcore", lambda p: True)
    yield q
    q.stop_processing()


def _rows(q):
    import json
    with q._get_connection() as conn:
        return [json.loads(r[0]) for r in conn.execute(
            "SELECT payload FROM messages WHERE destination = 'meshcore'")]


def _mesh_broadcast(channel=0, content="hello mesh"):
    from gateway.rns_bridge import BridgedMessage
    return BridgedMessage(source_network="meshtastic", source_id="!aabb0001",
                          destination_id=None, content=content,
                          is_broadcast=True, metadata={"channel": channel})


# ── chokepoint 1: enqueue ─────────────────────────────────────────────

def test_a_failed_bridge_send_requeues_on_the_resolved_slot(
        bridge, queue):  # noqa: F811
    # The exact leak shape: Meshtastic primary channel (0) → MeshCore, the
    # configured private slot is 1, the send fails.
    bridge._persistent_queue = queue
    bridge.config.meshcore.bridge_target_channel = 1
    bridge._meshcore_handler = MagicMock()
    bridge._meshcore_handler.send_text.return_value = False

    bridge._process_bridge_to_meshcore(_mesh_broadcast(channel=0))

    rows = _rows(queue)
    assert len(rows) == 1
    assert rows[0]["channel"] == 1        # the resolved slot, never 0


def test_a_meshcore_requeue_without_a_resolved_slot_is_refused(
        bridge, queue):  # noqa: F811
    bridge._persistent_queue = queue

    assert bridge._requeue_failed_message(
        _mesh_broadcast(channel=0), "meshcore") is False

    assert _rows(queue) == []
    assert bridge.stats["meshcore_requeue_no_slot_refused"] == 1


def test_the_refusal_is_said_out_loud(bridge, queue, caplog):  # noqa: F811
    import logging
    bridge._persistent_queue = queue
    with caplog.at_level(logging.WARNING):
        bridge._requeue_failed_message(_mesh_broadcast(), "meshcore")

    assert any("no resolved MeshCore slot" in r.getMessage()
               for r in caplog.records)


def test_an_explicit_slot_still_requeues(bridge, queue):  # noqa: F811
    # Control: the subsystem-down replay path passes the override.
    bridge._persistent_queue = queue

    assert bridge._requeue_failed_message(
        _mesh_broadcast(channel=0), "meshcore", channel_override=1) is True

    assert _rows(queue)[0]["channel"] == 1


def test_a_meshtastic_requeue_is_unchanged(bridge, tmp_path):  # noqa: F811
    # Scope control: only the MeshCore destination is refused.
    q = PersistentMessageQueue(db_path=str(tmp_path / "m.db"))
    q.register_sender("meshtastic", lambda p: True)
    bridge._persistent_queue = q
    try:
        assert bridge._requeue_failed_message(
            _mesh_broadcast(channel=2), "meshtastic") is True
    finally:
        q.stop_processing()


# ── chokepoint 2: the worker's dispatch ───────────────────────────────

def _mc_handler():
    from gateway.meshcore_handler import MeshCoreHandler
    config = SimpleNamespace(
        meshcore=SimpleNamespace(
            enabled=True, device_path="/dev/ttyUSB1", baud_rate=115200,
            connection_type="serial", tcp_host="localhost", tcp_port=4000,
            auto_fetch_messages=True, bridge_channels=True, bridge_dms=True,
            simulation_mode=True, channel_poll_interval_sec=5),
        meshtastic=SimpleNamespace(host="localhost", port=4403))
    health = MagicMock()
    health.record_error.return_value = "transient"
    h = MeshCoreHandler(config=config, node_tracker=MagicMock(), health=health,
                        stop_event=threading.Event(), stats={"errors": 0},
                        stats_lock=threading.Lock(),
                        message_queue=Queue(maxsize=10))
    h._connected = True
    return h


def _drain(h):
    from utils import tx_guard
    commands = SimpleNamespace(
        get_contacts=AsyncMock(return_value=[
            {"public_key": b"\xab\xcd", "adv_name": "p3"}]),
        send_msg=AsyncMock(return_value=None),
        send_chan_msg=AsyncMock(return_value=None))
    h._meshcore = SimpleNamespace(commands=commands)
    h._connected = True
    with tx_guard.allow_meshcore_egress():
        asyncio.run(h._process_outbound())
    return commands


@pytest.mark.parametrize("row", [
    {"message": "x", "_queue_msg_id": "q-1", "metadata": {"channel": 0}},
    {"message": "x", "_queue_msg_id": "q-2", "metadata": {}},
    {"message": "x", "_queue_msg_id": "q-3", "channel": "",
     "metadata": {"channel": 2}},
    {"message": "x", "_queue_msg_id": "q-4", "channel": "Public"},
], ids=["metadata-source-slot", "no-slot", "empty-slot", "unparsable-slot"])
def test_a_queued_channel_row_without_an_explicit_slot_never_airs(row):
    h = _mc_handler()
    # queue_send refuses these up front now; this pins the WORKER backstop,
    # for a row handed to the send queue by any other path.
    assert h.queue_send(dict(row)) is False
    h._send_queue.put_nowait(dict(row))

    cmds = _drain(h)

    cmds.send_chan_msg.assert_not_called()
    assert h.stats["meshcore_queue_row_no_slot_refused"] == 2  # both gates
    states = [e.state.value for e in
              dc.get_singleton().history_for(row["_queue_msg_id"])]
    assert states == ["dropped"]          # a witness on the row's own id


def test_a_slot_less_row_is_refused_before_the_queue_calls_it_delivered(
        tmp_path):
    # Review of b0b57544, finding 1: refusing only in the async worker came
    # AFTER mark_delivered, so one row read SENT + DROPPED and "delivered".
    # Driven through a REAL queue dispatch, not queue_send alone.
    h = _mc_handler()
    q = PersistentMessageQueue(db_path=str(tmp_path / "q.db"))
    q.register_sender("meshcore", h.queue_send)
    row = q.enqueue({"message": "x", "metadata": {"channel": 0}}, "meshcore",
                    source="meshtastic")
    q.process_once()
    cmds = _drain(h)
    q.stop_processing()

    cmds.send_chan_msg.assert_not_called()
    states = [e.state.value for e in dc.get_singleton().history_for(row)]
    assert "sent" not in states
    with q._get_connection() as conn:
        status = conn.execute("SELECT status FROM messages WHERE id = ?",
                              (row,)).fetchone()[0]
    assert status != "delivered"
    assert h.stats["meshcore_queue_row_no_slot_refused"] == 1


@pytest.mark.parametrize("slot", [True, False])
def test_a_bool_is_not_a_slot(slot):
    # Review finding 6: True is an int; it must not become slot 1.
    h = _mc_handler()

    assert h.queue_send({"message": "x", "_queue_msg_id": "q-b",
                         "channel": slot}) is False


def test_a_queued_row_with_an_explicit_slot_airs_there():
    # Control.
    h = _mc_handler()
    h.queue_send({"message": "x", "_queue_msg_id": "q-5", "channel": 1})

    cmds = _drain(h)

    cmds.send_chan_msg.assert_awaited_once_with(1, "x")


def test_a_queued_dm_row_needs_no_slot():
    # Control: a DM goes to its contact; the slot is irrelevant.
    h = _mc_handler()
    h.queue_send({"message": "x", "_queue_msg_id": "q-6",
                  "destination": "abcd"})

    cmds = _drain(h)

    cmds.send_msg.assert_awaited_once()
    cmds.send_chan_msg.assert_not_called()


def test_a_direct_send_text_broadcast_is_unchanged():
    # Scope control: send_text stamps metadata.channel from its caller's
    # explicit argument — the CanonicalMessage path is not a queue row.
    h = _mc_handler()
    h.send_text("x", channel=2)

    cmds = _drain(h)

    cmds.send_chan_msg.assert_awaited_once_with(2, "x")
