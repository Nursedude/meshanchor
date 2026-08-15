"""Tests for the LXMF→MeshCore re-emit bridge plug-in.

Companion to ``test_lxmf_broadcast_bridge.py``. The re-emit bridge is the
inverse direction: instead of fanning MeshCore broadcasts out to LXMF
subscribers, it listens on the gateway's primary LXMRouter for LXMF DMs
from configured ``MeshtasticBroadcastBridge`` identities, strips the wire
prefix, and re-emits the body on a configured MeshCore channel.

Run: python3 -m pytest tests/test_meshtastic_reemit_bridge.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from gateway.config import GatewayConfig, MeshtasticReemitConfig
from gateway.meshtastic_reemit_bridge import (
    MeshtasticReemitBridge,
    _coerce_to_str,
    create_from_gateway_config,
)


MOC_BROADCAST_HASH = "32ee84c3e0d18def4dee1ab39c02db2a"
MOC3_BROADCAST_HASH = "aaa2365f799cc28aa7697df943096074"
STRANGER_HASH = "0011223344556677889900aabbccddee"


def _make_config(**overrides) -> MeshtasticReemitConfig:
    defaults = dict(
        enabled=True,
        source_identities=[MOC_BROADCAST_HASH, MOC3_BROADCAST_HASH],
        target_channel=0,
    )
    defaults.update(overrides)
    return MeshtasticReemitConfig(**defaults)


def _make_bridge(
    config: MeshtasticReemitConfig = None,
    handler: MagicMock = None,
) -> MeshtasticReemitBridge:
    cfg = config or _make_config()
    handler = handler if handler is not None else MagicMock()
    if handler is not None and not hasattr(handler, "send_text"):
        handler.send_text = MagicMock(return_value=True)
    elif handler is not None:
        # Default send_text to truthy so happy paths succeed.
        handler.send_text = MagicMock(return_value=True)
    bridge = MeshtasticReemitBridge(cfg, handler_getter=lambda: handler)
    bridge.start()
    return bridge


# ---------------------------------------------------------------------------
# _coerce_to_str
# ---------------------------------------------------------------------------


class TestCoerceToStr:
    def test_decodes_bytes(self):
        assert _coerce_to_str(b"hello") == "hello"

    def test_passes_str_through(self):
        assert _coerce_to_str("hello") == "hello"

    def test_none_returns_empty(self):
        assert _coerce_to_str(None) == ""

    def test_bytes_with_invalid_utf8_does_not_raise(self):
        # \xff is invalid UTF-8; we don't want hot-path exceptions.
        result = _coerce_to_str(b"\xff\xfegood")
        assert isinstance(result, str)
        assert "good" in result

    def test_bytearray_handled(self):
        assert _coerce_to_str(bytearray(b"hello")) == "hello"


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


class TestLifecycle:
    def test_start_when_enabled(self):
        bridge = MeshtasticReemitBridge(_make_config(), handler_getter=lambda: None)
        assert bridge.start() is True
        assert bridge.is_running is True
        bridge.stop()
        assert bridge.is_running is False

    def test_start_idempotent(self):
        bridge = MeshtasticReemitBridge(_make_config(), handler_getter=lambda: None)
        bridge.start()
        assert bridge.start() is True
        assert bridge.is_running is True

    def test_start_skipped_when_disabled(self):
        cfg = _make_config(enabled=False)
        bridge = MeshtasticReemitBridge(cfg, handler_getter=lambda: None)
        assert bridge.start() is False
        assert bridge.is_running is False

    def test_start_emits_warning_when_allowlist_empty(self, caplog):
        cfg = _make_config(source_identities=[])
        bridge = MeshtasticReemitBridge(cfg, handler_getter=lambda: None)
        import logging

        with caplog.at_level(logging.WARNING):
            bridge.start()
        assert any(
            "source_identities is empty" in rec.message for rec in caplog.records
        )

    def test_stop_idempotent(self):
        bridge = MeshtasticReemitBridge(_make_config(), handler_getter=lambda: None)
        bridge.start()
        bridge.stop()
        # Second call is a no-op (no exception).
        bridge.stop()
        assert bridge.is_running is False

    def test_on_lxmf_message_returns_false_when_not_running(self):
        handler = MagicMock()
        handler.send_text = MagicMock(return_value=True)
        bridge = MeshtasticReemitBridge(_make_config(), handler_getter=lambda: handler)
        # NOT started.
        result = bridge.on_lxmf_message(
            bytes.fromhex(MOC_BROADCAST_HASH),
            b"[meshtastic ch2:!abc] hi",
        )
        assert result is False
        handler.send_text.assert_not_called()


# ---------------------------------------------------------------------------
# Source-identity filter
# ---------------------------------------------------------------------------


class TestSourceIdentityFilter:
    def test_allowed_identity_re_emits(self):
        handler = MagicMock()
        bridge = _make_bridge(handler=handler)
        result = bridge.on_lxmf_message(
            bytes.fromhex(MOC_BROADCAST_HASH),
            b"[meshtastic ch2:!abcd1234] hello",
        )
        assert result is True
        handler.send_text.assert_called_once()
        assert bridge.stats["reemitted"] == 1
        assert bridge.stats["filtered_identity"] == 0

    def test_allowed_identity_moc3_re_emits(self):
        handler = MagicMock()
        bridge = _make_bridge(handler=handler)
        bridge.on_lxmf_message(
            bytes.fromhex(MOC3_BROADCAST_HASH),
            b"[meshtastic ch2:!abcd1234] hello from moc3",
        )
        handler.send_text.assert_called_once()

    def test_unknown_identity_filtered(self):
        handler = MagicMock()
        bridge = _make_bridge(handler=handler)
        result = bridge.on_lxmf_message(
            bytes.fromhex(STRANGER_HASH),
            b"[meshtastic ch2:!abcd1234] stranger",
        )
        assert result is False
        handler.send_text.assert_not_called()
        assert bridge.stats["filtered_identity"] == 1
        assert bridge.stats["reemitted"] == 0

    def test_identity_match_is_case_insensitive(self):
        handler = MagicMock()
        bridge = _make_bridge(handler=handler)
        # uppercase the inbound hash — must still match the lowercased
        # allowlist
        bridge.on_lxmf_message(
            MOC_BROADCAST_HASH.upper(),
            b"[meshtastic ch2:!abcd1234] hi",
        )
        handler.send_text.assert_called_once()

    def test_identity_match_accepts_hex_string_input(self):
        # source_hash arrives as bytes from LXMF; defensive support for
        # str input keeps the bridge usable from other call sites.
        handler = MagicMock()
        bridge = _make_bridge(handler=handler)
        bridge.on_lxmf_message(
            MOC_BROADCAST_HASH,
            b"[meshtastic ch2:!abcd1234] hi",
        )
        handler.send_text.assert_called_once()


# ---------------------------------------------------------------------------
# Synth-ACK loop guard
# ---------------------------------------------------------------------------


class TestSynthAckLoopGuard:
    def test_delivered_prefix_dropped(self):
        handler = MagicMock()
        bridge = _make_bridge(handler=handler)
        bridge.on_lxmf_message(
            bytes.fromhex(MOC_BROADCAST_HASH),
            b"[delivered: bcast-12345-ch0]",
        )
        handler.send_text.assert_not_called()
        assert bridge.stats["filtered_synth_ack"] == 1

    def test_timeout_prefix_dropped(self):
        handler = MagicMock()
        bridge = _make_bridge(handler=handler)
        bridge.on_lxmf_message(
            bytes.fromhex(MOC_BROADCAST_HASH),
            b"[timeout: bcast-12345-ch0]",
        )
        handler.send_text.assert_not_called()
        assert bridge.stats["filtered_synth_ack"] == 1

    def test_failed_prefix_dropped(self):
        handler = MagicMock()
        bridge = _make_bridge(handler=handler)
        bridge.on_lxmf_message(
            bytes.fromhex(MOC_BROADCAST_HASH),
            b"[failed: bcast-12345-ch0]",
        )
        handler.send_text.assert_not_called()

    def test_synth_ack_guard_runs_AFTER_identity_filter(self):
        """An ACK from a stranger increments filtered_identity, not
        filtered_synth_ack — identity is the first wall."""
        handler = MagicMock()
        bridge = _make_bridge(handler=handler)
        bridge.on_lxmf_message(
            bytes.fromhex(STRANGER_HASH),
            b"[delivered: x]",
        )
        assert bridge.stats["filtered_identity"] == 1
        assert bridge.stats["filtered_synth_ack"] == 0

    def test_custom_drop_prefix_list_honored(self):
        cfg = _make_config(drop_prefixes=["[banned-prefix:"])
        handler = MagicMock()
        bridge = _make_bridge(config=cfg, handler=handler)
        bridge.on_lxmf_message(
            bytes.fromhex(MOC_BROADCAST_HASH),
            b"[banned-prefix: hi]",
        )
        handler.send_text.assert_not_called()
        # And the previously-default [delivered: now passes since the
        # operator overrode the list.
        bridge.on_lxmf_message(
            bytes.fromhex(MOC_BROADCAST_HASH),
            b"[delivered: x]",
        )
        # The strip regex won't match "[delivered: x]" so it falls
        # through to format with sender=? text="[delivered: x]"
        handler.send_text.assert_called_once()


# ---------------------------------------------------------------------------
# Nested-bridge loop guard (post-strip)
# ---------------------------------------------------------------------------


class TestNestedBridgeLoopGuard:
    """The 2026-05-18 23:02 HST field-detected loop.

    A MeshCore p3 broadcast on "meshanchor" channel → LXMFBroadcastBridge
    fans out as LXMF DM to subscribers (moc, moc3) → moc's gateway
    bridges to Meshtastic ch2 as ``[MeshCore] [ch0:?] meshanchor p3:
    hello`` → moc3's Meshtastic radio RXs the broadcast →
    MeshtasticBroadcastBridge wraps in ``[meshtastic ch2:!moc] [MeshCore]
    ...`` and DMs to meshanchor-server → re-emit bridge picks it up.
    Without the post-strip guard, the re-emit puts the MeshCore-origin
    content back onto MeshCore Public, closing the loop.
    """

    def _round_tripped_content(self) -> bytes:
        return (
            b"[meshtastic ch2:!ebfa1b11] [MeshCore] [ch0:?] meshanchor p3: hello"
        )

    def test_field_detected_loop_dropped(self):
        handler = MagicMock()
        bridge = _make_bridge(handler=handler)
        result = bridge.on_lxmf_message(
            bytes.fromhex(MOC3_BROADCAST_HASH),
            self._round_tripped_content(),
        )
        assert result is False
        handler.send_text.assert_not_called()
        assert bridge.stats["filtered_nested_bridge"] == 1

    def test_meshcore_egress_loop_dropped(self):
        """MeshCore->Meshtastic egress carries a `[MC:xxxx]` prefix. If that
        message round-trips back via moc->LXMF, the re-emit MUST drop it
        (default nested_drop_prefixes includes "[MC:") so a MeshCore broadcast
        egressed to Meshtastic doesn't echo back onto MeshCore."""
        handler = MagicMock()
        bridge = _make_bridge(handler=handler)
        result = bridge.on_lxmf_message(
            bytes.fromhex(MOC_BROADCAST_HASH),
            b"[meshtastic ch2:!32962f10] [MC:abcd1234] hello from meshcore",
        )
        assert result is False
        handler.send_text.assert_not_called()
        assert bridge.stats["filtered_nested_bridge"] == 1

    def test_nested_guard_runs_AFTER_strip(self):
        """The wire content `[meshtastic ch2:...] [MeshCore] ...` has the
        Meshtastic prefix at index 0; without strip-first, the
        [MeshCore] check would never match. This pins the order."""
        handler = MagicMock()
        bridge = _make_bridge(handler=handler)
        bridge.on_lxmf_message(
            bytes.fromhex(MOC_BROADCAST_HASH),
            b"[meshtastic ch2:!abc] [MeshCore] some body",
        )
        handler.send_text.assert_not_called()
        assert bridge.stats["filtered_nested_bridge"] == 1
        # filtered_synth_ack should NOT have been bumped — the body
        # doesn't start with [delivered: or [timeout: or [failed: at
        # pre-strip time either.
        assert bridge.stats["filtered_synth_ack"] == 0

    def test_legitimate_meshtastic_broadcast_not_dropped(self):
        """A Meshtastic device user sending text that happens to mention
        '[MeshCore]' in the middle of their content (not at the start
        of the stripped body) MUST still re-emit. The guard only fires
        on prefix-match."""
        handler = MagicMock()
        bridge = _make_bridge(handler=handler)
        bridge.on_lxmf_message(
            bytes.fromhex(MOC_BROADCAST_HASH),
            b"[meshtastic ch2:!abc] talking about [MeshCore] vs Meshtastic",
        )
        handler.send_text.assert_called_once()
        assert bridge.stats["filtered_nested_bridge"] == 0
        assert bridge.stats["reemitted"] == 1

    def test_rns_injection_echo_dropped(self):
        """The documented echo: a MeshCore cmd round-trips through MeshForge's
        RNS→Mesh injection and comes back as `[RNS:627f] [ch0:p4] ...`. After
        strip the body starts with `[RNS:` → must drop (was leaking pre-2026-05-25
        because the guard only had [MeshCore]/[MC:)."""
        handler = MagicMock()
        bridge = _make_bridge(handler=handler)
        result = bridge.on_lxmf_message(
            bytes.fromhex(MOC_BROADCAST_HASH),
            b"[meshtastic ch2:!32962f10] [RNS:627f] [ch0:p4] meshanchor p4: wx",
        )
        assert result is False
        handler.send_text.assert_not_called()
        assert bridge.stats["filtered_nested_bridge"] == 1

    def test_lxmf_broadcast_channel_tag_echo_dropped(self):
        """`[ch0:` / `[ch1:` is the LXMFBroadcast fan-out tag; round-tripped
        content carrying it is an echo, not fresh MeshCore content."""
        handler = MagicMock()
        bridge = _make_bridge(handler=handler)
        bridge.on_lxmf_message(
            bytes.fromhex(MOC_BROADCAST_HASH),
            b"[meshtastic ch2:!abc] [ch0:p4] meshanchor p4: cmd",
        )
        handler.send_text.assert_not_called()
        assert bridge.stats["filtered_nested_bridge"] == 1

    def test_own_output_self_echo_dropped(self):
        """`[Mesh:` is THIS bridge's own output_format prefix. If its own
        re-emit round-trips back, drop it — closes the tightest self-echo loop."""
        handler = MagicMock()
        bridge = _make_bridge(handler=handler)
        bridge.on_lxmf_message(
            bytes.fromhex(MOC_BROADCAST_HASH),
            b"[meshtastic ch2:!abc] [Mesh:p4] wx",
        )
        handler.send_text.assert_not_called()
        assert bridge.stats["filtered_nested_bridge"] == 1

    def test_nested_guard_disabled_by_empty_list(self):
        """Operator can opt out by setting nested_drop_prefixes=[]."""
        cfg = _make_config(nested_drop_prefixes=[])
        handler = MagicMock()
        bridge = _make_bridge(config=cfg, handler=handler)
        bridge.on_lxmf_message(
            bytes.fromhex(MOC3_BROADCAST_HASH),
            self._round_tripped_content(),
        )
        # Now the message DOES re-emit (loop guard turned off).
        handler.send_text.assert_called_once()
        assert bridge.stats["filtered_nested_bridge"] == 0

    def test_custom_nested_prefix_honored(self):
        """Operator can add other bridge tags they want to never
        re-emit (e.g. [MQTT], [AREDN] when those bridges land)."""
        cfg = _make_config(nested_drop_prefixes=["[FUTURE]"])
        handler = MagicMock()
        bridge = _make_bridge(config=cfg, handler=handler)
        bridge.on_lxmf_message(
            bytes.fromhex(MOC_BROADCAST_HASH),
            b"[meshtastic ch2:!abc] [FUTURE] some body",
        )
        handler.send_text.assert_not_called()
        # And [MeshCore] now passes since the operator overrode the list.
        bridge.on_lxmf_message(
            bytes.fromhex(MOC_BROADCAST_HASH),
            b"[meshtastic ch2:!abc] [MeshCore] hi",
        )
        handler.send_text.assert_called_once()

    def test_nested_prefix_checked_on_unmatched_strip_too(self):
        """When the strip regex doesn't match, `stripped` is the whole
        body. The nested guard should still catch a leading bridge tag
        in that body — defensive against future prefix-format changes."""
        handler = MagicMock()
        bridge = _make_bridge(handler=handler)
        # No `[meshtastic ch...]` wrapper — body starts directly with
        # [MeshCore]. Could happen if MeshtasticBroadcastBridge ever
        # changes format. The guard MUST still catch it.
        bridge.on_lxmf_message(
            bytes.fromhex(MOC_BROADCAST_HASH),
            b"[MeshCore] [ch0:?] meshanchor p3: raw body no prefix",
        )
        handler.send_text.assert_not_called()
        assert bridge.stats["filtered_nested_bridge"] == 1


# ---------------------------------------------------------------------------
# Prefix stripping + output formatting
# ---------------------------------------------------------------------------


class TestPrefixStripAndFormat:
    def test_default_prefix_stripped_and_default_format_applied(self):
        handler = MagicMock()
        bridge = _make_bridge(handler=handler)
        bridge.on_lxmf_message(
            bytes.fromhex(MOC_BROADCAST_HASH),
            b"[meshtastic ch2:!32962f10] smoke v3",
        )
        handler.send_text.assert_called_once()
        body, kwargs = handler.send_text.call_args[0], handler.send_text.call_args[1]
        assert body[0] == "[Mesh:!32962f10] smoke v3"
        assert kwargs.get("destination") is None
        assert kwargs.get("channel") == 0

    @pytest.mark.parametrize("channel_idx", [0, 1, 2, 9])
    def test_strip_handles_multiple_channel_digits(self, channel_idx):
        handler = MagicMock()
        bridge = _make_bridge(handler=handler)
        bridge.on_lxmf_message(
            bytes.fromhex(MOC_BROADCAST_HASH),
            f"[meshtastic ch{channel_idx}:!abcd1234] body".encode(),
        )
        handler.send_text.assert_called_once()
        body = handler.send_text.call_args[0][0]
        assert "body" in body
        assert "!abcd1234" in body

    def test_unmatched_prefix_falls_through_with_placeholders(self):
        handler = MagicMock()
        bridge = _make_bridge(handler=handler)
        bridge.on_lxmf_message(
            bytes.fromhex(MOC_BROADCAST_HASH),
            b"raw body no prefix",
        )
        handler.send_text.assert_called_once()
        body = handler.send_text.call_args[0][0]
        # Sender falls back to "?" placeholder, whole body re-emitted.
        assert "raw body no prefix" in body

    def test_custom_output_format_used(self):
        cfg = _make_config(
            output_format="{sender}@ch{channel}> {text}",
        )
        handler = MagicMock()
        bridge = _make_bridge(config=cfg, handler=handler)
        bridge.on_lxmf_message(
            bytes.fromhex(MOC_BROADCAST_HASH),
            b"[meshtastic ch2:!abcd1234] hi",
        )
        body = handler.send_text.call_args[0][0]
        assert body == "!abcd1234@ch2> hi"

    def test_bad_output_format_falls_back_safely(self):
        cfg = _make_config(
            # {bogus} is not a known field; .format raises KeyError
            output_format="{bogus} {text}",
        )
        handler = MagicMock()
        bridge = _make_bridge(config=cfg, handler=handler)
        bridge.on_lxmf_message(
            bytes.fromhex(MOC_BROADCAST_HASH),
            b"[meshtastic ch2:!abcd1234] hi",
        )
        # Fallback shape: "[Mesh:{sender}] {text}"
        body = handler.send_text.call_args[0][0]
        assert body == "[Mesh:!abcd1234] hi"

    def test_invalid_strip_pattern_falls_back_to_default(self, caplog):
        cfg = _make_config(
            # unbalanced paren is invalid regex
            strip_pattern=r"^\[meshtastic ch(?P<channel>\d+",
        )
        import logging
        with caplog.at_level(logging.WARNING):
            handler = MagicMock()
            bridge = _make_bridge(config=cfg, handler=handler)
        # Default pattern still works.
        bridge.on_lxmf_message(
            bytes.fromhex(MOC_BROADCAST_HASH),
            b"[meshtastic ch2:!abcd1234] hi",
        )
        body = handler.send_text.call_args[0][0]
        assert body == "[Mesh:!abcd1234] hi"

    def test_target_channel_threaded_through_send_text(self):
        cfg = _make_config(target_channel=3)
        handler = MagicMock()
        bridge = _make_bridge(config=cfg, handler=handler)
        bridge.on_lxmf_message(
            bytes.fromhex(MOC_BROADCAST_HASH),
            b"[meshtastic ch2:!abcd1234] hi",
        )
        assert handler.send_text.call_args[1]["channel"] == 3


# ---------------------------------------------------------------------------
# Sender labels (operator-configured display names for known nodes)
# ---------------------------------------------------------------------------


class TestSenderLabels:
    def test_label_applied_to_matching_sender(self):
        cfg = _make_config(sender_labels={"!abcd1234": "🤖bot"})
        handler = MagicMock()
        bridge = _make_bridge(config=cfg, handler=handler)
        bridge.on_lxmf_message(
            bytes.fromhex(MOC_BROADCAST_HASH),
            b"[meshtastic ch2:!abcd1234] Tide Data",
        )
        body = handler.send_text.call_args[0][0]
        assert body == "[Mesh:\U0001f916bot] Tide Data"

    def test_wire_sender_case_insensitive(self):
        # Loader lowercases keys; the bridge lowercases the WIRE sender,
        # so a mixed-case sender token on the wire still matches.
        cfg = _make_config(sender_labels={"!abcd1234": "bot"})
        handler = MagicMock()
        bridge = _make_bridge(config=cfg, handler=handler)
        bridge.on_lxmf_message(
            bytes.fromhex(MOC_BROADCAST_HASH),
            b"[meshtastic ch2:!ABCD1234] hi",
        )
        body = handler.send_text.call_args[0][0]
        assert body == "[Mesh:bot] hi"

    def test_unlabeled_sender_unchanged(self):
        cfg = _make_config(sender_labels={"!abcd1234": "bot"})
        handler = MagicMock()
        bridge = _make_bridge(config=cfg, handler=handler)
        bridge.on_lxmf_message(
            bytes.fromhex(MOC_BROADCAST_HASH),
            b"[meshtastic ch2:!deadbeef] hi",
        )
        body = handler.send_text.call_args[0][0]
        assert body == "[Mesh:!deadbeef] hi"

    def test_label_survives_bad_output_format_fallback(self):
        cfg = _make_config(
            output_format="{bogus} {text}",
            sender_labels={"!abcd1234": "bot"},
        )
        handler = MagicMock()
        bridge = _make_bridge(config=cfg, handler=handler)
        bridge.on_lxmf_message(
            bytes.fromhex(MOC_BROADCAST_HASH),
            b"[meshtastic ch2:!abcd1234] hi",
        )
        body = handler.send_text.call_args[0][0]
        assert body == "[Mesh:bot] hi"

    def test_no_labels_config_is_noop(self):
        handler = MagicMock()
        bridge = _make_bridge(handler=handler)
        bridge.on_lxmf_message(
            bytes.fromhex(MOC_BROADCAST_HASH),
            b"[meshtastic ch2:!abcd1234] hi",
        )
        body = handler.send_text.call_args[0][0]
        assert body == "[Mesh:!abcd1234] hi"

    def test_loader_coerces_mistyped_labels_to_empty_with_warning(self, caplog):
        import logging
        with caplog.at_level(logging.WARNING):
            out = GatewayConfig._coerce_sender_labels(["!abcd1234", "bot"])
        assert out == {}
        assert any("sender_labels" in r.message for r in caplog.records)

    def test_loader_lowercases_keys_and_none_is_empty(self):
        assert GatewayConfig._coerce_sender_labels(None) == {}
        out = GatewayConfig._coerce_sender_labels({"!ABCD1234": "Bot"})
        assert out == {"!abcd1234": "Bot"}


# ---------------------------------------------------------------------------
# Handler resolution
# ---------------------------------------------------------------------------


class TestHandlerResolution:
    def test_no_active_handler_drops_message_gracefully(self):
        # handler_getter returns None — MeshCore not connected.
        cfg = _make_config()
        bridge = MeshtasticReemitBridge(cfg, handler_getter=lambda: None)
        bridge.start()
        result = bridge.on_lxmf_message(
            bytes.fromhex(MOC_BROADCAST_HASH),
            b"[meshtastic ch2:!abcd1234] hi",
        )
        assert result is False
        assert bridge.stats["handler_unavailable"] == 1
        assert bridge.stats["reemitted"] == 0

    def test_send_text_failure_counted_as_error(self):
        handler = MagicMock()
        handler.send_text = MagicMock(return_value=False)
        cfg = _make_config()
        bridge = MeshtasticReemitBridge(cfg, handler_getter=lambda: handler)
        bridge.start()
        result = bridge.on_lxmf_message(
            bytes.fromhex(MOC_BROADCAST_HASH),
            b"[meshtastic ch2:!abcd1234] hi",
        )
        assert result is False
        assert bridge.stats["errors"] == 1
        assert bridge.stats["reemitted"] == 0

    def test_send_text_failure_logs_warning(self, caplog):
        """A send_text→False reemit drop must surface a WARNING, not be
        counted silently (observability gap closed 2026-05-24)."""
        import logging
        handler = MagicMock()
        handler.send_text = MagicMock(return_value=False)
        cfg = _make_config()
        bridge = MeshtasticReemitBridge(cfg, handler_getter=lambda: handler)
        bridge.start()
        with caplog.at_level(logging.WARNING,
                             logger="gateway.meshtastic_reemit_bridge"):
            bridge.on_lxmf_message(
                bytes.fromhex(MOC_BROADCAST_HASH),
                b"[meshtastic ch2:!abcd1234] hi",
            )
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert any("returned False" in r.getMessage() for r in warnings)

    def test_send_text_exception_counted_as_error(self):
        handler = MagicMock()
        handler.send_text = MagicMock(side_effect=RuntimeError("boom"))
        cfg = _make_config()
        bridge = MeshtasticReemitBridge(cfg, handler_getter=lambda: handler)
        bridge.start()
        result = bridge.on_lxmf_message(
            bytes.fromhex(MOC_BROADCAST_HASH),
            b"[meshtastic ch2:!abcd1234] hi",
        )
        assert result is False
        assert bridge.stats["errors"] == 1


# ---------------------------------------------------------------------------
# Empty / malformed inputs
# ---------------------------------------------------------------------------


class TestEmptyOrMalformed:
    def test_empty_content_filtered(self):
        handler = MagicMock()
        bridge = _make_bridge(handler=handler)
        bridge.on_lxmf_message(bytes.fromhex(MOC_BROADCAST_HASH), b"")
        handler.send_text.assert_not_called()
        assert bridge.stats["filtered_empty"] == 1

    def test_none_content_filtered(self):
        handler = MagicMock()
        bridge = _make_bridge(handler=handler)
        bridge.on_lxmf_message(bytes.fromhex(MOC_BROADCAST_HASH), None)
        handler.send_text.assert_not_called()
        assert bridge.stats["filtered_empty"] == 1


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------


class TestStatus:
    def test_status_shape(self):
        bridge = _make_bridge()
        status = bridge.get_status()
        assert status["running"] is True
        assert status["enabled"] is True
        assert MOC_BROADCAST_HASH in status["source_identities"]
        assert MOC3_BROADCAST_HASH in status["source_identities"]
        assert status["target_channel"] == 0
        assert "stats" in status
        for key in (
            "received",
            "filtered_identity",
            "filtered_synth_ack",
            "filtered_nested_bridge",
            "filtered_empty",
            "reemitted",
            "errors",
            "handler_unavailable",
        ):
            assert key in status["stats"]
        assert "nested_drop_prefixes" in status

    def test_stats_increment_through_lifecycle(self):
        handler = MagicMock()
        bridge = _make_bridge(handler=handler)
        bridge.on_lxmf_message(
            bytes.fromhex(MOC_BROADCAST_HASH),
            b"[meshtastic ch2:!abcd1234] hi",
        )
        bridge.on_lxmf_message(bytes.fromhex(STRANGER_HASH), b"x")
        status = bridge.get_status()
        assert status["stats"]["received"] == 2
        assert status["stats"]["reemitted"] == 1
        assert status["stats"]["filtered_identity"] == 1


# ---------------------------------------------------------------------------
# Factory + config gating
# ---------------------------------------------------------------------------


class TestFactory:
    def test_factory_returns_none_when_disabled(self):
        cfg = GatewayConfig()
        cfg.meshtastic_reemit = MeshtasticReemitConfig(enabled=False)
        assert create_from_gateway_config(cfg) is None

    def test_factory_returns_bridge_when_enabled(self):
        cfg = GatewayConfig()
        cfg.meshtastic_reemit = MeshtasticReemitConfig(
            enabled=True,
            source_identities=[MOC_BROADCAST_HASH],
            target_channel=0,
        )
        bridge = create_from_gateway_config(cfg)
        assert bridge is not None
        assert isinstance(bridge, MeshtasticReemitBridge)

    def test_factory_handles_missing_attr(self):
        # Sometimes test configs are built without the new attr — make
        # sure the factory doesn't AttributeError before checking enabled.
        class FakeConfig:
            pass
        assert create_from_gateway_config(FakeConfig()) is None


# ---------------------------------------------------------------------------
# Config round-trip
# ---------------------------------------------------------------------------


class TestConfigRoundTrip:
    def test_load_save_preserves_meshtastic_reemit(self, tmp_path, monkeypatch):
        # Redirect get_config_path to a temp dir so we don't clobber the
        # operator's gateway.json.
        from gateway import config as cfg_mod

        cfg_path = tmp_path / "gateway.json"
        monkeypatch.setattr(
            GatewayConfig, "get_config_path", classmethod(lambda cls: cfg_path)
        )

        cfg = GatewayConfig()
        cfg.meshtastic_reemit = MeshtasticReemitConfig(
            enabled=True,
            source_identities=[MOC_BROADCAST_HASH, MOC3_BROADCAST_HASH],
            target_channel=1,
            output_format="[X:{sender}] {text}",
            drop_prefixes=["[gone:"],
            nested_drop_prefixes=["[OldBridge]"],
        )
        cfg.save()

        loaded = GatewayConfig.load()
        assert loaded.meshtastic_reemit.enabled is True
        assert MOC_BROADCAST_HASH in loaded.meshtastic_reemit.source_identities
        assert MOC3_BROADCAST_HASH in loaded.meshtastic_reemit.source_identities
        assert loaded.meshtastic_reemit.target_channel == 1
        assert loaded.meshtastic_reemit.output_format == "[X:{sender}] {text}"
        assert loaded.meshtastic_reemit.drop_prefixes == ["[gone:"]
        # Echo-loop invariant (2026-05-26): a narrow nested_drop_prefixes
        # override keeps the operator's custom entries but is force-unioned
        # with the invariant bridge-tag prefixes — config drift must not be
        # able to resurrect the re-emit echo loop (the p4 self-echo). The
        # operator can ADD prefixes, never remove these.
        ndp = loaded.meshtastic_reemit.nested_drop_prefixes
        assert "[OldBridge]" in ndp        # operator's custom entry preserved
        for inv in ("[RNS:", "[ch0:", "[ch1:", "[Mesh:", "[MC:", "[MeshCore]"):
            assert inv in ndp, f"invariant prefix {inv} must be force-unioned"

    def test_load_with_missing_block_uses_defaults(self, tmp_path, monkeypatch):
        # Older gateway.json files don't have meshtastic_reemit at all —
        # the loader must fall back to the dataclass default (disabled).
        import json

        cfg_path = tmp_path / "gateway.json"
        cfg_path.write_text(json.dumps({"enabled": False}))
        monkeypatch.setattr(
            GatewayConfig, "get_config_path", classmethod(lambda cls: cfg_path)
        )
        loaded = GatewayConfig.load()
        assert loaded.meshtastic_reemit.enabled is False
        assert loaded.meshtastic_reemit.source_identities == []
        # Default nested guard kicks in automatically — operators who
        # don't know about the loop trap get it for free.
        assert "[MeshCore]" in loaded.meshtastic_reemit.nested_drop_prefixes

    def test_load_lowercases_source_identities(self, tmp_path, monkeypatch):
        import json

        cfg_path = tmp_path / "gateway.json"
        cfg_path.write_text(json.dumps({
            "meshtastic_reemit": {
                "enabled": True,
                "source_identities": [MOC_BROADCAST_HASH.upper()],
            },
        }))
        monkeypatch.setattr(
            GatewayConfig, "get_config_path", classmethod(lambda cls: cfg_path)
        )
        loaded = GatewayConfig.load()
        assert MOC_BROADCAST_HASH in loaded.meshtastic_reemit.source_identities


# ---------------------------------------------------------------------------
# Production handler_getter (covers lazy import path)
# ---------------------------------------------------------------------------


class TestProductionHandlerResolver:
    def test_resolver_falls_back_to_get_active_handler(self, monkeypatch):
        """When handler_getter is None, the bridge must import
        get_active_handler lazily and call it. We monkeypatch the symbol
        through to a sentinel to prove the resolver wires it correctly."""
        sentinel = MagicMock()
        sentinel.send_text = MagicMock(return_value=True)

        import gateway.meshcore_handler as mh

        monkeypatch.setattr(mh, "get_active_handler", lambda: sentinel)

        cfg = _make_config()
        bridge = MeshtasticReemitBridge(cfg)  # no handler_getter
        bridge.start()
        ok = bridge.on_lxmf_message(
            bytes.fromhex(MOC_BROADCAST_HASH),
            b"[meshtastic ch2:!abcd1234] hi",
        )
        assert ok is True
        sentinel.send_text.assert_called_once()
