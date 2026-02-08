"""
PSKReporter MQTT Subscriber for MeshForge.

Connects to the public PSKReporter MQTT feed (mqtt.pskreporter.info) to receive
real-time amateur radio reception reports. Provides band activity, spot statistics,
and propagation data for the MeshForge NOC.

PSKReporter MQTT feed provided by M0LTE:
    Broker: mqtt.pskreporter.info
    Ports: 1883 (plain), 1884 (TLS)
    Topics: pskr/filter/v2/{band}/{mode}/{sendercall}/{receivercall}/
            {senderlocator}/{receiverlocator}/{sendercountry}/{receivercountry}

JSON payload fields:
    sq  - Sequence number
    f   - Frequency (Hz)
    md  - Mode (FT8, FT4, CW, etc.)
    rp  - Report (dB SNR)
    t   - Timestamp (RX, Unix epoch)
    sc  - Sender callsign
    sl  - Sender locator (Maidenhead)
    rc  - Receiver callsign
    rl  - Receiver locator (Maidenhead)
    sa  - Sender ADIF country code
    ra  - Receiver ADIF country code
    b   - Band (e.g., "20m", "40m")

Usage:
    from monitoring.pskreporter_subscriber import PSKReporterSubscriber

    sub = PSKReporterSubscriber()
    sub.start()

    # Get recent spots
    spots = sub.get_spots(limit=50)

    # Get band activity summary
    activity = sub.get_band_activity()

    # Get stats
    stats = sub.get_stats()
"""

import json
import logging
import ssl
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

# Import centralized path utility for sudo compatibility
from utils.paths import get_real_user_home

logger = logging.getLogger(__name__)

# PSKReporter MQTT broker defaults
PSKR_BROKER = "mqtt.pskreporter.info"
PSKR_PORT = 1883          # Plain MQTT
PSKR_PORT_TLS = 1884      # MQTT + TLS
PSKR_BASE_TOPIC = "pskr/filter/v2"

# Limits
MAX_SPOTS = 5000           # Ring buffer size
MAX_PAYLOAD_BYTES = 16384  # 16 KB max per message (spots are small)
SPOT_RETENTION_HOURS = 4   # Keep spots for 4 hours

# Common HF + VHF amateur bands
KNOWN_BANDS = {
    "160m", "80m", "60m", "40m", "30m", "20m", "17m",
    "15m", "12m", "10m", "6m", "2m", "70cm",
}

# Common digital modes
KNOWN_MODES = {
    "FT8", "FT4", "CW", "WSPR", "JS8", "PSK31", "RTTY",
    "JT65", "JT9", "MSK144", "Q65",
}


@dataclass
class PSKSpot:
    """A single PSKReporter reception report."""
    sequence: int = 0
    frequency: int = 0          # Hz
    mode: str = ""              # FT8, FT4, CW, etc.
    snr: int = 0                # dB
    timestamp: int = 0          # Unix epoch (RX time)
    sender_call: str = ""       # TX station callsign
    sender_locator: str = ""    # TX Maidenhead grid
    receiver_call: str = ""     # RX station callsign
    receiver_locator: str = ""  # RX Maidenhead grid
    sender_country: int = 0     # ADIF country code
    receiver_country: int = 0   # ADIF country code
    band: str = ""              # e.g., "20m"
    received_at: float = field(default_factory=time.time)

    @property
    def frequency_mhz(self) -> float:
        """Frequency in MHz."""
        return self.frequency / 1_000_000 if self.frequency else 0.0

    @property
    def age_seconds(self) -> float:
        """Seconds since this spot was received."""
        return time.time() - self.received_at


class PSKReporterSubscriber:
    """
    MQTT subscriber for PSKReporter spot feed.

    Connects to mqtt.pskreporter.info and receives real-time
    amateur radio reception reports for propagation monitoring.
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """
        Initialize PSKReporter subscriber.

        Args:
            config: Optional configuration dict. Keys:
                broker: MQTT broker hostname (default: mqtt.pskreporter.info)
                port: MQTT port (default: 1883)
                use_tls: Use TLS (default: False)
                callsign: Filter to specific callsign (default: None = all)
                bands: List of bands to monitor (default: all)
                modes: List of modes to monitor (default: all)
                max_spots: Ring buffer size (default: 5000)
        """
        self._config = config or self._load_config()
        self._client = None
        self._connected = False
        self._stop_event = threading.Event()
        self._reconnect_thread = None

        # Spot storage (ring buffer)
        max_spots = self._config.get("max_spots", MAX_SPOTS)
        self._spots: deque = deque(maxlen=max_spots)
        self._spots_lock = threading.Lock()

        # Band activity tracking
        self._band_activity: Dict[str, Dict[str, Any]] = {}
        self._band_lock = threading.Lock()

        # Callbacks
        self._spot_callbacks: List[Callable[[PSKSpot], None]] = []

        # Stats
        self._stats_lock = threading.Lock()
        self._stats = {
            "spots_received": 0,
            "spots_rejected": 0,
            "connect_time": None,
            "last_spot_time": None,
            "reconnect_attempts": 0,
            "last_disconnect_reason": "",
            "bands_active": 0,
            "modes_seen": set(),
        }

    def _load_config(self) -> Dict[str, Any]:
        """Load configuration from file or return defaults."""
        config_path = (
            get_real_user_home() / ".config" / "meshforge" / "pskreporter.json"
        )
        if config_path.exists():
            try:
                return json.loads(config_path.read_text())
            except Exception as e:
                logger.error(f"Failed to load PSKReporter config: {e}")

        return {
            "broker": PSKR_BROKER,
            "port": PSKR_PORT,
            "use_tls": False,
            "callsign": "",       # Empty = monitor all spots
            "bands": [],          # Empty = all bands
            "modes": [],          # Empty = all modes
            "max_spots": MAX_SPOTS,
            "enabled": False,
            "auto_reconnect": True,
            "reconnect_delay": 5,
            "max_reconnect_delay": 60,
        }

    def save_config(self) -> bool:
        """Save current configuration to file."""
        config_dir = get_real_user_home() / ".config" / "meshforge"
        config_dir.mkdir(parents=True, exist_ok=True)
        config_path = config_dir / "pskreporter.json"

        try:
            # Convert sets to lists for JSON serialization
            save_config = dict(self._config)
            if isinstance(save_config.get("modes"), set):
                save_config["modes"] = list(save_config["modes"])
            config_path.write_text(json.dumps(save_config, indent=2))
            return True
        except Exception as e:
            logger.error(f"Failed to save PSKReporter config: {e}")
            return False

    def start(self) -> bool:
        """Start the PSKReporter MQTT subscriber."""
        if self._connected:
            return True

        self._stop_event.clear()
        return self._connect()

    def stop(self) -> None:
        """Stop the subscriber."""
        self._stop_event.set()
        self._disconnect()

    def _connect(self) -> bool:
        """Connect to PSKReporter MQTT broker."""
        try:
            import paho.mqtt.client as mqtt
        except ImportError:
            logger.error("paho-mqtt not installed. Run: pip install paho-mqtt")
            return False

        try:
            client_id = f"meshforge_pskr_{int(time.time())}"
            if hasattr(mqtt, 'CallbackAPIVersion'):
                self._client = mqtt.Client(
                    callback_api_version=mqtt.CallbackAPIVersion.VERSION1,
                    client_id=client_id,
                    protocol=mqtt.MQTTv311,
                )
            else:
                self._client = mqtt.Client(
                    client_id=client_id,
                    protocol=mqtt.MQTTv311,
                )

            self._client.on_connect = self._on_connect
            self._client.on_disconnect = self._on_disconnect
            self._client.on_message = self._on_message

            if self._config.get("use_tls", False):
                context = ssl.create_default_context()
                self._client.tls_set_context(context)

            broker = self._config.get("broker", PSKR_BROKER)
            port = self._config.get("port", PSKR_PORT)
            connect_timeout = self._config.get("connect_timeout", 15)

            logger.info(f"Connecting to PSKReporter MQTT: {broker}:{port}")

            self._client.connect_async(broker, port, keepalive=60)
            self._client.loop_start()

            import atexit
            atexit.register(self._atexit_cleanup)

            # Wait for connection
            start_time = time.time()
            while not self._connected and (time.time() - start_time) < connect_timeout:
                if self._stop_event.is_set():
                    break
                time.sleep(0.1)

            if not self._connected:
                logger.warning(
                    f"PSKReporter MQTT connection to {broker}:{port} "
                    f"timed out after {connect_timeout}s"
                )

            return True

        except Exception as e:
            logger.error(f"PSKReporter MQTT connection failed: {e}")
            return False

    def _disconnect(self) -> None:
        """Disconnect from broker."""
        client = self._client
        if client:
            try:
                try:
                    client.disconnect()
                except Exception:
                    pass

                def stop_loop():
                    try:
                        client.loop_stop()
                    except Exception:
                        pass

                stop_thread = threading.Thread(target=stop_loop, daemon=True)
                stop_thread.start()
                stop_thread.join(timeout=3.0)
            except Exception as e:
                logger.debug(f"PSKReporter disconnect cleanup: {e}")
            self._client = None
        self._connected = False

    def _atexit_cleanup(self) -> None:
        """Cleanup on process exit."""
        if self._client:
            try:
                self._stop_event.set()
                self._client.disconnect()
                self._client.loop_stop()
            except Exception:
                pass
            self._client = None

    def _on_connect(self, client, userdata, flags, rc):
        """MQTT connection callback."""
        if rc == 0:
            self._connected = True
            with self._stats_lock:
                self._stats["connect_time"] = datetime.now()
            logger.info("Connected to PSKReporter MQTT feed")
            self._subscribe_topics()
        else:
            error_msgs = {
                1: "Incorrect protocol version",
                2: "Invalid client identifier",
                3: "Server unavailable",
                4: "Bad username or password",
                5: "Not authorized",
            }
            logger.error(
                f"PSKReporter MQTT connect failed: "
                f"{error_msgs.get(rc, f'Error {rc}')}"
            )

    def _subscribe_topics(self) -> None:
        """Subscribe to PSKReporter MQTT topics based on config."""
        if not self._client:
            return

        callsign = self._config.get("callsign", "").strip().upper()
        bands = self._config.get("bands", [])
        modes = self._config.get("modes", [])

        # Build topic filter(s)
        # Topic: pskr/filter/v2/{band}/{mode}/{sendercall}/{receivercall}/
        #        {senderlocator}/{receiverlocator}/{sendercountry}/{receivercountry}

        if callsign:
            # Monitor specific callsign (as sender or receiver)
            if bands:
                for band in bands:
                    mode_filter = modes[0] if len(modes) == 1 else "+"
                    # As sender
                    topic = f"{PSKR_BASE_TOPIC}/{band}/{mode_filter}/{callsign}/#"
                    self._client.subscribe(topic)
                    logger.info(f"PSKReporter subscribed (TX): {topic}")
                    # As receiver
                    topic = f"{PSKR_BASE_TOPIC}/{band}/{mode_filter}/+/{callsign}/#"
                    self._client.subscribe(topic)
                    logger.info(f"PSKReporter subscribed (RX): {topic}")
            else:
                # All bands for this callsign
                topic = f"{PSKR_BASE_TOPIC}/+/+/{callsign}/#"
                self._client.subscribe(topic)
                logger.info(f"PSKReporter subscribed (TX): {topic}")
                topic = f"{PSKR_BASE_TOPIC}/+/+/+/{callsign}/#"
                self._client.subscribe(topic)
                logger.info(f"PSKReporter subscribed (RX): {topic}")
        elif bands:
            # Monitor specific bands (all callsigns)
            for band in bands:
                mode_filter = modes[0] if len(modes) == 1 else "+"
                topic = f"{PSKR_BASE_TOPIC}/{band}/{mode_filter}/#"
                self._client.subscribe(topic)
                logger.info(f"PSKReporter subscribed: {topic}")
        else:
            # Monitor everything (high volume!)
            topic = f"{PSKR_BASE_TOPIC}/#"
            self._client.subscribe(topic)
            logger.info(f"PSKReporter subscribed (all): {topic}")

    def _on_disconnect(self, client, userdata, rc):
        """MQTT disconnect callback."""
        self._connected = False
        if rc != 0:
            with self._stats_lock:
                self._stats["last_disconnect_reason"] = f"rc_{rc}"
            logger.warning(f"PSKReporter MQTT unexpected disconnect (rc={rc})")
            if (self._config.get("auto_reconnect", True)
                    and not self._stop_event.is_set()):
                self._start_reconnect()
        else:
            logger.info("Disconnected from PSKReporter MQTT")

    def _start_reconnect(self) -> None:
        """Start reconnection thread."""
        if self._reconnect_thread and self._reconnect_thread.is_alive():
            return
        self._reconnect_thread = threading.Thread(
            target=self._reconnect_loop, daemon=True
        )
        self._reconnect_thread.start()

    def _reconnect_loop(self) -> None:
        """Reconnect with exponential backoff."""
        delay = self._config.get("reconnect_delay", 5)
        max_delay = self._config.get("max_reconnect_delay", 60)

        while not self._stop_event.is_set():
            logger.info(f"PSKReporter reconnecting in {delay}s...")
            self._stop_event.wait(delay)
            if self._stop_event.is_set():
                break

            with self._stats_lock:
                self._stats["reconnect_attempts"] += 1

            if self._connect():
                logger.info("PSKReporter reconnection successful")
                break

            delay = min(delay * 1.5, max_delay)

    def _on_message(self, client, userdata, msg):
        """MQTT message callback - parse PSKReporter spot."""
        try:
            payload = msg.payload

            if len(payload) > MAX_PAYLOAD_BYTES:
                with self._stats_lock:
                    self._stats["spots_rejected"] += 1
                return

            data = json.loads(payload.decode("utf-8"))
            spot = self._parse_spot(data)
            if spot is None:
                with self._stats_lock:
                    self._stats["spots_rejected"] += 1
                return

            # Store spot
            with self._spots_lock:
                self._spots.append(spot)

            # Update band activity
            self._update_band_activity(spot)

            # Update stats
            with self._stats_lock:
                self._stats["spots_received"] += 1
                self._stats["last_spot_time"] = datetime.now()
                if isinstance(self._stats["modes_seen"], set):
                    self._stats["modes_seen"].add(spot.mode)

            # Notify callbacks
            for callback in list(self._spot_callbacks):
                try:
                    callback(spot)
                except Exception as e:
                    logger.debug(f"PSKReporter spot callback error: {e}")

        except (json.JSONDecodeError, UnicodeDecodeError):
            with self._stats_lock:
                self._stats["spots_rejected"] += 1
        except Exception as e:
            logger.debug(f"PSKReporter message processing error: {e}")

    def _parse_spot(self, data: Dict[str, Any]) -> Optional[PSKSpot]:
        """Parse a PSKReporter JSON spot payload.

        Expected fields: sq, f, md, rp, t, sc, sl, rc, rl, sa, ra, b
        """
        if not isinstance(data, dict):
            return None

        # Required fields
        sender = data.get("sc", "")
        receiver = data.get("rc", "")
        if not sender or not receiver:
            return None

        freq = data.get("f", 0)
        if not isinstance(freq, (int, float)) or freq <= 0:
            return None

        return PSKSpot(
            sequence=data.get("sq", 0),
            frequency=int(freq),
            mode=str(data.get("md", "")),
            snr=int(data.get("rp", 0)) if data.get("rp") is not None else 0,
            timestamp=int(data.get("t", 0)) if data.get("t") else 0,
            sender_call=str(sender),
            sender_locator=str(data.get("sl", "")),
            receiver_call=str(receiver),
            receiver_locator=str(data.get("rl", "")),
            sender_country=int(data.get("sa", 0)) if data.get("sa") else 0,
            receiver_country=int(data.get("ra", 0)) if data.get("ra") else 0,
            band=str(data.get("b", "")),
        )

    def _update_band_activity(self, spot: PSKSpot) -> None:
        """Update band activity tracking from a new spot."""
        if not spot.band:
            return

        now = time.time()
        with self._band_lock:
            if spot.band not in self._band_activity:
                self._band_activity[spot.band] = {
                    "spot_count": 0,
                    "first_seen": now,
                    "last_seen": now,
                    "modes": set(),
                    "unique_senders": set(),
                    "unique_receivers": set(),
                    "snr_sum": 0,
                    "snr_count": 0,
                }

            entry = self._band_activity[spot.band]
            entry["spot_count"] += 1
            entry["last_seen"] = now
            entry["modes"].add(spot.mode)
            entry["unique_senders"].add(spot.sender_call)
            entry["unique_receivers"].add(spot.receiver_call)
            if spot.snr != 0:
                entry["snr_sum"] += spot.snr
                entry["snr_count"] += 1

    # =========================================================================
    # Public API
    # =========================================================================

    def is_connected(self) -> bool:
        """Check if connected to PSKReporter MQTT."""
        return self._connected

    def get_spots(self, limit: int = 100, band: str = "",
                  mode: str = "", callsign: str = "") -> List[PSKSpot]:
        """Get recent spots with optional filtering.

        Args:
            limit: Maximum spots to return
            band: Filter by band (e.g., "20m")
            mode: Filter by mode (e.g., "FT8")
            callsign: Filter by callsign (sender or receiver)

        Returns:
            List of PSKSpot objects, newest first
        """
        with self._spots_lock:
            spots = list(self._spots)

        # Apply filters
        if band:
            spots = [s for s in spots if s.band == band]
        if mode:
            mode_upper = mode.upper()
            spots = [s for s in spots if s.mode == mode_upper]
        if callsign:
            call_upper = callsign.upper()
            spots = [s for s in spots
                     if s.sender_call == call_upper
                     or s.receiver_call == call_upper]

        # Return newest first, limited
        spots.reverse()
        return spots[:limit]

    def get_band_activity(self) -> Dict[str, Dict[str, Any]]:
        """Get band activity summary.

        Returns:
            Dict keyed by band name with activity stats:
            - spot_count: Total spots on this band
            - last_seen: Timestamp of most recent spot
            - modes: Set of modes seen
            - unique_senders: Count of unique TX stations
            - unique_receivers: Count of unique RX stations
            - avg_snr: Average SNR (dB)
        """
        result = {}
        with self._band_lock:
            for band, data in self._band_activity.items():
                avg_snr = (
                    data["snr_sum"] / data["snr_count"]
                    if data["snr_count"] > 0 else None
                )
                result[band] = {
                    "spot_count": data["spot_count"],
                    "last_seen": data["last_seen"],
                    "modes": list(data["modes"]),
                    "unique_senders": len(data["unique_senders"]),
                    "unique_receivers": len(data["unique_receivers"]),
                    "avg_snr": round(avg_snr, 1) if avg_snr is not None else None,
                }
        return result

    def get_stats(self) -> Dict[str, Any]:
        """Get subscriber statistics."""
        with self._stats_lock:
            stats = dict(self._stats)
            # Convert set to list for JSON compatibility
            if isinstance(stats.get("modes_seen"), set):
                stats["modes_seen"] = list(stats["modes_seen"])

        with self._band_lock:
            stats["bands_active"] = len(self._band_activity)

        with self._spots_lock:
            stats["spots_buffered"] = len(self._spots)

        stats["connected"] = self._connected
        return stats

    def register_spot_callback(
        self, callback: Callable[[PSKSpot], None]
    ) -> None:
        """Register callback for new spots."""
        self._spot_callbacks.append(callback)

    def get_propagation_data(self) -> Dict[str, Any]:
        """Get propagation data suitable for integration with propagation module.

        Returns data in a format compatible with get_enhanced_data() in
        commands/propagation.py.
        """
        activity = self.get_band_activity()
        stats = self.get_stats()

        # Determine which bands are "open" based on recent activity
        now = time.time()
        open_bands = {}
        for band, data in activity.items():
            age = now - data["last_seen"]
            if age < 300:  # Active in last 5 minutes
                open_bands[band] = "open"
            elif age < 900:  # Active in last 15 minutes
                open_bands[band] = "marginal"

        return {
            "pskreporter": {
                "spots_total": stats.get("spots_received", 0),
                "spots_buffered": stats.get("spots_buffered", 0),
                "bands_active": stats.get("bands_active", 0),
                "modes_seen": stats.get("modes_seen", []),
                "open_bands": open_bands,
                "band_activity": activity,
                "connected": self._connected,
                "source": "PSKReporter (mqtt.pskreporter.info)",
            }
        }


# =========================================================================
# Factory functions
# =========================================================================

def create_pskreporter_subscriber(
    callsign: str = "",
    bands: Optional[List[str]] = None,
    modes: Optional[List[str]] = None,
    use_tls: bool = False,
) -> PSKReporterSubscriber:
    """Create a PSKReporter subscriber with common settings.

    Args:
        callsign: Monitor specific callsign (empty = all)
        bands: List of bands to monitor (empty = all)
        modes: List of modes to monitor (empty = all)
        use_tls: Use TLS connection

    Returns:
        Configured PSKReporterSubscriber instance
    """
    port = PSKR_PORT_TLS if use_tls else PSKR_PORT
    config = {
        "broker": PSKR_BROKER,
        "port": port,
        "use_tls": use_tls,
        "callsign": callsign,
        "bands": bands or [],
        "modes": modes or [],
        "max_spots": MAX_SPOTS,
        "enabled": True,
        "auto_reconnect": True,
        "reconnect_delay": 5,
        "max_reconnect_delay": 60,
    }
    return PSKReporterSubscriber(config=config)


# Singleton management

_subscriber: Optional[PSKReporterSubscriber] = None


def get_pskreporter_subscriber() -> PSKReporterSubscriber:
    """Get or create the global PSKReporter subscriber."""
    global _subscriber
    if _subscriber is None:
        _subscriber = PSKReporterSubscriber()
    return _subscriber


def start_pskreporter() -> bool:
    """Start the PSKReporter subscriber.

    Returns:
        True if started successfully
    """
    subscriber = get_pskreporter_subscriber()
    return subscriber.start()


def stop_pskreporter() -> None:
    """Stop the PSKReporter subscriber."""
    global _subscriber
    if _subscriber:
        _subscriber.stop()
        _subscriber = None
