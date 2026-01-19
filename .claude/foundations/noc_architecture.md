# MeshForge NOC Architecture

> **Status**: APPROVED - Implementation in Progress
> **Date**: 2026-01-19
> **Authors**: WH6GXZ (Nursedude), Claude AI

---

## Vision

MeshForge is not just a client—it **IS** the Meshtastic node. When you install MeshForge, you get a complete, working mesh node with:

- meshtasticd (managed by MeshForge)
- rnsd (managed by MeshForge)
- Gateway bridge (RNS ↔ Meshtastic)
- GTK UI (primary interface)
- Web UI (remote access)
- Self-diagnostics and telemetry

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                     MeshForge NOC                               │
│                                                                 │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │               Service Orchestrator                        │  │
│  │  (starts, monitors, restarts managed services)            │  │
│  └───────────────────────────────────────────────────────────┘  │
│           │              │              │                       │
│           ▼              ▼              ▼                       │
│  ┌─────────────┐ ┌─────────────┐ ┌─────────────┐               │
│  │ meshtasticd │ │    rnsd     │ │  mosquitto  │               │
│  │  (managed)  │ │  (managed)  │ │ (optional)  │               │
│  └──────┬──────┘ └──────┬──────┘ └─────────────┘               │
│         │               │                                       │
│         ▼               ▼                                       │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │                  MeshForge Core                          │   │
│  │  ┌─────────────┐ ┌─────────────┐ ┌─────────────┐        │   │
│  │  │   Gateway   │ │ Node Tracker│ │ Diagnostics │        │   │
│  │  │   Bridge    │ │             │ │   Engine    │        │   │
│  │  └─────────────┘ └─────────────┘ └─────────────┘        │   │
│  └─────────────────────────────────────────────────────────┘   │
│                          │                                      │
│           ┌──────────────┴──────────────┐                      │
│           ▼                              ▼                      │
│  ┌─────────────────┐          ┌─────────────────┐              │
│  │     GTK UI      │          │     Web UI      │              │
│  │ (local primary) │          │ (remote access) │              │
│  └─────────────────┘          └─────────────────┘              │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
          │                              │
          ▼                              ▼
   /dev/ttyUSB0                    TCP/HTTP
   (local radio)               (remote clients)
```

---

## Service Orchestrator

### Responsibilities

1. **Install** - Set up meshtasticd, rnsd on first run
2. **Start** - Launch services in correct order with health checks
3. **Monitor** - Watch for failures, auto-restart
4. **Stop** - Clean shutdown of all services

### Managed Services

| Service | Port | Purpose | Required |
|---------|------|---------|----------|
| meshtasticd | 4403 | Meshtastic daemon | Yes |
| rnsd | - | Reticulum daemon | Yes (for gateway) |
| mosquitto | 1883 | MQTT broker | Optional |

### Startup Sequence

```python
STARTUP_ORDER = [
    ('meshtasticd', 5),   # Start first, wait 5s for device init
    ('rnsd', 3),          # Start after meshtasticd
    ('meshforge-core', 0) # Start last
]
```

### Health Monitoring

```python
def health_check(self, service: str) -> bool:
    """Double-tap verification."""
    # First check: systemctl is-active
    if not self._systemctl_active(service):
        return False

    # Second check: functional verification
    if service == 'meshtasticd':
        return self._check_port(4403)
    elif service == 'rnsd':
        return self._check_rnsd_responding()

    return True
```

---

## Install Flow

### Fresh Install (No meshtasticd)

```
┌─────────────────────────────────────────────────────────────┐
│              MeshForge Installation                         │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  [1/6] Installing system dependencies...          ✓        │
│  [2/6] Installing meshtasticd...                  ✓        │
│  [3/6] Installing Reticulum (RNS)...              ✓        │
│  [4/6] Installing MeshForge...                    ✓        │
│  [5/6] Detecting radio hardware...                          │
│        └─ Found: /dev/ttyUSB0 (T-Beam)            ✓        │
│  [6/6] Starting services...                       ✓        │
│                                                             │
│  ✓ MeshForge NOC is ready!                                 │
│                                                             │
│  Your node is now part of the mesh network.                │
│  Run 'meshforge' to open the interface.                    │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### Existing meshtasticd Detected

```
┌─────────────────────────────────────────────────────────────┐
│              MeshForge Installation                         │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  Detected: meshtasticd is already installed                │
│                                                             │
│  How would you like MeshForge to work?                     │
│                                                             │
│  ● Take ownership (Recommended)                            │
│    MeshForge will manage meshtasticd as part of the        │
│    NOC stack. Best for dedicated mesh nodes.               │
│                                                             │
│  ○ Connect as client                                       │
│    Use existing meshtasticd. For when another tool         │
│    manages the service.                                    │
│                                                             │
│  ○ Remote admin only                                       │
│    No local meshtasticd. Connect to remote nodes.          │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

---

## Configuration

### MeshForge Config (`~/.config/meshforge/noc.yaml`)

```yaml
# NOC Mode Configuration
noc:
  mode: "local"  # local | client | remote-only

  services:
    meshtasticd:
      managed: true
      auto_install: true
      device: "/dev/ttyUSB0"  # or "auto"

    rnsd:
      managed: true
      auto_install: true

    mosquitto:
      managed: false  # optional
      auto_install: false

  startup:
    auto_start_services: true
    health_check_interval: 30  # seconds
    restart_on_failure: true
    max_restart_attempts: 3

  remote:
    # For remote-only mode
    meshtasticd_host: ""
    meshtasticd_port: 4403
```

---

## CLI Integration

### Double-Tap Pattern

The meshtastic CLI sometimes fails then works on retry. MeshForge wraps this:

```python
class MeshtasticCLI:
    """Wrapper with retry logic for meshtastic CLI."""

    MAX_RETRIES = 3
    RETRY_DELAYS = [1, 2, 3]  # seconds

    def run_command(self, args: list, timeout: int = 30) -> Result:
        """Run meshtastic CLI with double-tap retry."""
        for attempt in range(self.MAX_RETRIES):
            try:
                result = subprocess.run(
                    ['meshtastic', '--host', 'localhost'] + args,
                    capture_output=True,
                    text=True,
                    timeout=timeout
                )
                if result.returncode == 0:
                    return Result(success=True, output=result.stdout)

            except Exception as e:
                logger.warning(f"CLI attempt {attempt+1} failed: {e}")

            if attempt < self.MAX_RETRIES - 1:
                time.sleep(self.RETRY_DELAYS[attempt])

        return Result(success=False, error="Max retries exceeded")
```

---

## Systemd Integration

### meshforge.service (Updated)

```ini
[Unit]
Description=MeshForge Mesh Network Operations Center
Documentation=https://github.com/Nursedude/meshforge
# MeshForge orchestrates services, doesn't depend on them
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/meshforge
# Orchestrator starts meshtasticd, rnsd before core
ExecStart=/opt/meshforge/venv/bin/python src/orchestrator.py
ExecStop=/opt/meshforge/venv/bin/python src/orchestrator.py --stop
Restart=on-failure
RestartSec=10

Environment=PYTHONUNBUFFERED=1
Environment=MESHFORGE_NOC_MODE=local

[Install]
WantedBy=multi-user.target
```

---

## Web UI Parity

Both GTK and Web UI serve the same data:

| Feature | GTK UI | Web UI |
|---------|--------|--------|
| Node list | ✓ | ✓ |
| Node map | ✓ | ✓ |
| Messaging | ✓ | ✓ |
| Radio config | ✓ | ✓ |
| Service status | ✓ | ✓ |
| Logs viewer | ✓ | ✓ |
| Diagnostics | ✓ | ✓ |
| Self-telemetry | ✓ | ✓ |

Web UI is served by MeshForge at `http://localhost:8880` for remote access.

---

## Security Considerations

1. **Service permissions**: meshtasticd needs access to /dev/ttyUSB*
2. **udev rules**: Auto-create rules for radio hardware
3. **Web UI**: Default to localhost-only, opt-in for network access
4. **No external meshtasticd**: When MeshForge owns meshtasticd, block external connections to prevent contention

---

## Migration Path

### Phase 1: Service Orchestrator (This PR)
- Create `src/core/orchestrator.py`
- Update install.sh to install meshtasticd + rnsd
- Add installation wizard for existing setups

### Phase 2: CLI Integration
- Wrap meshtastic CLI with retry logic
- Integrate radio config into orchestrator

### Phase 3: Web UI Parity
- Ensure web UI has feature parity with GTK
- Add remote access configuration

### Phase 4: Self-Diagnostics
- Node sees own telemetry
- Health dashboard shows service status
- Log aggregation from all managed services

---

## Related Documents

- `.claude/research/meshforge_native_meshtastic.md` - Native integration research
- `.claude/foundations/persistent_issues.md` - Issues #17, #18, #20 (solved by this)
- `.claude/foundations/domain_architecture.md` - Core vs Plugin model

---

*Made with aloha for the mesh community*
