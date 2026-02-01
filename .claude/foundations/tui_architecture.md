# TUI Architecture Guide

> Code-level documentation for MeshForge's Terminal UI (launcher_tui)

**Date**: 2026-01-30
**Status**: Living Document
**Audience**: Developers working on the TUI codebase

---

## Overview

The TUI is MeshForge's primary interface, using whiptail/dialog for a raspi-config style experience. The architecture uses a **mixin pattern** to keep the codebase modular despite the single-class design required by whiptail integration.

### Key Design Decisions

1. **Mixin Composition**: Features are organized into `*_mixin.py` files that are combined via multiple inheritance
2. **DialogBackend Abstraction**: Whiptail/dialog commands wrapped in a clean Python API
3. **Status Bar**: Persistent service status displayed via `--backtitle`
4. **Startup Checks**: Environment detection and conflict resolution before menu loop

---

## Directory Structure

```
src/launcher_tui/
├── Entry Points
│   ├── __init__.py           # Package exports
│   ├── __main__.py           # python -m launcher_tui
│   └── main.py               # MeshForgeLauncher class + orchestration
│
├── Core Infrastructure
│   ├── backend.py            # DialogBackend - whiptail/dialog wrapper
│   ├── startup_checks.py     # Environment detection, hardware scan
│   ├── conflict_resolver.py  # Interactive port conflict resolution
│   └── status_bar.py         # Persistent status line
│
└── Feature Mixins (24 files)
    ├── Network & Mesh
    │   ├── rns_menu_mixin.py         # Reticulum menu
    │   ├── radio_menu_mixin.py       # Meshtastic radio
    │   ├── aredn_mixin.py            # AREDN integration
    │   ├── rns_interfaces_mixin.py   # RNS interfaces
    │   └── nomadnet_client_mixin.py  # NomadNet
    │
    ├── Configuration
    │   ├── meshtasticd_config_mixin.py  # meshtasticd config
    │   ├── channel_config_mixin.py      # Channels
    │   ├── first_run_mixin.py           # Setup wizard
    │   ├── settings_menu_mixin.py       # App settings
    │   └── device_backup_mixin.py       # Backup/restore
    │
    ├── Tools & Analysis
    │   ├── rf_tools_mixin.py        # RF calculators
    │   ├── rf_awareness_mixin.py    # SDR monitoring
    │   ├── ai_tools_mixin.py        # Maps, diagnostics
    │   ├── topology_mixin.py        # Network topology
    │   ├── metrics_mixin.py         # Historical metrics
    │   └── link_quality_mixin.py    # Link analysis
    │
    └── Operations
        ├── quick_actions_mixin.py   # Single-key shortcuts
        ├── emergency_mode_mixin.py  # Field operations
        ├── system_tools_mixin.py    # Linux shell, reboot
        ├── logs_menu_mixin.py       # Log viewing
        ├── service_menu_mixin.py    # Service control
        └── hardware_menu_mixin.py   # Hardware detection
```

---

## Class Hierarchy

### MeshForgeLauncher

The main class inherits from all mixins:

```python
class MeshForgeLauncher(
    RFToolsMixin,
    ChannelConfigMixin,
    AIToolsMixin,
    MeshtasticdConfigMixin,
    SitePlannerMixin,
    ServiceDiscoveryMixin,
    FirstRunMixin,
    SystemToolsMixin,
    QuickActionsMixin,
    EmergencyModeMixin,
    RNSInterfacesMixin,
    NomadNetClientMixin,
    TopologyMixin,
    RFAwarenessMixin,
    MetricsMixin,
    LinkQualityMixin,
    RNSMenuMixin,
    AREDNMixin,
    RadioMenuMixin,
    ServiceMenuMixin,
    HardwareMenuMixin,
    SettingsMenuMixin,
    LogsMenuMixin,
    DeviceBackupMixin
):
    """MeshForge launcher with raspi-config style interface."""
```

### Why Mixins?

- **No diamond inheritance**: Mixins don't inherit from each other
- **Modular features**: Each mixin is a cohesive unit
- **Shared state**: All mixins access `self.dialog` and base methods
- **File size control**: Keeps individual files under 500 lines

---

## Core Components

### DialogBackend (`backend.py`)

Terminal UI abstraction for whiptail/dialog:

```python
class DialogBackend:
    def menu(self, title: str, text: str, choices: List[Tuple[str, str]]) -> Optional[str]:
        """Show selection menu. Returns tag or None if cancelled."""

    def msgbox(self, title: str, text: str) -> None:
        """Show information message with OK button."""

    def yesno(self, title: str, text: str, default_no: bool = False) -> bool:
        """Show yes/no confirmation. Returns True for Yes."""

    def inputbox(self, title: str, text: str, init: str = "") -> Optional[str]:
        """Show text input. Returns user input or None if cancelled."""

    def infobox(self, title: str, text: str) -> None:
        """Show transient message (auto-closes)."""

    def set_status_bar(self, status_bar: StatusBar) -> None:
        """Attach status bar for --backtitle."""
```

**Implementation Details**:
- Detects whiptail (preferred) or dialog at runtime
- Redirects stderr to temp file to capture selection
- 3600s timeout (allows long interactive sessions)
- Status bar injected as `--backtitle` on every dialog

### StartupChecker (`startup_checks.py`)

Environment detection at launch:

```python
@dataclass
class EnvironmentState:
    services: Dict[str, ServiceInfo]   # meshtasticd, rnsd, mosquitto
    conflicts: List[PortConflict]      # Port usage conflicts
    hardware: HardwareInfo             # SPI/I2C/USB/GPIO
    is_root: bool
    has_display: bool
    is_ssh: bool
    is_first_run: bool
    config_exists: bool
```

**Service Monitoring**:
- Checks systemctl status + port availability
- 10-second cache TTL
- States: RUNNING, STOPPED, FAILED, UNKNOWN

**Hardware Detection**:
- SPI: `/dev/spidev*`
- I2C: `/dev/i2c-*`
- USB Serial: `/dev/ttyUSB*`, `/dev/ttyACM*`
- GPIO: `/sys/class/gpio`

### StatusBar (`status_bar.py`)

Persistent status shown at top of every dialog:

```
MeshForge v0.4.7 | meshtasticd: ● | rnsd: ○ | mqtt: ○ | Conflicts: 1
```

**Caching**:
- Service status: 10s TTL
- Space weather: 300s TTL (matches NOAA)

---

## Entry Flow

```
python -m launcher_tui
    ↓
__main__.py → main.main()
    ↓
MeshForgeLauncher()
    ├── __init__()
    │   ├── self.dialog = DialogBackend()
    │   ├── self._setup_status_bar()
    │   ├── self._startup_checker = StartupChecker()
    │   └── self.env = self._detect_environment()
    │
    └── run()
        ├── Check root privilege (exit if not)
        ├── Check dialog available (fallback if not)
        ├── _run_startup_checks()
        │   ├── Detect environment
        │   ├── Check port conflicts
        │   └── Show conflict resolution (if needed)
        ├── _check_first_run()
        │   └── _run_first_run_wizard() (if first run)
        ├── _check_service_misconfig()
        ├── _maybe_auto_start_map()
        └── _run_main_menu()  ← Main loop
```

---

## Mixin Pattern

### Anatomy of a Mixin

```python
class ExampleMixin:
    """Mixin providing feature menu functionality."""

    def _feature_menu(self):
        """Top-level menu handler."""
        while True:
            choices = [
                ("action1", "First action description"),
                ("action2", "Second action description"),
                ("back", "Back"),
            ]
            choice = self.dialog.menu("Feature Title", "Select an option:", choices)

            if choice is None or choice == "back":
                break
            elif choice == "action1":
                self._handle_action1()
            elif choice == "action2":
                self._handle_action2()

    def _handle_action1(self):
        """Implements specific action."""
        # Use self.dialog for UI
        result = self.dialog.inputbox("Input", "Enter value:", "default")
        if result:
            self.dialog.msgbox("Success", f"You entered: {result}")
```

### Available Base Methods

All mixins have access to these via `self`:

```python
# UI
self.dialog                    # DialogBackend instance
self._wait_for_enter(msg)      # Pause for user after terminal output

# Validation
self._validate_hostname(host)  # True if valid hostname
self._validate_port(port_str)  # True if 1-65535

# Environment
self._env_state               # Current EnvironmentState
self._startup_checker         # StartupChecker instance
self._detect_environment()    # Refresh environment

# Paths
self.src_dir                  # src/ directory path

# CLI
self._get_meshtastic_cli()    # Get meshtastic CLI path
```

### Menu Choices Format

Menu items are tuples: `(tag, description)`

```python
choices = [
    ("status", "View service status"),      # tag="status"
    ("restart", "Restart all services"),    # tag="restart"
    ("back", "Back to main menu"),          # tag="back"
]
choice = self.dialog.menu("Services", "Select action:", choices)
# choice is "status", "restart", "back", or None (cancelled)
```

---

## Common Patterns

### 1. Menu Loop Pattern

Every submenu uses this structure:

```python
def _some_menu(self):
    while True:
        choice = self.dialog.menu("Title", "Subtitle", choices)
        if choice is None or choice == "back":
            break
        # Handle choice...
```

### 2. Terminal Output Pattern

For commands that produce text output:

```python
def _show_logs(self):
    """Display log output in terminal."""
    subprocess.run(['clear'], check=False, timeout=5)
    print("=== Recent Logs ===\n")
    subprocess.run(['journalctl', '-u', 'meshtasticd', '-n', '50'], timeout=30)
    self._wait_for_enter("Press Enter to continue...")
```

### 3. Dynamic Method Dispatch

Used by QuickActionsMixin and others:

```python
ACTIONS = [
    ('s', 'Service status', '_qa_service_status'),
    ('n', 'Node list', '_qa_node_list'),
]

def _quick_menu(self):
    for tag, desc, method_name in ACTIONS:
        if choice == tag:
            method = getattr(self, method_name)
            method()
```

### 4. Error Handling with Fallback

```python
def _optional_feature(self):
    try:
        from utils.optional_module import feature
        feature()
    except ImportError:
        self.dialog.msgbox("Unavailable", "Feature requires optional dependency")
        return
```

### 5. Subprocess with Timeout

Always include timeouts:

```python
result = subprocess.run(
    ['meshtastic', '--info'],
    capture_output=True,
    text=True,
    timeout=30  # REQUIRED
)
```

---

## Adding New Features

### Step 1: Create Mixin File

Create `src/launcher_tui/my_feature_mixin.py`:

```python
"""My Feature mixin for MeshForge TUI."""


class MyFeatureMixin:
    """Provides my feature functionality."""

    def _my_feature_menu(self):
        """Main entry point for this feature."""
        while True:
            choices = [
                ("action1", "Do something"),
                ("action2", "Do another thing"),
                ("back", "Back"),
            ]
            choice = self.dialog.menu(
                "My Feature",
                "Select an option:",
                choices
            )

            if choice is None or choice == "back":
                break
            elif choice == "action1":
                self._my_action1()
            elif choice == "action2":
                self._my_action2()

    def _my_action1(self):
        """Handle first action."""
        self.dialog.msgbox("Action 1", "You selected action 1")

    def _my_action2(self):
        """Handle second action."""
        result = self.dialog.inputbox("Action 2", "Enter value:")
        if result:
            self.dialog.msgbox("Result", f"You entered: {result}")
```

### Step 2: Add to MeshForgeLauncher

In `main.py`, add to the import and class definition:

```python
from .my_feature_mixin import MyFeatureMixin

class MeshForgeLauncher(
    MyFeatureMixin,  # Add here
    RFToolsMixin,
    # ... rest of mixins
):
```

### Step 3: Wire to Menu

Add to appropriate parent menu:

```python
def _some_parent_menu(self):
    choices = [
        # ... existing choices
        ("myfeature", "My New Feature"),
        ("back", "Back"),
    ]
    # ... in handler:
    elif choice == "myfeature":
        self._my_feature_menu()
```

---

## Security Considerations

### Input Validation

```python
def _validate_hostname(self, host: str) -> bool:
    """Prevent flag injection in ping/DNS commands."""
    if not host or host.startswith('-'):
        return False
    # Additional validation...
    return True
```

### Path Security

Always use `get_real_user_home()` instead of `Path.home()`:

```python
from utils.paths import get_real_user_home

# CORRECT - works with sudo
home = get_real_user_home()
config = home / ".config" / "meshforge"

# WRONG - returns /root with sudo
# config = Path.home() / ".config" / "meshforge"
```

### Subprocess Safety

Never use `shell=True`:

```python
# CORRECT
subprocess.run(['meshtastic', '--info', node_id], timeout=30)

# WRONG - command injection risk
subprocess.run(f'meshtastic --info {node_id}', shell=True)
```

---

## Testing

### Running Tests

```bash
python3 -m pytest tests/ -v
```

### Mocking DialogBackend

```python
from unittest.mock import MagicMock

def test_my_feature():
    launcher = MeshForgeLauncher.__new__(MeshForgeLauncher)
    launcher.dialog = MagicMock()
    launcher.dialog.menu.return_value = "action1"
    launcher.dialog.msgbox = MagicMock()

    launcher._my_feature_menu()

    launcher.dialog.msgbox.assert_called_once()
```

---

## File Size Guidelines

Per `.claude/foundations/persistent_issues.md`, files should stay under 1,500 lines:

| File | Target | Purpose |
|------|--------|---------|
| main.py | <1,500 | Menu orchestration + base methods |
| *_mixin.py | <500 | Single feature implementation |
| backend.py | <300 | Dialog abstraction |
| startup_checks.py | <500 | Environment detection |

When a mixin exceeds 500 lines, consider splitting into:
- Core mixin (menu + dispatch)
- Helper module (utility functions)

---

## Related Documents

- `.claude/research/tui_menu_redesign.md` - UI/UX design research
- `.claude/foundations/noc_architecture.md` - High-level NOC architecture
- `.claude/foundations/domain_architecture.md` - Core vs Plugin model
- `.claude/foundations/persistent_issues.md` - Known issues (Path.home() bug, etc.)

---

*Architecture maintained by the mesh community*
