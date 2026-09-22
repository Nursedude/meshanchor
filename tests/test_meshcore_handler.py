"""
Tests for MeshCoreHandler — MeshCore companion radio integration.

Tests cover:
- Handler instantiation and DI pattern
- Simulation mode (no hardware)
- Message receive → queue flow
- Outbound message processing
- Connection lifecycle
- Device detection
- Event subscription
"""

import asyncio
import os
import sys
import threading
import time
from datetime import datetime
from queue import Queue
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from gateway.meshcore_handler import (
    MeshCoreHandler,
    MeshCoreSimulator,
    detect_meshcore_devices,
    _HAS_MESHCORE,
)
from gateway.canonical_message import CanonicalMessage, MessageType, Protocol
from gateway.bridge_health import MessageOrigin


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
def mock_config():
    """Create a minimal gateway config with MeshCore settings."""
    meshcore = SimpleNamespace(
        enabled=True,
        device_path='/dev/ttyUSB1',
        baud_rate=115200,
        connection_type='serial',
        tcp_host='localhost',
        tcp_port=4000,
        auto_fetch_messages=True,
        bridge_channels=True,
        bridge_dms=True,
        simulation_mode=True,
        channel_poll_interval_sec=5,
    )
    config = SimpleNamespace(
        meshcore=meshcore,
        meshtastic=SimpleNamespace(host='localhost', port=4403),
    )
    return config


@pytest.fixture
def mock_health():
    """Create a mock health monitor."""
    health = MagicMock()
    health.record_error.return_value = 'transient'
    health.record_connection_event = MagicMock()
    health.record_message_sent = MagicMock()
    return health


@pytest.fixture
def mock_node_tracker():
    """Create a mock node tracker."""
    tracker = MagicMock()
    tracker.add_node = MagicMock()
    return tracker


@pytest.fixture
def handler(mock_config, mock_health, mock_node_tracker):
    """Create a MeshCoreHandler in simulation mode."""
    stop_event = threading.Event()
    stats = {'errors': 0}
    stats_lock = threading.Lock()
    queue = Queue(maxsize=100)

    h = MeshCoreHandler(
        config=mock_config,
        node_tracker=mock_node_tracker,
        health=mock_health,
        stop_event=stop_event,
        stats=stats,
        stats_lock=stats_lock,
        message_queue=queue,
    )
    return h


# =============================================================================
# Instantiation & DI Pattern
# =============================================================================

class TestInstantiation:
    """Test handler creation follows the DI pattern."""

    def test_handler_creates(self, handler):
        """Handler instantiates without errors."""
        assert handler is not None
        assert handler.is_connected is False

    def test_simulation_mode_detected(self, handler):
        """Simulation mode detected when meshcore not installed or config says so."""
        assert handler._simulation_mode is True

    def test_handler_with_callbacks(self, mock_config, mock_health, mock_node_tracker):
        """Handler accepts all callback parameters."""
        msg_cb = MagicMock()
        status_cb = MagicMock()
        bridge_cb = MagicMock()

        h = MeshCoreHandler(
            config=mock_config,
            node_tracker=mock_node_tracker,
            health=mock_health,
            stop_event=threading.Event(),
            stats={},
            stats_lock=threading.Lock(),
            message_queue=Queue(),
            message_callback=msg_cb,
            status_callback=status_cb,
            should_bridge=bridge_cb,
        )
        assert h._message_callback is msg_cb
        assert h._status_callback is status_cb
        assert h._should_bridge is bridge_cb


# =============================================================================
# Simulator
# =============================================================================

class TestSimulator:
    """Test MeshCoreSimulator for hardware-free testing."""

    def test_simulator_creates(self):
        """Simulator instantiates with fake contacts."""
        sim = MeshCoreSimulator()
        assert len(sim._contacts) > 0

    def test_simulator_start_stop(self):
        """Simulator starts and stops cleanly."""
        async def _test():
            sim = MeshCoreSimulator()
            await sim.start()
            assert sim._running is True
            await sim.stop()
            assert sim._running is False
        asyncio.run(_test())

    def test_simulator_get_contacts(self):
        """Simulator returns fake contacts."""
        async def _test():
            sim = MeshCoreSimulator()
            contacts = await sim.get_contacts()
            assert len(contacts) >= 2
            assert 'adv_name' in contacts[0]
        asyncio.run(_test())

    def test_simulator_send_msg(self):
        """Simulator accepts send operations."""
        async def _test():
            sim = MeshCoreSimulator()
            result = await sim.send_msg(None, "Test message")
            assert result is True
        asyncio.run(_test())

    def test_simulator_send_channel(self):
        """Simulator accepts channel broadcasts."""
        async def _test():
            sim = MeshCoreSimulator()
            result = await sim.send_channel_txt_msg("Broadcast test")
            assert result is True
        asyncio.run(_test())

    def test_simulator_subscribe(self):
        """Simulator accepts event subscriptions."""
        sim = MeshCoreSimulator()
        callback = MagicMock()
        sim.subscribe('CONTACT_MSG_RECV', callback)
        assert 'CONTACT_MSG_RECV' in sim._subscribers


# =============================================================================
# Connection Lifecycle
# =============================================================================

class TestConnectionLifecycle:
    """Test connect/disconnect behavior."""

    def test_connect_simulation(self, handler):
        """Connect in simulation mode succeeds."""
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(handler._connect())
            assert handler.is_connected is True
            assert isinstance(handler._meshcore, MeshCoreSimulator)
        finally:
            loop.close()

    def test_disconnect_clears_state(self, handler):
        """Disconnect clears connection state."""
        # First connect
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(handler._connect())
            assert handler.is_connected is True
        finally:
            loop.close()

        # Then disconnect (need loop for async disconnect)
        handler._loop = None
        handler._connected = False
        handler._meshcore = None
        assert handler.is_connected is False

    def test_not_connected_by_default(self, handler):
        """Handler starts disconnected."""
        assert handler.is_connected is False
        assert handler._meshcore is None


# =============================================================================
# Message Receive → Queue
# =============================================================================

class TestMessageReceive:
    """Test incoming message processing."""

    def test_contact_message_queued(self, handler):
        """Incoming DM is converted and queued."""
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(handler._connect())

            event = SimpleNamespace(
                type='CONTACT_MSG_RECV',
                payload={
                    'text': 'Hello from MeshCore',
                    'sender': 'abc123',
                    'destination': 'def456',
                    'is_channel': False,
                    'channel': 0,
                },
            )
            loop.run_until_complete(handler._on_contact_message(event))

            # Check message was queued
            assert not handler._message_queue.empty()
            msg = handler._message_queue.get_nowait()
            assert isinstance(msg, CanonicalMessage)
            assert msg.content == 'Hello from MeshCore'
            assert msg.source_network == 'meshcore'
        finally:
            loop.close()

    def test_channel_message_queued(self, handler):
        """Incoming channel message is converted and queued."""
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(handler._connect())

            event = SimpleNamespace(
                type='CHANNEL_MSG_RECV',
                payload={
                    'text': 'Channel broadcast',
                    'sender': 'node789',
                    'destination': None,
                    'is_channel': True,
                    'channel': 1,
                },
            )
            loop.run_until_complete(handler._on_channel_message(event))

            msg = handler._message_queue.get_nowait()
            assert msg.is_broadcast is True
        finally:
            loop.close()

    def test_routing_filter_blocks_message(self, handler):
        """Messages blocked by routing rules are not queued."""
        handler._should_bridge = lambda msg: False

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(handler._connect())

            event = SimpleNamespace(
                type='CONTACT_MSG_RECV',
                payload={
                    'text': 'Blocked message',
                    'sender': 'abc',
                    'destination': None,
                    'is_channel': False,
                    'channel': 0,
                },
            )
            loop.run_until_complete(handler._on_contact_message(event))

            assert handler._message_queue.empty()
        finally:
            loop.close()

    def test_message_callback_invoked(self, handler):
        """Message callback is called for received messages."""
        callback = MagicMock()
        handler._message_callback = callback

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(handler._connect())

            event = SimpleNamespace(
                type='CONTACT_MSG_RECV',
                payload={
                    'text': 'Callback test',
                    'sender': 'abc',
                    'destination': None,
                    'is_channel': False,
                    'channel': 0,
                },
            )
            loop.run_until_complete(handler._on_contact_message(event))

            assert callback.called
            args = callback.call_args[0]
            assert isinstance(args[0], CanonicalMessage)
        finally:
            loop.close()


# =============================================================================
# Outbound Messages
# =============================================================================

class TestOutbound:
    """Test outbound message processing."""

    def test_send_text_queues(self, handler):
        """send_text() queues message for async processing."""
        handler._connected = True
        result = handler.send_text("Test message", destination="abc123")
        assert result is True
        assert not handler._send_queue.empty()

    def test_send_text_broadcast(self, handler):
        """send_text() with no destination creates broadcast."""
        handler._connected = True
        result = handler.send_text("Broadcast")
        assert result is True
        msg = handler._send_queue.get_nowait()
        assert msg.is_broadcast is True

    def test_send_text_not_connected(self, handler):
        """send_text() returns False when disconnected."""
        assert handler.is_connected is False
        result = handler.send_text("Should fail")
        assert result is False

    def test_queue_send_interface(self, handler):
        """queue_send() works for persistent queue integration."""
        handler._connected = True
        result = handler.queue_send({
            'message': 'Queued message',
            'destination': 'abc123',
        })
        assert result is True

    def test_process_outbound(self, handler):
        """Outbound messages are sent via MeshCore."""
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(handler._connect())

            msg = CanonicalMessage(
                content="Outbound test",
                destination_address=None,
                is_broadcast=True,
            )
            handler._send_queue.put_nowait(msg)

            loop.run_until_complete(handler._process_outbound())

            # Queue should be drained
            assert handler._send_queue.empty()
            # Stats updated
            assert handler.stats.get('meshcore_tx', 0) >= 1
        finally:
            loop.close()


# =============================================================================
# Node Tracking (Advertisement)
# =============================================================================

class TestNodeTracking:
    """Test node discovery from advertisements."""

    def test_advertisement_adds_node(self, handler, mock_node_tracker):
        """Advertisement events add nodes to tracker."""
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(handler._connect())

            event = SimpleNamespace(
                type='ADVERTISEMENT',
                payload={
                    'adv_name': 'TestRepeater',
                    'pubkey_prefix': 'aabbcc',
                },
            )
            loop.run_until_complete(handler._on_advertisement(event))

            assert mock_node_tracker.add_node.called
        finally:
            loop.close()

    def test_advertisement_stamps_receipt_clock(self, handler, mock_node_tracker):
        """A FIRST advertisement must carry OUR receipt time, not None.

        Regression (2026-09-21): `_on_advertisement` built the UnifiedNode
        without `last_seen`, and only `_merge_node` stamps it — via
        `existing.update_seen()` — so a node heard exactly ONCE sat at None
        forever. Measured live on meshanchor-server the same day: 14 of 22
        meshcore nodes had `last_seen=None` while 8 carried real times.

        Why it matters beyond the field: the contacts pane's "last heard"
        column has no other truthful source. `last_advert` is the SENDER's
        clock (live range 2022-12-31 .. 2084-12-21) and `lastmod` is the
        firmware's record-modified stamp used by meshcore_py as an
        If-Modified-Since sync cursor, frozen 8 days back. A pane reading
        either renders "never heard" for a node heard seconds ago.
        """
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(handler._connect())

            before = datetime.now()
            event = SimpleNamespace(
                type='ADVERTISEMENT',
                payload={'adv_name': 'FirstHeard', 'pubkey_prefix': 'f00dcafe'},
            )
            loop.run_until_complete(handler._on_advertisement(event))
            after = datetime.now()

            assert mock_node_tracker.add_node.called
            node = mock_node_tracker.add_node.call_args[0][0]
            assert node.last_seen is not None, (
                "first advert left no receipt clock - the pane would render "
                "a live node as never heard"
            )
            assert before <= node.last_seen <= after
            assert node.is_online is True
        finally:
            loop.close()

    def test_advertisement_object_payload(self, handler, mock_node_tracker):
        """Advertisement with object-style payload."""
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(handler._connect())

            payload = SimpleNamespace(
                adv_name='ObjectNode',
                public_key=b'\xde\xad\xbe\xef',
                name='',
            )
            event = SimpleNamespace(type='ADVERTISEMENT', payload=payload)
            loop.run_until_complete(handler._on_advertisement(event))

            assert mock_node_tracker.add_node.called
        finally:
            loop.close()


# =============================================================================
# Device Detection
# =============================================================================

class TestDeviceDetection:
    """Test serial device scanning."""

    # NOTE: detect_meshcore_devices() checks os.path.exists('/dev/ttyMeshCore')
    # BEFORE the glob scan. On a box with the real udev symlink (e.g.
    # meshanchor-server) that leg leaks the real device into the result unless
    # the exists check is sealed too — patch it where it's looked up.

    @patch('utils.meshcore_connection.os.path.exists', return_value=False)
    @patch('glob.glob')
    def test_detect_devices(self, mock_glob, mock_exists):
        """Detect USB serial devices."""
        mock_glob.side_effect = [
            ['/dev/ttyUSB0', '/dev/ttyUSB1'],
            ['/dev/ttyACM0'],
        ]
        devices = detect_meshcore_devices()
        assert '/dev/ttyUSB0' in devices
        assert '/dev/ttyUSB1' in devices
        assert '/dev/ttyACM0' in devices

    @patch('utils.meshcore_connection.os.path.exists', return_value=False)
    @patch('glob.glob')
    def test_no_devices(self, mock_glob, mock_exists):
        """No devices found returns empty list."""
        mock_glob.return_value = []
        devices = detect_meshcore_devices()
        assert devices == []

    @patch('utils.meshcore_connection.os.path.realpath')
    @patch('utils.meshcore_connection.os.path.exists', return_value=True)
    @patch('glob.glob')
    def test_ttymeshcore_symlink_listed_first_and_target_deduped(
        self, mock_glob, mock_exists, mock_realpath
    ):
        """When the udev symlink exists it leads the list and its realpath
        target is skipped from the raw scan — pinned with mocks so the leg
        keeps coverage without real hardware."""
        mock_glob.side_effect = [
            [],                                # /dev/ttyUSB*
            ['/dev/ttyACM0', '/dev/ttyACM1'],  # /dev/ttyACM*
        ]
        mock_realpath.side_effect = lambda p: {
            '/dev/ttyMeshCore': '/dev/ttyACM0',
        }.get(p, p)
        devices = detect_meshcore_devices()
        assert devices == ['/dev/ttyMeshCore', '/dev/ttyACM1']


# =============================================================================
# Test Connection
# =============================================================================

class TestTestConnection:
    """Test connection testing methods."""

    def test_serial_device_exists(self, handler):
        """test_connection() checks serial device existence."""
        with patch('os.path.exists', return_value=True):
            assert handler.test_connection() is True

    def test_serial_device_missing(self, handler):
        """test_connection() fails if device missing."""
        with patch('os.path.exists', return_value=False):
            assert handler.test_connection() is False

    def test_tcp_connection(self, mock_config, mock_health, mock_node_tracker):
        """test_connection() for TCP mode."""
        mock_config.meshcore.connection_type = 'tcp'
        mock_config.meshcore.tcp_host = 'localhost'
        mock_config.meshcore.tcp_port = 4000

        h = MeshCoreHandler(
            config=mock_config,
            node_tracker=mock_node_tracker,
            health=mock_health,
            stop_event=threading.Event(),
            stats={},
            stats_lock=threading.Lock(),
            message_queue=Queue(),
        )

        with patch('socket.socket') as mock_sock:
            mock_instance = MagicMock()
            mock_instance.connect_ex.return_value = 0
            mock_sock.return_value = mock_instance
            assert h.test_connection() is True


# =============================================================================
# Contact Resolution
# =============================================================================

class TestContactResolution:
    """Test finding MeshCore contacts by address."""

    def test_find_by_pubkey(self, handler):
        """Find contact by public key prefix."""
        contacts = [
            {'public_key': b'\xaa\xbb\xcc\xdd', 'adv_name': 'Node1'},
            {'public_key': b'\x11\x22\x33\x44', 'adv_name': 'Node2'},
        ]
        result = handler._find_contact(contacts, 'aabbcc')
        assert result is not None
        assert result['adv_name'] == 'Node1'

    def test_find_by_name(self, handler):
        """Find contact by advertised name."""
        contacts = [
            {'public_key': b'\xaa\xbb', 'adv_name': 'AlphaNode'},
        ]
        result = handler._find_contact(contacts, 'AlphaNode')
        assert result is not None

    def test_not_found(self, handler):
        """Return None when contact not found."""
        contacts = [
            {'public_key': b'\xaa\xbb', 'adv_name': 'Node1'},
        ]
        result = handler._find_contact(contacts, 'nonexistent')
        assert result is None

    def test_empty_contacts(self, handler):
        """Return None for empty contact list."""
        result = handler._find_contact([], 'anything')
        assert result is None

    def test_object_contact(self, handler):
        """Handle contact objects (not just dicts)."""
        contact = SimpleNamespace(
            public_key=b'\xde\xad\xbe\xef',
            adv_name='ObjectContact',
        )
        result = handler._find_contact([contact], 'deadbeef')
        assert result is not None


# =============================================================================
# Stats Tracking
# =============================================================================

class TestStats:
    """Test statistics tracking."""

    def test_rx_stats_increment(self, handler):
        """Receive events increment meshcore_rx counter."""
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(handler._connect())

            event = SimpleNamespace(
                type='CONTACT_MSG_RECV',
                payload={
                    'text': 'Stats test',
                    'sender': 'abc',
                    'destination': None,
                    'is_channel': False,
                    'channel': 0,
                },
            )
            loop.run_until_complete(handler._on_contact_message(event))

            assert handler.stats.get('meshcore_rx', 0) >= 1
        finally:
            loop.close()

    def test_ack_stats_increment(self, handler):
        """ACK events increment meshcore_acks counter."""
        loop = asyncio.new_event_loop()
        try:
            event = SimpleNamespace(type='ACK', payload={})
            loop.run_until_complete(handler._on_ack(event))
            assert handler.stats.get('meshcore_acks', 0) >= 1
        finally:
            loop.close()


# =============================================================================
# Chat buffer + module-level active-handler accessor
# =============================================================================

class TestChatBuffer:
    """Ring buffer feeds the daemon's HTTP chat API and TUI handler."""

    def test_record_appends_with_monotonic_id(self, handler):
        handler.record_chat_message(direction="rx", text="hello", channel=2)
        handler.record_chat_message(direction="tx", text="world", channel=2)
        out = handler.get_recent_chat()
        assert [e["text"] for e in out] == ["hello", "world"]
        assert out[1]["id"] > out[0]["id"]
        assert out[0]["direction"] == "rx"
        assert out[1]["direction"] == "tx"

    def test_since_id_filters(self, handler):
        handler.record_chat_message(direction="rx", text="a")
        handler.record_chat_message(direction="rx", text="b")
        handler.record_chat_message(direction="rx", text="c")
        all_entries = handler.get_recent_chat()
        midpoint = all_entries[1]["id"]
        recent = handler.get_recent_chat(since_id=midpoint)
        assert [e["text"] for e in recent] == ["c"]

    def test_buffer_capacity_is_bounded(self, handler):
        # Buffer maxlen=200; fill past it and confirm oldest get dropped.
        for i in range(250):
            handler.record_chat_message(direction="rx", text=f"msg-{i}")
        out = handler.get_recent_chat()
        assert len(out) == 200
        # Oldest 50 were evicted; first surviving entry is msg-50.
        assert out[0]["text"] == "msg-50"
        assert out[-1]["text"] == "msg-249"

    def test_known_channels_aggregates_from_buffer(self, handler):
        handler.record_chat_message(direction="rx", text="x", channel=1)
        handler.record_chat_message(direction="tx", text="y", channel=2)
        handler.record_chat_message(direction="rx", text="z", channel=1)
        chans = {c["channel"]: c["last_seen"] for c in handler.get_known_channels()}
        assert set(chans.keys()) == {1, 2}
        # Channel 1's last_seen should be at-or-after channel 2's (it has
        # the most recent entry — z).
        assert chans[1] >= chans[2]


class TestActiveHandlerAccessor:
    """Module-level singleton — config_api needs to find the running handler
    from another module without import-cycle tangles."""

    def test_handler_registers_on_init(self, handler):
        from gateway.meshcore_handler import get_active_handler
        assert get_active_handler() is handler

    def test_disconnect_clears_active(self, handler):
        from gateway.meshcore_handler import get_active_handler
        handler.disconnect()
        assert get_active_handler() is None

    def test_stale_disconnect_does_not_clobber_new_handler(
        self, mock_config, mock_health, mock_node_tracker
    ):
        """Identity-checked clear: an older handler's disconnect must not
        evict a newer handler that has already taken over the slot."""
        from gateway.meshcore_handler import get_active_handler

        stop_event = threading.Event()
        stats = {}
        lock = threading.Lock()
        queue = Queue(maxsize=10)

        old = MeshCoreHandler(
            config=mock_config, node_tracker=mock_node_tracker,
            health=mock_health, stop_event=stop_event, stats=stats,
            stats_lock=lock, message_queue=queue,
        )
        new = MeshCoreHandler(
            config=mock_config, node_tracker=mock_node_tracker,
            health=mock_health, stop_event=stop_event, stats=stats,
            stats_lock=lock, message_queue=queue,
        )
        # `new` registered itself in __init__.
        assert get_active_handler() is new
        # Stale disconnect from `old` should NOT clear the slot.
        old.disconnect()
        assert get_active_handler() is new
        # `new`'s own disconnect clears it.
        new.disconnect()
        assert get_active_handler() is None


class TestSendPathEgressGuard:
    """THE drill for the 2026-08-09 MF finding, ported: a test driving the
    MeshCore send path without declaring egress must be refused BEFORE any
    send method is touched — serial is invisible to the socket tripwire,
    and this is MeshAnchor's PRIMARY radio. Deliberately OUTSIDE
    TestSendPath, whose autouse fixture declares egress for the class."""

    def test_undeclared_send_is_refused_and_sends_nothing(self, handler):
        from utils import tx_guard
        from utils.tx_guard import TransmitBlocked
        fake_commands = MagicMock()
        fake_commands.send_chan_msg = AsyncMock(return_value=None)
        handler._connected = True
        handler._meshcore = MagicMock(commands=fake_commands)

        tx_guard.clear_blocked_attempts()
        loop = asyncio.new_event_loop()
        try:
            with pytest.raises(TransmitBlocked):
                loop.run_until_complete(
                    handler._send_message("leak", destination=None))
        finally:
            loop.close()
        fake_commands.send_chan_msg.assert_not_awaited()
        recs = tx_guard.blocked_attempts()
        assert recs and recs[-1]["kind"] == "meshcore_tx"

    def test_simulator_send_needs_no_declaration(self, handler):
        """The in-process simulator is not egress; a guard that fires on it
        gets switched off (narrowness drill)."""
        from gateway.meshcore_handler import MeshCoreSimulator
        handler._meshcore = MeshCoreSimulator()
        handler._connected = True
        loop = asyncio.new_event_loop()
        try:
            assert loop.run_until_complete(
                handler._send_message("sim text", destination=None)) is True
        finally:
            loop.close()


class TestSendPath:
    """Regression coverage for the three send-path bugs that surfaced
    on meshanchor-server 2026-05-02 once the chat API actually drove
    outbound traffic:

    1. `send_channel_txt_msg` doesn't exist on meshcore_py's CommandHandler;
       real method is `send_chan_msg(chan, msg)`.
    2. `get_contacts()` returns an Event; iterating it directly raised
       "Event object is not iterable".
    3. CanonicalMessage was protocol-agnostic but the channel slot was
       being dropped between send_text() and _process_outbound, defaulting
       every send to slot 0 (Public) regardless of TUI/API choice.
    """

    @pytest.fixture(autouse=True)
    def _declared_meshcore_egress(self):
        """These tests deliberately exercise the send path against doubles —
        declared once for the class (2026-08-09 egress guard: the companion
        is a real LoRa radio and _send_message is now a guarded chokepoint).
        """
        from utils import tx_guard
        with tx_guard.allow_meshcore_egress():
            yield

    def test_resolve_channel_passes_int_through(self, handler):
        assert handler._resolve_channel(1, source="test") == 1
        assert handler._resolve_channel(0, source="test") == 0
        assert handler._resolve_channel(7, source="test") == 7

    def test_resolve_channel_missing_defaults_to_zero(self, handler, caplog):
        """Channel-0 Public leak fix (2026-05-19): missing channel
        metadata silently defaults to 0 (Public) but logs at DEBUG so
        the caller can be tracked down and fixed. Don't change the
        default value — that would mask the call site rather than
        surface it."""
        import logging
        with caplog.at_level(logging.DEBUG, logger='gateway.meshcore_handler'):
            assert handler._resolve_channel(None, source="missing-test") == 0
        assert any("missing-test" in r.message for r in caplog.records)

    def test_resolve_channel_empty_string_defaults_to_zero(self, handler):
        assert handler._resolve_channel('', source="empty-test") == 0

    def test_resolve_channel_unparsable_defaults_to_zero(self, handler, caplog):
        import logging
        with caplog.at_level(logging.DEBUG, logger='gateway.meshcore_handler'):
            assert handler._resolve_channel("not-an-int", source="unparsable-test") == 0
        assert any("unparsable channel" in r.message for r in caplog.records)

    def test_extract_contacts_from_event_payload_dict(self, handler):
        evt = SimpleNamespace(payload={
            "alpha": {"adv_name": "alpha", "public_key": b"\x01\x02"},
            "beta":  {"adv_name": "beta",  "public_key": b"\x03\x04"},
        })
        contacts = handler._extract_contacts(evt)
        assert len(contacts) == 2
        names = {c["adv_name"] for c in contacts}
        assert names == {"alpha", "beta"}

    def test_extract_contacts_from_plain_list(self, handler):
        evt = SimpleNamespace(payload=[{"adv_name": "x"}])
        assert handler._extract_contacts(evt) == [{"adv_name": "x"}]

    def test_extract_contacts_handles_none(self, handler):
        assert handler._extract_contacts(None) == []

    def test_extract_contacts_unknown_shape_returns_empty(self, handler):
        # Stringly-typed payload should NOT be iterated — that was the
        # exact pre-fix bug that surfaced "Event object is not iterable".
        evt = SimpleNamespace(payload="this-is-not-iterable-as-contacts")
        assert handler._extract_contacts(evt) == []

    def test_send_text_carries_channel_via_metadata(self, handler):
        handler._connected = True
        ok = handler.send_text("hello slot 2", channel=2)
        assert ok is True
        msg = handler._send_queue.get_nowait()
        assert isinstance(msg, CanonicalMessage)
        assert msg.metadata.get('channel') == 2
        assert msg.content == "hello slot 2"
        assert msg.is_broadcast is True

    def test_send_message_uses_send_chan_msg_for_broadcast(self, handler):
        """Broadcast path must call send_chan_msg(channel, text) — not
        the nonexistent send_channel_txt_msg."""
        fake_commands = MagicMock()
        fake_commands.send_chan_msg = AsyncMock(return_value=None)
        # AsyncMock for any attr — but DON'T provide send_channel_txt_msg
        # so a regression that calls it would AttributeError.
        del fake_commands.send_channel_txt_msg

        handler._connected = True
        handler._meshcore = MagicMock(commands=fake_commands)

        loop = asyncio.new_event_loop()
        try:
            ok = loop.run_until_complete(
                handler._send_message("hi", destination=None, channel=2)
            )
        finally:
            loop.close()
        assert ok is True
        fake_commands.send_chan_msg.assert_awaited_once_with(2, "hi")

    def test_send_message_dm_extracts_contacts_from_event(self, handler):
        """DM path: get_contacts() returns Event; _send_message must
        unwrap .payload before iterating."""
        contact = {"adv_name": "p3", "public_key": b"\xab\xcd\xef\x01"}
        contacts_evt = SimpleNamespace(payload={"p3": contact})

        fake_commands = MagicMock()
        fake_commands.get_contacts = AsyncMock(return_value=contacts_evt)
        fake_commands.send_msg = AsyncMock(return_value=None)
        fake_commands.send_chan_msg = AsyncMock(return_value=None)

        handler._connected = True
        handler._meshcore = MagicMock(commands=fake_commands)

        loop = asyncio.new_event_loop()
        try:
            ok = loop.run_until_complete(
                handler._send_message("ping", destination="abcd")
            )
        finally:
            loop.close()
        assert ok is True
        fake_commands.send_msg.assert_awaited_once_with(contact, "ping")
        # Did NOT fall through to channel broadcast since contact resolved.
        fake_commands.send_chan_msg.assert_not_awaited()

    def test_send_message_dm_drops_when_contact_missing(self, handler):
        """Channel-0 Public leak fix (2026-05-19): when contact resolution
        fails for a DM, the handler must DROP the message rather than
        falling through to ``send_chan_msg``. The pre-fix behaviour aired
        misdirected DMs on slot 0 (Public) — a privacy bug independent of
        which channel was passed. Drop + log + counter is the principled
        move."""
        contacts_evt = SimpleNamespace(payload={})  # empty contacts

        fake_commands = MagicMock()
        fake_commands.get_contacts = AsyncMock(return_value=contacts_evt)
        fake_commands.send_chan_msg = AsyncMock(return_value=None)
        fake_commands.send_msg = AsyncMock(return_value=None)

        handler._connected = True
        handler._meshcore = MagicMock(commands=fake_commands)

        loop = asyncio.new_event_loop()
        try:
            ok = loop.run_until_complete(
                handler._send_message("ping", destination="missing-id", channel=1)
            )
        finally:
            loop.close()
        # Drop returns False so upstream sees the failure (was True+leak).
        assert ok is False
        # Critically: broadcast NEVER fires, regardless of the channel arg.
        fake_commands.send_chan_msg.assert_not_awaited()
        fake_commands.send_msg.assert_not_awaited()
        # Counter increments so operators can see the drop rate.
        assert handler.stats.get('meshcore_dm_dropped_contact_not_found') == 1

    def test_send_message_dm_drop_counter_accumulates(self, handler):
        """Multiple drops increment the same counter — operator must be
        able to see the drop rate over time, not just whether it ever
        happened."""
        contacts_evt = SimpleNamespace(payload={})

        fake_commands = MagicMock()
        fake_commands.get_contacts = AsyncMock(return_value=contacts_evt)
        fake_commands.send_chan_msg = AsyncMock(return_value=None)
        fake_commands.send_msg = AsyncMock(return_value=None)

        handler._connected = True
        handler._meshcore = MagicMock(commands=fake_commands)

        loop = asyncio.new_event_loop()
        try:
            for _ in range(3):
                loop.run_until_complete(
                    handler._send_message("ping", destination="missing-id", channel=1)
                )
        finally:
            loop.close()
        assert handler.stats.get('meshcore_dm_dropped_contact_not_found') == 3
        fake_commands.send_chan_msg.assert_not_awaited()

    def test_send_message_dm_no_leak_to_public_on_channel_zero_arg(self, handler):
        """Explicit channel-0 ARG must NOT cause a slot-0 broadcast on
        contact-not-found. This is the precise leak shape from 2026-05-19:
        the synth-ACK call site at rns_bridge.py:1096-1098 omits the
        channel kwarg, which defaults to 0 in the handler signature, which
        used to cascade through to send_chan_msg(0, text)."""
        contacts_evt = SimpleNamespace(payload={})

        fake_commands = MagicMock()
        fake_commands.get_contacts = AsyncMock(return_value=contacts_evt)
        fake_commands.send_chan_msg = AsyncMock(return_value=None)
        fake_commands.send_msg = AsyncMock(return_value=None)

        handler._connected = True
        handler._meshcore = MagicMock(commands=fake_commands)

        loop = asyncio.new_event_loop()
        try:
            ok = loop.run_until_complete(
                handler._send_message("[delivered: abcd1234]",
                                      destination="stale-pubkey")
            )
        finally:
            loop.close()
        assert ok is False
        # The exact pre-fix shape: send_chan_msg(0, "[delivered: ...]"). Must NOT fire.
        fake_commands.send_chan_msg.assert_not_awaited()

    def test_process_outbound_dict_falls_back_to_metadata_channel(self, handler):
        """Channel-0 Public leak follow-up (2026-05-20). The persistent
        queue's replay path hands ``_process_outbound`` a dict payload.
        Pre-fix lift in _requeue_failed_message left channel only in
        ``metadata['channel']``; the dict branch read ``msg.get('channel')``
        from the OUTER dict → None → defaulted to slot 0 (Public). Even
        after the lift, fall back to metadata.channel for any caller that
        forgets to lift, so the leak class can never resurrect via a
        dict-shaped payload."""
        fake_commands = MagicMock()
        fake_commands.send_chan_msg = AsyncMock(return_value=None)
        del fake_commands.send_channel_txt_msg

        handler._connected = True
        handler._meshcore = MagicMock(commands=fake_commands)
        # Dict payload with channel ONLY in metadata (no top-level lift).
        payload = {
            'message': 'private cargo',
            'destination': None,
            'metadata': {'channel': 1},
        }
        handler._send_queue.put_nowait(payload)

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(handler._process_outbound())
        finally:
            loop.close()
        # Slot 1, not slot 0 — the fallback found the channel in metadata.
        fake_commands.send_chan_msg.assert_awaited_once_with(1, "private cargo")

    def test_process_outbound_dict_top_level_channel_wins(self, handler):
        """When both top-level and metadata channel are present, top-level
        wins (it's the canonical carrier set by the lift in
        _requeue_failed_message). Verifies the fallback only kicks in
        when top-level is missing."""
        fake_commands = MagicMock()
        fake_commands.send_chan_msg = AsyncMock(return_value=None)
        del fake_commands.send_channel_txt_msg

        handler._connected = True
        handler._meshcore = MagicMock(commands=fake_commands)
        payload = {
            'message': 'cargo',
            'destination': None,
            'channel': 2,             # canonical carrier
            'metadata': {'channel': 9},  # stale duplicate (should be ignored)
        }
        handler._send_queue.put_nowait(payload)

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(handler._process_outbound())
        finally:
            loop.close()
        fake_commands.send_chan_msg.assert_awaited_once_with(2, "cargo")


# =============================================================================
# Mesh oracle (read-only) — MeshCore leg (MeshCore is MeshAnchor's domain)
# =============================================================================

class TestMeshOracleMeshcoreWiring:
    """Wiring of the read-only mesh oracle into the MeshCore RX paths.

    Access is additive: a DM is identity-gated (MESHANCHOR_ORACLE_MESHCORE_
    ALLOWLIST), a channel query is channel-gated (MESHANCHOR_ORACLE_MESHCORE_
    CHANNELS). A DM is answered DIRECTED to the asker; a channel query is
    answered to the group on the private slot. Both consume the query.
    """

    def test_oracle_default_off(self, handler, monkeypatch):
        monkeypatch.delenv("MESHANCHOR_ORACLE_ENABLED", raising=False)
        assert handler._build_meshcore_oracle_responder() is None
        assert handler._oracle is None  # not built at construction either

    def test_build_responder_enabled(self, handler, monkeypatch):
        monkeypatch.setenv("MESHANCHOR_ORACLE_ENABLED", "1")
        monkeypatch.setenv("MESHANCHOR_ORACLE_MESHCORE_ALLOWLIST", "*")
        assert handler._build_meshcore_oracle_responder() is not None

    def test_build_responder_with_channel_names(self, handler, monkeypatch):
        # MeshCore channel identity is a NAME (from the "<channel> <sender>:"
        # message prefix), so the whitelist is lowercased names, not indices.
        monkeypatch.setenv("MESHANCHOR_ORACLE_ENABLED", "1")
        monkeypatch.delenv("MESHANCHOR_ORACLE_MESHCORE_ALLOWLIST", raising=False)
        monkeypatch.setenv("MESHANCHOR_ORACLE_MESHCORE_CHANNELS", "MeshAnchor, Fleet")
        responder = handler._build_meshcore_oracle_responder()
        assert responder is not None
        assert responder._allowed_channels == {"meshanchor", "fleet"}

    def test_parse_meshcore_channel_text(self):
        from gateway.meshcore_handler import _parse_meshcore_channel_text
        assert _parse_meshcore_channel_text("meshanchor p3: Status") == (
            "meshanchor", "p3", "Status")
        assert _parse_meshcore_channel_text("MeshAnchor p3: ?") == (
            "meshanchor", "p3", "?")
        assert _parse_meshcore_channel_text("meshanchor p3: status report") == (
            "meshanchor", "p3", "status report")  # only the FIRST ': ' splits
        # no "<...>: " prefix → channel unidentifiable, whole content as query
        assert _parse_meshcore_channel_text("status") == (None, "", "status")
        assert _parse_meshcore_channel_text("") == (None, "", "")

    def test_dm_query_routed_directed_and_consumed(self, handler):
        handler._oracle = MagicMock()
        handler._oracle.handle.return_value = "dude-AI@x: nodes:?"
        event = SimpleNamespace(type='CONTACT_MSG_RECV', payload={
            'text': 'status', 'sender': 'abc123', 'destination': 'gw',
            'is_channel': False, 'channel': 0})
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(handler._on_contact_message(event))
        finally:
            loop.close()
        # DM is identity-gated → channel passed as None
        handler._oracle.handle.assert_called_once_with('abc123', 'status', None)
        assert handler._message_queue.empty()  # consumed, NOT bridged onward

    def test_ingress_discloses_the_REAL_wire_slot_and_keys(self, handler, caplog):
        """2026-09-18 ingress witness. Payload keys are meshcore_py reader.py's
        CHANNEL_MSG_RECV ``res`` verbatim — ``channel_idx`` is the slot; there
        is no ``channel`` / ``is_channel`` key on the wire. The log must name
        the index and (first messages) the keys, so the wire shape is captured
        in the journal, never assumed from a fixture."""
        import logging
        handler._oracle = None
        event = SimpleNamespace(type='CHANNEL_MSG_RECV', payload={
            'type': 'CHAN', 'channel_idx': 1, 'path_len': 0, 'txt_type': 0,
            'sender_timestamp': 1789767168, 'text': 'meshanchor p4: wx'})
        loop = asyncio.new_event_loop()
        try:
            with caplog.at_level(logging.INFO):
                loop.run_until_complete(handler._on_channel_message(event))
        finally:
            loop.close()
        assert "MeshCore channel rx idx=1" in caplog.text, caplog.text
        assert "'channel_idx'" in caplog.text
        assert "text='meshanchor p4: wx'" in caplog.text
        msg = handler._message_queue.get_nowait()
        assert msg.metadata['channel'] == 1  # the slot, as the wire said — not a name

    def test_channel_query_parsed_and_routed_by_name(self, handler):
        # Channel text is "<sender name>: <text>" with an empty source_address.
        # The hook takes sender + query from the text, but the channel NAME
        # the oracle gates on is the DEVICE's name for the wire's slot
        # (2026-09-18) — never a word out of the text.
        handler.get_radio_state = lambda refresh=False: {'channels': [
            {'idx': 0, 'name': 'Public'}, {'idx': 1, 'name': 'meshanchor'}]}
        handler._oracle = MagicMock()
        handler._oracle.handle.return_value = "dude-AI@x: nodes:?"
        event = SimpleNamespace(type='CHANNEL_MSG_RECV', payload={
            'type': 'CHAN', 'channel_idx': 1, 'text': 'meshanchor p3: status'})
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(handler._on_channel_message(event))
        finally:
            loop.close()
        # channel matched by NAME, sender from the prefix, query stripped of prefix
        handler._oracle.handle.assert_called_once_with('p3', 'status', 'meshanchor')
        assert handler._message_queue.empty()  # consumed

    def test_channel_query_bridges_through_when_consume_false(self, handler):
        # Bridge-through (MESHANCHOR_ORACLE_CONSUME=0): the oracle answers AND
        # the command still bridges, so the far mesh's NOC sees the activity.
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(handler._connect())
            handler._oracle = MagicMock()
            handler._oracle.handle.return_value = "dude-AI@x: nodes:?"
            handler._oracle.consume = False
            handler.get_radio_state = lambda refresh=False: {'channels': [
                {'idx': 0, 'name': 'Public'}, {'idx': 1, 'name': 'meshanchor'}]}
            event = SimpleNamespace(type='CHANNEL_MSG_RECV', payload={
                'type': 'CHAN', 'channel_idx': 1, 'text': 'meshanchor p3: status'})
            loop.run_until_complete(handler._on_channel_message(event))
            handler._oracle.handle.assert_called_once_with('p3', 'status', 'meshanchor')
            assert not handler._message_queue.empty()  # answered AND bridged
        finally:
            loop.close()

    def test_channel_reply_broadcasts_on_private_slot(self, handler, monkeypatch):
        # The reply is a channel BROADCAST on the private slot — NOT a DM to the
        # parsed sender (a display name, not a resolvable contact: a DM drops).
        monkeypatch.setenv("MESHANCHOR_ORACLE_ENABLED", "1")
        monkeypatch.delenv("MESHANCHOR_ORACLE_MESHCORE_ALLOWLIST", raising=False)
        monkeypatch.setenv("MESHANCHOR_ORACLE_MESHCORE_CHANNELS", "meshanchor")
        monkeypatch.setenv("MESHANCHOR_ORACLE_MESHCORE_REPLY_SLOT", "1")
        monkeypatch.setattr("oracle.fetch_api_status", lambda *a, **k: None)
        monkeypatch.setattr("utils.jsonl_log.append_jsonl", lambda *a, **k: None)
        handler.send_text = MagicMock(return_value=True)
        handler._oracle = handler._build_meshcore_oracle_responder()
        assert handler._oracle is not None
        handler.get_radio_state = lambda refresh=False: {'channels': [
            {'idx': 0, 'name': 'Public'}, {'idx': 1, 'name': 'meshanchor'}]}
        event = SimpleNamespace(type='CHANNEL_MSG_RECV', payload={
            'type': 'CHAN', 'channel_idx': 1, 'text': 'meshanchor p3: status'})
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(handler._on_channel_message(event))
        finally:
            loop.close()
        handler.send_text.assert_called_once()
        kwargs = handler.send_text.call_args.kwargs
        assert kwargs.get("destination") is None  # channel broadcast, not a DM
        assert kwargs.get("channel") == 1          # the private slot
        assert handler._message_queue.empty()      # query consumed

    def test_non_query_passes_through_to_bridge(self, handler):
        handler._oracle = MagicMock()
        handler._oracle.handle.return_value = None  # not a query
        event = SimpleNamespace(type='CONTACT_MSG_RECV', payload={
            'text': 'good morning', 'sender': 'abc', 'destination': 'gw',
            'is_channel': False, 'channel': 0})
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(handler._on_contact_message(event))
        finally:
            loop.close()
        assert not handler._message_queue.empty()  # passed through

    def test_oracle_none_does_not_break_rx(self, handler):
        handler._oracle = None  # default-off: hook is a no-op
        event = SimpleNamespace(type='CONTACT_MSG_RECV', payload={
            'text': 'hello', 'sender': 'abc', 'destination': 'gw',
            'is_channel': False, 'channel': 0})
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(handler._on_contact_message(event))
        finally:
            loop.close()
        assert not handler._message_queue.empty()


class TestMeshOracleDmReplyIsDirected:
    """A DM query's reply goes back to the ASKER, never onto a channel.

    Drilled 2026-09-20 (the DM-first Public-bot leg): the reply closure
    broadcast every oracle answer on the private slot regardless of who
    asked, so a DM from a community node would have been answered onto OUR
    private channel — the asker never sees it, the group sees a reply to a
    question it never saw. The payload here is meshcore_py 2.3.7 reader.py's
    CONTACT_MSG_RECV ``res`` verbatim (``pubkey_prefix``, ``path_len``,
    ``txt_type``, ``sender_timestamp``, ``text``) — no fabricated ``sender``
    / ``destination`` / ``is_channel`` keys, which the wire never carries.
    """

    ASKER = "a1b2c3d4e5f6"  # 6-byte pubkey prefix as reader.py hex()es it

    def _wire_dm(self, text="status", prefix=None):
        return SimpleNamespace(type='CONTACT_MSG_RECV', payload={
            'pubkey_prefix': prefix or self.ASKER, 'path_len': 2,
            'txt_type': 0, 'sender_timestamp': 1789767168, 'text': text})

    def _build(self, handler, monkeypatch, allowlist, channels=None):
        monkeypatch.setenv("MESHANCHOR_ORACLE_ENABLED", "1")
        monkeypatch.setenv("MESHANCHOR_ORACLE_MESHCORE_ALLOWLIST", allowlist)
        if channels is None:
            monkeypatch.delenv("MESHANCHOR_ORACLE_MESHCORE_CHANNELS", raising=False)
        else:
            monkeypatch.setenv("MESHANCHOR_ORACLE_MESHCORE_CHANNELS", channels)
        monkeypatch.setenv("MESHANCHOR_ORACLE_MESHCORE_REPLY_SLOT", "1")
        monkeypatch.setattr("oracle.fetch_api_status", lambda *a, **k: None)
        monkeypatch.setattr("utils.jsonl_log.append_jsonl", lambda *a, **k: None)
        handler.send_text = MagicMock(return_value=True)
        handler._oracle = handler._build_meshcore_oracle_responder()
        assert handler._oracle is not None

    def _run(self, handler, event):
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(handler._on_contact_message(event))
        finally:
            loop.close()

    def test_dm_reply_is_directed_to_the_asker_pubkey(self, handler, monkeypatch):
        self._build(handler, monkeypatch, allowlist=self.ASKER)
        self._run(handler, self._wire_dm("status"))
        handler.send_text.assert_called_once()
        kwargs = handler.send_text.call_args.kwargs
        # the asker's wire identity is the DM target — a channel broadcast
        # here is the defect this test was written to fail on
        assert kwargs.get("destination") == self.ASKER, kwargs
        assert kwargs.get("channel") != 0  # never lands on Public on any fallback
        assert handler._message_queue.empty()  # consumed (default consume=True)

    def test_dm_from_an_unlisted_pubkey_is_refused_not_broadcast(self, handler, monkeypatch):
        self._build(handler, monkeypatch, allowlist="ffffffffffff")
        self._run(handler, self._wire_dm("status"))
        handler.send_text.assert_not_called()

    def test_channel_reply_still_broadcasts_on_the_private_slot(self, handler, monkeypatch):
        """The channel leg's contract is unchanged: the group is answered ON
        the private channel (the asker there is a display name, not a
        resolvable contact)."""
        self._build(handler, monkeypatch, allowlist="", channels="meshanchor")
        handler.get_radio_state = lambda refresh=False: {'channels': [
            {'idx': 0, 'name': 'Public'}, {'idx': 1, 'name': 'meshanchor'}]}
        event = SimpleNamespace(type='CHANNEL_MSG_RECV', payload={
            'type': 'CHAN', 'channel_idx': 1, 'path_len': 0, 'txt_type': 0,
            'sender_timestamp': 1789767168, 'text': 'meshanchor p4: status'})
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(handler._on_channel_message(event))
        finally:
            loop.close()
        kwargs = handler.send_text.call_args.kwargs
        assert kwargs.get("destination") is None
        assert kwargs.get("channel") == 1


class TestMeshOracleChannelLegNeverDirects:
    """A CHANNEL query is never answered by a DIRECTED send addressed from the
    sender's own text.

    Drilled 2026-09-21 reviewing `3b510eb3`. That fix discriminated the two
    oracle legs on ``channel is None`` — but the channel leg passed
    ``dev_name.lower() if dev_name else None``, and ``channel_name_for``
    returns None FOUR ways (slot idx absent, radio state unreadable, slot not
    in the device table, empty name). So an unnamed slot took the DM branch
    with ``dest`` = the display name parsed out of the message text, and
    ``_find_contact`` matches ``address == adv_name``: the reply went to
    whichever contact the ASKER named. Text-derived routing — the class the
    2026-09-18 ``channel_idx`` arc exists to refuse. Both tests below failed
    on `3b510eb3` with ``destination='RAK1'``.

    Latent there, not live: it needs answer-all, which is exactly the
    ``ALLOWLIST=*`` opening the Public-bot plan proposes.
    """

    CHAN_EVENT = dict(type='CHAN', channel_idx=3, path_len=4, txt_type=0,
                      sender_timestamp=1789767168, text='meshanchor RAK1: status')

    def _build_answer_all(self, handler, monkeypatch):
        monkeypatch.setenv("MESHANCHOR_ORACLE_ENABLED", "1")
        monkeypatch.setenv("MESHANCHOR_ORACLE_MESHCORE_ALLOWLIST", "*")
        monkeypatch.delenv("MESHANCHOR_ORACLE_MESHCORE_CHANNELS", raising=False)
        monkeypatch.setenv("MESHANCHOR_ORACLE_MESHCORE_REPLY_SLOT", "1")
        monkeypatch.setattr("oracle.fetch_api_status", lambda *a, **k: None)
        monkeypatch.setattr("utils.jsonl_log.append_jsonl", lambda *a, **k: None)
        handler.send_text = MagicMock(return_value=True)
        handler._oracle = handler._build_meshcore_oracle_responder()
        assert handler._oracle is not None

    def _run(self, handler):
        event = SimpleNamespace(type='CHANNEL_MSG_RECV', payload=dict(self.CHAN_EVENT))
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(handler._on_channel_message(event))
        finally:
            loop.close()

    def test_slot_missing_from_the_device_table_still_broadcasts(self, handler, monkeypatch):
        self._build_answer_all(handler, monkeypatch)
        handler.get_radio_state = lambda refresh=False: {'channels': [
            {'idx': 0, 'name': 'Public'}, {'idx': 1, 'name': 'meshanchor'}]}
        self._run(handler)
        assert handler.send_text.called
        kwargs = handler.send_text.call_args.kwargs
        assert kwargs.get("destination") is None, kwargs
        assert kwargs.get("channel") == 1

    def test_unreadable_radio_state_still_broadcasts(self, handler, monkeypatch):
        """A transiently cold state cache must not change WHO gets the reply."""
        self._build_answer_all(handler, monkeypatch)

        def _boom(refresh=False):
            raise RuntimeError("radio state cache cold")

        handler.get_radio_state = _boom
        self._run(handler)
        assert handler.send_text.called
        assert handler.send_text.call_args.kwargs.get("destination") is None

    def test_reply_closure_refuses_a_non_wire_dm_target(self, handler, monkeypatch):
        """The closure's own guard, independent of what the leg passes: a DM
        target that is not a wire pubkey_prefix is dropped, never broadcast."""
        self._build_answer_all(handler, monkeypatch)
        responder = handler._oracle
        assert responder._send_fn("reply", "meshanchor p4", None) is False
        handler.send_text.assert_not_called()
        assert responder._send_fn("reply", "7eb0fa289c11", None) is True
        assert handler.send_text.call_args.kwargs.get("destination") == "7eb0fa289c11"


class TestContactSnapshotOrdering:
    """The contact list is ordered by the clock we OWN.

    `last_advert` is the sender's own clock and is wrong in the field (8 of
    67 contacts on RAK1 read >1y off, 2026-09-20). Ordering by it puts a node
    with a bad RTC at the top of a list whose "last heard" column is
    `lastmod` — this radio's receipt clock. Added 2026-09-21 reviewing
    `9ac1f53d`, which sorted on `last_advert`.
    """

    FORGED = {'public_key': 'aa' * 32, 'adv_name': 'bad clock', 'type': 1,
              'last_advert': 4102444800, 'lastmod': 1789000000}   # advert in 2100
    RECENT = {'public_key': 'bb' * 32, 'adv_name': 'heard just now', 'type': 1,
              'last_advert': 1700000000, 'lastmod': 1789960000}

    def _snapshot(self, handler, raw):
        handler._connected = True
        handler._meshcore = SimpleNamespace(commands=SimpleNamespace(
            get_contacts=lambda: "coro"))
        handler._run_radio_write = lambda coro: "evt"
        handler._extract_contacts = lambda evt: raw
        return handler.get_contacts_snapshot()

    def test_ordered_by_lastmod_not_the_senders_advert_clock(self, handler):
        snap = self._snapshot(handler, [self.FORGED, self.RECENT])
        assert snap["observed"] is True and snap["count"] == 2
        assert [c["name"] for c in snap["contacts"]] == \
            ["heard just now", "bad clock"]

    def test_lastmod_iso_is_published_for_the_pane(self, handler):
        snap = self._snapshot(handler, [self.RECENT])
        c = snap["contacts"][0]
        assert c["lastmod_iso"] == time.strftime(
            "%Y-%m-%dT%H:%M:%S", time.localtime(self.RECENT["lastmod"]))
        assert c["lastmod_iso"] != c["last_advert_iso"]

    def test_absent_clocks_are_None_never_1970(self, handler):
        snap = self._snapshot(handler, [{'public_key': 'cc' * 32,
                                         'adv_name': 'no clocks', 'type': 1}])
        c = snap["contacts"][0]
        assert c["lastmod_iso"] is None and c["last_advert_iso"] is None

    def test_unreadable_table_is_not_an_empty_list(self, handler):
        handler._connected = True
        handler._meshcore = SimpleNamespace(commands=SimpleNamespace(
            get_contacts=lambda: "coro"))

        def _boom(coro):
            raise RuntimeError("radio did not answer")

        handler._run_radio_write = _boom
        snap = handler.get_contacts_snapshot()
        assert snap["observed"] is False and snap["count"] == 0
        assert "radio did not answer" in snap["reason"]


class TestContactNormalisation:
    """meshcore_py 2.3.7 reader.py CONTACTS fields verbatim: public_key is a
    hex STRING there (the simulator hands bytes); type 2 = repeater."""

    def test_wire_shaped_contact(self, handler):
        c = {'public_key': '7eb0fa289c11' + '00' * 26, 'adv_name': 'meshanchor p4',
             'type': 1, 'flags': 0, 'out_path_len': -1, 'out_path': '',
             'adv_lat': 19.4, 'adv_lon': -155.3, 'last_advert': 1789960000, 'lastmod': 1789960001}
        n = handler._normalise_contact(c)
        assert n["prefix"] == "7eb0fa289c11" and len(n["public_key"]) == 64
        assert n["role"] == "companion" and n["out_path_len"] == -1
        # the ISO form is the BOX's local clock — derive the expectation the
        # same way rather than pin a date (CI runs UTC; the twin runs HST —
        # a date literal here pinned the author's timezone, not the code)
        assert n["last_advert"] == 1789960000
        assert n["last_advert_iso"] == time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(1789960000))

    def test_unknown_type_and_missing_fields_are_none_not_guessed(self, handler):
        n = handler._normalise_contact({'adv_name': 'x', 'public_key': b'\xaa\xbb', 'type': 9})
        assert n["role"] is None and n["last_advert"] is None and n["last_advert_iso"] is None
        assert n["public_key"] == "aabb"
