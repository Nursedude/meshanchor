"""
Tests for MeshtasticApiProxy node sanitization.

The Meshtastic web client crashes when clicking nodes with incomplete
data (phantom nodes from MQTT). The proxy sanitizes:
1. /json/nodes responses (JSON-level) — for REST API consumers
2. /api/v1/fromradio protobuf packets — for the React web client

See: https://github.com/meshtastic/web/issues/862
"""

import json
import struct
import pytest


# Import the static method directly
from gateway.meshtastic_api_proxy import MeshtasticApiProxy


# ────────────────────────────────────────────────────────────────────
# Protobuf wire format helpers for building test packets
# ────────────────────────────────────────────────────────────────────

def _encode_varint(value: int) -> bytes:
    """Encode an integer as a protobuf varint."""
    result = bytearray()
    while value > 0x7F:
        result.append((value & 0x7F) | 0x80)
        value >>= 7
    result.append(value & 0x7F)
    return bytes(result)


def _encode_field_varint(field_number: int, value: int) -> bytes:
    """Encode a varint field (wire type 0)."""
    tag = _encode_varint((field_number << 3) | 0)
    return tag + _encode_varint(value)


def _encode_field_bytes(field_number: int, data: bytes) -> bytes:
    """Encode a length-delimited field (wire type 2)."""
    tag = _encode_varint((field_number << 3) | 2)
    return tag + _encode_varint(len(data)) + data


def _encode_field_string(field_number: int, text: str) -> bytes:
    """Encode a string field (wire type 2)."""
    return _encode_field_bytes(field_number, text.encode('utf-8'))


def _build_user_proto(long_name: str = "TestNode", short_name: str = "TST") -> bytes:
    """Build a User protobuf sub-message."""
    return (
        _encode_field_string(2, long_name) +    # long_name = field 2
        _encode_field_string(3, short_name)     # short_name = field 3
    )


def _build_nodeinfo_proto(num: int, user: bytes = None) -> bytes:
    """Build a NodeInfo protobuf sub-message."""
    result = _encode_field_varint(1, num)       # num = field 1
    if user is not None:
        result += _encode_field_bytes(2, user)  # user = field 2
    return result


def _build_fromradio_nodeinfo(node_num: int, user: bytes = None) -> bytes:
    """Build a FromRadio protobuf containing a NodeInfo."""
    nodeinfo = _build_nodeinfo_proto(node_num, user)
    return (
        _encode_field_varint(1, 1) +               # id = field 1
        _encode_field_bytes(4, nodeinfo)            # node_info = field 4
    )


def _build_fromradio_config_complete(config_id: int = 42) -> bytes:
    """Build a FromRadio with config_complete_id (field 7, varint)."""
    return (
        _encode_field_varint(1, 1) +               # id = field 1
        _encode_field_varint(7, config_id)          # config_complete_id = field 7
    )


def _build_fromradio_meshpacket(packet_data: bytes = b'\x01\x02') -> bytes:
    """Build a FromRadio with a MeshPacket (field 9)."""
    return (
        _encode_field_varint(1, 2) +               # id = field 1
        _encode_field_bytes(9, packet_data)         # packet = field 9
    )


class TestSanitizeNodesJson:
    """Tests for _sanitize_nodes_json static method."""

    def test_healthy_nodes_unchanged(self):
        """Nodes with complete data should pass through unmodified."""
        nodes = {
            "!aabbccdd": {
                "num": 2864434397,
                "user": {
                    "id": "!aabbccdd",
                    "longName": "Hilltop-1",
                    "shortName": "HT1",
                    "hwModel": "RAK4631",
                },
                "role": "CLIENT",
            }
        }
        data = json.dumps(nodes).encode()
        result = MeshtasticApiProxy._sanitize_nodes_json(data)
        parsed = json.loads(result)

        assert parsed["!aabbccdd"]["user"]["longName"] == "Hilltop-1"
        assert parsed["!aabbccdd"]["user"]["shortName"] == "HT1"
        assert parsed["!aabbccdd"]["role"] == "CLIENT"

    def test_phantom_node_no_user(self):
        """Phantom node with no 'user' object gets defaults."""
        nodes = {
            "!deadbeef": {
                "num": 3735928559,
            }
        }
        data = json.dumps(nodes).encode()
        result = MeshtasticApiProxy._sanitize_nodes_json(data)
        parsed = json.loads(result)

        node = parsed["!deadbeef"]
        assert "user" in node
        assert node["user"]["longName"]  # Not empty
        assert node["user"]["shortName"] == "????"
        assert node["user"]["hwModel"] == "UNSET"
        assert node["role"] == "CLIENT"

    def test_phantom_node_empty_user(self):
        """Node with empty user fields gets defaults filled in."""
        nodes = {
            "!11223344": {
                "num": 287454020,
                "user": {
                    "id": "!11223344",
                    "longName": "",
                    "shortName": "",
                    "hwModel": "",
                },
                "role": "ROUTER",
            }
        }
        data = json.dumps(nodes).encode()
        result = MeshtasticApiProxy._sanitize_nodes_json(data)
        parsed = json.loads(result)

        user = parsed["!11223344"]["user"]
        assert user["longName"]  # Should be filled with default
        assert user["shortName"] == "????"
        assert user["hwModel"] == "UNSET"
        # Existing role should be preserved
        assert parsed["!11223344"]["role"] == "ROUTER"

    def test_missing_role_gets_default(self):
        """Node with missing 'role' field gets CLIENT default."""
        nodes = {
            "!aabb0011": {
                "num": 2864054289,
                "user": {
                    "id": "!aabb0011",
                    "longName": "M3GO",
                    "shortName": "M3GO",
                    "hwModel": "HELTEC_V3",
                },
            }
        }
        data = json.dumps(nodes).encode()
        result = MeshtasticApiProxy._sanitize_nodes_json(data)
        parsed = json.loads(result)

        assert parsed["!aabb0011"]["role"] == "CLIENT"
        # User data should be preserved
        assert parsed["!aabb0011"]["user"]["longName"] == "M3GO"

    def test_mixed_healthy_and_phantom(self):
        """Mix of healthy and phantom nodes — only phantoms get patched."""
        nodes = {
            "!healthy01": {
                "num": 1,
                "user": {
                    "id": "!healthy01",
                    "longName": "Good Node",
                    "shortName": "GOOD",
                    "hwModel": "RAK4631",
                },
                "role": "CLIENT",
            },
            "!phantom01": {
                "num": 2,
                # No user object at all
            },
            "!phantom02": {
                "num": 3,
                "user": {
                    "id": "!phantom02",
                    "longName": "",
                    "shortName": "",
                },
            },
        }
        data = json.dumps(nodes).encode()
        result = MeshtasticApiProxy._sanitize_nodes_json(data)
        parsed = json.loads(result)

        # Healthy node preserved exactly
        assert parsed["!healthy01"]["user"]["longName"] == "Good Node"
        assert parsed["!healthy01"]["role"] == "CLIENT"

        # Phantom nodes patched
        assert parsed["!phantom01"]["user"]["longName"]  # Has a default
        assert parsed["!phantom01"]["role"] == "CLIENT"
        assert parsed["!phantom02"]["user"]["shortName"] == "????"

    def test_invalid_json_passes_through(self):
        """Non-JSON data passes through unchanged."""
        data = b"this is not json"
        result = MeshtasticApiProxy._sanitize_nodes_json(data)
        assert result == data

    def test_non_dict_json_passes_through(self):
        """JSON that isn't a dict (e.g., list) passes through."""
        data = json.dumps([1, 2, 3]).encode()
        result = MeshtasticApiProxy._sanitize_nodes_json(data)
        assert result == data

    def test_empty_dict_passes_through(self):
        """Empty node dict passes through."""
        data = json.dumps({}).encode()
        result = MeshtasticApiProxy._sanitize_nodes_json(data)
        assert json.loads(result) == {}

    def test_user_is_none_gets_replaced(self):
        """Node where 'user' is null/None gets a proper user object."""
        nodes = {
            "!nulluser": {
                "num": 99,
                "user": None,
            }
        }
        data = json.dumps(nodes).encode()
        result = MeshtasticApiProxy._sanitize_nodes_json(data)
        parsed = json.loads(result)

        assert isinstance(parsed["!nulluser"]["user"], dict)
        assert parsed["!nulluser"]["user"]["longName"]
        assert parsed["!nulluser"]["user"]["shortName"] == "????"

    def test_partial_user_preserves_existing(self):
        """Node with some user fields keeps existing data."""
        nodes = {
            "!partial": {
                "num": 50,
                "user": {
                    "id": "!partial",
                    "longName": "M3shGO",
                    # shortName missing
                    # hwModel missing
                },
                "role": "ROUTER_CLIENT",
            }
        }
        data = json.dumps(nodes).encode()
        result = MeshtasticApiProxy._sanitize_nodes_json(data)
        parsed = json.loads(result)

        # Existing fields preserved
        assert parsed["!partial"]["user"]["longName"] == "M3shGO"
        assert parsed["!partial"]["role"] == "ROUTER_CLIENT"
        # Missing fields filled
        assert parsed["!partial"]["user"]["shortName"] == "????"
        assert parsed["!partial"]["user"]["hwModel"] == "UNSET"

    def test_long_name_default_uses_last_4_chars(self):
        """Default longName uses last 4 chars of node key."""
        nodes = {
            "!aabbccdd": {
                "num": 2864434397,
                "user": {
                    "id": "!aabbccdd",
                    "longName": "",
                    "shortName": "TEST",
                    "hwModel": "HELTEC_V3",
                },
                "role": "CLIENT",
            }
        }
        data = json.dumps(nodes).encode()
        result = MeshtasticApiProxy._sanitize_nodes_json(data)
        parsed = json.loads(result)

        # Should use last 4 chars of key for default name
        assert "ccdd" in parsed["!aabbccdd"]["user"]["longName"]

    def test_null_position_replaced_with_dict(self):
        """Node with null position gets empty dict to prevent crash."""
        nodes = {
            "!aabb0022": {
                "num": 100,
                "user": {
                    "id": "!aabb0022",
                    "longName": "TestNode",
                    "shortName": "TST",
                    "hwModel": "RAK4631",
                },
                "role": "CLIENT",
                "position": None,
            }
        }
        data = json.dumps(nodes).encode()
        result = MeshtasticApiProxy._sanitize_nodes_json(data)
        parsed = json.loads(result)

        assert isinstance(parsed["!aabb0022"]["position"], dict)

    def test_null_device_metrics_replaced_with_dict(self):
        """Node with null deviceMetrics gets empty dict."""
        nodes = {
            "!aabb0033": {
                "num": 200,
                "user": {
                    "id": "!aabb0033",
                    "longName": "TestNode2",
                    "shortName": "TS2",
                    "hwModel": "HELTEC_V3",
                },
                "role": "ROUTER",
                "deviceMetrics": None,
            }
        }
        data = json.dumps(nodes).encode()
        result = MeshtasticApiProxy._sanitize_nodes_json(data)
        parsed = json.loads(result)

        assert isinstance(parsed["!aabb0033"]["deviceMetrics"], dict)

    def test_missing_last_heard_gets_default(self):
        """Phantom node missing lastHeard gets default 0."""
        nodes = {
            "!aabb0044": {
                "num": 300,
                # No user, no role, no lastHeard — full phantom
            }
        }
        data = json.dumps(nodes).encode()
        result = MeshtasticApiProxy._sanitize_nodes_json(data)
        parsed = json.loads(result)

        assert parsed["!aabb0044"]["lastHeard"] == 0

    def test_missing_num_parsed_from_hex_key(self):
        """Node missing 'num' gets it parsed from hex node key."""
        nodes = {
            "!deadbeef": {
                "user": {
                    "id": "!deadbeef",
                    "longName": "Phantom",
                    "shortName": "PHT",
                    "hwModel": "UNSET",
                },
                "role": "CLIENT",
            }
        }
        data = json.dumps(nodes).encode()
        result = MeshtasticApiProxy._sanitize_nodes_json(data)
        parsed = json.loads(result)

        assert parsed["!deadbeef"]["num"] == 0xdeadbeef

    def test_valid_position_dict_unchanged(self):
        """Node with valid position dict is not replaced."""
        nodes = {
            "!aabb0055": {
                "num": 400,
                "user": {
                    "id": "!aabb0055",
                    "longName": "GPS Node",
                    "shortName": "GPS",
                    "hwModel": "RAK4631",
                },
                "role": "CLIENT",
                "position": {"latitude": 21.3069, "longitude": -157.8583, "altitude": 5},
                "lastHeard": 1707500000,
            }
        }
        data = json.dumps(nodes).encode()
        result = MeshtasticApiProxy._sanitize_nodes_json(data)
        parsed = json.loads(result)

        # Position preserved exactly
        assert parsed["!aabb0055"]["position"]["latitude"] == 21.3069
        assert parsed["!aabb0055"]["position"]["longitude"] == -157.8583

    def test_full_phantom_node_all_fields_patched(self):
        """Completely bare phantom node gets all required fields."""
        nodes = {
            "!ff001122": {}
        }
        data = json.dumps(nodes).encode()
        result = MeshtasticApiProxy._sanitize_nodes_json(data)
        parsed = json.loads(result)

        node = parsed["!ff001122"]
        assert isinstance(node["user"], dict)
        assert node["user"]["longName"]
        assert node["user"]["shortName"] == "????"
        assert node["user"]["hwModel"] == "UNSET"
        assert node["role"] == "CLIENT"
        assert node["lastHeard"] == 0
        assert "num" in node


class TestProtobufPhantomNodeFilter:
    """Tests for _is_phantom_nodeinfo protobuf-level filtering.

    The Meshtastic React web client receives node data via protobuf
    streaming (/api/v1/fromradio), not JSON.  Phantom NodeInfo packets
    with missing User data crash the React UI when clicked.

    These tests construct real protobuf wire-format packets to verify
    the filter correctly identifies and drops phantom nodes while
    passing through all other packet types.
    """

    def test_phantom_nodeinfo_no_user(self):
        """NodeInfo without User field is detected as phantom."""
        packet = _build_fromradio_nodeinfo(node_num=0xDEADBEEF)
        assert MeshtasticApiProxy._is_phantom_nodeinfo(packet) is True

    def test_healthy_nodeinfo_with_user(self):
        """NodeInfo with User field passes through."""
        user = _build_user_proto(long_name="Hilltop-1", short_name="HT1")
        packet = _build_fromradio_nodeinfo(node_num=0xAABBCCDD, user=user)
        assert MeshtasticApiProxy._is_phantom_nodeinfo(packet) is False

    def test_config_complete_passes_through(self):
        """config_complete_id (field 7) is not a NodeInfo — passes through."""
        packet = _build_fromradio_config_complete(config_id=42)
        assert MeshtasticApiProxy._is_phantom_nodeinfo(packet) is False

    def test_meshpacket_passes_through(self):
        """MeshPacket (field 9) is not a NodeInfo — passes through."""
        packet = _build_fromradio_meshpacket(b'\x08\x01\x10\x02')
        assert MeshtasticApiProxy._is_phantom_nodeinfo(packet) is False

    def test_empty_data_passes_through(self):
        """Empty data passes through (not a phantom)."""
        assert MeshtasticApiProxy._is_phantom_nodeinfo(b'') is False
        assert MeshtasticApiProxy._is_phantom_nodeinfo(b'\x00') is False

    def test_short_data_passes_through(self):
        """Very short data (< 4 bytes) passes through."""
        assert MeshtasticApiProxy._is_phantom_nodeinfo(b'\x08\x01') is False

    def test_corrupt_data_passes_through(self):
        """Corrupted protobuf passes through (don't filter what we can't parse)."""
        assert MeshtasticApiProxy._is_phantom_nodeinfo(b'\xFF\xFF\xFF\xFF\xFF') is False

    def test_nodeinfo_with_empty_user(self):
        """NodeInfo with empty User sub-message is detected as phantom."""
        # User field present but 0 bytes (no long_name/short_name inside)
        packet = _build_fromradio_nodeinfo(node_num=0x11223344, user=b'')
        assert MeshtasticApiProxy._is_phantom_nodeinfo(packet) is True

    def test_nodeinfo_with_minimal_user(self):
        """NodeInfo with just a short_name in User is NOT phantom."""
        user = _encode_field_string(3, "X")  # short_name only
        packet = _build_fromradio_nodeinfo(node_num=0x55667788, user=user)
        assert MeshtasticApiProxy._is_phantom_nodeinfo(packet) is False

    def test_multiple_phantom_nodeinfo_sequential(self):
        """Multiple phantom checks don't interfere with each other."""
        phantom = _build_fromradio_nodeinfo(node_num=0xAAAA)
        user = _build_user_proto("Node-A", "NA")
        healthy = _build_fromradio_nodeinfo(node_num=0xBBBB, user=user)
        config = _build_fromradio_config_complete(99)

        assert MeshtasticApiProxy._is_phantom_nodeinfo(phantom) is True
        assert MeshtasticApiProxy._is_phantom_nodeinfo(healthy) is False
        assert MeshtasticApiProxy._is_phantom_nodeinfo(config) is False
        assert MeshtasticApiProxy._is_phantom_nodeinfo(phantom) is True


class TestProtobufWireFormatHelpers:
    """Tests for the _read_varint and _extract_protobuf_fields helpers."""

    def test_read_varint_single_byte(self):
        """Single-byte varint (value < 128)."""
        val, pos = MeshtasticApiProxy._read_varint(b'\x05', 0)
        assert val == 5
        assert pos == 1

    def test_read_varint_multi_byte(self):
        """Multi-byte varint (value >= 128)."""
        # 300 = 0b100101100 → encoded as [0xAC, 0x02]
        val, pos = MeshtasticApiProxy._read_varint(b'\xAC\x02', 0)
        assert val == 300
        assert pos == 2

    def test_read_varint_at_offset(self):
        """Varint read starting at non-zero offset."""
        val, pos = MeshtasticApiProxy._read_varint(b'\x00\x00\x07', 2)
        assert val == 7
        assert pos == 3

    def test_read_varint_empty(self):
        """Varint from empty data returns None."""
        val, pos = MeshtasticApiProxy._read_varint(b'', 0)
        assert val is None
        assert pos is None

    def test_extract_fields_varint(self):
        """Extract a varint field from protobuf bytes."""
        # Field 1, wire type 0, value 42 → tag=0x08, value=0x2A
        data = _encode_field_varint(1, 42)
        fields = MeshtasticApiProxy._extract_protobuf_fields(data)
        assert 1 in fields
        assert fields[1][0] == (0, 42)  # (wire_type, value)

    def test_extract_fields_length_delimited(self):
        """Extract a length-delimited field from protobuf bytes."""
        data = _encode_field_bytes(4, b'\x08\x01')
        fields = MeshtasticApiProxy._extract_protobuf_fields(data)
        assert 4 in fields
        assert fields[4][0] == (2, b'\x08\x01')

    def test_extract_multiple_fields(self):
        """Extract multiple fields from a single message."""
        data = (
            _encode_field_varint(1, 1) +
            _encode_field_bytes(4, b'\x08\xFF\x01')
        )
        fields = MeshtasticApiProxy._extract_protobuf_fields(data)
        assert 1 in fields
        assert 4 in fields
