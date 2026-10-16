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


# ── 2026-10-16: the [unverified] marker disarmed this guard ──────────────────
# aca37676 (twin of MF cbf7457b, 2026-10-15) prepends "[unverified] " to an
# unverified LXMF source's content. After every daemon restart, the MF
# broadcast bridges (moc 32ee…, moc3 aaa2…) are unverified until they
# re-announce (≤10 min; no warm-up here, review I4), so their copies arrived
# as "[unverified] [meshtastic ch2:…] Sun" — no longer STARTING with a bridge
# tag — and went back onto moc's LongFast: 00:21:08→00:28:04, a bot answered a
# command twice (lehua mesh_bot "SunRise 06:14" then "06:15"; packet id
# 2603929968 matched on moc's radio). Two correct fixes disarmed each other.
# The guard judges what the SENDER put on the wire, never our own label on it,
# and the label is ONE constant shared with the stamper.

import time  # noqa: E402

UNVERIFIED_ECHO = "[unverified] [meshtastic ch2:!b03bb70c] Sun"


class TestUnverifiedMarkDoesNotDisarmTheGuard:

    def test_the_observed_unverified_echo_is_an_echo(self):
        assert is_rns_to_mesh_echo(UNVERIFIED_ECHO) is True

    def test_an_unverified_UNTAGGED_message_still_flows(self):
        """The mark alone is not a bridge tag: an unverified bot reply or
        operator text is still bridged (OPEN posture), marked."""
        assert is_rns_to_mesh_echo("[unverified] Tide Data for Hilo") is False

    def test_the_mark_is_one_constant_shared_with_the_stamper(self):
        """A re-spelled copy is how the guard and the stamper drift apart
        (honest_failure_modes #5): both must import the same name."""
        import inspect
        from gateway import base_handler, bridge_rns_events_mixin, rns_bridge
        assert base_handler.UNVERIFIED_MARK == "[unverified] "
        for mod in (bridge_rns_events_mixin, rns_bridge):
            src = inspect.getsource(mod)
            assert '"[unverified] "' not in src, (
                f"{mod.__name__} re-spells the mark; import UNVERIFIED_MARK")


@pytest.mark.usefixtures("allow_local_radio_tx")
class TestTheStampersRealOutputNeverReachesTheRadio:
    """The consumer path, end to end: an UNVERIFIED LXMF copy of a
    broadcast-bridge message goes through the REAL receive stamper and the
    REAL _process_rns_to_mesh. Whatever the stamper prepends next, this test
    fails before the loop re-opens."""

    def test_unverified_broadcast_bridge_copy_is_suppressed(self, bridge):
        bridge._mesh_handler = None
        bridge.config.meshtastic_egress = MeshtasticEgressConfig(
            enabled=True, host="10.0.0.5", port=9443, tls=True,
            channel_index=2)
        lxmf = MagicMock()
        lxmf.source_hash = bytes.fromhex("aaa2365f799cc28aa7697df943096074")
        lxmf.signature_validated = False         # not re-announced yet
        lxmf.unverified_reason = 0x01
        lxmf.content = "[meshtastic ch2:!b03bb70c] Sun"
        lxmf.title = ""
        lxmf.stamp = time.time()
        bridge._on_lxmf_receive(lxmf)
        msg = bridge._rns_to_mesh_queue.get(timeout=1)
        assert msg.content.startswith("[unverified] "), (
            "precondition: the stamper marks an unverified source")
        before = bridge.stats.get('rns_to_mesh_echo_suppressed', 0)
        with patch("gateway.meshtastic_protobuf_client.send_text_direct"
                   ) as send:
            bridge._process_rns_to_mesh(msg)
        send.assert_not_called()
        assert bridge.stats['rns_to_mesh_echo_suppressed'] == before + 1


def _unverified_through_the_stamper(bridge, content, source="aaa2365f799cc28aa7697df943096074"):
    bridge._mesh_handler = None
    bridge.config.meshtastic_egress = MeshtasticEgressConfig(
        enabled=True, host="10.0.0.5", port=9443, tls=True, channel_index=2)
    lxmf = MagicMock()
    lxmf.source_hash = bytes.fromhex(source)
    lxmf.signature_validated = False
    lxmf.unverified_reason = 0x01
    lxmf.content = content
    lxmf.title = ""
    lxmf.stamp = time.time()
    bridge._on_lxmf_receive(lxmf)
    return bridge._rns_to_mesh_queue.get(timeout=1)


@pytest.mark.usefixtures("allow_local_radio_tx")
class TestTheWireCarriesTheMarkOnce:
    """Non-author read of the echo fix (2026-10-16): the RF tag's "?" IS the
    mark, but the radio got "[RNS:?aaa2] [unverified] …" — the strip fed only
    the LOG line, the chunker took the raw content (live on moc's meshtasticd
    00:21:07, id 0x9b34d16d). The old pin read source text, not the wire."""

    def test_the_radio_gets_the_tag_not_both(self, bridge):
        msg = _unverified_through_the_stamper(bridge, "Tide Data for Hilo")
        with patch("gateway.meshtastic_protobuf_client.send_text_direct",
                   return_value=True) as send:
            bridge._process_rns_to_mesh(msg)
        assert send.called, "an unverified UNTAGGED reply must still flow"
        wire = " ".join(str(a) for a in send.call_args.args) + " " + " ".join(
            str(v) for v in send.call_args.kwargs.values())
        assert "[RNS:?aaa2] Tide Data for Hilo" in wire, wire
        assert "[unverified]" not in wire, wire


@pytest.mark.usefixtures("allow_local_radio_tx")
class TestDualPathDedupSeesPastTheMark:
    """Same review: the dual-path registry lookup took the raw content, so an
    unverified copy of something the local mesh_bridge already sent was not
    recognised (gated, default off on meshanchor-server — latent)."""

    def test_an_unverified_copy_of_a_sent_message_is_suppressed(self, bridge):
        from gateway.base_handler import get_rf_tx_registry
        text = "dual-path probe 2026-10-16 unverified"
        get_rf_tx_registry().register(text)
        with patch.object(type(bridge), "_dual_path_dedup_on",
                          return_value=True, create=True):
            msg = _unverified_through_the_stamper(
                bridge, text, source="32ee84c3799cc28aa7697df943096074")
            before = bridge.stats.get('rns_to_mesh_dual_path_suppressed', 0)
            with patch("gateway.meshtastic_protobuf_client.send_text_direct",
                       return_value=True) as send:
                bridge._process_rns_to_mesh(msg)
        assert not send.called, "dual-path dedup disarmed by the mark"
        assert bridge.stats['rns_to_mesh_dual_path_suppressed'] == before + 1
