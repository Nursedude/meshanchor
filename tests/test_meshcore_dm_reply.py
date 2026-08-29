"""Directed-DM reply leg (prototype 2026-08-28).

Pins the syn/ack contract: an "@<contact> <text>" reply arriving over the
bridge becomes a MeshCore DM (never a channel broadcast), its path ACK is
correlated back into a [MC:reply] notice, a bad address gets a negative notice
instead of a silent void, and both loop guards still outrank the DM parse —
MeshCore-origin echoes must never re-enter MeshCore as DMs either.
"""

import threading
from queue import Full, Queue
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from gateway.meshcore_dm_reply import (
    PendingDmAcks,
    RecentTextDedup,
    ack_code_hex,
    parse_directed_reply,
)
from gateway.meshcore_bridge_mixin import MeshCoreBridgeMixin


# ── the parser ──

class TestParseDirectedReply:
    def test_plain_at_reply(self):
        r = parse_directed_reply("@CME1 thanks, copy that")
        assert r.contact_query == "CME1"
        assert r.reply_text == "thanks, copy that"
        assert r.origin_label == ""

    def test_peels_wire_tags_and_captures_mesh_sender(self):
        r = parse_directed_reply("[RNS:627f] [meshtastic ch2:!abc123] @p4 wx?")
        assert r.contact_query == "p4"
        assert r.reply_text == "wx?"
        assert r.origin_label == "!abc123"

    def test_mesh_bridge_tag_form_captures_sender(self):
        r = parse_directed_reply("[Mesh:KH6ABC] @CME1 aloha")
        assert r.origin_label == "KH6ABC"

    def test_non_reply_is_none(self):
        assert parse_directed_reply("just chatting about @things") is None
        assert parse_directed_reply("[RNS:627f] plain text") is None
        assert parse_directed_reply("") is None
        assert parse_directed_reply(None) is None

    def test_bare_at_name_without_text_is_none(self):
        assert parse_directed_reply("@CME1") is None
        assert parse_directed_reply("@CME1   ") is None

    def test_email_like_token_is_not_a_reply(self):
        assert parse_directed_reply("mail me user@example.com ok") is None

    def test_tag_peeling_is_bounded(self):
        content = "[a] " * 10 + "@CME1 hi"
        # More tags than the bound: the parse honestly fails closed (falls
        # through to the channel path) rather than looping.
        assert parse_directed_reply(content) is None

    def test_pubkey_prefix_address(self):
        r = parse_directed_reply("@4802ed93 got your message")
        assert r.contact_query == "4802ed93"


# ── the pending-ack registry ──

class TestPendingDmAcks:
    def test_register_and_pop(self):
        p = PendingDmAcks()
        p.register("AB12", {"contact": "CME1"}, timeout_s=30)
        assert p.pop("ab12") == {"contact": "CME1"}
        assert p.pop("ab12") is None            # claimed once

    def test_expired_entry_ages_out(self, monkeypatch):
        import gateway.meshcore_dm_reply as mod
        now = [1000.0]
        monkeypatch.setattr(mod.time, "monotonic", lambda: now[0])
        p = PendingDmAcks()
        p.register("aa", {"contact": "x"}, timeout_s=5)
        now[0] += 120
        assert p.pop("aa") is None

    def test_cap_evicts_oldest_not_newest(self):
        p = PendingDmAcks(max_pending=2)
        p.register("a1", {"n": 1}, timeout_s=10)
        p.register("a2", {"n": 2}, timeout_s=20)
        p.register("a3", {"n": 3}, timeout_s=30)
        assert p.pop("a1") is None
        assert p.pop("a3") == {"n": 3}

    def test_ack_code_hex_shapes(self):
        assert ack_code_hex(b"\x48\x02\xed\x93") == "4802ed93"
        assert ack_code_hex("4802ED93") == "4802ed93"
        assert ack_code_hex(None) == ""
        assert ack_code_hex(12345) == ""


# ── the dedup window ──

class TestRecentTextDedup:
    def test_second_copy_inside_window_is_seen(self):
        d = RecentTextDedup(window_s=60)
        assert d.seen("CME1", "hi") is False
        assert d.seen("CME1", "hi") is True
        assert d.seen("CME1", "different") is False
        assert d.seen("other", "hi") is False

    def test_copy_outside_window_is_fresh(self, monkeypatch):
        import gateway.meshcore_dm_reply as mod
        now = [50.0]
        monkeypatch.setattr(mod.time, "monotonic", lambda: now[0])
        d = RecentTextDedup(window_s=60)
        assert d.seen("CME1", "hi") is False
        now[0] += 120
        assert d.seen("CME1", "hi") is False


# ── the mixin routing leg ──

class _Host(MeshCoreBridgeMixin):
    """Minimal host: just the attrs _process_bridge_to_meshcore touches."""

    def __init__(self, dm_replies_enabled=True, target_channel=1):
        self.config = SimpleNamespace(
            meshcore=SimpleNamespace(
                dm_replies_enabled=dm_replies_enabled,
                bridge_target_channel=target_channel,
            ),
            meshtastic_reemit=None,
        )
        self.stats = {'errors': 0}
        self._stats_lock = threading.Lock()
        self.health = MagicMock()
        self._meshcore_handler = MagicMock()
        self._meshcore_handler.send_text = MagicMock(return_value=True)

    def _requeue_failed_message(self, msg, dest):
        return False


def _msg(content, source_network="rns", source_address="627fabcd"):
    return SimpleNamespace(
        source_network=source_network,
        source_address=source_address,
        content=content,
        is_broadcast=True,
        metadata={},
        via_internet=False,
    )


class TestMixinDmReplyRouting:
    def test_at_reply_goes_as_dm_not_channel(self):
        h = _Host()
        h._process_bridge_to_meshcore(
            _msg("[meshtastic ch2:!abc123] @CME1 copy that"))
        h._meshcore_handler.send_text.assert_called_once()
        args, kwargs = h._meshcore_handler.send_text.call_args
        assert args[1] == "CME1"                      # DM destination
        assert args[0] == "[RNS:!abc123] copy that"   # origin carried, tags gone
        assert kwargs["reply_ctx"] == {"contact": "CME1", "origin": "!abc123"}
        assert h.stats.get('meshcore_dm_reply_tx') == 1

    def test_non_reply_still_goes_to_channel(self):
        h = _Host()
        h._process_bridge_to_meshcore(_msg("plain chatter"))
        args, _ = h._meshcore_handler.send_text.call_args
        assert args[1] is None                        # broadcast
        assert args[2] == 1                           # configured slot

    def test_kill_switch_restores_channel_path(self):
        h = _Host(dm_replies_enabled=False)
        h._process_bridge_to_meshcore(_msg("@CME1 hello"))
        args, _ = h._meshcore_handler.send_text.call_args
        assert args[1] is None

    def test_meshcore_origin_echo_never_becomes_a_dm(self):
        """Split-horizon outranks the DM parse: a MeshCore-origin round-trip
        containing an @-shape must not be re-injected as a DM."""
        h = _Host()
        h._process_bridge_to_meshcore(_msg("[MC:p4] @CME1 hi"))
        h._meshcore_handler.send_text.assert_not_called()
        assert h.stats.get('meshcore_bridge_echo_loop_drop') == 1

    def test_duplicate_relay_copy_is_deduped(self):
        h = _Host()
        h._process_bridge_to_meshcore(
            _msg("[meshtastic ch2:!abc123] @CME1 copy", source_address="627f"))
        h._process_bridge_to_meshcore(
            _msg("[meshtastic ch2:!abc123] @CME1 copy", source_address="58ce"))
        assert h._meshcore_handler.send_text.call_count == 1
        assert h.stats.get('meshcore_dm_reply_dedup_drop') == 1

    def test_dm_text_truncated_to_160_bytes(self):
        h = _Host()
        h._process_bridge_to_meshcore(_msg("@CME1 " + "x" * 400))
        args, _ = h._meshcore_handler.send_text.call_args
        assert len(args[0].encode('utf-8')) <= 160

    def test_reemit_owned_source_reply_still_becomes_a_dm(self):
        """THE first-field-test miss (2026-08-28): the operator's reply came
        from a reemit-owned gateway identity, the reemit-deferral guard ran
        before the DM parse, and the reply broadcast on the bridge channel.
        The DM parse now outranks the deferral."""
        owned = "32ee84c3e0d18def4dee1ab39c02db2a"
        h = _Host()
        h.config.meshtastic_reemit = SimpleNamespace(
            enabled=True, source_identities=[owned])
        h._process_bridge_to_meshcore(_msg(
            "[meshtastic ch2:!b29fa244] @CME1 hi",
            source_address=owned))
        h._meshcore_handler.send_text.assert_called_once()
        args, kwargs = h._meshcore_handler.send_text.call_args
        assert args[1] == "CME1"
        assert kwargs["reply_ctx"]["origin"] == "!b29fa244"

    def test_reemit_owned_non_reply_still_defers(self):
        """The reply-doubling guard must keep working for ordinary owned-
        source traffic — only ADDRESSED replies outrank it."""
        owned = "32ee84c3e0d18def4dee1ab39c02db2a"
        h = _Host()
        h.config.meshtastic_reemit = SimpleNamespace(
            enabled=True, source_identities=[owned])
        h._process_bridge_to_meshcore(_msg(
            "[meshtastic ch2:!b29fa244] plain chatter",
            source_address=owned))
        h._meshcore_handler.send_text.assert_not_called()
        assert h.stats.get('meshcore_bridge_reemit_dedup_drop') == 1


# ── the reemit bridge's matching skip ──

class TestReemitDirectedReplySkip:
    OWNED = "32ee84c3e0d18def4dee1ab39c02db2a"

    def _bridge(self, handler, meshcore_config=None):
        from gateway.config import MeshtasticReemitConfig
        from gateway.meshtastic_reemit_bridge import MeshtasticReemitBridge
        cfg = MeshtasticReemitConfig(
            enabled=True, source_identities=[self.OWNED], target_channel=1)
        b = MeshtasticReemitBridge(cfg, handler_getter=lambda: handler,
                                   meshcore_config=meshcore_config)
        b.start()
        return b

    def test_directed_reply_is_skipped_not_reemitted(self):
        handler = MagicMock()
        handler.send_text = MagicMock(return_value=True)
        b = self._bridge(handler)
        ok = b.on_lxmf_message(
            self.OWNED, "[meshtastic ch2:!b29fa244] @CME1 hi")
        assert ok is False
        handler.send_text.assert_not_called()
        assert b.stats["skipped_directed_reply"] == 1

    def test_kill_switch_off_restores_channel_reemit(self):
        """Disabling dm_replies must restore old behavior at BOTH legs —
        the reemit skip honors the same flag as the DM leg."""
        handler = MagicMock()
        handler.send_text = MagicMock(return_value=True)
        b = self._bridge(handler, meshcore_config=SimpleNamespace(
            dm_replies_enabled=False))
        ok = b.on_lxmf_message(
            self.OWNED, "[meshtastic ch2:!b29fa244] @CME1 hi")
        assert ok is True
        handler.send_text.assert_called_once()

    def test_non_reply_still_reemits(self):
        handler = MagicMock()
        handler.send_text = MagicMock(return_value=True)
        b = self._bridge(handler)
        ok = b.on_lxmf_message(
            self.OWNED, "[meshtastic ch2:!b29fa244] plain chatter")
        assert ok is True
        handler.send_text.assert_called_once()


# ── handler-side ack correlation (thin methods, isolated instance) ──

def _bare_handler():
    """MeshCoreHandler without __init__ — only the attrs the ack path uses."""
    from gateway.meshcore_handler import MeshCoreHandler
    h = MeshCoreHandler.__new__(MeshCoreHandler)
    h.stats = {}
    h._stats_lock = threading.Lock()
    h._dm_acks = PendingDmAcks()
    h._message_queue = Queue(maxsize=10)
    h._message_callback = None
    return h


class TestHandlerAckCorrelation:
    def test_watch_then_ack_emits_confirmed_notice(self):
        import asyncio
        h = _bare_handler()
        send_evt = SimpleNamespace(payload={
            "expected_ack": b"\x48\x02\xed\x93", "suggested_timeout": 2970})
        h._register_dm_ack_watch(send_evt, "CME1",
                                 {"contact": "CME1", "origin": "!abc123"})
        assert h.stats.get('meshcore_dm_ack_watch') == 1
        ack_evt = SimpleNamespace(payload={"code": "4802ed93"})
        asyncio.run(h._on_ack(ack_evt))
        assert h.stats.get('meshcore_dm_ack_confirmed') == 1
        notice = h._message_queue.get_nowait()
        assert "✓" in notice.content and "CME1" in notice.content
        assert notice.source_address == "reply"     # bridges as [MC:reply] — never "ack": bots trigger on it (2026-08-29)
        assert notice.source_network == "meshcore"  # split-horizon safe

    def test_unmatched_ack_is_counted_but_emits_nothing(self):
        import asyncio
        h = _bare_handler()
        asyncio.run(h._on_ack(SimpleNamespace(payload={"code": "deadbeef"})))
        assert h.stats.get('meshcore_acks') == 1
        assert h._message_queue.empty()

    def test_send_result_without_expected_ack_is_witnessed_not_watched(self):
        h = _bare_handler()
        h._register_dm_ack_watch(SimpleNamespace(payload={}), "CME1",
                                 {"contact": "CME1", "origin": "x"})
        assert h.stats.get('meshcore_dm_ack_unwatchable') == 1
        assert len(h._dm_acks) == 0

    def test_notice_on_full_queue_leaves_a_witness(self):
        h = _bare_handler()
        h._message_queue = Queue(maxsize=1)
        h._message_queue.put_nowait("occupied")
        h._emit_dm_notice("✗ test", {"contact": "c", "origin": "o"})
        assert h.stats.get('meshcore_dm_notice_dropped_full') == 1


class TestMeshtasticNodeIdExclusion:
    """'@!hex' is Meshtastic addressing — the DM leg must not claim it
    (2026-08-29: every such parse was a guaranteed-✗ notice per bot ack)."""

    def test_bang_prefixed_target_is_not_a_directed_reply(self):
        assert parse_directed_reply("@!b29fa244 Testing 1,2,3") is None

    def test_bang_target_after_wire_tags_is_not_claimed(self):
        assert parse_directed_reply(
            "[RNS:!a2e95ba4] @!b29fa244 hi there") is None

    def test_plain_name_target_still_parses(self):
        r = parse_directed_reply("@51d12a51 good day")
        assert r is not None and r.contact_query == "51d12a51"


class TestLabWireFilter:
    """Fleet lab plumbing (PING/ACK wire shapes) stays off MC channels —
    the hourly gateway_rt_canary ACK was fanning onto the bridge channel
    (2026-08-29). Regex-level pins for _LAB_WIRE_RE."""

    def _re(self):
        from gateway.meshcore_bridge_mixin import _LAB_WIRE_RE
        return _LAB_WIRE_RE

    def test_canary_ack_matches(self):
        assert self._re().match("ACK seq=1788031382 orig=canary-mesh")

    def test_tracer_ping_matches(self):
        assert self._re().match("PING seq=42 from=lab-tracer")

    def test_wire_tagged_canary_matches(self):
        assert self._re().match("[RNS:3dfbdb5d] ACK seq=1788031382 orig=canary-mesh")

    def test_human_text_mentioning_ack_does_not_match(self):
        assert self._re().match("ack") is None
        assert self._re().match("ACK-ACK! that was fast") is None
        assert self._re().match("did you get my ACK seq question?") is None

    def test_dm_reply_to_lab_shape_not_matched(self):
        assert self._re().match("@51d12a51 ACK seq stuff") is None
