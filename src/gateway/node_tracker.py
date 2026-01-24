"""
Unified Node Tracker for RNS and Meshtastic Networks
Tracks nodes from both networks with position and telemetry data
"""

import threading
import time
import logging
import os
from datetime import datetime
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Callable, Any
from pathlib import Path
import json

logger = logging.getLogger(__name__)

# Import centralized path utility
from utils.paths import get_real_user_home


@dataclass
class Position:
    """Geographic position"""
    latitude: float = 0.0
    longitude: float = 0.0
    altitude: float = 0.0
    precision: int = 5  # decimal places
    timestamp: Optional[datetime] = None

    def is_valid(self) -> bool:
        """Check if position is valid"""
        return (self.latitude != 0.0 or self.longitude != 0.0) and \
               -90 <= self.latitude <= 90 and -180 <= self.longitude <= 180

    def to_dict(self) -> dict:
        return {
            "latitude": round(self.latitude, self.precision),
            "longitude": round(self.longitude, self.precision),
            "altitude": self.altitude,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None
        }


@dataclass
class AirQualityMetrics:
    """Air quality sensor data (e.g., PMSA003I, SCD4X)"""
    pm10_standard: Optional[int] = None   # PM1.0 standard (µg/m³)
    pm25_standard: Optional[int] = None   # PM2.5 standard (µg/m³)
    pm100_standard: Optional[int] = None  # PM10 standard (µg/m³)
    pm10_environmental: Optional[int] = None
    pm25_environmental: Optional[int] = None
    pm100_environmental: Optional[int] = None
    co2: Optional[int] = None             # CO2 in ppm (SCD4X)
    iaq: Optional[int] = None             # Indoor Air Quality index
    gas_resistance: Optional[float] = None  # BME680 gas sensor
    timestamp: Optional[datetime] = None

    def to_dict(self) -> dict:
        return {k: v for k, v in {
            "pm10_standard": self.pm10_standard,
            "pm25_standard": self.pm25_standard,
            "pm100_standard": self.pm100_standard,
            "pm10_environmental": self.pm10_environmental,
            "pm25_environmental": self.pm25_environmental,
            "pm100_environmental": self.pm100_environmental,
            "co2": self.co2,
            "iaq": self.iaq,
            "gas_resistance": self.gas_resistance,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None
        }.items() if v is not None}

    def has_data(self) -> bool:
        """Check if any air quality data is present"""
        return any([self.pm25_standard, self.co2, self.iaq, self.gas_resistance])


@dataclass
class HealthMetrics:
    """Health sensor data (heart rate, SpO2, temperature)"""
    heart_rate: Optional[int] = None      # BPM
    spo2: Optional[int] = None            # Blood oxygen saturation %
    body_temperature: Optional[float] = None  # Celsius
    timestamp: Optional[datetime] = None

    def to_dict(self) -> dict:
        return {k: v for k, v in {
            "heart_rate": self.heart_rate,
            "spo2": self.spo2,
            "body_temperature": self.body_temperature,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None
        }.items() if v is not None}


@dataclass
class DetectionSensor:
    """Detection sensor state (motion, reed switch, etc.)"""
    name: str = ""                        # Sensor name (e.g., "Motion", "Door")
    triggered: bool = False               # Current state
    gpio_pin: Optional[int] = None        # Monitored GPIO pin
    triggered_high: bool = True           # Whether HIGH means triggered
    last_triggered: Optional[datetime] = None
    trigger_count: int = 0                # Number of triggers since reset

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "triggered": self.triggered,
            "gpio_pin": self.gpio_pin,
            "triggered_high": self.triggered_high,
            "last_triggered": self.last_triggered.isoformat() if self.last_triggered else None,
            "trigger_count": self.trigger_count
        }


@dataclass
class Telemetry:
    """
    Complete node telemetry data.

    Supports all Meshtastic telemetry types:
    - Device metrics (battery, voltage, channel utilization, airtime)
    - Environment metrics (temperature, humidity, pressure from BME280/BME680)
    - Air quality metrics (PM2.5, CO2 from PMSA003I, SCD4X)
    - Health metrics (heart rate, SpO2)
    - Detection sensors (motion, door sensors)

    Reference: https://meshtastic.org/docs/configuration/module/telemetry/
    """
    # Device Metrics
    battery_level: Optional[int] = None   # 0-100%
    voltage: Optional[float] = None       # Battery voltage
    channel_utilization: Optional[float] = None  # 0-100% (how busy the channel is)
    air_util_tx: Optional[float] = None   # TX airtime utilization %
    uptime: Optional[int] = None          # Uptime in seconds

    # Environment Metrics (BME280, BME680, BMP280, etc.)
    temperature: Optional[float] = None   # Celsius
    humidity: Optional[float] = None      # 0-100%
    pressure: Optional[float] = None      # hPa (barometric pressure)
    gas_resistance: Optional[float] = None  # Ohms (BME680 VOC sensor)

    # Air Quality (PMSA003I, SCD4X)
    air_quality: Optional[AirQualityMetrics] = None

    # Health Metrics
    health: Optional[HealthMetrics] = None

    # Detection Sensors
    detection_sensors: List[DetectionSensor] = field(default_factory=list)

    timestamp: Optional[datetime] = None

    def to_dict(self) -> dict:
        result = {k: v for k, v in {
            "battery_level": self.battery_level,
            "voltage": self.voltage,
            "channel_utilization": self.channel_utilization,
            "air_util_tx": self.air_util_tx,
            "uptime": self.uptime,
            "temperature": self.temperature,
            "humidity": self.humidity,
            "pressure": self.pressure,
            "gas_resistance": self.gas_resistance,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None
        }.items() if v is not None}

        if self.air_quality and self.air_quality.has_data():
            result["air_quality"] = self.air_quality.to_dict()

        if self.health:
            result["health"] = self.health.to_dict()

        if self.detection_sensors:
            result["detection_sensors"] = [s.to_dict() for s in self.detection_sensors]

        return result

    def has_environment_sensors(self) -> bool:
        """Check if node has environment sensors"""
        return any([self.temperature, self.humidity, self.pressure, self.gas_resistance])

    def has_air_quality_sensors(self) -> bool:
        """Check if node has air quality sensors"""
        return self.air_quality is not None and self.air_quality.has_data()

    def has_detection_sensors(self) -> bool:
        """Check if node has detection sensors"""
        return len(self.detection_sensors) > 0

    def get_sensor_summary(self) -> str:
        """Get a summary of available sensor data"""
        parts = []
        if self.battery_level is not None:
            parts.append(f"🔋{self.battery_level}%")
        if self.temperature is not None:
            parts.append(f"🌡️{self.temperature:.1f}°C")
        if self.humidity is not None:
            parts.append(f"💧{self.humidity:.0f}%")
        if self.air_quality and self.air_quality.pm25_standard:
            parts.append(f"AQI:{self.air_quality.pm25_standard}")
        if self.detection_sensors:
            triggered = sum(1 for s in self.detection_sensors if s.triggered)
            parts.append(f"📡{triggered}/{len(self.detection_sensors)}")
        return " ".join(parts) if parts else "No data"


@dataclass
class UnifiedNode:
    """Represents a node from either RNS or Meshtastic network"""
    # Core identity
    id: str  # Unified identifier (network prefix + hash/id)
    network: str  # "meshtastic", "rns", or "both"
    name: str = ""
    short_name: str = ""

    # Position and telemetry
    position: Position = field(default_factory=Position)
    telemetry: Telemetry = field(default_factory=Telemetry)

    # Network-specific identifiers
    meshtastic_id: Optional[str] = None  # !abcd1234
    rns_hash: Optional[bytes] = None  # 16-byte destination hash

    # Radio metrics
    snr: Optional[float] = None
    rssi: Optional[int] = None
    hops: Optional[int] = None

    # Status
    is_online: bool = False
    is_gateway: bool = False
    is_local: bool = False  # Is this our own node
    last_seen: Optional[datetime] = None
    first_seen: Optional[datetime] = None

    # Hardware info
    hardware_model: Optional[str] = None
    firmware_version: Optional[str] = None
    role: Optional[str] = None

    def __post_init__(self):
        if self.first_seen is None:
            self.first_seen = datetime.now()

    def update_seen(self):
        """Update last seen timestamp"""
        self.last_seen = datetime.now()
        self.is_online = True

    def get_age_string(self) -> str:
        """Get human-readable time since last seen"""
        if not self.last_seen:
            return "Never"

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

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization"""
        return {
            "id": self.id,
            "network": self.network,
            "name": self.name,
            "short_name": self.short_name,
            "position": self.position.to_dict() if self.position.is_valid() else None,
            "telemetry": self.telemetry.to_dict(),
            "meshtastic_id": self.meshtastic_id,
            "rns_hash": self.rns_hash.hex() if self.rns_hash else None,
            "snr": self.snr,
            "rssi": self.rssi,
            "hops": self.hops,
            "is_online": self.is_online,
            "is_gateway": self.is_gateway,
            "is_local": self.is_local,
            "last_seen": self.last_seen.isoformat() if self.last_seen else None,
            "last_seen_ago": self.get_age_string(),
            "hardware_model": self.hardware_model,
            "firmware_version": self.firmware_version,
            "role": self.role,
        }

    @classmethod
    def from_meshtastic(cls, mesh_node: dict, is_local: bool = False) -> 'UnifiedNode':
        """Create from Meshtastic node data"""
        node_id = mesh_node.get('num', 0)
        user = mesh_node.get('user', {})
        position = mesh_node.get('position', {})
        metrics = mesh_node.get('deviceMetrics', {})

        meshtastic_id = f"!{node_id:08x}"

        node = cls(
            id=f"mesh_{meshtastic_id}",
            network="meshtastic",
            name=user.get('longName', meshtastic_id),
            short_name=user.get('shortName', ''),
            meshtastic_id=meshtastic_id,
            is_local=is_local,
            hardware_model=user.get('hwModel'),
            role=user.get('role'),
        )

        # Position
        if position:
            node.position = Position(
                latitude=position.get('latitude', 0) or 0,
                longitude=position.get('longitude', 0) or 0,
                altitude=position.get('altitude', 0) or 0,
                timestamp=datetime.now()
            )

        # Telemetry
        if metrics:
            node.telemetry = Telemetry(
                battery_level=metrics.get('batteryLevel'),
                voltage=metrics.get('voltage'),
                uptime=metrics.get('uptimeSeconds'),
                timestamp=datetime.now()
            )

        # Radio metrics
        node.snr = mesh_node.get('snr')
        node.hops = mesh_node.get('hopsAway')
        node.last_seen = datetime.now()

        return node

    @classmethod
    def from_rns(cls, rns_hash: bytes, name: str = "", app_data: bytes = None) -> 'UnifiedNode':
        """Create from RNS announce/discovery data"""
        hash_hex = rns_hash.hex()

        node = cls(
            id=f"rns_{hash_hex[:16]}",
            network="rns",
            name=name or hash_hex[:8],
            short_name=hash_hex[:4].upper(),
            rns_hash=rns_hash,
        )

        # Parse app_data if available (may contain name, position, etc.)
        if app_data:
            try:
                # LXMF announces include display name
                if len(app_data) > 0:
                    # First byte might be display name length
                    # This varies by application
                    pass
            except Exception as e:
                logger.debug(f"Could not parse RNS app_data: {e}")

        node.last_seen = datetime.now()
        return node


class UnifiedNodeTracker:
    """
    Tracks nodes from both RNS and Meshtastic networks.
    Provides unified view for map display and monitoring.
    """

    OFFLINE_THRESHOLD = 3600  # 1 hour
    MAX_NODES = 10000  # Prevent unbounded memory growth

    @classmethod
    def get_cache_file(cls) -> Path:
        """Get the cache file path (evaluated at runtime, not import time)"""
        return get_real_user_home() / ".config" / "meshforge" / "node_cache.json"

    def __init__(self):
        self._nodes: Dict[str, UnifiedNode] = {}
        self._lock = threading.RLock()
        self._callbacks: List[Callable] = []
        self._running = False
        self._stop_event = threading.Event()
        self._cleanup_thread = None
        self._rns_thread = None
        self._reticulum = None
        self._rns_connected = False

        # Load cached nodes
        self._load_cache()

    def start(self):
        """Start the node tracker"""
        self._running = True
        self._stop_event.clear()
        self._cleanup_thread = threading.Thread(target=self._cleanup_loop, daemon=True)
        self._cleanup_thread.start()

        # Initialize RNS in the main thread to avoid signal handler issues
        # RNS.Reticulum() sets up signal handlers which only work in main thread
        self._init_rns_main_thread()

        logger.info("Node tracker started")

    def _init_rns_main_thread(self):
        """Initialize RNS from main thread, then start background listener.

        IMPORTANT: MeshForge operates as a CLIENT ONLY - it connects to existing
        rnsd/NomadNet instances but never creates its own RNS instance that would
        bind interfaces and conflict with NomadNet or other RNS services.

        NOTE: RNS.Reticulum() uses signal handlers which ONLY work in the main
        thread. If called from a background thread, it will fail with:
        "signal only works in main thread of the main interpreter"
        """
        # Check if we're in the main thread - RNS signal handlers require it
        import threading as _threading
        current = _threading.current_thread()
        main = _threading.main_thread()
        is_main = current is main
        logger.info(f"Thread check: current={current.name}, main={main.name}, is_main={is_main}")

        if not is_main:
            logger.warning("RNS initialization must be in main thread - skipping node discovery")
            logger.info("RNS node discovery disabled (call start() from main thread to enable)")
            self._rns_connected = False
            return

        try:
            import RNS
            logger.info("Checking for existing RNS service...")

            # Check if rnsd is already running
            from utils.gateway_diagnostic import find_rns_processes
            rns_pids = find_rns_processes()

            if not rns_pids:
                # No rnsd running - DO NOT initialize our own RNS instance
                # This would bind AutoInterface port and block NomadNet from starting
                logger.info("No rnsd detected - skipping RNS node discovery")
                logger.info("To enable RNS features, start rnsd first: sudo systemctl start rnsd")
                logger.info("MeshForge will operate without RNS node tracking")
                self._rns_connected = False
                return

            # rnsd is running - connect to existing instance as CLIENT ONLY
            logger.info(f"rnsd detected (PID: {rns_pids[0]}), connecting as client...")
            try:
                # Create a client-only config to avoid interface conflicts
                # This prevents RNS from trying to bind ports that rnsd already owns
                import tempfile
                client_config_dir = Path(tempfile.gettempdir()) / "meshforge_rns_client"
                client_config_dir.mkdir(exist_ok=True)
                client_config_file = client_config_dir / "config"

                # Write minimal client-only config (no interfaces, just shared transport)
                client_config_file.write_text("""# MeshForge RNS Client Config (auto-generated)
# This config connects to existing rnsd without creating interfaces

[reticulum]
share_instance = Yes
shared_instance_port = 37428
instance_control_port = 37429
""")

                # Connect using client-only config
                self._reticulum = RNS.Reticulum(configdir=str(client_config_dir))
                self._rns_connected = True
                logger.info("Connected to existing rnsd instance")

                # Register announce handler to receive node announcements
                class NodeAnnounceHandler:
                    def __init__(self, tracker):
                        self.tracker = tracker
                        self.aspect_filter = None

                    def received_announce(self, destination_hash, announced_identity, app_data):
                        try:
                            self.tracker._on_rns_announce(destination_hash, announced_identity, app_data)
                        except Exception as e:
                            logger.error(f"Error handling RNS announce: {e}")

                RNS.Transport.register_announce_handler(NodeAnnounceHandler(self))
                logger.info("Registered announce handler with rnsd")

                # Load known destinations from rnsd (may be empty initially)
                self._load_known_rns_destinations(RNS)

                # Store RNS module reference for background loop
                self._rns_module = RNS

                # Start background loop (will re-check path_table periodically)
                self._rns_thread = threading.Thread(target=self._rns_loop, daemon=True)
                self._rns_thread.start()

                # Schedule delayed re-check after 5 seconds for sync'd data
                def delayed_check():
                    import time
                    time.sleep(5)
                    if self._running and self._rns_connected:
                        logger.debug("Running delayed RNS destination check...")
                        self._load_known_rns_destinations(RNS)

                threading.Thread(target=delayed_check, daemon=True).start()

            except Exception as e:
                logger.warning(f"Could not connect to rnsd: {e}")
                logger.info("RNS nodes may not appear on map - ensure rnsd is running properly")
                self._rns_connected = False

        except ImportError:
            logger.info("RNS module not installed. To enable RNS node discovery:")
            logger.info("  1. Install RNS: pip install rns")
            logger.info("  2. Start rnsd: sudo systemctl start rnsd")
            logger.info("  3. Restart MeshForge")
        except Exception as e:
            logger.warning(f"Failed to initialize RNS discovery: {e}")
            self._rns_connected = False

    def _rns_loop(self):
        """Background loop for RNS - periodically check for new destinations.

        When connected as a shared instance client, the path_table may not
        be populated immediately. This loop periodically checks for new
        destinations that rnsd has discovered.
        """
        import time
        import RNS

        check_interval = 30  # Check every 30 seconds
        last_check = 0

        while self._running:
            if self._stop_event.wait(1):
                break

            # Periodic check for new RNS destinations
            current_time = time.time()
            if current_time - last_check >= check_interval:
                last_check = current_time
                try:
                    # Re-check path_table for newly discovered routes
                    new_count = 0
                    if hasattr(RNS.Transport, 'path_table') and RNS.Transport.path_table:
                        for dest_hash, path_data in RNS.Transport.path_table.items():
                            try:
                                if isinstance(dest_hash, bytes) and len(dest_hash) == 16:
                                    node_id = f"rns_{dest_hash.hex()[:16]}"
                                    if node_id not in self._nodes:
                                        hops = 0
                                        if isinstance(path_data, tuple) and len(path_data) > 1:
                                            hops = path_data[1]
                                        node = UnifiedNode.from_rns(dest_hash, name="", app_data=None)
                                        self.add_node(node)
                                        new_count += 1
                                        logger.debug(f"Discovered RNS destination: {dest_hash.hex()[:8]} ({hops} hops)")
                            except Exception as e:
                                logger.debug(f"Error processing path_table entry: {e}")

                    if new_count > 0:
                        logger.info(f"Discovered {new_count} new RNS destinations from path_table")

                except Exception as e:
                    logger.debug(f"Error checking path_table: {e}")

    def stop(self, timeout: float = 5.0):
        """Stop the node tracker and wait for threads to finish

        Args:
            timeout: Seconds to wait for each thread to finish
        """
        logger.info("Stopping node tracker...")
        self._running = False
        self._stop_event.set()

        # Wait for cleanup thread to finish
        if hasattr(self, '_cleanup_thread') and self._cleanup_thread and self._cleanup_thread.is_alive():
            self._cleanup_thread.join(timeout=timeout)
            if self._cleanup_thread.is_alive():
                logger.warning("Cleanup thread did not stop in time")

        # Wait for RNS thread to finish
        if hasattr(self, '_rns_thread') and self._rns_thread and self._rns_thread.is_alive():
            self._rns_thread.join(timeout=timeout)
            if self._rns_thread.is_alive():
                logger.warning("RNS thread did not stop in time")

        self._save_cache()
        logger.info("Node tracker stopped")

    def add_node(self, node: UnifiedNode):
        """Add or update a node"""
        with self._lock:
            existing = self._nodes.get(node.id)
            if existing:
                # Merge data
                self._merge_node(existing, node)
            else:
                # Evict oldest offline nodes if at capacity
                if len(self._nodes) >= self.MAX_NODES:
                    self._evict_stale_nodes()
                self._nodes[node.id] = node
                logger.debug(f"Added new node: {node.id} ({node.name})")

            self._notify_callbacks("update", node)

    def _evict_stale_nodes(self):
        """Evict oldest offline nodes to stay within MAX_NODES. Called under _lock."""
        offline = [
            (nid, n) for nid, n in self._nodes.items()
            if not n.is_online
        ]
        if not offline:
            # All online — evict oldest by last_seen
            offline = list(self._nodes.items())

        # Sort by last_seen ascending (oldest first)
        offline.sort(key=lambda x: x[1].last_seen or datetime.min)

        # Evict 10% to avoid frequent evictions
        evict_count = max(1, len(self._nodes) // 10)
        for nid, _ in offline[:evict_count]:
            del self._nodes[nid]

        if evict_count > 0:
            logger.info(f"Evicted {evict_count} stale nodes (capacity: {self.MAX_NODES})")

    def remove_node(self, node_id: str):
        """Remove a node"""
        with self._lock:
            if node_id in self._nodes:
                node = self._nodes.pop(node_id)
                self._notify_callbacks("remove", node)
                logger.debug(f"Removed node: {node_id}")

    def get_node(self, node_id: str) -> Optional[UnifiedNode]:
        """Get a node by ID"""
        with self._lock:
            return self._nodes.get(node_id)

    def get_all_nodes(self) -> List[UnifiedNode]:
        """Get all tracked nodes"""
        with self._lock:
            return list(self._nodes.values())

    def get_meshtastic_nodes(self) -> List[UnifiedNode]:
        """Get only Meshtastic nodes"""
        with self._lock:
            return [n for n in self._nodes.values()
                    if n.network in ("meshtastic", "both")]

    def get_rns_nodes(self) -> List[UnifiedNode]:
        """Get only RNS nodes"""
        with self._lock:
            return [n for n in self._nodes.values()
                    if n.network in ("rns", "both")]

    def get_node_by_mesh_id(self, meshtastic_id: str) -> Optional[UnifiedNode]:
        """Get a node by its Meshtastic ID (e.g., !abcd1234)"""
        with self._lock:
            for node in self._nodes.values():
                if node.meshtastic_id == meshtastic_id:
                    return node
            return None

    def get_node_by_rns_hash(self, rns_hash: bytes) -> Optional[UnifiedNode]:
        """Get a node by its RNS destination hash"""
        with self._lock:
            for node in self._nodes.values():
                if node.rns_hash == rns_hash:
                    return node
            return None

    def get_nodes_with_position(self) -> List[UnifiedNode]:
        """Get nodes that have valid positions"""
        with self._lock:
            return [n for n in self._nodes.values()
                    if n.position and n.position.is_valid()]

    def get_online_nodes(self) -> List[UnifiedNode]:
        """Get online nodes only"""
        with self._lock:
            return [n for n in self._nodes.values() if n.is_online]

    def get_stats(self) -> dict:
        """Get tracker statistics"""
        with self._lock:
            nodes = list(self._nodes.values())
            return {
                "total": len(nodes),
                "meshtastic": sum(1 for n in nodes if n.network in ("meshtastic", "both")),
                "rns": sum(1 for n in nodes if n.network in ("rns", "both")),
                "online": sum(1 for n in nodes if n.is_online),
                "with_position": sum(1 for n in nodes if n.position and n.position.is_valid()),
                "gateways": sum(1 for n in nodes if n.is_gateway),
            }

    def register_callback(self, callback: Callable):
        """Register a callback for node updates"""
        with self._lock:
            self._callbacks.append(callback)

    def unregister_callback(self, callback: Callable):
        """Unregister a callback"""
        with self._lock:
            if callback in self._callbacks:
                self._callbacks.remove(callback)

    def _merge_node(self, existing: UnifiedNode, new: UnifiedNode):
        """Merge new node data into existing node"""
        # Update network type if we see it on both
        if existing.network != new.network:
            existing.network = "both"

        # Update identifiers
        if new.meshtastic_id:
            existing.meshtastic_id = new.meshtastic_id
        if new.rns_hash:
            existing.rns_hash = new.rns_hash

        # Update name if we have a better one
        if new.name and (not existing.name or existing.name.startswith("!")):
            existing.name = new.name
        if new.short_name:
            existing.short_name = new.short_name

        # Update position if newer
        if new.position.is_valid():
            existing.position = new.position

        # Update telemetry if newer
        if new.telemetry.timestamp:
            existing.telemetry = new.telemetry

        # Update metrics
        if new.snr is not None:
            existing.snr = new.snr
        if new.rssi is not None:
            existing.rssi = new.rssi
        if new.hops is not None:
            existing.hops = new.hops

        # Update hardware info
        if new.hardware_model:
            existing.hardware_model = new.hardware_model
        if new.firmware_version:
            existing.firmware_version = new.firmware_version
        if new.role:
            existing.role = new.role

        # Update status
        existing.is_gateway = existing.is_gateway or new.is_gateway
        existing.update_seen()

    def _notify_callbacks(self, event: str, node: UnifiedNode):
        """Notify registered callbacks"""
        for callback in self._callbacks:
            try:
                callback(event, node)
            except Exception as e:
                logger.error(f"Callback error: {e}")

    def _cleanup_loop(self):
        """Periodically mark offline nodes and save cache"""
        while self._running:
            if self._stop_event.wait(60):
                break

            with self._lock:
                now = datetime.now()
                for node in self._nodes.values():
                    if node.last_seen:
                        age = (now - node.last_seen).total_seconds()
                        if age > self.OFFLINE_THRESHOLD:
                            node.is_online = False

            # Save cache every 5 minutes
            self._save_cache()

    def _load_cache(self):
        """Load node cache from file"""
        cache_file = self.get_cache_file()
        if not cache_file.exists():
            return

        try:
            with open(cache_file, 'r') as f:
                data = json.load(f)

            for node_data in data.get('nodes', []):
                node = UnifiedNode(
                    id=node_data['id'],
                    network=node_data['network'],
                    name=node_data.get('name', ''),
                    short_name=node_data.get('short_name', ''),
                    meshtastic_id=node_data.get('meshtastic_id'),
                    rns_hash=bytes.fromhex(node_data['rns_hash']) if node_data.get('rns_hash') else None,
                    hardware_model=node_data.get('hardware_model'),
                    role=node_data.get('role'),
                    is_online=False,  # Assume offline until we hear from them
                )
                # Restore last_seen from cache
                if node_data.get('last_seen'):
                    try:
                        node.last_seen = datetime.fromisoformat(node_data['last_seen'])
                    except (ValueError, TypeError):
                        pass
                # Restore position from cache
                pos_data = node_data.get('position')
                if pos_data and isinstance(pos_data, dict):
                    node.position = Position(
                        latitude=pos_data.get('latitude', 0.0),
                        longitude=pos_data.get('longitude', 0.0),
                        altitude=pos_data.get('altitude', 0.0),
                    )
                self._nodes[node.id] = node

            logger.info(f"Loaded {len(self._nodes)} nodes from cache")

        except Exception as e:
            logger.warning(f"Failed to load node cache: {e}")

    def _save_cache(self):
        """Save node cache to file"""
        try:
            cache_file = self.get_cache_file()
            cache_file.parent.mkdir(parents=True, exist_ok=True)

            with self._lock:
                nodes_data = [n.to_dict() for n in self._nodes.values()]

            cache_data = {
                'version': 1,
                'saved_at': datetime.now().isoformat(),
                'nodes': nodes_data
            }

            from utils.paths import atomic_write_text
            atomic_write_text(cache_file, json.dumps(cache_data, indent=2))

            # Also save to /tmp for web API access (cross-process sharing)
            try:
                tmp_path = '/tmp/meshforge_rns_nodes.json'
                if os.path.islink(tmp_path):
                    logger.warning(f"Refusing to write to symlink: {tmp_path}")
                else:
                    fd = os.open(
                        tmp_path,
                        os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW,
                        0o644
                    )
                    with os.fdopen(fd, 'w') as f:
                        json.dump(cache_data, f)
            except Exception as e:
                logger.debug(f"Could not save web API cache: {e}")

        except Exception as e:
            logger.warning(f"Failed to save node cache: {e}")

    def to_geojson(self) -> dict:
        """Export nodes as GeoJSON for map display"""
        features = []

        for node in self.get_nodes_with_position():
            feature = {
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [
                        node.position.longitude,
                        node.position.latitude
                    ]
                },
                "properties": {
                    "id": node.id,
                    "name": node.name,
                    "network": node.network,
                    "is_online": node.is_online,
                    "is_local": node.is_local,
                    "is_gateway": node.is_gateway,
                    "snr": node.snr,
                    "battery": node.telemetry.battery_level,
                    "last_seen": node.get_age_string(),
                }
            }
            features.append(feature)

        return {
            "type": "FeatureCollection",
            "features": features
        }

    def _load_known_rns_destinations(self, RNS):
        """Load known destinations from RNS path table and identity cache.

        Priority order (most complete first):
        1. RNS.Transport.path_table - complete routing table from rnsd
        2. RNS.Identity.known_destinations - cached identities
        3. RNS.Transport.destinations - local destinations only (fallback)
        """
        try:
            known_count = 0

            # PRIMARY: Check path_table - contains ALL destinations rnsd knows about
            # This is the complete routing table, updated in real-time
            if hasattr(RNS.Transport, 'path_table') and RNS.Transport.path_table:
                for dest_hash, path_data in RNS.Transport.path_table.items():
                    try:
                        if isinstance(dest_hash, bytes) and len(dest_hash) == 16:
                            node_id = f"rns_{dest_hash.hex()[:16]}"
                            if node_id not in self._nodes:
                                # Extract hop count from path tuple if available
                                hops = 0
                                if isinstance(path_data, tuple) and len(path_data) > 1:
                                    hops = path_data[1]

                                node = UnifiedNode.from_rns(dest_hash, name="", app_data=None)
                                # Store hop count for later use
                                if hasattr(node, 'hops'):
                                    node.hops = hops
                                self.add_node(node)
                                known_count += 1
                                logger.debug(f"Loaded from path_table: {dest_hash.hex()[:8]} ({hops} hops)")
                    except Exception as e:
                        logger.debug(f"Error loading from path_table: {e}")

            # SECONDARY: Check identity known destinations (for any missed in path_table)
            if hasattr(RNS.Identity, 'known_destinations') and RNS.Identity.known_destinations:
                known_dests = RNS.Identity.known_destinations
                # Handle both dict (hash->identity) and list (hashes) formats
                if isinstance(known_dests, dict):
                    dest_hashes = known_dests.keys()
                else:
                    dest_hashes = known_dests

                for dest_hash in dest_hashes:
                    try:
                        if isinstance(dest_hash, bytes) and len(dest_hash) == 16:
                            node_id = f"rns_{dest_hash.hex()[:16]}"
                            if node_id not in self._nodes:
                                node = UnifiedNode.from_rns(dest_hash, name="", app_data=None)
                                self.add_node(node)
                                known_count += 1
                                logger.debug(f"Loaded from known_destinations: {dest_hash.hex()[:8]}")
                    except Exception as e:
                        logger.debug(f"Error loading known identity: {e}")

            # TERTIARY: Check Transport.destinations (local only - least useful)
            if hasattr(RNS.Transport, 'destinations') and RNS.Transport.destinations:
                destinations = RNS.Transport.destinations
                if isinstance(destinations, dict):
                    dest_items = destinations.values()
                elif isinstance(destinations, list):
                    dest_items = destinations
                else:
                    dest_items = []

                for dest in dest_items:
                    try:
                        if hasattr(dest, 'hash'):
                            node_id = f"rns_{dest.hash.hex()[:16]}"
                            if node_id not in self._nodes:
                                node = UnifiedNode.from_rns(dest.hash, name="", app_data=None)
                                self.add_node(node)
                                known_count += 1
                    except Exception as e:
                        logger.debug(f"Error loading destination: {e}")

            if known_count > 0:
                logger.info(f"Loaded {known_count} known RNS destinations")
            else:
                logger.debug("No known RNS destinations found (path_table may be empty)")

        except Exception as e:
            logger.debug(f"Could not load known RNS destinations: {e}")

    def _on_rns_announce(self, dest_hash, announced_identity, app_data):
        """Handle RNS announce for node discovery"""
        try:
            # Parse display name from app_data if available
            display_name = ""
            if app_data:
                try:
                    # LXMF announces typically include display name
                    # Try to decode as UTF-8 string
                    display_name = app_data.decode('utf-8', errors='ignore').strip()
                    # Clean up - remove non-printable characters
                    display_name = ''.join(c for c in display_name if c.isprintable())
                except Exception as e:
                    logger.debug(f"Could not decode RNS display name: {e}")

            # Create node from announce
            node = UnifiedNode.from_rns(dest_hash, name=display_name, app_data=app_data)
            self.add_node(node)

            hash_short = dest_hash.hex()[:8]
            logger.info(f"Discovered RNS node: {hash_short} ({display_name or 'unnamed'})")

        except Exception as e:
            logger.error(f"Error processing RNS announce: {e}")
