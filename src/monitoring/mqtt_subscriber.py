"""
MQTT Nodeless Subscriber for MeshForge.

Enables mesh monitoring WITHOUT local Meshtastic hardware by connecting
to the public MQTT broker (mqtt.meshtastic.org) or a private broker.

This is the "nodeless" mode - observe the mesh from anywhere with internet.

Based on: pdxlocations/connect approach
See: https://github.com/pdxlocations/connect

Usage:
    subscriber = MQTTNodelessSubscriber()
    subscriber.start()

    # Get discovered nodes
    nodes = subscriber.get_nodes()

    # Get recent messages
    messages = subscriber.get_messages(limit=100)
"""

import json
import logging
import ssl
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Callable, Any
from collections import deque

# Import centralized path utility for sudo compatibility
from utils.paths import get_real_user_home

logger = logging.getLogger(__name__)

# Default Meshtastic MQTT settings
DEFAULT_BROKER = "mqtt.meshtastic.org"
DEFAULT_PORT_TLS = 8883
DEFAULT_PORT = 1883
DEFAULT_ROOT_TOPIC = "msh/US/2/e"
DEFAULT_CHANNEL = "LongFast"
DEFAULT_KEY = "AQ=="  # Default Meshtastic encryption key


@dataclass
class MQTTNode:
    """Node discovered via MQTT."""
    node_id: str
    long_name: str = ""
    short_name: str = ""
    hardware_model: str = ""
    role: str = ""
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    altitude: Optional[float] = None
    battery_level: Optional[int] = None
    voltage: Optional[float] = None
    channel_utilization: Optional[float] = None
    air_util_tx: Optional[float] = None
    snr: Optional[float] = None
    rssi: Optional[int] = None
    last_seen: datetime = field(default_factory=datetime.now)
    via_mqtt: bool = True
    hop_start: Optional[int] = None
    hops_away: Optional[int] = None

    def is_online(self, threshold_minutes: int = 15) -> bool:
        """Check if node was seen recently."""
        delta = datetime.now() - self.last_seen
        return delta.total_seconds() < threshold_minutes * 60

    def get_age_string(self) -> str:
        """Get human-readable age string."""
        delta = datetime.now() - self.last_seen
        seconds = delta.total_seconds()
        if seconds < 60:
            return f"{int(seconds)}s ago"
        elif seconds < 3600:
            return f"{int(seconds / 60)}m ago"
        elif seconds < 86400:
            return f"{int(seconds / 3600)}h ago"
        else:
            return f"{int(seconds / 86400)}d ago"


@dataclass
class MQTTMessage:
    """Message received via MQTT."""
    message_id: str
    from_id: str
    to_id: str
    text: str
    channel: int = 0
    timestamp: datetime = field(default_factory=datetime.now)
    hop_start: Optional[int] = None
    snr: Optional[float] = None
    rssi: Optional[int] = None


class MQTTNodelessSubscriber:
    """
    MQTT subscriber for nodeless Meshtastic monitoring.

    Connects to MQTT broker and passively monitors mesh traffic
    without requiring local hardware.
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """
        Initialize the MQTT subscriber.

        Args:
            config: Optional configuration dict. If not provided,
                   loads from ~/.config/meshforge/mqtt_nodeless.json
        """
        self._config = config or self._load_config()
        self._client = None
        self._connected = False
        self._stop_event = threading.Event()
        self._reconnect_thread = None

        # Data storage
        self._nodes: Dict[str, MQTTNode] = {}
        self._messages: deque = deque(maxlen=1000)  # Last 1000 messages
        self._nodes_lock = threading.Lock()
        self._messages_lock = threading.Lock()

        # Callbacks
        self._node_callbacks: List[Callable[[MQTTNode], None]] = []
        self._message_callbacks: List[Callable[[MQTTMessage], None]] = []

        # Stats
        self._stats = {
            "messages_received": 0,
            "nodes_discovered": 0,
            "connect_time": None,
            "last_message_time": None,
        }

    def _load_config(self) -> Dict[str, Any]:
        """Load configuration from file or return defaults."""
        config_path = get_real_user_home() / ".config" / "meshforge" / "mqtt_nodeless.json"

        if config_path.exists():
            try:
                return json.loads(config_path.read_text())
            except Exception as e:
                logger.error(f"Failed to load MQTT nodeless config: {e}")

        # Return defaults
        return {
            "broker": DEFAULT_BROKER,
            "port": DEFAULT_PORT_TLS,
            "username": "",
            "password": "",
            "root_topic": DEFAULT_ROOT_TOPIC,
            "channel": DEFAULT_CHANNEL,
            "key": DEFAULT_KEY,
            "use_tls": True,
            "regions": ["US"],  # Subscribe to these regions
            "auto_reconnect": True,
            "reconnect_delay": 5,
            "max_reconnect_delay": 60,
        }

    def save_config(self) -> bool:
        """Save current configuration to file."""
        config_dir = get_real_user_home() / ".config" / "meshforge"
        config_dir.mkdir(parents=True, exist_ok=True)
        config_path = config_dir / "mqtt_nodeless.json"

        try:
            config_path.write_text(json.dumps(self._config, indent=2))
            return True
        except Exception as e:
            logger.error(f"Failed to save MQTT nodeless config: {e}")
            return False

    def start(self) -> bool:
        """Start the MQTT subscriber."""
        if self._connected:
            return True

        self._stop_event.clear()
        return self._connect()

    def stop(self) -> None:
        """Stop the MQTT subscriber."""
        self._stop_event.set()
        self._disconnect()

    def _connect(self) -> bool:
        """Connect to MQTT broker."""
        try:
            import paho.mqtt.client as mqtt
        except ImportError:
            logger.error("paho-mqtt not installed. Run: pip install paho-mqtt")
            return False

        try:
            # Create client
            self._client = mqtt.Client(
                client_id=f"meshforge_nodeless_{int(time.time())}",
                protocol=mqtt.MQTTv311
            )

            # Set callbacks
            self._client.on_connect = self._on_connect
            self._client.on_disconnect = self._on_disconnect
            self._client.on_message = self._on_message

            # Set credentials if provided
            username = self._config.get("username")
            password = self._config.get("password")
            if username:
                self._client.username_pw_set(username, password)

            # Configure TLS
            if self._config.get("use_tls", True):
                self._setup_tls()

            # Connect
            broker = self._config.get("broker", DEFAULT_BROKER)
            port = self._config.get("port", DEFAULT_PORT_TLS)

            logger.info(f"Connecting to MQTT broker {broker}:{port}")
            self._client.connect(broker, port, keepalive=60)
            self._client.loop_start()

            return True

        except Exception as e:
            logger.error(f"MQTT connection failed: {e}")
            return False

    def _setup_tls(self) -> None:
        """Configure TLS for MQTT connection."""
        if not self._client:
            return

        try:
            context = ssl.create_default_context()
            self._client.tls_set_context(context)
            logger.debug("TLS configured for MQTT")
        except Exception as e:
            logger.warning(f"TLS setup warning: {e}")
            # Fall back to insecure TLS
            try:
                self._client.tls_set(cert_reqs=ssl.CERT_NONE)
                self._client.tls_insecure_set(True)
                logger.warning("Using insecure TLS (cert verification disabled)")
            except Exception as e2:
                logger.error(f"TLS setup failed: {e2}")

    def _disconnect(self) -> None:
        """Disconnect from MQTT broker."""
        if self._client:
            try:
                self._client.loop_stop()
                self._client.disconnect()
            except Exception as e:
                logger.debug(f"Disconnect cleanup: {e}")
            self._client = None
        self._connected = False

    def _on_connect(self, client, userdata, flags, rc):
        """MQTT connection callback."""
        if rc == 0:
            self._connected = True
            self._stats["connect_time"] = datetime.now()
            logger.info("Connected to MQTT broker (nodeless mode)")

            # Subscribe to topics based on configured regions
            self._subscribe_topics()
        else:
            error_msgs = {
                1: "Incorrect protocol version",
                2: "Invalid client identifier",
                3: "Server unavailable",
                4: "Bad username or password",
                5: "Not authorized",
            }
            logger.error(f"MQTT connect failed: {error_msgs.get(rc, f'Error {rc}')}")

    def _subscribe_topics(self) -> None:
        """Subscribe to Meshtastic MQTT topics."""
        if not self._client:
            return

        root = self._config.get("root_topic", DEFAULT_ROOT_TOPIC)
        channel = self._config.get("channel", DEFAULT_CHANNEL)

        # Subscribe to JSON-formatted messages (easier to parse)
        # Topic format: msh/{region}/2/json/{channel}/#
        topic = f"{root.rsplit('/', 1)[0]}/json/{channel}/#"
        self._client.subscribe(topic)
        logger.info(f"Subscribed to: {topic}")

        # Also subscribe to encrypted topic for node discovery
        # Topic format: msh/{region}/2/e/{channel}/#
        enc_topic = f"{root}/{channel}/#"
        self._client.subscribe(enc_topic)
        logger.info(f"Subscribed to: {enc_topic}")

    def _on_disconnect(self, client, userdata, rc):
        """MQTT disconnection callback."""
        self._connected = False

        if rc != 0:
            logger.warning(f"Unexpected MQTT disconnect (rc={rc})")
            if self._config.get("auto_reconnect", True) and not self._stop_event.is_set():
                self._start_reconnect()
        else:
            logger.info("Disconnected from MQTT broker")

    def _start_reconnect(self) -> None:
        """Start reconnection thread."""
        if self._reconnect_thread and self._reconnect_thread.is_alive():
            return

        self._reconnect_thread = threading.Thread(
            target=self._reconnect_loop,
            daemon=True
        )
        self._reconnect_thread.start()

    def _reconnect_loop(self) -> None:
        """Reconnection loop with exponential backoff."""
        delay = self._config.get("reconnect_delay", 5)
        max_delay = self._config.get("max_reconnect_delay", 60)

        while not self._stop_event.is_set():
            logger.info(f"Reconnecting in {delay}s...")
            self._stop_event.wait(delay)

            if self._stop_event.is_set():
                break

            if self._connect():
                logger.info("Reconnection successful")
                break

            delay = min(delay * 1.5, max_delay)

    def _on_message(self, client, userdata, msg):
        """MQTT message callback."""
        try:
            topic = msg.topic
            payload = msg.payload

            self._stats["messages_received"] += 1
            self._stats["last_message_time"] = datetime.now()

            # Try to decode JSON payload
            if "/json/" in topic:
                self._handle_json_message(topic, payload)
            else:
                # Encrypted payload - just track node existence
                self._handle_encrypted_message(topic, payload)

        except Exception as e:
            logger.debug(f"Error processing MQTT message: {e}")

    def _handle_json_message(self, topic: str, payload: bytes) -> None:
        """Handle JSON-formatted Meshtastic message."""
        try:
            data = json.loads(payload.decode('utf-8'))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return

        # Extract node info from sender
        sender = data.get("sender") or data.get("from")
        if sender:
            self._update_node_from_json(sender, data)

        # Handle specific message types
        msg_type = data.get("type", "")

        if msg_type == "nodeinfo":
            self._handle_nodeinfo(data)
        elif msg_type == "position":
            self._handle_position(data)
        elif msg_type == "telemetry":
            self._handle_telemetry(data)
        elif msg_type == "text":
            self._handle_text_message(data)

    def _handle_encrypted_message(self, topic: str, payload: bytes) -> None:
        """Handle encrypted message - just track node existence."""
        # Topic format: msh/US/2/e/LongFast/!abcd1234
        parts = topic.split("/")
        if len(parts) >= 6:
            node_id = parts[-1]
            if node_id.startswith("!"):
                self._ensure_node(node_id)

    def _ensure_node(self, node_id: str) -> MQTTNode:
        """Ensure a node exists in our tracking."""
        with self._nodes_lock:
            if node_id not in self._nodes:
                self._nodes[node_id] = MQTTNode(node_id=node_id)
                self._stats["nodes_discovered"] += 1
            else:
                self._nodes[node_id].last_seen = datetime.now()
            return self._nodes[node_id]

    def _update_node_from_json(self, node_id: str, data: Dict) -> None:
        """Update node info from JSON message."""
        node = self._ensure_node(node_id)

        # Update fields from message
        if "snr" in data:
            node.snr = data["snr"]
        if "rssi" in data:
            node.rssi = data["rssi"]
        if "hop_start" in data:
            node.hop_start = data["hop_start"]
        if "hops_away" in data:
            node.hops_away = data["hops_away"]

        # Notify callbacks
        for callback in self._node_callbacks:
            try:
                callback(node)
            except Exception as e:
                logger.debug(f"Node callback error: {e}")

    def _handle_nodeinfo(self, data: Dict) -> None:
        """Handle nodeinfo message."""
        payload = data.get("payload", {})
        node_id = payload.get("id") or data.get("from")
        if not node_id:
            return

        node = self._ensure_node(node_id)
        node.long_name = payload.get("longname", node.long_name)
        node.short_name = payload.get("shortname", node.short_name)
        node.hardware_model = payload.get("hardware", node.hardware_model)
        node.role = payload.get("role", node.role)

    def _handle_position(self, data: Dict) -> None:
        """Handle position message."""
        payload = data.get("payload", {})
        node_id = data.get("from")
        if not node_id:
            return

        node = self._ensure_node(node_id)

        # Position may be in different formats
        if "latitude_i" in payload:
            node.latitude = payload["latitude_i"] / 1e7
            node.longitude = payload["longitude_i"] / 1e7
        elif "latitude" in payload:
            node.latitude = payload["latitude"]
            node.longitude = payload["longitude"]

        if "altitude" in payload:
            node.altitude = payload["altitude"]

    def _handle_telemetry(self, data: Dict) -> None:
        """Handle telemetry message."""
        payload = data.get("payload", {})
        node_id = data.get("from")
        if not node_id:
            return

        node = self._ensure_node(node_id)

        # Device metrics
        device = payload.get("device_metrics", {})
        if device:
            if "battery_level" in device:
                node.battery_level = device["battery_level"]
            if "voltage" in device:
                node.voltage = device["voltage"]
            if "channel_utilization" in device:
                node.channel_utilization = device["channel_utilization"]
            if "air_util_tx" in device:
                node.air_util_tx = device["air_util_tx"]

    def _handle_text_message(self, data: Dict) -> None:
        """Handle text message."""
        payload = data.get("payload", {})
        text = payload.get("text") or data.get("text", "")
        if not text:
            return

        msg = MQTTMessage(
            message_id=str(data.get("id", time.time())),
            from_id=data.get("from", ""),
            to_id=data.get("to", ""),
            text=text,
            channel=data.get("channel", 0),
            snr=data.get("snr"),
            rssi=data.get("rssi"),
            hop_start=data.get("hop_start"),
        )

        with self._messages_lock:
            self._messages.append(msg)

        # Notify callbacks
        for callback in self._message_callbacks:
            try:
                callback(msg)
            except Exception as e:
                logger.debug(f"Message callback error: {e}")

    # Public API

    def is_connected(self) -> bool:
        """Check if connected to MQTT broker."""
        return self._connected

    def get_nodes(self) -> List[MQTTNode]:
        """Get all discovered nodes."""
        with self._nodes_lock:
            return list(self._nodes.values())

    def get_node(self, node_id: str) -> Optional[MQTTNode]:
        """Get a specific node by ID."""
        with self._nodes_lock:
            return self._nodes.get(node_id)

    def get_online_nodes(self, threshold_minutes: int = 15) -> List[MQTTNode]:
        """Get nodes seen within threshold."""
        with self._nodes_lock:
            return [n for n in self._nodes.values() if n.is_online(threshold_minutes)]

    def get_nodes_with_position(self) -> List[MQTTNode]:
        """Get nodes that have position data."""
        with self._nodes_lock:
            return [n for n in self._nodes.values()
                    if n.latitude is not None and n.longitude is not None]

    def get_messages(self, limit: int = 100) -> List[MQTTMessage]:
        """Get recent messages."""
        with self._messages_lock:
            messages = list(self._messages)
            return messages[-limit:]

    def get_stats(self) -> Dict[str, Any]:
        """Get subscriber statistics."""
        return {
            **self._stats,
            "node_count": len(self._nodes),
            "online_count": len(self.get_online_nodes()),
            "with_position": len(self.get_nodes_with_position()),
            "message_count": len(self._messages),
        }

    def register_node_callback(self, callback: Callable[[MQTTNode], None]) -> None:
        """Register callback for node updates."""
        self._node_callbacks.append(callback)

    def register_message_callback(self, callback: Callable[[MQTTMessage], None]) -> None:
        """Register callback for new messages."""
        self._message_callbacks.append(callback)

    def get_geojson(self) -> Dict:
        """Get nodes as GeoJSON FeatureCollection for mapping."""
        features = []

        for node in self.get_nodes_with_position():
            if node.latitude and node.longitude:
                feature = {
                    "type": "Feature",
                    "geometry": {
                        "type": "Point",
                        "coordinates": [node.longitude, node.latitude]
                    },
                    "properties": {
                        "id": node.node_id,
                        "name": node.long_name or node.short_name or node.node_id,
                        "network": "meshtastic",
                        "is_online": node.is_online(),
                        "via_mqtt": True,
                        "snr": node.snr,
                        "battery": node.battery_level,
                        "last_seen": node.get_age_string(),
                        "hardware": node.hardware_model,
                        "role": node.role,
                    }
                }
                features.append(feature)

        return {"type": "FeatureCollection", "features": features}
