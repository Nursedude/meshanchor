"""Tests for the LXMF broadcast bridge plug-in.

The bridge piggybacks on an already-running RNS/LXMF stack, so these
tests mock the router/RNS/LXMF modules directly. We exercise:

- SubscriberStore CRUD round-trips on a real SQLite file
- format_broadcast_text default + custom + bad template
- LXMFBroadcastBridge channel filtering / message-type filtering
- LXMFBroadcastBridge fan-out to subscribers
- LXMFBroadcastBridge subscription protocol (subscribe / unsubscribe / help)
- LXMFBroadcastBridge dispatch by destination_hash
- create_from_gateway_config gating

Run: python3 -m pytest tests/test_lxmf_broadcast_bridge.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from gateway.canonical_message import CanonicalMessage, MessageType, Protocol
from gateway.config import LXMFBroadcastConfig
from gateway.lxmf_broadcast_bridge import (
    LXMFBroadcastBridge,
    SubscriberStore,
    create_from_gateway_config,
    format_broadcast_text,
)


# ---------------------------------------------------------------------------
# SubscriberStore
# ---------------------------------------------------------------------------


class TestSubscriberStore:
    def test_add_and_list(self, tmp_path):
        store = SubscriberStore(tmp_path / "subs.db")
        assert store.add("aabbccdd11223344") is True
        subs = store.list_all()
        assert len(subs) == 1
        assert subs[0].lxmf_hash == "aabbccdd11223344"

    def test_add_idempotent(self, tmp_path):
        store = SubscriberStore(tmp_path / "subs.db")
        assert store.add("deadbeef") is True
        assert store.add("deadbeef") is False
        assert len(store.list_all()) == 1

    def test_add_normalises_case(self, tmp_path):
        store = SubscriberStore(tmp_path / "subs.db")
        store.add("AABBCCDD")
        assert store.add("aabbccdd") is False
        subs = store.list_all()
        assert subs[0].lxmf_hash == "aabbccdd"

    def test_remove(self, tmp_path):
        store = SubscriberStore(tmp_path / "subs.db")
        store.add("deadbeef")
        assert store.remove("deadbeef") is True
        assert store.remove("deadbeef") is False
        assert store.list_all() == []

    def test_persists_across_instances(self, tmp_path):
        path = tmp_path / "subs.db"
        SubscriberStore(path).add("cafebabe")
        store2 = SubscriberStore(path)
        assert any(s.lxmf_hash == "cafebabe" for s in store2.list_all())

    def test_mark_delivered_updates_timestamp(self, tmp_path):
        store = SubscriberStore(tmp_path / "subs.db")
        store.add("deadbeef")
        store.mark_delivered("deadbeef")
        sub = store.list_all()[0]
        assert sub.last_delivery is not None


# ---------------------------------------------------------------------------
# format_broadcast_text
# ---------------------------------------------------------------------------


def _make_canonical(
    *,
    text: str = "hello",
    channel: int = 0,
    sender: str = "abcdef",
    is_broadcast: bool = True,
    source_network: str = Protocol.MESHCORE.value,
    msg_type: MessageType = MessageType.TEXT,
) -> CanonicalMessage:
    return CanonicalMessage(
        source_network=source_network,
        source_address=sender,
        destination_address=None,
        content=text,
        message_type=msg_type,
        is_broadcast=is_broadcast,
        metadata={"channel": channel},
    )


class TestFormatBroadcastText:
    def test_default_format(self):
        msg = _make_canonical(text="ping", channel=1, sender="abcdef")
        out = format_broadcast_text(msg, "[ch{channel}:{sender}] {text}")
        assert out == "[ch1:abcdef] ping"

    def test_custom_format(self):
        msg = _make_canonical(text="ping", channel=2, sender="zzz")
        out = format_broadcast_text(msg, "{sender}@{channel}> {text}")
        assert out == "zzz@2> ping"

    def test_truncates_long_sender(self):
        msg = _make_canonical(sender="a" * 64)
        out = format_broadcast_text(msg, "{sender}: {text}")
        # Sender capped at 16 chars
        assert out.startswith("a" * 16 + ":")

    def test_bad_template_falls_back(self):
        msg = _make_canonical(text="ping", channel=1, sender="abc")
        # {missing} key is not in our format dict
        out = format_broadcast_text(msg, "[{missing}] {text}")
        assert "ping" in out
        assert out.startswith("[ch1:abc]")


# ---------------------------------------------------------------------------
# LXMFBroadcastBridge — fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_rns_lxmf():
    """Mock RNS / LXMF modules with the surface the bridge uses."""
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
    lxmf.LXMessage = MagicMock(name="LXMessage")

    return rns, lxmf


@pytest.fixture
def fake_router():
    router = MagicMock(name="LXMRouter")
    # register_delivery_identity returns an object with a .hash bytes attribute
    src = MagicMock()
    src.hash = bytes.fromhex("aabbccddeeff0011")
    router.register_delivery_identity.return_value = src
    return router


def _make_bridge(tmp_path, fake_router, fake_rns_lxmf, *, enabled=True, channels=None,
                 autosubscribe=False, prefix=None):
    rns, lxmf = fake_rns_lxmf
    cfg = LXMFBroadcastConfig(
        enabled=enabled,
        channels=channels if channels is not None else [0, 1],
        display_name="Test Broadcast",
        announce_interval_sec=0,  # disable announce thread
        prefix_format=prefix or "[ch{channel}:{sender}] {text}",
        autosubscribe=autosubscribe,
    )
    return LXMFBroadcastBridge(
        broadcast_config=cfg,
        lxmf_router=fake_router,
        rns_module=rns,
        lxmf_module=lxmf,
        identity_path=tmp_path / "identity",
        db_path=tmp_path / "subs.db",
    )


# ---------------------------------------------------------------------------
# LXMFBroadcastBridge — start / identity
# ---------------------------------------------------------------------------


class TestBridgeLifecycle:
    def test_start_registers_identity(self, tmp_path, fake_router, fake_rns_lxmf):
        b = _make_bridge(tmp_path, fake_router, fake_rns_lxmf)
        assert b.start() is True
        fake_router.register_delivery_identity.assert_called_once()
        # First announce fires synchronously on start
        fake_router.announce.assert_called_once()
        assert b.is_running is True
        assert b.destination_hash_hex == "aabbccddeeff0011"

    def test_start_without_router_fails(self, tmp_path, fake_rns_lxmf):
        rns, lxmf = fake_rns_lxmf
        cfg = LXMFBroadcastConfig(enabled=True, announce_interval_sec=0)
        b = LXMFBroadcastBridge(
            broadcast_config=cfg,
            lxmf_router=None,
            rns_module=rns,
            lxmf_module=lxmf,
            identity_path=tmp_path / "id",
            db_path=tmp_path / "subs.db",
        )
        assert b.start() is False
        assert b.is_running is False

    def test_stop_is_safe_when_not_running(self, tmp_path, fake_router, fake_rns_lxmf):
        b = _make_bridge(tmp_path, fake_router, fake_rns_lxmf)
        b.stop()  # should not raise


# ---------------------------------------------------------------------------
# LXMFBroadcastBridge — MeshCore RX filtering & fan-out
# ---------------------------------------------------------------------------


class TestMeshCoreFanout:
    def test_filters_non_meshcore(self, tmp_path, fake_router, fake_rns_lxmf):
        b = _make_bridge(tmp_path, fake_router, fake_rns_lxmf)
        b.start()
        # Subscriber present, but message is from RNS
        b._subs.add("deadbeef00112233")
        msg = _make_canonical(source_network=Protocol.RNS.value, text="hi")
        b.on_meshcore_message(msg)
        # No outbound LXMessage built
        rns, lxmf = fake_rns_lxmf
        lxmf.LXMessage.assert_not_called()
        assert b.stats["filtered_non_meshcore"] == 1

    def test_filters_non_broadcast(self, tmp_path, fake_router, fake_rns_lxmf):
        b = _make_bridge(tmp_path, fake_router, fake_rns_lxmf)
        b.start()
        b._subs.add("deadbeef00112233")
        msg = _make_canonical(is_broadcast=False)
        b.on_meshcore_message(msg)
        rns, lxmf = fake_rns_lxmf
        lxmf.LXMessage.assert_not_called()

    def test_filters_disallowed_channel(self, tmp_path, fake_router, fake_rns_lxmf):
        b = _make_bridge(tmp_path, fake_router, fake_rns_lxmf, channels=[0])
        b.start()
        b._subs.add("deadbeef00112233")
        msg = _make_canonical(channel=7, text="off-channel")
        b.on_meshcore_message(msg)
        rns, lxmf = fake_rns_lxmf
        lxmf.LXMessage.assert_not_called()
        assert b.stats["filtered_channel"] == 1

    def test_skips_empty_content(self, tmp_path, fake_router, fake_rns_lxmf):
        b = _make_bridge(tmp_path, fake_router, fake_rns_lxmf)
        b.start()
        b._subs.add("deadbeef00112233")
        msg = _make_canonical(text="")
        b.on_meshcore_message(msg)
        rns, lxmf = fake_rns_lxmf
        lxmf.LXMessage.assert_not_called()

    def test_fans_out_to_each_subscriber(self, tmp_path, fake_router, fake_rns_lxmf):
        b = _make_bridge(tmp_path, fake_router, fake_rns_lxmf)
        b.start()
        b._subs.add("aaaa000000000001")
        b._subs.add("aaaa000000000002")
        msg = _make_canonical(channel=0, text="ping", sender="alice123")
        b.on_meshcore_message(msg)
        rns, lxmf = fake_rns_lxmf
        # Two LXMessages built, one per subscriber
        assert lxmf.LXMessage.call_count == 2
        # Both queued via router.handle_outbound (initial start announce
        # already happened — handle_outbound is the fan-out signal)
        assert fake_router.handle_outbound.call_count == 2
        # Message body carries the formatted prefix + content
        first_call_args = lxmf.LXMessage.call_args_list[0].args
        assert "ping" in first_call_args[2]
        assert "alice123" in first_call_args[2]
        assert b.stats["fanouts"] == 2


# ---------------------------------------------------------------------------
# LXMFBroadcastBridge — subscription protocol
# ---------------------------------------------------------------------------


def _fake_lxmf_message(*, dest_hash: bytes, source_hash: bytes, body: str):
    msg = MagicMock()
    msg.destination_hash = dest_hash
    msg.source_hash = source_hash
    msg.content = body.encode("utf-8")
    msg.title = b"test"
    return msg


class TestSubscriptionProtocol:
    def test_ignores_other_destinations(self, tmp_path, fake_router, fake_rns_lxmf):
        b = _make_bridge(tmp_path, fake_router, fake_rns_lxmf)
        b.start()
        # Address a different identity
        msg = _fake_lxmf_message(
            dest_hash=bytes.fromhex("ffffffffffffffff"),
            source_hash=bytes.fromhex("aaaa000000000003"),
            body="subscribe",
        )
        b.on_lxmf_message(msg)
        assert b._subs.list_all() == []

    def test_subscribe_adds_subscriber(self, tmp_path, fake_router, fake_rns_lxmf):
        b = _make_bridge(tmp_path, fake_router, fake_rns_lxmf)
        b.start()
        source = bytes.fromhex("aaaa000000000003")
        msg = _fake_lxmf_message(
            dest_hash=b._destination_hash, source_hash=source, body="subscribe please"
        )
        b.on_lxmf_message(msg)
        hashes = [s.lxmf_hash for s in b._subs.list_all()]
        assert "aaaa000000000003" in hashes
        assert b.stats["subscribes"] == 1

    def test_unsubscribe_removes_subscriber(self, tmp_path, fake_router, fake_rns_lxmf):
        b = _make_bridge(tmp_path, fake_router, fake_rns_lxmf)
        b.start()
        b._subs.add("aaaa000000000003")
        msg = _fake_lxmf_message(
            dest_hash=b._destination_hash,
            source_hash=bytes.fromhex("aaaa000000000003"),
            body="unsubscribe",
        )
        b.on_lxmf_message(msg)
        assert b._subs.list_all() == []
        assert b.stats["unsubscribes"] == 1

    def test_unknown_verb_does_not_subscribe_by_default(
        self, tmp_path, fake_router, fake_rns_lxmf
    ):
        b = _make_bridge(tmp_path, fake_router, fake_rns_lxmf)
        b.start()
        msg = _fake_lxmf_message(
            dest_hash=b._destination_hash,
            source_hash=bytes.fromhex("aaaa000000000003"),
            body="hello world",
        )
        b.on_lxmf_message(msg)
        assert b._subs.list_all() == []

    def test_autosubscribe_adds_on_unknown_verb(
        self, tmp_path, fake_router, fake_rns_lxmf
    ):
        b = _make_bridge(tmp_path, fake_router, fake_rns_lxmf, autosubscribe=True)
        b.start()
        msg = _fake_lxmf_message(
            dest_hash=b._destination_hash,
            source_hash=bytes.fromhex("aaaa000000000004"),
            body="hi there",
        )
        b.on_lxmf_message(msg)
        hashes = [s.lxmf_hash for s in b._subs.list_all()]
        assert "aaaa000000000004" in hashes


# ---------------------------------------------------------------------------
# create_from_gateway_config gating
# ---------------------------------------------------------------------------


class TestFactory:
    def test_returns_none_when_disabled(self):
        gateway_config = MagicMock()
        gateway_config.lxmf_broadcast = LXMFBroadcastConfig(enabled=False)
        out = create_from_gateway_config(gateway_config, lxmf_router=MagicMock())
        assert out is None

    def test_returns_none_without_router(self):
        gateway_config = MagicMock()
        gateway_config.lxmf_broadcast = LXMFBroadcastConfig(enabled=True)
        assert create_from_gateway_config(gateway_config, lxmf_router=None) is None

    def test_returns_instance_when_enabled(self, tmp_path):
        # Override identity_file/db_file so we don't create files in the
        # user's real ~/.config during a unit test.
        gateway_config = MagicMock()
        gateway_config.lxmf_broadcast = LXMFBroadcastConfig(
            enabled=True,
            identity_file=str(tmp_path / "id"),
            db_file=str(tmp_path / "subs.db"),
        )
        bridge = create_from_gateway_config(gateway_config, lxmf_router=MagicMock())
        assert isinstance(bridge, LXMFBroadcastBridge)
