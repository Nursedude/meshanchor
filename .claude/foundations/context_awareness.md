# Dude AI Context Awareness

> **Dude AI knows your network. That's what makes it useful.**

This document defines what context Dude AI tracks to provide intelligent assistance.

---

## Table of Contents

1. [User Context](#user-context)
2. [MeshForge as Meshtastic Web Client](#meshforge-as-meshtastic-web-client)
3. [Hardware Configuration](#hardware-configuration)
4. [Network Topology](#network-topology)
5. [RNS Interface Templates](#rns-interface-templates)
6. [Connection Pool Management](#connection-pool-management)
7. [Queue Management](#queue-management)
8. [System State](#system-state)
9. [Past Issues & Solutions](#past-issues--solutions)
10. [Fresh Install Landmarks](#fresh-install-landmarks)

---

## User Context

### Meshtastic User Options

Dude AI tracks user identity from Meshtastic configuration:

| Field | Type | Description |
|-------|------|-------------|
| `long_name` | string (max 39 chars) | Full display name (e.g., "Nurse Dude WH6GXZ") |
| `short_name` | string (max 4 chars) | Abbreviated name shown on small displays |
| `is_licensed` | boolean | Licensed amateur radio operator |
| `hw_model` | enum | Hardware model (T-Beam, RAK4631, etc.) |

**Callsign Detection:**
```python
# Extract callsign from long_name
import re
callsign_pattern = r'[A-Z]{1,2}[0-9][A-Z]{1,3}'
match = re.search(callsign_pattern, user.long_name.upper())
if match:
    user_callsign = match.group()
```

### Location Context

| Field | Type | Description |
|-------|------|-------------|
| `latitude_i` | int32 | Latitude in 1e-7 degrees |
| `longitude_i` | int32 | Longitude in 1e-7 degrees |
| `altitude` | int32 | Altitude in meters |
| `time` | fixed32 | Position timestamp |

**Grid Square Calculation:**
```python
def latlon_to_grid(lat, lon):
    """Convert lat/lon to 6-character Maidenhead grid."""
    lon += 180
    lat += 90
    field = chr(int(lon / 20) + ord('A')) + chr(int(lat / 10) + ord('A'))
    square = str(int((lon % 20) / 2)) + str(int(lat % 10))
    subsq = chr(int((lon % 2) * 12) + ord('a')) + chr(int((lat % 1) * 24) + ord('a'))
    return field + square + subsq
```

Reference: [Meshtastic User Config](https://meshtastic.org/docs/configuration/radio/user/)

---

## MeshForge as Meshtastic Web Client

**Critical Understanding:** MeshForge acts as a **web client** to Meshtastic, not as native firmware.

Reference: [Meshtastic Web Client](https://meshtastic.org/docs/software/web-client/)

### Connection Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    Connection Model                          │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  ┌──────────────┐        TCP/Protobuf      ┌──────────────┐ │
│  │  MeshForge   │◄────────────────────────►│ meshtasticd  │ │
│  │ (Web Client) │        Port 4403         │  (Daemon)    │ │
│  └──────────────┘                          └──────┬───────┘ │
│                                                   │         │
│                                              Serial/USB      │
│                                                   │         │
│                                            ┌──────▼───────┐ │
│                                            │   LoRa HW    │ │
│                                            │ (T-Beam etc) │ │
│                                            └──────────────┘ │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

### What MeshForge Can Do (as web client)

| Capability | Status | Notes |
|------------|--------|-------|
| Read node info | Yes | Long name, short name, HW model |
| Read position | Yes | Lat, lon, altitude |
| Read telemetry | Yes | Battery, voltage, SNR, channel util |
| Send messages | Yes | Text to channels or DMs |
| Read mesh topology | Yes | Node list, hops, SNR |
| Change config | Limited | Requires admin access |
| Flash firmware | No | Use native tools |

### Meshtastic Module Configuration

Reference: [Meshtastic Modules](https://meshtastic.org/docs/configuration/module/)

Modules that affect MeshForge operation:

| Module | Impact on MeshForge |
|--------|---------------------|
| **Serial** | Enables TCP interface for meshtasticd |
| **Telemetry** | Device metrics available in dashboard |
| **Position** | GPS data for map display |
| **Range Test** | SNR/distance data for RF analysis |
| **Store & Forward** | Message persistence across reboots |
| **External Notification** | Alert triggers from mesh |
| **Remote Hardware** | GPIO control via mesh (advanced) |

### Module Context for Dude AI

```python
@dataclass
class MeshtasticModuleContext:
    """Meshtastic module state."""

    # Core modules
    serial_enabled: bool       # Required for TCP
    telemetry_enabled: bool    # For dashboard
    position_enabled: bool     # For mapping

    # Optional modules
    store_forward_enabled: bool
    range_test_enabled: bool
    external_notif_enabled: bool

    # Module-specific settings
    telemetry_interval_sec: int
    position_broadcast_sec: int
```

---

## Hardware Configuration

### Device Detection

Dude AI identifies connected hardware:

```python
@dataclass
class HardwareContext:
    hw_model: str           # e.g., "TBEAM", "RAK4631", "HELTEC_V3"
    firmware_version: str   # e.g., "2.4.0.1234"
    has_gps: bool
    has_screen: bool
    battery_level: int      # 0-100 or -1 if unknown
    voltage: float          # Battery voltage
    serial_port: str        # e.g., "/dev/ttyACM0"
    usb_product: str        # USB device description
```

### Multi-Radio Configuration

MeshForge supports multiple radio configurations simultaneously:

| Config | Radio | Port | Use Case |
|--------|-------|------|----------|
| **MOC1** | Short Turbo | 4403 | High bandwidth, short range (~3 km) |
| **MOC2** | Long Fast | 4404 | Extended range, lower bandwidth (~30 km) |
| **RNode** | RNS Direct | /dev/ttyACM0 | Reticulum native LoRa |

---

## Network Topology

### Unified Gateway Architecture

```
┌──────────────────────────────────────────────────────────────────────────┐
│                    MeshForge Gateway Architecture                         │
├──────────────────────────────────────────────────────────────────────────┤
│                                                                           │
│  MOC1 (Short Turbo)           MOC2 (Long Fast)                           │
│  High bandwidth, short range  Extended range, lower bandwidth             │
│       ↓ LoRa                       ↓ LoRa                                │
│  meshtasticd:4403             meshtasticd:4404                           │
│       ↓                            ↓                                      │
│       └──────────→ MeshForge ←─────┘                                     │
│                    Gateway                                                │
│                       ↓                                                   │
│              RNS (shared instance)                                        │
│                       ↓                                                   │
│              TCPInterface → Network RNS Server                           │
│                       ↓                                                   │
│              Wider RNS Network                                           │
│                                                                           │
└──────────────────────────────────────────────────────────────────────────┘
```

### Topology Context Model

```python
@dataclass
class NetworkTopology:
    """Network topology context for Dude AI."""

    # Meshtastic layers
    meshtastic_interfaces: List[MeshtasticInterface]

    # RNS layers
    rns_interfaces: List[RNSInterface]
    transport_enabled: bool

    # Gateway state
    gateway_running: bool
    bridge_active: bool

    # Node inventory
    local_nodes: List[UnifiedNode]
    remote_nodes: List[UnifiedNode]

    # Link quality
    link_map: Dict[str, LinkQuality]  # node_id -> quality

@dataclass
class MeshtasticInterface:
    name: str           # e.g., "MOC1", "MOC2"
    port: int           # TCP port (4403, 4404)
    modem_preset: str   # "SHORT_TURBO", "LONG_FAST"
    connected: bool
    node_count: int

@dataclass
class RNSInterface:
    name: str           # Interface name from config
    type: str           # "TCPClientInterface", "AutoInterface", etc.
    enabled: bool
    connected: bool
    target_host: Optional[str]
    target_port: Optional[int]
```

---

## RNS Interface Templates

Reticulum supports **any physical medium** - this is key to its flexibility.

Reference: [Reticulum Manual - Networks](https://reticulum.network/manual/networks.html)

### All Interface Types

| Type | Medium | Use Case |
|------|--------|----------|
| **AutoInterface** | UDP multicast | Local network discovery |
| **TCPClientInterface** | TCP/IP | Connect to remote server |
| **TCPServerInterface** | TCP/IP | Host entry point |
| **UDPInterface** | UDP | Broadcast/multicast networks |
| **RNodeInterface** | LoRa (serial) | Direct radio via RNode |
| **SerialInterface** | Serial link | Point-to-point wired |
| **KISSInterface** | TNC (serial) | External packet radio TNC |
| **I2PInterface** | I2P overlay | Anonymous/censorship-resistant |
| **PipeInterface** | Unix pipe | Inter-process communication |
| **Meshtastic_Interface** | Meshtastic TCP | Bridge to Meshtastic network |

### TCP/IP Interfaces

#### TCP Client (Connect to Network)

```ini
[[Network Name]]
  type = TCPClientInterface
  enabled = yes
  target_host = <server_ip>
  target_port = 4242
  name = Network Name
```

**Use case:** Connect to an existing RNS network hosted by someone else.

#### TCP Server (Host Entry Point)

```ini
[[My RNS Server]]
  type = TCPServerInterface
  enabled = yes
  listen_ip = 0.0.0.0
  listen_port = 4242
  name = My RNS Server
```

**Use case:** Host your own RNS entry point for others to connect.

#### UDP Interface (Broadcast/Multicast)

```ini
[[UDP Broadcast]]
  type = UDPInterface
  enabled = yes
  listen_ip = 0.0.0.0
  listen_port = 4966
  forward_ip = 255.255.255.255
  forward_port = 4966
```

**Use case:** Local network broadcast, multicast groups.

### Local Discovery

#### Auto Interface

```ini
[[Default Interface]]
  type = AutoInterface
  enabled = yes
  group_id = reticulum
```

**Use case:** Auto-discover RNS nodes on local network (uses UDP multicast).

### Radio Interfaces

#### RNode Interface (LoRa)

```ini
[[My RNode]]
  type = RNodeInterface
  interface_enabled = True
  port = /dev/ttyACM0
  frequency = 903625000
  txpower = 22
  bandwidth = 250000
  spreadingfactor = 7
  codingrate = 5
  name = My RNode
```

**Use case:** Direct LoRa communication via RNode hardware.

#### Meshtastic Interface (Bridge)

```ini
[[Meshtastic Bridge]]
  type = Meshtastic_Interface
  enabled = yes
  target_host = localhost
  target_port = 4403
```

**Use case:** Bridge RNS to Meshtastic network via meshtasticd TCP.

### Serial Interfaces

#### Serial Interface (Point-to-Point)

```ini
[[Serial Link]]
  type = SerialInterface
  enabled = yes
  port = /dev/ttyUSB0
  speed = 115200
  databits = 8
  parity = none
  stopbits = 1
```

**Use case:** Direct wired connection between two nodes.

#### KISS Interface (TNC)

```ini
[[KISS TNC]]
  type = KISSInterface
  enabled = yes
  port = /dev/ttyUSB0
  speed = 9600
```

**Use case:** External packet radio TNC (Terminal Node Controller).

### Overlay Networks

#### I2P Interface (Anonymous)

```ini
[[I2P Tunnel]]
  type = I2PInterface
  enabled = yes
  peers = <destination>.b32.i2p
```

**Use case:** Privacy, censorship resistance via I2P network.

### Template: Gateway Client Setup

For connecting a MeshForge gateway to an existing RNS network:

```ini
# Auto-discovery on local network
[[Default Interface]]
  type = AutoInterface
  enabled = yes

# Connect to RNS network
[[RNS Network]]
  type = TCPClientInterface
  enabled = yes
  target_host = <NETWORK_SERVER_IP>
  target_port = 4242
  name = RNS Network
```

### Template: Gateway Server Setup

For hosting an RNS entry point that others connect to:

```ini
# Auto-discovery on local network
[[Default Interface]]
  type = AutoInterface
  enabled = yes

# Host entry point
[[My Network Server]]
  type = TCPServerInterface
  enabled = yes
  listen_ip = 0.0.0.0
  listen_port = 4242
  name = My Network Server

# Optional: Connect to public testnet for wider reach
[[RNS Testnet Dublin]]
  type = TCPClientInterface
  enabled = no  # Enable when ready
  target_host = dublin.connect.reticulum.network
  target_port = 4965
```

Reference: [Reticulum Manual - Interfaces](https://reticulum.network/manual/interfaces.html)

---

## Connection Pool Management

### RNS Connection Architecture

Reticulum manages connections automatically, but Dude AI should understand:

```python
@dataclass
class ConnectionPool:
    """RNS connection pool state."""

    # Active connections
    active_interfaces: List[str]

    # Connection states
    tcp_connections: Dict[str, ConnectionState]

    # Traffic stats
    bytes_in: int
    bytes_out: int
    packets_in: int
    packets_out: int

    # Announce state
    announced_destinations: List[str]
    discovered_destinations: List[str]

class ConnectionState(Enum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"
    FAILED = "failed"
```

### Connection Patterns

| Pattern | Description | When to Use |
|---------|-------------|-------------|
| **Single Connection** | One TCPClientInterface to server | Simple client setup |
| **Multi-Uplink** | Multiple TCPClientInterfaces | Redundancy, load distribution |
| **Proxy Access** | Connect via I2P or Tor | Privacy, censorship resistance |
| **Direct Radio** | RNodeInterface only | Isolated network, no internet |

### Reconnection Behavior

TCP interfaces auto-reconnect on failure:

```python
# RNS handles this internally, but Dude AI should track:
@dataclass
class ReconnectionContext:
    last_connected: datetime
    disconnect_reason: str
    reconnect_attempts: int
    next_retry: datetime
    backoff_seconds: int  # Exponential backoff
```

---

## Queue Management

### Message Queue Context

MeshForge uses SQLite-backed persistent queuing:

```python
@dataclass
class QueueContext:
    """Message queue state for Dude AI awareness."""

    # Queue stats
    pending_count: int
    failed_count: int
    delivered_count: int

    # By destination
    by_destination: Dict[str, int]  # dest_id -> pending count

    # Delivery metrics
    avg_delivery_time_ms: float
    retry_rate: float  # % of messages needing retry

    # Oldest pending
    oldest_pending_timestamp: datetime

    # Queue health
    queue_healthy: bool
    backpressure_active: bool

@dataclass
class QueuedMessage:
    id: str
    destination: str
    content_type: str
    priority: int
    created_at: datetime
    attempts: int
    last_attempt: Optional[datetime]
    next_retry: Optional[datetime]
    status: str  # "pending", "sending", "delivered", "failed"
```

### Queue Request Patterns

```python
# Dude AI should understand queue operations:

class QueueOperations:
    def enqueue(self, message: Message) -> str:
        """Add message to queue. Returns message_id."""
        pass

    def get_status(self, message_id: str) -> QueuedMessage:
        """Get current status of queued message."""
        pass

    def retry_failed(self) -> int:
        """Retry all failed messages. Returns count."""
        pass

    def purge_expired(self, max_age_hours: int = 24) -> int:
        """Remove messages older than max_age. Returns count."""
        pass
```

---

## System State

### Current State Model

```python
@dataclass
class SystemState:
    """Complete system state for Dude AI context."""

    # Services
    meshtasticd_running: bool
    rnsd_running: bool
    gateway_running: bool

    # Network health
    meshtastic_nodes_online: int
    rns_destinations_known: int

    # Resources
    cpu_percent: float
    memory_percent: float
    disk_free_gb: float

    # Recent events
    last_message_sent: datetime
    last_message_received: datetime
    last_error: Optional[str]

    # Uptime
    meshforge_uptime_seconds: int
    system_uptime_seconds: int
```

### Health Indicators

| Indicator | Healthy | Warning | Critical |
|-----------|---------|---------|----------|
| Message delivery rate | > 95% | 80-95% | < 80% |
| Average latency | < 5s | 5-30s | > 30s |
| Queue depth | < 100 | 100-500 | > 500 |
| Failed messages | < 5% | 5-20% | > 20% |
| Node availability | > 90% | 70-90% | < 70% |

---

## Past Issues & Solutions

Dude AI references the persistent issues database:

**Location:** `.claude/foundations/persistent_issues.md`

### Quick Reference

| Issue Code | Problem | Solution |
|------------|---------|----------|
| **MF001** | `Path.home()` returns `/root` with sudo | Use `get_real_user_home()` |
| **MF002** | Command injection risk | Never use `shell=True` |
| **MF003** | Silent exception swallowing | Always specify exception type |
| **MF004** | Subprocess hangs | Always use `timeout=` |

### Contextual Problem Detection

```python
def detect_known_issue(symptom: str, context: SystemState) -> Optional[str]:
    """Match symptom to known issue."""

    # MF001: Wrong home directory
    if "FileNotFoundError" in symptom and "/root/.config" in symptom:
        return "MF001: Path.home() bug - use get_real_user_home()"

    # Connection issues
    if "Connection refused" in symptom:
        if not context.meshtasticd_running:
            return "meshtasticd service not running"
        if not context.rnsd_running:
            return "rnsd service not running"

    # Queue backup
    if context.queue_depth > 500:
        return "Message queue backlog - check destination availability"

    return None
```

---

## Implementation Notes

### Context Updates

Context should be refreshed:
- **User context:** On connection/reconnection
- **Hardware context:** On device detection
- **Topology:** Every 30 seconds or on change
- **Queue state:** Every 5 seconds
- **System state:** Every 10 seconds

### Privacy Considerations

- Node IDs and callsigns stay local
- No mesh data sent to cloud without explicit consent
- Context used only for local diagnostics
- API mode (PRO) sends only anonymized summaries

### Integration Points

1. **ClaudeAssistant.set_network_context()** - Feed topology data
2. **DiagnosticEngine.diagnose()** - Include context for better analysis
3. **KnowledgeBase.query()** - Context-aware responses
4. **UI Panels** - Display relevant context to user

---

## Fresh Install Landmarks

**Goal:** Achieve a successful, minimal-intervention installation.

> "We need that landmark" - WH6GXZ

### MOC2 (Long Fast) Installation Checklist

This is the reference installation for a **Long Fast** configuration with RNS bridge.

#### Prerequisites

- [ ] Raspberry Pi or compatible Linux system
- [ ] Meshtastic-compatible LoRa hardware (USB)
- [ ] Python 3.9+
- [ ] Internet connection (for initial setup)

#### Stage 1: System Preparation

```bash
# Update system
sudo apt update && sudo apt upgrade -y

# Install dependencies
sudo apt install -y python3-pip python3-venv git

# Create virtual environment (recommended)
python3 -m venv ~/.venv/meshforge
source ~/.venv/meshforge/bin/activate
```

**Landmark 1:** System updated, venv active

#### Stage 2: Install meshtasticd

```bash
# Install Meshtastic daemon
pip install meshtastic

# Verify installation
meshtastic --version
```

**Landmark 2:** `meshtastic --version` returns version number

#### Stage 3: Configure LoRa Hardware

```bash
# Detect connected device
meshtastic --info

# Set modem preset (Long Fast for MOC2)
meshtastic --set lora.modem_preset LONG_FAST

# Set region (US for 900 MHz)
meshtastic --set lora.region US

# Verify settings
meshtastic --get lora
```

**Landmark 3:** `meshtastic --info` shows device with correct preset

#### Stage 4: Start meshtasticd Service

```bash
# Create service file
sudo tee /etc/systemd/system/meshtasticd.service << 'EOF'
[Unit]
Description=Meshtastic Daemon
After=network.target

[Service]
Type=simple
User=<YOUR_USER>
ExecStart=/home/<YOUR_USER>/.venv/meshforge/bin/meshtasticd
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

# Enable and start
sudo systemctl daemon-reload
sudo systemctl enable meshtasticd
sudo systemctl start meshtasticd

# Verify
systemctl status meshtasticd
```

**Landmark 4:** `systemctl status meshtasticd` shows "active (running)"

#### Stage 5: Install RNS

```bash
# Install Reticulum
pip install rns

# Generate default config
rnsd --config

# Verify
rnsd --version
```

**Landmark 5:** `rnsd --version` returns version number

#### Stage 6: Configure RNS for Gateway

```bash
# Edit RNS config
nano ~/.reticulum/config
```

Minimum viable config:

```ini
[reticulum]
enable_transport = False
share_instance = Yes
shared_instance_port = 37428

[logging]
loglevel = 4

[interfaces]
[[Default Interface]]
  type = AutoInterface
  enabled = yes

# Gateway to RNS network (optional - for network connectivity)
# [[RNS Network]]
#   type = TCPClientInterface
#   enabled = yes
#   target_host = <server_ip>
#   target_port = 4242
```

**Landmark 6:** `~/.reticulum/config` exists with valid syntax

#### Stage 7: Start rnsd Service

```bash
# Create service file
sudo tee /etc/systemd/system/rnsd.service << 'EOF'
[Unit]
Description=Reticulum Network Stack Daemon
After=network.target

[Service]
Type=simple
User=<YOUR_USER>
ExecStart=/home/<YOUR_USER>/.venv/meshforge/bin/rnsd
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

# Enable and start
sudo systemctl daemon-reload
sudo systemctl enable rnsd
sudo systemctl start rnsd

# Verify
systemctl status rnsd
```

**Landmark 7:** `systemctl status rnsd` shows "active (running)"

#### Stage 8: Install MeshForge

```bash
# Clone repository
git clone https://github.com/Nursedude/meshforge.git
cd meshforge

# Install
pip install -e .

# Verify
python3 -c "from src.__version__ import __version__; print(__version__)"
```

**Landmark 8:** Version prints (e.g., "0.4.7-beta")

#### Stage 9: Launch MeshForge

```bash
# Launch with auto-detect UI
sudo python3 src/launcher.py

# Or standalone mode (zero dependencies)
python3 src/standalone.py
```

**Landmark 9:** MeshForge UI displays, shows connected node

#### Stage 10: Verify Full Stack

```bash
# Check all services
systemctl status meshtasticd rnsd

# Check Meshtastic connection
meshtastic --info

# Check RNS status
rnstatus
```

**FINAL LANDMARK:** All three commands succeed, MeshForge shows nodes

---

### Troubleshooting Fresh Install

| Symptom | Check | Fix |
|---------|-------|-----|
| "Connection refused" on 4403 | `systemctl status meshtasticd` | Restart service |
| "No serial port found" | `ls /dev/ttyACM* /dev/ttyUSB*` | Check USB connection |
| "Permission denied" on serial | User in dialout group? | `sudo usermod -a -G dialout $USER` |
| RNS "shared instance" error | Is rnsd running? | `systemctl start rnsd` |
| MeshForge shows no nodes | meshtasticd connected? | Check `meshtastic --info` |

### Dude AI Install Context

```python
@dataclass
class InstallState:
    """Track installation progress for Dude AI."""

    # Landmarks achieved
    system_updated: bool
    meshtasticd_installed: bool
    meshtasticd_running: bool
    lora_configured: bool
    rnsd_installed: bool
    rnsd_running: bool
    meshforge_installed: bool
    full_stack_verified: bool

    # Current blocker
    current_landmark: int  # 1-10
    blocker_description: Optional[str]

    # System info
    python_version: str
    os_version: str
    architecture: str
```

---

*Last updated: 2026-01-21*
*Part of Dude AI Context Awareness system*
