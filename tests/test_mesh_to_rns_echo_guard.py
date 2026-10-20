"""Mesh→RNS must not re-bridge content that has already crossed a bridge.

MeshAnchor #151 re-test (KN4YDL, 2026-10-19): the gateway sends to Meshtastic
("[MC:KN4YDL-mc2] Test 5" from MeshCore, "[RNS:f3b2] Test 1" from RNS), the
WiFi radio uplinks its OWN transmission as MQTT JSON, and the gateway bridged
it back to RNS a second time:

    Bridge MC→RNS [ch:1]: 2/2 dest(s) — [MC:KN4YDL-mc2] Test 5...
    Bridge Mesh→RNS: 2/2 dest(s) — [Mesh:e678] [MC:KN4YDL-mc2] Test 5...  <- echo

so every bridged message reached MeshChatX twice. MeshForge drops tagged text
on this leg (``is_already_bridged``, b01d8af); MeshAnchor carried only the
RNS→Mesh twin (``is_rns_to_mesh_echo``). The guard existed on one leg and not
its symmetric twin — honest_failure_modes #5, again.

The tester's ``message_filter`` did NOT stop it and never had: when the
classifier decides, a non-matching filter only lowers confidence. That is a
separate (open) decision; this guard does not depend on routing rules.
"""

import queue
import threading
from unittest.mock import MagicMock, patch

import pytest

from gateway.base_handler import is_already_bridged
from gateway.config import ECHO_LOOP_INVARIANT_PREFIXES, GatewayConfig

from tests.test_rns_bridge import bridge  # noqa: F401 — shared fixture
from tests.test_m2r_requeue_f4 import _broadcast, _connect


class TestIsAlreadyBridged:

    def test_vocabulary_is_the_shared_invariant_list(self):
        """Derived, never re-spelled (honest_failure_modes #5)."""
        for tag in ECHO_LOOP_INVARIANT_PREFIXES:
            assert is_already_bridged(f"{tag}x] body") is True

    @pytest.mark.parametrize("body", [
        "[MC:KN4YDL-mc2] Test 5",            # THE observed MC echo
        "[RNS:f3b2] Test 1",                 # THE observed RNS echo
        "[RNS:?f3b2] unverified source",
        "[Mesh:e678] [MC:x] nested",
        "  [MeshCore] leading whitespace",
    ])
    def test_tagged_content_is_bridged(self, body):
        assert is_already_bridged(body) is True

    @pytest.mark.parametrize("body", [
        "Test 5", "wx", "not a [MC:x] tag mid-sentence", "", None, 42,
    ])
    def test_untagged_or_junk_is_not(self, body):
        assert is_already_bridged(body) is False


def _with_destination(bridge):  # noqa: F811
    """A configured default destination — without one nothing is sent and a
    'not called' assertion passes on unguarded code (it did, first draft)."""
    _connect(bridge)
    bridge.config.rns.get_lxmf_destinations = MagicMock(
        return_value=["ab" * 16])


class TestProcessMeshToRnsSuppressesEchoes:

    def test_the_observed_echo_never_reaches_rns(self, bridge):  # noqa: F811
        _with_destination(bridge)
        with patch.object(bridge, "send_to_rns", return_value=True) as send:
            bridge._process_mesh_to_rns(_broadcast("[MC:KN4YDL-mc2] Test 5"))
        send.assert_not_called()

    def test_suppression_leaves_a_witness_and_is_not_an_attempt(
            self, bridge):  # noqa: F811
        """A silent drop is the defect class (honest_failure_modes #9). An
        echo was never an attempt, so attempted = delivered + dropped holds."""
        _connect(bridge)
        with patch.object(bridge, "send_to_rns", return_value=True):
            bridge._process_mesh_to_rns(_broadcast("[RNS:f3b2] Test 1"))
        assert bridge.stats.get('mesh_to_rns_echo_suppressed', 0) == 1
        assert bridge.stats.get('mesh_to_rns_attempted', 0) == 0

    def test_untagged_content_still_bridges(self, bridge):  # noqa: F811
        _with_destination(bridge)
        with patch.object(bridge, "send_to_rns", return_value=True) as send:
            bridge._process_mesh_to_rns(_broadcast("Test 5"))
        assert send.called, "guard suppressed legitimate Mesh→RNS traffic"
        assert bridge.stats.get('mesh_to_rns_echo_suppressed', 0) == 0


class TestTheMqttIngressEchoIsSuppressedEndToEnd:
    """The consumer path: the radio's uplinked JSON of our own transmission,
    through the REAL MQTT ingress and the REAL _process_mesh_to_rns."""

    def test_uplinked_own_transmission_is_not_rebridged(
            self, bridge):  # noqa: F811
        from gateway.mqtt_bridge_handler import MQTTBridgeHandler
        q = queue.Queue()
        handler = MQTTBridgeHandler(
            config=GatewayConfig(), node_tracker=MagicMock(),
            health=MagicMock(), stop_event=threading.Event(), stats={},
            stats_lock=threading.Lock(), message_queue=q)
        handler._should_bridge = lambda m: True   # classifier bridges it
        handler._bridge_text_message({
            "from": 0xdd03e678, "sender": "!dd03e678", "to": 0xFFFFFFFF,
            "id": 151, "channel": 6, "type": "text",
            "payload": {"text": "[MC:KN4YDL-mc2] Test 5"},
        }, "msh/2/json/mt-bridge/!dd03e678")
        msg = q.get_nowait()
        _with_destination(bridge)
        with patch.object(bridge, "send_to_rns", return_value=True) as send:
            bridge._process_mesh_to_rns(msg)
        send.assert_not_called()
        assert bridge.stats['mesh_to_rns_echo_suppressed'] == 1
