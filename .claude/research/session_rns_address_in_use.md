# Session Status: RNS Address-in-Use & Gateway Bridge Fixes

> **Branch**: `claude/fix-address-in-use-FktgY`
> **Date**: 2026-01-27
> **Status**: RNS path resolution fixed, config validation added, bridge hardened

---

## Session 2: Config Path Resolution & Validation (Current)

### Root Cause Found: Wrong Config Path Resolution

MeshForge was using `get_real_user_home() / ".reticulum"` for RNS config paths.
This returns `/home/<user>/.reticulum` when running with sudo, but RNS itself
uses `os.path.expanduser("~")` which resolves to `/root/.reticulum` when
running as root. Since rnsd runs as root (systemd), the config is in `/root/`.

**RNS config resolution order** (from `RNS/Reticulum.py`):
1. User-specified `configdir` parameter
2. `/etc/reticulum/config` (system-wide)
3. `~/.config/reticulum/config` (XDG-style)
4. `~/.reticulum/config` (traditional fallback)

Where `~` = effective user's home (NOT real user when sudo).

### What Was Fixed

#### 1. ReticulumPaths class (utils/paths.py)
- Now mirrors RNS's own config resolution logic
- Uses `Path.home()` (effective user), NOT `get_real_user_home()`
- Checks all 3 RNS locations: /etc/reticulum, ~/.config/reticulum, ~/.reticulum
- Clear docstring explaining WHY this differs from MeshForge's path strategy

#### 2. All RNS path references across codebase (12 files)
- `commands/rns.py` → uses `ReticulumPaths`
- `config/rns_config.py` → uses `ReticulumPaths`
- `utils/rns_config.py` → uses `ReticulumPaths`
- `utils/system.py` → uses `ReticulumPaths`
- `utils/diagnostic_engine.py` → uses `ReticulumPaths`
- `utils/gateway_diagnostic.py` → uses `ReticulumPaths`
- `core/diagnostics/engine.py` → uses `ReticulumPaths`
- `cli/diagnose.py` → uses `ReticulumPaths`
- `commands/rnode.py` → uses `ReticulumPaths`
- `launcher_tui/main.py` → uses `ReticulumPaths`
- `gateway/rns_bridge.py` → uses `ReticulumPaths`

#### 3. Proactive RNS config validation in TUI
- `_validate_rns_config_content()` - Checks for:
  - Missing `[reticulum]` section
  - Missing `share_instance = Yes` (client apps can't connect without it)
  - No interfaces configured
  - No Meshtastic_Interface (needed for mesh bridging)
- `_check_rns_setup()` - Called on RNS menu entry:
  - If no config: offers to deploy template
  - If config exists: validates and shows issues
- New "Check RNS Setup" menu option
- "No shared instance" error now checks config and shows specific issues

#### 4. Bridge RNS initialization hardened
- `_init_rns_main_thread()` now lets RNS use its own config resolution
- Removed temp config hack (was creating `/tmp/meshforge_rns_bridge/config`)
- Logs the resolved config path for debugging
- Still correctly handles: reinitialise, errno 98, ImportError

#### 5. Test updated
- `test_get_config_path_as_sudo` now correctly expects RNS config to be in
  effective user's home (not real user's home)

---

## Session 1: Error Handling (Previous - committed & pushed)

### 1. TUI: Better error messages for rnpath/rnstatus
- Captures both stdout AND stderr (RNS logs errors to stdout)
- Detects "Address already in use" specifically, shows port conflict diagnostics
- Detects "No shared instance" specifically, suggests `share_instance = Yes`
- Handles FileNotFoundError (RNS not installed) and TimeoutExpired separately
- Removed invalid `-s` flag from rnstatus (it means `--sort`, not summary)

### 2. Bridge: RNS initialization from main thread
- Added `_init_rns_main_thread()` called from `start()` before spawning threads
- Fixes "signal only works in main thread" error that permanently blocked RNS
- Handles `OSError("reinitialise")` when node_tracker already initialized
- Sets `_rns_pre_initialized` flag so `_connect_rns()` skips to LXMF setup

### 3. Bridge: errno 98 handling
- No longer falls through to identity/LXMF setup with `_reticulum = None`
- Re-checks for rnsd (race condition between initial check and bind)
- If rnsd found: marks permanent (defer to rnsd)
- If stale socket: marks transient (allows retry with backoff)

---

## RNS Architecture Knowledge (Learned)

### Shared Instance Model
- **rnsd** creates a shared instance (domain socket or TCP port 37428)
- Other programs calling `RNS.Reticulum()` auto-detect the shared instance
- When detected, they connect as clients via LocalInterface (no interface binding)
- Requires `share_instance = Yes` in config
- Multiple programs share rnsd's interfaces transparently

### Config Path Resolution (Critical)
- RNS uses `os.path.expanduser("~")` → effective user's home
- When running as root (rnsd, sudo): `~` = `/root/`
- When running as user: `~` = `/home/<user>/`
- **MeshForge config** uses `get_real_user_home()` (persists across sudo)
- **RNS config** uses `Path.home()` (matches RNS's own resolution)

### Interface Types
| Type | Purpose | Config Key |
|------|---------|------------|
| AutoInterface | Zero-config UDP multicast local discovery | type = AutoInterface |
| TCPServerInterface | Accept TCP connections | listen_ip, listen_port |
| TCPClientInterface | Connect to TCP server | target_host, target_port |
| SerialInterface | Direct serial link | port, speed |
| RNodeInterface | LoRa via RNode hardware | port, frequency, txpower, etc. |
| Meshtastic_Interface | RNS over Meshtastic LoRa (plugin) | tcp_port, data_speed, hop_limit |

### Interface Modes
- `full` (default): All discovery, meshing, transport
- `gateway`: Like full, plus discovers paths on behalf of connected nodes
- `ap`, `ptp`, `roaming`, `boundary`: Special topology modes

### Transport Mode
- `enable_transport = Yes`: Node routes traffic for others, passes announces
- Only for stationary, always-on systems
- Without transport: node only communicates directly

### Meshtastic_Interface Plugin
- Source: https://github.com/Nursedude/RNS_Over_Meshtastic_Gateway
- Install: Copy `Meshtastic_Interface.py` to `~/.reticulum/interfaces/`
- Connection methods: serial (port), BLE (ble_port), TCP (tcp_port)
- `data_speed` presets: 0=LONG_FAST, 6=SHORT_FAST, 8=SHORT_TURBO (recommended)
- `mode = gateway`: bridges RNS packets over Meshtastic LoRa

### MeshForge Bridge vs Meshtastic_Interface
- **Meshtastic_Interface**: Transport layer. RNS packets → Meshtastic LoRa → RNS
- **MeshForge Bridge**: Application layer. LXMF messages ↔ Meshtastic text messages
- Both can coexist. Meshtastic_Interface is the foundation layer.

### RNS Ecosystem Apps (all connect to rnsd shared instance)
- **NomadNet** (`pip install nomadnet`): Terminal mesh browser, LXMF messaging
- **Sideband** (`pip install sbapp`): Mobile/desktop LXMF client
- **MeshChat** (`pip install meshchat`): Mesh chat application
- **rnsh**: SSH over RNS
- **LXST**: Real-time streaming (in development)

### RNS CLI Utilities
- `rnsd`: Daemon - keeps interfaces open, handles transport
- `rnstatus`: Shows interface status (like ifconfig for RNS)
- `rnpath`: Path table and path testing
- `rnprobe`: Probe a destination
- `rncp`: Copy files over RNS
- `rnid`: Identity management
- `rnx`: Execute commands over RNS

---

## Remaining Work

### On User's Production System
1. **Deploy correct config**: Copy `templates/reticulum.conf` to
   `/root/.reticulum/config` (the TUI now offers this automatically)
2. **Install Meshtastic_Interface plugin**: Copy to `/root/.reticulum/interfaces/`
3. **Restart rnsd**: `sudo systemctl restart rnsd`
4. **Verify**: `rnstatus` should show interfaces and shared instance

### Code Improvements (Future)
- TUI could check for Meshtastic_Interface plugin in `_check_rns_setup()`
- Setup wizard could offer full RNS setup (install, config, plugin, service)
- Bridge could validate RNS config before starting

---

## Key Files for Reference

| File | Purpose |
|------|---------|
| `src/utils/paths.py` | **ReticulumPaths** - RNS config resolution (FIXED) |
| `templates/reticulum.conf` | Complete working RNS config with Meshtastic_Interface |
| `config_templates/rns_meshtastic_gateway.conf` | Detailed gateway config with comments |
| `src/gateway/rns_bridge.py` | Main bridge - RNS init from main thread |
| `src/launcher_tui/main.py` | TUI - RNS menu with config validation |
| `src/commands/rns.py` | RNS commands module |
| `.claude/research/rns_complete.md` | RNS API reference |
| `.claude/research/rns_integration.md` | Integration patterns |
| `.claude/research/rns_comprehensive.md` | Comprehensive RNS docs |
