"""
Tests for Packet Dissectors - Protocol parsers for mesh network packets.

Tests MeshtasticDissector, RNSDissector, and DisplayFilter.
Validates packet parsing, protocol tree construction, and filter matching.

Run: python3 -m pytest tests/test_packet_dissectors.py -v
"""

import sys
from datetime import datetime
from pathlib import Path

import pytest

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from monitoring.traffic_models import (
    FieldType,
    HopState,
    MeshPacket,
    MESHTASTIC_PORTS,
    PacketDirection,
    PacketField,
    PacketProtocol,
    PacketTree,
)
from monitoring.packet_dissectors import (
    MeshtasticDissector,
    RNSDissector,
    DisplayFilter,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def meshtastic_dissector():
    """Create a MeshtasticDissector instance."""
    return MeshtasticDissector()


@pytest.fixture
def rns_dissector():
    """Create an RNSDissector instance."""
    return RNSDissector()


@pytest.fixture
def basic_meshtastic_metadata():
    """Basic Meshtastic packet metadata."""
    return {
        "protocol": "meshtastic",
        "from": "!aabb0001",
        "to": "!ffffffff",
        "channel": 0,
        "hopLimit": 2,
        "hopStart": 3,
        "viaMqtt": False,
        "wantAck": False,
        "portnum": 1,
        "snr": 8.5,
        "rssi": -80,
    }


@pytest.fixture
def text_message_metadata():
    """Meshtastic text message metadata with decoded payload."""
    return {
        "protocol": "meshtastic",
        "from": "!aabb0001",
        "to": "!ccdd0002",
        "channel": 0,
        "hopLimit": 3,
        "hopStart": 3,
        "portnum": 1,
        "snr": 12.0,
        "rssi": -65,
        "decoded": {
            "portnum": 1,
            "text": "Hello mesh!",
            "payload": b"Hello mesh!",
        },
    }


@pytest.fixture
def position_metadata():
    """Meshtastic position packet metadata."""
    return {
        "protocol": "meshtastic",
        "from": "!aabb0001",
        "to": "!ffffffff",
        "channel": 0,
        "hopLimit": 3,
        "hopStart": 3,
        "portnum": 4,
        "decoded": {
            "portnum": 4,
            "position": {
                "latitude": 21.3069,
                "longitude": -157.8583,
                "altitude": 45,
            }
        },
    }


@pytest.fixture
def nodeinfo_metadata():
    """Meshtastic node info packet metadata."""
    return {
        "protocol": "meshtastic",
        "from": "!aabb0001",
        "to": "!ffffffff",
        "portnum": 5,
        "hopLimit": 3,
        "hopStart": 3,
        "decoded": {
            "portnum": 5,
            "user": {
                "longName": "Maui Gateway",
                "shortName": "MG01",
                "hwModel": "HELTEC_V3",
            }
        },
    }


@pytest.fixture
def telemetry_metadata():
    """Meshtastic telemetry packet metadata."""
    return {
        "protocol": "meshtastic",
        "from": "!aabb0001",
        "to": "!ffffffff",
        "portnum": 67,
        "hopLimit": 3,
        "hopStart": 3,
        "decoded": {
            "portnum": 67,
            "telemetry": {
                "deviceMetrics": {
                    "batteryLevel": 85,
                    "voltage": 3.7,
                    "channelUtilization": 12.5,
                }
            }
        },
    }


@pytest.fixture
def rns_metadata():
    """Basic RNS packet metadata."""
    return {
        "protocol": "rns",
        "dest_hash": "abcdef0123456789abcdef0123456789",
        "source_hash": "1234567890abcdef1234567890abcdef",
        "packet_type": "DATA",
        "hops": 2,
        "interface": "TCPInterface",
        "service_type": "lxmf.delivery",
    }


@pytest.fixture
def rns_announce_metadata():
    """RNS announce packet metadata."""
    return {
        "protocol": "rns",
        "dest_hash": "abcdef0123456789abcdef0123456789",
        "source_hash": "1234567890abcdef1234567890abcdef",
        "packet_type": "ANNOUNCE",
        "hops": 0,
        "interface": "AutoInterface",
        "aspect": "lxmf.delivery",
        "announce_app_data": b"Maui Node",
        "identity_hash": "aabbccdd11223344",
    }


# ---------------------------------------------------------------------------
# MeshtasticDissector - can_dissect tests
# ---------------------------------------------------------------------------

class TestMeshtasticCanDissect:
    """Tests for MeshtasticDissector.can_dissect()."""

    def test_can_dissect_by_protocol(self, meshtastic_dissector):
        """Detects Meshtastic by protocol field."""
        assert meshtastic_dissector.can_dissect(b"", {"protocol": "meshtastic"}) is True

    def test_can_dissect_by_from_to(self, meshtastic_dissector):
        """Detects Meshtastic by from/to fields."""
        assert meshtastic_dissector.can_dissect(b"", {"from": "!abc", "to": "!fff"}) is True

    def test_cannot_dissect_rns(self, meshtastic_dissector):
        """Does not match RNS protocol."""
        assert meshtastic_dissector.can_dissect(b"", {"protocol": "rns"}) is False

    def test_cannot_dissect_empty(self, meshtastic_dissector):
        """Does not match empty metadata."""
        assert meshtastic_dissector.can_dissect(b"", {}) is False


# ---------------------------------------------------------------------------
# MeshtasticDissector - dissect tests
# ---------------------------------------------------------------------------

class TestMeshtasticDissect:
    """Tests for MeshtasticDissector.dissect()."""

    def test_basic_packet_fields(self, meshtastic_dissector, basic_meshtastic_metadata):
        """Basic packet fields are extracted correctly."""
        packet = meshtastic_dissector.dissect(b"test_data", basic_meshtastic_metadata)

        assert packet.protocol == PacketProtocol.MESHTASTIC
        assert packet.source == "!aabb0001"
        assert packet.destination == "!ffffffff"
        assert packet.channel == 0
        assert packet.hop_limit == 2
        assert packet.hop_start == 3
        assert packet.hops_taken == 1
        assert packet.via_mqtt is False
        assert packet.want_ack is False
        assert packet.portnum == 1
        assert packet.port_name == "TEXT_MESSAGE"
        assert packet.snr == 8.5
        assert packet.rssi == -80
        assert packet.size == 9  # len(b"test_data")

    def test_direction_inbound(self, meshtastic_dissector):
        """Default direction is inbound."""
        metadata = {"from": "!abc", "to": "!fff", "hopLimit": 3, "hopStart": 3}
        packet = meshtastic_dissector.dissect(b"", metadata)
        assert packet.direction == PacketDirection.INBOUND

    def test_direction_outbound(self, meshtastic_dissector):
        """Outbound direction from metadata."""
        metadata = {"from": "!abc", "to": "!fff", "direction": "outbound",
                     "hopLimit": 3, "hopStart": 3}
        packet = meshtastic_dissector.dissect(b"", metadata)
        assert packet.direction == PacketDirection.OUTBOUND

    def test_direction_relayed(self, meshtastic_dissector):
        """Relayed direction when relay node present."""
        metadata = {"from": "!abc", "to": "!fff", "relayNode": 42,
                     "hopLimit": 3, "hopStart": 3}
        packet = meshtastic_dissector.dissect(b"", metadata)
        assert packet.direction == PacketDirection.RELAYED

    def test_via_mqtt(self, meshtastic_dissector):
        """Via MQTT flag is captured."""
        metadata = {"from": "!abc", "to": "!fff", "viaMqtt": True,
                     "hopLimit": 3, "hopStart": 3}
        packet = meshtastic_dissector.dissect(b"", metadata)
        assert packet.via_mqtt is True

    def test_channel_utilization(self, meshtastic_dissector):
        """Channel utilization metric captured."""
        metadata = {"from": "!abc", "to": "!fff", "channelUtilization": 25.5,
                     "hopLimit": 3, "hopStart": 3}
        packet = meshtastic_dissector.dissect(b"", metadata)
        assert packet.channel_utilization == 25.5

    def test_air_util_tx(self, meshtastic_dissector):
        """TX air utilization metric captured."""
        metadata = {"from": "!abc", "to": "!fff", "airUtilTx": 3.2,
                     "hopLimit": 3, "hopStart": 3}
        packet = meshtastic_dissector.dissect(b"", metadata)
        assert packet.air_util_tx == 3.2

    def test_relay_node_captured(self, meshtastic_dissector):
        """Relay node from 2.6+ captured."""
        metadata = {"from": "!abc", "to": "!fff", "relayNode": 0x42,
                     "hopLimit": 3, "hopStart": 3}
        packet = meshtastic_dissector.dissect(b"", metadata)
        assert packet.relay_node == 0x42

    def test_next_hop_captured(self, meshtastic_dissector):
        """Next hop from 2.6+ captured."""
        metadata = {"from": "!abc", "to": "!fff", "nextHop": 0x55,
                     "hopLimit": 3, "hopStart": 3}
        packet = meshtastic_dissector.dissect(b"", metadata)
        assert packet.next_hop == 0x55

    def test_relay_node_zero_ignored(self, meshtastic_dissector):
        """Relay node 0 is treated as not set."""
        metadata = {"from": "!abc", "to": "!fff", "relayNode": 0,
                     "hopLimit": 3, "hopStart": 3}
        packet = meshtastic_dissector.dissect(b"", metadata)
        assert packet.relay_node is None

    def test_decoded_text_payload(self, meshtastic_dissector, text_message_metadata):
        """Text message payload extracted."""
        packet = meshtastic_dissector.dissect(b"test", text_message_metadata)
        assert packet.decoded_payload is not None
        assert packet.payload == b"Hello mesh!"

    def test_decoded_string_payload(self, meshtastic_dissector):
        """String payload (non-bytes) is encoded."""
        metadata = {
            "from": "!abc", "to": "!fff", "portnum": 1,
            "hopLimit": 3, "hopStart": 3,
            "decoded": {"payload": "string_payload"},
        }
        packet = meshtastic_dissector.dissect(b"", metadata)
        assert packet.payload == b"string_payload"

    def test_alternative_field_names(self, meshtastic_dissector):
        """Alternative field names (fromId, toId, rxSnr, rxRssi) work."""
        metadata = {
            "fromId": "!aabb0001",
            "toId": "!ccdd0002",
            "rxSnr": 5.0,
            "rxRssi": -90,
            "hopLimit": 3,
            "hopStart": 3,
        }
        packet = meshtastic_dissector.dissect(b"", metadata)
        assert packet.source == "!aabb0001"
        assert packet.destination == "!ccdd0002"
        assert packet.snr == 5.0
        assert packet.rssi == -90


# ---------------------------------------------------------------------------
# MeshtasticDissector - protocol tree tests
# ---------------------------------------------------------------------------

class TestMeshtasticProtocolTree:
    """Tests for protocol tree construction."""

    def test_tree_has_frame_layer(self, meshtastic_dissector, basic_meshtastic_metadata):
        """Protocol tree includes Frame layer."""
        packet = meshtastic_dissector.dissect(b"data", basic_meshtastic_metadata)
        assert packet.tree is not None

        frame = packet.tree.get_field("frame.time")
        assert frame is not None
        assert frame.field_type == FieldType.TIMESTAMP

    def test_tree_has_mesh_layer(self, meshtastic_dissector, basic_meshtastic_metadata):
        """Protocol tree includes Meshtastic layer fields."""
        packet = meshtastic_dissector.dissect(b"data", basic_meshtastic_metadata)

        source = packet.tree.get_field("mesh.from")
        assert source is not None
        assert source.value == "!aabb0001"

        dest = packet.tree.get_field("mesh.to")
        assert dest is not None
        assert dest.value == "!ffffffff"

    def test_tree_has_routing(self, meshtastic_dissector, basic_meshtastic_metadata):
        """Protocol tree includes routing fields."""
        packet = meshtastic_dissector.dissect(b"data", basic_meshtastic_metadata)

        hop_limit = packet.tree.get_field("mesh.hop_limit")
        assert hop_limit is not None
        assert hop_limit.value == 2

        hops = packet.tree.get_field("mesh.hops")
        assert hops is not None
        assert hops.value == 1

    def test_tree_has_metrics(self, meshtastic_dissector, basic_meshtastic_metadata):
        """Protocol tree includes radio metrics."""
        packet = meshtastic_dissector.dissect(b"data", basic_meshtastic_metadata)

        snr = packet.tree.get_field("mesh.snr")
        assert snr is not None
        assert snr.value == 8.5

        rssi = packet.tree.get_field("mesh.rssi")
        assert rssi is not None
        assert rssi.value == -80

    def test_tree_has_payload_info(self, meshtastic_dissector, basic_meshtastic_metadata):
        """Protocol tree includes payload info."""
        packet = meshtastic_dissector.dissect(b"data", basic_meshtastic_metadata)

        portnum = packet.tree.get_field("mesh.portnum")
        assert portnum is not None
        assert portnum.value == 1

        port_name = packet.tree.get_field("mesh.port")
        assert port_name is not None
        assert port_name.value == "TEXT_MESSAGE"

    def test_tree_relay_tracking(self, meshtastic_dissector):
        """Protocol tree includes relay/next_hop fields."""
        metadata = {
            "from": "!abc", "to": "!fff",
            "relayNode": 0x42, "nextHop": 0x55,
            "hopLimit": 2, "hopStart": 3,
        }
        packet = meshtastic_dissector.dissect(b"", metadata)

        relay = packet.tree.get_field("mesh.relay")
        assert relay is not None
        assert "42" in relay.value

        next_hop = packet.tree.get_field("mesh.next_hop")
        assert next_hop is not None
        assert "55" in next_hop.value

    def test_tree_text_message_decoded(self, meshtastic_dissector, text_message_metadata):
        """Text message content appears in tree."""
        packet = meshtastic_dissector.dissect(b"", text_message_metadata)

        text = packet.tree.get_field("mesh.text")
        assert text is not None
        assert text.value == "Hello mesh!"

    def test_tree_position_decoded(self, meshtastic_dissector, position_metadata):
        """Position data appears in tree."""
        packet = meshtastic_dissector.dissect(b"", position_metadata)

        lat = packet.tree.get_field("mesh.pos.lat")
        assert lat is not None
        assert abs(lat.value - 21.3069) < 0.001

        lon = packet.tree.get_field("mesh.pos.lon")
        assert lon is not None
        assert abs(lon.value - (-157.8583)) < 0.001

    def test_tree_nodeinfo_decoded(self, meshtastic_dissector, nodeinfo_metadata):
        """Node info data appears in tree."""
        packet = meshtastic_dissector.dissect(b"", nodeinfo_metadata)

        long_name = packet.tree.get_field("mesh.user.long")
        assert long_name is not None
        assert long_name.value == "Maui Gateway"

        short_name = packet.tree.get_field("mesh.user.short")
        assert short_name is not None
        assert short_name.value == "MG01"

    def test_tree_telemetry_decoded(self, meshtastic_dissector, telemetry_metadata):
        """Telemetry data appears in tree."""
        packet = meshtastic_dissector.dissect(b"", telemetry_metadata)

        battery = packet.tree.get_field("mesh.telem.battery")
        assert battery is not None
        assert battery.value == 85

        voltage = packet.tree.get_field("mesh.telem.voltage")
        assert voltage is not None
        assert abs(voltage.value - 3.7) < 0.01

    def test_tree_ascii_output(self, meshtastic_dissector, basic_meshtastic_metadata):
        """Tree generates readable ASCII output."""
        packet = meshtastic_dissector.dissect(b"data", basic_meshtastic_metadata)
        ascii_output = packet.tree.format_ascii()

        assert "Frame" in ascii_output
        assert "Meshtastic" in ascii_output
        assert "Source" in ascii_output


# ---------------------------------------------------------------------------
# RNSDissector - can_dissect tests
# ---------------------------------------------------------------------------

class TestRNSCanDissect:
    """Tests for RNSDissector.can_dissect()."""

    def test_can_dissect_by_protocol(self, rns_dissector):
        """Detects RNS by protocol field."""
        assert rns_dissector.can_dissect(b"", {"protocol": "rns"}) is True

    def test_can_dissect_by_dest_hash(self, rns_dissector):
        """Detects RNS by dest_hash field."""
        assert rns_dissector.can_dissect(b"", {"dest_hash": "abcdef"}) is True

    def test_can_dissect_by_destination_hash(self, rns_dissector):
        """Detects RNS by destination_hash field."""
        assert rns_dissector.can_dissect(b"", {"destination_hash": "abcdef"}) is True

    def test_can_dissect_by_packet_type(self, rns_dissector):
        """Detects RNS by packet_type field."""
        assert rns_dissector.can_dissect(b"", {"packet_type": "ANNOUNCE"}) is True

    def test_cannot_dissect_meshtastic(self, rns_dissector):
        """Does not match Meshtastic protocol."""
        assert rns_dissector.can_dissect(b"", {"protocol": "meshtastic"}) is False

    def test_cannot_dissect_empty(self, rns_dissector):
        """Does not match empty metadata."""
        assert rns_dissector.can_dissect(b"", {}) is False


# ---------------------------------------------------------------------------
# RNSDissector - dissect tests
# ---------------------------------------------------------------------------

class TestRNSDissect:
    """Tests for RNSDissector.dissect()."""

    def test_basic_rns_packet(self, rns_dissector, rns_metadata):
        """Basic RNS packet fields are extracted."""
        raw_data = bytes(20)  # Enough for header parsing
        packet = rns_dissector.dissect(raw_data, rns_metadata)

        assert packet.protocol == PacketProtocol.RNS
        assert packet.rns_dest_hash == bytes.fromhex("abcdef0123456789abcdef0123456789")
        assert packet.source == "1234567890abcdef1234567890abcdef"
        assert packet.hops_taken == 2
        assert packet.rns_service == "lxmf.delivery"
        assert packet.rns_interface == "TCPInterface"

    def test_rns_destination_from_hash(self, rns_dissector, rns_metadata):
        """Destination is derived from dest_hash (first 16 hex chars)."""
        packet = rns_dissector.dissect(bytes(20), rns_metadata)
        assert packet.destination == "abcdef0123456789"  # First 16 hex chars

    def test_rns_hop_calculation(self, rns_dissector, rns_metadata):
        """RNS hop limit is 128 - hops_taken."""
        packet = rns_dissector.dissect(bytes(20), rns_metadata)
        assert packet.hop_start == 128
        assert packet.hop_limit == 126  # 128 - 2

    def test_rns_direction_inbound(self, rns_dissector):
        """Default direction is inbound."""
        metadata = {"protocol": "rns", "dest_hash": "aa" * 16}
        packet = rns_dissector.dissect(bytes(20), metadata)
        assert packet.direction == PacketDirection.INBOUND

    def test_rns_direction_outbound(self, rns_dissector):
        """Outbound direction from metadata."""
        metadata = {"protocol": "rns", "dest_hash": "aa" * 16, "direction": "outbound"}
        packet = rns_dissector.dissect(bytes(20), metadata)
        assert packet.direction == PacketDirection.OUTBOUND

    def test_rns_direction_internal(self, rns_dissector):
        """Internal direction from metadata."""
        metadata = {"protocol": "rns", "dest_hash": "aa" * 16, "direction": "internal"}
        packet = rns_dissector.dissect(bytes(20), metadata)
        assert packet.direction == PacketDirection.INTERNAL

    def test_rns_dest_hash_bytes(self, rns_dissector):
        """dest_hash as bytes instead of string."""
        dest = bytes.fromhex("abcdef0123456789abcdef0123456789")
        metadata = {"protocol": "rns", "dest_hash": dest}
        packet = rns_dissector.dissect(bytes(20), metadata)
        assert packet.rns_dest_hash == dest

    def test_rns_dest_hash_invalid(self, rns_dissector):
        """Invalid dest_hash string is handled."""
        metadata = {"protocol": "rns", "dest_hash": "not_hex"}
        packet = rns_dissector.dissect(bytes(20), metadata)
        assert packet.rns_dest_hash is None

    def test_rns_source_from_bytes(self, rns_dissector):
        """Source hash from bytes is converted to hex string."""
        source = bytes.fromhex("aabbccdd11223344")
        metadata = {"protocol": "rns", "source_hash": source}
        packet = rns_dissector.dissect(bytes(20), metadata)
        assert packet.source == "aabbccdd11223344"

    def test_rns_source_fallback(self, rns_dissector):
        """Source falls back to 'local' when not available."""
        metadata = {"protocol": "rns"}
        packet = rns_dissector.dissect(bytes(20), metadata)
        assert packet.source == "local"

    def test_rns_empty_data(self, rns_dissector):
        """Empty raw data doesn't crash."""
        metadata = {"protocol": "rns"}
        packet = rns_dissector.dissect(b"", metadata)
        assert packet.size == 0


# ---------------------------------------------------------------------------
# RNSDissector - protocol tree tests
# ---------------------------------------------------------------------------

class TestRNSProtocolTree:
    """Tests for RNS protocol tree construction."""

    def test_tree_has_frame_layer(self, rns_dissector, rns_metadata):
        """Tree includes Frame layer."""
        packet = rns_dissector.dissect(bytes(20), rns_metadata)
        assert packet.tree is not None

        frame_time = packet.tree.get_field("frame.time")
        assert frame_time is not None

    def test_tree_has_rns_layer(self, rns_dissector, rns_metadata):
        """Tree includes Reticulum layer."""
        packet = rns_dissector.dissect(bytes(20), rns_metadata)

        packet_type = packet.tree.get_field("rns.type")
        assert packet_type is not None
        assert packet_type.value == "DATA"

    def test_tree_has_dest_hash(self, rns_dissector, rns_metadata):
        """Tree includes destination hash."""
        packet = rns_dissector.dissect(bytes(20), rns_metadata)

        dest = packet.tree.get_field("rns.dest_hash")
        assert dest is not None
        assert dest.value == "abcdef0123456789abcdef0123456789"

    def test_tree_has_routing(self, rns_dissector, rns_metadata):
        """Tree includes routing fields."""
        packet = rns_dissector.dissect(bytes(20), rns_metadata)

        hops = packet.tree.get_field("rns.hops")
        assert hops is not None
        assert hops.value == 2

        ttl = packet.tree.get_field("rns.ttl")
        assert ttl is not None
        assert ttl.value == 126

    def test_tree_has_interface(self, rns_dissector, rns_metadata):
        """Tree includes interface info."""
        packet = rns_dissector.dissect(bytes(20), rns_metadata)

        iface = packet.tree.get_field("rns.interface")
        assert iface is not None
        assert iface.value == "TCPInterface"

    def test_tree_has_service(self, rns_dissector, rns_metadata):
        """Tree includes service/aspect."""
        packet = rns_dissector.dissect(bytes(20), rns_metadata)

        service = packet.tree.get_field("rns.service")
        assert service is not None
        assert service.value == "lxmf.delivery"

    def test_tree_header_parsing(self, rns_dissector, rns_metadata):
        """Tree parses raw header bytes when available."""
        # Construct raw data with known header bytes
        raw = bytes([0x32, 0x10]) + bytes(18)  # flags=3, hops=2, type=1
        packet = rns_dissector.dissect(raw, rns_metadata)

        flags = packet.tree.get_field("rns.header.flags")
        assert flags is not None
        assert flags.value == 3

        header_hops = packet.tree.get_field("rns.header.hops")
        assert header_hops is not None
        assert header_hops.value == 2

    def test_tree_announce_fields(self, rns_dissector, rns_announce_metadata):
        """Announce packet has announce-specific fields."""
        raw = bytes(20)
        packet = rns_dissector.dissect(raw, rns_announce_metadata)

        aspect = packet.tree.get_field("rns.announce.aspect")
        assert aspect is not None
        assert aspect.value == "lxmf.delivery"

        name = packet.tree.get_field("rns.announce.name")
        assert name is not None
        assert name.value == "Maui Node"

    def test_tree_announce_identity(self, rns_dissector, rns_announce_metadata):
        """Announce packet includes identity hash."""
        raw = bytes(20)
        packet = rns_dissector.dissect(raw, rns_announce_metadata)

        identity = packet.tree.get_field("rns.announce.identity")
        assert identity is not None
        assert identity.value == "aabbccdd11223344"

    def test_tree_link_fields(self, rns_dissector):
        """Link packets include link-specific fields."""
        metadata = {
            "protocol": "rns",
            "packet_type": "LINK_REQUEST",
            "link_id": "deadbeef12345678",
            "link_state": "pending",
            "rtt_ms": 150.5,
        }
        packet = rns_dissector.dissect(bytes(20), metadata)

        link_id = packet.tree.get_field("rns.link.id")
        assert link_id is not None
        assert link_id.value == "deadbeef12345678"

        state = packet.tree.get_field("rns.link.state")
        assert state is not None
        assert state.value == "pending"

        rtt = packet.tree.get_field("rns.link.rtt")
        assert rtt is not None
        assert abs(rtt.value - 150.5) < 0.1

    def test_tree_lxmf_fields(self, rns_dissector):
        """LXMF service includes message fields."""
        metadata = {
            "protocol": "rns",
            "service_type": "lxmf.delivery",
            "lxmf_title": "Test Message",
            "lxmf_content": "Hello from RNS!",
        }
        packet = rns_dissector.dissect(bytes(20), metadata)

        title = packet.tree.get_field("rns.lxmf.title")
        assert title is not None
        assert title.value == "Test Message"

        content = packet.tree.get_field("rns.lxmf.content")
        assert content is not None
        assert content.value == "Hello from RNS!"

    def test_tree_lxmf_long_content_truncated(self, rns_dissector):
        """Long LXMF content is truncated in tree."""
        long_content = "A" * 200
        metadata = {
            "protocol": "rns",
            "service_type": "lxmf.delivery",
            "lxmf_content": long_content,
        }
        packet = rns_dissector.dissect(bytes(20), metadata)

        content = packet.tree.get_field("rns.lxmf.content")
        assert content is not None
        assert len(content.value) < 200
        assert content.value.endswith("...")

    def test_tree_interface_type(self, rns_dissector):
        """Interface type field appears when provided."""
        metadata = {
            "protocol": "rns",
            "interface_type": "LoRa",
        }
        packet = rns_dissector.dissect(bytes(20), metadata)

        iface_type = packet.tree.get_field("rns.iface_type")
        assert iface_type is not None
        assert iface_type.value == "LoRa"


# ---------------------------------------------------------------------------
# DisplayFilter tests
# ---------------------------------------------------------------------------

class TestDisplayFilter:
    """Tests for Wireshark-style display filter."""

    def _make_meshtastic_packet(self, **overrides):
        """Helper to create a Meshtastic packet with tree."""
        dissector = MeshtasticDissector()
        metadata = {
            "from": "!aabb0001",
            "to": "!ffffffff",
            "channel": 0,
            "hopLimit": 2,
            "hopStart": 3,
            "portnum": 1,
            "snr": 8.5,
            "rssi": -80,
        }
        metadata.update(overrides)
        return dissector.dissect(b"data", metadata)

    def test_empty_filter_matches_all(self):
        """Empty filter matches everything."""
        f = DisplayFilter("")
        packet = self._make_meshtastic_packet()
        assert f.matches(packet) is True

    def test_equality_filter(self):
        """Equality filter on string field."""
        f = DisplayFilter('mesh.from == "!aabb0001"')
        packet = self._make_meshtastic_packet()
        assert f.matches(packet) is True

    def test_equality_filter_no_match(self):
        """Equality filter doesn't match wrong value."""
        f = DisplayFilter('mesh.from == "!ccdd0002"')
        packet = self._make_meshtastic_packet()
        assert f.matches(packet) is False

    def test_inequality_filter(self):
        """Inequality filter."""
        f = DisplayFilter('mesh.from != "!ccdd0002"')
        packet = self._make_meshtastic_packet()
        assert f.matches(packet) is True

    def test_numeric_greater_than(self):
        """Numeric greater than filter."""
        f = DisplayFilter('mesh.hops > 0')
        packet = self._make_meshtastic_packet()
        assert f.matches(packet) is True

    def test_numeric_less_than(self):
        """Numeric less than filter."""
        f = DisplayFilter('mesh.hop_limit < 5')
        packet = self._make_meshtastic_packet()
        assert f.matches(packet) is True

    def test_numeric_greater_equal(self):
        """Numeric greater-or-equal filter."""
        f = DisplayFilter('mesh.hop_limit >= 2')
        packet = self._make_meshtastic_packet()
        assert f.matches(packet) is True

    def test_numeric_less_equal(self):
        """Numeric less-or-equal filter."""
        f = DisplayFilter('mesh.hops <= 1')
        packet = self._make_meshtastic_packet()
        assert f.matches(packet) is True

    def test_float_filter(self):
        """Float comparison filter."""
        f = DisplayFilter('mesh.snr >= -5')
        packet = self._make_meshtastic_packet(snr=8.5)
        assert f.matches(packet) is True

    def test_float_filter_negative(self):
        """Float comparison with negative target."""
        f = DisplayFilter('mesh.snr > 10')
        packet = self._make_meshtastic_packet(snr=8.5)
        assert f.matches(packet) is False

    def test_boolean_filter_true(self):
        """Boolean filter matches true."""
        f = DisplayFilter('mesh.mqtt == true')
        packet = self._make_meshtastic_packet(viaMqtt=True)
        assert f.matches(packet) is True

    def test_boolean_filter_false(self):
        """Boolean filter matches false."""
        f = DisplayFilter('mesh.mqtt == false')
        packet = self._make_meshtastic_packet(viaMqtt=False)
        assert f.matches(packet) is True

    def test_missing_field_no_match(self):
        """Filter on non-existent field returns False."""
        f = DisplayFilter('mesh.nonexistent == "value"')
        packet = self._make_meshtastic_packet()
        assert f.matches(packet) is False

    def test_no_tree_no_match(self):
        """Packet without tree returns False."""
        f = DisplayFilter('mesh.from == "!abc"')
        packet = MeshPacket()
        assert f.matches(packet) is False

    def test_compile_empty(self):
        """Compiling empty expression succeeds."""
        f = DisplayFilter("")
        assert f.compile() is True

    def test_compile_valid(self):
        """Compiling valid expression succeeds."""
        f = DisplayFilter('mesh.from == "!abc"')
        assert f.compile() is True

    def test_and_filter(self):
        """AND filter combines multiple conditions."""
        f = DisplayFilter('mesh.channel == 0 and mesh.hops > 0')
        packet = self._make_meshtastic_packet()
        assert f.matches(packet) is True

    def test_and_filter_fails(self):
        """AND filter fails when one condition doesn't match."""
        f = DisplayFilter('mesh.channel == 0 and mesh.hops > 5')
        packet = self._make_meshtastic_packet()
        assert f.matches(packet) is False

    def test_portnum_filter(self):
        """Portnum filter for protocol selection."""
        f = DisplayFilter('mesh.portnum == 1')
        packet = self._make_meshtastic_packet(portnum=1)
        assert f.matches(packet) is True

    def test_available_fields(self):
        """get_available_fields returns expected fields."""
        fields = DisplayFilter.get_available_fields()
        assert "mesh.from" in fields
        assert "mesh.snr" in fields
        assert "rns.type" in fields
        assert "frame.protocol" in fields
        # Check descriptions are non-empty
        assert len(fields["mesh.from"]) > 0


# ---------------------------------------------------------------------------
# Traffic models tests
# ---------------------------------------------------------------------------

class TestTrafficModels:
    """Tests for traffic_models data classes."""

    def test_packet_field_to_dict(self):
        """PacketField serializes to dict."""
        f = PacketField(name="Test", abbrev="test.field", value="hello",
                        field_type=FieldType.STRING, description="A test field")
        d = f.to_dict()
        assert d["name"] == "Test"
        assert d["value"] == "hello"
        assert d["type"] == "string"

    def test_packet_field_with_children(self):
        """PacketField serializes children."""
        parent = PacketField(name="Parent", abbrev="p", value=None,
                             field_type=FieldType.NESTED)
        child = PacketField(name="Child", abbrev="p.c", value=42,
                            field_type=FieldType.INTEGER)
        parent.children.append(child)
        d = parent.to_dict()
        assert "children" in d
        assert len(d["children"]) == 1

    def test_packet_field_display_value(self):
        """PacketField display value formatting."""
        # None
        f = PacketField(name="T", abbrev="t", value=None)
        assert f.get_display_value() == "<none>"

        # Bool
        f = PacketField(name="T", abbrev="t", value=True, field_type=FieldType.BOOLEAN)
        assert f.get_display_value() == "True"

        # Float
        f = PacketField(name="T", abbrev="t", value=3.14159, field_type=FieldType.FLOAT)
        assert "3.1416" in f.get_display_value()

        # Bytes (short)
        f = PacketField(name="T", abbrev="t", value=b"\xab\xcd")
        assert f.get_display_value() == "abcd"

        # Bytes (long)
        f = PacketField(name="T", abbrev="t", value=b"\x00" * 20)
        assert "bytes" in f.get_display_value()

    def test_packet_field_matches_filter_contains(self):
        """PacketField contains operator."""
        f = PacketField(name="T", abbrev="t", value="Hello World",
                        field_type=FieldType.STRING)
        assert f.matches_filter("contains", "world") is True
        assert f.matches_filter("contains", "xyz") is False

    def test_packet_field_matches_filter_matches(self):
        """PacketField regex matches operator."""
        f = PacketField(name="T", abbrev="t", value="!aabb0001",
                        field_type=FieldType.STRING)
        assert f.matches_filter("matches", r"!aabb\d+") is True
        assert f.matches_filter("matches", r"^!ccdd") is False

    def test_packet_tree_operations(self):
        """PacketTree add/get operations."""
        tree = PacketTree()
        layer = tree.add_layer("Test", "test")
        tree.add_field(layer, "Value", "test.val", 42, FieldType.INTEGER)

        assert tree.get_field("test.val") is not None
        assert tree.get_field("test.val").value == 42
        assert tree.get_field("nonexistent") is None

    def test_packet_tree_to_dict(self):
        """PacketTree serializes to dict list."""
        tree = PacketTree()
        layer = tree.add_layer("Layer", "l")
        tree.add_field(layer, "F", "l.f", "v")

        d = tree.to_dict()
        assert isinstance(d, list)
        assert len(d) == 1

    def test_mesh_packet_summary(self):
        """MeshPacket summary string."""
        pkt = MeshPacket(
            protocol=PacketProtocol.MESHTASTIC,
            source="!aabb0001",
            destination="!ffffffff",
            port_name="TEXT_MESSAGE",
            snr=8.5,
            rssi=-80,
        )
        summary = pkt.get_summary()
        assert "!aabb0001" in summary
        assert "TEXT_MESSAGE" in summary

    def test_mesh_packet_to_dict(self):
        """MeshPacket serializes to dict."""
        pkt = MeshPacket(
            protocol=PacketProtocol.MESHTASTIC,
            source="!abc",
        )
        d = pkt.to_dict()
        assert d["protocol"] == "meshtastic"
        assert d["source"] == "!abc"

    def test_mesh_packet_from_dict(self):
        """MeshPacket deserializes from dict."""
        data = {
            "id": "test_pkt",
            "timestamp": datetime.now().isoformat(),
            "protocol": "meshtastic",
            "source": "!abc",
            "destination": "broadcast",
            "channel": 1,
            "portnum": 1,
        }
        pkt = MeshPacket.from_dict(data)
        assert pkt.source == "!abc"
        assert pkt.channel == 1

    def test_meshtastic_ports_mapping(self):
        """MESHTASTIC_PORTS has expected entries."""
        assert MESHTASTIC_PORTS[1] == "TEXT_MESSAGE"
        assert MESHTASTIC_PORTS[4] == "POSITION"
        assert MESHTASTIC_PORTS[5] == "NODEINFO"
        assert MESHTASTIC_PORTS[67] == "TELEMETRY"
        assert MESHTASTIC_PORTS[70] == "TRACEROUTE"
        assert MESHTASTIC_PORTS[71] == "NEIGHBORINFO"

    def test_hop_info_to_dict(self):
        """HopInfo serializes correctly."""
        from monitoring.traffic_models import HopInfo
        hop = HopInfo(hop_number=1, node_id="!abc", snr=5.0, rssi=-75)
        d = hop.to_dict()
        assert d["hop"] == 1
        assert d["node_id"] == "!abc"
        assert d["snr"] == 5.0
