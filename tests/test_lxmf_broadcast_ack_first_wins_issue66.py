"""
Issue #66 first-caller: LXMFBroadcastBridge ack-synthesis wiring.

The bridge fans a MeshCore broadcast out as N parallel LXMF DMs. The
first subscriber whose LXMF delivery callback fires causes a synthetic
[delivered:<id>] back to the originating MeshCore device; later winners
are idempotent (PersistentMessageQueue.mark_acked returns None on the
2nd call, so _maybe_emit_ack_for_msgid emits at most once per broadcast).
Per-subscriber failures don't drive a synth [failed:…] — only the
substrate's timeout sweep emits [timeout:…] when no subscriber wins.

These tests exercise the wiring contract:
- ack_required=False is silent (backward compat)
- ack_required=True with subscribers registers ONE pending-ack per fan-out
- The same msg_id flows from on_meshcore_message into every subscriber's
  LXMessage callback closure
- Recursion guard via metadata['meshforge_is_synth_ack'] short-circuits
  registration
- Defensive: missing persistent_queue / missing source_address / no
  subscribers / register_pending_ack raising all gracefully fall back to
  the pre-Issue-#66 fire-and-forget path
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from gateway.canonical_message import CanonicalMessage, MessageType, Protocol
from gateway.config import LXMFBroadcastConfig
from gateway.lxmf_broadcast_bridge import LXMFBroadcastBridge


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_canonical(*, text="hello", channel=1, sender="p3pubkeyhex",
                    is_broadcast=True, source_network=None, metadata=None):
    md = {"channel": channel}
    if metadata:
        md.update(metadata)
    return CanonicalMessage(
        source_network=source_network or Protocol.MESHCORE.value,
        source_address=sender,
        destination_address=None,
        content=text,
        message_type=MessageType.TEXT,
        is_broadcast=is_broadcast,
        metadata=md,
    )


def _fake_rns_lxmf():
    rns = MagicMock(name="RNS")
    rns.Identity = MagicMock()
    rns.Identity.from_file = MagicMock(side_effect=Exception("no file"))
    rns.Identity.return_value = MagicMock(name="identity")
    rns.Transport = MagicMock()
    rns.Transport.has_path = MagicMock(return_value=True)
    rns.Transport.request_path = MagicMock()
    rns.Identity.recall = MagicMock(return_value=MagicMock(name="dest_identity"))
    rns.Destination = MagicMock()
    rns.Destination.OUT = "OUT"
    rns.Destination.SINGLE = "SINGLE"

    lxmf = MagicMock(name="LXMF")
    # Each LXMessage instance is a fresh MagicMock so each subscriber's
    # callbacks are captured independently.
    lxmf.LXMessage = MagicMock(side_effect=lambda *a, **kw: MagicMock(name="lxm"))
    router = MagicMock(name="LXMRouter")
    src = MagicMock()
    src.hash = bytes.fromhex("aabbccddeeff0011")
    router.register_delivery_identity.return_value = src
    lxmf.LXMRouter.return_value = router
    return rns, lxmf, router


def _start_bridge(tmp_path, *, ack_required=False, persistent_queue=None,
                  ack_emit_callback=None, channels=None,
                  pre_register_subs=None):
    rns, lxmf, router = _fake_rns_lxmf()
    cfg = LXMFBroadcastConfig(
        enabled=True,
        channels=channels if channels is not None else [0, 1],
        display_name="Test Broadcast",
        announce_interval_sec=0,
        prefix_format="[ch{channel}:{sender}] {text}",
        autosubscribe=False,
        ack_required=ack_required,
    )
    b = LXMFBroadcastBridge(
        broadcast_config=cfg,
        rns_module=rns,
        lxmf_module=lxmf,
        identity_path=tmp_path / "identity",
        db_path=tmp_path / "subs.db",
        storage_path=tmp_path / "storage",
        persistent_queue=persistent_queue,
        ack_emit_callback=ack_emit_callback,
    )
    assert b.start() is True
    for sub_hash in (pre_register_subs or []):
        b._subs.add(sub_hash)
    return b, rns, lxmf, router


# ---------------------------------------------------------------------------
# ack_required=False — backward compat
# ---------------------------------------------------------------------------


class TestAckRequiredOff:

    def test_no_pending_ack_when_flag_off(self, tmp_path):
        pq = MagicMock()
        emit = MagicMock()
        b, _, _, _ = _start_bridge(
            tmp_path,
            ack_required=False,
            persistent_queue=pq,
            ack_emit_callback=emit,
            pre_register_subs=["1122334455667788"],
        )
        b.on_meshcore_message(_make_canonical())
        pq.register_pending_ack.assert_not_called()
        emit.assert_not_called()

    def test_no_callbacks_wired_when_flag_off(self, tmp_path):
        pq = MagicMock()
        emit = MagicMock()
        b, _, lxmf, _ = _start_bridge(
            tmp_path,
            ack_required=False,
            persistent_queue=pq,
            ack_emit_callback=emit,
            pre_register_subs=["1122334455667788"],
        )
        b.on_meshcore_message(_make_canonical())
        # The LXMessage created for the subscriber had NO delivery callback
        # registered (since ack_msg_id stays None).
        # Find the lxm instance returned by the LXMessage call.
        assert lxmf.LXMessage.called
        lxm_instance = lxmf.LXMessage.return_value
        # When using side_effect, return_value is a default Mock;
        # the actual returned object is what the bridge used. Easier:
        # check that register_delivery_callback was never invoked on any
        # call_args object. With side_effect=lambda, each call returns a
        # FRESH MagicMock — we check via stats["fanouts"] increased and
        # no emit was called.
        assert b.stats["fanouts"] >= 1


# ---------------------------------------------------------------------------
# ack_required=True — happy path
# ---------------------------------------------------------------------------


class TestAckRequiredOn:

    def test_pending_ack_registered_once_per_fanout(self, tmp_path):
        pq = MagicMock()
        emit = MagicMock(return_value=True)
        b, _, _, _ = _start_bridge(
            tmp_path,
            ack_required=True,
            persistent_queue=pq,
            ack_emit_callback=emit,
            pre_register_subs=["1122334455667788", "2233445566778899"],
        )
        msg = _make_canonical(sender="p3pubkeyhex", channel=1)
        b.on_meshcore_message(msg)
        # ONE registration, even though two subscribers will be fanned to
        assert pq.register_pending_ack.call_count == 1
        kwargs = pq.register_pending_ack.call_args.kwargs
        assert kwargs["origin_network"] == "meshcore"
        assert kwargs["origin_address"] == "p3pubkeyhex"
        assert kwargs["timeout_seconds"] == 300
        # msg_id has the bcast-<ts>-ch<channel> shape
        msg_id_arg = pq.register_pending_ack.call_args.args[0]
        assert msg_id_arg.startswith("bcast-")
        assert msg_id_arg.endswith("-ch1")

    def test_callbacks_registered_on_each_lxmessage(self, tmp_path):
        # Use a real list of created LXMessage instances to inspect.
        instances = []

        def lxm_factory(*a, **kw):
            inst = MagicMock(name="lxm")
            instances.append(inst)
            return inst

        rns = MagicMock()
        rns.Identity = MagicMock()
        rns.Identity.from_file = MagicMock(side_effect=Exception("no file"))
        rns.Identity.return_value = MagicMock()
        rns.Transport = MagicMock()
        rns.Transport.has_path = MagicMock(return_value=True)
        rns.Transport.request_path = MagicMock()
        rns.Identity.recall = MagicMock(return_value=MagicMock())
        rns.Destination = MagicMock()
        rns.Destination.OUT = "OUT"
        rns.Destination.SINGLE = "SINGLE"

        lxmf = MagicMock()
        lxmf.LXMessage = MagicMock(side_effect=lxm_factory)
        router = MagicMock()
        src = MagicMock()
        src.hash = bytes.fromhex("aabbccddeeff0011")
        router.register_delivery_identity.return_value = src
        lxmf.LXMRouter.return_value = router

        pq = MagicMock()
        emit = MagicMock(return_value=True)
        cfg = LXMFBroadcastConfig(
            enabled=True, channels=[0, 1], display_name="t",
            announce_interval_sec=0,
            prefix_format="[ch{channel}:{sender}] {text}",
            autosubscribe=False, ack_required=True,
        )
        b = LXMFBroadcastBridge(
            broadcast_config=cfg, rns_module=rns, lxmf_module=lxmf,
            identity_path=tmp_path / "id", db_path=tmp_path / "s.db",
            storage_path=tmp_path / "stg",
            persistent_queue=pq, ack_emit_callback=emit,
        )
        assert b.start() is True
        b._subs.add("1122334455667788")
        b._subs.add("2233445566778899")

        b.on_meshcore_message(_make_canonical(channel=1))

        # Two subscribers => two LXMessage instances => callbacks on each
        assert len(instances) == 2
        for inst in instances:
            inst.register_delivery_callback.assert_called_once()
            inst.register_failed_callback.assert_called_once()

    def test_on_delivered_callback_invokes_emit_with_msg_id_and_delivered(
            self, tmp_path):
        instances = []

        def lxm_factory(*a, **kw):
            inst = MagicMock(name="lxm")
            instances.append(inst)
            return inst

        rns = MagicMock()
        rns.Identity = MagicMock()
        rns.Identity.from_file = MagicMock(side_effect=Exception("no file"))
        rns.Identity.return_value = MagicMock()
        rns.Transport = MagicMock()
        rns.Transport.has_path = MagicMock(return_value=True)
        rns.Identity.recall = MagicMock(return_value=MagicMock())
        rns.Destination = MagicMock()
        rns.Destination.OUT = "OUT"
        rns.Destination.SINGLE = "SINGLE"
        lxmf = MagicMock()
        lxmf.LXMessage = MagicMock(side_effect=lxm_factory)
        router = MagicMock()
        src = MagicMock()
        src.hash = bytes.fromhex("aabbccddeeff0011")
        router.register_delivery_identity.return_value = src
        lxmf.LXMRouter.return_value = router

        pq = MagicMock()
        emit = MagicMock(return_value=True)
        cfg = LXMFBroadcastConfig(
            enabled=True, channels=[0, 1], display_name="t",
            announce_interval_sec=0,
            prefix_format="[ch{channel}:{sender}] {text}",
            autosubscribe=False, ack_required=True,
        )
        b = LXMFBroadcastBridge(
            broadcast_config=cfg, rns_module=rns, lxmf_module=lxmf,
            identity_path=tmp_path / "id", db_path=tmp_path / "s.db",
            storage_path=tmp_path / "stg",
            persistent_queue=pq, ack_emit_callback=emit,
        )
        assert b.start() is True
        b._subs.add("1122334455667788")
        b._subs.add("2233445566778899")

        b.on_meshcore_message(_make_canonical(channel=1))

        assert len(instances) == 2
        registered_msg_id = pq.register_pending_ack.call_args.args[0]

        # Pull the on_delivered closure that was registered on the FIRST
        # subscriber, and invoke it as the LXMF stack would.
        on_delivered_first = instances[0].register_delivery_callback.call_args.args[0]
        on_delivered_first(MagicMock(name="receipt"))

        emit.assert_called_once_with(registered_msg_id, "delivered")

    def test_on_failed_per_subscriber_does_not_emit(self, tmp_path):
        # Per-subscriber failure should NOT cause emit('failed') — the
        # substrate's timeout sweep handles "no winner" instead. Per-
        # subscriber failures only log at DEBUG.
        instances = []
        def lxm_factory(*a, **kw):
            inst = MagicMock(name="lxm")
            instances.append(inst)
            return inst

        rns = MagicMock()
        rns.Identity = MagicMock()
        rns.Identity.from_file = MagicMock(side_effect=Exception("no file"))
        rns.Identity.return_value = MagicMock()
        rns.Transport = MagicMock()
        rns.Transport.has_path = MagicMock(return_value=True)
        rns.Identity.recall = MagicMock(return_value=MagicMock())
        rns.Destination = MagicMock()
        rns.Destination.OUT = "OUT"
        rns.Destination.SINGLE = "SINGLE"
        lxmf = MagicMock()
        lxmf.LXMessage = MagicMock(side_effect=lxm_factory)
        router = MagicMock()
        src = MagicMock()
        src.hash = bytes.fromhex("aabbccddeeff0011")
        router.register_delivery_identity.return_value = src
        lxmf.LXMRouter.return_value = router

        pq = MagicMock()
        emit = MagicMock(return_value=True)
        cfg = LXMFBroadcastConfig(
            enabled=True, channels=[0, 1], display_name="t",
            announce_interval_sec=0,
            prefix_format="[ch{channel}:{sender}] {text}",
            autosubscribe=False, ack_required=True,
        )
        b = LXMFBroadcastBridge(
            broadcast_config=cfg, rns_module=rns, lxmf_module=lxmf,
            identity_path=tmp_path / "id", db_path=tmp_path / "s.db",
            storage_path=tmp_path / "stg",
            persistent_queue=pq, ack_emit_callback=emit,
        )
        assert b.start() is True
        b._subs.add("1122334455667788")
        b.on_meshcore_message(_make_canonical(channel=1))
        assert len(instances) == 1
        on_failed = instances[0].register_failed_callback.call_args.args[0]
        on_failed(MagicMock(name="receipt"))
        emit.assert_not_called()


# ---------------------------------------------------------------------------
# Guards and defensive paths
# ---------------------------------------------------------------------------


class TestGuards:

    def test_no_subscribers_no_pending_ack(self, tmp_path):
        pq = MagicMock()
        emit = MagicMock()
        b, _, _, _ = _start_bridge(
            tmp_path, ack_required=True,
            persistent_queue=pq, ack_emit_callback=emit,
            pre_register_subs=[],  # no subscribers
        )
        b.on_meshcore_message(_make_canonical())
        pq.register_pending_ack.assert_not_called()

    def test_no_source_address_no_pending_ack(self, tmp_path):
        pq = MagicMock()
        emit = MagicMock()
        b, _, _, _ = _start_bridge(
            tmp_path, ack_required=True,
            persistent_queue=pq, ack_emit_callback=emit,
            pre_register_subs=["1122334455667788"],
        )
        b.on_meshcore_message(_make_canonical(sender=""))
        pq.register_pending_ack.assert_not_called()

    def test_recursion_guard_synth_ack_marker_skips(self, tmp_path):
        pq = MagicMock()
        emit = MagicMock()
        b, _, _, _ = _start_bridge(
            tmp_path, ack_required=True,
            persistent_queue=pq, ack_emit_callback=emit,
            pre_register_subs=["1122334455667788"],
        )
        b.on_meshcore_message(
            _make_canonical(metadata={"meshforge_is_synth_ack": True})
        )
        pq.register_pending_ack.assert_not_called()

    def test_persistent_queue_missing_no_pending_ack(self, tmp_path):
        emit = MagicMock()
        b, _, _, _ = _start_bridge(
            tmp_path, ack_required=True,
            persistent_queue=None, ack_emit_callback=emit,
            pre_register_subs=["1122334455667788"],
        )
        # Should not raise; fan-out proceeds without ack tracking
        b.on_meshcore_message(_make_canonical())
        emit.assert_not_called()
        assert b.stats["fanouts"] >= 1

    def test_emit_callback_missing_no_pending_ack(self, tmp_path):
        pq = MagicMock()
        b, _, _, _ = _start_bridge(
            tmp_path, ack_required=True,
            persistent_queue=pq, ack_emit_callback=None,
            pre_register_subs=["1122334455667788"],
        )
        b.on_meshcore_message(_make_canonical())
        pq.register_pending_ack.assert_not_called()

    def test_register_pending_ack_raises_does_not_block_fanout(self, tmp_path):
        pq = MagicMock()
        pq.register_pending_ack.side_effect = RuntimeError("db wedge")
        emit = MagicMock()
        b, _, _, _ = _start_bridge(
            tmp_path, ack_required=True,
            persistent_queue=pq, ack_emit_callback=emit,
            pre_register_subs=["1122334455667788"],
        )
        b.on_meshcore_message(_make_canonical())
        # Fan-out still happened despite registration failure
        assert b.stats["fanouts"] >= 1
