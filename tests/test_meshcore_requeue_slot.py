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
from gateway.message_queue import PersistentMessageQueue, SendRefused
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
        bridge, tmp_path):  # noqa: F811
    # ONE gate (review of 39f566c1, #3/#9): the bridge writes only the slot
    # it resolved and the queue's validate_fn refuses — one counter, one
    # DROPPED unsendable witness, no bridge-side second refusal.
    q = _validated_queue(tmp_path)
    bridge._persistent_queue = q
    try:
        assert bridge._requeue_failed_message(
            _mesh_broadcast(channel=0), "meshcore") is False

        assert _rows(q) == []
        assert q.get_stats()["refused_invalid"] == 1
        assert _drop_reasons("refused-") == [("dropped", "unsendable")]
        assert "meshcore_requeue_no_slot_refused" not in bridge.stats
    finally:
        q.stop_processing()


def test_the_refusal_is_said_out_loud(bridge, tmp_path, caplog):  # noqa: F811
    import logging
    q = _validated_queue(tmp_path)
    bridge._persistent_queue = q
    try:
        with caplog.at_level(logging.WARNING):
            bridge._requeue_failed_message(_mesh_broadcast(), "meshcore")
    finally:
        q.stop_processing()

    said = [r.getMessage() for r in caplog.records
            if "refused at enqueue" in r.getMessage()]
    assert len(said) == 1                 # said once, not once per gate
    assert "no valid slot" in said[0]


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
    with pytest.raises(SendRefused):
        h.queue_send(dict(row))
    h._send_queue.put_nowait(dict(row))

    cmds = _drain(h)

    cmds.send_chan_msg.assert_not_called()
    # queue_send's refusal is the QUEUE's to count (mark_refused); the
    # handler counter is the worker backstop's alone.
    assert h.stats["meshcore_queue_row_no_slot_refused"] == 1
    drops = [(e.state.value, e.drop_reason.value) for e in
             dc.get_singleton().history_for(row["_queue_msg_id"])]
    # a witness on the row's own id, under the class's ONE drop_reason (#5)
    assert drops == [("dropped", "unsendable")]


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
    assert q.get_stats()["refused_invalid"] == 1
    assert h.stats.get("meshcore_queue_row_no_slot_refused", 0) == 0


@pytest.mark.parametrize("slot", [True, False])
def test_a_bool_is_not_a_slot(slot):
    # Review finding 6: True is an int; it must not become slot 1.
    h = _mc_handler()

    with pytest.raises(SendRefused):
        h.queue_send({"message": "x", "_queue_msg_id": "q-b", "channel": slot})


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


# ── one validation at enqueue (2026-10-24, re-review of 0ba66869) ─────
# The bridge-side refusal and the queue_send refusal were two gates with two
# predicates; queue_send's plain False made a permanent refusal look
# transient (retried ~4x, dead-lettered as "retries exhausted", the per-row
# DROPPED witness lost). Now the queue itself refuses a row its sender
# declares invalid, at enqueue, and a sender's SendRefused dead-letters a
# row already in the table on its FIRST attempt.

def _validated_queue(tmp_path, send_fn=lambda p: True):
    from gateway.meshcore_outbound_mixin import validate_queue_row
    q = PersistentMessageQueue(db_path=str(tmp_path / "v.db"))
    q.register_sender("meshcore", send_fn, validate_fn=validate_queue_row)
    return q


def _drop_reasons(msg_id_prefix):
    return [(e.state.value, e.drop_reason.value if e.drop_reason else None)
            for e in dc.get_singleton().recent(200)
            if e.id.startswith(msg_id_prefix)]


def test_the_queue_refuses_a_slot_less_meshcore_row_at_enqueue(tmp_path):
    q = _validated_queue(tmp_path)
    try:
        msg_id = q.enqueue({"message": "x", "metadata": {"channel": 0}},
                           "meshcore", source="meshtastic")

        assert msg_id is None
        assert _rows(q) == []
        assert q.get_stats()["refused_invalid"] == 1
        assert _drop_reasons("refused-") == [("dropped", "unsendable")]
    finally:
        q.stop_processing()


@pytest.mark.parametrize("slot", [
    True, False, "", "Public", -1, None,
    1.5, 1.0, "1", 256, 10**6,
], ids=["true", "false", "empty", "name", "negative", "none",
        "float", "integral-float", "digit-string", "past-the-wire-byte",
        "huge"])
def test_no_non_slot_value_passes_enqueue(tmp_path, slot):
    # #6: meshcore_py 2.3.7 encodes the slot as chan.to_bytes(1, "little")
    # (measured on meshanchor-server 2026-10-24): an int in 0..255 or the
    # send raises. int(1.5) == 1 must not become slot 1.
    q = _validated_queue(tmp_path)
    try:
        assert q.enqueue({"message": "x", "channel": slot}, "meshcore") is None
    finally:
        q.stop_processing()


@pytest.mark.parametrize("payload", [
    {"message": "x", "channel": 1},
    {"message": "x", "channel": 0},       # Public, chosen explicitly
    {"message": "x", "channel": 255},     # the wire byte's last value
    {"message": "x", "destination": "abcd"},
], ids=["private-slot", "explicit-public", "wire-max", "dm"])
def test_a_valid_meshcore_row_still_enqueues(tmp_path, payload):
    q = _validated_queue(tmp_path)
    try:
        assert q.enqueue(payload, "meshcore") is not None
    finally:
        q.stop_processing()


def test_the_bridge_refuses_a_bool_channel_override(bridge, tmp_path):  # noqa: F811
    # Re-review of 0ba66869: int(True) == 1 made channel_override=True
    # enqueue as slot 1.
    q = _validated_queue(tmp_path)
    bridge._persistent_queue = q
    try:
        assert bridge._requeue_failed_message(
            _mesh_broadcast(channel=0), "meshcore",
            channel_override=True) is False
        assert _rows(q) == []
    finally:
        q.stop_processing()


def test_a_slot_less_row_already_queued_dead_letters_on_its_first_attempt(
        tmp_path):
    # A row written before this validation existed (the live table on
    # deploy day): no validator at write time, the real handler at dispatch.
    h = _mc_handler()
    q = PersistentMessageQueue(db_path=str(tmp_path / "legacy.db"))
    q.register_sender("meshcore", lambda p: True)
    row = q.enqueue({"message": "x", "metadata": {"channel": 0}}, "meshcore",
                    source="meshtastic")
    terminal = []
    q.register_sender("meshcore", h.queue_send,
                      on_terminal=lambda p, outcome: terminal.append(outcome))
    try:
        q.process_once()
        cmds = _drain(h)

        cmds.send_chan_msg.assert_not_called()
        with q._get_connection() as conn:
            status, retries = conn.execute(
                "SELECT status, retry_count FROM messages WHERE id = ?",
                (row,)).fetchone()
        assert status == "dead_letter"
        assert retries == 1                  # one attempt, never retried
        drops = [(e.state.value, e.drop_reason.value)
                 for e in dc.get_singleton().history_for(row)
                 if e.state.value == "dropped"]
        assert drops == [("dropped", "unsendable")]
        assert "sent" not in [e.state.value for e in
                              dc.get_singleton().history_for(row)]
        assert terminal == ["dropped"]
        assert q.get_stats()["refused_invalid"] == 1   # ONE counter, #3/#9
    finally:
        q.stop_processing()


def test_the_bridge_registers_the_meshcore_validator(tmp_path):
    # Wiring, observed: build the bridge with MeshCore enabled and a real
    # queue, then ask THAT queue to take a slot-less row. Without validate_fn
    # on the registration the enqueue validation is dead code on the box.
    from unittest.mock import patch
    from tests.test_rns_bridge import _mock_gateway_config
    q = PersistentMessageQueue(db_path=str(tmp_path / "wired.db"))
    config = _mock_gateway_config()
    config.meshcore.enabled = True
    with patch("gateway.rns_bridge.GatewayConfig"), \
         patch("gateway.rns_bridge.get_node_tracker"), \
         patch("gateway.rns_bridge.BridgeHealthMonitor"), \
         patch("gateway.rns_bridge.DeliveryTracker"), \
         patch("gateway.rns_bridge.MeshtasticHandler"), \
         patch("gateway.rns_bridge.ReconnectStrategy"), \
         patch("gateway.rns_bridge.HAS_CIRCUIT_BREAKER", False), \
         patch("gateway.rns_bridge.CircuitBreakerRegistry", None), \
         patch("gateway.rns_bridge.HAS_PERSISTENT_QUEUE", True), \
         patch("gateway.rns_bridge.PersistentMessageQueue", return_value=q), \
         patch("gateway.rns_bridge.HAS_MESHCORE", True), \
         patch("gateway.rns_bridge.MeshCoreHandler"), \
         patch("utils.meshcore_supervisor_client.is_supervisor_running",
               return_value=False), \
         patch("gateway.message_routing.CLASSIFIER_AVAILABLE", False), \
         patch("gateway.rns_bridge.HAS_SERVICE_CHECK", False), \
         patch("gateway.rns_bridge.HAS_EVENT_BUS", False), \
         patch("gateway.rns_bridge.HAS_RNS_SNIFFER", False):
        from gateway.rns_bridge import RNSMeshtasticBridge
        RNSMeshtasticBridge(config=config)
    try:
        assert q.has_sender("meshcore")
        assert q.enqueue({"message": "x", "metadata": {"channel": 0}},
                         "meshcore") is None
        assert q.enqueue({"message": "x", "channel": 1}, "meshcore")
    finally:
        q.stop_processing()


# ── a slot refusal is a DELIVERY FAILURE (operator 2026-10-24) ────────
# Re-review of 7c582519, #1: the gates recorded ``invalid_payload``, which
# sits in the benign pre-delivery half — so a bridged message the gateway
# accepted and then lost to a slot refusal never read as a failure on its
# leg. Operator: "count it as a delivery failure — at least that's how we
# understand it at this time." Its own reason, so ``invalid_payload`` keeps
# its benign meaning and the classification can move again in one place.

def test_unsendable_is_a_delivery_failure_and_invalid_payload_is_not():
    assert dc.DropReason.UNSENDABLE.value == "unsendable"
    assert "unsendable" in dc.DELIVERY_FAILURE_REASONS
    assert "invalid_payload" not in dc.DELIVERY_FAILURE_REASONS


def test_a_refused_row_reads_failed_on_its_leg(tmp_path):
    # The consumer that changes: BIC-2's per-leg outcome, not the
    # confirmation rate (MeshCore never confirms — measured 10-24,
    # confirmable_protocols == ["rns"] on meshanchor-server).
    q = _validated_queue(tmp_path)
    try:
        q.enqueue({"message": "x", "metadata": {"channel": 0}}, "meshcore",
                  source="meshtastic")
    finally:
        q.stop_processing()
    [event] = [e for e in dc.get_singleton().recent(200)
               if e.id.startswith("refused-")]
    assert dc._leg_outcome(event) == "failed"


# ── the slot is bounded ONCE, where the bridge resolves it ────────────
# Re-review #3 / queued #2: bridge_target_channel=256 passed the resolver,
# reached meshcore_py's chan.to_bytes(1) and died as a generic
# OverflowError. Out of range is a misconfiguration that loses every
# bridged message: one DROPPED unsendable per message, said on the leg.
# UNSET stays what it was — the bridge to MeshCore is off by config, which
# is not a delivery failure.

@pytest.mark.parametrize("slot", [256, 300, 10**6])
def test_an_out_of_range_bridge_slot_is_refused_and_recorded(
        bridge, slot):  # noqa: F811
    bridge.config.meshcore.bridge_target_channel = slot
    bridge._meshcore_handler = MagicMock()

    bridge._process_bridge_to_meshcore(_mesh_broadcast(channel=0))

    bridge._meshcore_handler.send_text.assert_not_called()
    drops = [(e.state.value, e.drop_reason.value, e.source)
             for e in dc.get_singleton().recent(200)
             if e.protocol == "meshcore"]
    assert drops == [("dropped", "unsendable", "meshtastic")]
    assert bridge.stats["meshcore_bridge_slot_out_of_range"] == 1
    # One message, one stat (review of 6a21cde3, #3): not ALSO "unset".
    assert "meshcore_bridge_default_channel_drop" not in bridge.stats


def test_an_unset_bridge_slot_records_no_failure(bridge):  # noqa: F811
    # Control: unset = the operator has not enabled the MeshCore bridge.
    bridge.config.meshcore.bridge_target_channel = -1
    bridge._meshcore_handler = MagicMock()

    bridge._process_bridge_to_meshcore(_mesh_broadcast(channel=0))

    bridge._meshcore_handler.send_text.assert_not_called()
    assert [e for e in dc.get_singleton().recent(200)
            if e.protocol == "meshcore"] == []
    assert "meshcore_bridge_slot_out_of_range" not in bridge.stats


def test_the_wire_maximum_still_bridges(bridge):  # noqa: F811
    bridge.config.meshcore.bridge_target_channel = 255
    bridge._meshcore_handler = MagicMock()
    bridge._meshcore_handler.send_text.return_value = True

    bridge._process_bridge_to_meshcore(_mesh_broadcast(channel=0))

    assert bridge._meshcore_handler.send_text.call_args.args[2] == 255


# ── the enqueue refusal WARNING is rate-limited (queued #7) ───────────
# Now that the queue is the ONE gate, every writer's refusal funnels into
# one log line; a misconfigured busy mesh would flood the journal. The
# counter still counts every refusal — only the log is throttled.

def test_repeated_refusals_warn_once_and_count_every_one(tmp_path, caplog):
    import logging
    q = _validated_queue(tmp_path)
    try:
        with caplog.at_level(logging.WARNING):
            for i in range(5):
                q.enqueue({"message": f"x{i}", "metadata": {}}, "meshcore")
        said = [r.getMessage() for r in caplog.records
                if "refused at enqueue" in r.getMessage()]
        assert len(said) == 1
        assert q.get_stats()["refused_invalid"] == 5
        assert len([e for e in dc.get_singleton().recent(200)
                    if e.id.startswith("refused-")]) == 5

        # After the interval the next refusal speaks again and says how
        # many it swallowed.
        q._refused_log_state["meshcore"] = (0.0, 4)
        caplog.clear()
        with caplog.at_level(logging.WARNING):
            q.enqueue({"message": "x9", "metadata": {}}, "meshcore")
        said = [r.getMessage() for r in caplog.records
                if "refused at enqueue" in r.getMessage()]
        assert len(said) == 1 and "4 more suppressed" in said[0]
    finally:
        q.stop_processing()


# ── review of 6a21cde3 (fix round) ────────────────────────────────────

@pytest.mark.parametrize("slot", [300, True, 1.9, "Public"])
def test_the_resolver_is_pure(bridge, slot):  # noqa: F811
    # #2: the replay path calls the resolver BEFORE its private-message
    # check; a resolver that records made one private message two drops.
    # The CALLER records, once, on the branch that actually drops.
    bridge.config.meshcore.bridge_target_channel = slot
    assert bridge._resolve_bridge_target_channel(_mesh_broadcast()) == -1
    assert dc.get_singleton().recent(200) == []
    assert "meshcore_bridge_slot_out_of_range" not in bridge.stats


@pytest.mark.parametrize("slot", [True, 1.9, "Public"])
def test_a_non_slot_config_never_airs_live(bridge, slot):  # noqa: F811
    # #9: int(True) == int(1.9) == 1 aired live on slot 1 while the queue's
    # predicate refused the same value. ONE predicate for both paths.
    bridge.config.meshcore.bridge_target_channel = slot
    bridge._meshcore_handler = MagicMock()

    bridge._process_bridge_to_meshcore(_mesh_broadcast(channel=0))

    bridge._meshcore_handler.send_text.assert_not_called()
    assert bridge.stats["meshcore_bridge_slot_out_of_range"] == 1


def test_a_digit_string_config_still_bridges(bridge):  # noqa: F811
    # Control: JSON config is not coerced by the dataclass; "1" worked
    # before (int()) and must keep working.
    bridge.config.meshcore.bridge_target_channel = "1"
    assert bridge._resolve_bridge_target_channel(_mesh_broadcast()) == 1


def test_a_raising_validator_reads_as_our_error_not_the_destinations(
        tmp_path):
    # #8: fail-closed is right, but a validator bug is a GATEWAY fault —
    # labelling it unsendable sends the operator to the slot config.
    def boom(payload):
        raise KeyError("shape")
    q = PersistentMessageQueue(db_path=str(tmp_path / "boom.db"))
    q.register_sender("meshcore", lambda p: True, validate_fn=boom)
    try:
        assert q.enqueue({"message": "x", "channel": 1}, "meshcore") is None
    finally:
        q.stop_processing()
    assert _drop_reasons("refused-") == [("dropped", "non_retriable_error")]
