"""
Tests for standalone message listener.

Run: python3 -m pytest tests/test_message_listener.py -v
"""

import pytest
from datetime import datetime
from unittest.mock import patch, MagicMock

from src.utils.message_listener import (
    MessageListener,
    ListenerStatus,
    CONNECTED,
    CONNECTING,
    DISCONNECTED,
    ERROR,
    get_listener,
    diagnose_pubsub,
)


class TestListenerStatus:
    """Tests for ListenerStatus dataclass."""

    def test_default_state(self):
        """Test default status is disconnected."""
        status = ListenerStatus(state=DISCONNECTED)
        assert status.state == DISCONNECTED
        assert status.messages_received == 0
        assert status.connected_since is None
        assert status.error is None

    def test_to_dict(self):
        """Test status serialization."""
        now = datetime.now()
        status = ListenerStatus(
            state=CONNECTED,
            connected_since=now,
            messages_received=5,
            last_message_time=now,
        )
        d = status.to_dict()
        assert d['state'] == CONNECTED
        assert d['messages_received'] == 5
        assert d['connected_since'] is not None

    def test_to_dict_with_error(self):
        """Test status with error serialization."""
        status = ListenerStatus(state=ERROR, error="Connection refused")
        d = status.to_dict()
        assert d['state'] == ERROR
        assert d['error'] == "Connection refused"


class TestMessageListener:
    """Tests for MessageListener class."""

    def test_init_defaults(self):
        """Test default initialization."""
        listener = MessageListener()
        assert listener.host == "localhost"
        assert listener.store_messages is True
        assert listener._running is False

    def test_init_custom_host(self):
        """Test custom host initialization."""
        listener = MessageListener(host="192.168.1.100")
        assert listener.host == "192.168.1.100"

    def test_get_status_initial(self):
        """Test initial status is disconnected."""
        listener = MessageListener()
        status = listener.get_status()
        assert status.state == DISCONNECTED

    def test_callback_registration(self):
        """Test callback add/remove."""
        listener = MessageListener()

        def my_callback(msg):
            pass

        listener.add_callback(my_callback)
        assert my_callback in listener._callbacks

        listener.remove_callback(my_callback)
        assert my_callback not in listener._callbacks

    def test_callback_remove_nonexistent(self):
        """Test removing non-existent callback doesn't raise."""
        listener = MessageListener()

        def my_callback(msg):
            pass

        # Should not raise
        listener.remove_callback(my_callback)

    def test_start_already_running(self):
        """Test start when already running returns True."""
        listener = MessageListener()
        listener._running = True
        result = listener.start()
        assert result is True

    def test_stop_clears_state(self):
        """Test stop sets disconnected state."""
        listener = MessageListener()
        listener._running = True
        listener._status.state = CONNECTED
        listener.stop()
        assert listener._running is False
        assert listener._status.state == DISCONNECTED


class TestMessageHandling:
    """Tests for message parsing."""

    def test_handle_text_message_structure(self):
        """Test text message packet parsing."""
        listener = MessageListener(store_messages=False)
        listener._status.state = CONNECTED

        # Track callback invocations
        received = []
        listener.add_callback(lambda msg: received.append(msg))

        # Simulate packet
        packet = {
            'fromId': '!abc12345',
            'toId': '^all',
            'channel': 0,
            'rxSnr': 5.5,
            'rxRssi': -90,
            'hopStart': 3,
            'hopLimit': 2,
            'decoded': {
                'portnum': 'TEXT_MESSAGE_APP',
                'payload': b'Hello mesh!'
            }
        }

        listener._handle_text_message(
            packet,
            packet['decoded'],
            packet['fromId']
        )

        assert len(received) == 1
        msg = received[0]
        assert msg['from_id'] == '!abc12345'
        assert msg['content'] == 'Hello mesh!'
        assert msg['channel'] == 0
        assert msg['snr'] == 5.5
        assert msg['rssi'] == -90
        assert msg['hops_away'] == 1
        assert msg['is_broadcast'] is True

    def test_handle_empty_message_ignored(self):
        """Test empty messages are ignored."""
        listener = MessageListener(store_messages=False)
        received = []
        listener.add_callback(lambda msg: received.append(msg))

        packet = {
            'fromId': '!abc12345',
            'decoded': {
                'portnum': 'TEXT_MESSAGE_APP',
                'payload': b''
            }
        }

        listener._handle_text_message(packet, packet['decoded'], '!abc12345')
        assert len(received) == 0

    def test_direct_message_detection(self):
        """Test DM vs broadcast detection."""
        listener = MessageListener(store_messages=False)
        received = []
        listener.add_callback(lambda msg: received.append(msg))

        # Direct message packet
        packet = {
            'fromId': '!sender',
            'toId': '!receiver',
            'decoded': {
                'portnum': 'TEXT_MESSAGE_APP',
                'payload': b'Private message'
            }
        }

        listener._handle_text_message(packet, packet['decoded'], '!sender')
        assert received[0]['is_broadcast'] is False
        assert received[0]['to_id'] == '!receiver'


class TestDiagnostics:
    """Tests for diagnostic functions."""

    def test_diagnose_pubsub_structure(self):
        """Test diagnose_pubsub returns expected structure."""
        result = diagnose_pubsub()

        assert 'pubsub_available' in result
        assert 'meshtastic_available' in result
        assert 'subscriptions' in result
        assert 'errors' in result
        assert isinstance(result['subscriptions'], list)
        assert isinstance(result['errors'], list)

    @patch('socket.socket')
    def test_diagnose_pubsub_port_check(self, mock_socket):
        """Test port check in diagnostics."""
        mock_sock = MagicMock()
        mock_sock.connect_ex.return_value = 0
        mock_socket.return_value = mock_sock

        result = diagnose_pubsub()
        # If pubsub module exists, this should work
        assert 'meshtasticd_port_open' in result


class TestSingleton:
    """Tests for singleton pattern."""

    def test_get_listener_returns_same_instance(self):
        """Test get_listener returns singleton."""
        import src.utils.message_listener as ml
        ml._listener = None  # Reset singleton

        listener1 = get_listener()
        listener2 = get_listener()
        assert listener1 is listener2

        ml._listener = None  # Clean up
