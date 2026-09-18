"""RNS→Mesh must not re-radiate content that has already been on Meshtastic.

Live-caught 2026-09-18, about an hour after the radio-less RNS→Mesh leg was
repaired on meshanchor-server: 11 of that leg's first 20 bridges were real
ch2 user traffic returning to ch2 —

    ch2 RX -> moc MeshtasticBroadcastBridge (stamps "[meshtastic ch2:…]")
           -> LXMF fan-out -> subscriber's _process_rns_to_mesh
           -> meshtastic_egress -> moc ch2

Real airtime on a shared LongFast channel, re-saying what every listener
already heard. It needs a box that BOTH subscribes to another box's
Meshtastic fan-out AND can transmit on that channel — only a radio-less
gateway with meshtastic_egress does both, which is why MeshForge never saw
it (moc: 0 of 300 RNS→Mesh bridges carried the tag over 7 days).

``meshtastic_reemit_bridge`` has carried the SYMMETRIC guard since
2026-05-18, and its own comment names "_process_rns_to_mesh re-broadcast" as
the loop it closes. The guard simply was never written on this leg — the
same one-leg-of-two shape as the egress gate fixed the same night.
"""

import pytest
from unittest.mock import MagicMock, patch

from gateway.base_handler import is_rns_to_mesh_echo
from gateway.config import (
    ECHO_LOOP_INVARIANT_PREFIXES,
    MESH_WIRE_TAG_PREFIX,
    RNS_TO_MESH_ECHO_PREFIXES,
    MeshtasticEgressConfig,
)

from tests.test_rns_bridge import bridge  # noqa: F401 — shared fixture


class TestEchoVocabularyIsDerived:
    """The legs must not drift apart (honest_failure_modes #5)."""

    def test_every_shared_invariant_tag_is_covered(self):
        for tag in ECHO_LOOP_INVARIANT_PREFIXES:
            assert tag in RNS_TO_MESH_ECHO_PREFIXES, (
                f"{tag!r} is in the shared invariant list but this leg would "
                "re-radiate it — the two legs have drifted")

    def test_the_meshtastic_wire_tag_is_covered(self):
        """THE one this leg needs and the shared list does not carry: the
        re-emit leg STRIPS this tag rather than dropping on it."""
        assert MESH_WIRE_TAG_PREFIX in RNS_TO_MESH_ECHO_PREFIXES


class TestIsRnsToMeshEcho:

    @pytest.mark.parametrize("body", [
        "[meshtastic ch2:!58835c1f] test",          # THE observed echo
        "[meshtastic ch0:!abc] anything",
        "[MC:p4] hello",
        "[RNS:0f64] round-tripped",
        "[Mesh:moc] forwarded",
        "[MeshCore] broadcast",
        "[ch0: fanout",
        "[ch1: fanout",
        "   [meshtastic ch2:!x] leading whitespace tolerated",
    ])
    def test_tagged_content_is_an_echo(self, body):
        assert is_rns_to_mesh_echo(body) is True

    @pytest.mark.parametrize("body", [
        "plain operator text",
        "ACK seq=123 orig=canary",                  # tracer ACKs still flow
        "Tide Data for Hilo: ...",                  # a bot reply still flows
        "wx",                                       # a bare bot command
        "not a [meshtastic ch2:] tag mid-sentence",  # tag must LEAD
        "",
    ])
    def test_untagged_content_is_not_an_echo(self, body):
        assert is_rns_to_mesh_echo(body) is False

    def test_non_string_is_not_an_echo_and_does_not_raise(self):
        for junk in (None, 42, b"[meshtastic ch2:!x] bytes", ["x"]):
            assert is_rns_to_mesh_echo(junk) is False


@pytest.mark.usefixtures("allow_local_radio_tx")
class TestProcessRnsToMeshSuppressesEchoes:

    def _radioless(self, bridge):
        bridge._mesh_handler = None
        bridge.config.meshtastic_egress = MeshtasticEgressConfig(
            enabled=True, host="10.0.0.5", port=9443, tls=True,
            channel_index=2)
        return bridge

    def _msg(self, content):
        m = MagicMock()
        m.content = content
        m.source_id = "32ee84c3e0d18def"
        return m

    def test_the_observed_echo_never_reaches_the_radio(self, bridge):
        """THE pin, in the exact shape seen on the air."""
        self._radioless(bridge)
        with patch("gateway.meshtastic_protobuf_client.send_text_direct"
                   ) as send:
            bridge._process_rns_to_mesh(
                self._msg("[meshtastic ch2:!58835c1f] test"))
        send.assert_not_called()

    def test_suppression_leaves_a_witness(self, bridge):
        """A silent drop is the defect class this repo keeps paying for —
        the counter is how an operator sees it instead of wondering where a
        message went (honest_failure_modes #9)."""
        self._radioless(bridge)
        before = bridge.stats.get('rns_to_mesh_echo_suppressed', 0)
        with patch("gateway.meshtastic_protobuf_client.send_text_direct"):
            bridge._process_rns_to_mesh(
                self._msg("[meshtastic ch2:!58835c1f] test"))
        assert bridge.stats['rns_to_mesh_echo_suppressed'] == before + 1

    def test_untagged_content_still_bridges(self, bridge):
        """The guard must not silence the leg it protects — a bot reply has
        no bridge tag and must still reach the radio."""
        self._radioless(bridge)
        with patch("gateway.meshtastic_protobuf_client.send_text_direct",
                   return_value=True) as send:
            bridge._process_rns_to_mesh(self._msg("Tide Data for Hilo"))
        assert send.called, "guard suppressed legitimate RNS→Mesh traffic"
        assert bridge.stats.get('rns_to_mesh_echo_suppressed', 0) == 0


class TestMeshCoreToMeshIsUnaffected:
    """⚠️ The regression this guard could plausibly cause.

    "[MC:" is in the dropped vocabulary, and MeshCore->Meshtastic was
    repaired the same night. It survives because that path runs
    meshcore_bridge_mixin -> send_to_meshtastic DIRECTLY and never reaches
    _process_rns_to_mesh; only a "[MC:" body arriving over LXMF (a second
    copy of something already bridged) is dropped.
    """

    @pytest.mark.usefixtures("allow_local_radio_tx")
    def test_send_to_meshtastic_has_no_echo_guard(self, bridge):
        bridge._mesh_handler = None
        bridge.config.meshtastic_egress = MeshtasticEgressConfig(
            enabled=True, host="10.0.0.5", port=9443, tls=True,
            channel_index=2)
        with patch("gateway.meshtastic_protobuf_client.send_text_direct",
                   return_value=True) as send:
            ok = bridge.send_to_meshtastic("[MC:p4] hello from MeshCore")
        assert ok is True, "MeshCore→Meshtastic was broken by the echo guard"
        assert send.call_args.args[0] == "[MC:p4] hello from MeshCore"
