"""
Tests for CanonicalMessage — protocol-agnostic message format.

Tests cover:
- Factory methods (from_meshtastic, from_meshcore, from_rns, from_bridged_message)
- Serialization (to_meshtastic_text, to_meshcore_text, to_bridged_message)
- Text truncation for payload-limited protocols
- Internet origin filtering
- Round-trip fidelity
"""

import sys
import os
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

# Ensure src is on path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from gateway.canonical_message import (
    CanonicalMessage,
    MessageType,
    Protocol,
    MESHCORE_MAX_TEXT,
    MESHTASTIC_MAX_PAYLOAD,
    TRUNCATION_INDICATOR,
    _truncate_utf8,
    _portnum_to_message_type,
)
from gateway.bridge_health import MessageOrigin


# =============================================================================
# Factory: from_meshtastic
# =============================================================================

class TestFromMeshtastic:
    """Test CanonicalMessage.from_meshtastic()."""

    def test_basic_text_message(self):
        """Parse a simple text message packet."""
        packet = {
            'from': 0xAABBCCDD,
            'to': 0xFFFFFFFF,
            'fromId': '!aabbccdd',
            'decoded': {
                'portnum': 'TEXT_MESSAGE_APP',
                'text': 'Hello from Meshtastic',
            },
            'hopLimit': 3,
            'hopStart': 3,
            'channel': 0,
        }
        msg = CanonicalMessage.from_meshtastic(packet)

        assert msg.source_network == 'meshtastic'
        assert msg.source_address == '!aabbccdd'
        assert msg.content == 'Hello from Meshtastic'
        assert msg.message_type == MessageType.TEXT
        assert msg.is_broadcast is True
        assert msg.destination_address is None
        assert msg.via_internet is False
        assert msg.origin == MessageOrigin.RADIO

    def test_direct_message(self):
        """Parse a direct message (non-broadcast)."""
        packet = {
            'from': 0xAABBCCDD,
            'to': 0x11223344,
            'fromId': '!aabbccdd',
            'toId': '!11223344',
            'decoded': {
                'portnum': 'TEXT_MESSAGE_APP',
                'text': 'DM to you',
            },
            'hopLimit': 2,
            'hopStart': 3,
        }
        msg = CanonicalMessage.from_meshtastic(packet)

        assert msg.is_broadcast is False
        assert msg.destination_address == '!11223344'
        assert msg.hop_count == 1  # 3 - 2

    def test_mqtt_origin(self):
        """MQTT-originated messages flagged correctly."""
        packet = {
            'from': 0xAABBCCDD,
            'to': 0xFFFFFFFF,
            'decoded': {'portnum': 'TEXT_MESSAGE_APP', 'text': 'via MQTT'},
            'viaMqtt': True,
        }
        msg = CanonicalMessage.from_meshtastic(packet)

        assert msg.via_internet is True
        assert msg.origin == MessageOrigin.MQTT

    def test_telemetry_portnum(self):
        """Telemetry portnum maps to TELEMETRY type."""
        packet = {
            'from': 0xAABBCCDD,
            'to': 0xFFFFFFFF,
            'decoded': {'portnum': 'TELEMETRY_APP', 'payload': b'\x01\x02'},
        }
        msg = CanonicalMessage.from_meshtastic(packet)

        assert msg.message_type == MessageType.TELEMETRY

    def test_position_portnum(self):
        """Position portnum maps to POSITION type."""
        packet = {
            'from': 0xAABBCCDD,
            'to': 0xFFFFFFFF,
            'decoded': {'portnum': 'POSITION_APP'},
        }
        msg = CanonicalMessage.from_meshtastic(packet)

        assert msg.message_type == MessageType.POSITION

    def test_from_id_fallback(self):
        """fromId fallback to hex-formatted 'from' field."""
        packet = {
            'from': 0x12345678,
            'to': 0xFFFFFFFF,
            'decoded': {'portnum': 'TEXT_MESSAGE_APP', 'text': 'test'},
        }
        msg = CanonicalMessage.from_meshtastic(packet)

        assert msg.source_address == '!12345678'

    def test_metadata_preserved(self):
        """Protocol-specific metadata preserved in metadata dict."""
        packet = {
            'from': 0xAABBCCDD,
            'to': 0xFFFFFFFF,
            'decoded': {'portnum': 'TEXT_MESSAGE_APP', 'text': 'test'},
            'rxSnr': 10.5,
            'rxRssi': -85,
            'channel': 2,
            'id': 12345,
        }
        msg = CanonicalMessage.from_meshtastic(packet)

        assert msg.metadata['rxSnr'] == 10.5
        assert msg.metadata['rxRssi'] == -85
        assert msg.metadata['channel'] == 2
        assert msg.metadata['packet_id'] == 12345

    def test_integer_portnum(self):
        """Integer portnums (from protobuf) handled correctly."""
        packet = {
            'from': 0xAABBCCDD,
            'to': 0xFFFFFFFF,
            'decoded': {'portnum': 1, 'text': 'text msg'},
        }
        msg = CanonicalMessage.from_meshtastic(packet)

        assert msg.message_type == MessageType.TEXT


# =============================================================================
# Factory: from_meshcore
# =============================================================================

class TestFromMeshcore:
    """Test CanonicalMessage.from_meshcore()."""

    def test_contact_message(self):
        """Parse a MeshCore direct message event."""
        contact = SimpleNamespace(
            adv_name='NodeA',
            public_key=b'\xaa\xbb\xcc\xdd\xee\xff',
        )
        payload = SimpleNamespace(
            text='Hello from MeshCore',
            contact=contact,
            destination='some_dest',
            is_channel=False,
            channel=0,
        )
        event = SimpleNamespace(
            type='CONTACT_MSG_RECV',
            payload=payload,
        )
        msg = CanonicalMessage.from_meshcore(event)

        assert msg.source_network == 'meshcore'
        assert msg.content == 'Hello from MeshCore'
        assert msg.message_type == MessageType.TEXT
        assert msg.is_broadcast is False
        assert msg.via_internet is False
        assert msg.origin == MessageOrigin.RADIO
        assert msg.hop_limit == 64

    def test_channel_message(self):
        """Parse a MeshCore channel (broadcast) message."""
        payload = SimpleNamespace(
            text='Channel broadcast',
            contact=None,
            sender='abc123',
            destination=None,
            is_channel=True,
            channel=1,
        )
        event = SimpleNamespace(
            type='CHANNEL_MSG_RECV',
            payload=payload,
        )
        msg = CanonicalMessage.from_meshcore(event)

        assert msg.is_broadcast is True
        assert msg.destination_address is None
        assert msg.metadata['channel'] == 1

    def test_advertisement_event(self):
        """Parse a MeshCore advertisement (node discovery)."""
        payload = SimpleNamespace(
            text='',
            contact=None,
            sender='deadbeef',
            destination=None,
            is_channel=False,
            channel=0,
        )
        event = SimpleNamespace(
            type='ADVERTISEMENT',
            payload=payload,
        )
        msg = CanonicalMessage.from_meshcore(event)

        assert msg.message_type == MessageType.NODEINFO

    def test_dict_payload(self):
        """Handle dict-style payloads (for simulation/testing)."""
        event = SimpleNamespace(
            type='CONTACT_MSG_RECV',
            payload={
                'text': 'Dict payload test',
                'sender': 'node123',
                'destination': None,
                'is_channel': False,
                'channel': 0,
            },
        )
        msg = CanonicalMessage.from_meshcore(event)

        assert msg.content == 'Dict payload test'
        assert msg.source_address == 'node123'

    def test_ack_event(self):
        """ACK events map to ACK message type."""
        event = SimpleNamespace(
            type='ACK',
            payload={'text': '', 'sender': 'x', 'destination': None,
                     'is_channel': False, 'channel': 0},
        )
        msg = CanonicalMessage.from_meshcore(event)

        assert msg.message_type == MessageType.ACK


# =============================================================================
# Factory: from_rns
# =============================================================================

class TestFromRNS:
    """Test CanonicalMessage.from_rns()."""

    def test_basic_lxmf_delivery(self):
        """Parse a basic LXMF delivery."""
        lxmf = SimpleNamespace(
            content=b'Hello from RNS',
            source_hash=b'\xde\xad\xbe\xef',
            destination_hash=b'\xca\xfe\xba\xbe',
            title=b'Test Title',
            fields={},
        )
        msg = CanonicalMessage.from_rns(lxmf)

        assert msg.source_network == 'rns'
        assert msg.source_address == 'deadbeef'
        assert msg.destination_address == 'cafebabe'
        assert msg.content == 'Hello from RNS'
        assert msg.metadata['title'] == 'Test Title'

    def test_broadcast_lxmf(self):
        """Broadcast LXMF (no destination hash)."""
        lxmf = SimpleNamespace(
            content=b'Broadcast',
            source_hash=b'\xaa\xbb',
            destination_hash=None,
            title=None,
            fields={},
        )
        msg = CanonicalMessage.from_rns(lxmf)

        assert msg.is_broadcast is True
        assert msg.destination_address is None


# =============================================================================
# Factory: from_bridged_message (backward compatibility)
# =============================================================================

class TestFromBridgedMessage:
    """Test CanonicalMessage.from_bridged_message()."""

    def test_round_trip(self):
        """BridgedMessage → CanonicalMessage → BridgedMessage preserves fields."""
        from gateway.rns_bridge import BridgedMessage

        original = BridgedMessage(
            source_network='meshtastic',
            source_id='!aabbccdd',
            destination_id='!11223344',
            content='Round trip test',
            title='Test',
            is_broadcast=False,
            origin=MessageOrigin.RADIO,
            via_internet=False,
            metadata={'channel': 2},
        )

        canonical = CanonicalMessage.from_bridged_message(original)
        restored = canonical.to_bridged_message()

        assert restored.source_network == original.source_network
        assert restored.source_id == original.source_id
        assert restored.destination_id == original.destination_id
        assert restored.content == original.content
        assert restored.is_broadcast == original.is_broadcast
        assert restored.origin == original.origin
        assert restored.via_internet == original.via_internet

    def test_broadcast_round_trip(self):
        """Broadcast BridgedMessage round-trips correctly."""
        from gateway.rns_bridge import BridgedMessage

        original = BridgedMessage(
            source_network='rns',
            source_id='deadbeef',
            destination_id=None,
            content='Broadcast test',
            is_broadcast=True,
            origin=MessageOrigin.UNKNOWN,
        )

        canonical = CanonicalMessage.from_bridged_message(original)
        assert canonical.is_broadcast is True
        assert canonical.destination_address is None

        restored = canonical.to_bridged_message()
        assert restored.is_broadcast is True
        assert restored.destination_id is None


# =============================================================================
# Serialization: Text Truncation
# =============================================================================

class TestTextTruncation:
    """Test payload-size-aware text truncation."""

    def test_meshcore_truncation(self):
        """Long messages truncated for MeshCore's 160-byte limit."""
        long_text = "A" * 200
        msg = CanonicalMessage(content=long_text)
        result = msg.to_meshcore_text()

        assert len(result.encode('utf-8')) <= MESHCORE_MAX_TEXT
        assert result.endswith(TRUNCATION_INDICATOR)

    def test_meshtastic_no_truncation(self):
        """Short messages pass through untruncated."""
        short_text = "Short message"
        msg = CanonicalMessage(content=short_text)
        result = msg.to_meshtastic_text()

        assert result == short_text

    def test_meshtastic_truncation(self):
        """Messages exceeding Meshtastic limit are truncated."""
        long_text = "B" * 300
        msg = CanonicalMessage(content=long_text)
        result = msg.to_meshtastic_text()

        assert len(result.encode('utf-8')) <= MESHTASTIC_MAX_PAYLOAD
        assert result.endswith(TRUNCATION_INDICATOR)

    def test_utf8_clean_truncation(self):
        """Multi-byte UTF-8 characters truncated at character boundaries."""
        # Each emoji is 4 bytes in UTF-8
        emoji_text = "\U0001F600" * 50  # 200 bytes
        result = _truncate_utf8(emoji_text, 100)

        # Should not have broken multi-byte sequences
        result.encode('utf-8')  # Should not raise
        assert len(result.encode('utf-8')) <= 100

    def test_no_truncation_when_fits(self):
        """Text within limits returned unchanged."""
        text = "Fits fine"
        result = _truncate_utf8(text, 100)
        assert result == text


# =============================================================================
# Filtering: should_bridge
# =============================================================================

class TestShouldBridge:
    """Test message filtering logic."""

    def test_normal_message_bridges(self):
        """Normal radio message should bridge."""
        msg = CanonicalMessage(
            origin=MessageOrigin.RADIO,
            via_internet=False,
        )
        assert msg.should_bridge() is True

    def test_mqtt_filtered(self):
        """MQTT messages filtered when filter_mqtt=True."""
        msg = CanonicalMessage(
            origin=MessageOrigin.MQTT,
            via_internet=True,
        )
        assert msg.should_bridge(filter_mqtt=True) is False

    def test_mqtt_not_filtered_by_default(self):
        """MQTT messages pass by default."""
        msg = CanonicalMessage(
            origin=MessageOrigin.MQTT,
            via_internet=True,
        )
        assert msg.should_bridge() is True

    def test_internet_to_meshcore_filtered(self):
        """Internet-originated messages dropped when destined for MeshCore."""
        msg = CanonicalMessage(
            via_internet=True,
            destination_network='meshcore',
        )
        assert msg.should_bridge(filter_internet_to_meshcore=True) is False

    def test_radio_to_meshcore_allowed(self):
        """Radio-originated messages pass to MeshCore."""
        msg = CanonicalMessage(
            via_internet=False,
            destination_network='meshcore',
        )
        assert msg.should_bridge(filter_internet_to_meshcore=True) is True


# =============================================================================
# Routing: get_destinations
# =============================================================================

class TestGetDestinations:
    """Test destination network routing."""

    def test_broadcast_excludes_source(self):
        """Broadcast messages route to all networks except source."""
        msg = CanonicalMessage(
            source_network='meshtastic',
            is_broadcast=True,
        )
        dests = msg.get_destinations()

        assert 'meshtastic' not in dests
        assert 'meshcore' in dests
        assert 'rns' in dests

    def test_directed_uses_destination_network(self):
        """Directed messages route to specified destination."""
        msg = CanonicalMessage(
            source_network='meshtastic',
            destination_network='rns',
            is_broadcast=False,
        )
        dests = msg.get_destinations()

        assert dests == ['rns']

    def test_no_destination(self):
        """Non-broadcast with no destination returns empty."""
        msg = CanonicalMessage(
            source_network='meshtastic',
            is_broadcast=False,
        )
        dests = msg.get_destinations()

        assert dests == []


# =============================================================================
# Portnum Mapping
# =============================================================================

class TestPortnumMapping:
    """Test Meshtastic portnum to MessageType mapping."""

    def test_known_portnums(self):
        assert _portnum_to_message_type('TEXT_MESSAGE_APP') == MessageType.TEXT
        assert _portnum_to_message_type('TELEMETRY_APP') == MessageType.TELEMETRY
        assert _portnum_to_message_type('POSITION_APP') == MessageType.POSITION
        assert _portnum_to_message_type('NODEINFO_APP') == MessageType.NODEINFO

    def test_unknown_portnum(self):
        assert _portnum_to_message_type('CUSTOM_APP') == MessageType.UNKNOWN

    def test_integer_portnums(self):
        assert _portnum_to_message_type(1) == MessageType.TEXT
        assert _portnum_to_message_type(67) == MessageType.TELEMETRY
        assert _portnum_to_message_type(999) == MessageType.UNKNOWN


# =============================================================================
# String Representation
# =============================================================================

class TestStringRepr:
    """Test __str__ for logging."""

    def test_broadcast_str(self):
        msg = CanonicalMessage(
            source_network='meshcore',
            source_address='abc123',
            content='Test broadcast',
            is_broadcast=True,
        )
        s = str(msg)
        assert 'meshcore:abc123' in s
        assert 'broadcast' in s

    def test_directed_str(self):
        msg = CanonicalMessage(
            source_network='meshtastic',
            source_address='!aabb',
            destination_address='!ccdd',
            content='Test DM',
        )
        s = str(msg)
        assert '!ccdd' in s

    def test_long_content_truncated_in_str(self):
        msg = CanonicalMessage(content='X' * 100)
        s = str(msg)
        assert '...' in s
        assert len(s) < 200
