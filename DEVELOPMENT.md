# MeshForge - Development Guide

This document contains critical development methods, patterns, and lessons learned for contributing to MeshForge.

---

## Table of Contents

1. [Project Structure](#project-structure)
2. [TUI Architecture](#tui-architecture)
3. [Meshtastic Integration](#meshtastic-integration)
4. [Critical Patterns](#critical-patterns)
5. [Common Pitfalls](#common-pitfalls)

---

## Project Structure

```
src/
├── launcher_tui/          # Terminal UI — PRIMARY INTERFACE
│   ├── main.py            # NOC dispatcher (whiptail/dialog)
│   ├── backend/           # Backend services
│   └── *_mixin.py         # 36 feature mixins
├── gateway/               # RNS-Meshtastic bridge
│   ├── rns_bridge.py      # Main gateway (MQTT transport)
│   ├── message_queue.py   # SQLite persistent queue
│   └── rns_transport.py   # Packet fragmentation/reassembly
├── commands/              # Command modules
│   ├── propagation.py     # Space weather (NOAA primary)
│   └── base.py            # CommandResult base class
├── utils/                 # RF tools, common utilities
│   ├── paths.py           # get_real_user_home() — sudo-safe
│   ├── service_check.py   # check_service() — SINGLE SOURCE OF TRUTH
│   ├── rf.py              # RF calculations
│   └── safe_import.py     # Optional dependency wrapper
├── monitoring/            # Node monitoring (no sudo required)
│   └── mqtt_subscriber.py # Nodeless MQTT monitoring
├── core/                  # Orchestrator, diagnostics, plugin base
├── standalone.py          # Zero-dependency RF tools
└── __version__.py         # Version and changelog
```

---

## TUI Architecture

### Mixin Pattern

MeshForge TUI uses a mixin-based architecture. Each feature is a separate mixin class composed into `MeshForgeLauncher`:

```python
class MeshForgeLauncher(
    DashboardMixin,
    GatewayMixin,
    RFToolsMixin,
    ServiceMenuMixin,
    # ... 36 mixins total
    BaseMixin,
):
    """Main TUI application."""
    pass
```

### Threading in TUI

Background work uses daemon threads. The TUI (whiptail/dialog) is blocking by nature — no thread-safety concerns like GTK:

```python
import threading

def background_work():
    result = slow_operation()
    # TUI refreshes on next menu loop — no idle_add needed

threading.Thread(target=background_work, daemon=True).start()
```

### Error Isolation with _safe_call()

All menu dispatch methods are wrapped with `_safe_call()` to prevent crashes from propagating:

```python
def _safe_call(self, func, *args, **kwargs):
    """Call function with exception isolation."""
    try:
        return func(*args, **kwargs)
    except Exception as e:
        self._show_error(f"Operation failed: {e}")
```

---

## Meshtastic Integration

### CLI Paths

The meshtastic CLI may be installed in different locations:

```python
CLI_PATHS = [
    '/root/.local/bin/meshtastic',
    '/home/pi/.local/bin/meshtastic',
    '/usr/local/bin/meshtastic',
    '/usr/bin/meshtastic',
    'meshtastic'  # Fallback to PATH
]

def find_meshtastic_cli():
    for path in CLI_PATHS:
        if Path(path).exists() or path == 'meshtastic':
            return path
    return None
```

### CLI Commands

Common meshtastic CLI commands:

```bash
# Connect to local meshtasticd
meshtastic --host localhost

# Get node list
meshtastic --host localhost --nodes

# Get full info
meshtastic --host localhost --info

# Set position (correct format)
meshtastic --host localhost --setlat 19.435175 --setlon -155.213842 --setalt 100

# Reboot device
meshtastic --host localhost --reboot
```

### TCP Interface (Python API)

For sudo-free monitoring, use the meshtastic Python API directly:

```python
from meshtastic.tcp_interface import TCPInterface
from pubsub import pub

def on_receive(packet, interface):
    """Handle received packets"""
    print(f"Received: {packet}")

def on_connection(interface, topic=pub.AUTO_TOPIC):
    """Handle connection events"""
    print(f"Connected to {interface.myInfo}")

# Subscribe to events
pub.subscribe(on_receive, "meshtastic.receive")
pub.subscribe(on_connection, "meshtastic.connection.established")

# Connect (no sudo required!)
interface = TCPInterface(hostname="localhost", portNumber=4403)

# Access node info
my_node = interface.myInfo
nodes = interface.nodes

# Clean up
interface.close()
```

### Configuration Paths

```python
CONFIG_PATHS = {
    'main': Path('/etc/meshtasticd/config.yaml'),
    'config_d': Path('/etc/meshtasticd/config.d'),      # Active configs
    'available_d': Path('/etc/meshtasticd/available.d'), # Available configs
}

# Check for both .yaml and .yml extensions
active_configs = list(config_d.glob('*.yaml')) + list(config_d.glob('*.yml'))
```

---

## Critical Patterns

### Subprocess with sudo

For operations requiring root (hardware config, service control):

```python
# Hardware enable (requires sudo)
subprocess.run(['sudo', 'raspi-config', 'nonint', 'do_spi', '0'], check=True, timeout=30)

# Service control (requires sudo)
subprocess.run(['sudo', 'systemctl', 'restart', 'meshtasticd'], check=True, timeout=30)

# Reading service status (no sudo needed)
subprocess.run(['systemctl', 'is-active', 'meshtasticd'], capture_output=True, timeout=10)
```

### Service Status (Single Source of Truth)

Always use `utils/service_check.py` — never raw subprocess for service status:

```python
from utils.service_check import check_service

status = check_service('meshtasticd')
if status.available:
    # Service is running
else:
    print(status.fix_hint)  # Actionable fix suggestion
```

### Node Count from CLI

Parse node IDs from meshtastic --nodes output:

```python
import re

result = subprocess.run(
    [cli_path, '--host', 'localhost', '--nodes'],
    capture_output=True, text=True, timeout=15
)

# Extract unique node IDs (format: !xxxxxxxx)
node_ids = re.findall(r'!([0-9a-fA-F]{8})', result.stdout)
unique_nodes = set(node_ids)
node_count = len(unique_nodes)
```

### Uptime Parsing

Handle multiple timestamp formats from journalctl:

```python
import re
from datetime import datetime

def parse_uptime(log_output):
    """Parse service start time from logs"""
    patterns = [
        r'(\w{3}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2})',  # "Jan 02 15:30:45"
        r'(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})', # "2026-01-02 15:30:45"
        r'(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})',   # ISO format
    ]

    for pattern in patterns:
        match = re.search(pattern, log_output)
        if match:
            # Parse and calculate uptime
            ...
```

---

## Common Pitfalls

### 1. Missing sudo for Hardware

**Symptom**: Permission denied errors when enabling SPI/I2C

**Solution**: Use `subprocess.run(['sudo', ...], timeout=30)` for hardware configuration.

### 2. Config File Extensions

**Symptom**: Dashboard shows "0 configs" when configs exist

**Solution**: Check both `.yaml` AND `.yml` extensions.

### 3. CLI Path Not Found

**Symptom**: "meshtastic not found" or FileNotFoundError

**Solution**: Check multiple possible installation paths via `find_meshtastic_cli()`.

### 4. Path.home() Under sudo (MF001)

**Symptom**: Config files written to `/root` instead of user's home

**Solution**: Always use `get_real_user_home()` from `utils/paths.py`.

### 5. Parsing Meshtastic CLI JSON

**CRITICAL**: The meshtastic CLI outputs JSON in Python dict format (single quotes, True/False).

```python
# Convert Python dict format to JSON before parsing
meta_str = meta_str.replace("'", '"')
meta_str = meta_str.replace("True", "true")
meta_str = meta_str.replace("False", "false")
meta = json.loads(meta_str)
```

---

## Node Monitoring Module

The `src/monitoring/` module provides sudo-free node monitoring via TCP interface.

### Quick Start

```bash
# Run the monitor (no sudo required!)
python3 -m src.monitor

# Continuous monitoring
python3 -m src.monitor --watch

# JSON output for scripting
python3 -m src.monitor --json

# Connect to remote node
python3 -m src.monitor --host 192.168.1.100
```

### Why No Sudo?

The monitoring module uses the meshtastic Python API's TCP interface which:
- Connects to port 4403 (no privileged port)
- Uses user-space networking
- Reads only, no hardware access required

This allows running lightweight monitoring on any user account.

---

## Version History

| Date | Version | Changes |
|------|---------|---------|
| 2026-02-17 | 0.5.4-beta | Updated for TUI-only architecture, removed GTK references |
| 2026-01-02 | 3.2.4 | JSON parsing patterns, daemon mode, Connected Radio section |
| 2026-01-02 | 3.2.3 | Added Node Monitoring module, @work decorator patterns |
| 2026-01-02 | 3.2.2 | Initial development guide |

---

## See Also

- [RESEARCH.md](RESEARCH.md) - Technical research and references
- [README.md](README.md) - Project overview and usage
