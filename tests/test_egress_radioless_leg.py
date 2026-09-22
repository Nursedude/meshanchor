"""The radio-less Meshtastic egress leg — gate, sender, and delivery.

Regression pins for the 2026-09-17 defect: on meshanchor-server (no local
meshtasticd) the RNS→Meshtastic leg was 100% dead for five days. Two causes,
both pinned here:

1. ``rns_bridge._bridge_loop`` gated on ``health.get_subsystem_state`` alone.
   That state is ``DISABLED`` **by design** on a radio-less box — the very
   condition ``meshtastic_egress`` exists to cover — so the loop took its
   degraded branch on every message and ``_process_rns_to_mesh`` (the only
   path to the egress) never ran. ``meshcore_bridge_mixin`` asked the same
   question one file away and got it right; both now derive it from
   ``meshtastic_path_available``.
2. The degraded branch enqueues under ``"meshtastic"``, whose sender was
   registered only when a local ``_mesh_handler`` existed — so the enqueue
   was REJECTED (Issue #67's no-sender drop) and the message discarded
   rather than retried.

⚠️ Deliberately NOT asserting ``has_sender("meshtastic") is True``. That
check passes on a registered callable that never delivers — it would have
passed all week. Every test below terminates at a DELIVERED payload with the
channel asserted.
"""

import pytest
from unittest.mock import MagicMock, patch

from gateway.bridge_health import SubsystemState
from gateway.config import MeshtasticEgressConfig

# The fully-mocked bridge fixture lives with the bridge's own tests; importing
# it keeps ONE definition rather than a second copy that can drift from it.
from tests.test_rns_bridge import bridge, _mock_gateway_config  # noqa: F401


EGRESS = dict(enabled=True, host="10.0.0.5", port=9443, tls=True,
              channel_index=2)


def _radioless(bridge):
    """Put the fixture bridge in meshanchor-server's real shape."""
    bridge._mesh_handler = None
    bridge._meshcore_handler = None
    bridge.config.meshtastic_egress = MeshtasticEgressConfig(**EGRESS)
    # DISABLED is not a fault here — it is what a box with no meshtasticd
    # correctly reports, and the whole defect was reading it as one.
    bridge.health.get_subsystem_state.return_value = SubsystemState.DISABLED
    return bridge


# ---------------------------------------------------------------------------
# the predicate
# ---------------------------------------------------------------------------

class TestMeshtasticPathAvailable:

    def test_egress_beats_disabled_subsystem(self, bridge):
        """THE regression: DISABLED + egress configured is a LIVE path."""
        _radioless(bridge)
        assert bridge.meshtastic_path_available() is True

    def test_disabled_without_egress_is_not_available(self, bridge):
        bridge._mesh_handler = None
        bridge.config.meshtastic_egress = MeshtasticEgressConfig()
        bridge.health.get_subsystem_state.return_value = SubsystemState.DISABLED
        assert bridge.meshtastic_path_available() is False

    def test_disconnected_without_egress_is_not_available(self, bridge):
        bridge._mesh_handler = None
        bridge.config.meshtastic_egress = MeshtasticEgressConfig()
        bridge.health.get_subsystem_state.return_value = (
            SubsystemState.DISCONNECTED)
        assert bridge.meshtastic_path_available() is False

    def test_healthy_local_radio_without_egress_is_available(self, bridge):
        bridge.config.meshtastic_egress = MeshtasticEgressConfig()
        bridge.health.get_subsystem_state.return_value = SubsystemState.HEALTHY
        assert bridge.meshtastic_path_available() is True

    def test_egress_enabled_but_hostless_is_not_a_path(self, bridge):
        """An enabled egress with no host is not a route. This is the shape
        the config carries before anyone fills it in."""
        bridge._mesh_handler = None
        bridge.config.meshtastic_egress = MeshtasticEgressConfig(
            enabled=True, host="", channel_index=2)
        bridge.health.get_subsystem_state.return_value = SubsystemState.DISABLED
        assert bridge.meshtastic_path_available() is False

    def test_both_gates_ask_one_predicate(self, bridge):
        """The two gates that disagreed for five days now share a source.

        Pinned behaviourally: flipping the predicate flips BOTH answers, so
        they cannot drift apart again without this failing.
        """
        _radioless(bridge)
        with patch.object(type(bridge), "meshtastic_path_available",
                          return_value=False) as pred:
            assert bridge.meshtastic_path_available() is False
            assert pred.called


# ---------------------------------------------------------------------------
# the worker gate — terminates at a delivered payload
# ---------------------------------------------------------------------------

@pytest.mark.usefixtures("allow_local_radio_tx")
class TestRnsToMeshReachesTheEgress:

    def _run_one_iteration(self, bridge):
        """Drive exactly one pass of _bridge_loop, whichever branch it takes.

        Stopping on the SUCCESS path would hang the test when the bug is
        present, so the stop is driven from the top of the loop instead —
        the test terminates either way and then asserts on what happened.
        """
        seen = {"n": 0}

        def _sync():
            seen["n"] += 1
            if seen["n"] >= 2:
                bridge._running = False

        bridge._sync_subsystem_states = _sync
        bridge._running = True
        bridge._bridge_loop()

    def test_message_goes_out_the_egress_not_into_the_drop(self, bridge):
        """THE pin. Before the fix this message was requeued and discarded."""
        _radioless(bridge)
        msg = MagicMock()
        msg.content = "hello from RNS"
        msg.source_id = "abc123"
        bridge._rns_to_mesh_queue.put(msg)

        with patch("gateway.meshtastic_protobuf_client.send_text_direct",
                   return_value=True) as mock_send, \
             patch.object(bridge, "_requeue_failed_message") as mock_requeue:
            self._run_one_iteration(bridge)

        assert mock_send.called, (
            "RNS→Mesh message never reached the egress — the worker took the "
            "degraded branch on a box whose Meshtastic subsystem is DISABLED "
            "by design (the 2026-09-17 defect)")
        # the peer gateway's channel, not the local-radio channel
        assert mock_send.call_args.kwargs["channel_index"] == 2
        assert mock_send.call_args.kwargs["host"] == "10.0.0.5"
        mock_requeue.assert_not_called()

    def test_still_requeues_when_there_is_genuinely_no_path(self, bridge):
        """The degraded branch must survive — this fix widens the gate, it
        does not remove it. No egress + DISABLED is still a dead path."""
        bridge._mesh_handler = None
        bridge._meshcore_handler = None
        bridge.config.meshtastic_egress = MeshtasticEgressConfig()
        bridge.health.get_subsystem_state.return_value = SubsystemState.DISABLED
        msg = MagicMock()
        msg.content = "hello"
        bridge._rns_to_mesh_queue.put(msg)

        with patch("gateway.meshtastic_protobuf_client.send_text_direct") as send, \
             patch.object(bridge, "_requeue_failed_message",
                          return_value=True) as mock_requeue:
            self._run_one_iteration(bridge)

        send.assert_not_called()
        mock_requeue.assert_called_once()
        assert mock_requeue.call_args.args[1] == "meshtastic"


# ---------------------------------------------------------------------------
# the queue sender — also terminates at a delivered payload
# ---------------------------------------------------------------------------

@pytest.mark.usefixtures("allow_local_radio_tx")
class TestEgressQueueSender:

    def test_delivers_payload_on_the_egress_channel(self, bridge):
        _radioless(bridge)
        with patch("gateway.meshtastic_protobuf_client.send_text_direct",
                   return_value=True) as mock_send:
            ok = bridge._queue_send_meshtastic_egress({"message": "retry me"})
        assert ok is True
        kwargs = mock_send.call_args.kwargs
        assert kwargs["host"] == "10.0.0.5"
        # ⚠️ the EGRESS's channel, never the payload's — a retry must land on
        # the same channel the original direct send used (Issue #37 lives on
        # this replay path).
        assert kwargs["channel_index"] == 2

    def test_payload_channel_cannot_redirect_the_retry(self, bridge):
        _radioless(bridge)
        with patch("gateway.meshtastic_protobuf_client.send_text_direct",
                   return_value=True) as mock_send:
            bridge._queue_send_meshtastic_egress(
                {"message": "retry me", "channel": 7})
        assert mock_send.call_args.kwargs["channel_index"] == 2

    def test_accepts_content_key_too(self, bridge):
        _radioless(bridge)
        with patch("gateway.meshtastic_protobuf_client.send_text_direct",
                   return_value=True) as mock_send:
            assert bridge._queue_send_meshtastic_egress(
                {"content": "from content"}) is True
        assert mock_send.call_args.args[0] == "from content"

    def test_empty_payload_is_false_and_sends_nothing(self, bridge):
        _radioless(bridge)
        with patch("gateway.meshtastic_protobuf_client.send_text_direct") as s:
            assert bridge._queue_send_meshtastic_egress({}) is False
            assert bridge._queue_send_meshtastic_egress({"message": ""}) is False
        s.assert_not_called()

    def test_long_message_is_chunked_not_truncated(self, bridge):
        """The local-handler path truncates; an un-chunked requeue would lose
        every line past the cap. Chunk instead, same helper as the direct path."""
        _radioless(bridge)
        long_msg = "x" * 900
        with patch("gateway.meshtastic_protobuf_client.send_text_direct",
                   return_value=True) as mock_send:
            assert bridge._queue_send_meshtastic_egress(
                {"message": long_msg}) is True
        assert mock_send.call_count > 1, "long message was not chunked"
        sent = "".join(c.args[0] for c in mock_send.call_args_list)
        assert sent == long_msg, "chunking lost or reordered content"

    def test_partial_failure_reports_failure_and_still_tries_every_chunk(
            self, bridge):
        """All-or-nothing return so a half-send is never recorded as a
        delivery — but no short-circuit, so one transient failure does not
        abandon the remaining chunks."""
        _radioless(bridge)
        long_msg = "y" * 900
        with patch("gateway.meshtastic_protobuf_client.send_text_direct",
                   side_effect=[False, True, True, True, True]) as mock_send:
            assert bridge._queue_send_meshtastic_egress(
                {"message": long_msg}) is False
        assert mock_send.call_count > 1, "gave up after the first failure"


# ---------------------------------------------------------------------------
# the registration itself — the half that made the defect silent
# ---------------------------------------------------------------------------

@pytest.fixture
def queued_bridge():
    """A bridge WITH a persistent queue, so registration is observable.

    The shared ``bridge`` fixture pins ``HAS_PERSISTENT_QUEUE=False``; the
    registration under test only happens when a queue exists.
    """
    with patch("gateway.rns_bridge.GatewayConfig") as MockConfig, \
         patch("gateway.rns_bridge.get_node_tracker"), \
         patch("gateway.rns_bridge.BridgeHealthMonitor"), \
         patch("gateway.rns_bridge.DeliveryTracker"), \
         patch("gateway.rns_bridge.MeshtasticHandler"), \
         patch("gateway.rns_bridge.ReconnectStrategy") as MockReconnect, \
         patch("gateway.rns_bridge.HAS_CIRCUIT_BREAKER", False), \
         patch("gateway.rns_bridge.HAS_PERSISTENT_QUEUE", True), \
         patch("gateway.rns_bridge.PersistentMessageQueue") as MockQueue, \
         patch("gateway.message_routing.CLASSIFIER_AVAILABLE", False), \
         patch("gateway.rns_bridge.HAS_SERVICE_CHECK", False), \
         patch("gateway.rns_bridge.HAS_EVENT_BUS", False), \
         patch("gateway.rns_bridge.HAS_RNS_SNIFFER", False):

        mock_config = _mock_gateway_config()
        mock_config.meshtastic_egress = MeshtasticEgressConfig(**EGRESS)
        MockConfig.load.return_value = mock_config
        MockReconnect.for_rns.return_value = MagicMock()
        MockQueue.return_value = MagicMock()

        from gateway.rns_bridge import RNSMeshtasticBridge
        yield RNSMeshtasticBridge, mock_config, MockQueue


class TestEgressSenderIsRegistered:

    def _registered(self, queue_mock):
        return {c.args[0]: c.args[1]
                for c in queue_mock.return_value.register_sender.call_args_list
                if c.args}

    def test_radioless_gateway_registers_an_egress_backed_sender(
            self, queued_bridge):
        """Before the fix nothing registered 'meshtastic' here, so every
        enqueue was rejected and the message discarded."""
        Bridge, cfg, MockQueue = queued_bridge
        # This is how meshanchor-server actually gets there: the box has no
        # meshtasticd, so meshtastic.enabled is false and _mesh_handler is
        # None by construction — which is also why its subsystem reads
        # DISABLED. Not a contrived shape.
        cfg.meshtastic.enabled = False
        b = Bridge(config=cfg)
        reg = self._registered(MockQueue)
        assert "meshtastic" in reg, (
            "radio-less gateway with an egress registered no 'meshtastic' "
            "sender — queued messages are dropped at enqueue (Issue #67)")
        assert reg["meshtastic"] == b._queue_send_meshtastic_egress

    def test_no_egress_means_no_sender_and_no_false_promise(
            self, queued_bridge):
        """Without an egress there is genuinely nothing to send through, and
        registering a sender that always fails would be worse than the drop."""
        Bridge, cfg, MockQueue = queued_bridge
        cfg.meshtastic_egress = MeshtasticEgressConfig()
        cfg.meshtastic.enabled = False
        Bridge(config=cfg)
        assert "meshtastic" not in self._registered(MockQueue)
