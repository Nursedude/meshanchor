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
    STATE_DEAD,
    STATE_DEGRADED,
    STATE_HEALTHY,
    STATE_STALE,
    SubscriberStore,
    TIER_EXTERNAL,
    TIER_FEDERATION,
    TIER_LOCAL,
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


def _make_fake_lxmf(*, register_returns_source: bool = True,
                    source_hash_hex: str = "aabbccddeeff0011"):
    """Build a MagicMock that mimics LXMF 0.9.4's surface.

    `register_returns_source=False` simulates the "router already has a
    delivery identity, returns None" case from LXMF 0.9.4 — that's the
    behavior the original implementation tripped over.
    """
    lxmf = MagicMock(name="LXMF")
    lxmf.LXMessage = MagicMock(name="LXMessage")

    # LXMRouter constructor returns a router instance whose
    # register_delivery_identity returns either a source-with-.hash
    # or None per the flag.
    router = MagicMock(name="LXMRouter")
    if register_returns_source:
        src = MagicMock()
        src.hash = bytes.fromhex(source_hash_hex)
        router.register_delivery_identity.return_value = src
    else:
        router.register_delivery_identity.return_value = None
    lxmf.LXMRouter.return_value = router
    return lxmf, router


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

    lxmf, router = _make_fake_lxmf()
    return rns, lxmf, router


def _make_bridge(tmp_path, fake_rns_lxmf, *, enabled=True, channels=None,
                 autosubscribe=False, prefix=None):
    rns, lxmf, _router = fake_rns_lxmf
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
        rns_module=rns,
        lxmf_module=lxmf,
        identity_path=tmp_path / "identity",
        db_path=tmp_path / "subs.db",
        storage_path=tmp_path / "storage",
    )


# ---------------------------------------------------------------------------
# LXMFBroadcastBridge — start / identity
# ---------------------------------------------------------------------------


class TestBridgeLifecycle:
    def test_start_registers_identity(self, tmp_path, fake_rns_lxmf):
        b = _make_bridge(tmp_path, fake_rns_lxmf)
        assert b.start() is True
        rns, lxmf, router = fake_rns_lxmf
        # Bridge built its OWN LXMRouter and registered a delivery identity
        lxmf.LXMRouter.assert_called_once()
        router.register_delivery_identity.assert_called_once()
        # First announce fires synchronously on start
        router.announce.assert_called_once()
        assert b.is_running is True
        assert b.destination_hash_hex == "aabbccddeeff0011"

    def test_start_fails_when_router_returns_none(self, tmp_path):
        """Regression guard: LXMF 0.9.4 returns None if a delivery identity
        is already registered on the router. Make sure start() reports
        failure instead of silently coming up with no usable hash."""
        rns = MagicMock()
        rns.Identity.from_file = MagicMock(side_effect=Exception("no file"))
        rns.Identity.return_value = MagicMock()
        lxmf, _router = _make_fake_lxmf(register_returns_source=False)
        cfg = LXMFBroadcastConfig(enabled=True, announce_interval_sec=0)
        b = LXMFBroadcastBridge(
            broadcast_config=cfg,
            rns_module=rns,
            lxmf_module=lxmf,
            identity_path=tmp_path / "id",
            db_path=tmp_path / "subs.db",
            storage_path=tmp_path / "storage",
        )
        assert b.start() is False
        assert b.is_running is False
        assert b.destination_hash_hex == ""

    def test_stop_is_safe_when_not_running(self, tmp_path, fake_rns_lxmf):
        b = _make_bridge(tmp_path, fake_rns_lxmf)
        b.stop()  # should not raise


# ---------------------------------------------------------------------------
# LXMFBroadcastBridge — MeshCore RX filtering & fan-out
# ---------------------------------------------------------------------------


class TestMeshCoreFanout:
    def test_filters_non_meshcore(self, tmp_path, fake_rns_lxmf):
        b = _make_bridge(tmp_path, fake_rns_lxmf)
        b.start()
        b._subs.add("deadbeef00112233")
        msg = _make_canonical(source_network=Protocol.RNS.value, text="hi")
        b.on_meshcore_message(msg)
        _rns, lxmf, _router = fake_rns_lxmf
        lxmf.LXMessage.assert_not_called()
        assert b.stats["filtered_non_meshcore"] == 1

    def test_filters_non_broadcast(self, tmp_path, fake_rns_lxmf):
        b = _make_bridge(tmp_path, fake_rns_lxmf)
        b.start()
        b._subs.add("deadbeef00112233")
        msg = _make_canonical(is_broadcast=False)
        b.on_meshcore_message(msg)
        _rns, lxmf, _router = fake_rns_lxmf
        lxmf.LXMessage.assert_not_called()

    def test_filters_disallowed_channel(self, tmp_path, fake_rns_lxmf):
        b = _make_bridge(tmp_path, fake_rns_lxmf, channels=[0])
        b.start()
        b._subs.add("deadbeef00112233")
        msg = _make_canonical(channel=7, text="off-channel")
        b.on_meshcore_message(msg)
        _rns, lxmf, _router = fake_rns_lxmf
        lxmf.LXMessage.assert_not_called()
        assert b.stats["filtered_channel"] == 1

    def test_skips_empty_content(self, tmp_path, fake_rns_lxmf):
        b = _make_bridge(tmp_path, fake_rns_lxmf)
        b.start()
        b._subs.add("deadbeef00112233")
        msg = _make_canonical(text="")
        b.on_meshcore_message(msg)
        _rns, lxmf, _router = fake_rns_lxmf
        lxmf.LXMessage.assert_not_called()

    def test_fans_out_to_each_subscriber(self, tmp_path, fake_rns_lxmf):
        b = _make_bridge(tmp_path, fake_rns_lxmf)
        b.start()
        b._subs.add("aaaa000000000001")
        b._subs.add("aaaa000000000002")
        msg = _make_canonical(channel=0, text="ping", sender="alice123")
        b.on_meshcore_message(msg)
        _rns, lxmf, router = fake_rns_lxmf
        assert lxmf.LXMessage.call_count == 2
        assert router.handle_outbound.call_count == 2
        first_call_args = lxmf.LXMessage.call_args_list[0].args
        assert "ping" in first_call_args[2]
        assert "alice123" in first_call_args[2]
        assert b.stats["fanouts"] == 2


# ---------------------------------------------------------------------------
# LXMFBroadcastBridge — subscription protocol
# ---------------------------------------------------------------------------


def _fake_lxmf_message(*, source_hash: bytes, body: str):
    """Mock the LXMessage shape the bridge's delivery callback receives.

    The bridge's own LXMRouter only delivers messages addressed to its
    identity, so destination_hash isn't filtered any more.
    """
    msg = MagicMock()
    msg.source_hash = source_hash
    msg.content = body.encode("utf-8")
    msg.title = b"test"
    return msg


class TestSubscriptionProtocol:
    def test_subscribe_adds_subscriber(self, tmp_path, fake_rns_lxmf):
        b = _make_bridge(tmp_path, fake_rns_lxmf)
        b.start()
        source = bytes.fromhex("aaaa000000000003")
        msg = _fake_lxmf_message(source_hash=source, body="subscribe please")
        b._on_lxmf_delivery(msg)
        hashes = [s.lxmf_hash for s in b._subs.list_all()]
        assert "aaaa000000000003" in hashes
        assert b.stats["subscribes"] == 1

    def test_unsubscribe_removes_subscriber(self, tmp_path, fake_rns_lxmf):
        b = _make_bridge(tmp_path, fake_rns_lxmf)
        b.start()
        b._subs.add("aaaa000000000003")
        msg = _fake_lxmf_message(
            source_hash=bytes.fromhex("aaaa000000000003"), body="unsubscribe"
        )
        b._on_lxmf_delivery(msg)
        assert b._subs.list_all() == []
        assert b.stats["unsubscribes"] == 1

    def test_unknown_verb_does_not_subscribe_by_default(self, tmp_path, fake_rns_lxmf):
        b = _make_bridge(tmp_path, fake_rns_lxmf)
        b.start()
        msg = _fake_lxmf_message(
            source_hash=bytes.fromhex("aaaa000000000003"), body="hello world"
        )
        b._on_lxmf_delivery(msg)
        assert b._subs.list_all() == []

    def test_autosubscribe_adds_on_unknown_verb(self, tmp_path, fake_rns_lxmf):
        b = _make_bridge(tmp_path, fake_rns_lxmf, autosubscribe=True)
        b.start()
        msg = _fake_lxmf_message(
            source_hash=bytes.fromhex("aaaa000000000004"), body="hi there"
        )
        b._on_lxmf_delivery(msg)
        hashes = [s.lxmf_hash for s in b._subs.list_all()]
        assert "aaaa000000000004" in hashes


# ---------------------------------------------------------------------------
# create_from_gateway_config gating
# ---------------------------------------------------------------------------


class TestFactory:
    def test_returns_none_when_disabled(self):
        gateway_config = MagicMock()
        gateway_config.lxmf_broadcast = LXMFBroadcastConfig(enabled=False)
        out = create_from_gateway_config(gateway_config)
        assert out is None

    def test_returns_instance_when_enabled(self, tmp_path):
        # Override identity_file/db_file so the unit test doesn't pollute
        # the operator's real ~/.config.
        gateway_config = MagicMock()
        gateway_config.lxmf_broadcast = LXMFBroadcastConfig(
            enabled=True,
            identity_file=str(tmp_path / "id"),
            db_file=str(tmp_path / "subs.db"),
        )
        gateway_config.rns = MagicMock(propagation_node="")
        bridge = create_from_gateway_config(gateway_config)
        assert isinstance(bridge, LXMFBroadcastBridge)


# ---------------------------------------------------------------------------
# Active-bridge accessor (config_api seam)
# ---------------------------------------------------------------------------


class TestActiveBridgeAccessor:
    """The `/lxmf-broadcast/*` HTTP surface reaches into the bridge via
    `get_active_broadcast_bridge()`. Same pattern as `meshcore_handler`.
    """

    def test_start_registers_active_bridge(self, tmp_path, fake_rns_lxmf):
        from gateway.lxmf_broadcast_bridge import get_active_broadcast_bridge

        b = _make_bridge(tmp_path, fake_rns_lxmf)
        assert b.start() is True
        try:
            assert get_active_broadcast_bridge() is b
        finally:
            b.stop()
        assert get_active_broadcast_bridge() is None

    def test_stop_only_clears_self(self, tmp_path, fake_rns_lxmf):
        """Identity guard: a stale stop() from a previous bridge must NOT
        clobber a freshly-started replacement."""
        from gateway.lxmf_broadcast_bridge import get_active_broadcast_bridge

        b1 = _make_bridge(tmp_path / "a", fake_rns_lxmf)
        b1.start()

        # Simulate a second bridge taking over (e.g., daemon restart on
        # the same process — unusual but the lock must handle it).
        rns2 = MagicMock()
        rns2.Identity.from_file = MagicMock(side_effect=Exception("no file"))
        rns2.Identity.return_value = MagicMock()
        rns2.Transport = MagicMock()
        rns2.Transport.has_path = MagicMock(return_value=True)
        rns2.Identity.recall = MagicMock(return_value=MagicMock())
        rns2.Destination = MagicMock()
        lxmf2, _r2 = _make_fake_lxmf(source_hash_hex="bbccddeeff001122")
        cfg = LXMFBroadcastConfig(
            enabled=True, channels=[0], display_name="B2", announce_interval_sec=0,
        )
        b2 = LXMFBroadcastBridge(
            broadcast_config=cfg,
            rns_module=rns2,
            lxmf_module=lxmf2,
            identity_path=tmp_path / "b" / "id",
            db_path=tmp_path / "b" / "subs.db",
            storage_path=tmp_path / "b" / "storage",
        )
        b2.start()
        assert get_active_broadcast_bridge() is b2

        # b1.stop() is a no-op for the active slot because b2 owns it now.
        b1.stop()
        assert get_active_broadcast_bridge() is b2

        b2.stop()
        assert get_active_broadcast_bridge() is None


class TestGetStatusFields:
    """Status payload feeds the TUI + HTTP — keep its shape pinned."""

    def test_status_contains_new_fields(self, tmp_path, fake_rns_lxmf):
        b = _make_bridge(tmp_path, fake_rns_lxmf)
        b.start()
        try:
            status = b.get_status()
            assert "announce_interval_sec" in status
            assert "started_at_iso" in status
            assert status["started_at_iso"] is not None
            assert "autosubscribe" in status
            assert "subscriber_count" in status
            assert isinstance(status["subscribers"], list)
        finally:
            b.stop()

    def test_status_subscribers_are_row_dicts(self, tmp_path, fake_rns_lxmf):
        """Old shape was `subscribers: int`. New shape is `subscribers: [row,...]`
        with subscriber_count carrying the integer. TUI relies on this."""
        b = _make_bridge(tmp_path, fake_rns_lxmf)
        b.start()
        try:
            b._subs.add("deadbeef00112233")
            status = b.get_status()
            assert status["subscriber_count"] == 1
            assert len(status["subscribers"]) == 1
            row = status["subscribers"][0]
            assert row["lxmf_hash"] == "deadbeef00112233"
            assert "added_at" in row
            assert "last_delivery" in row
        finally:
            b.stop()

    def test_status_when_stopped(self, tmp_path, fake_rns_lxmf):
        b = _make_bridge(tmp_path, fake_rns_lxmf)
        status = b.get_status()
        assert status["running"] is False
        assert status["started_at_iso"] is None


# ---------------------------------------------------------------------------
# S1 — tier + state machine columns
# ---------------------------------------------------------------------------


class TestSubscriberStoreS1Schema:
    """Schema migration + tier-aware add. Live `meshanchor-server` has rows
    pre-dating these columns; the migration must promote them in-place."""

    def test_fresh_db_has_default_tier_and_state(self, tmp_path):
        store = SubscriberStore(tmp_path / "subs.db")
        store.add("deadbeef00112233")
        sub = store.list_all()[0]
        assert sub.tier == TIER_EXTERNAL
        assert sub.state == STATE_HEALTHY
        assert sub.consecutive_failures == 0
        assert sub.last_failure_at is None

    def test_add_accepts_tier_argument(self, tmp_path):
        store = SubscriberStore(tmp_path / "subs.db")
        store.add("aaaa000000000001", tier=TIER_LOCAL)
        store.add("aaaa000000000002", tier=TIER_FEDERATION)
        store.add("aaaa000000000003")  # default external
        by_local = store.list_by_tier(TIER_LOCAL)
        by_fed = store.list_by_tier(TIER_FEDERATION)
        by_ext = store.list_by_tier(TIER_EXTERNAL)
        assert {s.lxmf_hash for s in by_local} == {"aaaa000000000001"}
        assert {s.lxmf_hash for s in by_fed} == {"aaaa000000000002"}
        assert {s.lxmf_hash for s in by_ext} == {"aaaa000000000003"}

    def test_add_tier_idempotent_first_value_sticks(self, tmp_path):
        """Per S1 contract: re-adding doesn't downgrade tier."""
        store = SubscriberStore(tmp_path / "subs.db")
        store.add("deadbeef00112233", tier=TIER_LOCAL)
        store.add("deadbeef00112233", tier=TIER_EXTERNAL)  # ignored
        assert store.list_all()[0].tier == TIER_LOCAL

    def test_migration_from_pre_s1_schema(self, tmp_path):
        """Simulate the live meshanchor-server DB: old schema (3 columns)
        with 2 populated rows. Init must add columns + default existing
        rows to external/healthy/0/NULL without losing the rows."""
        import sqlite3

        db = tmp_path / "subs.db"
        # Build the pre-S1 schema by hand and seed two rows.
        with sqlite3.connect(db) as conn:
            conn.execute(
                "CREATE TABLE subscribers ("
                "lxmf_hash TEXT PRIMARY KEY, "
                "added_at TEXT NOT NULL, "
                "last_delivery TEXT)"
            )
            conn.execute(
                "INSERT INTO subscribers VALUES (?, ?, ?)",
                ("a0e0253bd9256a32f83090ca15671f19",
                 "2026-05-09T17:41:21", "2026-05-13T01:05:28"),
            )
            conn.execute(
                "INSERT INTO subscribers VALUES (?, ?, ?)",
                ("b6a88d408fa0fbfab0bc49befbee8aba",
                 "2026-05-09T17:58:14", "2026-05-13T01:05:28"),
            )
            conn.commit()

        # Migration runs on first SubscriberStore() against the DB.
        store = SubscriberStore(db)
        subs = store.list_all()
        assert len(subs) == 2
        for s in subs:
            assert s.tier == TIER_EXTERNAL
            assert s.state == STATE_HEALTHY
            assert s.consecutive_failures == 0
            assert s.last_failure_at is None
            assert s.last_delivery is not None  # original data preserved

    def test_migration_is_idempotent(self, tmp_path):
        """Re-running init on a post-S1 schema must not error or duplicate
        columns. PRAGMA table_info gating handles this."""
        db = tmp_path / "subs.db"
        SubscriberStore(db).add("deadbeef00112233", tier=TIER_LOCAL)
        # Second init on the same DB
        store2 = SubscriberStore(db)
        sub = store2.list_all()[0]
        assert sub.tier == TIER_LOCAL  # not clobbered to default


class TestSubscriberStoreS1Failure:
    """mark_failed counter + last_failure_at; mark_delivered resets both."""

    def test_mark_failed_increments_counter(self, tmp_path):
        store = SubscriberStore(tmp_path / "subs.db")
        store.add("deadbeef00112233")
        store.mark_failed("deadbeef00112233", "test_reason")
        store.mark_failed("deadbeef00112233", "test_reason")
        store.mark_failed("deadbeef00112233", "test_reason")
        sub = store.list_all()[0]
        assert sub.consecutive_failures == 3
        assert sub.last_failure_at is not None

    def test_mark_delivered_resets_failure_counter(self, tmp_path):
        store = SubscriberStore(tmp_path / "subs.db")
        store.add("deadbeef00112233")
        for _ in range(5):
            store.mark_failed("deadbeef00112233", "reason")
        store.mark_delivered("deadbeef00112233")
        sub = store.list_all()[0]
        assert sub.consecutive_failures == 0
        assert sub.state == STATE_HEALTHY
        assert sub.last_delivery is not None

    def test_mark_delivered_recovers_from_degraded(self, tmp_path):
        """S1 carries the reset logic so S3's auto-degraded transitions
        recover cleanly on the next successful fan-out."""
        store = SubscriberStore(tmp_path / "subs.db")
        store.add("deadbeef00112233")
        store.transition_state("deadbeef00112233", STATE_DEGRADED)
        assert store.list_all()[0].state == STATE_DEGRADED
        store.mark_delivered("deadbeef00112233")
        assert store.list_all()[0].state == STATE_HEALTHY

    def test_mark_failed_on_unknown_hash_is_noop(self, tmp_path):
        """mark_failed is called inside _send_to_subscriber's exception
        paths; if it ever runs against a row that isn't in the DB
        (e.g. _reply with an ephemeral Subscriber) it must not raise."""
        store = SubscriberStore(tmp_path / "subs.db")
        store.mark_failed("nonexistent00000000000000000000000", "reason")
        assert store.list_all() == []

    def test_transition_state_returns_false_for_unknown_hash(self, tmp_path):
        store = SubscriberStore(tmp_path / "subs.db")
        assert (
            store.transition_state("nonexistent00000000000000000000000", STATE_DEAD)
            is False
        )

    def test_list_by_state(self, tmp_path):
        store = SubscriberStore(tmp_path / "subs.db")
        store.add("aaaa000000000001")
        store.add("aaaa000000000002")
        store.add("aaaa000000000003")
        store.transition_state("aaaa000000000002", STATE_STALE)
        store.transition_state("aaaa000000000003", STATE_DEAD)
        assert (
            {s.lxmf_hash for s in store.list_by_state(STATE_HEALTHY)}
            == {"aaaa000000000001"}
        )
        assert (
            {s.lxmf_hash for s in store.list_by_state(STATE_STALE)}
            == {"aaaa000000000002"}
        )


class TestFanoutWiresMarkFailed:
    """The three existing failure paths in _send_to_subscriber must call
    mark_failed on the subscriber's row."""

    def test_invalid_hash_marks_failed(self, tmp_path, fake_rns_lxmf):
        b = _make_bridge(tmp_path, fake_rns_lxmf)
        b.start()
        try:
            # Insert a syntactically invalid hash that bytes.fromhex rejects.
            # SubscriberStore lower-cases on add; we bypass via direct SQL so
            # the row exists for mark_failed to update.
            with b._subs._lock, __import__("sqlite3").connect(
                str(b._subs._db_path)
            ) as conn:
                conn.execute(
                    "INSERT INTO subscribers("
                    "lxmf_hash, added_at, tier, state, consecutive_failures"
                    ") VALUES (?, ?, ?, ?, 0)",
                    ("zzznothex", "2026-05-12T00:00:00",
                     TIER_EXTERNAL, STATE_HEALTHY),
                )
                conn.commit()
            msg = _make_canonical(text="ping", sender="alice")
            b.on_meshcore_message(msg)
            sub = next(
                s for s in b._subs.list_all() if s.lxmf_hash == "zzznothex"
            )
            assert sub.consecutive_failures >= 1
            assert sub.last_failure_at is not None
        finally:
            b.stop()

    def test_identity_recall_none_marks_failed(self, tmp_path, fake_rns_lxmf):
        rns, _lxmf, _router = fake_rns_lxmf
        rns.Identity.recall = MagicMock(return_value=None)
        b = _make_bridge(tmp_path, fake_rns_lxmf)
        b.start()
        try:
            b._subs.add("deadbeef00112233")
            b.on_meshcore_message(_make_canonical(text="ping", sender="alice"))
            sub = b._subs.list_all()[0]
            assert sub.consecutive_failures >= 1
        finally:
            b.stop()

    def test_handle_outbound_exception_marks_failed(self, tmp_path, fake_rns_lxmf):
        _rns, _lxmf, router = fake_rns_lxmf
        router.handle_outbound = MagicMock(side_effect=RuntimeError("boom"))
        b = _make_bridge(tmp_path, fake_rns_lxmf)
        b.start()
        try:
            b._subs.add("deadbeef00112233")
            b.on_meshcore_message(_make_canonical(text="ping", sender="alice"))
            sub = b._subs.list_all()[0]
            assert sub.consecutive_failures >= 1
        finally:
            b.stop()

    def test_successful_fanout_resets_failure_counter(
        self, tmp_path, fake_rns_lxmf,
    ):
        """End-to-end: fail once, success once. Counter resets to 0.

        After S3's backoff lands, two rapid failures in a row would skip
        the second attempt (in cooldown). We backdate the failure stamp
        between broadcasts so the second fanout actually fires.
        """
        rns, _lxmf, _router = fake_rns_lxmf
        recall_results = [None, MagicMock()]
        rns.Identity.recall = MagicMock(side_effect=lambda *a, **k: recall_results.pop(0))
        b = _make_bridge(tmp_path, fake_rns_lxmf)
        b.start()
        try:
            b._subs.add("deadbeef00112233", tier=TIER_LOCAL)
            b.on_meshcore_message(_make_canonical(text="m1", sender="a"))
            assert b._subs.list_all()[0].consecutive_failures == 1
            # Backdate failure stamp so backoff window is satisfied.
            import sqlite3
            with sqlite3.connect(str(b._subs._db_path)) as conn:
                conn.execute(
                    "UPDATE subscribers SET last_failure_at = ? "
                    "WHERE lxmf_hash = ?",
                    ("2020-01-01T00:00:00", "deadbeef00112233"),
                )
                conn.commit()
            b.on_meshcore_message(_make_canonical(text="m2", sender="a"))
            sub = b._subs.list_all()[0]
            assert sub.consecutive_failures == 0
            assert sub.last_delivery is not None
        finally:
            b.stop()


class TestStatusJSONS1Fields:
    """Confirm get_status() row shape exposes the new columns."""

    def test_row_has_tier_state_failure_columns(self, tmp_path, fake_rns_lxmf):
        b = _make_bridge(tmp_path, fake_rns_lxmf)
        b.start()
        try:
            b._subs.add("deadbeef00112233", tier=TIER_LOCAL)
            b._subs.mark_failed("deadbeef00112233", "reason")
            row = b.get_status()["subscribers"][0]
            assert row["tier"] == TIER_LOCAL
            assert row["state"] == STATE_HEALTHY
            assert row["consecutive_failures"] == 1
            assert row["last_failure_at"] is not None
        finally:
            b.stop()


# ---------------------------------------------------------------------------
# S3 — state-machine auto-transitions
# ---------------------------------------------------------------------------


from datetime import datetime, timedelta  # noqa: E402

from gateway.lxmf_broadcast_bridge import (  # noqa: E402
    Subscriber,
)


def _sub(
    *, fails=0, state=STATE_HEALTHY, tier=TIER_EXTERNAL,
    last_delivery=None, last_failure_at=None, added_at=None,
):
    """Build a Subscriber row for pure-function state/backoff tests."""
    return Subscriber(
        lxmf_hash="deadbeef00112233",
        added_at=added_at or datetime(2026, 1, 1),
        last_delivery=last_delivery,
        tier=tier,
        state=state,
        consecutive_failures=fails,
        last_failure_at=last_failure_at,
    )


class TestDesiredStatePureFunction:
    """SubscriberStore.desired_state is a pure function — easiest to pin in
    isolation rather than driving it via real DB rows."""

    NOW = datetime(2026, 5, 12, 12, 0, 0)

    def test_zero_failures_is_healthy(self):
        sub = _sub(fails=0)
        assert SubscriberStore.desired_state(sub, self.NOW) == STATE_HEALTHY

    def test_one_failure_recent_success_stays_healthy(self):
        sub = _sub(
            fails=1,
            last_delivery=self.NOW - timedelta(minutes=5),
            last_failure_at=self.NOW,
        )
        assert SubscriberStore.desired_state(sub, self.NOW) == STATE_HEALTHY

    def test_three_failures_recent_success_is_degraded(self):
        sub = _sub(
            fails=3,
            last_delivery=self.NOW - timedelta(hours=1),
            last_failure_at=self.NOW,
        )
        assert SubscriberStore.desired_state(sub, self.NOW) == STATE_DEGRADED

    def test_failures_24h_without_success_is_stale(self):
        sub = _sub(
            fails=5,
            last_delivery=self.NOW - timedelta(hours=25),
            last_failure_at=self.NOW,
        )
        assert SubscriberStore.desired_state(sub, self.NOW) == STATE_STALE

    def test_failures_7d_without_success_is_dead(self):
        sub = _sub(
            fails=20,
            last_delivery=self.NOW - timedelta(days=8),
            last_failure_at=self.NOW,
        )
        assert SubscriberStore.desired_state(sub, self.NOW) == STATE_DEAD

    def test_no_last_delivery_falls_back_to_added_at(self):
        """A never-delivered subscriber uses added_at as the success baseline."""
        sub = _sub(
            fails=10,
            added_at=self.NOW - timedelta(days=10),
            last_failure_at=self.NOW,
            last_delivery=None,
        )
        assert SubscriberStore.desired_state(sub, self.NOW) == STATE_DEAD

    def test_dead_wins_over_degraded(self):
        """Severity ordering: 7d-no-success outranks the 3-fails threshold."""
        sub = _sub(
            fails=100,
            last_delivery=self.NOW - timedelta(days=10),
            last_failure_at=self.NOW,
        )
        assert SubscriberStore.desired_state(sub, self.NOW) == STATE_DEAD


class TestStateTransitionsInMarkFailed:
    """End-to-end: mark_failed UPDATEs the row, reads it back, and applies
    desired_state. Tests use the SubscriberStore directly against tmp DBs."""

    def test_three_failures_transitions_to_degraded(self, tmp_path):
        store = SubscriberStore(tmp_path / "subs.db")
        store.add("deadbeef00112233")
        for _ in range(3):
            store.mark_failed("deadbeef00112233", "reason")
        sub = store.list_all()[0]
        assert sub.consecutive_failures == 3
        assert sub.state == STATE_DEGRADED

    def test_two_failures_stays_healthy(self, tmp_path):
        store = SubscriberStore(tmp_path / "subs.db")
        store.add("deadbeef00112233")
        store.mark_failed("deadbeef00112233", "reason")
        store.mark_failed("deadbeef00112233", "reason")
        sub = store.list_all()[0]
        assert sub.consecutive_failures == 2
        assert sub.state == STATE_HEALTHY

    def test_mark_delivered_after_degraded_returns_to_healthy(self, tmp_path):
        store = SubscriberStore(tmp_path / "subs.db")
        store.add("deadbeef00112233")
        for _ in range(5):
            store.mark_failed("deadbeef00112233", "reason")
        assert store.list_all()[0].state == STATE_DEGRADED
        store.mark_delivered("deadbeef00112233")
        sub = store.list_all()[0]
        assert sub.state == STATE_HEALTHY
        assert sub.consecutive_failures == 0

    def test_mark_failed_on_unknown_hash_is_noop_still(self, tmp_path):
        """Regression guard from S1: ephemeral _reply path must not crash
        when state-machine code reads back a non-existent row."""
        store = SubscriberStore(tmp_path / "subs.db")
        store.mark_failed("nonexistent00000000000000000000000", "reason")
        assert store.list_all() == []


class TestBackoffMath:
    """SubscriberStore.backoff_seconds — tier-specific exponential backoff."""

    def test_zero_failures_returns_zero(self):
        assert SubscriberStore.backoff_seconds(_sub(fails=0)) == 0.0

    def test_local_tier_starts_at_one_second(self):
        sub = _sub(fails=1, tier=TIER_LOCAL)
        assert SubscriberStore.backoff_seconds(sub) == 1.0

    def test_local_tier_doubles_per_failure(self):
        # fails=1→1, fails=2→2, fails=3→4
        assert SubscriberStore.backoff_seconds(_sub(fails=2, tier=TIER_LOCAL)) == 2.0
        assert SubscriberStore.backoff_seconds(_sub(fails=3, tier=TIER_LOCAL)) == 4.0

    def test_local_tier_caps_at_10_minutes(self):
        sub = _sub(fails=20, tier=TIER_LOCAL)
        assert SubscriberStore.backoff_seconds(sub) == 600.0

    def test_external_tier_starts_at_30_seconds(self):
        assert (
            SubscriberStore.backoff_seconds(_sub(fails=1, tier=TIER_EXTERNAL))
            == 30.0
        )

    def test_external_tier_caps_at_one_hour(self):
        sub = _sub(fails=30, tier=TIER_EXTERNAL)
        assert SubscriberStore.backoff_seconds(sub) == 3600.0

    def test_unknown_tier_falls_back_to_external(self):
        sub = _sub(fails=1, tier="weird")
        assert SubscriberStore.backoff_seconds(sub) == 30.0


class TestShouldAttempt:
    NOW = datetime(2026, 5, 12, 12, 0, 0)

    def test_healthy_subscriber_always_attempts(self, tmp_path):
        store = SubscriberStore(tmp_path / "subs.db")
        sub = _sub(fails=0)
        assert store.should_attempt(sub, self.NOW) is True

    def test_skips_during_backoff_window(self, tmp_path):
        store = SubscriberStore(tmp_path / "subs.db")
        # Local tier, 3 failures = 4s backoff. Last fail 1s ago → skip.
        sub = _sub(
            fails=3, tier=TIER_LOCAL,
            last_failure_at=self.NOW - timedelta(seconds=1),
        )
        assert store.should_attempt(sub, self.NOW) is False

    def test_attempts_after_backoff_window(self, tmp_path):
        store = SubscriberStore(tmp_path / "subs.db")
        # Local tier, 3 failures = 4s backoff. Last fail 5s ago → attempt.
        sub = _sub(
            fails=3, tier=TIER_LOCAL,
            last_failure_at=self.NOW - timedelta(seconds=5),
        )
        assert store.should_attempt(sub, self.NOW) is True

    def test_missing_last_failure_still_attempts(self, tmp_path):
        """Defensive: a row with fails > 0 but no last_failure_at (shouldn't
        happen in production) shouldn't get permanently skipped."""
        store = SubscriberStore(tmp_path / "subs.db")
        sub = _sub(fails=3, last_failure_at=None)
        assert store.should_attempt(sub, self.NOW) is True


class TestFanoutSkipsBackoffSubscribers:
    """on_meshcore_message must filter via should_attempt and bump the
    skipped_backoff stat."""

    def test_skips_subscriber_in_backoff(self, tmp_path, fake_rns_lxmf):
        _rns, lxmf, _router = fake_rns_lxmf
        b = _make_bridge(tmp_path, fake_rns_lxmf)
        b.start()
        try:
            b._subs.add("deadbeef00112233", tier=TIER_LOCAL)
            # Force the subscriber into backoff by stamping a recent failure.
            with b._subs._lock, __import__("sqlite3").connect(
                str(b._subs._db_path)
            ) as conn:
                conn.execute(
                    "UPDATE subscribers SET "
                    "consecutive_failures = 5, "
                    "last_failure_at = ? "
                    "WHERE lxmf_hash = ?",
                    (datetime.utcnow().isoformat(), "deadbeef00112233"),
                )
                conn.commit()
            b.on_meshcore_message(_make_canonical(text="ping", sender="a"))
            lxmf.LXMessage.assert_not_called()
            assert b.stats["skipped_backoff"] == 1
        finally:
            b.stop()

    def test_attempts_healthy_subscriber_in_same_fanout(self, tmp_path, fake_rns_lxmf):
        """Healthy + backoff'd in the same broadcast: healthy still goes out."""
        _rns, lxmf, _router = fake_rns_lxmf
        b = _make_bridge(tmp_path, fake_rns_lxmf)
        b.start()
        try:
            b._subs.add("aaaa000000000001", tier=TIER_LOCAL)  # healthy
            b._subs.add("bbbb000000000002", tier=TIER_LOCAL)  # will be in backoff
            with b._subs._lock, __import__("sqlite3").connect(
                str(b._subs._db_path)
            ) as conn:
                conn.execute(
                    "UPDATE subscribers SET "
                    "consecutive_failures = 5, "
                    "last_failure_at = ? "
                    "WHERE lxmf_hash = ?",
                    (datetime.utcnow().isoformat(), "bbbb000000000002"),
                )
                conn.commit()
            b.on_meshcore_message(_make_canonical(text="ping", sender="a"))
            assert lxmf.LXMessage.call_count == 1
            assert b.stats["skipped_backoff"] == 1
            assert b.stats["fanouts"] == 1
        finally:
            b.stop()
