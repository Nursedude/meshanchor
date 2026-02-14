"""
Meshtastic-to-Meshtastic Preset Bridge

Bridges two Meshtastic networks with different LoRa presets,
enabling communication between e.g., LONG_FAST and SHORT_TURBO meshes.

Requirements:
- Two Meshtastic radios (one per preset)
- Two meshtasticd instances on different ports
- MeshForge gateway configured with bridge_mode="mesh_bridge"
"""

import re
import threading
import time
import logging
import hashlib
from queue import Queue, Empty
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import Optional, Dict, Set, Callable, Any

from .config import GatewayConfig, MeshtasticBridgeConfig, MeshtasticConfig
from utils.safe_import import safe_import

logger = logging.getLogger(__name__)

# Import meshtastic library (optional - graceful fallback)
_meshtastic, _HAS_MESHTASTIC = safe_import('meshtastic')
_meshtastic_tcp, _HAS_MESHTASTIC_TCP = safe_import('meshtastic.tcp_interface')
_pub, _HAS_PUBSUB = safe_import('pubsub', 'pub')


@dataclass
class BridgedMeshMessage:
    """Message being bridged between Meshtastic presets"""
    source_preset: str  # "longfast" or "shortturbo"
    source_id: str      # Node ID (!xxxxxxxx)
    destination_id: Optional[str]
    content: str
    channel: int = 0
    is_broadcast: bool = False
    timestamp: datetime = field(default_factory=datetime.now)
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def dedup_key(self) -> str:
        """Generate key for duplicate detection"""
        content_hash = hashlib.md5(self.content.encode()).hexdigest()[:8]
        return f"{self.source_id}:{content_hash}"


class MeshtasticPresetBridge:
    """
    Bridges messages between two Meshtastic networks with different presets.

    Typical use case: LONG_FAST rural mesh <-> SHORT_TURBO local mesh
    """

    def __init__(self, config: Optional[GatewayConfig] = None):
        self.config = config or GatewayConfig.load()
        self.bridge_config = self.config.mesh_bridge

        # State
        self._running = False
        self._stop_event = threading.Event()
        self._primary_connected = False
        self._secondary_connected = False

        # Interfaces
        self._primary_interface = None
        self._secondary_interface = None

        # Message queues
        self._primary_to_secondary: Queue = Queue(maxsize=1000)
        self._secondary_to_primary: Queue = Queue(maxsize=1000)

        # Duplicate suppression
        self._seen_messages: Dict[str, datetime] = {}
        self._seen_lock = threading.Lock()

        # Threads
        self._primary_thread = None
        self._secondary_thread = None
        self._bridge_thread = None
        self._cleanup_thread = None

        # Callbacks
        self._message_callbacks = []
        self._status_callbacks = []

        # Statistics
        self._stats_lock = threading.Lock()
        self.stats = {
            'messages_primary_to_secondary': 0,
            'messages_secondary_to_primary': 0,
            'duplicates_suppressed': 0,
            'errors': 0,
            'start_time': None,
        }

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def is_connected(self) -> bool:
        return self._primary_connected or self._secondary_connected

    def start(self) -> bool:
        """Start the mesh bridge"""
        if self._running:
            logger.warning("Mesh bridge already running")
            return True

        if not self.bridge_config.enabled:
            logger.warning("Mesh bridge not enabled in config")
            return False

        logger.info("Starting Meshtastic preset bridge...")
        logger.info(f"  Primary: {self.bridge_config.primary.preset} @ {self.bridge_config.primary.host}:{self.bridge_config.primary.port}")
        logger.info(f"  Secondary: {self.bridge_config.secondary.preset} @ {self.bridge_config.secondary.host}:{self.bridge_config.secondary.port}")

        self._running = True
        self._stop_event.clear()
        self.stats['start_time'] = datetime.now()

        # Start connection threads
        self._primary_thread = threading.Thread(
            target=self._primary_loop,
            daemon=True,
            name="MeshBridge-Primary"
        )
        self._primary_thread.start()

        self._secondary_thread = threading.Thread(
            target=self._secondary_loop,
            daemon=True,
            name="MeshBridge-Secondary"
        )
        self._secondary_thread.start()

        # Start bridge thread
        self._bridge_thread = threading.Thread(
            target=self._bridge_loop,
            daemon=True,
            name="MeshBridge-Forward"
        )
        self._bridge_thread.start()

        # Start cleanup thread
        self._cleanup_thread = threading.Thread(
            target=self._cleanup_loop,
            daemon=True,
            name="MeshBridge-Cleanup"
        )
        self._cleanup_thread.start()

        logger.info("Mesh bridge started")
        self._notify_status("started")
        return True

    def stop(self):
        """Stop the mesh bridge"""
        if not self._running:
            return

        logger.info("Stopping mesh bridge...")
        self._running = False
        self._stop_event.set()

        # Disconnect interfaces
        self._disconnect_primary()
        self._disconnect_secondary()

        # Wait for threads
        for thread in [self._primary_thread, self._secondary_thread,
                       self._bridge_thread, self._cleanup_thread]:
            if thread and thread.is_alive():
                thread.join(timeout=5)

        logger.info("Mesh bridge stopped")
        self._notify_status("stopped")

    def get_status(self) -> dict:
        """Get current bridge status"""
        uptime = None
        if self.stats['start_time']:
            uptime = (datetime.now() - self.stats['start_time']).total_seconds()

        return {
            'running': self._running,
            'enabled': self.bridge_config.enabled,
            'primary': {
                'connected': self._primary_connected,
                'preset': self.bridge_config.primary.preset,
                'host': self.bridge_config.primary.host,
                'port': self.bridge_config.primary.port,
            },
            'secondary': {
                'connected': self._secondary_connected,
                'preset': self.bridge_config.secondary.preset,
                'host': self.bridge_config.secondary.host,
                'port': self.bridge_config.secondary.port,
            },
            'direction': self.bridge_config.direction,
            'uptime_seconds': uptime,
            'statistics': self.stats.copy(),
        }

    def register_message_callback(self, callback: Callable):
        """Register callback for bridged messages"""
        self._message_callbacks.append(callback)

    def register_status_callback(self, callback: Callable):
        """Register callback for status changes"""
        self._status_callbacks.append(callback)

    # ========================================
    # Private Methods
    # ========================================

    def _primary_loop(self):
        """Main loop for primary Meshtastic connection"""
        while self._running:
            try:
                if not self._primary_connected:
                    self._connect_primary()

                if self._stop_event.wait(1):
                    break

            except Exception as e:
                logger.error(f"Primary loop error: {e}")
                self._primary_connected = False
                if self._stop_event.wait(5):
                    break

    def _secondary_loop(self):
        """Main loop for secondary Meshtastic connection"""
        while self._running:
            try:
                if not self._secondary_connected:
                    self._connect_secondary()

                if self._stop_event.wait(1):
                    break

            except Exception as e:
                logger.error(f"Secondary loop error: {e}")
                self._secondary_connected = False
                if self._stop_event.wait(5):
                    break

    def _bridge_loop(self):
        """Main loop for forwarding messages"""
        while self._running:
            try:
                # Process primary -> secondary queue
                if self.bridge_config.direction in ("bidirectional", "primary_to_secondary"):
                    try:
                        msg = self._primary_to_secondary.get(timeout=0.1)
                        self._forward_to_secondary(msg)
                    except Empty:
                        pass

                # Process secondary -> primary queue
                if self.bridge_config.direction in ("bidirectional", "secondary_to_primary"):
                    try:
                        msg = self._secondary_to_primary.get(timeout=0.1)
                        self._forward_to_primary(msg)
                    except Empty:
                        pass

            except Exception as e:
                logger.error(f"Bridge loop error: {e}")
                if self._stop_event.wait(1):
                    break

    def _cleanup_loop(self):
        """Cleanup stale dedup entries"""
        while self._running:
            try:
                if self._stop_event.wait(10):
                    break

                cutoff = datetime.now() - timedelta(seconds=self.bridge_config.dedup_window_sec)

                with self._seen_lock:
                    expired = [k for k, v in self._seen_messages.items() if v < cutoff]
                    for key in expired:
                        del self._seen_messages[key]

            except Exception as e:
                logger.error(f"Cleanup loop error: {e}")

    def _connect_primary(self):
        """Connect to primary Meshtastic interface"""
        self._primary_interface, self._primary_connected = self._connect_meshtastic(
            self.bridge_config.primary,
            "primary",
            self._on_primary_receive
        )

    def _connect_secondary(self):
        """Connect to secondary Meshtastic interface"""
        self._secondary_interface, self._secondary_connected = self._connect_meshtastic(
            self.bridge_config.secondary,
            "secondary",
            self._on_secondary_receive
        )

    def _connect_meshtastic(self, config: MeshtasticConfig, name: str, callback) -> tuple:
        """Connect to a Meshtastic interface"""
        if not _HAS_MESHTASTIC or not _HAS_MESHTASTIC_TCP or not _HAS_PUBSUB:
            logger.error("Meshtastic library not installed")
            return None, False

        try:
            logger.info(f"Connecting to {name} Meshtastic at {config.host}:{config.port}")

            interface = _meshtastic_tcp.TCPInterface(hostname=config.host)

            # Subscribe with unique topic name to avoid conflicts
            topic_name = f"meshtastic.receive.{name}"

            def on_receive(packet, interface):
                callback(packet)

            # Use a unique subscription key
            _pub.subscribe(on_receive, "meshtastic.receive")

            logger.info(f"Connected to {name} ({config.preset})")
            self._notify_status(f"{name}_connected")

            return interface, True

        except Exception as e:
            logger.error(f"Failed to connect to {name}: {e}")
            return None, False

    def _disconnect_primary(self):
        """Disconnect primary interface"""
        if self._primary_interface:
            try:
                self._primary_interface.close()
            except Exception as e:
                logger.debug(f"Error closing primary: {e}")
            self._primary_interface = None
        self._primary_connected = False

    def _disconnect_secondary(self):
        """Disconnect secondary interface"""
        if self._secondary_interface:
            try:
                self._secondary_interface.close()
            except Exception as e:
                logger.debug(f"Error closing secondary: {e}")
            self._secondary_interface = None
        self._secondary_connected = False

    def _on_primary_receive(self, packet: dict):
        """Handle message from primary interface"""
        self._process_receive(packet, "primary", self._primary_to_secondary)

    def _on_secondary_receive(self, packet: dict):
        """Handle message from secondary interface"""
        self._process_receive(packet, "secondary", self._secondary_to_primary)

    def _process_receive(self, packet: dict, source: str, queue: Queue):
        """Process received message and queue for forwarding"""
        try:
            decoded = packet.get('decoded', {})
            portnum = decoded.get('portnum')

            # Only bridge text messages
            if portnum != 'TEXT_MESSAGE_APP':
                return

            from_id = packet.get('fromId', '')
            to_id = packet.get('toId', '')

            payload = decoded.get('payload', b'')
            if isinstance(payload, bytes):
                text = payload.decode('utf-8', errors='ignore')
            else:
                text = str(payload)

            # Skip if message matches exclude filter
            if self.bridge_config.exclude_filter:
                try:
                    if re.search(self.bridge_config.exclude_filter, text):
                        return
                except re.error:
                    pass

            # Skip if message filter set and doesn't match
            if self.bridge_config.message_filter:
                try:
                    if not re.search(self.bridge_config.message_filter, text):
                        return
                except re.error:
                    pass

            preset = self.bridge_config.primary.preset if source == "primary" else self.bridge_config.secondary.preset

            msg = BridgedMeshMessage(
                source_preset=preset,
                source_id=from_id,
                destination_id=to_id,
                content=text,
                channel=packet.get('channel', 0),
                is_broadcast=to_id == '!ffffffff',
                metadata={
                    'snr': packet.get('rxSnr'),
                    'rssi': packet.get('rxRssi'),
                    'source_interface': source,
                }
            )

            # Check for duplicate
            if self._is_duplicate(msg):
                with self._stats_lock:
                    self.stats['duplicates_suppressed'] += 1
                return

            # Queue for forwarding
            try:
                queue.put_nowait(msg)
            except Exception:
                logger.warning(f"Queue full, dropping message from {source}")

            # Notify callbacks
            self._notify_message(msg)

        except Exception as e:
            logger.error(f"Error processing message from {source}: {e}")

    def _is_duplicate(self, msg: BridgedMeshMessage) -> bool:
        """Check if message was recently seen (loop prevention)"""
        key = msg.dedup_key

        with self._seen_lock:
            if key in self._seen_messages:
                return True

            self._seen_messages[key] = datetime.now()
            return False

    def _forward_to_secondary(self, msg: BridgedMeshMessage):
        """Forward message to secondary interface"""
        self._forward_message(
            msg,
            self._secondary_interface,
            self._secondary_connected,
            "secondary"
        )
        with self._stats_lock:
            self.stats['messages_primary_to_secondary'] += 1

    def _forward_to_primary(self, msg: BridgedMeshMessage):
        """Forward message to primary interface"""
        self._forward_message(
            msg,
            self._primary_interface,
            self._primary_connected,
            "primary"
        )
        with self._stats_lock:
            self.stats['messages_secondary_to_primary'] += 1

    def _forward_message(self, msg: BridgedMeshMessage, interface, connected: bool, dest_name: str):
        """Forward a message to the specified interface"""
        if not connected or not interface:
            logger.warning(f"Cannot forward to {dest_name}: not connected")
            return

        try:
            # Format message with prefix if enabled
            content = msg.content
            if self.bridge_config.add_prefix:
                prefix = self.bridge_config.prefix_format.format(
                    source_preset=msg.source_preset,
                    source_id=msg.source_id[-4:] if msg.source_id else "????",
                )
                content = prefix + content

            # Send to interface
            interface.sendText(
                content,
                destinationId=msg.destination_id if not msg.is_broadcast else None,
                channelIndex=msg.channel
            )

            logger.info(f"Bridged {msg.source_preset} -> {dest_name}: {content[:50]}...")

        except Exception as e:
            logger.error(f"Failed to forward to {dest_name}: {e}")
            with self._stats_lock:
                self.stats['errors'] += 1

    def _notify_message(self, msg: BridgedMeshMessage):
        """Notify message callbacks"""
        for callback in list(self._message_callbacks):
            try:
                callback(msg)
            except Exception as e:
                logger.error(f"Message callback error: {e}")

    def _notify_status(self, status: str):
        """Notify status callbacks"""
        status_data = self.get_status()
        for callback in list(self._status_callbacks):
            try:
                callback(status, status_data)
            except Exception as e:
                logger.error(f"Status callback error: {e}")


def create_mesh_bridge(config: Optional[GatewayConfig] = None) -> MeshtasticPresetBridge:
    """
    Create a Meshtastic preset bridge instance.

    Args:
        config: Optional gateway config, loads from file if not provided

    Returns:
        Configured bridge instance (not started)
    """
    return MeshtasticPresetBridge(config)
