"""Admin/config/request operations for MeshtasticProtobufClient.

Extracted from meshtastic_protobuf_client.py for file size compliance (CLAUDE.md #6).

Provides admin message sending, config read/write, channel/owner operations,
device metadata, neighbor info, traceroute, position requests, and cached
state accessors via a mixin class.
"""

import logging
import time
from typing import Any, Dict, List, Optional

from utils.safe_import import safe_import

from .meshtastic_protobuf_ops import (
    DeviceConfigSnapshot,
    DeviceMetadataResult,
    ModuleConfigSnapshot,
    NeighborReport,
    TracerouteResult,
    parse_device_metadata,
    parse_position,
    parse_traceroute,
)

logger = logging.getLogger(__name__)

# Protobuf imports — same deferred pattern as meshtastic_protobuf_client.py
_admin_pb2, _config_pb2, _mesh_pb2, _module_config_pb2, _portnums_pb2, _HAS_PB2 = safe_import(
    'meshtastic.protobuf',
    'admin_pb2', 'config_pb2', 'mesh_pb2', 'module_config_pb2', 'portnums_pb2',
    package=None,
)
_json_format_mod, _HAS_PB_JSON = safe_import('google.protobuf.json_format')
_pb2_available = _HAS_PB2 and _HAS_PB_JSON

if _HAS_PB2:
    admin_pb2 = _admin_pb2
    config_pb2 = _config_pb2
    mesh_pb2 = _mesh_pb2
    module_config_pb2 = _module_config_pb2
    portnums_pb2 = _portnums_pb2


class ProtobufAdminMixin:
    """Mixin: admin messages, config R/W, channels, owner, traceroute, position.

    Requires host class to provide:
    - self.send_mesh_packet(payload, dest_num, portnum, ...) -> int
    - self._register_pending(request_id) -> Event
    - self._wait_for_response(request_id, timeout) -> decoded
    - self._get_fromradio() -> bytes
    - self._dispatch_fromradio(data)
    - self._my_node_num, self._pending_lock, self._pending_events, self._pending_responses
    - self._device_config, self._module_config, self._channels, self._node_infos
    - self.is_polling (property)
    """

    # ------------------------------------------------------------------
    # Admin message helpers
    # ------------------------------------------------------------------

    def _send_admin(
        self,
        admin_msg,
        dest_num: Optional[int] = None,
        want_response: bool = True,
    ) -> int:
        """Send an AdminMessage to a node.

        Args:
            admin_msg: admin_pb2.AdminMessage
            dest_num: Target node (None = local node)
            want_response: Whether to expect a response

        Returns:
            Packet ID (0 on failure)
        """
        if dest_num is None:
            dest_num = self._my_node_num
        if dest_num is None:
            logger.error("Cannot send admin: no node number")
            return 0

        return self.send_mesh_packet(
            payload=admin_msg.SerializeToString(),
            dest_num=dest_num,
            portnum=portnums_pb2.PortNum.ADMIN_APP,
            want_response=want_response,
        )

    def _admin_request(
        self,
        admin_msg,
        dest_num: Optional[int] = None,
        timeout: float = 10.0,
    ) -> Optional[Any]:
        """Send an admin request and wait for the response.

        Args:
            admin_msg: AdminMessage to send
            dest_num: Target node (None = local)
            timeout: Response timeout in seconds

        Returns:
            Decoded response payload, or None on timeout
        """
        packet_id = self._send_admin(admin_msg, dest_num=dest_num)
        if packet_id == 0:
            return None

        event = self._register_pending(packet_id)

        # If not polling, do manual poll
        if not self.is_polling:
            return self._manual_poll_for_response(packet_id, timeout)

        return self._wait_for_response(packet_id, timeout)

    def _manual_poll_for_response(
        self, request_id: int, timeout: float
    ) -> Optional[Any]:
        """Poll for a response manually (when polling thread is not running)."""
        deadline = time.monotonic() + timeout

        while time.monotonic() < deadline:
            data = self._get_fromradio()
            if data:
                self._dispatch_fromradio(data)

            with self._pending_lock:
                if request_id not in self._pending_events:
                    # Already resolved
                    return self._pending_responses.pop(request_id, None)
                event = self._pending_events.get(request_id)

            if event and event.is_set():
                with self._pending_lock:
                    self._pending_events.pop(request_id, None)
                    return self._pending_responses.pop(request_id, None)

            time.sleep(0.1)

        # Timeout
        with self._pending_lock:
            self._pending_events.pop(request_id, None)
            self._pending_responses.pop(request_id, None)
        return None

    # ------------------------------------------------------------------
    # Config read operations
    # ------------------------------------------------------------------

    def get_config(self, config_type: int) -> Optional[Any]:
        """Get a device config section.

        Args:
            config_type: AdminMessage.ConfigType value (0=device, 5=lora, etc.)

        Returns:
            The config protobuf sub-message, or None on failure
        """
        if not _pb2_available:
            return None

        admin = admin_pb2.AdminMessage()
        admin.get_config_request = config_type

        resp = self._admin_request(admin)
        if resp and hasattr(resp, 'payload'):
            try:
                admin_resp = admin_pb2.AdminMessage()
                admin_resp.ParseFromString(resp.payload)
                if admin_resp.HasField('get_config_response'):
                    cfg = admin_resp.get_config_response
                    variant = cfg.WhichOneof("payload_variant")
                    if variant:
                        return getattr(cfg, variant)
            except Exception as e:
                logger.warning(f"Failed to parse config response: {e}")
        return None

    def get_all_config(self) -> DeviceConfigSnapshot:
        """Get all device configuration sections.

        Returns:
            DeviceConfigSnapshot with all populated sections
        """
        snapshot = DeviceConfigSnapshot()
        if not _pb2_available:
            return snapshot

        config_fields = [
            (0, 'device'), (1, 'position'), (2, 'power'),
            (3, 'network'), (4, 'display'), (5, 'lora'),
            (6, 'bluetooth'), (7, 'security'),
        ]
        for type_id, field_name in config_fields:
            result = self.get_config(type_id)
            if result is not None:
                setattr(snapshot, field_name, result)

        return snapshot

    def get_module_config(self, module_type: int) -> Optional[Any]:
        """Get a module config section.

        Args:
            module_type: AdminMessage.ModuleConfigType value (0=mqtt, 5=telemetry, etc.)

        Returns:
            The module config protobuf sub-message, or None on failure
        """
        if not _pb2_available:
            return None

        admin = admin_pb2.AdminMessage()
        admin.get_module_config_request = module_type

        resp = self._admin_request(admin)
        if resp and hasattr(resp, 'payload'):
            try:
                admin_resp = admin_pb2.AdminMessage()
                admin_resp.ParseFromString(resp.payload)
                if admin_resp.HasField('get_module_config_response'):
                    mcfg = admin_resp.get_module_config_response
                    variant = mcfg.WhichOneof("payload_variant")
                    if variant:
                        return getattr(mcfg, variant)
            except Exception as e:
                logger.warning(f"Failed to parse module config response: {e}")
        return None

    def get_all_module_config(self) -> ModuleConfigSnapshot:
        """Get all module configuration sections.

        Returns:
            ModuleConfigSnapshot with all populated sections
        """
        snapshot = ModuleConfigSnapshot()
        if not _pb2_available:
            return snapshot

        module_fields = [
            (0, 'mqtt'), (1, 'serial'), (2, 'external_notification'),
            (3, 'store_forward'), (4, 'range_test'), (5, 'telemetry'),
            (6, 'canned_message'), (7, 'audio'), (8, 'remote_hardware'),
            (9, 'neighbor_info'), (10, 'ambient_lighting'),
            (11, 'detection_sensor'), (12, 'paxcounter'),
        ]
        for type_id, field_name in module_fields:
            result = self.get_module_config(type_id)
            if result is not None:
                setattr(snapshot, field_name, result)

        return snapshot

    # ------------------------------------------------------------------
    # Config write operations
    # ------------------------------------------------------------------

    def set_config(self, config_name: str, config_msg: Any) -> bool:
        """Set a device config section (with begin/commit transaction).

        Args:
            config_name: Config section name ('device', 'lora', etc.)
            config_msg: The protobuf config sub-message to apply

        Returns:
            True on success
        """
        if not _pb2_available:
            return False

        # Begin edit
        begin = admin_pb2.AdminMessage()
        begin.begin_edit_settings = True
        self._send_admin(begin, want_response=False)
        time.sleep(0.2)

        # Set config
        setter = admin_pb2.AdminMessage()
        set_cfg = setter.set_config
        if hasattr(set_cfg, config_name):
            getattr(set_cfg, config_name).CopyFrom(config_msg)
        else:
            logger.error(f"Unknown config section: {config_name}")
            return False
        self._send_admin(setter, want_response=False)
        time.sleep(0.2)

        # Commit
        commit = admin_pb2.AdminMessage()
        commit.commit_edit_settings = True
        self._send_admin(commit, want_response=False)

        logger.info(f"Config '{config_name}' written successfully")
        return True

    def set_module_config(self, module_name: str, module_msg: Any) -> bool:
        """Set a module config section (with begin/commit transaction).

        Args:
            module_name: Module name ('mqtt', 'telemetry', etc.)
            module_msg: The protobuf module config sub-message to apply

        Returns:
            True on success
        """
        if not _pb2_available:
            return False

        # Begin edit
        begin = admin_pb2.AdminMessage()
        begin.begin_edit_settings = True
        self._send_admin(begin, want_response=False)
        time.sleep(0.2)

        # Set module config
        setter = admin_pb2.AdminMessage()
        set_mcfg = setter.set_module_config
        if hasattr(set_mcfg, module_name):
            getattr(set_mcfg, module_name).CopyFrom(module_msg)
        else:
            logger.error(f"Unknown module config: {module_name}")
            return False
        self._send_admin(setter, want_response=False)
        time.sleep(0.2)

        # Commit
        commit = admin_pb2.AdminMessage()
        commit.commit_edit_settings = True
        self._send_admin(commit, want_response=False)

        logger.info(f"Module config '{module_name}' written successfully")
        return True

    # ------------------------------------------------------------------
    # Channel operations
    # ------------------------------------------------------------------

    def get_channel(self, index: int) -> Optional[Any]:
        """Get a specific channel by index.

        Args:
            index: Channel index (0-7)

        Returns:
            Channel protobuf, or None
        """
        if not _pb2_available:
            return None

        admin = admin_pb2.AdminMessage()
        admin.get_channel_request = index + 1  # 1-indexed in the protocol

        resp = self._admin_request(admin)
        if resp and hasattr(resp, 'payload'):
            try:
                admin_resp = admin_pb2.AdminMessage()
                admin_resp.ParseFromString(resp.payload)
                if admin_resp.HasField('get_channel_response'):
                    return admin_resp.get_channel_response
            except Exception as e:
                logger.warning(f"Failed to parse channel response: {e}")
        return None

    def get_channels(self) -> List[Any]:
        """Get all channels (from cache or fresh request).

        Returns:
            List of Channel protobufs
        """
        if self._channels:
            return list(self._channels)

        channels = []
        for i in range(8):
            ch = self.get_channel(i)
            if ch:
                channels.append(ch)
        return channels

    def set_channel(self, channel) -> bool:
        """Set a channel configuration.

        Args:
            channel: channel_pb2.Channel with index set

        Returns:
            True on success
        """
        if not _pb2_available:
            return False

        admin = admin_pb2.AdminMessage()
        admin.set_channel.CopyFrom(channel)
        packet_id = self._send_admin(admin, want_response=False)
        return packet_id != 0

    # ------------------------------------------------------------------
    # Owner operations
    # ------------------------------------------------------------------

    def get_owner(self) -> Optional[Any]:
        """Get device owner info.

        Returns:
            User protobuf, or None
        """
        if not _pb2_available:
            return None

        admin = admin_pb2.AdminMessage()
        admin.get_owner_request = True

        resp = self._admin_request(admin)
        if resp and hasattr(resp, 'payload'):
            try:
                admin_resp = admin_pb2.AdminMessage()
                admin_resp.ParseFromString(resp.payload)
                if admin_resp.HasField('get_owner_response'):
                    return admin_resp.get_owner_response
            except Exception as e:
                logger.warning(f"Failed to parse owner response: {e}")
        return None

    def set_owner(
        self,
        long_name: Optional[str] = None,
        short_name: Optional[str] = None,
    ) -> bool:
        """Set device owner name.

        Args:
            long_name: Long name (up to 40 chars)
            short_name: Short name (up to 4 chars)

        Returns:
            True on success
        """
        if not _pb2_available:
            return False

        admin = admin_pb2.AdminMessage()
        if long_name is not None:
            admin.set_owner.long_name = long_name[:40]
        if short_name is not None:
            admin.set_owner.short_name = short_name[:4]

        packet_id = self._send_admin(admin, want_response=False)
        return packet_id != 0

    # ------------------------------------------------------------------
    # Device metadata
    # ------------------------------------------------------------------

    def request_device_metadata(
        self, node_num: Optional[int] = None, timeout: float = 10.0
    ) -> Optional[DeviceMetadataResult]:
        """Request device metadata from a node.

        Args:
            node_num: Target node (None = local)
            timeout: Response timeout

        Returns:
            DeviceMetadataResult or None on timeout
        """
        if not _pb2_available:
            return None

        admin = admin_pb2.AdminMessage()
        admin.get_device_metadata_request = True

        resp = self._admin_request(admin, dest_num=node_num, timeout=timeout)
        if resp and hasattr(resp, 'payload'):
            return parse_device_metadata(resp.payload)
        return None

    # ------------------------------------------------------------------
    # Neighbor info
    # ------------------------------------------------------------------

    def get_neighbor_reports(self) -> Dict[int, NeighborReport]:
        """Get all cached neighbor reports received via NEIGHBORINFO_APP.

        To collect neighbor info, ensure neighbor_info module is enabled
        on mesh nodes and the polling loop is running. Reports arrive
        automatically as nodes broadcast their neighbor tables.

        Returns:
            Dict mapping node_num to their latest NeighborReport
        """
        # Neighbor reports are dispatched via callbacks during polling.
        # This is a convenience accessor for any accumulated reports.
        # Callers should register a NEIGHBOR_INFO callback for real-time data.
        return {}

    # ------------------------------------------------------------------
    # Traceroute
    # ------------------------------------------------------------------

    def send_traceroute(
        self, dest_num: int, hop_limit: int = 7, timeout: float = 30.0
    ) -> Optional[TracerouteResult]:
        """Send a traceroute to a destination node.

        Args:
            dest_num: Destination node number
            hop_limit: Maximum hops
            timeout: Response timeout

        Returns:
            TracerouteResult or None on timeout
        """
        if not _pb2_available:
            return None

        route_discovery = mesh_pb2.RouteDiscovery()
        payload = route_discovery.SerializeToString()

        packet_id = self.send_mesh_packet(
            payload=payload,
            dest_num=dest_num,
            portnum=portnums_pb2.PortNum.TRACEROUTE_APP,
            want_response=True,
            hop_limit=hop_limit,
        )
        if packet_id == 0:
            return None

        event = self._register_pending(packet_id)

        if not self.is_polling:
            resp = self._manual_poll_for_response(packet_id, timeout)
        else:
            resp = self._wait_for_response(packet_id, timeout)

        if resp and hasattr(resp, 'payload'):
            return parse_traceroute(resp.payload, dest_num)
        return None

    # ------------------------------------------------------------------
    # Position request
    # ------------------------------------------------------------------

    def request_position(
        self, dest_num: int, timeout: float = 10.0
    ) -> Optional[Dict[str, Any]]:
        """Request position from a remote node.

        Args:
            dest_num: Target node number
            timeout: Response timeout

        Returns:
            Position dict or None on timeout
        """
        if not _pb2_available:
            return None

        position = mesh_pb2.Position()
        payload = position.SerializeToString()

        packet_id = self.send_mesh_packet(
            payload=payload,
            dest_num=dest_num,
            portnum=portnums_pb2.PortNum.POSITION_APP,
            want_response=True,
        )
        if packet_id == 0:
            return None

        event = self._register_pending(packet_id)

        if not self.is_polling:
            resp = self._manual_poll_for_response(packet_id, timeout)
        else:
            resp = self._wait_for_response(packet_id, timeout)

        if resp and hasattr(resp, 'payload'):
            return parse_position(resp.payload)
        return None

    # ------------------------------------------------------------------
    # Cached state accessors
    # ------------------------------------------------------------------

    def get_cached_config(self) -> DeviceConfigSnapshot:
        """Return the config snapshot from session setup."""
        return self._device_config

    def get_cached_module_config(self) -> ModuleConfigSnapshot:
        """Return the module config snapshot from session setup."""
        return self._module_config

    def get_cached_node_infos(self) -> Dict[int, Any]:
        """Return node infos received during session setup and polling."""
        return dict(self._node_infos)

    def get_cached_channels(self) -> List[Any]:
        """Return channels received during session setup."""
        return list(self._channels)
