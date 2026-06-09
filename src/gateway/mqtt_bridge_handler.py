"""
MQTT Bridge Handler for RNS Gateway.

Replaces TCP-based MeshtasticHandler with zero-interference approach.

RX: Receives mesh traffic via MQTT subscription (no TCP connection needed).
TX: Sends to mesh via HTTP protobuf (/api/v1/toradio), CLI as fallback.

Architecture:
    RX: Meshtastic mesh -> meshtasticd -> MQTT broker -> MQTTBridgeHandler
    TX: MQTTBridgeHandler -> HTTP protobuf -> meshtasticd -> Meshtastic mesh
        (fallback: CLI subprocess -> meshtasticd TCP -> Meshtastic mesh)

Zero interference:
    - RX via MQTT: no TCP connection to meshtasticd
    - TX via HTTP protobuf: uses /api/v1/toradio (same as web client)
    - Web client on :9443 works uninterrupted
    - Multiple monitoring tools can coexist

Requires:
    - mosquitto (or any MQTT broker) running locally
    - meshtasticd configured with mqtt.enabled=true, mqtt.json_enabled=true
    - paho-mqtt (pip install paho-mqtt)
    - meshtastic Python package (for protobuf TX; CLI used as fallback)

Usage:
    handler = MQTTBridgeHandler(config, node_tracker, health, ...)
    handler.run_loop()  # Blocks, runs in thread
"""

import json
import logging
import subprocess
import threading
import time
from datetime import datetime
from queue import Full
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional

from .ack_tracker import AckTracker, routing_error_to_drop_reason
from .base_handler import (
    BaseMessageHandler, dual_path_dedup_enabled, dual_path_dedup_window_s,
    get_rf_tx_registry,
)
from utils.meshtastic_se_crypto import (
    DEFAULT_KEY_B64, crypto_available, decode_service_envelope,
)
from utils.safe_import import safe_import

logger = logging.getLogger(__name__)


class _LazyDeliveryCounters:
    """Deferred delivery_counters import (mirrors message_queue's proxy).

    A module-level ``from gateway import delivery_counters`` here deadlocks
    the daemon's threaded startup (gateway/__init__ eagerly imports the
    package, so touching the parent from a submodule mid-import creates a
    cross-thread _ModuleLock cycle). First attribute access imports + caches.
    """

    def __getattr__(self, name):
        from gateway import delivery_counters as mod
        globals()["_dc"] = mod
        return getattr(mod, name)


_dc = _LazyDeliveryCounters()

# Optional MQTT client
_mqtt_mod, _HAS_PAHO_MQTT = safe_import('paho.mqtt.client')

# Optional protobuf client
_get_protobuf_client, _HAS_PROTOBUF_CLIENT = safe_import(
    '.meshtastic_protobuf_client', 'get_protobuf_client', package='gateway',
)

# Sudo-safe home directory — first-party, always available (MF001)
from utils.paths import get_real_user_home as _get_real_user_home_fn
from utils.service_check import check_service as _check_service

if TYPE_CHECKING:
    from .bridge_health import BridgeHealthMonitor
    from .config import GatewayConfig
    from .node_tracker import UnifiedNodeTracker


class MQTTBridgeHandler(BaseMessageHandler):
    """
    MQTT-based Meshtastic handler for the gateway bridge.

    Subscribes to meshtasticd's MQTT topics to receive mesh traffic.
    Uses meshtastic CLI for sending messages (transient, no interference).

    This replaces the TCP-based MeshtasticHandler that held a persistent
    connection to port 4403, blocking the web client.

    Args:
        config: Gateway configuration object
        node_tracker: Unified node tracker instance
        health: Bridge health monitor instance
        stop_event: Threading event for graceful shutdown
        stats: Shared statistics dictionary
        stats_lock: Lock for thread-safe stats updates
        message_queue: Queue for messages to be bridged to RNS
        message_callback: Callback for received messages
        status_callback: Callback for status changes
        should_bridge: Callback to check routing rules
    """

    def __init__(
        self,
        config: 'GatewayConfig',
        node_tracker: 'UnifiedNodeTracker',
        health: 'BridgeHealthMonitor',
        stop_event: threading.Event,
        stats: Dict[str, Any],
        stats_lock: threading.Lock,
        message_queue,
        message_callback: Optional[Callable] = None,
        status_callback: Optional[Callable] = None,
        should_bridge: Optional[Callable] = None,
        load_balancer=None,
    ):
        super().__init__(
            config=config,
            node_tracker=node_tracker,
            health=health,
            stop_event=stop_event,
            stats=stats,
            stats_lock=stats_lock,
            message_queue=message_queue,
            message_callback=message_callback,
            status_callback=status_callback,
            should_bridge=should_bridge,
        )

        # TX load balancer (optional, for dual-radio setups)
        self._load_balancer = load_balancer

        # MQTT client (handler-specific)
        self._client = None
        self._mqtt_lock = threading.Lock()

        # Meshtastic CLI path (cached)
        self._cli_path: Optional[str] = None

        # Deduplication: track recent message IDs to avoid loops
        self._recent_ids: Dict[str, float] = {}
        self._dedup_window = 60  # seconds

        # Honest Meshtastic delivery confirmation (#74, ported from MeshForge
        # 204da9e) via the /e/ ServiceEnvelope MQTT topic. The gateway already
        # TXes wantAck DMs (send_text_direct default) and already subscribes to
        # /2/e/; the recipient's ROUTING_APP ACK rides /e/ encrypted, so we
        # decode it there — staying within MQTT, NO fromradio read (#17/#75
        # preserved). In-flight packet_id->msg_id map → CONFIRMED / DROPPED.
        self._ack_tracker = AckTracker(
            ttl_sec=getattr(config.rns, 'ack_pending_ttl_sec', None),
            max_pending=getattr(config.rns, 'ack_pending_max', None),
        )
        if getattr(config.rns, 'meshtastic_ack_consumption_enabled', False):
            if crypto_available():
                logger.info(
                    "Meshtastic ACK consumption ACTIVE (mqtt_bridge / #74): "
                    "decoding ROUTING_APP from the /e/ ServiceEnvelope topic "
                    "to confirm wantAck DMs — no fromradio read.")
            else:
                logger.warning(
                    "rns.meshtastic_ack_consumption_enabled is set but the "
                    "cryptography/meshtastic-protobuf deps needed to decode "
                    "the /e/ ServiceEnvelope topic are unavailable — ACK "
                    "consumption is INERT (delivery stays 'Sent, not "
                    "guaranteed'). Install requirements to enable.")

    # --- Thread-2 step 4: ACK consumption via /e/ (ported from MeshForge) ---

    @property
    def ack_tracker(self) -> AckTracker:
        """In-flight Meshtastic DM ACK tracker."""
        return self._ack_tracker

    def _ack_consumption_enabled(self) -> bool:
        try:
            return bool(getattr(
                self.config.rns, 'meshtastic_ack_consumption_enabled', False))
        except Exception:
            return False

    def _channel_keys(self) -> List[str]:
        """Base64 channel PSKs to try when decrypting /e/ packets: the default
        LongFast key + downlink_psk + any configured channel_keys."""
        keys = [DEFAULT_KEY_B64]
        psk = getattr(self.config.meshtastic, 'downlink_psk', '') or ''
        if isinstance(psk, str) and psk and psk not in keys:
            keys.append(psk)
        for k in getattr(self.config.meshtastic, 'channel_keys', None) or []:
            if isinstance(k, str) and k and k not in keys:
                keys.append(k)
        return keys

    def _maybe_register_ack(self, packet_id, dest_num, msg_id,
                            record_sent: bool) -> None:
        """Arm ACK confirmation for a just-sent DM. No-op for broadcasts and
        when disabled. Direct path synthesizes an id + records SENT; queue
        path passes _queue_msg_id (SENT owned by mark_delivered)."""
        try:
            if not self._ack_consumption_enabled():
                return
            if (dest_num is None or dest_num == 0xFFFFFFFF
                    or not isinstance(packet_id, int) or packet_id <= 0):
                return
            if not msg_id:
                msg_id = f"mesh-{packet_id:08x}"
            if record_sent:
                _dc.record(_dc.DeliveryState.SENT, msg_id=msg_id,
                           protocol="meshtastic")
            self._ack_tracker.register(packet_id, msg_id, protocol="meshtastic")
        except Exception as e:
            logger.debug(f"Could not arm ACK tracking: {e}")

    def _handle_routing_envelope(self, dp) -> None:
        """A decoded ROUTING_APP /e/ packet → CONFIRMED / DROPPED if it
        matches an in-flight DM. Never raises into the MQTT loop."""
        try:
            resolved = self._ack_tracker.resolve(dp.request_id)
            if resolved is None:
                return
            msg_id, protocol = resolved
            err = dp.routing_error_name()
            if err in (None, "", "NONE"):
                _dc.record(_dc.DeliveryState.CONFIRMED, msg_id=msg_id,
                           protocol=protocol)
                with self._stats_lock:
                    self.stats['mesh_ack_confirmed'] = (
                        self.stats.get('mesh_ack_confirmed', 0) + 1)
                logger.info(
                    f"Meshtastic ACK confirmed delivery of {msg_id} "
                    f"(pkt={dp.request_id:#0x}, via /e/)")
            else:
                reason = routing_error_to_drop_reason(err)
                _dc.record(_dc.DeliveryState.DROPPED, msg_id=msg_id,
                           protocol=protocol, drop_reason=reason,
                           note=f"meshtastic_nak:{err}"[:80])
                with self._stats_lock:
                    self.stats['mesh_ack_failed'] = (
                        self.stats.get('mesh_ack_failed', 0) + 1)
                logger.warning(
                    f"Meshtastic NAK for {msg_id} (pkt={dp.request_id:#0x}): "
                    f"{err} -> {reason.value}")
        except Exception as e:
            logger.debug(f"Error handling /e/ routing envelope: {e}")

    def run_loop(self) -> None:
        """
        Main loop: connect to MQTT and process messages.

        Blocks until stop_event is set. Handles reconnection automatically.
        """
        while not self._stop_event.is_set():
            try:
                if not self._connected:
                    logger.info("Connecting to MQTT broker for gateway bridge...")
                    self._connect()

                    if self._connected:
                        self.health.record_connection_event("meshtastic", "connected")
                        logger.info("MQTT bridge handler connected")
                        self._notify_status("meshtastic_connected")
                    else:
                        self.health.record_connection_event("meshtastic", "retry")
                        self._stop_event.wait(5)
                        continue

                # MQTT client has its own event loop via loop_start()
                # We just need to stay alive and do periodic maintenance
                self._cleanup_dedup()
                self._stop_event.wait(1)

            except Exception as e:
                self.health.record_error("meshtastic", e)
                logger.error(f"MQTT bridge loop error: {e}")
                self._connected = False
                self.health.record_connection_event("meshtastic", "error", str(e))
                self._stop_event.wait(5)

    def connect(self) -> bool:
        """Connect to MQTT broker (ABC contract)."""
        return self._connect()

    def _connect(self) -> bool:
        """Connect to MQTT broker and subscribe to meshtasticd topics."""
        if not _HAS_PAHO_MQTT:
            logger.error("paho-mqtt not installed. Install with: pip install paho-mqtt")
            return False

        # Pre-flight: verify MQTT broker is running
        mqtt_cfg = self.config.mqtt_bridge
        if mqtt_cfg.broker in ('localhost', '127.0.0.1', '::1'):
            broker_status = _check_service('mosquitto')
            if not broker_status.available:
                logger.warning("mosquitto service check: %s (attempting connection anyway)",
                               broker_status.message)
                if broker_status.fix_hint:
                    logger.info("Fix: %s", broker_status.fix_hint)
                # Continue — mosquitto may be running outside systemd

        mqtt = _mqtt_mod

        try:
            # Create MQTT client
            client_id = f"meshanchor-gateway-{int(time.time()) % 10000}"
            self._client = mqtt.Client(
                client_id=client_id,
                protocol=mqtt.MQTTv311,
            )

            # Auth if configured
            if mqtt_cfg.username:
                self._client.username_pw_set(mqtt_cfg.username, mqtt_cfg.password)

            # TLS if configured
            if mqtt_cfg.use_tls:
                self._client.tls_set()

            # Callbacks
            self._client.on_connect = self._on_connect
            self._client.on_disconnect = self._on_disconnect
            self._client.on_message = self._on_message

            # Connect
            self._client.connect(
                mqtt_cfg.broker,
                mqtt_cfg.port,
                keepalive=60,
            )

            # Start background thread for MQTT event loop
            self._client.loop_start()

            # Wait briefly for connection
            for _ in range(50):
                if self._connected:
                    return True
                if self._stop_event.wait(0.1):
                    return False

            if not self._connected:
                logger.warning("MQTT connection timed out")
                return False

            return True

        except Exception as e:
            logger.error(f"Failed to connect to MQTT broker: {e}")
            self._connected = False
            return False

    def _on_connect(self, client, userdata, flags, rc):
        """MQTT connect callback - subscribe to meshtasticd topics."""
        if rc == 0:
            self._connected = True
            mqtt_cfg = self.config.mqtt_bridge

            # Subscribe to JSON topics (human-readable, recommended)
            # Topic format: msh/{REGION}/2/json/{CHANNEL}/{NODE_ID}
            if mqtt_cfg.json_enabled:
                json_topic = f"{mqtt_cfg.root_topic}/{mqtt_cfg.region}/2/json/{mqtt_cfg.channel}/#"
                client.subscribe(json_topic)
                logger.debug(f"Subscribed to JSON topic: {json_topic}")

            # Also subscribe to protobuf topics for completeness
            # Topic format: msh/{REGION}/2/e/{CHANNEL}/{NODE_ID}
            proto_topic = f"{mqtt_cfg.root_topic}/{mqtt_cfg.region}/2/e/{mqtt_cfg.channel}/#"
            client.subscribe(proto_topic)
            logger.debug(f"Subscribed to protobuf topic: {proto_topic}")

            logger.info(f"MQTT bridge connected to {mqtt_cfg.broker}:{mqtt_cfg.port}")
        else:
            logger.error(f"MQTT connection failed with code {rc}")
            self._connected = False

    def _on_disconnect(self, client, userdata, rc):
        """MQTT disconnect callback."""
        was_connected = self._connected
        self._connected = False
        if was_connected:
            if rc == 0:
                logger.info("MQTT bridge disconnected cleanly")
            else:
                logger.warning(f"MQTT bridge disconnected unexpectedly (rc={rc})")
                self.health.record_connection_event("meshtastic", "disconnected", f"rc={rc}")
            self._notify_status("meshtastic_disconnected")

    def _on_message(self, client, userdata, msg):
        """Handle incoming MQTT message from meshtasticd."""
        try:
            topic = msg.topic
            payload = msg.payload

            # Determine if JSON or protobuf based on topic
            if "/json/" in topic:
                self._handle_json_message(topic, payload)
            else:
                # Protobuf messages need decoding - skip for now,
                # JSON mode is the recommended path
                self._handle_protobuf_message(topic, payload)

        except Exception as e:
            logger.error(f"Error processing MQTT message: {e}")

    def _handle_json_message(self, topic: str, payload: bytes) -> None:
        """
        Handle JSON-encoded message from meshtasticd MQTT.

        JSON messages have this structure:
        {
            "channel": 0,
            "from": 1234567890,
            "id": 12345678,
            "payload": {"text": "Hello"},
            "sender": "!abcd1234",
            "timestamp": 1234567890,
            "to": 4294967295,
            "type": "text"
        }
        """
        try:
            data = json.loads(payload.decode('utf-8', errors='ignore'))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            logger.debug(f"Failed to parse MQTT JSON: {e}")
            return

        msg_type = data.get('type', '')
        sender = data.get('sender', '')
        msg_id = str(data.get('id', ''))

        # Dedup check
        if msg_id and self._is_duplicate(msg_id):
            return

        # Update node tracking
        from_num = data.get('from', 0)
        if from_num:
            self._update_node_from_mqtt(data)

        # Handle text messages for bridging
        if msg_type == 'text':
            self._bridge_text_message(data, topic)

        # Handle telemetry for node tracking
        elif msg_type == 'telemetry':
            self._update_telemetry(data)

        # Handle position for maps
        elif msg_type == 'position':
            self._update_position(data)

        # Handle nodeinfo for discovery
        elif msg_type == 'nodeinfo':
            self._update_nodeinfo(data)

    def _handle_protobuf_message(self, topic: str, payload: bytes) -> None:
        """Handle a protobuf ServiceEnvelope (/e/) from meshtasticd MQTT.

        Thread-2 step 4 (#74): the /e/ topic carries every MeshPacket the
        radio hears, including the ROUTING_APP ACK for a wantAck DM we sent.
        We decode it (channel-decrypt — no fromradio read) to confirm delivery.

        Cost-bounded: only decode when ACK consumption is enabled AND at least
        one DM is awaiting its ACK — so a quiet/disabled gateway pays nothing.
        Bridging of mesh TEXT still goes via the /json/ path."""
        if not self._ack_consumption_enabled():
            return
        try:
            if self._ack_tracker.pending_count() == 0:
                return
            dp = decode_service_envelope(payload, self._channel_keys())
            if dp is None or not dp.is_routing or dp.request_id <= 0:
                return
            self._handle_routing_envelope(dp)
        except Exception as e:
            logger.debug(f"Protobuf /e/ decode failed on {topic}: {e}")

    def _bridge_text_message(self, data: dict, topic: str) -> None:
        """Bridge a text message from Meshtastic to RNS."""
        from .rns_bridge import BridgedMessage
        from .bridge_health import MessageOrigin

        sender = data.get('sender', '')
        to_num = data.get('to', 0)
        payload = data.get('payload', {})
        text = payload.get('text', '') if isinstance(payload, dict) else str(payload)
        channel = data.get('channel', 0)

        if not text:
            return

        # Determine destination
        to_id = f"!{to_num:08x}" if to_num else None
        is_broadcast = to_num == 0xFFFFFFFF

        # Seen-on-RF registration (mirror of MeshForge b645fa7, cross-BOX
        # dedup direction): a broadcast heard here IS on this radio's mesh,
        # whoever TX'd it — including another box's radio on the same RF
        # segment, which this box's own TX bookkeeping can never see.
        # Registering at RX lets the inject-side checks suppress the relay
        # copy of the same content. Tagged content registers too (it is
        # refused for re-bridging downstream but is still on the mesh).
        # Registration unconditional/cheap; suppression flag-gated.
        if is_broadcast:
            get_rf_tx_registry().register(text)

        msg = BridgedMessage(
            source_network="meshtastic",
            source_id=sender,
            destination_id=to_id,
            content=text,
            is_broadcast=is_broadcast,
            origin=MessageOrigin.MQTT,
            via_internet=False,  # Local MQTT, not internet relay
            metadata={
                'channel': channel,
                'mqtt_topic': topic,
                'msg_id': data.get('id'),
                'timestamp': data.get('timestamp'),
            },
        )

        # Store incoming message for UI/history
        try:
            from commands import messaging
            dest = None if is_broadcast else to_id
            messaging.store_incoming(
                from_id=sender,
                content=text,
                network="meshtastic",
                to_id=dest,
                channel=channel,
            )
        except Exception as e:
            logger.debug(f"Could not store incoming message: {e}")

        # Queue for bridging if routing rules allow
        if self._message_queue is not None:
            if self._should_bridge and not self._should_bridge(msg):
                logger.debug(f"Message from {sender} blocked by routing rules")
            else:
                try:
                    self._message_queue.put_nowait(msg)
                except Full:
                    logger.warning("Mesh->RNS queue full, dropping message")
                    with self._stats_lock:
                        self.stats['errors'] += 1

        # Notify callback
        if self._message_callback:
            try:
                self._message_callback(msg)
            except Exception as e:
                logger.error(f"Message callback error: {e}")

        # Emit to event bus for TUI live feed (Issue #17 Phase 3)
        try:
            from utils.event_bus import emit_message
            emit_message(
                direction='rx',
                content=text,
                node_id=sender,
                channel=channel,
                network='meshtastic',
                raw_data={
                    'to_id': to_id,
                    'is_broadcast': is_broadcast,
                    'mqtt_topic': topic,
                    'msg_id': data.get('id'),
                    'timestamp': data.get('timestamp'),
                }
            )
        except Exception as e:
            logger.debug(f"Event bus emit failed: {e}")

    @staticmethod
    def _originator_id(data: dict) -> str:
        """Node ID of the packet's ORIGINATOR, not its MQTT uplinker.

        `sender` is the gateway radio that published the packet to MQTT —
        on a localhost broker that is ALWAYS this box's own radio, so
        keying node-tracker updates on it wrote every heard node's
        nodeinfo/position onto the gateway's own node id (names churned
        to whichever node was heard last; positions teleported between
        sites). Same sender-vs-from class as MeshForge's 9554f06
        text-attribution fix; parity port of MeshForge 0ce2a65. `from`
        is the originating node; fall back to `sender` only when `from`
        is absent.
        """
        from_num = data.get('from', 0)
        if from_num:
            return f"!{from_num:08x}"
        return data.get('sender', '')

    def _update_node_from_mqtt(self, data: dict) -> None:
        """Update node tracker from MQTT message data."""
        try:
            from .node_tracker import UnifiedNode

            node_id = self._originator_id(data)
            if not node_id:
                return

            node = UnifiedNode(
                id=node_id,
                name=node_id,
                network="meshtastic",
                meshtastic_id=node_id,
            )
            self.node_tracker.add_node(node)
        except Exception as e:
            logger.debug(f"Error updating node from MQTT: {e}")

    def _update_telemetry(self, data: dict) -> None:
        """Update node with telemetry data from MQTT."""
        try:
            node_id = self._originator_id(data)
            payload = data.get('payload', {})
            if not isinstance(payload, dict) or not node_id:
                return

            # Device metrics
            device = payload.get('device_metrics', {})
            if device:
                logger.debug(f"Telemetry from {node_id}: "
                            f"battery={device.get('battery_level')}%, "
                            f"chUtil={device.get('channel_utilization')}%")

            # Environment metrics
            env = payload.get('environment_metrics', {})
            if env:
                logger.debug(f"Environment from {node_id}: "
                            f"temp={env.get('temperature')}C, "
                            f"humidity={env.get('relative_humidity')}%")
        except Exception as e:
            logger.debug(f"Error processing telemetry: {e}")

    def _update_position(self, data: dict) -> None:
        """Update node position from MQTT for maps."""
        try:
            node_id = self._originator_id(data)
            payload = data.get('payload', {})
            if not isinstance(payload, dict) or not node_id:
                return

            lat = payload.get('latitude_i', 0) / 1e7 if payload.get('latitude_i') else None
            lon = payload.get('longitude_i', 0) / 1e7 if payload.get('longitude_i') else None
            alt = payload.get('altitude')

            if lat and lon:
                logger.debug(f"Position from {node_id}: {lat:.6f}, {lon:.6f}")
                # Node tracker update with position would go here
        except Exception as e:
            logger.debug(f"Error processing position: {e}")

    def _update_nodeinfo(self, data: dict) -> None:
        """Update node info from MQTT."""
        try:
            from .node_tracker import UnifiedNode

            node_id = self._originator_id(data)
            payload = data.get('payload', {})
            if not isinstance(payload, dict) or not node_id:
                return

            long_name = payload.get('longname', '')
            short_name = payload.get('shortname', '')
            hw_model = payload.get('hardware', '')

            node = UnifiedNode(
                id=node_id,
                name=long_name or short_name or node_id,
                network="meshtastic",
                meshtastic_id=node_id,
            )
            self.node_tracker.add_node(node)
            logger.debug(f"NodeInfo from {node_id}: {long_name} ({short_name})")
        except Exception as e:
            logger.debug(f"Error processing nodeinfo: {e}")

    def send_text(self, message: str, destination: str = None, channel: int = 0,
                  msg_id: Optional[str] = None, record_sent: bool = True) -> bool:
        """
        Send a text message to Meshtastic network.

        Primary: HTTP protobuf via /api/v1/toradio (no TCP, no subprocess).
        Fallback: meshtastic CLI (transient subprocess).

        Args:
            message: Text content to send
            destination: Destination node ID (None for broadcast)
            channel: Channel index to send on
            msg_id: delivery_counters id to confirm against the ROUTING_APP
                ACK (queue path passes _queue_msg_id; direct path synthesizes)
            record_sent: record a SENT transition here (direct path); False
                when the persistent queue already owns the SENT (queue path)

        Returns:
            True if message sent successfully, False otherwise.
        """
        message = self._truncate_if_needed(message)

        # Try HTTP protobuf first (preferred — no TCP contention, no subprocess)
        if self._send_via_http_protobuf(message, destination, channel,
                                        msg_id=msg_id, record_sent=record_sent):
            # Dual-path dedup (mirror of MeshForge 2d205b7): this toradio
            # route is the destination="meshtastic" dispatch path — register
            # broadcast TX so the other paths to this radio can suppress
            # their duplicate copy. Registration unconditional/cheap;
            # suppression is flag-gated at the check side.
            if not destination:
                get_rf_tx_registry().register(message)
            return True

        # Fall back to CLI
        logger.debug("HTTP protobuf TX unavailable, falling back to CLI")
        if self._send_via_cli(message, destination, channel):
            if not destination:
                get_rf_tx_registry().register(message)
            return True
        return False

    def _send_via_http_protobuf(
        self, message: str, destination: str = None, channel: int = 0,
        msg_id: Optional[str] = None, record_sent: bool = True,
    ) -> bool:
        """Send text via HTTP protobuf transport (preferred TX path).

        Primary: Stateless direct POST to /api/v1/toradio — NEVER reads
        from /api/v1/fromradio, so the web client at :9443 is never
        starved of delivery ACK packets.

        Fallback: Session-based protobuf client (legacy, only if direct
        send fails).
        """
        # Convert hex node ID string to int (e.g. "!aabbccdd" -> 0xaabbccdd)
        dest_num = None
        if destination:
            dest_num = self._node_id_to_num(destination)

        # Primary: stateless direct send — zero fromradio contention
        try:
            from .meshtastic_protobuf_client import send_text_direct_with_id
            host = self.config.meshtastic.host

            # Use load balancer for port selection if available
            if self._load_balancer and self._load_balancer.state.value != "disabled":
                http_port = self._load_balancer.get_tx_port()
                logger.debug("TX load balancer selected port %d", http_port)
            else:
                http_port = getattr(self.config.meshtastic, 'http_port', 9443) or 9443

            # Capture the minted packet_id so a DM's ROUTING_APP ACK (which
            # rides /e/) can confirm it (Thread-2 step 4 / #74).
            pkt_id = send_text_direct_with_id(
                text=message, host=host, port=http_port,
                destination=dest_num, channel_index=channel)
            if pkt_id is not None:
                self._maybe_register_ack(pkt_id, dest_num, msg_id, record_sent)
                return True
        except Exception as e:
            logger.debug(f"Stateless HTTP protobuf TX failed: {e}")

        # Fallback: session-based send (reads fromradio during connect)
        # Skip fallback when load balancer selected a non-primary port — the
        # session client only connects to the primary radio and would bypass
        # the load balancer's port selection.
        if self._load_balancer and http_port != getattr(
            self.config.meshtastic, 'http_port', 9443
        ):
            logger.debug(
                "Skipping session fallback: load balancer selected port %d", http_port
            )
            return False

        if not _HAS_PROTOBUF_CLIENT:
            return False

        get_protobuf_client = _get_protobuf_client

        try:
            client = get_protobuf_client()

            if not client.is_connected:
                if not client.connect():
                    logger.debug("Protobuf client failed to connect for TX")
                    return False

            return client.send_text(
                text=message,
                destination=dest_num,
                channel_index=channel,
            )
        except Exception as e:
            logger.debug(f"Session-based HTTP protobuf TX failed: {e}")
            return False

    @staticmethod
    def _node_id_to_num(node_id: str) -> Optional[int]:
        """Convert a Meshtastic node ID string to numeric form.

        Args:
            node_id: Node ID like "!aabbccdd" or "0xaabbccdd" or decimal string

        Returns:
            Integer node number, or None if unparseable
        """
        if not node_id:
            return None
        try:
            cleaned = node_id.lstrip('!')
            return int(cleaned, 16)
        except ValueError:
            try:
                return int(node_id)
            except ValueError:
                logger.warning(f"Cannot parse node ID: {node_id}")
                return None

    def _send_via_cli(self, message: str, destination: str = None, channel: int = 0) -> bool:
        """Send text via meshtastic CLI (fallback TX path).

        Spawns a transient CLI process that connects via TCP, sends, exits.
        Works but slower and uses the TCP slot briefly.
        """
        cli = self._find_cli()
        if not cli:
            logger.error("meshtastic CLI not found. Install with: pip install meshtastic")
            return False

        try:
            host = self.config.meshtastic.host
            cmd = [cli, '--host', host, '--sendtext', message]

            if destination:
                cmd.extend(['--dest', destination])
            if channel > 0:
                cmd.extend(['--ch-index', str(channel)])

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30,
            )

            if result.returncode == 0:
                logger.info(f"Sent to Meshtastic via CLI: {message[:50]}...")
                return True
            else:
                logger.warning(f"CLI send failed (rc={result.returncode}): {result.stderr[:200]}")
                return False

        except subprocess.TimeoutExpired:
            logger.error("meshtastic CLI timed out")
            return False
        except FileNotFoundError:
            logger.error(f"meshtastic CLI not found at: {cli}")
            self._cli_path = None  # Reset cache
            return False
        except Exception as e:
            logger.error(f"CLI send failed: {e}")
            return False

    def queue_send(self, payload: Dict) -> bool:
        """
        Send handler for persistent queue - Meshtastic destination.

        Args:
            payload: Dictionary with 'message', 'destination', 'channel' keys

        Returns:
            True if sent successfully, False otherwise.
        """
        message = self._truncate_if_needed(payload.get('message', ''))
        destination = payload.get('destination')
        channel = payload.get('channel', 0)

        # Dispatch-time dual-path dedup re-check (gated, broadcast only) —
        # mirror of MeshForge 2d205b7. The enqueue-side check races the
        # other TX path's registration; by dispatch time (past the TX
        # pacing) the registry is settled, so re-checking here closes the
        # race deterministically. Suppress-only-on-hit: True marks the
        # queue entry done (the content IS on the radio, other path).
        if (not destination and dual_path_dedup_enabled(self.config)
                and get_rf_tx_registry().seen_within(
                    message, dual_path_dedup_window_s(self.config))):
            with self._stats_lock:
                self.stats['dispatch_dedup_suppressed'] = (
                    self.stats.get('dispatch_dedup_suppressed', 0) + 1)
            logger.info(
                f"Queue dispatch suppressed (dual-path dedup — already on "
                f"RF): {message[:50]}...")
            return True

        # Queue owns the SENT transition (mark_delivered on _queue_msg_id);
        # arm ACK confirmation against that same id (record_sent=False).
        return self.send_text(message, destination, channel,
                              msg_id=payload.get('_queue_msg_id'),
                              record_sent=False)

    def publish_to_mqtt(self, payload: Dict) -> bool:
        """
        Publish a message to the MQTT broker.

        Used as persistent queue sender callback for destination="mqtt".
        Publishes bridged messages (from RNS) to the Meshtastic MQTT
        topic so meshtasticd picks them up for radio transmission.

        Args:
            payload: Dictionary with 'message', 'channel', 'source_id' keys

        Returns:
            True if published successfully, False otherwise.
        """
        if not self._connected or not self._client:
            return False

        message = payload.get('message', '')
        channel = payload.get('channel', 0)
        source_id = payload.get('source_id', 'meshanchor')

        if not message:
            return False

        # Dispatch-time dual-path dedup re-check (gated) — mirror of
        # MeshForge 2d205b7, applied to MA's live R→M dispatch path
        # (destination="mqtt"; all sends here are broadcast). The
        # enqueue-side check races the mesh_bridge registration; by
        # dispatch time the registry is settled. Suppress-only-on-hit.
        if (dual_path_dedup_enabled(self.config)
                and get_rf_tx_registry().seen_within(
                    message, dual_path_dedup_window_s(self.config))):
            with self._stats_lock:
                self.stats['dispatch_dedup_suppressed'] = (
                    self.stats.get('dispatch_dedup_suppressed', 0) + 1)
            logger.info(
                f"MQTT dispatch suppressed (dual-path dedup — already on "
                f"RF): {message[:50]}...")
            return True

        mqtt_cfg = self.config.mqtt_bridge

        # Build JSON payload matching meshtasticd format
        mqtt_payload = json.dumps({
            "from": 0,
            "payload": {"text": message},
            "sender": source_id,
            "type": "text",
            "channel": channel,
        })

        # Publish to the JSON topic
        topic = (f"{mqtt_cfg.root_topic}/{mqtt_cfg.region}/2/json/"
                 f"{mqtt_cfg.channel}/meshanchor")

        try:
            with self._mqtt_lock:
                result = self._client.publish(topic, mqtt_payload, qos=1)
            if result.rc == 0:
                logger.info(f"Published to MQTT: {message[:50]}...")
                # Dual-path dedup (mirror of MeshForge f02ad82): this is the
                # live R→M TX path in mqtt_bridge mode (destination="mqtt"
                # queue items end here) and it terminates at the same radio —
                # register so the mesh_bridge forward of the same content
                # can suppress its duplicate. All sends here are broadcast.
                get_rf_tx_registry().register(message)
                return True
            else:
                logger.warning(f"MQTT publish failed with rc={result.rc}")
                return False
        except Exception as e:
            logger.error(f"MQTT publish error: {e}")
            return False

    def test_connection(self) -> bool:
        """Test MQTT broker connectivity."""
        import socket
        mqtt_cfg = self.config.mqtt_bridge
        sock = None
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(5)
            result = sock.connect_ex((mqtt_cfg.broker, mqtt_cfg.port))
            return result == 0
        except (OSError, Exception) as e:
            logger.debug(f"MQTT broker connection test failed: {e}")
            return False
        finally:
            if sock:
                try:
                    sock.close()
                except Exception:
                    pass

    def disconnect(self) -> None:
        """Disconnect from MQTT broker."""
        if self._client:
            try:
                self._client.loop_stop()
                self._client.disconnect()
            except Exception as e:
                logger.debug(f"Error disconnecting MQTT: {e}")
        self._connected = False

    def _find_cli(self) -> Optional[str]:
        """Find meshtastic CLI binary (cached)."""
        if self._cli_path:
            return self._cli_path

        import shutil
        path = shutil.which('meshtastic')
        if path:
            self._cli_path = path
            return path

        # Check common locations
        for candidate in [
            '/usr/local/bin/meshtastic',
            '/usr/bin/meshtastic',
            str(self._get_user_bin() / 'meshtastic'),
        ]:
            if self._path_exists(candidate):
                self._cli_path = candidate
                return candidate

        return None

    def _get_user_bin(self):
        """Get user's local bin directory."""
        return _get_real_user_home_fn() / '.local' / 'bin'

    @staticmethod
    def _path_exists(path: str) -> bool:
        """Check if a file exists at path."""
        import os
        return os.path.isfile(path) and os.access(path, os.X_OK)

    def _is_duplicate(self, msg_id: str) -> bool:
        """Check if message ID was seen recently (dedup)."""
        now = time.time()
        with self._mqtt_lock:
            if msg_id in self._recent_ids:
                return True
            self._recent_ids[msg_id] = now
        return False

    def _cleanup_dedup(self) -> None:
        """Remove expired entries from dedup cache."""
        now = time.time()
        with self._mqtt_lock:
            expired = [
                k for k, v in self._recent_ids.items()
                if now - v > self._dedup_window
            ]
            for k in expired:
                del self._recent_ids[k]

