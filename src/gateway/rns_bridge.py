"""
RNS-Meshtastic Bridge Service
Bridges Reticulum Network Stack and Meshtastic networks

MeshCore bridge processing extracted to meshcore_bridge_mixin.py.
RNS/LXMF connection lifecycle extracted to _rns_bridge_connection.py.
"""

import threading
import time
import logging
from queue import Queue, Empty, Full
from datetime import datetime
from typing import Optional, Callable, Dict, Any
from dataclasses import dataclass

from .config import GatewayConfig
from .node_tracker import UnifiedNodeTracker, UnifiedNode
from .reconnect import ReconnectStrategy
from .bridge_health import (
    BridgeHealthMonitor, DeliveryTracker,
    BridgeStatus, SubsystemState, MessageOrigin
)
from utils.boundary_timing import call_boundary
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

logger = logging.getLogger(__name__)

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


def _coerce_metadata_for_json(obj):
    """Recursively decode bytes inside dict/list payloads.

    Issue #66: PersistentMessageQueue.enqueue computes a dedup hash via
    json.dumps; raw bytes (LXMF title fields, sender_key, etc.) raise and
    drop the message silently. errors='replace' so corrupt input never
    crashes the requeue path.
    """
    if isinstance(obj, bytes):
        return obj.decode("utf-8", errors="replace")
    if isinstance(obj, dict):
        return {k: _coerce_metadata_for_json(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_coerce_metadata_for_json(v) for v in obj]
    return obj


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


class RNSMeshtasticBridge(RNSConnectionMixin, MeshCoreBridgeMixin):
    """
    Main gateway bridge between RNS, Meshtastic, and MeshCore networks.

    Supports multiple modes:
    1. RNS Over Meshtastic - Uses Meshtastic as RNS transport layer
    2. Message Bridge - Translates messages between separate networks
    3. MeshCore Bridge - Bridges MeshCore companion radio with other protocols
    4. Tri-Bridge - All three protocols (Meshtastic + MeshCore + RNS)

    MeshCore bridge processing methods inherited from MeshCoreBridgeMixin.
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

        # Register Meshtastic sender now that handler exists
        if self._persistent_queue and self._mesh_handler:
            self._persistent_queue.register_sender(
                "meshtastic", self._mesh_handler.queue_send
            )

            # Register MQTT sender for persistent queue (mqtt_bridge mode)
            if (self.config.bridge_mode == "mqtt_bridge"
                    and hasattr(self._mesh_handler, 'publish_to_mqtt')):
                self._persistent_queue.register_sender(
                    "mqtt", self._mesh_handler.publish_to_mqtt
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

    @property
    def bridge_status(self) -> BridgeStatus:
        """Get current bridge operational status."""
        return self.health.get_bridge_status()

    # =========================================================================
    # Subsystem State Management (Phase 2: Circuit Breakers)
    # =========================================================================

    def _update_subsystem_state(self, subsystem: str, state: SubsystemState) -> None:
        """Update a subsystem's state and emit an event if it changed.

        Args:
            subsystem: "meshtastic" or "rns"
            state: New SubsystemState value
        """
        old_state = self.health.set_subsystem_state(subsystem, state)
        if old_state != state:
            # Emit event for StatusBar and other listeners
            if HAS_EVENT_BUS:
                try:
                    from utils.event_bus import emit_service_status
                    emit_service_status(
                        f"bridge_{subsystem}",
                        available=(state == SubsystemState.HEALTHY),
                        message=f"{subsystem}: {state.value}",
                    )
                except Exception as e:
                    logger.debug(f"Failed to emit subsystem state event: {e}")

    def get_subsystem_state(self, subsystem: str) -> SubsystemState:
        """Get the current state of a bridge subsystem.

        Args:
            subsystem: "meshtastic", "rns", or "meshcore"

        Returns:
            Current SubsystemState.
        """
        return self.health.get_subsystem_state(subsystem)

    @property
    def is_fully_healthy(self) -> bool:
        """Check if bridge is fully operational (both networks up)."""
        return self.health.is_bridge_fully_healthy()

    def can_send_to(self, destination: str) -> bool:
        """
        Check if we can send to a destination (circuit breaker check).

        Args:
            destination: Target node/identity ID

        Returns:
            True if sending is allowed, False if circuit is open
        """
        if self._circuit_breaker is None:
            return True
        return self._circuit_breaker.can_send(destination)

    def record_send_success(self, destination: str) -> None:
        """Record successful send to destination (for circuit breaker)."""
        if self._circuit_breaker is not None:
            self._circuit_breaker.record_success(destination)

    def record_send_failure(self, destination: str, error: str = "") -> None:
        """Record failed send to destination (for circuit breaker)."""
        if self._circuit_breaker is not None:
            self._circuit_breaker.record_failure(destination, error)

    def get_open_circuits(self) -> Dict[str, Any]:
        """Get destinations with open circuits (currently blocked)."""
        if self._circuit_breaker is None:
            return {}
        return self._circuit_breaker.get_open_circuits()

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

    def get_status(self) -> dict:
        """Get current bridge status including subsystem states."""
        uptime = None
        if self.stats['start_time']:
            uptime = (datetime.now() - self.stats['start_time']).total_seconds()

        mesh_connected = self._mesh_handler.is_connected if self._mesh_handler else False
        meshcore_connected = (
            self._meshcore_handler.is_connected if self._meshcore_handler else False
        )
        meshcore_config = getattr(self.config, 'meshcore', None)
        return {
            'running': self._running,
            'enabled': self.config.enabled,
            'meshtastic_connected': mesh_connected,
            'rns_connected': self._connected_rns,
            'rns_via_rnsd': self._rns_via_rnsd,
            'meshcore_connected': meshcore_connected,
            'meshcore_enabled': bool(meshcore_config and meshcore_config.enabled),
            'uptime_seconds': uptime,
            'statistics': self.stats.copy(),
            'node_stats': self.node_tracker.get_stats(),
            'subsystems': self.health.get_subsystem_states(),
            'bridge_status': self.bridge_status.value,
        }

    def send_to_meshtastic(self, message: str, destination: str = None, channel: int = 0) -> bool:
        """Send a message to Meshtastic network."""
        if not self._mesh_handler:
            logger.warning("Meshtastic handler not initialized")
            return False
        return self._mesh_handler.send_text(message, destination, channel)

    def send_to_rns(self, message: str, destination_hash: bytes = None) -> bool:
        """Send a message to RNS network via LXMF"""
        if not self._connected_rns:
            logger.warning("Not connected to RNS")
            return False

        if self._lxmf_source is None:
            logger.warning("LXMF source not initialized (partial RNS init)")
            return False

        try:
            import RNS
            import LXMF

            if destination_hash:
                # Direct message
                hash_short = destination_hash.hex()[:8]
                if not call_boundary("rnsd.has_path",
                                     RNS.Transport.has_path, destination_hash,
                                     target=hash_short):
                    call_boundary("rnsd.request_path",
                                  RNS.Transport.request_path, destination_hash,
                                  target=hash_short)
                    # Wait briefly for path (interruptible on shutdown)
                    for _ in range(50):
                        if RNS.Transport.has_path(destination_hash):
                            break
                        if self._stop_event.wait(0.1):
                            break

                if not RNS.Transport.has_path(destination_hash):
                    logger.warning("No path to destination")
                    return False

                dest_identity = call_boundary("rnsd.identity_recall",
                                              RNS.Identity.recall, destination_hash,
                                              target=hash_short)
                destination = RNS.Destination(
                    dest_identity,
                    RNS.Destination.OUT,
                    RNS.Destination.SINGLE,
                    "lxmf",
                    "delivery"
                )
            else:
                # Broadcast not directly supported in LXMF
                logger.info(
                    "Broadcast to RNS dropped: set rns.default_lxmf_destination "
                    "in gateway config to route broadcasts to a specific LXMF peer"
                )
                return False

            lxm = LXMF.LXMessage(
                destination,
                self._lxmf_source,
                message,
                "MeshAnchor Gateway"
            )

            # Track delivery confirmation
            msg_id = f"lxmf-{int(time.time() * 1000)}"
            self.delivery_tracker.track_message(
                msg_id, destination_hash, message[:50]
            )

            # Register LXMF delivery/failure callbacks
            #
            # Issue #66: on_delivered + on_failed also call
            # _maybe_emit_ack_for_msgid so messages with ack_required=True
            # (and a registered pending-ack record in the queue) get a
            # synthetic ACK CanonicalMessage routed back to the origin
            # protocol. Idempotent — mark_acked() returns None on the
            # second call to prevent double-emission.
            def on_delivered(receipt):
                self.delivery_tracker.confirm_delivery(msg_id)
                self._maybe_emit_ack_for_msgid(msg_id, kind='delivered')

            def on_failed(receipt):
                reason = "delivery_failed"
                if hasattr(receipt, 'failure_reason'):
                    reason = str(receipt.failure_reason)
                self.delivery_tracker.confirm_failure(msg_id, reason)
                self._maybe_emit_ack_for_msgid(msg_id, kind='failed')

            try:
                lxm.register_delivery_callback(on_delivered)
                lxm.register_failed_callback(on_failed)
            except (AttributeError, TypeError):
                # LXMF version may not support callbacks
                logger.debug("LXMF callbacks not available, skipping delivery tracking")

            call_boundary("rnsd.handle_outbound",
                          self._lxmf_router.handle_outbound, lxm,
                          target=hash_short)
            return True

        except Exception as e:
            logger.error(f"Failed to send to RNS: {e}")
            with self._stats_lock:
                self.stats['errors'] += 1
            return False

    # send_to_meshcore() inherited from MeshCoreBridgeMixin

    def _queue_send_rns(self, payload: Dict) -> bool:
        """Send handler for persistent queue - RNS destination."""
        message = payload.get('message', '')
        destination_hash = payload.get('destination_hash')

        if not self._connected_rns:
            return False

        try:
            import RNS
            import LXMF

            if not destination_hash:
                return False

            if isinstance(destination_hash, str):
                destination_hash = bytes.fromhex(destination_hash)

            hash_short = destination_hash.hex()[:8]
            if not call_boundary("rnsd.has_path",
                                 RNS.Transport.has_path, destination_hash,
                                 target=hash_short):
                call_boundary("rnsd.request_path",
                              RNS.Transport.request_path, destination_hash,
                              target=hash_short)
                for _ in range(30):
                    if RNS.Transport.has_path(destination_hash):
                        break
                    if self._stop_event.wait(0.1):
                        return False

            if not RNS.Transport.has_path(destination_hash):
                return False

            dest_identity = call_boundary("rnsd.identity_recall",
                                          RNS.Identity.recall, destination_hash,
                                          target=hash_short)
            destination = RNS.Destination(
                dest_identity, RNS.Destination.OUT,
                RNS.Destination.SINGLE, "lxmf", "delivery"
            )

            lxm = LXMF.LXMessage(destination, self._lxmf_source, message, "MeshAnchor Gateway")
            call_boundary("rnsd.handle_outbound",
                          self._lxmf_router.handle_outbound, lxm,
                          target=hash_short)
            return True

        except Exception as e:
            logger.error(f"Queue send to RNS failed: {e}")
            return False

    def enqueue_message(self, message: str, destination: str, dest_type: str = "meshtastic",
                        priority: str = "normal",
                        ack_required: bool = False,
                        ack_origin_network: Optional[str] = None,
                        ack_origin_address: Optional[str] = None,
                        ack_timeout_seconds: int = 300,
                        **kwargs) -> Optional[str]:
        """
        Enqueue a message for reliable delivery.

        Args:
            message: Message content
            destination: Destination ID/hash
            dest_type: "meshtastic" or "rns"
            priority: "low", "normal", "high", or "urgent"
            ack_required: Issue #66 — opt in to application-layer ack.
                When True (and both origin fields are set), a successful
                delivery callback emits a synthetic ACK CanonicalMessage
                back to (ack_origin_network, ack_origin_address). When
                the destination doesn't ack within ack_timeout_seconds
                the periodic sweep emits a TIMEOUT ACK instead.
            ack_origin_network: where to route the synthetic ACK back to
                ("meshtastic" / "meshcore" / "rns"). Required when
                ack_required=True.
            ack_origin_address: address on ack_origin_network. Required
                when ack_required=True.
            ack_timeout_seconds: how long before emitting a TIMEOUT ACK.
                Defaults to 5 minutes (PersistentMessageQueue default).
            **kwargs: Additional parameters (channel, etc.)

        Returns:
            Message ID if enqueued, None if queue unavailable
        """
        if not self._persistent_queue:
            # Fall back to direct send. ack_required is silently ignored
            # here — direct sends bypass the queue's pending-ack table,
            # so there's nothing to register.
            if dest_type == "meshtastic":
                return "direct" if self.send_to_meshtastic(message, destination, kwargs.get('channel', 0)) else None
            else:
                dest_hash = kwargs.get('destination_hash')
                if isinstance(dest_hash, str):
                    dest_hash = bytes.fromhex(dest_hash)
                return "direct" if self.send_to_rns(message, dest_hash) else None

        # Map priority string to enum
        priority_map = {
            "low": MessagePriority.LOW,
            "normal": MessagePriority.NORMAL,
            "high": MessagePriority.HIGH,
            "urgent": MessagePriority.URGENT,
        }
        msg_priority = priority_map.get(priority, MessagePriority.NORMAL)

        payload = {
            'message': message,
            'destination': destination,
            **kwargs
        }

        msg_id = self._persistent_queue.enqueue(
            payload=payload,
            destination=dest_type,
            priority=msg_priority
        )

        # Issue #66: register the pending-ack record so the LXMF
        # delivery callback / overdue sweep can synthesize back to
        # origin. Both origin fields must be present — silent skip
        # otherwise keeps the contract simple.
        if (msg_id
                and ack_required
                and ack_origin_network
                and ack_origin_address):
            try:
                self._persistent_queue.register_pending_ack(
                    msg_id,
                    origin_network=ack_origin_network,
                    origin_address=ack_origin_address,
                    timeout_seconds=ack_timeout_seconds,
                )
            except Exception as e:
                logger.warning(
                    f"register_pending_ack failed for {msg_id[:8]}: {e}"
                )

        return msg_id

    # --- Issue #66: application-layer ack synthesis ---------------------

    # Textual ACK forms — see MeshForge sister repo Issue #66 step 3b
    # for the design rationale (human-readable, opt-in, no cross-gateway
    # parser dependency).
    _ACK_TEXT_DELIVERED = "[delivered: {short_id}]"
    _ACK_TEXT_FAILED = "[failed: {short_id}]"
    _ACK_TEXT_TIMEOUT = "[timeout: {short_id}]"

    def _format_ack_text(self, msg_id: str, kind: str) -> str:
        """Compose the operator-visible ACK string. Public for tests."""
        short_id = (msg_id or "")[:8]
        if kind == 'delivered':
            return self._ACK_TEXT_DELIVERED.format(short_id=short_id)
        if kind == 'failed':
            return self._ACK_TEXT_FAILED.format(short_id=short_id)
        if kind == 'timeout':
            return self._ACK_TEXT_TIMEOUT.format(short_id=short_id)
        return f"[{kind}: {short_id}]"

    def _emit_ack_to_origin(
        self,
        msg_id: str,
        origin_network: str,
        origin_address: str,
        kind: str,
    ) -> bool:
        """
        Send a synthetic ACK message back to the origin sender.

        See MeshForge sister-repo docstring for the per-protocol
        dispatch contract — this is the symmetric MeshAnchor side.

        RECURSION INVARIANT (Issue #66 first-caller): synth ACKs MUST NOT
        themselves acquire their own pending-ack record, or a default-on
        deployment of ack_required=True would self-amplify into a feedback
        loop. Today's structural safety: this function dispatches via
        `_mesh_handler.send_text` / `_meshcore_handler.send_text` / direct
        `send_to_rns(... destination_hash=...)`, all of which bypass
        `enqueue_message` and `LXMFBroadcastBridge.on_meshcore_message`
        (the two ack-registration sites). If a future refactor routes
        synth ACKs through either of those paths, the outbound message
        MUST set `metadata['meshforge_is_synth_ack'] = True` AND the
        receiver-side guards in both ack-registration sites MUST consult
        that marker (already wired in LXMFBroadcastBridge.on_meshcore_message
        as of 2026-05-18). Verified by tests in
        tests/test_lxmf_broadcast_ack_first_wins_issue66.py.
        """
        text = self._format_ack_text(msg_id, kind)
        try:
            if origin_network == 'meshtastic':
                if not self._mesh_handler:
                    logger.debug(
                        "ack synthesis skipped: meshtastic handler absent "
                        f"(msg_id={msg_id[:8]} kind={kind})"
                    )
                    return False
                # Symmetric channel placeholder handling — mirrors the
                # MeshCore branch below. MeshtasticBroadcastBridge mints
                # the "channel:<idx>" origin_address when a broadcast
                # arrives without a usable source_address/source_id
                # (canonical_message synthesizes "!00000000" when fromId
                # is missing, so this is a rare edge path today, but the
                # asymmetry was a latent footgun — without parsing, the
                # placeholder string was passed as destinationId to
                # meshtastic-python, producing undefined behaviour).
                if origin_address.startswith("channel:"):
                    try:
                        ch_idx = int(origin_address.split(":", 1)[1])
                    except (ValueError, IndexError):
                        logger.warning(
                            "ack synthesis: bad channel placeholder %r "
                            "for msg_id=%s",
                            origin_address, msg_id[:8],
                        )
                        return False
                    return bool(self._mesh_handler.send_text(
                        text, destination=None, channel=ch_idx,
                    ))
                return bool(self._mesh_handler.send_text(
                    text, destination=origin_address, channel=0,
                ))
            if origin_network == 'meshcore':
                if not self._meshcore_handler:
                    logger.debug(
                        "ack synthesis skipped: meshcore handler absent "
                        f"(msg_id={msg_id[:8]} kind={kind})"
                    )
                    return False
                # Issue #66 first-caller: MeshCore channel broadcasts
                # don't carry sender identity, so LXMFBroadcastBridge
                # uses the placeholder origin_address "channel:<idx>" to
                # signal "broadcast the ACK back on the originating
                # channel" rather than addressing a (nonexistent) DM
                # destination. Parse the placeholder here and dispatch
                # via destination=None + channel=N (broadcast). Falls
                # back to the DM path for non-placeholder addresses
                # (future protocol versions where channels DO carry
                # sender pubkey, or DM-origin senders).
                if origin_address.startswith("channel:"):
                    try:
                        ch_idx = int(origin_address.split(":", 1)[1])
                    except (ValueError, IndexError):
                        logger.warning(
                            "ack synthesis: bad channel placeholder %r "
                            "for msg_id=%s",
                            origin_address, msg_id[:8],
                        )
                        return False
                    return bool(self._meshcore_handler.send_text(
                        text, destination=None, channel=ch_idx,
                    ))
                return bool(self._meshcore_handler.send_text(
                    text, destination=origin_address,
                ))
            if origin_network == 'rns':
                try:
                    dest_hash = bytes.fromhex(origin_address)
                except (TypeError, ValueError):
                    logger.warning(
                        "ack synthesis: bad RNS origin_address hex "
                        f"({origin_address!r}) for msg_id={msg_id[:8]}"
                    )
                    return False
                return bool(self.send_to_rns(text, destination_hash=dest_hash))
            logger.debug(
                f"ack synthesis: unknown origin_network={origin_network!r} "
                f"for msg_id={msg_id[:8]}"
            )
            return False
        except Exception as e:
            logger.warning(
                f"ack synthesis failed for msg_id={msg_id[:8]} "
                f"kind={kind} origin={origin_network}: {e}"
            )
            return False

    def _maybe_emit_ack_for_msgid(self, msg_id: str, kind: str) -> bool:
        """
        Convert delivery proof (or failure) into a synthetic ACK back to
        the origin sender — if and only if the message was registered
        via PersistentMessageQueue.register_pending_ack().

        Idempotent: mark_acked() returns None on the second call.
        """
        if not self._persistent_queue:
            return False
        try:
            origin = self._persistent_queue.mark_acked(msg_id)
        except Exception as e:
            logger.warning(
                f"ack synthesis: mark_acked failed for {msg_id[:8]}: {e}"
            )
            return False
        if not origin:
            return False
        return self._emit_ack_to_origin(
            msg_id,
            origin_network=origin['origin_network'],
            origin_address=origin['origin_address'],
            kind=kind,
        )

    def _sweep_overdue_acks(self) -> int:
        """
        Find pending-ack records past their deadline; emit a synthetic
        TIMEOUT ACK for each + finalise via mark_timeout().

        Called periodically from _bridge_loop (every ~30s). Returns the
        count of TIMEOUT ACKs emitted this sweep.
        """
        if not self._persistent_queue:
            return 0
        try:
            overdue = self._persistent_queue.find_overdue_acks()
        except Exception as e:
            logger.warning(f"ack sweep: find_overdue_acks failed: {e}")
            return 0
        emitted = 0
        for row in overdue:
            msg_id = row['message_id']
            try:
                if not self._persistent_queue.mark_timeout(msg_id):
                    continue
            except Exception as e:
                logger.warning(
                    f"ack sweep: mark_timeout failed for {msg_id[:8]}: {e}"
                )
                continue
            self._emit_ack_to_origin(
                msg_id,
                origin_network=row['origin_network'],
                origin_address=row['origin_address'],
                kind='timeout',
            )
            emitted += 1
        if emitted:
            logger.info(f"ack sweep: emitted {emitted} TIMEOUT ACK(s)")
        return emitted

    def get_queue_stats(self) -> Dict:
        """Get persistent queue statistics."""
        if self._persistent_queue:
            return self._persistent_queue.get_stats()
        return {}

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

    def test_connection(self) -> dict:
        """Test connectivity to all configured networks"""
        results = {
            'meshtastic': {'connected': False, 'error': None},
            'rns': {'connected': False, 'error': None},
        }

        # Test Meshtastic
        try:
            if self._mesh_handler and self._mesh_handler.test_connection():
                results['meshtastic']['connected'] = True
        except Exception as e:
            results['meshtastic']['error'] = str(e)

        # Test RNS
        try:
            if self._test_rns():
                results['rns']['connected'] = True
        except Exception as e:
            results['rns']['error'] = str(e)

        # Test MeshCore (if enabled)
        if self._meshcore_handler:
            results['meshcore'] = {'connected': False, 'error': None}
            try:
                if self._meshcore_handler.is_connected:
                    results['meshcore']['connected'] = True
            except Exception as e:
                results['meshcore']['error'] = str(e)

        return results

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

    def _sync_subsystem_states(self) -> None:
        """Synchronize subsystem states from connection status.

        Called each bridge loop iteration. Both handlers manage their own
        reconnection, so we observe connection states and update accordingly.
        The RNS subsystem state is also updated in _rns_loop, but we sync
        here too so the bridge loop has accurate state even when _rns_loop
        is not running (e.g., in tests).
        """
        # Meshtastic
        if not self._mesh_handler:
            self._update_subsystem_state("meshtastic", SubsystemState.DISABLED)
        elif self._mesh_handler.is_connected:
            self._update_subsystem_state("meshtastic", SubsystemState.HEALTHY)
        else:
            self._update_subsystem_state("meshtastic", SubsystemState.DISCONNECTED)

        # RNS (also managed by _rns_loop, but kept in sync here)
        if self._rns_init_failed_permanently:
            self._update_subsystem_state("rns", SubsystemState.DISABLED)
        elif self._connected_rns:
            self._update_subsystem_state("rns", SubsystemState.HEALTHY)
        # Note: don't overwrite DISCONNECTED here — _rns_loop handles transitions

        # MeshCore
        if not self._meshcore_handler:
            self._update_subsystem_state("meshcore", SubsystemState.DISABLED)
        elif self._meshcore_handler.is_connected:
            self._update_subsystem_state("meshcore", SubsystemState.HEALTHY)
        else:
            self._update_subsystem_state("meshcore", SubsystemState.DISCONNECTED)

    def _drain_persistent_queue(self) -> None:
        """Process pending messages from the persistent queue.

        Called periodically from _bridge_loop when subsystems are healthy.
        Only drains messages destined for currently-connected subsystems.
        """
        if not self._persistent_queue:
            return
        try:
            self._persistent_queue.process_once(batch_size=5)
        except Exception as e:
            logger.warning(f"Persistent queue drain error: {e}")

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

            destination_hash = None
            if msg.destination_id and not msg.is_broadcast:
                destination_hash = self._get_rns_destination(msg.destination_id)

            # Broadcast → default LXMF destination (e.g., NomadNet)
            default_dest = getattr(self.config.rns, 'default_lxmf_destination', '')
            if destination_hash is None and isinstance(default_dest, str) and default_dest:
                try:
                    destination_hash = bytes.fromhex(default_dest)
                except ValueError:
                    logger.warning("Invalid default_lxmf_destination hex in config")

            if self.send_to_rns(content, destination_hash):
                logger.info(f"Bridge Mesh→RNS: {content[:50]}...")
                with self._stats_lock:
                    self.stats['messages_mesh_to_rns'] += 1
                self.health.record_message_sent("mesh_to_rns")
            else:
                if msg.is_broadcast:
                    logger.debug(f"Mesh→RNS broadcast not sent (no propagation node): {content[:30]}...")
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

    def _get_rns_destination(self, meshtastic_id: str) -> bytes:
        """Look up RNS destination hash for a Meshtastic node ID"""
        # Check node tracker for known mappings
        if hasattr(self, 'node_tracker') and self.node_tracker:
            node = self.node_tracker.get_node_by_mesh_id(meshtastic_id)
            if node and hasattr(node, 'rns_hash') and node.rns_hash:
                return node.rns_hash
        return None

    def _requeue_failed_message(self, msg, destination: str,
                                *, channel_override: Optional[int] = None) -> bool:
        """Persist a failed message to the persistent queue for later retry.

        Args:
            msg: The message that failed to send (BridgedMessage or CanonicalMessage).
            destination: Target network ("meshtastic", "rns", or "meshcore").
            channel_override: When set, this slot is written to the
                payload's top-level ``channel`` field — overriding any
                ``metadata['channel']`` lift. Cross-protocol bridge
                cargo passes this (resolved via
                ``_resolve_bridge_target_channel``) so the replay path
                doesn't preserve the SOURCE protocol's channel index.
                Without this override, Issue #37's privacy class was
                still live via the replay path even after the resolver
                fix — bug discovered live 2026-05-20.

        Returns:
            True if message was successfully persisted, False otherwise.
        """
        if not self._persistent_queue:
            return False

        try:
            # Handle both BridgedMessage (source_id) and CanonicalMessage (source_address)
            source_id = getattr(msg, 'source_id', None) or getattr(msg, 'source_address', '')
            dest_id = getattr(msg, 'destination_id', None) or getattr(msg, 'destination_address', '')
            # BridgedMessage.__post_init__ does NOT centralize bytes→str on
            # MeshAnchor; CanonicalMessage may arrive with bytes content, and
            # LXMF metadata frequently carries bytes title/sender_key. Coerce
            # both — json.dumps inside enqueue's dedup-hash step otherwise
            # raises and drops the message (Issue #66 mirror gap).
            content = msg.content
            if isinstance(content, bytes):
                content = content.decode("utf-8", errors="replace")
            elif not isinstance(content, str):
                content = ""
            metadata = _coerce_metadata_for_json(msg.metadata or {})
            # Lift channel into the top-level payload — channel-0 Public
            # leak follow-up (2026-05-20). The persistent queue's MeshCore
            # sender (meshcore_handler.queue_send → _process_outbound)
            # reads ``msg.get('channel')`` from the outer dict; without
            # this lift, a requeued message would have channel=None and
            # `_resolve_channel` would default it to 0 (Public), leaking
            # private-channel cargo on replay after a disconnect window.
            payload = {
                'message': content,
                'source_id': source_id,
                'destination_id': dest_id or "",
                'metadata': metadata,
            }
            if channel_override is not None:
                # Caller (cross-protocol bridge_to_meshcore path) already
                # resolved the destination slot via the config-only
                # resolver. Write it verbatim — do NOT fall through to
                # metadata.channel, which would re-introduce the leak.
                try:
                    payload['channel'] = int(channel_override)
                except (TypeError, ValueError):
                    pass
            else:
                raw_channel = metadata.get('channel') if isinstance(metadata, dict) else None
                if raw_channel is not None and raw_channel != '':
                    try:
                        payload['channel'] = int(raw_channel)
                    except (TypeError, ValueError):
                        pass
            msg_id = self._persistent_queue.enqueue(
                payload=payload,
                destination=destination,
                priority=MessagePriority.HIGH,
            )
            if msg_id is None:
                # Issue #67: enqueue() drops when no sender is registered
                # for the destination (or when deduped, or queue-full).
                # Don't lie about a successful requeue — health reporting
                # uses this return value.
                return False
            logger.debug(f"Failed message re-queued to persistent storage ({destination})")
            return True
        except Exception as e:
            logger.error(f"Failed to persist message for retry: {e}")
            return False

    def _process_rns_to_mesh(self, msg: BridgedMessage):
        """Process message from RNS to Meshtastic.

        In mqtt_bridge mode, routes through the persistent queue for
        reliable delivery with retry. Otherwise sends directly and
        persists to queue on failure.
        """
        try:
            prefix = f"[RNS:{msg.source_id[:4]}] "
            content = prefix + msg.content

            # In mqtt_bridge mode, use persistent queue for reliable delivery
            if (self._persistent_queue
                    and self.config.bridge_mode == "mqtt_bridge"
                    and HAS_PERSISTENT_QUEUE):
                payload = {
                    'message': content,
                    'channel': self.config.meshtastic.channel,
                    'source_id': msg.source_id,
                }
                msg_id = self._persistent_queue.enqueue(
                    payload=payload,
                    destination="mqtt",
                    priority=MessagePriority.NORMAL,
                )
                if msg_id:
                    logger.info(f"Bridge RNS→Mesh (queued): {content[:50]}...")
                    with self._stats_lock:
                        self.stats['messages_rns_to_mesh'] += 1
                    self.health.record_message_sent("rns_to_mesh")
                    return

            # Direct send (non-MQTT mode or queue unavailable)
            if self.send_to_meshtastic(content, channel=self.config.meshtastic.channel):
                logger.info(f"Bridge RNS→Mesh: {content[:50]}...")
                with self._stats_lock:
                    self.stats['messages_rns_to_mesh'] += 1
                self.health.record_message_sent("rns_to_mesh")
            else:
                logger.warning("Failed to bridge RNS→Mesh")
                with self._stats_lock:
                    self.stats['errors'] += 1
                requeued = self._requeue_failed_message(msg, "meshtastic")
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
