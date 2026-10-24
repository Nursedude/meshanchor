"""BIC-2 slice 2b — the direct sends name their leg.

Twin of MeshForge's ``tests/test_delivery_legs_r2m.py`` (f5c6a26d +
c2024ca7 + 7d93de4d), on MeshAnchor's own seams. Before this slice a direct
send counted under ``?>dst`` at best — and MeshAnchor's MeshtasticHandler,
its supervisor MeshCore handler, its remote-egress branch and its direct
``send_to_rns`` recorded no SENT at all, so those sends were counted
nowhere. The live DB on meshanchor-server read ``?>meshcore`` for every
R→MC direct send (10-23 18:43/19:43).

Seams: ``AckTracker.resolve_leg``; each handler's ``send_text(...,
source=)`` / ``queue_send``; ``BridgeSendMixin.send_to_meshtastic`` /
``send_to_rns`` → ``delivery_counters.snapshot()["legs"]``.
"""
from __future__ import annotations

import ast
import asyncio
import sys
import threading
from pathlib import Path
from queue import Queue
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gateway import delivery_counters as dc
from gateway.ack_tracker import AckTracker
from gateway.delivery_legs import LEG_SOURCE_KEY
from tests.test_rns_bridge import bridge  # noqa: F401 — fixture
from tests.test_rns_bridge import (
    TestCircuitBreakerWiringIssue74 as _CB,
)

SRC = Path(__file__).resolve().parents[1] / "src"


@pytest.fixture(autouse=True)
def _isolate_delivery_counters(tmp_path, monkeypatch):
    monkeypatch.setenv("MESHANCHOR_DELIVERY_COUNTERS_DB",
                       str(tmp_path / "delivery.db"))
    dc._reset_singleton_for_tests()
    yield
    dc._reset_singleton_for_tests()


def _legs():
    return dc.get_singleton().snapshot()["legs"]


def _ids(state=None):
    return [e.id for e in dc.get_singleton().recent()
            if state is None or e.state.value == state]


def _freeze_synth_clock(monkeypatch):
    import gateway.delivery_legs as dl
    monkeypatch.setattr(dl.time, "time", lambda: 1792780000.123)


# ── the ACK tracker carries the leg from send to confirmation ─────────

def test_resolve_leg_returns_the_registered_source():
    t = AckTracker()
    t.register(0xA1, "m1", dest_num=0xAABB0001, source="meshcore")

    assert t.resolve_leg(0xA1, from_num=0xAABB0001) == (
        "m1", "meshtastic", "meshcore")


def test_resolve_keeps_its_two_tuple_shape():
    t = AckTracker()
    t.register(0xA2, "m2", dest_num=0xAABB0001, source="meshcore")

    assert t.resolve(0xA2, from_num=0xAABB0001) == ("m2", "meshtastic")


# ── MeshtasticHandler (TCP interface; CLI fallback) ───────────────────

@pytest.fixture
def mt(allow_local_radio_tx):
    from gateway.config import GatewayConfig, MeshtasticConfig
    from gateway.meshtastic_handler import MeshtasticHandler
    cfg = GatewayConfig()
    cfg.meshtastic = MeshtasticConfig(host="127.0.0.1", port=4403)
    h = MeshtasticHandler(
        config=cfg, node_tracker=MagicMock(), health=MagicMock(),
        stop_event=threading.Event(),
        stats={'errors': 0, 'messages_received': 0, 'messages_sent': 0},
        stats_lock=threading.Lock(), message_queue=Queue(maxsize=10))
    h._connected = True
    h._interface = MagicMock()
    h._interface.sendText.return_value = MagicMock(id=0x11112222, to=0xAABB0001)
    return h


def test_a_direct_meshtastic_broadcast_is_counted_sent_on_its_leg(mt):
    assert mt.send_text("all hands", source="meshcore") is True

    assert _legs()["meshcore>meshtastic"]["15m"]["sent"] == 1
    assert _ids("sent") == ["mesh-11112222"]


def test_a_direct_meshtastic_dm_is_counted_sent_on_its_leg(mt):
    assert mt.send_text("hi", destination="!aabb0001", source="rns") is True

    assert _legs()["rns>meshtastic"]["15m"]["sent"] == 1


def test_a_failed_meshtastic_send_is_not_counted(mt):
    mt._interface.sendText.return_value = None

    assert mt.send_text("all hands", source="meshcore") is False

    assert "meshcore>meshtastic" not in _legs()


def test_a_meshtastic_cli_fallback_send_is_counted_sent(mt, monkeypatch):
    mt._interface = None
    monkeypatch.setattr(mt, "_send_via_cli", lambda *a, **k: True)

    assert mt.send_text("all hands", source="meshcore") is True

    assert _legs()["meshcore>meshtastic"]["15m"]["sent"] == 1


def test_record_sent_false_leaves_the_sent_to_the_queue(mt):
    assert mt.send_text("chunk", source="rns", record_sent=False) is True

    assert _legs() == {}


def test_the_meshtastic_queue_path_records_no_second_sent(mt):
    assert mt.queue_send({"message": "row", "_queue_msg_id": "q-1",
                          LEG_SOURCE_KEY: "rns"})

    assert _legs() == {}


def test_a_counter_error_never_fails_a_meshtastic_send(mt, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("counter broke")
    monkeypatch.setattr(dc, "record", boom)

    assert mt.send_text("all hands", source="meshcore") is True
    assert mt.stats["delivery_record_errors"] == 1


def test_meshtastic_synth_ids_do_not_collide(mt, monkeypatch):
    _freeze_synth_clock(monkeypatch)
    mt._interface.sendText.return_value = MagicMock(id=None, to=None)
    mt.send_text("chunk 1", source="rns")
    mt.send_text("chunk 2", source="rns")

    ids = _ids("sent")
    assert len(ids) == 2 and len(set(ids)) == 2


# ── MQTTBridgeHandler (meshtasticd over HTTP protobuf; CLI fallback) ──

@pytest.fixture
def mq(allow_local_radio_tx):
    from gateway.config import GatewayConfig
    from gateway.mqtt_bridge_handler import MQTTBridgeHandler
    config = GatewayConfig()
    config.rns.meshtastic_ack_consumption_enabled = True
    h = MQTTBridgeHandler(
        config=config, node_tracker=MagicMock(), health=MagicMock(),
        stop_event=threading.Event(), stats={},
        stats_lock=threading.Lock(), message_queue=MagicMock())
    h.config.meshtastic.host = "127.0.0.1"
    h.config.meshtastic.http_port = 9443
    h._load_balancer = None
    h._ack_tracker = AckTracker()
    h._ack_consumption_enabled = lambda: True
    return h


class _Pkt:
    def __init__(self, req, frm, err=None):
        self.request_id, self.from_node, self._err = req, frm, err

    def routing_error_name(self):
        return self._err


def _http(pkt_id):
    return patch("gateway.meshtastic_protobuf_client.send_text_direct_with_id",
                 lambda **kw: pkt_id)


def test_mqtt_direct_broadcast_is_counted_sent_on_its_leg(mq):
    with _http(0x2222):
        assert mq.send_text("all hands", source="meshcore") is True

    assert _legs()["meshcore>meshtastic"]["15m"]["sent"] == 1


def test_mqtt_direct_dm_confirms_on_the_same_leg(mq):
    with _http(0x3333):
        assert mq.send_text("hi", destination="!aabb0001", source="meshcore")

    mq._handle_routing_envelope(_Pkt(0x3333, 0xAABB0001))

    win = _legs()["meshcore>meshtastic"]["15m"]
    assert win["sent"] == 1 and win["confirmed"] == 1


def test_mqtt_nak_fails_on_the_same_leg(mq):
    with _http(0x4444):
        mq.send_text("hi", destination="!aabb0001", source="meshcore")

    mq._handle_routing_envelope(_Pkt(0x4444, 0xAABB0001, "MAX_RETRANSMIT"))

    assert _legs()["meshcore>meshtastic"]["15m"]["failed"] == 1


def test_mqtt_queued_dm_confirms_on_the_rows_leg_without_a_second_sent(mq):
    with _http(0x5555):
        assert mq.queue_send({"message": "reply", "destination": "!aabb0001",
                              "_queue_msg_id": "q-9",
                              LEG_SOURCE_KEY: "meshcore"})

    mq._handle_routing_envelope(_Pkt(0x5555, 0xAABB0001))

    win = _legs()["meshcore>meshtastic"]["15m"]
    assert win["confirmed"] == 1 and "sent" not in win


def test_mqtt_cli_fallback_send_is_counted_sent(mq, monkeypatch):
    monkeypatch.setattr(mq, "_send_via_http_protobuf", lambda *a, **k: False)
    monkeypatch.setattr(mq, "_send_via_cli", lambda *a, **k: True)

    assert mq.send_text("all hands", source="meshcore") is True

    assert _legs()["meshcore>meshtastic"]["15m"]["sent"] == 1


def test_mqtt_refuses_a_bool_as_a_packet_id(mq):
    with _http(True):
        mq.send_text("hi", destination="!aabb0001", source="meshcore")

    assert "mesh-00000001" not in _ids()
    assert mq._ack_tracker.pending_count() == 0


# ── MeshCoreHandler (in-process; async send worker) ───────────────────

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


class _Err:
    type, payload = "command_error", {"code_string": "ERR_TIMEOUT"}

    def is_error(self):
        return True


def _mc_drain(h, send_evt=None, chan_evt=None):
    from utils import tx_guard
    contact = {"public_key": b"\xab\xcd", "adv_name": "p3"}
    h._meshcore = SimpleNamespace(commands=SimpleNamespace(
        get_contacts=AsyncMock(return_value=[contact]),
        send_msg=AsyncMock(return_value=send_evt),
        send_chan_msg=AsyncMock(return_value=chan_evt)))
    h._connected = True
    with tx_guard.allow_meshcore_egress():
        asyncio.run(h._process_outbound())


def test_a_meshcore_dm_is_sent_on_its_leg():
    h = _mc_handler()
    assert h.send_text("hello", destination="abcd", source="rns")

    _mc_drain(h)

    assert _legs()["rns>meshcore"]["15m"]["sent"] == 1


def test_a_meshcore_channel_broadcast_is_sent_on_its_leg():
    h = _mc_handler()
    assert h.send_text("hello", channel=2, source="meshtastic")

    _mc_drain(h)

    assert _legs()["meshtastic>meshcore"]["15m"]["sent"] == 1


def test_a_companion_refusal_fails_on_its_leg():
    h = _mc_handler()
    h.send_text("hello", destination="abcd", source="rns")

    _mc_drain(h, send_evt=_Err())

    assert _legs()["rns>meshcore"]["15m"]["failed"] == 1


def test_a_contact_not_found_drop_is_on_its_leg():
    h = _mc_handler()
    h.send_text("hello", destination="nobody", source="rns")

    _mc_drain(h)

    assert _legs()["rns>meshcore"]["15m"]["failed"] == 1


def test_a_queued_meshcore_row_is_not_counted_sent_twice():
    h = _mc_handler()
    h.queue_send({"message": "hello", "destination": "abcd", "channel": 1,
                  "_queue_msg_id": "q-mc-1", LEG_SOURCE_KEY: "rns"})

    _mc_drain(h)

    assert _ids("sent") == []   # on NO id, on no leg — the queue owns it


def test_a_queued_meshcore_refusal_fails_on_the_rows_id_and_leg():
    h = _mc_handler()
    h.queue_send({"message": "x", "destination": "abcd", "id": "abc",
                  "_queue_msg_id": "q-row", LEG_SOURCE_KEY: "rns"})

    _mc_drain(h, send_evt=_Err())

    assert _legs()["rns>meshcore"]["15m"]["failed"] == 1
    assert dc.get_singleton().history_for("q-row")
    assert not dc.get_singleton().history_for("abc")


def test_a_meshcore_queue_row_ends_with_exactly_one_sent_on_its_own_id(
        tmp_path):
    # Through a REAL queue dispatch, not queue_send alone (MF 7d93de4d #9).
    from gateway.message_queue import PersistentMessageQueue
    h = _mc_handler()
    q = PersistentMessageQueue(db_path=str(tmp_path / "q.db"))
    q.register_sender("meshcore", h.queue_send)
    row = q.enqueue({"message": "hello", "destination": "abcd"}, "meshcore",
                    source="rns")
    q.process_once()
    _mc_drain(h)
    q.stop_processing()

    states = [e.state.value for e in dc.get_singleton().history_for(row)]
    assert states.count("sent") == 1
    assert _ids("sent") == [row]   # and no second SENT on a minted id
    assert _legs()["rns>meshcore"]["15m"]["sent"] == 1


def test_meshcore_synth_ids_do_not_collide(monkeypatch):
    from gateway.meshcore_handler import _record_tx
    _freeze_synth_clock(monkeypatch)
    _record_tx("dropped", None, "non_retriable_error", "not connected")
    _record_tx("dropped", None, "non_retriable_error", "not connected")

    ids = _ids()
    assert len(ids) == 2 and len(set(ids)) == 2


# ── MeshCoreSupervisorHandler (synchronous TX over the supervisor) ────

def _supervisor():
    from gateway.meshcore_supervisor_handler import MeshCoreSupervisorHandler
    h = MeshCoreSupervisorHandler.__new__(MeshCoreSupervisorHandler)
    h._connected = True
    h._client = MagicMock()
    h._truncate_if_needed = lambda t: t
    h.record_chat_message = lambda **kw: None
    return h


def test_a_supervisor_direct_send_is_counted_sent_on_its_leg():
    h = _supervisor()

    assert h.send_text("hello", destination="abcd", source="rns") is True

    assert _legs()["rns>meshcore"]["15m"]["sent"] == 1


def test_a_supervisor_queue_send_leaves_the_sent_to_the_queue():
    h = _supervisor()

    assert h.queue_send({"content": "hello", "destination": "abcd",
                         "_queue_msg_id": "q-s", LEG_SOURCE_KEY: "rns"})

    assert _legs() == {}


# ── BridgeSendMixin: remote egress + direct send_to_rns ───────────────

def test_remote_egress_send_is_counted_sent_on_its_leg(bridge):  # noqa: F811
    bridge._mesh_handler = None
    bridge.config.meshtastic_egress = SimpleNamespace(
        enabled=True, host="10.0.0.9", port=9443, tls=False,
        channel_index=2, want_ack=True)
    with patch("gateway.meshtastic_protobuf_client.send_text_direct",
               return_value=True):
        assert bridge.send_to_meshtastic("hi", source="meshcore") is True

    assert _legs()["meshcore>meshtastic"]["15m"]["sent"] == 1


def test_the_egress_queue_sender_leaves_the_sent_to_the_queue(bridge):  # noqa: F811
    bridge._mesh_handler = None
    bridge.config.meshtastic_egress = SimpleNamespace(
        enabled=True, host="10.0.0.9", port=9443, tls=False,
        channel_index=2, want_ack=True)
    with patch("gateway.meshtastic_protobuf_client.send_text_direct",
               return_value=True):
        assert bridge._queue_send_meshtastic_egress(
            {"message": "row", "_queue_msg_id": "q-e",
             LEG_SOURCE_KEY: "meshcore"})

    assert _legs() == {}


@pytest.mark.usefixtures("allow_rns_tx")
@pytest.mark.parametrize("which,outcome", [("delivered", "confirmed"),
                                           ("failed", "failed")])
def test_a_direct_rns_send_is_sent_and_resolves_on_its_leg(
        bridge, which, outcome):  # noqa: F811
    fake_rns, fake_lxmf = _CB._fake_rns_lxmf_modules()
    _CB()._prime(bridge)
    with patch.dict(sys.modules, {"RNS": fake_rns, "LXMF": fake_lxmf}):
        assert bridge.send_to_rns("hi", b"\xab" * 16, source="meshcore")
    lxm = fake_lxmf.LXMessage.return_value
    reg = (lxm.register_delivery_callback if which == "delivered"
           else lxm.register_failed_callback)
    reg.call_args[0][0](MagicMock(name="receipt"))

    win = _legs()["meshcore>rns"]["15m"]
    assert win["sent"] == 1 and win[outcome] == 1
    assert "?>rns" not in _legs()


@pytest.mark.usefixtures("allow_rns_tx")
def test_lxmf_synth_ids_do_not_collide(bridge, monkeypatch):  # noqa: F811
    _freeze_synth_clock(monkeypatch)
    fake_rns, fake_lxmf = _CB._fake_rns_lxmf_modules()
    _CB()._prime(bridge)
    with patch.dict(sys.modules, {"RNS": fake_rns, "LXMF": fake_lxmf}):
        bridge.send_to_rns("a", b"\xab" * 16, source="meshcore")
        bridge.send_to_rns("b", b"\xcd" * 16, source="meshcore")

    ids = _ids("sent")
    assert len(ids) == 2 and len(set(ids)) == 2


def test_enqueue_message_fallback_names_its_leg(bridge):  # noqa: F811
    bridge._persistent_queue = None
    bridge._mesh_handler = MagicMock()
    bridge._mesh_handler.send_text.return_value = True

    assert bridge.enqueue_message("hi", "!aabb0001", dest_type="meshtastic",
                                  source="meshcore") == "direct"

    assert bridge._mesh_handler.send_text.call_args.kwargs["source"] == \
        "meshcore"


# ── every direct-send caller names its leg ────────────────────────────

# Receivers that are not delivery-counted handlers: the low-level protobuf
# session client (its handler records around it).
_NOT_HANDLERS = {"client"}
# Callers that cannot know the origin; each counts under ``?>dst``.
_SOURCELESS_SEND = {
    # The messaging CLI wrapper calls commands.gateway.send_to_*, which
    # stamp source="local" on the bridge call themselves.
    ("messaging.py", "send_message"),
}
_SEND_FNS = {"send_text", "send_to_meshtastic", "send_to_meshcore",
             "send_to_rns"}


def test_every_direct_send_caller_names_a_source():
    missing = []
    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_text())
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for node in ast.walk(fn):
                if not (isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Attribute)
                        and node.func.attr in _SEND_FNS):
                    continue
                recv = node.func.value
                name = (recv.attr if isinstance(recv, ast.Attribute)
                        else getattr(recv, "id", ""))
                if name in _NOT_HANDLERS:
                    continue
                if any(k.arg == "source" for k in node.keywords):
                    continue
                if (path.name, fn.name) in _SOURCELESS_SEND:
                    continue
                missing.append(f"{path.relative_to(SRC)}:{node.lineno} "
                               f"in {fn.name}()")
    assert not missing, ("direct send without source= counts under '?>dst': "
                         + ", ".join(sorted(set(missing))))


def test_the_guard_sees_a_planted_sourceless_send(tmp_path, monkeypatch):
    # A guard that never failed is not evidence: plant a violation.
    planted = tmp_path / "src"
    planted.mkdir()
    (planted / "x.py").write_text(
        "def f(h):\n    return h._mesh_handler.send_text('hi')\n")
    monkeypatch.setattr(sys.modules[__name__], "SRC", planted)

    with pytest.raises(AssertionError, match="x.py:2"):
        test_every_direct_send_caller_names_a_source()
