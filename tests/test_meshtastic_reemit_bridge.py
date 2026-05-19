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
            "filtered_empty",
            "reemitted",
            "errors",
            "handler_unavailable",
        ):
            assert key in status["stats"]

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
        )
        cfg.save()

        loaded = GatewayConfig.load()
        assert loaded.meshtastic_reemit.enabled is True
        assert MOC_BROADCAST_HASH in loaded.meshtastic_reemit.source_identities
        assert MOC3_BROADCAST_HASH in loaded.meshtastic_reemit.source_identities
        assert loaded.meshtastic_reemit.target_channel == 1
        assert loaded.meshtastic_reemit.output_format == "[X:{sender}] {text}"
        assert loaded.meshtastic_reemit.drop_prefixes == ["[gone:"]

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
