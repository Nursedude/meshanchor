"""A private message never leaves on a MeshCore CHANNEL (bridge matrix F2 + F7).

F7: the meshtastic Python library reports a broadcast's ``toId`` as
``'^all'`` (``meshtastic.BROADCAST_ADDR``), never ``'!ffffffff'`` — the TCP
handler compared against the latter, so every TCP-ingress broadcast was
labelled a DM. Broadcast is decided on the wire fact (``packet['to']``).

F2: a Meshtastic DM pulled off the Mesh→RNS queue was fanned onto the
MeshCore queue unconditionally and sent on the bridge channel. The
``@contact`` directed-reply leg (private → MeshCore DM) must keep working;
only private → CHANNEL is refused, with a witness counter. The disconnect
replay branch persists for CHANNEL replay, so it refuses the same content.
"""

import threading
from queue import Queue
from types import SimpleNamespace
from unittest.mock import MagicMock

from gateway.meshcore_bridge_mixin import MeshCoreBridgeMixin
from gateway.meshtastic_handler import MeshtasticHandler, packet_is_broadcast
from gateway.meshcore_bridge_mixin import private_to_channel
from gateway.rns_bridge import BridgedMessage


# ── F7: broadcast decided on the wire fact ──

class TestPacketIsBroadcast:
    def test_library_broadcast_shape(self):
        # what meshtastic.mesh_interface emits: to=BROADCAST_NUM, toId='^all'
        assert packet_is_broadcast({'to': 0xFFFFFFFF, 'toId': '^all'}) is True

    def test_caret_all_without_numeric(self):
        assert packet_is_broadcast({'toId': '^all'}) is True

    def test_legacy_bang_ffffffff_string(self):
        assert packet_is_broadcast({'toId': '!ffffffff'}) is True

    def test_directed(self):
        assert packet_is_broadcast({'to': 0x1234abcd, 'toId': '!1234abcd'}) is False

    def test_numeric_wins_over_string(self):
        assert packet_is_broadcast({'to': 0x1234abcd, 'toId': '^all'}) is False


def _bare_mesh_handler():
    h = MeshtasticHandler.__new__(MeshtasticHandler)
    h._message_queue = Queue(maxsize=10)
    h._should_bridge = None
    h._message_callback = None
    h._stats_lock = threading.Lock()
    h.stats = {'errors': 0}
    return h


class TestTcpIngressBroadcastLabel:
    def _ingest(self, packet):
        h = _bare_mesh_handler()
        h._handle_text_message(packet, {'payload': b'hi'}, '!a1b2c3d4')
        return h._message_queue.get_nowait()

    def test_library_broadcast_is_labelled_broadcast(self):
        msg = self._ingest({'to': 0xFFFFFFFF, 'toId': '^all', 'channel': 0})
        assert msg.is_broadcast is True

    def test_dm_is_labelled_dm(self):
        msg = self._ingest({'to': 0x1234abcd, 'toId': '!1234abcd', 'channel': 0})
        assert msg.is_broadcast is False


# ── F2: private → channel refused at the consumer ──

class _Host(MeshCoreBridgeMixin):
    def __init__(self):
        self.config = SimpleNamespace(
            meshcore=SimpleNamespace(dm_replies_enabled=True,
                                     bridge_target_channel=1),
            meshtastic_reemit=None,
        )
        self.stats = {'errors': 0}
        self._stats_lock = threading.Lock()
        self.health = MagicMock()
        self._meshcore_handler = MagicMock()
        self._meshcore_handler.send_text = MagicMock(return_value=True)

    def _requeue_failed_message(self, msg, dest, **kw):
        return False


def _mesh(content, is_broadcast):
    return BridgedMessage(source_network="meshtastic", source_id="!a1b2c3d4",
                          destination_id=None if is_broadcast else "!0000beef",
                          content=content, is_broadcast=is_broadcast)


class TestMeshtasticDmNeverOnChannel:
    def test_dm_is_refused_with_witness(self):
        h = _Host()
        h._process_bridge_to_meshcore(_mesh("my private note", is_broadcast=False))
        h._meshcore_handler.send_text.assert_not_called()
        assert h.stats.get('meshcore_bridge_private_drop') == 1
        h.health.record_message_failed.assert_called_once_with(
            "mesh_to_meshcore", requeued=False)

    def test_broadcast_still_goes_to_channel(self):
        h = _Host()
        h._process_bridge_to_meshcore(_mesh("hello all", is_broadcast=True))
        args, _ = h._meshcore_handler.send_text.call_args
        assert args[1] is None and args[2] == 1

    def test_directed_dm_reply_still_goes_as_dm(self):
        h = _Host()
        h._process_bridge_to_meshcore(_mesh("@CME1 copy that", is_broadcast=False))
        args, _ = h._meshcore_handler.send_text.call_args
        assert args[1] == "CME1"
        assert 'meshcore_bridge_private_drop' not in h.stats

    def test_rns_origin_is_not_a_meshtastic_dm(self):
        # RNS-origin BridgedMessages default is_broadcast=False; the bridge's
        # RNS→MC channel leg must not be swallowed by the DM gate.
        h = _Host()
        h._process_bridge_to_meshcore(BridgedMessage(
            source_network="rns", source_id="627fabcd" * 4, destination_id=None,
            content="from lxmf"))
        h._meshcore_handler.send_text.assert_called_once()


class TestReplayPredicate:
    """The disconnect branch persists for CHANNEL replay — same refusal."""

    def test_meshtastic_dm_is_private(self):
        assert private_to_channel(_mesh("secret", is_broadcast=False)) is True

    def test_directed_reply_is_private_any_origin(self):
        assert private_to_channel(BridgedMessage(
            source_network="rns", source_id="x", destination_id=None,
            content="@bob secret")) is True

    def test_broadcast_and_rns_chatter_are_not(self):
        assert private_to_channel(_mesh("hello", is_broadcast=True)) is False
        assert private_to_channel(BridgedMessage(
            source_network="rns", source_id="x", destination_id=None,
            content="plain")) is False

    def test_kill_switch_makes_at_shape_ordinary_text(self):
        assert private_to_channel(BridgedMessage(
            source_network="rns", source_id="x", destination_id=None,
            content="@bob hi"), directed_replies=False) is False
        # a Meshtastic DM stays private whatever the switch says
        assert private_to_channel(_mesh("@bob hi", is_broadcast=False),
                                  directed_replies=False) is True
