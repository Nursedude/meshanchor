"""Bridge matrix F16 (MA side; MeshForge lead): the Meshtastic/RNS → MeshCore
fan-out.

The bridge loop put EVERY Mesh→RNS message on the MeshCore queue, whatever
the 10-19 Mesh→RNS echo guard decided, so a bridge-tagged ``[RNS:…]`` line
heard on Meshtastic left for MeshCore as ``[Mesh:x] [RNS:…] …`` (split-horizon
looks only for MeshCore markers); a full queue dropped it at DEBUG with no
counter. The guard sits on the MeshCore leg's own facts — never on what the
Mesh→RNS leg decided — so an RNS outage cannot starve MeshCore.

MeshAnchor has outbound slot routing, so the MF ``can_broadcast`` refusal
(F5) is not ported; the -1 slot drop in _process_bridge_to_meshcore stays.

Run: python3 -m pytest tests/test_meshcore_fanout_truth.py -v
"""

import logging
import sys
from pathlib import Path
from queue import Queue
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from tests.test_rns_bridge import bridge  # noqa: F401 — fixture re-export


def _mesh_msg(content, source_id="!aabb0042"):
    from gateway.rns_bridge import BridgedMessage
    return BridgedMessage(source_network="meshtastic", source_id=source_id,
                          destination_id=None, content=content,
                          is_broadcast=True)


def _drain(q):
    out = []
    while not q.empty():
        out.append(q.get_nowait())
    return out


class TestEchoNeverFansOut:

    def test_rns_tagged_mesh_line_is_not_queued(self, bridge):
        bridge._meshcore_handler = MagicMock()
        bridge._fan_out_to_meshcore(_mesh_msg("[RNS:ab12] hello"))
        assert _drain(bridge._bridge_to_meshcore_queue) == []
        assert bridge.stats.get("meshcore_fanout_echo_suppressed") == 1

    def test_plain_mesh_line_is_queued(self, bridge):
        bridge._meshcore_handler = MagicMock()
        msg = _mesh_msg("hello")
        bridge._fan_out_to_meshcore(msg)
        assert _drain(bridge._bridge_to_meshcore_queue) == [msg]
        assert not bridge.stats.get("meshcore_fanout_echo_suppressed")

    def test_echo_guard_does_not_read_rns_down(self, bridge):
        bridge._meshcore_handler = MagicMock()
        bridge.health.get_subsystem_state.side_effect = AssertionError(
            "MeshCore fan-out must not depend on the RNS leg's state")
        msg = _mesh_msg("hello")
        bridge._fan_out_to_meshcore(msg)
        assert _drain(bridge._bridge_to_meshcore_queue) == [msg]


class TestOriginFilterRunsFirst:

    def test_mqtt_origin_tagged_line_is_not_counted_as_echo(self, bridge):
        """Twin parity with MF: an MQTT-origin line is never a MeshCore
        attempt, so it is filtered quietly, not counted as an echo."""
        from gateway.bridge_health import MessageOrigin
        bridge._meshcore_handler = MagicMock()
        msg = _mesh_msg("[RNS:ab12] hello")
        msg.origin = MessageOrigin.MQTT
        bridge._fan_out_to_meshcore(msg)
        assert _drain(bridge._bridge_to_meshcore_queue) == []
        assert not bridge.stats.get("meshcore_fanout_echo_suppressed")


class TestQueueFullIsCounted:

    def test_full_queue_counts_and_warns_once(self, bridge, caplog):
        bridge._meshcore_handler = MagicMock()
        bridge._bridge_to_meshcore_queue = Queue(maxsize=1)
        bridge._bridge_to_meshcore_queue.put_nowait(object())
        with caplog.at_level(logging.DEBUG, logger="gateway.meshcore_bridge_mixin"):
            for _ in range(3):
                bridge._fan_out_to_meshcore(_mesh_msg("hello"))
        assert bridge.stats.get("meshcore_fanout_dropped_queue_full") == 3
        warns = [r for r in caplog.records
                 if r.levelno == logging.WARNING and "queue full" in r.message]
        assert len(warns) == 1

    def test_no_handler_is_a_no_op(self, bridge):
        bridge._meshcore_handler = None
        bridge._fan_out_to_meshcore(_mesh_msg("hello"))
        assert _drain(bridge._bridge_to_meshcore_queue) == []


class TestBridgeLoopUsesTheOneFanOut:

    def test_no_raw_meshcore_put_in_rns_bridge(self):
        src = (Path(__file__).parent.parent / "src" / "gateway"
               / "rns_bridge.py").read_text()
        assert "_bridge_to_meshcore_queue.put_nowait" not in src
        assert src.count("self._fan_out_to_meshcore(msg)") == 2


class TestHealthFollowsTheOutcome:
    """F5 (MA-4/MA-5): ENQUEUE is not delivery. Health counts the TX worker's
    outcome (``to_meshcore``), never the hand-off to its queue."""

    def test_enqueue_is_not_counted_sent(self, bridge):
        from unittest.mock import patch
        bridge._meshcore_handler = MagicMock()
        bridge.config.meshcore.bridge_target_channel = 1
        with patch.object(bridge, "_resolve_bridge_target_channel", return_value=1), \
             patch.object(bridge, "send_to_meshcore", return_value=True) as send:
            bridge._process_bridge_to_meshcore(_mesh_msg("hello"))
        assert send.called, "the control: the message reached the send"
        bridge.health.record_message_sent.assert_not_called()

    def _worker(self, send_ok):
        import asyncio
        import threading
        from types import SimpleNamespace
        from unittest.mock import patch
        from gateway.canonical_message import CanonicalMessage
        from gateway.meshcore_handler import MeshCoreHandler
        cfg = SimpleNamespace(
            meshcore=SimpleNamespace(
                enabled=True, device_path="/dev/ttyUSB1", baud_rate=115200,
                connection_type="serial", tcp_host="localhost", tcp_port=4000,
                auto_fetch_messages=True, bridge_channels=True,
                bridge_dms=True, simulation_mode=True,
                channel_poll_interval_sec=5),
            meshtastic=SimpleNamespace(host="localhost", port=4403))
        health = MagicMock()
        h = MeshCoreHandler(config=cfg, node_tracker=MagicMock(),
                            health=health, stop_event=threading.Event(),
                            stats={"errors": 0}, stats_lock=threading.Lock(),
                            message_queue=Queue(maxsize=10))
        h._send_queue.put_nowait(CanonicalMessage(
            content="x", destination_address=None, is_broadcast=True,
            metadata={"channel": 1}))

        async def fake_send(*a, **k):
            return send_ok
        loop = asyncio.new_event_loop()
        try:
            with patch.object(h, "_send_message", side_effect=fake_send) as sm:
                loop.run_until_complete(h._process_outbound())
            assert sm.called, "the control: the worker reached the send"
        finally:
            loop.close()
        return health

    def test_worker_success_counts_sent(self):
        health = self._worker(True)
        health.record_message_sent.assert_called_once_with("to_meshcore")
        health.record_message_failed.assert_not_called()

    def test_worker_failure_counts_failed(self):
        health = self._worker(False)
        health.record_message_sent.assert_not_called()
        health.record_message_failed.assert_called_once_with(
            "to_meshcore", requeued=False)

    def test_dm_reply_enqueue_is_not_counted_sent(self, bridge):
        from unittest.mock import patch
        bridge._meshcore_handler = MagicMock()
        with patch.object(bridge, "send_to_meshcore", return_value=True) as send:
            bridge._process_bridge_to_meshcore(_mesh_msg("@p3 hi there"))
        assert send.call_args.kwargs.get("destination") == "p3", \
            "the control: the directed-DM leg handled it"
        bridge.health.record_message_sent.assert_not_called()
