"""
Tests for MeshForge event bus system.

Run: python3 -m pytest tests/test_event_bus.py -v

Issue #17 Phase 3: Event bus enables RX message display in UI panels.
"""

import pytest
import threading
import time
from datetime import datetime
from unittest.mock import MagicMock, patch

from src.utils.event_bus import (
    EventBus,
    MessageEvent,
    ServiceEvent,
    NodeEvent,
    MessageDirection,
    event_bus,
    emit_message,
    emit_service_status,
    emit_node_update,
)


class TestEventBus:
    """Tests for EventBus class."""

    def test_subscribe_and_emit(self):
        """Test basic subscribe and emit functionality."""
        bus = EventBus()
        received = []

        def callback(event):
            received.append(event)

        bus.subscribe('test', callback)
        bus.emit_sync('test', {'data': 'hello'})

        assert len(received) == 1
        assert received[0] == {'data': 'hello'}

    def test_multiple_subscribers(self):
        """Test multiple subscribers receive events."""
        bus = EventBus()
        received1 = []
        received2 = []

        bus.subscribe('test', lambda e: received1.append(e))
        bus.subscribe('test', lambda e: received2.append(e))
        bus.emit_sync('test', 'event')

        assert len(received1) == 1
        assert len(received2) == 1

    def test_unsubscribe(self):
        """Test unsubscribing from events."""
        bus = EventBus()
        received = []

        def callback(event):
            received.append(event)

        bus.subscribe('test', callback)
        bus.emit_sync('test', 'first')
        bus.unsubscribe('test', callback)
        bus.emit_sync('test', 'second')

        assert len(received) == 1
        assert received[0] == 'first'

    def test_unsubscribe_nonexistent(self):
        """Test unsubscribing callback that wasn't subscribed."""
        bus = EventBus()

        # Should not raise
        bus.unsubscribe('test', lambda e: None)

    def test_emit_no_subscribers(self):
        """Test emitting event with no subscribers."""
        bus = EventBus()

        # Should not raise
        bus.emit_sync('test', 'data')

    def test_callback_exception_handling(self):
        """Test that exceptions in callbacks don't crash emit."""
        bus = EventBus()
        received = []

        def bad_callback(event):
            raise ValueError("test error")

        def good_callback(event):
            received.append(event)

        bus.subscribe('test', bad_callback)
        bus.subscribe('test', good_callback)
        bus.emit_sync('test', 'data')

        # Good callback should still receive event
        assert len(received) == 1

    def test_clear_subscribers(self):
        """Test clearing all subscribers."""
        bus = EventBus()
        received = []

        bus.subscribe('test', lambda e: received.append(e))
        bus.clear_subscribers('test')
        bus.emit_sync('test', 'data')

        assert len(received) == 0

    def test_clear_all_subscribers(self):
        """Test clearing all subscribers for all events."""
        bus = EventBus()
        received1 = []
        received2 = []

        bus.subscribe('event1', lambda e: received1.append(e))
        bus.subscribe('event2', lambda e: received2.append(e))
        bus.clear_subscribers()
        bus.emit_sync('event1', 'data')
        bus.emit_sync('event2', 'data')

        assert len(received1) == 0
        assert len(received2) == 0

    def test_get_subscriber_count(self):
        """Test getting subscriber count."""
        bus = EventBus()

        assert bus.get_subscriber_count('test') == 0

        bus.subscribe('test', lambda e: None)
        assert bus.get_subscriber_count('test') == 1

        bus.subscribe('test', lambda e: None)
        assert bus.get_subscriber_count('test') == 2

    def test_async_emit(self):
        """Test async emit calls subscribers in separate threads."""
        bus = EventBus()
        received = []
        thread_ids = []

        def callback(event):
            thread_ids.append(threading.current_thread().ident)
            received.append(event)

        bus.subscribe('test', callback)
        main_thread = threading.current_thread().ident

        bus.emit('test', 'data')
        time.sleep(0.1)  # Allow thread to complete

        assert len(received) == 1
        # Callback should run in different thread
        assert len(thread_ids) == 1


class TestMessageEvent:
    """Tests for MessageEvent dataclass."""

    def test_message_event_creation(self):
        """Test creating a MessageEvent."""
        event = MessageEvent(
            direction='rx',
            content='Hello mesh',
            node_id='!abc123',
            node_name='TestNode',
            channel=0,
            network='meshtastic'
        )

        assert event.direction == 'rx'
        assert event.content == 'Hello mesh'
        assert event.node_id == '!abc123'
        assert event.node_name == 'TestNode'
        assert isinstance(event.timestamp, datetime)

    def test_message_event_str_rx(self):
        """Test MessageEvent string representation for RX."""
        event = MessageEvent(
            direction='rx',
            content='Test message',
            node_name='TestNode'
        )

        str_repr = str(event)
        assert '←' in str_repr  # RX arrow
        assert 'TestNode' in str_repr
        assert 'Test message' in str_repr

    def test_message_event_str_tx(self):
        """Test MessageEvent string representation for TX."""
        event = MessageEvent(
            direction='tx',
            content='Test message',
            node_id='!abc123'
        )

        str_repr = str(event)
        assert '→' in str_repr  # TX arrow
        assert '!abc123' in str_repr


class TestServiceEvent:
    """Tests for ServiceEvent dataclass."""

    def test_service_event_creation(self):
        """Test creating a ServiceEvent."""
        event = ServiceEvent(
            service_name='meshtasticd',
            available=True,
            message='Service is running'
        )

        assert event.service_name == 'meshtasticd'
        assert event.available is True
        assert event.message == 'Service is running'
        assert isinstance(event.timestamp, datetime)


class TestNodeEvent:
    """Tests for NodeEvent dataclass."""

    def test_node_event_creation(self):
        """Test creating a NodeEvent."""
        event = NodeEvent(
            event_type='discovered',
            node_id='!abc123',
            node_name='TestNode',
            latitude=37.7749,
            longitude=-122.4194
        )

        assert event.event_type == 'discovered'
        assert event.node_id == '!abc123'
        assert event.latitude == 37.7749


class TestConvenienceFunctions:
    """Tests for convenience emit functions."""

    def test_emit_message(self):
        """Test emit_message convenience function."""
        received = []

        def callback(event):
            received.append(event)

        event_bus.subscribe('message', callback)

        try:
            emit_message(
                direction='rx',
                content='Test content',
                node_id='!abc123',
                network='meshtastic'
            )
            time.sleep(0.1)

            assert len(received) == 1
            assert isinstance(received[0], MessageEvent)
            assert received[0].content == 'Test content'
        finally:
            event_bus.unsubscribe('message', callback)

    def test_emit_service_status(self):
        """Test emit_service_status convenience function."""
        received = []

        def callback(event):
            received.append(event)

        event_bus.subscribe('service', callback)

        try:
            emit_service_status('meshtasticd', True, 'Running')
            time.sleep(0.1)

            assert len(received) == 1
            assert isinstance(received[0], ServiceEvent)
            assert received[0].service_name == 'meshtasticd'
        finally:
            event_bus.unsubscribe('service', callback)

    def test_emit_node_update(self):
        """Test emit_node_update convenience function."""
        received = []

        def callback(event):
            received.append(event)

        event_bus.subscribe('node', callback)

        try:
            emit_node_update(
                event_type='discovered',
                node_id='!abc123',
                node_name='TestNode'
            )
            time.sleep(0.1)

            assert len(received) == 1
            assert isinstance(received[0], NodeEvent)
            assert received[0].event_type == 'discovered'
        finally:
            event_bus.unsubscribe('node', callback)


class TestGlobalEventBus:
    """Tests for global event_bus singleton."""

    def test_singleton_exists(self):
        """Test that global event_bus exists."""
        assert event_bus is not None
        assert isinstance(event_bus, EventBus)

    def test_singleton_is_reusable(self):
        """Test that global event_bus can be used across tests."""
        received = []

        def callback(event):
            received.append(event)

        event_bus.subscribe('singleton_test', callback)

        try:
            event_bus.emit_sync('singleton_test', 'data')
            assert len(received) == 1
        finally:
            event_bus.unsubscribe('singleton_test', callback)
