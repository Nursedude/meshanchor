# MeshForge - Claude Code Configuration

> **Dude AI**: Network Engineer, Physicist, Programmer, Project Manager
> **Architect**: WH6GXZ (Nursedude) - HAM General, Infrastructure Engineering, RN BSN

## Quick Context

MeshForge is a **Network Operations Center (NOC)** bridging Meshtastic and Reticulum (RNS) mesh networks. First open-source tool to unify these incompatible mesh ecosystems.

## Branch Strategy

| Branch | Version | Purpose |
|--------|---------|---------|
| `main` | `0.5.4-beta` | Stable beta — gateway bridge, TUI, monitoring, RF tools |
| `alpha/meshcore-bridge` | `0.6.0-alpha` | MeshCore integration — 3-way routing, MeshCore handler, companion radio management |

- **main** is the production-ready line. All PRs targeting stable features merge here.
  Includes tactical ops (XTOC interop, ATAK/KML/CoT), MQTT bridge, security hardening.
- **alpha/meshcore-bridge** has diverged significantly from main (~2,200 commits ahead,
  ~100 behind as of 2026-02-22). Contains MeshCore 3-way routing and handler but lacks
  main's tactical module, contact mapping, and several utilities. These are parallel
  development tracks; convergence requires a dedicated reconciliation effort.
- Feature branches use `claude/` prefix and merge via PR to the appropriate target branch.

## Development Principles

```
1. Make it work       ← First priority
2. Make it reliable   ← Security, testing
3. Make it maintainable ← Clean code, docs
4. Make it fast       ← Only when proven necessary
```

## Key Commands

```bash
# Launch
sudo python3 src/launcher_tui/main.py   # Primary interface (TUI)
python3 src/standalone.py               # Zero-dependency RF tools

# GTK4 desktop was REMOVED (TUI is the only interface now)

# Verify changes
python3 -m pytest tests/ -v       # Run tests
python3 -c "from src.__version__ import __version__; print(__version__)"

# Version is in src/__version__.py
# main: 0.5.4-beta | alpha/meshcore-bridge: 0.6.0-alpha
```

## Architecture Overview

```
src/
├── launcher_tui/      # Terminal UI — PRIMARY INTERFACE
│   ├── main.py        # NOC dispatcher (whiptail/dialog)
│   ├── meshcore_mixin.py    # MeshCore TUI menu (alpha branch)
│   ├── rns_config_mixin.py  # RNS config editor (extracted)
│   └── rns_diagnostics_mixin.py  # RNS diagnostics (extracted)
├── commands/          # Command modules
│   ├── propagation.py # Space weather & HF propagation (NOAA primary)
│   ├── hamclock.py    # HamClock client (optional/legacy)
│   └── base.py        # CommandResult base class
├── gateway/           # RNS-Meshtastic bridge
│   ├── rns_bridge.py  # Main gateway bridge
│   ├── gateway_cli.py # Headless CLI helpers (extracted)
│   ├── meshcore_handler.py    # MeshCore protocol handler (alpha branch)
│   ├── canonical_message.py   # Multi-protocol message format (alpha branch)
│   ├── meshcore_bridge_mixin.py # MeshCore bridge mixin (alpha branch)
│   ├── message_routing.py     # 3-way routing classifier (alpha branch)
│   └── message_queue.py # Persistent message queue (SQLite)
├── monitoring/        # Network monitoring
│   └── mqtt_subscriber.py # Nodeless MQTT monitoring
├── plugins/           # Protocol plugins
│   └── meshcore.py    # MeshCore plugin (alpha branch)
├── utils/             # RF tools, common utilities
│   ├── rf.py          # RF calculations (tested)
│   ├── rf_fast.pyx    # Cython optimization
│   ├── common.py      # SettingsManager
│   ├── service_check.py     # Service management (SINGLE SOURCE OF TRUTH)
│   ├── auto_review.py # Self-audit system
│   ├── diagnostic_engine.py # Intelligent diagnostics
│   ├── knowledge_base.py    # Mesh networking knowledge
│   ├── classifier.py  # Traffic routing classifier
│   ├── claude_assistant.py  # AI assistant (Standalone + PRO)
│   └── coverage_map.py      # Folium map generator
├── launcher.py        # Auto-detect (falls through to TUI)
├── standalone.py      # Zero-dependency RF tools
└── __version__.py     # Version and changelog
```

## Code Standards

### Security (Non-negotiable)
- NO `shell=True` in subprocess calls
- NO bare `except:` clauses
- Validate all user inputs
- Use `subprocess.run()` with list args and timeouts

### Style
- Python 3.9+ features OK
- Type hints encouraged
- 4-space indentation
- ~100 char line limit

## When Exploring

Use the Explore agent for:
- "Where is X implemented?"
- "How does feature Y work?"
- "What files handle Z?"

## Auto-Review System

Run self-audit:
```python
cd src && python3 -c "
from utils.auto_review import ReviewOrchestrator
r = ReviewOrchestrator()
report = r.run_full_review()
print(f'Issues: {report.total_issues}')
"
```

## Research Documents

Deep documentation in `.claude/` (~55 files after 2026-02-20 cleanup):
- `foundations/meshforge_ecosystem.md` - **ECOSYSTEM: All 5 repos, boundaries, APIs** (canonical)
- `dude_ai_university.md` - Complete project knowledge base
- `foundations/domain_architecture.md` - **ARCHITECTURE: Core vs Plugin model**
- `foundations/ai_principles.md` - Human-centered design philosophy
- `foundations/persistent_issues.md` - **CRITICAL: Known issues & fixes**
- `INDEX.md` - Full documentation index with quick lookups
- `research/README.md` - Index of 21 technical deep dives (RNS, AREDN, HamClock, RF, etc.)

## Architecture Model

**Privilege Separation** (see `foundations/domain_architecture.md`):
- **Viewer Mode** (default, no sudo): Monitoring, RF calcs, API data
- **Admin Mode** (sudo): Service control, /etc/ config, hardware

**Core vs Plugin**:
- **Core**: Gateway bridge, node tracker, RF tools, diagnostics
- **Plugins**: HamClock, AREDN, MQTT, third-party integrations

**Services run independently** - MeshForge connects to them, doesn't embed them.

## Persistent Issues (MUST READ)

Before making changes, review `.claude/foundations/persistent_issues.md`:

### Path.home() Bug
**NEVER use `Path.home()`** for user config files. It returns `/root` when running with sudo.
```python
# WRONG
config = Path.home() / ".config" / "meshforge"

# CORRECT
from utils.paths import get_real_user_home
config = get_real_user_home() / ".config" / "meshforge"
```

### WebKit Root Sandbox
WebKit doesn't work when running as root. Always provide browser fallback.

### Service Verification
Always check if services (rnsd, HamClock, meshtasticd) are running before using. Provide actionable error messages.

## Service Management (utils/service_check.py)

**SINGLE SOURCE OF TRUTH** for systemd service operations. Always use these helpers instead of raw subprocess calls.

### Checking Service Status
```python
from utils.service_check import check_service, ServiceState

# Check if service is available
status = check_service('meshtasticd')
if not status.available:
    show_error(status.message)
    show_fix(status.fix_hint)

# Check specific states
if status.state == ServiceState.FAILED:
    print("Service crashed - check logs")
elif status.state == ServiceState.NOT_RUNNING:
    print("Service stopped")
```

### Restarting Services (after config changes)
```python
from utils.service_check import apply_config_and_restart

# After modifying /etc/meshtasticd/config.yaml:
success, msg = apply_config_and_restart('meshtasticd')
if not success:
    show_error(msg)
```

### Enabling Services at Boot
```python
from utils.service_check import enable_service

# After creating a new service file:
success, msg = enable_service('rnsd')

# Enable AND start immediately:
success, msg = enable_service('meshtasticd', start=True)
```

### Daemon Reload Only
```python
from utils.service_check import daemon_reload

# After modifying service unit files:
success, msg = daemon_reload()
```

### Starting / Stopping Services
```python
from utils.service_check import start_service, stop_service

# Start a service (uses _sudo_cmd internally):
success, msg = start_service('meshtasticd')

# Stop a service before reboot:
success, msg = stop_service('meshtasticd')
```

### Writing to System Paths (/etc/)
```python
from utils.service_check import _sudo_write

# Write a systemd service file (elevates with sudo tee when not root):
success, msg = _sudo_write('/etc/systemd/system/rnsd.service', service_content)

# Write a config file:
success, msg = _sudo_write('/etc/meshtasticd/config.yaml', yaml_content)
```

### Privilege Elevation for Arbitrary Commands
```python
from utils.service_check import _sudo_cmd

# Wrap any command that needs root — adds 'sudo' only when not already root:
subprocess.run(_sudo_cmd(['raspi-config', 'nonint', 'do_spi', '0']), check=True, timeout=30)
subprocess.run(_sudo_cmd(['reboot']), timeout=10)
```

### Available Helpers
| Function | Use Case |
|----------|----------|
| `check_service(name)` | Pre-flight check before connecting |
| `apply_config_and_restart(name)` | After config file changes |
| `enable_service(name, start=False)` | After creating service files |
| `disable_service(name)` | Disable a service from boot |
| `start_service(name)` | Start a stopped service |
| `stop_service(name)` | Stop a running service |
| `restart_service(name)` | Simple restart (no daemon-reload) |
| `daemon_reload()` | After modifying .service units |
| `_sudo_cmd(cmd_list)` | Prefix command with sudo when not root |
| `_sudo_write(path, content)` | Write to /etc/ with privilege elevation |

### NOPASSWD Sudoers Rule (Turnkey Appliances)
For dedicated mesh appliances where interactive password entry is impractical:
```bash
sudo cp templates/sudoers.d/meshforge-nopasswd /etc/sudoers.d/meshforge
sudo chmod 440 /etc/sudoers.d/meshforge
sudo visudo -cf /etc/sudoers.d/meshforge  # validate syntax
```

### Note on Fallbacks
Legacy fallback patterns were removed in v0.5.2 (Issue #26). All code now imports
directly from `utils.service_check` — no try/except compatibility shims needed.
Similarly, all `safe_import('utils.paths', ...)` fallbacks were consolidated to direct
imports in v0.5.4 (Issue #5, 2026-02-20).

## safe_import Rules (utils/safe_import.py)

`safe_import` wraps optional external dependencies. **Do NOT use it for first-party modules.**

```python
# CORRECT — external/optional dependency
from utils.safe_import import safe_import
RNS, _HAS_RNS = safe_import('RNS')

# WRONG — first-party module, always exists
_check_service, _HAS_SC = safe_import('utils.service_check', 'check_service')

# CORRECT — first-party module, use direct import
from utils.service_check import check_service
```

**When to use safe_import**: meshtastic, RNS, LXMF, pubsub, psutil, paho.mqtt,
serial, gi.repository, yaml — genuinely optional external packages.

**Test patching**: Patch `_HAS_*` flags directly, not `sys.modules`:
```python
@patch('gateway.rns_transport._HAS_MESHTASTIC', True)  # ✓
@patch.dict('sys.modules', {'meshtastic': MagicMock()})  # ✗ flags already set
```

## File Size Guidelines

Split files exceeding 1,500 lines (see `.claude/foundations/persistent_issues.md` Issue #6):

**File size audit (2026-02-21):**
- ⚠️ `knowledge_content.py` (1,993 lines) - Content file by design, acceptable
- ⚠️ `service_menu_mixin.py` (1,572 lines) - Monitor; OpenHamClock/MQTT candidates for extraction
- ⚠️ `rns_bridge.py` (1,570 lines) - MeshCoreBridgeMixin + MessageRouter + gateway_cli.py extracted
- ⚠️ `nomadnet_client_mixin.py` (1,519 lines) - Monitor
- ⚠️ `commands/rns.py` (1,516 lines) - Monitor
- ⚠️ `prometheus_exporter.py` (1,505 lines) - Grew after metrics_export split, monitor
- ⚠️ `config_api.py` (1,499 lines) - Monitor
- ⚠️ `map_data_collector.py` (1,491 lines) - Monitor
- ⚠️ `launcher_tui/main.py` (1,475 lines) - 46 mixins, borderline — monitor
- ⚠️ `map_http_handler.py` (1,465 lines) - Monitor
- ⚠️ `service_check.py` (1,415 lines) - Monitor
- ✅ `metrics_export.py` (96 lines) - Split to 3 modules (hub)
- ✅ `node_tracker.py` (975 lines) - Data classes extracted
- ✅ `hamclock.py` (1,025 lines)

**Refactoring history:**
- `launcher_tui/main.py` (was 2,822 → 1,336 → 1,799 → 1,433 → 1,488 → 1,516 → 1,507 → 1,475)
- `rns_bridge.py` (was 1,991 → 1,614 → 1,694 → 1,495 → 1,485 → 1,652 → 1,525 → 1,570, MeshtasticHandler + MessageRouter + gateway_cli + MeshCoreBridgeMixin extracted)
- `node_tracker.py` (was 1,808 → 930, node_models.py extracted)
- `rns_menu_mixin.py` (was 1,524 → 1,210 → 1,904 → 1,041, rns_sniffer_mixin + rns_config_mixin + rns_diagnostics_mixin extracted)
- `metrics_export.py` (was 1,762 → 96, split to common/prometheus/influxdb)
- `hamclock.py` (2,625 → 1,525 → 1,025)
- GTK4 panels removed (TUI is now only interface)

*Note: Always check if a mixin exists before adding to main.py.*

## Commit Style

```
feat: Add new feature
fix: Bug fix
docs: Documentation
refactor: Code restructure
test: Add tests
security: Security fix
```

## Intelligent Diagnostics System

MeshForge includes an AI-native diagnostics system:

### Standalone Mode (Offline)
```python
from utils.diagnostic_engine import diagnose, Category, Severity

# Report a symptom
diagnosis = diagnose(
    "Connection refused to meshtasticd",
    category=Category.CONNECTIVITY,
    severity=Severity.ERROR
)
if diagnosis:
    print(diagnosis.likely_cause)
    print(diagnosis.suggestions)
```

### Knowledge Base Query
```python
from utils.knowledge_base import get_knowledge_base

kb = get_knowledge_base()
results = kb.query("What is SNR?")
```

### AI Assistant
```python
from utils.claude_assistant import ClaudeAssistant

assistant = ClaudeAssistant()  # Standalone mode
response = assistant.ask("Why is my node offline?")
```

### Coverage Map Generation
```python
from utils.coverage_map import CoverageMapGenerator

gen = CoverageMapGenerator()
gen.add_nodes_from_geojson(geojson_data)
gen.generate("coverage_map.html")
```

## Propagation Data Sources

**Architecture**: NOAA SWPC is the PRIMARY data source (always works). HamClock and OpenHamClock are optional enhancements.

```python
from commands import propagation
from commands.propagation import DataSource

# Get space weather (always works - uses NOAA)
result = propagation.get_space_weather()

# Configure optional sources (persists to ~/.config/meshforge/propagation.json)
propagation.configure_source(DataSource.OPENHAMCLOCK, host="localhost", port=3000)

# Get enhanced data (NOAA + optional sources)
result = propagation.get_enhanced_data()
```

**Data source priority:**
1. NOAA SWPC — Primary, always available, no dependencies
2. OpenHamClock — Optional, self-hosted Docker (port 3000)
3. HamClock (legacy) — Optional, sunsets June 2026

**Config persistence**: Source configuration auto-saves to `~/.config/meshforge/propagation.json` via `SettingsManager`. Settings survive restarts.

**For legacy code**: `commands.hamclock` still works for backward compatibility but new code should use `commands.propagation`.

## Contact

- GitHub: github.com/Nursedude/meshforge
- Callsign: WH6GXZ

---
*Made with aloha for the mesh community*
