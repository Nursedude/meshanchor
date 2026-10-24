"""BIC-2 slice 2a — the persistent queue's lifecycle events carry their leg.

Twin of MeshForge's ``tests/test_delivery_legs_queue.py`` (3c9057a4 + its
two review rounds + 5575b41d). Before this slice every queued, retried,
expired or dead-lettered MeshAnchor row counted under ``?>dst``; the live
DB on meshanchor-server read only ``?>meshcore`` / ``?>rns`` (10-23). The
origin rides the payload as ``_leg_source`` (one constant,
``delivery_legs.LEG_SOURCE_KEY``); every sender and the dedup hash read
named keys only.

Seam: ``PersistentMessageQueue.enqueue(..., source=)`` → its lifecycle
events → ``delivery_counters.snapshot()["legs"]``.

Not here (slice 2b): the DIRECT sends (``send_to_rns`` /
``send_to_meshtastic`` / MeshCore handler) and the MeshCore queue-row
SENT/FAILED ordering.
"""
from __future__ import annotations

import ast
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from gateway import delivery_counters as dc
from gateway.delivery_legs import LEG_SOURCE_KEY
from gateway.message_queue import MessagePriority, PersistentMessageQueue

SRC = Path(__file__).resolve().parents[1] / "src"


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
    # MA's enqueue refuses a destination nobody sends to (Issue #67);
    # MeshForge's does not, so its twin tests need no senders.
    for dest in ("rns", "rns_xform", "meshtastic", "meshcore"):
        q.register_sender(dest, lambda p: True)
    yield q
    q.stop_processing()


def _legs():
    return dc.get_singleton().snapshot()["legs"]


def _age(queue, msg_id, seconds):
    with queue._get_connection() as conn:
        conn.execute("UPDATE messages SET created_at = ? WHERE id = ?",
                     ((datetime.now() - timedelta(seconds=seconds)).isoformat(),
                      msg_id))


# ── the queue row's own lifecycle ─────────────────────────────────────

def test_a_queued_message_counts_on_its_leg(queue):
    queue.enqueue({"message": "hi", "destination_hash": "ab" * 16}, "rns",
                  source="meshcore")

    assert _legs()["meshcore>rns"]["15m"]["queued"] == 1


def test_a_delivered_queue_row_counts_sent_on_the_same_leg(queue):
    msg_id = queue.enqueue({"message": "hi"}, "rns", source="meshcore")

    queue.mark_delivered(msg_id)

    assert _legs()["meshcore>rns"]["15m"]["sent"] == 1


def test_exhausted_retries_count_failed_on_the_same_leg(queue):
    msg_id = queue.enqueue({"message": "hi"}, "rns", source="meshcore",
                           max_retries=1)

    queue.mark_failed(msg_id, "no path")   # retry_count 1 >= 1: dead letter

    assert _legs()["meshcore>rns"]["15m"]["failed"] == 1
    assert "?>rns" not in _legs()


def test_a_shed_row_counts_on_its_leg(queue):
    queue.enqueue({"text": "sheddable"}, "rns", source="meshcore",
                  priority=MessagePriority.LOW)

    assert queue._shed_overflow(count=1) == 1

    assert _legs()["meshcore>rns"]["15m"]["dropped_other"] == 1


def test_an_expired_row_counts_failed_on_its_leg(queue):
    queue.register_sender("rns", lambda p: True, max_age_s=100)
    msg_id = queue.enqueue({"message": "x", "destination": "1"}, "rns",
                           source="meshcore")
    _age(queue, msg_id, 200)

    assert queue.expire_overdue() == 1

    assert _legs()["meshcore>rns"]["15m"]["failed"] == 1


def test_a_stale_dead_lettered_row_counts_failed_on_its_leg(queue):
    msg_id = queue.enqueue({"message": "hi"}, "rns", source="meshcore",
                           max_retries=1)
    old = datetime.now() - timedelta(seconds=queue.STALE_TIMEOUT + 60)
    with queue._get_connection() as conn:
        conn.execute("UPDATE messages SET status = 'in_progress', "
                     "retry_count = 1, updated_at = ? WHERE id = ?",
                     (old.isoformat(), msg_id))

    queue.cleanup_stale()

    assert _legs()["meshcore>rns"]["15m"]["failed"] == 1
    assert "?>rns" not in _legs()


def test_a_dedup_drop_counts_on_its_leg(queue):
    payload = {"message": "same", "destination": "1"}
    queue.enqueue(payload, "rns", source="meshcore")
    queue.enqueue(payload, "rns", source="meshcore")

    assert _legs()["meshcore>rns"]["15m"]["dropped_other"] == 1


def test_the_leg_source_does_not_change_the_dedup_hash(queue):
    # A row queued before this slice (no _leg_source) and the same message
    # queued after must still dedup against each other across a deploy.
    payload = {"message": "same", "destination": "1"}
    queue.enqueue(payload, "rns")

    assert queue.enqueue(payload, "rns", source="meshcore") is None


def test_the_callers_payload_is_not_mutated(queue):
    payload = {"message": "hi"}
    queue.enqueue(payload, "rns", source="meshcore")

    assert payload == {"message": "hi"}


def test_a_row_queued_without_a_source_still_reads_unknown(queue):
    # Absent is unknown, never a guessed transport (UNKNOWN first-class).
    queue.enqueue({"message": "hi"}, "rns")

    assert _legs()["?>rns"]["15m"]["queued"] == 1


# ── rns_xform rows: RNS-bound traffic, but their "sent" is a dispatch ──

def test_an_rns_xform_row_is_queued_on_its_rns_leg(queue):
    queue.enqueue({"content": "hi", "source_id": "!a"}, "rns_xform",
                  source="meshcore")

    assert _legs()["meshcore>rns"]["15m"]["queued"] == 1


def test_an_rns_xform_dispatch_is_not_counted_as_a_send(queue):
    # The replay runs the real pipeline; its own send records the real
    # SENT. Counting the dispatch too would double every spilled message.
    msg_id = queue.enqueue({"content": "hi", "source_id": "!a"}, "rns_xform",
                           source="meshcore")

    queue.mark_delivered(msg_id)

    assert "sent" not in _legs()["meshcore>rns"]["15m"]


def test_an_expired_rns_xform_row_is_a_failure_on_its_rns_leg(queue):
    queue.register_sender("rns_xform", lambda p: True, max_age_s=100)
    msg_id = queue.enqueue({"content": "x", "source_id": "!a"}, "rns_xform",
                           source="meshcore")
    _age(queue, msg_id, 200)

    assert queue.expire_overdue() == 1

    assert _legs()["meshcore>rns"]["15m"]["failed"] == 1


# ── late resolutions on a terminal row (MF review F5 + re-review #1) ──

def test_a_late_mark_failed_on_a_dead_lettered_row_is_not_a_second_loss(queue):
    msg_id = queue.enqueue({"message": "hi"}, "rns", source="meshcore",
                           max_retries=1)
    queue.mark_failed(msg_id, "no path")         # dead letter: one loss
    queue.mark_failed(msg_id, "late worker")     # row already terminal

    assert _legs()["meshcore>rns"]["15m"]["failed"] == 1
    assert dc.get_singleton().snapshot()["drop_reasons"][
        "retries_exhausted"] == 1


def test_a_late_failure_after_the_stale_reset_dead_lettered_the_row(queue):
    msg_id = queue.enqueue({"message": "hi"}, "rns", source="meshcore",
                           max_retries=1)
    old = datetime.now() - timedelta(seconds=queue.STALE_TIMEOUT + 60)
    with queue._get_connection() as conn:
        conn.execute("UPDATE messages SET status = 'in_progress', "
                     "updated_at = ? WHERE id = ?", (old.isoformat(), msg_id))
    queue.cleanup_stale()                        # dead letter: one loss
    failed_before = queue._stats["failed"]

    queue.mark_failed(msg_id, "late worker")     # row already terminal

    assert _legs()["meshcore>rns"]["15m"]["failed"] == 1
    assert queue._stats["failed"] == failed_before


def test_the_queue_failed_stat_agrees_with_the_drop_count(queue):
    msg_id = queue.enqueue({"message": "hi"}, "rns", source="meshcore",
                           max_retries=1)
    queue.mark_failed(msg_id, "no path")
    queue.mark_failed(msg_id, "late worker")

    assert queue._stats["failed"] == 1


# ── source-shape guards ───────────────────────────────────────────────

def test_every_queue_lifecycle_record_names_a_source():
    # A new _dc.record() in the queue without source= silently puts its
    # rows back on ?>dst. The byte-locked outage mixin names the leg
    # through the queue's ``_leg_record_kwargs`` hook.
    def _names_leg(k):
        if k.arg == "source":
            return True
        v = k.value
        return (k.arg is None and isinstance(v, ast.Call)
                and isinstance(v.func, ast.Attribute)
                and v.func.attr == "_leg_record_kwargs")

    missing = []
    for path in sorted((SRC / "gateway").glob("message_queue*.py")):
        for node in ast.walk(ast.parse(path.read_text())):
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "record"
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "_dc"
                    and not any(_names_leg(k) for k in node.keywords)):
                missing.append(f"{path.name}:{node.lineno}")
    assert not missing, ("queue lifecycle record without source=: "
                         + ", ".join(missing))


def test_the_queue_overrides_the_leg_hook_the_guard_trusts():
    # The guard above accepts the outage mixin's ``**_leg_record_kwargs``
    # spread; that is only a leg if the queue overrides the shared default
    # (which names nothing). MF review 10-23 #2.
    from gateway.message_queue_outage_mixin import MessageQueueOutageMixin

    assert (PersistentMessageQueue._leg_record_kwargs
            is not MessageQueueOutageMixin._leg_record_kwargs)
    q = PersistentMessageQueue.__new__(PersistentMessageQueue)
    assert q._leg_record_kwargs({LEG_SOURCE_KEY: "meshcore"}) == {
        "source": "meshcore"}
    assert q._leg_record_kwargs({}) == {"source": None}


# Producers that cannot know the origin. Each counts under ``?>dst``.
_SOURCELESS_ENQUEUE = {
    # OfflineSyncEngine.enqueue: a different queue class (category-keyed
    # telemetry sync), not the persistent message queue.
    ("offline_sync.py", "enqueue"),
}


def test_every_gateway_enqueue_names_a_source():
    # ALL of src/, not just gateway/: on MF the live end-state check found
    # a canary under src/lab/ enqueueing sourceless (10-23).
    missing = []
    for path in sorted(SRC.rglob("*.py")):
        if path.name.startswith("message_queue"):
            continue      # the queue itself
        for fn in ast.walk(ast.parse(path.read_text())):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for node in ast.walk(fn):
                if (isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Attribute)
                        and node.func.attr == "enqueue"
                        and not any(k.arg == "source" for k in node.keywords)
                        and (path.name, fn.name) not in _SOURCELESS_ENQUEUE):
                    missing.append(f"{path.name}:{node.lineno} in {fn.name}()")
    assert not missing, ("enqueue() without source= counts under '?>dst': "
                         + ", ".join(sorted(set(missing))))


# ── the bridge's queue paths ──────────────────────────────────────────

from tests.test_rns_bridge import bridge  # noqa: E402,F401 — fixture
from tests.test_rns_bridge import (  # noqa: E402
    TestCircuitBreakerWiringIssue74 as _CB,
)


@pytest.fixture
def real_queue_bridge(bridge, queue):  # noqa: F811 — fixture param
    bridge._persistent_queue = queue
    return bridge


@pytest.mark.usefixtures("allow_rns_tx")
@pytest.mark.parametrize("which,outcome", [("delivered", "confirmed"),
                                           ("failed", "failed")])
def test_a_queued_rns_send_confirms_on_the_rows_leg(bridge, which, outcome):  # noqa: F811
    import sys
    fake_rns, fake_lxmf = _CB._fake_rns_lxmf_modules()
    _CB()._prime(bridge)
    payload = {"message": "hi", "destination_hash": b"\xab" * 16,
               "_queue_msg_id": "row-7", LEG_SOURCE_KEY: "meshcore"}
    with patch.dict(sys.modules, {"RNS": fake_rns, "LXMF": fake_lxmf}):
        assert bridge._queue_send_rns(payload) is True
    lxm = fake_lxmf.LXMessage.return_value
    reg = (lxm.register_delivery_callback if which == "delivered"
           else lxm.register_failed_callback)
    reg.call_args[0][0](MagicMock(name="receipt"))

    assert _legs()["meshcore>rns"]["15m"][outcome] == 1
    assert "?>rns" not in _legs()


def _msg(source_network, origin=None):
    from gateway.rns_bridge import BridgedMessage
    m = BridgedMessage(source_network=source_network, source_id="!a",
                       destination_id=None, content="x", is_broadcast=True)
    if origin is not None:
        m.origin = origin
    return m


def test_a_requeued_message_is_queued_on_its_ingress_leg(real_queue_bridge):
    assert real_queue_bridge._requeue_failed_message(
        _msg("rns"), "meshcore") is True

    assert _legs()["rns>meshcore"]["15m"]["queued"] == 1


@pytest.mark.parametrize("origin", ["mqtt", "MQTT_ENUM"])
def test_mqtt_origin_is_read_whether_enum_or_string(bridge, origin):  # noqa: F811
    # source_network stays a routing input; MQTT ingress is its own leg.
    from gateway.bridge_health import MessageOrigin
    m = _msg("meshtastic",
             MessageOrigin.MQTT if origin == "MQTT_ENUM" else origin)

    assert bridge._leg_source(m) == "mqtt"


def test_requeued_rns_to_mesh_chunks_are_queued_on_the_rns_leg(
        real_queue_bridge):
    assert real_queue_bridge._requeue_failed_chunks(
        ["part 1", "part 2"], target="meshtastic", source="rns") is True

    assert _legs()["rns>meshtastic"]["15m"]["queued"] == 2


def test_the_mesh_preset_bridge_names_a_transport_not_an_interface():
    # MF's twin passes the interface name ("primary"/"secondary"), which
    # is outside the leg vocabulary and reads "?". Both ends of a preset
    # bridge are Meshtastic.
    tree = ast.parse((SRC / "gateway" / "mesh_bridge.py").read_text())
    sources = [k.value for node in ast.walk(tree)
               if isinstance(node, ast.Call)
               and isinstance(node.func, ast.Attribute)
               and node.func.attr == "enqueue"
               for k in node.keywords if k.arg == "source"]
    assert sources and all(isinstance(v, ast.Constant)
                           and v.value == "meshtastic" for v in sources)


# ── review of 016a38cb..d97a41e3 (contextless, 10-23) ─────────────────

def test_a_replay_that_carries_no_leg_reads_unknown_not_meshtastic(bridge):  # noqa: F811
    # F2: the spill replay hard-codes source_network="meshtastic" (a routing
    # input). A row queued before this slice carries no leg; the rebuilt
    # message must count under "?", never a guessed real transport.
    m = _msg("meshtastic")
    m.metadata = {"m2r_replay": True}

    assert bridge._leg_source(m) is None


def test_a_replay_that_carries_its_leg_reads_it(bridge):  # noqa: F811
    m = _msg("meshtastic")
    m.metadata = {"m2r_replay": True, "m2r_leg_source": "meshcore"}

    assert bridge._leg_source(m) == "meshcore"


def test_chunk_requeue_has_no_default_origin(real_queue_bridge):
    # F5 + re-review R4: a default only relocates the hard-code — a caller
    # that forgets the origin must fail loudly, not count on rns>X.
    # (Meshtastic target only: the chunks carry the Meshtastic channel, so
    # any other target is the #37 channel-leak class — re-review R3.)
    with pytest.raises(TypeError):
        real_queue_bridge._requeue_failed_chunks(["c1"], target="meshtastic")


def test_the_rns_to_mesh_chunk_requeue_names_the_messages_leg():
    # Re-review R7: pin the REAL call site, not a hypothetical shape.
    import inspect
    from gateway.rns_bridge import RNSMeshtasticBridge
    tree = ast.parse(inspect.cleandoc(
        "def _f():\n" + inspect.getsource(
            RNSMeshtasticBridge._process_rns_to_mesh)).replace(
                "def _f():\n", "", 1))
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Attribute)
             and n.func.attr == "_requeue_failed_chunks"]
    assert calls
    for c in calls:
        src = [k.value for k in c.keywords if k.arg == "source"]
        assert len(src) == 1 and ast.unparse(src[0]) == "self._leg_source(msg)"


@pytest.mark.parametrize("with_policy", [False, True])
def test_a_late_mark_failed_logs_no_retry_it_did_not_schedule(
        queue, caplog, with_policy):
    # F6: the guarded UPDATE matched 0 rows; the log must not claim a retry.
    # Both branches (re-review R8): the RetryPolicy path and the
    # RETRY_DELAYS fallback each build their own retry line.
    import logging
    from gateway.message_queue_models import RetryPolicy
    if with_policy:
        queue.set_retry_policy(RetryPolicy(max_tries=5))
    msg_id = queue.enqueue({"message": "hi"}, "rns", source="meshcore",
                           max_retries=3)
    with queue._get_connection() as conn:
        conn.execute("UPDATE messages SET status = 'dead_letter' "
                     "WHERE id = ?", (msg_id,))
    with caplog.at_level(logging.DEBUG, logger="gateway.message_queue"):
        assert queue.mark_failed(msg_id, "timeout") is False

    assert "scheduled for retry" not in caplog.text
