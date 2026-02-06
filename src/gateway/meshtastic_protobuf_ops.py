"""
Meshtastic Protobuf Operations — Data classes and helpers.

Provides structured data types for protobuf-over-HTTP operations:
- Config/ModuleConfig snapshots
- Neighbor info tracking
- Device metadata
- Traceroute results
- Event types for the polling dispatch system

These are stateless, testable structures consumed by MeshtasticProtobufClient.
"""

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Event types for the callback/dispatch system
# ---------------------------------------------------------------------------

class ProtobufEventType(Enum):
    """Events dispatched by the protobuf polling loop."""
    PACKET_RECEIVED = "packet_received"
    NODE_INFO_UPDATED = "node_info_updated"
    CONFIG_RECEIVED = "config_received"
    MODULE_CONFIG_RECEIVED = "module_config_received"
    CHANNEL_RECEIVED = "channel_received"
    CONFIG_COMPLETE = "config_complete"
    LOG_RECORD = "log_record"
    MY_INFO = "my_info"
    METADATA = "metadata"
    NEIGHBOR_INFO = "neighbor_info"
    TRACEROUTE_RESULT = "traceroute_result"
    POSITION_RECEIVED = "position_received"
    QUEUE_STATUS = "queue_status"
    CONNECTION_STATE = "connection_state"


# ---------------------------------------------------------------------------
# Transport configuration
# ---------------------------------------------------------------------------

@dataclass
class ProtobufTransportConfig:
    """Configuration for the protobuf HTTP transport."""
    host: str = "localhost"
    port: int = 9443
    tls: bool = True
    poll_interval: float = 0.5
    connect_timeout: float = 5.0
    read_timeout: float = 10.0
    session_timeout: float = 30.0
    max_empty_polls: int = 10
    backoff_interval: float = 2.0


# ---------------------------------------------------------------------------
# Config snapshots
# ---------------------------------------------------------------------------

@dataclass
class DeviceConfigSnapshot:
    """Snapshot of all device configuration sections.

    Fields hold the raw protobuf Config sub-messages (or None if not yet fetched).
    Use ``to_dict()`` for JSON-serializable output.
    """
    device: Optional[Any] = None
    position: Optional[Any] = None
    power: Optional[Any] = None
    network: Optional[Any] = None
    display: Optional[Any] = None
    lora: Optional[Any] = None
    bluetooth: Optional[Any] = None
    security: Optional[Any] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dict using protobuf JSON format."""
        result: Dict[str, Any] = {}
        try:
            import google.protobuf.json_format as jf
            for name in (
                'device', 'position', 'power', 'network',
                'display', 'lora', 'bluetooth', 'security',
            ):
                val = getattr(self, name)
                if val is not None:
                    result[name] = jf.MessageToDict(val)
        except ImportError:
            for name in (
                'device', 'position', 'power', 'network',
                'display', 'lora', 'bluetooth', 'security',
            ):
                val = getattr(self, name)
                if val is not None:
                    result[name] = str(val)
        return result


@dataclass
class ModuleConfigSnapshot:
    """Snapshot of all module configuration sections."""
    mqtt: Optional[Any] = None
    serial: Optional[Any] = None
    external_notification: Optional[Any] = None
    store_forward: Optional[Any] = None
    range_test: Optional[Any] = None
    telemetry: Optional[Any] = None
    canned_message: Optional[Any] = None
    audio: Optional[Any] = None
    remote_hardware: Optional[Any] = None
    neighbor_info: Optional[Any] = None
    ambient_lighting: Optional[Any] = None
    detection_sensor: Optional[Any] = None
    paxcounter: Optional[Any] = None

    def to_dict(self) -> Dict[str, Any]:
        result: Dict[str, Any] = {}
        try:
            import google.protobuf.json_format as jf
            for name in (
                'mqtt', 'serial', 'external_notification', 'store_forward',
                'range_test', 'telemetry', 'canned_message', 'audio',
                'remote_hardware', 'neighbor_info', 'ambient_lighting',
                'detection_sensor', 'paxcounter',
            ):
                val = getattr(self, name)
                if val is not None:
                    result[name] = jf.MessageToDict(val)
        except ImportError:
            pass
        return result


# ---------------------------------------------------------------------------
# Neighbor info
# ---------------------------------------------------------------------------

@dataclass
class NeighborEntry:
    """A single neighbor relationship from a NeighborInfo broadcast."""
    node_id: int
    snr: float = 0.0
    last_rx_time: int = 0
    node_broadcast_interval_secs: int = 0


@dataclass
class NeighborReport:
    """Neighbor info from a specific node."""
    reporting_node_id: int
    last_sent_by_id: int = 0
    broadcast_interval_secs: int = 0
    neighbors: List[NeighborEntry] = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)


# ---------------------------------------------------------------------------
# Device metadata
# ---------------------------------------------------------------------------

@dataclass
class DeviceMetadataResult:
    """Device metadata response from AdminMessage."""
    firmware_version: str = ""
    device_state_version: int = 0
    hw_model: str = ""
    can_shutdown: bool = False
    has_wifi: bool = False
    has_bluetooth: bool = False
    has_ethernet: bool = False
    has_remote_hardware: bool = False
    has_pkc: bool = False
    role: str = ""
    position_flags: int = 0


# ---------------------------------------------------------------------------
# Traceroute
# ---------------------------------------------------------------------------

@dataclass
class TracerouteResult:
    """Result of a traceroute operation."""
    destination: int
    route: List[int] = field(default_factory=list)
    snr_towards: List[float] = field(default_factory=list)
    route_back: List[int] = field(default_factory=list)
    snr_back: List[float] = field(default_factory=list)
    completed: bool = False
    timestamp: float = field(default_factory=time.time)


# ---------------------------------------------------------------------------
# Protobuf parsing helpers (stateless, testable)
# ---------------------------------------------------------------------------

def parse_neighbor_info(packet_payload: bytes, from_node: int) -> Optional[NeighborReport]:
    """Parse a NEIGHBORINFO_APP packet payload into a NeighborReport.

    Args:
        packet_payload: Raw protobuf bytes from MeshPacket.decoded.payload
        from_node: The node number that sent this packet

    Returns:
        NeighborReport or None on parse failure
    """
    try:
        from meshtastic.protobuf import mesh_pb2
        ni = mesh_pb2.NeighborInfo()
        ni.ParseFromString(packet_payload)

        neighbors = []
        for n in ni.neighbors:
            neighbors.append(NeighborEntry(
                node_id=n.node_id,
                snr=n.snr,
                last_rx_time=n.last_rx_time,
                node_broadcast_interval_secs=n.node_broadcast_interval_secs,
            ))

        return NeighborReport(
            reporting_node_id=ni.node_id or from_node,
            last_sent_by_id=ni.last_sent_by_id,
            broadcast_interval_secs=ni.node_broadcast_interval_secs,
            neighbors=neighbors,
        )
    except Exception as e:
        logger.warning(f"Failed to parse NeighborInfo: {e}")
        return None


def parse_device_metadata(admin_payload: bytes) -> Optional[DeviceMetadataResult]:
    """Parse an AdminMessage containing get_device_metadata_response.

    Args:
        admin_payload: Raw protobuf bytes of AdminMessage

    Returns:
        DeviceMetadataResult or None on parse failure
    """
    try:
        from meshtastic.protobuf import admin_pb2, mesh_pb2
        admin = admin_pb2.AdminMessage()
        admin.ParseFromString(admin_payload)

        if not admin.HasField('get_device_metadata_response'):
            return None

        md = admin.get_device_metadata_response
        hw_name = ""
        try:
            hw_name = mesh_pb2.HardwareModel.Name(md.hw_model)
        except ValueError:
            hw_name = str(md.hw_model)

        role_name = ""
        try:
            from meshtastic.protobuf import config_pb2
            role_name = config_pb2.Config.DeviceConfig.Role.Name(md.role)
        except (ValueError, AttributeError):
            role_name = str(md.role)

        return DeviceMetadataResult(
            firmware_version=md.firmware_version,
            device_state_version=md.device_state_version,
            hw_model=hw_name,
            can_shutdown=md.canShutdown,
            has_wifi=md.hasWifi,
            has_bluetooth=md.hasBluetooth,
            has_ethernet=md.hasEthernet,
            has_remote_hardware=md.hasRemoteHardware,
            has_pkc=md.hasPKC,
            role=role_name,
            position_flags=md.position_flags,
        )
    except Exception as e:
        logger.warning(f"Failed to parse DeviceMetadata: {e}")
        return None


def parse_traceroute(packet_payload: bytes, destination: int) -> Optional[TracerouteResult]:
    """Parse a TRACEROUTE_APP response payload into a TracerouteResult.

    Args:
        packet_payload: Raw protobuf bytes from MeshPacket.decoded.payload
        destination: The destination node number of the traceroute

    Returns:
        TracerouteResult or None on parse failure
    """
    try:
        from meshtastic.protobuf import mesh_pb2
        rd = mesh_pb2.RouteDiscovery()
        rd.ParseFromString(packet_payload)

        return TracerouteResult(
            destination=destination,
            route=list(rd.route),
            snr_towards=[s / 4.0 for s in rd.snr_towards],
            route_back=list(rd.route_back),
            snr_back=[s / 4.0 for s in rd.snr_back],
            completed=True,
        )
    except Exception as e:
        logger.warning(f"Failed to parse TracerouteResult: {e}")
        return None


def parse_position(packet_payload: bytes) -> Optional[Dict[str, Any]]:
    """Parse a POSITION_APP packet payload into a dict.

    Args:
        packet_payload: Raw protobuf bytes from MeshPacket.decoded.payload

    Returns:
        Dict with latitude, longitude, altitude, etc. or None on failure
    """
    try:
        from meshtastic.protobuf import mesh_pb2
        pos = mesh_pb2.Position()
        pos.ParseFromString(packet_payload)

        result: Dict[str, Any] = {}
        if pos.latitude_i != 0 or pos.longitude_i != 0:
            result['latitude'] = pos.latitude_i * 1e-7
            result['longitude'] = pos.longitude_i * 1e-7
        if pos.altitude != 0:
            result['altitude'] = pos.altitude
        if pos.time != 0:
            result['time'] = pos.time
        if pos.ground_speed != 0:
            result['ground_speed'] = pos.ground_speed
        if pos.ground_track != 0:
            result['ground_track'] = pos.ground_track
        if pos.sats_in_view != 0:
            result['sats_in_view'] = pos.sats_in_view
        if pos.precision_bits != 0:
            result['precision_bits'] = pos.precision_bits

        return result if result else None
    except Exception as e:
        logger.warning(f"Failed to parse Position: {e}")
        return None


# Config type name mappings for readable output
CONFIG_TYPE_NAMES = {
    0: 'device',
    1: 'position',
    2: 'power',
    3: 'network',
    4: 'display',
    5: 'lora',
    6: 'bluetooth',
    7: 'security',
    8: 'sessionkey',
    9: 'deviceui',
}

MODULE_CONFIG_TYPE_NAMES = {
    0: 'mqtt',
    1: 'serial',
    2: 'external_notification',
    3: 'store_forward',
    4: 'range_test',
    5: 'telemetry',
    6: 'canned_message',
    7: 'audio',
    8: 'remote_hardware',
    9: 'neighbor_info',
    10: 'ambient_lighting',
    11: 'detection_sensor',
    12: 'paxcounter',
    13: 'statusmessage',
}
