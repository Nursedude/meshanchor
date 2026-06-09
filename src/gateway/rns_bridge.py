"""
RNS-Meshtastic Bridge Service
Bridges Reticulum Network Stack and Meshtastic networks

MeshCore bridge processing extracted to meshcore_bridge_mixin.py.
RNS/LXMF connection lifecycle extracted to _rns_bridge_connection.py.
ACK synthesis, subsystem-state/circuit-breaker, and send/queue surfaces
extracted to bridge_ack_mixin.py / bridge_health_mixin.py /
bridge_send_mixin.py (2026-06-09 split, MeshForge parity).
"""

import threading
import logging
from queue import Queue, Empty, Full
from datetime import datetime
from typing import Optional, Callable, Dict, Any
from dataclasses import dataclass

from .base_handler import chunk_for_mesh, get_rf_tx_registry
from .config import GatewayConfig
from .node_tracker import UnifiedNodeTracker, UnifiedNode
from .reconnect import ReconnectStrategy
from .bridge_health import (
    BridgeHealthMonitor, DeliveryTracker,
    BridgeStatus, SubsystemState, MessageOrigin
)

from utils.safe_import import safe_import

# MQTT bridge handler (zero-interference, recommended)
MQTTBridgeHandler, HAS_MQTT_BRIDGE = safe_import(
    '.mqtt_bridge_handler', 'MQTTBridgeHandler', package=__package__
)

# TCP-based handler (legacy, requires meshtastic Python library)
MeshtasticHandler, HAS_MESHTASTIC_LIB = safe_import(
    '.meshtastic_handler', 'MeshtasticHandler', package=__package__
)

# MeshCore handler (companion radio via meshcore_py)
MeshCoreHandler, HAS_MESHCORE = safe_import(
    '.meshcore_handler', 'MeshCoreHandler', package=__package__
)

# Import circuit breaker for destination-level failure handling
CircuitBreakerRegistry, HAS_CIRCUIT_BREAKER = safe_import(
    '.circuit_breaker', 'CircuitBreakerRegistry', package=__package__
)

# TX load balancer and failover manager for dual-radio gateways
RadioLoadBalancer, LoadBalancerConfig, HAS_LOAD_BALANCER = safe_import(
    '.radio_failover', 'RadioLoadBalancer', 'LoadBalancerConfig', package=__package__
)
FailoverManager, FailoverConfig, HAS_FAILOVER = safe_import(
    '.radio_failover', 'FailoverManager', 'FailoverConfig', package=__package__
)

# Cross-gateway failover via MQTT heartbeat
GatewayHeartbeat, HeartbeatConfig, HAS_HEARTBEAT = safe_import(
    '.gateway_heartbeat', 'GatewayHeartbeat', 'HeartbeatConfig', package=__package__
)

# Import persistent message queue for reliable delivery
PersistentMessageQueue, MessagePriority, HAS_PERSISTENT_QUEUE = safe_import(
    '.message_queue', 'PersistentMessageQueue', 'MessagePriority', package=__package__
)

from .message_routing import MessageRouter
from .meshcore_bridge_mixin import MeshCoreBridgeMixin
from ._rns_bridge_connection import RNSConnectionMixin
from .bridge_ack_mixin import BridgeAckMixin
from .bridge_health_mixin import BridgeHealthMixin
from .bridge_send_mixin import BridgeSendMixin

logger = logging.getLogger(__name__)

# Minimum seconds between consecutive queue dispatches to meshtasticd.
# Firmware 2.7.x rate-limits API text broadcasts (Routing.Error
# RATE_LIMIT_EXCEEDED = 38) while the toradio HTTP hand-off still returns
# 200 — bursts (e.g. multi-chunk RNS→Mesh sends ~45ms apart) silently lose
# every packet after the first on RF (MeshForge live find, 2026-06-04).
# Organic sends 2-4s apart pass; 3s clears the limiter with margin.
MESHTASTIC_TX_MIN_SPACING_S = 3.0

# Centralized path utility — used by RNSConnectionMixin in _rns_bridge_connection.py
# NO FALLBACK: stale fallback copies caused config divergence bugs (Issue #25+)

# Import service checker for pre-flight checks (Issue #3)
from utils.service_check import check_service, ServiceState
HAS_SERVICE_CHECK = True

# Import event bus for RX message notifications (Issue #17 Phase 3)
from utils.event_bus import emit_message, emit_tactical
HAS_EVENT_BUS = True

# RNS sniffer is optional monitoring — not required for message bridging
try:
    from monitoring.rns_sniffer import (
        get_rns_sniffer, RNSPacketInfo, RNSPacketType,
        start_rns_capture, integrate_with_traffic_inspector
    )
    HAS_RNS_SNIFFER = True
except ImportError:
    HAS_RNS_SNIFFER = False

# Import RNS and LXMF modules (optional - for mesh bridge)
_RNS_mod, _HAS_RNS = safe_import('RNS')
_LXMF_mod, _HAS_LXMF = safe_import('LXMF')

# Config drift detection used by RNSConnectionMixin in _rns_bridge_connection.py

# WebSocket is optional — used for web UI push, not core bridging
try:
    from utils.websocket_server import (
        start_websocket_server, is_websocket_available, stop_websocket_server
    )
    HAS_WEBSOCKET = True
except ImportError:
    HAS_WEBSOCKET = False


@dataclass
class BridgedMessage:
    """Represents a message being bridged between networks"""
    source_network: str  # "meshtastic" or "rns"
    source_id: str
    destination_id: Optional[str]
    content: str
    title: Optional[str] = None
    timestamp: datetime = None
    is_broadcast: bool = False
    metadata: dict = None
    origin: MessageOrigin = MessageOrigin.UNKNOWN
    via_internet: bool = False  # True if message came through MQTT/internet

    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.now()
        if self.metadata is None:
            self.metadata = {}

    def should_bridge(self, filter_mqtt: bool = False) -> bool:
        """
        Check if this message should be bridged.

        Args:
            filter_mqtt: If True, drop MQTT-originated messages.
                        Useful for pure radio mesh networks.

        Returns:
            True if message should be bridged to other network.
        """
        if filter_mqtt and self.via_internet:
            return False
        if filter_mqtt and self.origin == MessageOrigin.MQTT:
            return False
        return True


class RNSMeshtasticBridge(RNSConnectionMixin, MeshCoreBridgeMixin,
                          BridgeAckMixin, BridgeHealthMixin,
                          BridgeSendMixin):
    """
    Main gateway bridge between RNS, Meshtastic, and MeshCore networks.

    Supports multiple modes:
    1. RNS Over Meshtastic - Uses Meshtastic as RNS transport layer
    2. Message Bridge - Translates messages between separate networks
    3. MeshCore Bridge - Bridges MeshCore companion radio with other protocols
    4. Tri-Bridge - All three protocols (Meshtastic + MeshCore + RNS)

    MeshCore bridge processing methods inherited from MeshCoreBridgeMixin.
    ACK synthesis from BridgeAckMixin, subsystem-state/circuit-breaker
    surface from BridgeHealthMixin, send/queue surface from
    BridgeSendMixin (2026-06-09 split, MeshForge parity).
    """

    def __init__(self, config: Optional[GatewayConfig] = None):
        self.config = config or GatewayConfig.load()
        self.node_tracker = UnifiedNodeTracker()

        # State
        self._running = False
        self._websocket_started = False
        self._connected_rns = False
        self._rns_via_rnsd = False  # True when rnsd handles RNS (bridge defers)
        self._rns_init_failed_permanently = False  # True if RNS can't be initialized from this thread
        self._rns_pre_initialized = False  # True if RNS was initialized from main thread

        # Reconnection strategy for RNS (Meshtastic reconnect is in handler)
        self._rns_reconnect = ReconnectStrategy.for_rns()
        self._stop_event = threading.Event()

        # Health monitoring
        self.health = BridgeHealthMonitor()

        # LXMF delivery confirmation tracking
        self.delivery_tracker = DeliveryTracker()

        # Message queues (bounded to prevent memory exhaustion)
        self._mesh_to_rns_queue = Queue(maxsize=1000)
        self._rns_to_mesh_queue = Queue(maxsize=1000)
        # MeshCore queues for 3-way routing
        self._meshcore_to_bridge_queue = Queue(maxsize=1000)
        self._bridge_to_meshcore_queue = Queue(maxsize=1000)

        # Threads
        self._mesh_thread = None
        self._rns_thread = None
        self._bridge_thread = None
        self._meshcore_thread = None

        # Callbacks (protected by _callbacks_lock for thread-safe registration)
        self._message_callbacks = []
        self._status_callbacks = []
        self._callbacks_lock = threading.Lock()

        # Thread-safe stats updates
        self._stats_lock = threading.Lock()

        # RNS components (lazy loaded)
        self._reticulum = None
        self._lxmf_router = None
        self._identity = None
        self._lxmf_source = None

        # Meshtastic handler (encapsulates connection and message handling)
        self._mesh_handler: Optional[MeshtasticHandler] = None

        # MeshCore handler (companion radio integration)
        self._meshcore_handler = None

        # LXMF broadcast bridge plug-in (instantiated after LXMF connects).
        # Owns its own LXMRouter — LXMF 0.9.4 caps a router at one
        # delivery identity, so we can't share the gateway's. The bridge
        # registers its own delivery callback on its own router; no hook
        # in _on_lxmf_receive is needed.
        self._lxmf_broadcast = None

        # LXMF → MeshCore re-emit bridge plug-in. Receives via the
        # gateway's primary LXMRouter (the _on_lxmf_receive hook below)
        # because the relevant DMs are addressed to the gateway identity,
        # not the broadcast identity. Closes the Meshtastic→MeshCore
        # asymmetry described in [[meshtastic-to-meshcore-bridge-gap]].
        self._meshtastic_reemit = None

        # Statistics
        self.stats = {
            'messages_mesh_to_rns': 0,
            'messages_rns_to_mesh': 0,
            # Dual-path dedup (gated): broadcast copies suppressed because
            # the local mesh_bridge already put the same content on RF.
            'rns_to_mesh_dual_path_suppressed': 0,
            'errors': 0,
            'bounced': 0,
            'start_time': None,
        }

        # Persistent message queue for reliable delivery
        # Note: Meshtastic sender registered after handler init below
        self._persistent_queue = None
        if HAS_PERSISTENT_QUEUE:
            try:
                self._persistent_queue = PersistentMessageQueue()
                # RNS sender registered here, Meshtastic sender after handler init
                self._persistent_queue.register_sender(
                    "rns", self._queue_send_rns
                )
                logger.info("Persistent message queue initialized")
            except Exception as e:
                logger.warning(f"Failed to initialize persistent queue: {e}")

        # Message routing and classification (extracted to message_routing.py)
        self._router = MessageRouter(self.config, self.stats, self._stats_lock)

        # Circuit breaker for destination-level failure handling
        self._circuit_breaker = None
        if HAS_CIRCUIT_BREAKER:
            self._circuit_breaker = CircuitBreakerRegistry(
                failure_threshold=5,
                recovery_timeout=60.0,
            )
            logger.info("Circuit breaker initialized for destination tracking")

        # MQTT filtering configuration
        self._filter_mqtt_messages = False  # Set True to drop MQTT-originated messages

        # Initialize Meshtastic handler based on bridge mode
        # MQTT bridge (recommended): zero interference with web client
        # TCP bridge (legacy): holds persistent connection, blocks web client

        # Initialize failover manager for dual-radio (crash detection + watchdog)
        self._failover_manager = None
        if HAS_FAILOVER and getattr(self.config.meshtastic, 'failover_enabled', False):
            fo_config = FailoverConfig(
                enabled=True,
                utilization_threshold=getattr(
                    self.config.meshtastic, 'failover_utilization_threshold', 25.0
                ),
                utilization_duration=getattr(
                    self.config.meshtastic, 'failover_utilization_duration', 30
                ),
                recovery_threshold=getattr(
                    self.config.meshtastic, 'failover_recovery_threshold', 15.0
                ),
                recovery_duration=getattr(
                    self.config.meshtastic, 'failover_recovery_duration', 60
                ),
                health_poll_interval=getattr(
                    self.config.meshtastic, 'failover_health_poll_interval', 5.0
                ),
                watchdog_enabled=getattr(
                    self.config.meshtastic, 'failover_watchdog_enabled', True
                ),
                restart_after_failures=getattr(
                    self.config.meshtastic, 'failover_restart_after_failures', 5
                ),
                max_restarts_per_hour=getattr(
                    self.config.meshtastic, 'failover_max_restarts_per_hour', 3
                ),
                restart_cooldown=getattr(
                    self.config.meshtastic, 'failover_restart_cooldown', 60
                ),
                primary_service=getattr(
                    self.config.meshtastic, 'failover_primary_service', 'meshtasticd'
                ),
                secondary_service=getattr(
                    self.config.meshtastic, 'failover_secondary_service', 'meshtasticd-alt'
                ),
            )
            self._failover_manager = FailoverManager(fo_config)
            self._failover_manager.start()
            logger.info("Radio failover manager enabled (watchdog=%s)",
                       "on" if fo_config.watchdog_enabled else "off")

        # Initialize TX load balancer if configured for dual-radio
        # Pass failover_manager so LB defers to failover state
        self._load_balancer = None
        if HAS_LOAD_BALANCER and getattr(self.config.meshtastic, 'load_balancer_enabled', False):
            lb_config = LoadBalancerConfig(
                enabled=True,
                tx_threshold=getattr(self.config.meshtastic, 'load_balancer_tx_threshold', 10.0),
                tx_max=getattr(self.config.meshtastic, 'load_balancer_tx_max', 20.0),
                health_poll_interval=getattr(
                    self.config.meshtastic, 'load_balancer_health_poll_interval', 5.0
                ),
                recovery_margin=getattr(
                    self.config.meshtastic, 'load_balancer_recovery_margin', 2.0
                ),
            )
            self._load_balancer = RadioLoadBalancer(
                lb_config,
                failover_manager=self._failover_manager,
            )
            self._load_balancer.start()
            logger.info("TX load balancer enabled (failover_aware=%s)",
                       "yes" if self._failover_manager else "no")

        # Initialize cross-gateway heartbeat if configured
        self._heartbeat = None
        if HAS_HEARTBEAT and getattr(self.config.meshtastic, 'gateway_heartbeat_enabled', False):
            hb_config = HeartbeatConfig(
                enabled=True,
                mqtt_broker=getattr(self.config.meshtastic, 'gateway_heartbeat_broker', 'localhost'),
                mqtt_port=getattr(self.config.meshtastic, 'gateway_heartbeat_port', 1883),
                heartbeat_interval=getattr(
                    self.config.meshtastic, 'gateway_heartbeat_interval', 15.0
                ),
                missed_heartbeats_threshold=getattr(
                    self.config.meshtastic, 'gateway_heartbeat_missed_threshold', 4
                ),
                role=getattr(self.config.meshtastic, 'gateway_role', 'primary'),
                gateway_id=getattr(self.config.meshtastic, 'gateway_id', ''),
            )
            self._heartbeat = GatewayHeartbeat(config=hb_config)
            self._heartbeat.start()
            logger.info("Cross-gateway heartbeat enabled (role=%s)", hb_config.role)

        meshtastic_enabled = getattr(self.config.meshtastic, 'enabled', True)
        if not meshtastic_enabled:
            logger.info(
                "Meshtastic handler disabled via config (meshtastic.enabled=false); "
                "gateway will operate without a Meshtastic peer"
            )
            self._mesh_handler = None
            self.health.set_subsystem_enabled("meshtastic", False)
        elif self.config.bridge_mode == "mqtt_bridge" and HAS_MQTT_BRIDGE:
            logger.info("Using MQTT bridge handler (zero-interference mode)")
            self._mesh_handler = MQTTBridgeHandler(
                config=self.config,
                node_tracker=self.node_tracker,
                health=self.health,
                stop_event=self._stop_event,
                stats=self.stats,
                stats_lock=self._stats_lock,
                message_queue=self._mesh_to_rns_queue,
                message_callback=self._notify_message,
                status_callback=lambda status: self._notify_status(status),
                should_bridge=self._router.should_bridge,
                load_balancer=self._load_balancer,
            )
        elif HAS_MESHTASTIC_LIB:
            if self.config.bridge_mode == "mqtt_bridge" and not HAS_MQTT_BRIDGE:
                logger.warning("MQTT bridge requested but paho-mqtt not available, "
                             "falling back to TCP handler")
            logger.info("Using TCP Meshtastic handler (legacy mode)")
            self._mesh_handler = MeshtasticHandler(
                config=self.config,
                node_tracker=self.node_tracker,
                health=self.health,
                stop_event=self._stop_event,
                stats=self.stats,
                stats_lock=self._stats_lock,
                message_queue=self._mesh_to_rns_queue,
                message_callback=self._notify_message,
                status_callback=lambda status: self._notify_status(status),
                should_bridge=self._router.should_bridge,
            )
        else:
            raise ImportError(
                "No Meshtastic handler available. Install paho-mqtt for MQTT bridge "
                "(recommended) or meshtastic Python library for legacy TCP bridge."
            )

        # Register Meshtastic sender now that handler exists.
        # min_spacing_s: meshtasticd 2.7.x NAKs burst API text broadcasts
        # with RATE_LIMIT_EXCEEDED (err=38) while the toradio HTTP hand-off
        # still returns 200 — a multi-chunk RNS→Mesh message dispatched
        # back-to-back silently loses every chunk after the first on RF
        # (MeshForge live find, 2026-06-04). 3s clears the limiter.
        if self._persistent_queue and self._mesh_handler:
            self._persistent_queue.register_sender(
                "meshtastic", self._mesh_handler.queue_send,
                min_spacing_s=MESHTASTIC_TX_MIN_SPACING_S,
            )

            # Register MQTT sender for persistent queue (mqtt_bridge mode).
            # Paced too: R→M chunks enqueue under destination="mqtt" in
            # mqtt_bridge mode (unlike MeshForge, which routes them under
            # "meshtastic"), and the broker hand-off ends at the same radio
            # TX queue — a burst published back-to-back hits the same
            # firmware rate limiter as a toradio burst.
            if (self.config.bridge_mode == "mqtt_bridge"
                    and hasattr(self._mesh_handler, 'publish_to_mqtt')):
                self._persistent_queue.register_sender(
                    "mqtt", self._mesh_handler.publish_to_mqtt,
                    min_spacing_s=MESHTASTIC_TX_MIN_SPACING_S,
                )
                logger.info("Registered 'mqtt' sender for persistent queue")

        # Initialize MeshCore handler if configured and available
        meshcore_config = getattr(self.config, 'meshcore', None)
        if HAS_MESHCORE and meshcore_config and meshcore_config.enabled:
            # If meshcore-radio.service is running, the supervisor owns the
            # serial port — opening it here would deadlock on EBUSY. Probe
            # the supervisor socket and pick the handler accordingly. The
            # bridge sees the same surface either way (chat buffer, queue
            # send, RX message_callback) so nothing else changes.
            from utils.meshcore_supervisor_client import is_supervisor_running
            if is_supervisor_running():
                from .meshcore_supervisor_handler import MeshCoreSupervisorHandler
                logger.info(
                    "MeshCore supervisor detected — bridge consumes via Unix socket"
                )
                handler_cls: Any = MeshCoreSupervisorHandler
            else:
                logger.info(
                    "MeshCore supervisor not running — bridge holds the radio in-process"
                )
                handler_cls = MeshCoreHandler
            self._meshcore_handler = handler_cls(
                config=self.config,
                node_tracker=self.node_tracker,
                health=self.health,
                stop_event=self._stop_event,
                stats=self.stats,
                stats_lock=self._stats_lock,
                message_queue=self._meshcore_to_bridge_queue,
                message_callback=self._notify_message,
                status_callback=lambda status: self._notify_status(status),
                should_bridge=self._router.should_bridge,
            )
            # Register MeshCore sender with persistent queue
            if self._persistent_queue:
                self._persistent_queue.register_sender(
                    "meshcore", self._meshcore_handler.queue_send
                )
            # Tell health monitor that MeshCore is enabled
            self.health.set_subsystem_enabled("meshcore", True)
            logger.info("MeshCore handler initialized")
        else:
            if meshcore_config and meshcore_config.enabled and not HAS_MESHCORE:
                logger.warning("MeshCore enabled in config but meshcore_handler not available")
            self.health.set_subsystem_enabled("meshcore", False)

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def is_connected(self) -> bool:
        return (self._mesh_handler and self._mesh_handler.is_connected) or self._connected_rns

    # Subsystem-state / circuit-breaker / status surface provided by
    # BridgeHealthMixin (bridge_health_mixin.py): bridge_status,
    # _update_subsystem_state, get_subsystem_state, is_fully_healthy,
    # can_send_to, record_send_success, record_send_failure,
    # get_open_circuits, get_status, test_connection,
    # _sync_subsystem_states.

    def set_filter_mqtt(self, enabled: bool) -> None:
        """
        Enable/disable MQTT message filtering.

        When enabled, messages that originated from MQTT/internet
        will not be bridged to the other network.

        Args:
            enabled: True to filter MQTT messages
        """
        self._filter_mqtt_messages = enabled
        logger.info(f"MQTT message filtering {'enabled' if enabled else 'disabled'}")

    def start(self) -> bool:
        """Start the gateway bridge"""
        if self._running:
            logger.warning("Bridge already running")
            return True

        # Issue #3: Pre-flight service check (skip when operator disabled Meshtastic)
        if HAS_SERVICE_CHECK and getattr(self.config.meshtastic, 'enabled', True):
            meshtasticd_status = check_service('meshtasticd')
            if not meshtasticd_status.available:
                logger.warning(f"meshtasticd not available: {meshtasticd_status.message}")
                logger.warning(f"Fix: {meshtasticd_status.fix_hint}")
                # Continue anyway - gateway can start in degraded mode
            else:
                logger.info("Pre-flight check: meshtasticd is running")

        logger.info("Starting RNS-Meshtastic bridge...")
        self._running = True
        self.stats['start_time'] = datetime.now()

        # Start WebSocket server for real-time message broadcast to web UI
        self._start_websocket_server()

        # Start node tracker
        self.node_tracker.start()

        # Pre-initialize RNS from main thread (signal handlers require it)
        # Must happen before spawning _rns_loop background thread
        self._init_rns_main_thread()

        # Start network threads
        if self.config.enabled:
            self._mesh_thread = threading.Thread(
                target=self._meshtastic_loop,
                daemon=True,
                name="MeshtasticBridge"
            )
            self._mesh_thread.start()

            self._rns_thread = threading.Thread(
                target=self._rns_loop,
                daemon=True,
                name="RNSBridge"
            )
            self._rns_thread.start()

            self._bridge_thread = threading.Thread(
                target=self._bridge_loop,
                daemon=True,
                name="MessageBridge"
            )
            self._bridge_thread.start()

        # Start MeshCore handler thread if initialized
        if self._meshcore_handler:
            self._meshcore_thread = threading.Thread(
                target=self._meshcore_loop,
                daemon=True,
                name="MeshCoreBridge"
            )
            self._meshcore_thread.start()
            logger.info("MeshCore handler thread started")

        # Start persistent queue processing
        if self._persistent_queue:
            self._persistent_queue.start_processing(interval=2.0)
            logger.info("Persistent message queue processing started")

        # Start RNS packet sniffer for Wireshark-grade traffic visibility
        if HAS_RNS_SNIFFER:
            try:
                start_rns_capture()
                integrate_with_traffic_inspector()
                logger.info("RNS packet sniffer started for traffic capture")
            except Exception as e:
                logger.warning(f"Could not start RNS sniffer: {e}")

        logger.info("Bridge started")
        self._notify_status("started")
        return True

    def stop(self):
        """Stop the gateway bridge"""
        if not self._running:
            return

        logger.info("Stopping bridge...")
        self._running = False
        self._stop_event.set()  # Wake any sleeping reconnect waits

        # Stop persistent queue processing
        if self._persistent_queue:
            self._persistent_queue.stop_processing()

        # Stop node tracker
        self.node_tracker.stop()

        # Stop LXMF broadcast bridge plug-in before tearing down RNS so
        # its announce thread exits while the router is still alive.
        if self._lxmf_broadcast:
            try:
                self._lxmf_broadcast.stop()
            except Exception as e:
                logger.debug(f"LXMF broadcast stop error: {e}")

        # Stop LXMF→MeshCore re-emit bridge — pure stateless hook, no
        # threads to join, but flips its is_running flag so any in-flight
        # _on_lxmf_receive call drops the message instead of dispatching.
        if self._meshtastic_reemit:
            try:
                self._meshtastic_reemit.stop()
            except Exception as e:
                logger.debug(f"meshtastic_reemit stop error: {e}")

        # Close connections
        if self._mesh_handler:
            self._mesh_handler.disconnect()
        if self._meshcore_handler:
            self._meshcore_handler.disconnect()
        self._disconnect_rns()

        # Wait for threads
        for thread in [self._mesh_thread, self._rns_thread,
                        self._bridge_thread, self._meshcore_thread]:
            if thread and thread.is_alive():
                thread.join(timeout=5)

        # Stop WebSocket server
        self._stop_websocket_server()

        # Stop TX load balancer
        if self._load_balancer:
            self._load_balancer.stop()

        # Stop failover manager
        if self._failover_manager:
            self._failover_manager.stop()

        # Stop cross-gateway heartbeat
        if self._heartbeat:
            self._heartbeat.stop()

        # Stop RNS sniffer
        if HAS_RNS_SNIFFER:
            try:
                from monitoring.rns_sniffer import stop_rns_capture
                stop_rns_capture()
            except Exception as e:
                logger.debug(f"RNS sniffer stop error: {e}")

        logger.info("Bridge stopped")
        self._notify_status("stopped")

    # Send/queue surface provided by BridgeSendMixin
    # (bridge_send_mixin.py): send_to_meshtastic, send_to_rns,
    # _queue_send_rns, enqueue_message, get_queue_stats,
    # _drain_persistent_queue, _get_rns_destination,
    # _requeue_failed_message, _requeue_failed_chunks,
    # _dual_path_dedup_on, _dual_path_dedup_window.
    # send_to_meshcore() inherited from MeshCoreBridgeMixin.

    # Issue #66 ACK synthesis provided by BridgeAckMixin
    # (bridge_ack_mixin.py): _format_ack_text, _emit_ack_to_origin,
    # _maybe_emit_ack_for_msgid, _sweep_overdue_acks.

    def _on_meshtastic_receive(self, packet: dict) -> None:
        """Handle incoming Meshtastic packet (compatibility shim).

        Delegates to MeshtasticHandler._on_receive. Kept for backward
        compatibility with integration tests and external callers.
        """
        if self._mesh_handler:
            self._mesh_handler._on_receive(packet)

    def register_message_callback(self, callback: Callable):
        """Register callback for bridged messages"""
        with self._callbacks_lock:
            self._message_callbacks.append(callback)

    def register_status_callback(self, callback: Callable):
        """Register callback for status changes"""
        with self._callbacks_lock:
            self._status_callbacks.append(callback)

    # ========================================
    # Private Methods
    # ========================================

    def _meshtastic_loop(self):
        """Main loop for Meshtastic connection - delegates to handler."""
        if self._mesh_handler:
            self._mesh_handler.run_loop()

    # _meshcore_loop() inherited from MeshCoreBridgeMixin

    def _rns_loop(self):
        """Main loop for RNS connection with auto-reconnect.

        Uses ReconnectStrategy for exponential backoff with jitter.
        Respects permanent failure flag for non-retriable errors.
        Manages the RNS subsystem state independently of Meshtastic.
        """
        _logged_permanent_failure = False
        while self._running:
            try:
                # Don't retry if RNS init failed permanently (e.g., library not installed)
                if self._rns_init_failed_permanently:
                    self._update_subsystem_state("rns", SubsystemState.DISABLED)
                    if not _logged_permanent_failure:
                        logger.warning("RNS initialization failed permanently - "
                                      "bridge will not attempt reconnection. "
                                      "Check RNS/LXMF installation and logs above.")
                        _logged_permanent_failure = True
                    self._stop_event.wait(30)
                    continue

                if not self._connected_rns:
                    self._update_subsystem_state("rns", SubsystemState.DISCONNECTED)
                    if not self._rns_reconnect.should_retry():
                        logger.warning("RNS reconnection: max attempts reached, resetting")
                        self._rns_reconnect.reset()
                        self._stop_event.wait(self._rns_reconnect.config.max_delay)
                        continue

                    logger.info(f"Attempting RNS connection "
                               f"(attempt {self._rns_reconnect.attempts + 1})...")
                    self.health.record_connection_event("rns", "retry")
                    self._connect_rns()

                    if self._connected_rns:
                        self._rns_reconnect.record_success()
                        self.health.record_connection_event("rns", "connected")
                        self._update_subsystem_state("rns", SubsystemState.HEALTHY)
                        logger.info("RNS connection established")
                        # MF Issue #74 port: a fresh transport
                        # invalidates stale per-destination OPEN state
                        # from the prior connection. Without this,
                        # circuits stay OPEN the full recovery_timeout
                        # after recovery.
                        if self._circuit_breaker is not None:
                            _reset = self._circuit_breaker.reset_all()
                            if _reset:
                                logger.info(
                                    f"Reset {_reset} circuit(s) after "
                                    f"RNS reconnect"
                                )
                        # Start LXMF broadcast bridge plug-in (idempotent)
                        self._maybe_start_lxmf_broadcast()
                        # Start LXMF→MeshCore re-emit bridge (idempotent)
                        self._maybe_start_meshtastic_reemit()
                    else:
                        self._rns_reconnect.record_failure()
                        self._rns_reconnect.wait(self._stop_event)
                        continue

                if self._connected_rns:
                    # RNS handles its own event loop
                    self._stop_event.wait(1)

            except Exception as e:
                category = self.health.record_error("rns", e)
                logger.error(f"RNS loop error ({category}): {e}")
                self._connected_rns = False
                self.health.record_connection_event("rns", "error", str(e))

                if category == "permanent":
                    logger.error("RNS permanent error detected, stopping retries")
                    self._rns_init_failed_permanently = True
                    self._update_subsystem_state("rns", SubsystemState.DISABLED)
                else:
                    self._update_subsystem_state("rns", SubsystemState.DISCONNECTED)
                    self._rns_reconnect.record_failure()
                    self._rns_reconnect.wait(self._stop_event)

    def _bridge_loop(self):
        """Main loop for message bridging.

        Phase 2 (Circuit Breakers): Each subsystem operates independently.
        When a destination is down, messages are queued to the persistent
        queue instead of being dropped. The bridge drains queued messages
        when the destination comes back up.
        """
        loop_count = 0
        while self._running:
            try:
                # Sync subsystem states from connection status
                self._sync_subsystem_states()

                # Process Meshtastic → RNS queue
                try:
                    msg = self._mesh_to_rns_queue.get(timeout=0.1)
                    rns_state = self.health.get_subsystem_state("rns")
                    if rns_state in (SubsystemState.DISCONNECTED, SubsystemState.DISABLED):
                        # RNS is down — queue for later delivery
                        requeued = self._requeue_failed_message(msg, "rns")
                        if requeued:
                            self.health.record_message_queued_degraded()
                            logger.debug("Mesh→RNS: RNS subsystem down, message queued")
                    else:
                        self._process_mesh_to_rns(msg)
                    # Also route Meshtastic → MeshCore if handler active
                    if self._meshcore_handler:
                        try:
                            self._bridge_to_meshcore_queue.put_nowait(msg)
                        except Full:
                            logger.debug("→MeshCore queue full, dropping Mesh→MC message")
                except Empty:
                    pass

                # Process RNS → Meshtastic queue
                try:
                    msg = self._rns_to_mesh_queue.get(timeout=0.1)
                    mesh_state = self.health.get_subsystem_state("meshtastic")
                    if mesh_state in (SubsystemState.DISCONNECTED, SubsystemState.DISABLED):
                        # Meshtastic is down — queue for later delivery
                        requeued = self._requeue_failed_message(msg, "meshtastic")
                        if requeued:
                            self.health.record_message_queued_degraded()
                            logger.debug("RNS→Mesh: Meshtastic subsystem down, message queued")
                    else:
                        self._process_rns_to_mesh(msg)
                    # Also route RNS → MeshCore if handler active
                    if self._meshcore_handler:
                        try:
                            self._bridge_to_meshcore_queue.put_nowait(msg)
                        except Full:
                            logger.debug("→MeshCore queue full, dropping RNS→MC message")
                except Empty:
                    pass

                # Process MeshCore → Bridge queue (route to Meshtastic and/or RNS)
                try:
                    msg = self._meshcore_to_bridge_queue.get_nowait()
                    self._process_meshcore_to_bridge(msg)
                except Empty:
                    pass

                # Process Bridge → MeshCore queue (messages from other networks)
                try:
                    msg = self._bridge_to_meshcore_queue.get_nowait()
                    mc_state = self.health.get_subsystem_state("meshcore")
                    if mc_state in (SubsystemState.DISCONNECTED, SubsystemState.DISABLED):
                        # MeshCore is down — requeue for retry. Apply the
                        # SAME channel resolver as the healthy path
                        # (`_process_bridge_to_meshcore` → `_resolve_bridge_target_channel`)
                        # BEFORE persisting the message. Without this,
                        # `_requeue_failed_message` lifts the SOURCE
                        # protocol's `metadata.channel` into the payload,
                        # and the replay (`meshcore_handler.queue_send`
                        # → `_process_outbound`) sends to that slot —
                        # re-introducing the Issue #37 leak shape via
                        # the disconnect-window path (live-confirmed
                        # 2026-05-20 before this fix). Drop with the
                        # same counter the healthy-path uses so
                        # operators can monitor both paths from one
                        # signal on `/api/stats`.
                        target_channel = self._resolve_bridge_target_channel(msg)
                        if target_channel < 0:
                            with self._stats_lock:
                                self.stats.setdefault(
                                    'meshcore_bridge_default_channel_drop', 0)
                                self.stats['meshcore_bridge_default_channel_drop'] += 1
                            logger.warning(
                                "Bridge →MC (replay): no channel resolved "
                                "(config.meshcore.bridge_target_channel unset); "
                                "dropping rather than letting the replay path "
                                "leak to slot 0 (Public)."
                            )
                            self.health.record_message_failed(
                                "mesh_to_meshcore", requeued=False,
                            )
                        else:
                            requeued = self._requeue_failed_message(
                                msg, "meshcore",
                                channel_override=target_channel,
                            )
                            if requeued:
                                self.health.record_message_queued_degraded()
                                logger.debug(
                                    "→MeshCore: subsystem down, "
                                    f"queued for replay on ch{target_channel}"
                                )
                    else:
                        self._process_bridge_to_meshcore(msg)
                except Empty:
                    pass

                # Periodically check delivery timeouts (~every 30s)
                loop_count += 1
                if loop_count % 150 == 0:
                    self.delivery_tracker.check_timeouts()
                    # Drain persistent queue when subsystems are back
                    self._drain_persistent_queue()
                    # Issue #66: surface overdue pending-acks as TIMEOUT
                    # ACKs so the origin sender stops wondering.
                    self._sweep_overdue_acks()

            except Exception as e:
                logger.error(f"Bridge loop error: {e}")
                self._stop_event.wait(1)

    # _sync_subsystem_states provided by BridgeHealthMixin;
    # _drain_persistent_queue provided by BridgeSendMixin.

    # RNS connection lifecycle methods provided by RNSConnectionMixin:
    # _suppress_signal_in_thread, _init_rns_main_thread, _connect_rns,
    # _setup_lxmf, _disconnect_rns

    def _maybe_start_lxmf_broadcast(self) -> None:
        """Start the LXMF broadcast bridge plug-in if configured.

        Idempotent — safe to call on every RNS reconnect. The bridge
        owns its own LXMRouter (LXMF 0.9.4 caps a router at one
        delivery identity, so it can't share the gateway's).
        """
        cfg = getattr(self.config, "lxmf_broadcast", None)
        # `is True` (not just truthy) so a MagicMock-attribute on a mocked
        # GatewayConfig in unit tests doesn't accidentally trigger startup.
        if cfg is None or getattr(cfg, "enabled", False) is not True:
            return
        if self._lxmf_broadcast is not None and self._lxmf_broadcast.is_running:
            return
        try:
            from .lxmf_broadcast_bridge import create_from_gateway_config
            if self._lxmf_broadcast is None:
                # Issue #66 first-caller wiring: hand the broadcast bridge a
                # reference to the persistent queue + our ack-emit callback
                # so its per-subscriber LXMF delivery callbacks can drive
                # synthetic [delivered:<id>] back to MeshCore via
                # _maybe_emit_ack_for_msgid. No-op when
                # lxmf_broadcast.ack_required is False (default).
                self._lxmf_broadcast = create_from_gateway_config(
                    self.config,
                    persistent_queue=self._persistent_queue,
                    ack_emit_callback=self._maybe_emit_ack_for_msgid,
                )
                if self._lxmf_broadcast is None:
                    return
                # MeshCore RX hook only — LXMF RX is handled by the
                # bridge's own router via its own delivery callback.
                self.register_message_callback(self._lxmf_broadcast.on_meshcore_message)
            if self._lxmf_broadcast.start():
                logger.info("LXMF broadcast bridge started (%s)",
                            self._lxmf_broadcast.destination_hash_hex)
        except Exception as e:
            logger.error("Failed to start LXMF broadcast bridge: %s", e)

    def _maybe_start_meshtastic_reemit(self) -> None:
        """Start the LXMF→MeshCore re-emit bridge if configured.

        Idempotent. Safe to call from the RNS loop on every reconnect.
        Stays a no-op when ``config.meshtastic_reemit.enabled = False``,
        so unit tests that build a default GatewayConfig don't trigger
        startup.
        """
        cfg = getattr(self.config, "meshtastic_reemit", None)
        if cfg is None or getattr(cfg, "enabled", False) is not True:
            return
        if (self._meshtastic_reemit is not None
                and self._meshtastic_reemit.is_running):
            return
        try:
            from .meshtastic_reemit_bridge import create_from_gateway_config
            if self._meshtastic_reemit is None:
                self._meshtastic_reemit = create_from_gateway_config(self.config)
                if self._meshtastic_reemit is None:
                    return
            if self._meshtastic_reemit.start():
                logger.info(
                    "Meshtastic re-emit bridge started — %d allowed "
                    "identity(ies), target MeshCore ch%d",
                    len(self._meshtastic_reemit._allowed_identities),
                    cfg.target_channel,
                )
        except Exception as e:
            logger.error("Failed to start meshtastic re-emit bridge: %s", e)

    def _on_lxmf_receive(self, message):
        """Handle incoming LXMF message"""
        try:
            # Update node info
            source_hash = message.source_hash
            node = UnifiedNode.from_rns(source_hash)
            self.node_tracker.add_node(node)

            # Capture LXMF message for traffic inspection
            if HAS_RNS_SNIFFER:
                try:
                    sniffer = get_rns_sniffer()
                    if sniffer and sniffer._running:
                        # LXMessage.content can be either bytes (binary LXMF
                        # payload) or str — encode only when we got text.
                        # (MeshForge #1162: 'bytes'.encode() raised, dropping
                        # the capture; delivery itself was unaffected.)
                        raw_content = message.content or b''
                        content_bytes = (
                            raw_content.encode('utf-8')
                            if isinstance(raw_content, str)
                            else raw_content
                        )
                        packet_info = RNSPacketInfo(
                            packet_type=RNSPacketType.DATA,
                            source_hash=source_hash,
                            direction="inbound",
                            payload=content_bytes,
                            payload_size=len(content_bytes),
                            announce_aspect="lxmf.delivery",
                        )
                        sniffer._store_packet(packet_info)
                except Exception as e:
                    logger.debug(f"RNS sniffer LXMF capture error: {e}")

            # Decode LXMF bytes->str up front so stored records and the
            # BridgedMessage carry clean text, not Python bytes reprs.
            # Without this, messages.db logs "[b'Meshtastic'] b'...'".
            content_str = (
                message.content.decode("utf-8", errors="replace")
                if isinstance(message.content, (bytes, bytearray))
                else (message.content or "")
            )
            title_str = (
                message.title.decode("utf-8", errors="replace")
                if isinstance(message.title, (bytes, bytearray))
                else (message.title or "")
            )

            msg = BridgedMessage(
                source_network="rns",
                source_id=source_hash.hex(),
                destination_id=None,
                content=content_str,
                title=title_str,
                metadata={
                    'lxmf_stamp': message.stamp,
                }
            )

            # Store incoming message for UI/history
            try:
                from commands import messaging
                # Combine title and content for RNS messages (decoded)
                content = content_str
                if title_str:
                    content = f"[{title_str}] {content}"
                messaging.store_incoming(
                    from_id=source_hash.hex(),
                    content=content,
                    network="rns",
                    to_id=None,  # LXMF doesn't have destination in received messages
                )
            except Exception as e:
                logger.debug(f"Could not store incoming RNS message: {e}")

            # Queue for bridging if enabled (non-blocking to prevent deadlock)
            if self._router.should_bridge(msg):
                try:
                    self._rns_to_mesh_queue.put_nowait(msg)
                except Full:
                    logger.warning("RNS→Mesh queue full, dropping message")
                    with self._stats_lock:
                        self.stats['errors'] += 1

            # Notify callbacks
            self._notify_message(msg)

            # LXMF→MeshCore re-emit hook. Only acts when the operator
            # opted in via meshtastic_reemit.enabled=True AND the LXMF
            # source_hash matches one of the configured
            # MeshtasticBroadcastBridge identities. Cheap no-op
            # otherwise. Wrapped so a bridge bug can't take down the
            # gateway's LXMF RX path.
            if self._meshtastic_reemit:
                try:
                    self._meshtastic_reemit.on_lxmf_message(
                        source_hash, content_str,
                    )
                except Exception as e:
                    logger.debug(f"meshtastic_reemit hook error: {e}")

        except Exception as e:
            logger.error(f"Error processing LXMF message: {e}")

    def _on_rns_announce(self, dest_hash, announced_identity, app_data):
        """Handle RNS announce for node discovery"""
        try:
            # Capture announce packet for traffic inspection
            if HAS_RNS_SNIFFER:
                try:
                    import RNS
                    sniffer = get_rns_sniffer()
                    if sniffer and sniffer._running:
                        packet_info = RNSPacketInfo(
                            packet_type=RNSPacketType.ANNOUNCE,
                            destination_hash=dest_hash,
                            direction="inbound",
                            announce_app_data=app_data,
                            announce_aspect="lxmf.delivery",
                        )
                        # Get identity hash if available
                        if announced_identity:
                            try:
                                packet_info.source_hash = announced_identity.hash
                                packet_info.announce_identity = announced_identity.hash
                            except Exception:
                                pass
                        # Get hop count
                        try:
                            if RNS.Transport.has_path(dest_hash):
                                hops = RNS.Transport.hops_to(dest_hash)
                                packet_info.hops = hops if hops is not None else 0
                        except Exception:
                            pass
                        sniffer._store_packet(packet_info)
                except Exception as e:
                    logger.debug(f"RNS sniffer capture error: {e}")

            node = UnifiedNode.from_rns(dest_hash, app_data=app_data)
            self.node_tracker.add_node(node)
            logger.debug(f"Discovered RNS node: {dest_hash.hex()[:8]}")
        except Exception as e:
            logger.error(f"Error processing RNS announce: {e}")

    # Routing delegated to MessageRouter (see gateway/message_routing.py)

    def get_routing_stats(self) -> Dict[str, Any]:
        """Get routing classifier statistics."""
        return self._router.get_routing_stats()

    def get_last_classification(self) -> Optional[Dict]:
        """Get the last classification result for debugging."""
        return self._router.get_last_classification()

    def fix_routing(self, msg_id: str, correct_category: str) -> bool:
        """Record a user correction for routing decisions."""
        return self._router.fix_routing(msg_id, correct_category)

    def _process_mesh_to_rns(self, msg: BridgedMessage):
        """Process message from Meshtastic to RNS.

        On send failure for non-broadcast messages, attempts to persist
        to the persistent queue for later retry.
        """
        try:
            prefix = f"[Mesh:{msg.source_id[-4:]}] " if msg.source_id else "[Mesh] "
            content = prefix + msg.content

            # Build destination list. Direct DM → single recipient; broadcast (or a
            # DM whose peer didn't resolve) fans out to every default_lxmf_destination
            # configured — str OR list, via get_lxmf_destinations(). Ported from
            # MeshForge (lead repo): the str-only path silently ignored a list config,
            # dropping every broadcast despite default_lxmf_destination being set.
            destinations = []
            if msg.destination_id and not msg.is_broadcast:
                direct = self._get_rns_destination(msg.destination_id)
                if direct:
                    destinations.append(direct)

            if not destinations:
                for hex_str in self.config.rns.get_lxmf_destinations():
                    try:
                        destinations.append(bytes.fromhex(hex_str))
                    except ValueError:
                        logger.warning(f"Invalid default_lxmf_destination hex: {hex_str!r}")

            sent_count = 0
            for dest_hash in destinations:
                if self.send_to_rns(content, dest_hash):
                    sent_count += 1

            if sent_count:
                if len(destinations) > 1:
                    logger.info(f"Bridge Mesh→RNS: {sent_count}/{len(destinations)} dest(s) — {content[:50]}...")
                else:
                    logger.info(f"Bridge Mesh→RNS: {content[:50]}...")
                with self._stats_lock:
                    self.stats['messages_mesh_to_rns'] += 1
                self.health.record_message_sent("mesh_to_rns")
            elif msg.is_broadcast:
                # No default_lxmf_destination configured, or all peers unreachable —
                # broadcast best-effort, debug-only (LXMF is point-to-point).
                logger.debug(f"Mesh→RNS broadcast not delivered: {content[:30]}...")
            else:
                logger.warning(f"Failed to bridge Mesh→RNS: {content[:30]}...")
                with self._stats_lock:
                    self.stats['errors'] += 1
                requeued = self._requeue_failed_message(msg, "rns")
                self.health.record_message_failed("mesh_to_rns", requeued=requeued)

        except Exception as e:
            logger.error(f"Error bridging Mesh→RNS: {e}")
            with self._stats_lock:
                self.stats['errors'] += 1
            self.health.record_error("rns", e)
            self._requeue_failed_message(msg, "rns")
            self.health.record_message_failed("mesh_to_rns", requeued=True)

    def _process_rns_to_mesh(self, msg: BridgedMessage):
        """Process message from RNS to Meshtastic.

        In mqtt_bridge mode, routes through the persistent queue for
        reliable delivery with retry. Otherwise sends directly and
        persists to queue on failure.

        Oversize content is split with ``chunk_for_mesh`` (ported from
        MeshForge `0066470`) so multi-line RNS content (NomadNet, or a bot
        reply bridged in over RNS) isn't silently truncated to one packet by
        the handler's ``_truncate_if_needed``. Short content yields a single
        chunk == content, so the common path is unchanged.
        """
        try:
            prefix = f"[RNS:{msg.source_id[:4]}] "
            content = prefix + msg.content

            # Dual-path dedup (gated, default off; mirror of MeshForge
            # 1494e8f). On a box whose LOCAL mesh_bridge also carries this
            # RF traffic, a broadcast the mesh_bridge already transmitted
            # arrives here a second time via a peer's RNS relay and would
            # land on the radio TWICE. Suppress ONLY on a registry hit —
            # when the local radio missed the message on RF there is no hit
            # and this copy still delivers (the relay's fallback value).
            if (self._dual_path_dedup_on()
                    and get_rf_tx_registry().seen_within(
                        msg.content, self._dual_path_dedup_window())):
                with self._stats_lock:
                    self.stats['rns_to_mesh_dual_path_suppressed'] = (
                        self.stats.get('rns_to_mesh_dual_path_suppressed', 0) + 1)
                logger.info(
                    f"Bridge RNS→Mesh suppressed (dual-path dedup — already "
                    f"on RF via mesh_bridge): {msg.content[:50]}...")
                return

            # The [RNS:xxxx] prefix rides EVERY chunk (prefix= reserves its
            # bytes; mirror of MeshForge f02ad82) — the tag is the echo-loop
            # invariant, and the chunk-0-only shape leaked untagged tail
            # chunks through the loop guards on every gateway.
            chunks = chunk_for_mesh(msg.content, prefix=prefix)
            multi = f" [{len(chunks)} chunks]" if len(chunks) > 1 else ""

            # In mqtt_bridge mode, enqueue each chunk as its own queue item
            # (independent retry). NOTE: meshanchor-server runs
            # bridge_mode="meshcore_bridge", so this branch is inactive there;
            # the direct path below is the live one.
            if (self._persistent_queue
                    and self.config.bridge_mode == "mqtt_bridge"
                    and HAS_PERSISTENT_QUEUE):
                enqueued = 0
                for chunk in chunks:
                    payload = {
                        'message': chunk,
                        'channel': self.config.meshtastic.channel,
                        'source_id': msg.source_id,
                    }
                    if self._persistent_queue.enqueue(
                        payload=payload,
                        destination="mqtt",
                        priority=MessagePriority.NORMAL,
                    ):
                        enqueued += 1
                if enqueued:
                    logger.info(f"Bridge RNS→Mesh (queued{multi}): {content[:50]}...")
                    with self._stats_lock:
                        self.stats['messages_rns_to_mesh'] += 1
                    self.health.record_message_sent("rns_to_mesh")
                    return
                # Nothing queued (all deduped/rejected) — fall through to a
                # direct send attempt, mirroring the original None→fallthrough.

            # Direct send (non-MQTT mode or queue unavailable). Send every
            # chunk; success requires all of them to go out.
            failed_chunks = [
                chunk for chunk in chunks
                if not self.send_to_meshtastic(
                    chunk, channel=self.config.meshtastic.channel)
            ]
            if chunks and not failed_chunks:
                logger.info(f"Bridge RNS→Mesh{multi}: {content[:50]}...")
                with self._stats_lock:
                    self.stats['messages_rns_to_mesh'] += 1
                self.health.record_message_sent("rns_to_mesh")
            else:
                sent = len(chunks) - len(failed_chunks)
                logger.warning(
                    f"Failed to bridge RNS→Mesh ({sent}/{len(chunks)} chunks sent)"
                )
                with self._stats_lock:
                    self.stats['errors'] += 1
                # Re-queue ONLY the failed chunks — each already byte-bounded —
                # so the retry preserves the full content instead of re-sending
                # the whole un-chunked original (which the retry would truncate)
                # and without re-transmitting chunks that already went out.
                requeued = self._requeue_failed_chunks(
                    failed_chunks or chunks, "meshtastic")
                self.health.record_message_failed("rns_to_mesh", requeued=requeued)

        except Exception as e:
            logger.error(f"Error bridging RNS→Mesh: {e}")
            with self._stats_lock:
                self.stats['errors'] += 1
            self.health.record_error("meshtastic", e)
            self._requeue_failed_message(msg, "meshtastic")
            self.health.record_message_failed("rns_to_mesh", requeued=True)

    # _process_meshcore_to_bridge() and _process_bridge_to_meshcore()
    # inherited from MeshCoreBridgeMixin — see meshcore_bridge_mixin.py

    def _test_rns(self) -> bool:
        """Test RNS availability"""
        return _HAS_RNS

    def _notify_message(self, msg):
        """Notify message callbacks and emit to event bus (thread-safe snapshot).

        Handles both BridgedMessage and CanonicalMessage objects.

        Issue #17 Phase 3: Emit messages to event bus so UI panels can subscribe
        and display RX messages without being directly coupled to the bridge.
        """
        with self._callbacks_lock:
            callbacks = list(self._message_callbacks)
        for callback in callbacks:
            try:
                callback(msg)
            except Exception as e:
                logger.error(f"Message callback error: {e}")

        # Emit to event bus for UI panels (Issue #17 Phase 3)
        if HAS_EVENT_BUS and emit_message:
            try:
                # Handle both BridgedMessage (source_id) and CanonicalMessage (source_address)
                node_id = getattr(msg, 'source_id', None) or getattr(msg, 'source_address', '') or ''
                dest_id = getattr(msg, 'destination_id', None) or getattr(msg, 'destination_address', None)
                title = getattr(msg, 'title', None) or (msg.metadata.get('title') if msg.metadata else None)
                emit_message(
                    direction='rx',
                    content=msg.content,
                    node_id=node_id,
                    node_name="",  # Could be enhanced with node lookup
                    channel=msg.metadata.get('channel', 0) if msg.metadata else 0,
                    network=msg.source_network,
                    raw_data={
                        'destination_id': dest_id,
                        'is_broadcast': msg.is_broadcast,
                        'title': title,
                        'timestamp': msg.timestamp.isoformat() if msg.timestamp else None,
                        'metadata': msg.metadata
                    }
                )
            except Exception as e:
                logger.warning(f"Event bus emit failed: {e}")

        # Auto-ingest tactical messages (X1 format) to timeline + event bus
        msg_type = getattr(msg, 'message_type', None)
        if msg_type is not None and hasattr(msg_type, 'value') and msg_type.value == 'tactical':
            try:
                from tactical.x1_codec import decode as x1_decode, is_x1
                if is_x1(msg.content):
                    tac_msg = x1_decode(msg.content)
                    emit_tactical(
                        tactical_type=tac_msg.tactical_type.name,
                        message_id=tac_msg.id,
                        sender_id=tac_msg.sender_id,
                        content=tac_msg.content,
                        encryption_mode=tac_msg.encryption_mode.value,
                    )
            except Exception as e:
                logger.warning(f"Tactical auto-ingest failed: {e}")

    def _start_websocket_server(self):
        """Start WebSocket server for real-time message broadcast to web UI.

        Default-off: meshanchor-map.service owns :5001. With both processes
        bound, second-to-bind loses with EADDRINUSE. Enable
        config.enable_websocket only when the gateway runs without
        meshanchor-map — otherwise map-served WS clients won't see events
        emitted into this process's in-memory event_bus regardless.
        """
        if not getattr(self.config, 'enable_websocket', False):
            logger.info(
                "Gateway WebSocket disabled (config.enable_websocket=False); "
                "meshanchor-map.service owns :5001"
            )
            return
        if not HAS_WEBSOCKET:
            return
        try:
            if is_websocket_available():
                if start_websocket_server(port=5001):
                    logger.info("WebSocket server started on port 5001")
                    self._websocket_started = True
                else:
                    logger.debug("WebSocket server failed to start")
            else:
                logger.debug("WebSocket not available (websockets library not installed)")
        except Exception as e:
            logger.debug(f"Could not start WebSocket server: {e}")

    def _stop_websocket_server(self):
        """Stop WebSocket server."""
        if getattr(self, '_websocket_started', False):
            try:
                stop_websocket_server()
                logger.info("WebSocket server stopped")
            except Exception as e:
                logger.debug(f"Error stopping WebSocket server: {e}")

    def _notify_status(self, status: str):
        """Notify status callbacks (thread-safe snapshot)"""
        with self._callbacks_lock:
            callbacks = list(self._status_callbacks)
        for callback in callbacks:
            try:
                callback(status, self.get_status())
            except Exception as e:
                logger.error(f"Status callback error: {e}")


# === Module-level helper functions for CLI/headless operation ===
# Extracted to gateway_cli.py; re-exported here for backward compatibility.
from .gateway_cli import (  # noqa: F401, E402
    start_gateway_headless,
    stop_gateway_headless,
    get_gateway_stats,
    is_gateway_running,
)
