"""
MeshForge Domain Knowledge Base.

Provides structured knowledge about mesh networking for intelligent diagnostics
and user assistance. Works offline for standalone mode.

Knowledge categories:
- RF fundamentals (propagation, antennas, LoRa)
- Protocol details (Meshtastic, Reticulum, MQTT)
- Hardware specifics (devices, serial ports, GPIO)
- Network topology (routing, relays, gateways)
- Troubleshooting guides

Usage:
    kb = KnowledgeBase()
    answer = kb.query("What causes low SNR?")
    guide = kb.get_troubleshooting_guide("no_connection")
"""

import logging
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Tuple
from enum import Enum

logger = logging.getLogger(__name__)


class KnowledgeTopic(Enum):
    """Knowledge topic categories."""
    RF_FUNDAMENTALS = "rf_fundamentals"
    MESHTASTIC = "meshtastic"
    RETICULUM = "reticulum"
    MQTT = "mqtt"
    HARDWARE = "hardware"
    NETWORKING = "networking"
    TROUBLESHOOTING = "troubleshooting"
    BEST_PRACTICES = "best_practices"


@dataclass
class KnowledgeEntry:
    """A piece of knowledge in the knowledge base."""
    topic: KnowledgeTopic
    title: str
    content: str
    keywords: List[str] = field(default_factory=list)
    related_entries: List[str] = field(default_factory=list)  # titles
    expertise_level: str = "intermediate"  # novice, intermediate, expert


@dataclass
class TroubleshootingStep:
    """A step in a troubleshooting guide."""
    instruction: str
    command: Optional[str] = None  # Shell command to run
    expected_result: Optional[str] = None
    if_fail: Optional[str] = None  # Next step if this fails


@dataclass
class TroubleshootingGuide:
    """A complete troubleshooting guide."""
    problem: str
    description: str
    prerequisites: List[str] = field(default_factory=list)
    steps: List[TroubleshootingStep] = field(default_factory=list)
    related_problems: List[str] = field(default_factory=list)


class KnowledgeBase:
    """
    Domain knowledge base for mesh networking.

    Provides:
    - Keyword-based queries
    - Troubleshooting guides
    - Concept explanations
    - Best practice recommendations
    """

    def __init__(self):
        """Initialize the knowledge base."""
        self._entries: Dict[str, KnowledgeEntry] = {}
        self._guides: Dict[str, TroubleshootingGuide] = {}
        self._keyword_index: Dict[str, List[str]] = {}  # keyword -> entry titles

        # Load knowledge
        self._load_rf_knowledge()
        self._load_meshtastic_knowledge()
        self._load_reticulum_knowledge()
        self._load_hardware_knowledge()
        self._load_troubleshooting_guides()
        self._load_best_practices()

        # Build index
        self._build_keyword_index()

    def _add_entry(self, entry: KnowledgeEntry) -> None:
        """Add an entry to the knowledge base."""
        self._entries[entry.title] = entry

    def _add_guide(self, guide: TroubleshootingGuide) -> None:
        """Add a troubleshooting guide."""
        self._guides[guide.problem] = guide

    def _build_keyword_index(self) -> None:
        """Build keyword search index."""
        for title, entry in self._entries.items():
            for keyword in entry.keywords:
                keyword_lower = keyword.lower()
                if keyword_lower not in self._keyword_index:
                    self._keyword_index[keyword_lower] = []
                self._keyword_index[keyword_lower].append(title)

    def _load_rf_knowledge(self) -> None:
        """Load RF fundamentals knowledge."""

        self._add_entry(KnowledgeEntry(
            topic=KnowledgeTopic.RF_FUNDAMENTALS,
            title="SNR (Signal-to-Noise Ratio)",
            content="""
SNR measures signal strength relative to background noise in decibels (dB).

For LoRa/Meshtastic:
- SNR > 0 dB: Good signal
- SNR -5 to 0 dB: Acceptable
- SNR -10 to -5 dB: Weak, may have packet loss
- SNR < -15 dB: Very weak, near receive limit

Factors affecting SNR:
1. Distance - Signal strength decreases with distance (inverse square law)
2. Obstacles - Buildings, trees, terrain block/reflect signals
3. Antenna quality - Higher gain antennas improve SNR
4. Interference - Other RF sources on same frequency
5. Antenna orientation - LoRa antennas are usually vertically polarized

Improvement strategies:
- Raise antenna height
- Use higher gain antenna
- Improve line of sight
- Reduce interference sources
- Add relay nodes to shorten hops
""",
            keywords=["snr", "signal", "noise", "weak signal", "reception", "decibels", "db"],
            expertise_level="novice",
        ))

        self._add_entry(KnowledgeEntry(
            topic=KnowledgeTopic.RF_FUNDAMENTALS,
            title="RSSI (Received Signal Strength Indicator)",
            content="""
RSSI measures absolute received signal power in dBm.

Typical values for LoRa:
- -50 to -70 dBm: Excellent (very close)
- -70 to -90 dBm: Good
- -90 to -110 dBm: Fair
- -110 to -120 dBm: Weak
- Below -120 dBm: At receiver sensitivity limit

Unlike SNR, RSSI doesn't account for noise floor.
Use both metrics together:
- High RSSI + High SNR = Good link
- Low RSSI + Good SNR = Weak but clean signal
- High RSSI + Low SNR = Strong interference present
""",
            keywords=["rssi", "signal strength", "dbm", "power", "received"],
            related_entries=["SNR (Signal-to-Noise Ratio)"],
            expertise_level="intermediate",
        ))

        self._add_entry(KnowledgeEntry(
            topic=KnowledgeTopic.RF_FUNDAMENTALS,
            title="LoRa Spreading Factor",
            content="""
Spreading Factor (SF) is a key LoRa parameter that trades range for speed.

SF7: Fastest, shortest range
SF8-SF11: Intermediate
SF12: Slowest, longest range

Each SF increase roughly doubles airtime and range.

Meshtastic presets map to these SFs:
- SHORT_FAST: SF7 (1-3 km urban)
- SHORT_SLOW: SF8
- MEDIUM_FAST: SF9 (~5 km)
- MEDIUM_SLOW: SF10
- LONG_FAST: SF11 (~10 km) - Default
- LONG_SLOW: SF12 (20+ km line of sight)

Higher SF = Better sensitivity but:
- Longer time on air (more battery)
- Higher channel utilization
- Fewer messages per hour allowed
""",
            keywords=["spreading factor", "sf", "range", "lora", "preset", "speed"],
            expertise_level="intermediate",
        ))

        self._add_entry(KnowledgeEntry(
            topic=KnowledgeTopic.RF_FUNDAMENTALS,
            title="Channel Utilization",
            content="""
Channel utilization indicates how busy the radio frequency is.

Measured as percentage of time the channel is in use:
- 0-25%: Light usage, plenty of capacity
- 25-50%: Moderate, still good
- 50-75%: Heavy, delays likely
- >75%: Congested, packet loss expected

Meshtastic enforces duty cycle limits:
- Maximum 10% transmit duty cycle (regulatory)
- Messages queued when channel busy
- Priority given to routing/ACK packets

Reducing channel utilization:
- Send fewer/shorter messages
- Use higher data rate (lower SF)
- Spread across multiple channels
- Use MQTT for non-critical traffic
""",
            keywords=["channel utilization", "duty cycle", "congestion", "busy", "airtime"],
            expertise_level="intermediate",
        ))

        self._add_entry(KnowledgeEntry(
            topic=KnowledgeTopic.RF_FUNDAMENTALS,
            title="Fresnel Zone",
            content="""
The Fresnel zone is an elliptical area around the line of sight that must be
clear for optimal RF propagation.

For LoRa at 915 MHz (US), the first Fresnel zone radius at midpoint:
- 1 km link: ~9 meters clearance needed
- 5 km link: ~20 meters clearance needed
- 10 km link: ~28 meters clearance needed

If obstacles intrude into >40% of Fresnel zone, signal loss increases significantly.

Practical implications:
- Antenna height matters more than you think
- A "clear" visual line of sight may not be RF clear
- Lakes/water are excellent reflectors
- Hills mid-path are worse than hills at endpoints

This is why rooftop antennas dramatically outperform ground-level ones,
even with "clear" line of sight.
""",
            keywords=["fresnel", "line of sight", "los", "clearance", "propagation", "height"],
            expertise_level="expert",
        ))

        self._add_entry(KnowledgeEntry(
            topic=KnowledgeTopic.RF_FUNDAMENTALS,
            title="Signal Quality Classification",
            content="""
Signal quality is classified based on both SNR and RSSI together:

EXCELLENT (reliable, high margin):
- SNR >= -3 dB AND RSSI >= -100 dBm
- Strong signal, well above noise floor
- Expect near 100% packet delivery

GOOD (normal operation):
- SNR >= -7 dB AND RSSI >= -115 dBm
- Standard quality for reliable mesh operation
- Occasional retransmits may occur

FAIR (usable but weak):
- SNR >= -15 dB AND RSSI >= -126 dBm
- May experience packet loss
- Consider improving antenna/position

BAD (unreliable):
- Below FAIR thresholds
- High packet loss expected
- Link may drop frequently

Link Margin:
The difference between received signal and receiver sensitivity.
- SF11 sensitivity: -134.5 dBm
- SF12 sensitivity: -137 dBm
- 10+ dB margin recommended for reliability

These thresholds are based on the meshtastic-go library and MeshTenna
antenna testing tool.
""",
            keywords=["signal quality", "classification", "good", "bad", "fair", "threshold", "link margin"],
            related_entries=["SNR (Signal-to-Noise Ratio)", "RSSI (Received Signal Strength Indicator)"],
            expertise_level="intermediate",
        ))

        self._add_entry(KnowledgeEntry(
            topic=KnowledgeTopic.RF_FUNDAMENTALS,
            title="Antenna Testing",
            content="""
Proper antenna testing ensures your system performs optimally.

Equipment:
- VNA (Vector Network Analyzer) for SWR/impedance
- NanoVNA is affordable (~$50) for hobbyists
- Alternatively: SWR meter inline during TX

Key Measurements:

SWR (Standing Wave Ratio):
- 1.0:1 = Perfect (impossible in practice)
- <1.5:1 = Excellent
- <2.0:1 = Good
- >3.0:1 = Poor, significant power loss

Return Loss:
- >20 dB = Excellent (<1% reflected)
- >14 dB = Good (<4% reflected)
- <10 dB = Poor (>10% reflected)

Resonant Frequency:
- Antenna should resonate at your operating frequency
- Off-resonance = higher SWR, reduced efficiency
- Many cheap antennas are mis-labeled

Cable and Connector Losses (at 915 MHz):
- RG174: ~0.9 dB/m (high loss, avoid for runs >1m)
- RG58: ~0.5 dB/m
- LMR400: ~0.15 dB/m (low loss, recommended)
- SMA connector: ~0.1 dB each
- Every connector/meter of cable reduces your signal

Best Practices:
- Keep cable runs as short as possible
- Use quality low-loss coax for longer runs
- Never close a window on coax cable
- Waterproof outdoor connections
- Mount antenna vertically for LoRa (vertical polarization)

Reference: MeshTenna antenna testing tool
""",
            keywords=["antenna", "testing", "vna", "swr", "return loss", "cable", "connector", "impedance"],
            related_entries=["Fresnel Zone"],
            expertise_level="expert",
        ))

    def _load_meshtastic_knowledge(self) -> None:
        """Load Meshtastic-specific knowledge."""

        self._add_entry(KnowledgeEntry(
            topic=KnowledgeTopic.MESHTASTIC,
            title="Meshtastic Node Roles",
            content="""
Meshtastic nodes can have different roles:

CLIENT (default):
- Normal node for sending/receiving messages
- Participates in mesh routing
- Good for mobile/portable use

CLIENT_MUTE:
- Receives all messages
- Does not transmit (stealth mode)
- Does not route for others

ROUTER:
- Optimized for routing/relaying
- Always on, never sleeps
- Higher priority for routing decisions
- Usually solar/mains powered

ROUTER_CLIENT:
- Hybrid router that also uses device
- Routes + normal messaging
- Good for home base stations

REPEATER:
- Pure relay, no user interface
- Minimal protocol overhead
- Ideal for remote hilltop repeaters
- Should be paired with router-role node

TRACKER:
- Optimized for GPS tracking
- Minimal other traffic
- Higher position update rate
""",
            keywords=["role", "router", "client", "repeater", "tracker", "node type"],
            expertise_level="novice",
        ))

        self._add_entry(KnowledgeEntry(
            topic=KnowledgeTopic.MESHTASTIC,
            title="Meshtastic Channels",
            content="""
Meshtastic supports multiple channels for message segregation.

Channel 0: Primary channel
- Required, always exists
- Used for node discovery and routing
- Default encryption key: "AQ==" (LongFast)

Channels 1-7: Secondary channels
- Optional additional channels
- Can have different encryption keys
- Useful for different groups/purposes

Each channel has:
- Name (human readable)
- PSK (Pre-Shared Key) for encryption
- Uplink/Downlink settings for MQTT

MQTT integration:
- Channels can be bridged to MQTT
- Uplink: Send messages to MQTT broker
- Downlink: Receive messages from MQTT
- Enables internet connectivity for mesh
""",
            keywords=["channel", "encryption", "psk", "key", "mqtt", "uplink", "downlink"],
            related_entries=["MQTT for Meshtastic"],
            expertise_level="intermediate",
        ))

        self._add_entry(KnowledgeEntry(
            topic=KnowledgeTopic.MESHTASTIC,
            title="meshtasticd Daemon",
            content="""
meshtasticd is the Linux daemon for Meshtastic radio access.

Purpose:
- Provides TCP/IP interface to Meshtastic radio
- Allows multiple clients (with limitations)
- Runs as system service

Configuration: /etc/meshtasticd/config.yaml
- Serial port settings
- TCP port (default 4403)
- Logging configuration

Common issues:
1. Only ONE client can hold write lock
   - MeshForge, nomadnet, meshtastic CLI compete
   - Solution: Close other clients

2. Serial port permissions
   - User needs dialout group membership
   - Or run as root (not recommended)

3. Device hot-plug
   - Daemon may not detect device changes
   - Restart after connecting/disconnecting radio

Commands:
- sudo systemctl status meshtasticd
- sudo systemctl restart meshtasticd
- journalctl -u meshtasticd -f
""",
            keywords=["meshtasticd", "daemon", "service", "tcp", "4403", "linux"],
            expertise_level="intermediate",
        ))

        self._add_entry(KnowledgeEntry(
            topic=KnowledgeTopic.MESHTASTIC,
            title="Meshtastic Telemetry Sensors",
            content="""
Meshtastic supports various telemetry sensors via I2C bus:

Device Metrics (built-in):
- Battery level and voltage
- Channel utilization (how busy the RF channel is)
- TX airtime (transmit duty cycle)
- Uptime

Environment Sensors (I2C):
- BME280: Temperature, humidity, barometric pressure (~$5-10)
- BME680: Same as BME280 + VOC gas sensor (~$15)
- BMP280: Temperature and pressure only (~$3)
- SHT31: High-accuracy temperature and humidity
- Sensors auto-detected on I2C bus at startup

Air Quality Sensors:
- PMSA003I: Particulate matter (PM1.0, PM2.5, PM10) (~$40)
- SCD4X: CO2 concentration (~$50)
- Good for environmental monitoring stations

Health Sensors:
- MAX30102: Heart rate and SpO2 (blood oxygen)
- Body temperature sensors

Configuration:
- Enable in Meshtastic app: Settings > Module Configuration > Telemetry
- Default broadcast interval: 30 minutes
- Can adjust interval for more/less frequent updates

Use Cases:
- Weather stations at remote locations
- Air quality monitoring network
- Solar-powered environmental sensors
- Garden/greenhouse monitoring
""",
            keywords=["telemetry", "sensor", "bme280", "temperature", "humidity", "air quality", "pm2.5", "environment"],
            expertise_level="intermediate",
        ))

        self._add_entry(KnowledgeEntry(
            topic=KnowledgeTopic.MESHTASTIC,
            title="Meshtastic Detection Sensor Module",
            content="""
The Detection Sensor module monitors GPIO pins for state changes and sends
alerts over the mesh network.

Use Cases:
- Motion detection (PIR sensors like HC-SR501)
- Door/window sensors (reed switches)
- Water leak detection
- Intrusion alerts for remote locations
- Tripwire-style security

Configuration Options:
- Monitor Pin: GPIO pin to watch
- Detection Triggered High: Is HIGH (1) the triggered state?
- Use Pull-up: Enable internal pull-up resistor
- Name: Alert name (e.g., "Motion" -> "Motion detected")
- Min Broadcast Interval: Minimum seconds between alerts
- State Broadcast Interval: Heartbeat interval (0 = only on change)

Hardware Notes:
- HC-SR501 PIR: Requires 5V, may not work on battery
- Reed switches: Work with 3.3V, very low power
- Choose GPIO pins not used by other functions
- Check your board's available GPIO pins

Alert Format:
When triggered, sends message: "{Name} detected" or "{Name} clear"
Example: "Motion detected" or "Door clear"

Requires firmware 2.2.2 or higher.
""",
            keywords=["detection", "sensor", "gpio", "motion", "pir", "reed", "switch", "alert", "security"],
            related_entries=["Meshtastic Telemetry Sensors"],
            expertise_level="intermediate",
        ))

    def _load_reticulum_knowledge(self) -> None:
        """Load Reticulum-specific knowledge."""

        self._add_entry(KnowledgeEntry(
            topic=KnowledgeTopic.RETICULUM,
            title="Reticulum Network Stack",
            content="""
Reticulum (RNS) is a cryptographic networking stack for reliable communication
over high-latency, low-bandwidth links.

Key concepts:
- Identity-based addressing (no IP addresses)
- End-to-end encryption by default
- Works over any transport (LoRa, TCP, I2P, etc.)
- Automatic routing and path discovery

Components:
- rnsd: Reticulum daemon
- nomadnet: Text-based messaging app
- LXMF: Messaging format
- Sideband: Mobile app

For MeshForge:
- RNS provides the "other" mesh network
- Gateway bridges Meshtastic ↔ RNS
- Different addressing schemes (hash vs node ID)
""",
            keywords=["reticulum", "rns", "cryptographic", "lxmf", "nomadnet", "identity"],
            expertise_level="intermediate",
        ))

        self._add_entry(KnowledgeEntry(
            topic=KnowledgeTopic.RETICULUM,
            title="RNS Interfaces",
            content="""
Reticulum supports multiple transport interfaces.

TCPClientInterface:
- Connect to remote RNS node via TCP
- Used for internet bridging
- Config: target_host, target_port

TCPServerInterface:
- Accept incoming TCP connections
- Run as hub for other nodes

SerialInterface:
- Direct serial connection
- For LoRa modems, packet radio

RNodeInterface:
- For RNode hardware (LoRa modem)
- Most common for RF mesh

LocalInterface:
- Loopback for local apps
- Always enabled

AutoInterface:
- Automatic peer discovery
- Uses UDP multicast on LAN
- Great for local testing

Configuration in: ~/.reticulum/config
""",
            keywords=["interface", "tcp", "serial", "rnode", "transport", "config"],
            expertise_level="intermediate",
        ))

        self._add_entry(KnowledgeEntry(
            topic=KnowledgeTopic.RETICULUM,
            title="RNS Cryptography",
            content="""
Reticulum uses strong, well-established cryptographic primitives:

Identity & Addressing:
- 512-bit Curve25519 keysets (Ed25519 + X25519)
- No source addresses on packets (initiator anonymity)
- Destination addresses are cryptographic hashes
- Globally unique without central coordination

Encryption:
- AES-256-CBC encryption with PKCS7 padding
- HMAC-SHA256 for authentication
- Forward secrecy via ephemeral ECDH exchanges
- Per-packet keys for privacy

Link Establishment:
- Only 3 packets (297 bytes) to establish encrypted link
- Link overhead: 0.44 bits per second
- Unforgeable delivery confirmations

This means:
- Messages are encrypted end-to-end by default
- No trust in network infrastructure required
- Even relay nodes cannot read message contents
- Identity is provable via cryptographic signatures

For MeshForge gateway:
- Each side maintains its own identity
- Bridge must have valid RNS identity to participate
- Messages re-encrypted across network boundary
""",
            keywords=["cryptography", "encryption", "aes", "curve25519", "ed25519", "identity", "security"],
            related_entries=["Reticulum Network Stack"],
            expertise_level="expert",
        ))

        self._add_entry(KnowledgeEntry(
            topic=KnowledgeTopic.RETICULUM,
            title="RNS Node Discovery",
            content="""
Reticulum uses an announce-based discovery system:

Announces:
- Nodes broadcast their identity and destination hash
- Public key shared for others to route to you
- App data can include display name, capabilities
- Without announcing, you are invisible on the network

Path Discovery:
- Automatic multi-hop path finding
- Transport layer maintains path table
- Paths expire and refresh automatically
- No central routing authority

Network Visualizer (like MeshChat):
- Shows announced nodes and their connectivity
- Tracks path hops to each destination
- Displays announce timestamps
- Helps understand network topology

Bootstrap:
- New nodes connect to known peers
- Temporary bootstrap links discover local infrastructure
- System automatically forms stronger direct links
- Bootstrap connections can be discarded after discovery

For MeshForge:
- Use list_known_destinations() to see known nodes
- Use discover_nodes() for active discovery
- Monitor Transport.path_table for topology
- Check Identity.known_destinations for all seen nodes
""",
            keywords=["discovery", "announce", "path", "routing", "bootstrap", "visualizer", "topology"],
            related_entries=["RNS Interfaces"],
            expertise_level="intermediate",
        ))

    def _load_hardware_knowledge(self) -> None:
        """Load hardware-related knowledge."""

        self._add_entry(KnowledgeEntry(
            topic=KnowledgeTopic.HARDWARE,
            title="Common Meshtastic Hardware",
            content="""
Popular Meshtastic-compatible devices:

LILYGO T-Beam:
- ESP32 + SX1276/SX1262 LoRa
- Built-in GPS, 18650 battery holder
- Good balance of features
- ~$30-40

Heltec V3:
- ESP32-S3 + SX1262
- Small OLED display
- Compact form factor
- ~$20-25

RAK WisBlock:
- Modular design
- nRF52840 + SX1262
- Low power, long battery life
- Professional quality

Station G2:
- Higher power output (1W)
- Better range
- Larger, not portable
- ~$80-100

For MeshForge as base station:
- Raspberry Pi + USB serial modem
- Or SPI-connected LoRa module
- meshtasticd handles radio access
""",
            keywords=["hardware", "tbeam", "heltec", "rak", "device", "radio", "esp32"],
            expertise_level="novice",
        ))

        self._add_entry(KnowledgeEntry(
            topic=KnowledgeTopic.HARDWARE,
            title="Serial Port Troubleshooting",
            content="""
Serial port issues are common with Meshtastic/RNS devices.

Finding your device:
ls /dev/ttyUSB* /dev/ttyACM*
dmesg | grep -i tty

Permission denied:
sudo usermod -aG dialout $USER
# Then logout/login

Device busy:
lsof /dev/ttyUSB0
# Kill the blocking process

Device not found:
- Check USB cable (data cable, not charge-only)
- Try different USB port
- Check dmesg for errors
- May need CH340/CP2102 driver

For Raspberry Pi:
- Disable Bluetooth to free /dev/ttyAMA0
- Edit /boot/config.txt: dtoverlay=disable-bt
- Reboot

Multiple devices:
- Use /dev/serial/by-id/ for stable names
- Prevents confusion when USB order changes
""",
            keywords=["serial", "tty", "usb", "permission", "port", "device"],
            expertise_level="intermediate",
        ))

    def _load_troubleshooting_guides(self) -> None:
        """Load troubleshooting guides."""

        self._add_guide(TroubleshootingGuide(
            problem="no_connection_meshtasticd",
            description="Cannot connect to meshtasticd service",
            prerequisites=["meshtasticd installed", "Meshtastic device connected"],
            steps=[
                TroubleshootingStep(
                    instruction="Check if meshtasticd is running",
                    command="sudo systemctl status meshtasticd",
                    expected_result="Active: active (running)",
                    if_fail="Start the service: sudo systemctl start meshtasticd",
                ),
                TroubleshootingStep(
                    instruction="Check if port 4403 is listening",
                    command="ss -tlnp | grep 4403",
                    expected_result="LISTEN ... :4403",
                    if_fail="Service may have crashed, check logs",
                ),
                TroubleshootingStep(
                    instruction="Check for other clients",
                    command="ss -tnp | grep 4403",
                    expected_result="No established connections or only MeshForge",
                    if_fail="Another client is connected, close it first",
                ),
                TroubleshootingStep(
                    instruction="Check meshtasticd logs for errors",
                    command="journalctl -u meshtasticd -n 50",
                    expected_result="No ERROR or CRITICAL messages",
                ),
                TroubleshootingStep(
                    instruction="Restart meshtasticd and try again",
                    command="sudo systemctl restart meshtasticd",
                ),
            ],
            related_problems=["serial_port_issues", "device_not_found"],
        ))

        self._add_guide(TroubleshootingGuide(
            problem="weak_signal",
            description="Nodes have weak signal (low SNR/RSSI)",
            prerequisites=["Nodes are powered on", "Basic connectivity exists"],
            steps=[
                TroubleshootingStep(
                    instruction="Check current SNR and RSSI values",
                    command="meshtastic --nodes",
                    expected_result="SNR > -10, RSSI > -110",
                ),
                TroubleshootingStep(
                    instruction="Verify antenna is properly connected",
                    expected_result="Antenna screwed on tightly, correct frequency band",
                    if_fail="Transmitting without antenna can damage radio!",
                ),
                TroubleshootingStep(
                    instruction="Check antenna orientation",
                    expected_result="Antenna vertical for maximum range",
                    if_fail="Horizontal antennas have different pattern",
                ),
                TroubleshootingStep(
                    instruction="Increase antenna height if possible",
                    expected_result="Even 1-2 meters height can double range",
                ),
                TroubleshootingStep(
                    instruction="Check for obstructions in RF path",
                    expected_result="Clear line of sight to other node",
                    if_fail="Consider relay node or better antenna placement",
                ),
                TroubleshootingStep(
                    instruction="Consider changing modem preset for more range",
                    command="meshtastic --set lora.modem_preset LONG_SLOW",
                    expected_result="Longer range but slower data rate",
                ),
            ],
        ))

        self._add_guide(TroubleshootingGuide(
            problem="high_channel_utilization",
            description="Channel utilization consistently above 50%",
            steps=[
                TroubleshootingStep(
                    instruction="Check current channel utilization",
                    command="meshtastic --info | grep -i util",
                    expected_result="Channel utilization < 25%",
                ),
                TroubleshootingStep(
                    instruction="Identify message sources",
                    expected_result="Determine which nodes are sending most traffic",
                ),
                TroubleshootingStep(
                    instruction="Reduce position broadcast rate",
                    command="meshtastic --set position.position_broadcast_secs 900",
                    expected_result="Position updates every 15 minutes instead of default",
                ),
                TroubleshootingStep(
                    instruction="Use faster modem preset if range allows",
                    command="meshtastic --set lora.modem_preset MEDIUM_FAST",
                    expected_result="Shorter air time per message",
                ),
                TroubleshootingStep(
                    instruction="Move high-volume traffic to MQTT",
                    expected_result="Telemetry via MQTT reduces RF usage",
                ),
            ],
        ))

    def _load_best_practices(self) -> None:
        """Load best practices knowledge."""

        self._add_entry(KnowledgeEntry(
            topic=KnowledgeTopic.BEST_PRACTICES,
            title="MeshForge Deployment Best Practices",
            content="""
Recommended practices for MeshForge deployment:

NETWORK DESIGN:
1. Start with minimum viable mesh (3-4 nodes)
2. Test RF links before adding complexity
3. Place router nodes at high points
4. Use MQTT for internet connectivity

GATEWAY CONFIGURATION:
1. Run MeshForge on stable power (not battery)
2. Use wired Ethernet when possible
3. Configure reasonable queue sizes
4. Enable message persistence

MONITORING:
1. Check diagnostic panel regularly
2. Set up alerts for critical issues
3. Monitor channel utilization
4. Track node battery levels

SECURITY:
1. Change default channel keys
2. Use TLS for MQTT connections
3. Don't expose services to internet directly
4. Keep firmware updated

RELIABILITY:
1. Test failover scenarios
2. Have backup power (UPS)
3. Document your configuration
4. Regular backups of config files
""",
            keywords=["best practices", "deployment", "setup", "configuration", "security"],
            expertise_level="intermediate",
        ))

    # ===== Query Methods =====

    def query(self, question: str, max_results: int = 3) -> List[Tuple[KnowledgeEntry, float]]:
        """
        Query the knowledge base.

        Args:
            question: Natural language question
            max_results: Maximum number of results

        Returns:
            List of (entry, relevance_score) tuples

        API Contract:
            - ALWAYS returns a list (never None)
            - Empty list if no matches found
            - Each element is a 2-tuple: (KnowledgeEntry, float)
            - Results sorted by relevance (highest first)
            - Callers MUST check 'if results:' before accessing results[0]
            - Tests: tests/test_ai_tools.py::TestKnowledgeBase
        """
        # Extract keywords from question
        words = re.findall(r'\b\w+\b', question.lower())
        stop_words = {'what', 'why', 'how', 'is', 'the', 'a', 'an', 'to', 'for', 'of', 'in', 'on'}
        keywords = [w for w in words if w not in stop_words and len(w) > 2]

        # Score entries by keyword matches
        scores: Dict[str, float] = {}

        for keyword in keywords:
            if keyword in self._keyword_index:
                for title in self._keyword_index[keyword]:
                    scores[title] = scores.get(title, 0) + 1.0

            # Partial matches
            for indexed_keyword in self._keyword_index:
                if keyword in indexed_keyword or indexed_keyword in keyword:
                    for title in self._keyword_index[indexed_keyword]:
                        scores[title] = scores.get(title, 0) + 0.5

        # Sort by score
        sorted_titles = sorted(scores.keys(), key=lambda t: scores[t], reverse=True)

        results = []
        for title in sorted_titles[:max_results]:
            entry = self._entries[title]
            results.append((entry, scores[title]))

        return results

    def get_entry(self, title: str) -> Optional[KnowledgeEntry]:
        """Get a specific knowledge entry by title."""
        return self._entries.get(title)

    def get_troubleshooting_guide(self, problem: str) -> Optional[TroubleshootingGuide]:
        """Get a troubleshooting guide by problem name."""
        return self._guides.get(problem)

    def list_topics(self) -> List[str]:
        """List all available topics."""
        return list(set(e.topic.value for e in self._entries.values()))

    def get_entries_by_topic(self, topic: KnowledgeTopic) -> List[KnowledgeEntry]:
        """Get all entries for a topic."""
        return [e for e in self._entries.values() if e.topic == topic]

    def get_all_guides(self) -> List[TroubleshootingGuide]:
        """Get all troubleshooting guides."""
        return list(self._guides.values())


# Singleton instance
_kb: Optional[KnowledgeBase] = None


def get_knowledge_base() -> KnowledgeBase:
    """Get the global knowledge base instance."""
    global _kb
    if _kb is None:
        _kb = KnowledgeBase()
    return _kb
