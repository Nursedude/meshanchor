# MeshForge Persistent Issues — Archive

> **Purpose**: Historical record of resolved, moot, or documented issues.
> These were moved from `persistent_issues.md` to reduce file size.
> Date archived: 2026-02-12

---

## Issue #2: WebKit Disabled When Running as Root (MOOT — GTK removed)

> **Status**: No longer relevant. GTK4 UI was removed; TUI is the only interface.
> Kept for historical reference only.

### Symptom
Embedded web views (HamClock live view) show "Open in Browser" instead of embedded content.

### Root Cause
WebKit uses Chromium's sandbox which refuses to run as root for security reasons. Since MeshForge is launched with `sudo python3 src/launcher.py`, WebKit is always disabled.

### Impact
- HamClock embedded view never works
- Any WebKit-based features fail silently
- Users think features are "broken"

### Proper Fix
1. **Accept the limitation** - WebKit cannot run as root
2. **Provide clear UX feedback** explaining why and offering alternatives
3. **Long-term**: Consider polkit for privileged operations so app runs as user

### Implementation Pattern
```python
_is_root = os.geteuid() == 0

if _is_root:
    # WebKit won't work - explain why and provide alternative
    label = Gtk.Label(label="Embedded view disabled (running as root)")
    label.set_tooltip_text("WebKit cannot run embedded when MeshForge is started with sudo.")
    # Offer "Open in Browser" button
```

### Prevention
- Document this limitation in UI
- Test both as root and as user
- Consider alternative rendering for web content


---

## Issue #11: GTK4/Libadwaita Taskbar Icon Shows Generic

### Symptom
MeshForge shows a generic application icon in the taskbar/dock instead of the custom MeshForge icon, despite multiple fix attempts.

### Root Cause
GTK4/libadwaita has strict requirements for taskbar icons:
1. Icon must be in hicolor theme structure (`~/.local/share/icons/hicolor/scalable/apps/`)
2. Icon filename must match `application_id` (e.g., `org.meshforge.app.svg`)
3. Desktop entry `StartupWMClass` must match the window's actual WM_CLASS
4. Icon cache must be updated after installation
5. Some desktop environments cache icons aggressively

### Attempts Made (2026-01-12)
- [x] Install icon to `~/.local/share/icons/hicolor/scalable/apps/org.meshforge.app.svg`
- [x] Add icon theme search path with `icon_theme.add_search_path()`
- [x] Set `Gtk.Window.set_default_icon_name("org.meshforge.app")`
- [x] Update icon cache with `gtk-update-icon-cache`
- [x] Fix ownership when running as root with sudo
- [ ] **NOT YET TRIED**: Verify WM_CLASS matches StartupWMClass in .desktop file
- [ ] **NOT YET TRIED**: Use GResource to bundle icon into application
- [ ] **NOT YET TRIED**: Check if issue is desktop-environment specific (GNOME vs KDE vs XFCE)

### Files Involved
- `src/launcher_vte.py` - VTE terminal wrapper (main GTK window)
- `src/gtk_ui/app.py` - Main GTK application
- `org.meshforge.app.desktop` - Desktop entry file
- `assets/meshforge-icon.svg` - Source icon

### Next Steps to Try
1. **Verify WM_CLASS**: Run `xprop WM_CLASS` and click the MeshForge window - ensure it matches `org.meshforge.app`
2. **Check GApplication**: Ensure `application_id='org.meshforge.app'` is set correctly in Adw.Application
3. **Try GResource**: Bundle icon as GResource instead of file system installation
4. **Desktop-specific**: Test on different DEs to isolate if it's environment-specific

### Fix (2026-01-13)

Run the desktop integration installer:

```bash
cd /opt/meshforge
sudo ./scripts/install-desktop.sh
```

This installs icons to:
- `/usr/share/icons/hicolor/scalable/apps/org.meshforge.app.svg`
- `/usr/share/icons/hicolor/{48,64,128,256}x{48,64,128,256}/apps/`
- `/usr/share/pixmaps/`

Then clear the icon cache:

```bash
gtk-update-icon-cache -f /usr/share/icons/hicolor
# Log out and back in, or restart desktop environment
```

### If Still Not Working

1. Verify icon installed: `ls /usr/share/icons/hicolor/scalable/apps/org.meshforge.app.svg`
2. Check WM_CLASS: `xprop WM_CLASS` → click MeshForge window → should show `org.meshforge.app`
3. Clear DE cache: Some DEs (GNOME, KDE) cache icons aggressively


---

## Issue #13: Meshtastic CLI Auto-Detection Freezes GTK

### Symptom
GTK UI freezes when checking node count, especially if meshtasticd TCP is not running on port 4403.

### Root Cause
The `_get_node_count()` method in `src/gtk_ui/app.py` calls the meshtastic CLI. Without `--host localhost`, the CLI does USB/serial auto-detection which:
1. Takes 10-15+ seconds per call
2. Blocks threads
3. Causes thread pile-up when status timer fires every 5 seconds
4. Eventually exhausts resources and freezes GTK

### Wrong Approach
```python
# WRONG - causes freeze
result = subprocess.run(
    [cli_path, '--nodes'],  # No --host = auto-detection
    timeout=15  # Too long
)
```

### Proper Fix
1. **Quick port check FIRST** - Skip CLI entirely if port 4403 not reachable
2. **Use --host localhost** - Prevents USB/serial scanning
3. **Short timeout** - 10 seconds max
4. **Cache results** - 30 second TTL prevents rapid CLI calls

```python
# CORRECT pattern
sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
sock.settimeout(1.0)
try:
    sock.connect(("localhost", 4403))
    port_reachable = True
except:
    port_reachable = False
finally:
    sock.close()

if not port_reachable:
    return cached_value  # Don't call CLI at all

# Only call CLI if port is reachable
result = subprocess.run(
    [cli_path, '--host', 'localhost', '--nodes'],
    timeout=10
)
```

### Files Fixed (2026-01-14)
- [x] `src/gtk_ui/app.py` - `_get_node_count()` method
- [x] Added `import shutil` (was missing, caused NameError fallback)

### Regression Tests
- `tests/test_gtk_crash_fixes.py::TestNodeCountThreadSafety`
- `tests/test_gtk_crash_fixes.py::TestNodeCountCodePattern`

### Prevention
- Always use `--host localhost` when calling meshtastic CLI in GUI contexts
- Do quick port checks before expensive subprocess calls
- Keep timeouts shorter than timer intervals to prevent thread pile-up
- Run `test_gtk_crash_fixes.py` to verify patterns are correct


---

## Issue #14: GTK Panel Lifecycle - Missing Cleanup Methods

### Symptom
- GTK freezes or crashes when closing window
- Memory growth during extended use
- Timers continue firing after panels are destroyed
- Orphaned signal handlers causing callbacks on destroyed widgets

### Root Cause
Panel cleanup was fragmented:
1. Only 9 of 24 panels had cleanup() methods
2. app.py used a hard-coded list for cleanup (missed 15 panels)
3. No standardized timer/signal tracking pattern
4. Each panel reinvented resource management

### Impact
- Timer leaks: GLib.timeout_add() handlers never cancelled
- Signal leaks: GTK signals never disconnected
- Thread races: Callbacks firing after widget destruction
- File descriptor exhaustion (errno 24)

### Architectural Fix (2026-01-14)

**1. Created PanelBase class** (`src/gtk_ui/panel_base.py`):
```python
class PanelBase(Gtk.Box):
    def __init__(self, main_window):
        self._pending_timers = []
        self._signal_handlers = {}
        self._is_destroyed = False
        self.connect("unrealize", self._on_unrealize)

    def _schedule_timer(self, delay_ms, callback, *args):
        """Timer tracking with auto-cleanup"""
        timer_id = GLib.timeout_add(delay_ms, callback, *args)
        self._pending_timers.append(timer_id)
        return timer_id

    def _connect_signal(self, widget, signal_name, callback):
        """Signal tracking with auto-cleanup"""
        handler_id = widget.connect(signal_name, callback)
        self._signal_handlers[widget].append(handler_id)
        return handler_id

    def cleanup(self):
        """Auto-called on unrealize"""
        self._cancel_all_timers()
        self._disconnect_all_signals()
```

**2. Auto-discover panels in app.py** (replaces hard-coded list):
```python
def _on_close_request(self, window):
    # Auto-discover ALL panels with cleanup()
    for attr_name in dir(self):
        if attr_name.endswith('_panel'):
            panel = getattr(self, attr_name, None)
            if panel and hasattr(panel, 'cleanup'):
                panel.cleanup()
```

**3. Added cleanup() to all 24 panels**

### Files Changed
- [NEW] `src/gtk_ui/panel_base.py` - PanelBase class with resource management
- [MOD] `src/gtk_ui/app.py` - Auto-discover cleanup
- [MOD] All 24 panel files - Added cleanup() methods

### Regression Tests
- `tests/test_gtk_crash_fixes.py::TestPanelBaseResourceManagement`
- `tests/test_gtk_crash_fixes.py::TestPanelCleanupCoverage`
- `tests/test_gtk_crash_fixes.py::TestAppAutoDiscoverCleanup`
- `tests/test_gtk_crash_fixes.py::TestTimerCleanupPatterns`

### Migration Path for New Panels
New panels should inherit from PanelBase:
```python
from gtk_ui.panel_base import PanelBase

class MyNewPanel(PanelBase):
    def __init__(self, main_window):
        super().__init__(main_window)
        # Use self._schedule_timer() instead of GLib.timeout_add()
        # Use self._connect_signal() instead of widget.connect()

    def cleanup(self):
        # Your cleanup code here
        super().cleanup()  # ALWAYS call parent cleanup
```

### Prevention
- Run `python3 -m unittest tests/test_gtk_crash_fixes.py -v` before releasing
- New panels must inherit from PanelBase or implement cleanup()
- Use `_schedule_timer()` and `_connect_signal()` for resource tracking


---

## Issue #15: GTK Startup Performance - Thundering Herd

### Symptom
- GTK app takes 5-10+ seconds to become responsive after launch
- UI renders but feels sluggish immediately after startup
- Multiple threads spawning simultaneously during initialization
- CPU spikes on startup

### Root Cause (Identified via scientific analysis 2026-01-14)
**Thundering Herd Problem**: All 21+ panels instantiated at startup, each spawning threads:

1. **Panel init triggers async work**: 12+ panels call `_refresh_data()`, `_check_status()`, `_load_*()` in `__init__`
2. **Multiple concurrent timers**: 5+ timers running (1s, 2s, 3s, 5s, 30s intervals)
3. **Status timer overhead**: app.py `_update_status` made 3-5 subprocess calls every 5 seconds
4. **No lazy loading**: Panels instantiated whether user visits them or not

### Quantified Impact
- 21 panels loaded at startup
- 12+ panels spawn threads immediately on `__init__`
- 143 `threading.Thread` calls found in GTK UI code
- 5+ recurring timers competing for CPU
- Each status update: 3-5 subprocess calls with combined 15+ second timeout

### Architectural Fix (2026-01-14)

**1. Lazy Panel Loading** - Only instantiate panels on first navigation:
```python
# Store loaders but don't call them
self._panel_loaders = {"service": self._add_service_page, ...}
self._loaded_panels = set()

# Create lightweight placeholders
for name in self._panel_loaders.keys():
    placeholder = self._create_loading_placeholder(name)
    self.content_stack.add_named(placeholder, name)

# Only load dashboard (default view) at startup
self._load_panel("dashboard")

# Lazy load on navigation
def _on_nav_selected(self, listbox, row):
    if page_name not in self._loaded_panels:
        self._load_panel(page_name)
    self.content_stack.set_visible_child_name(page_name)
```

**2. Deferred Initial Refresh** - Let UI render first:
```python
# WRONG - thread spawns immediately
def __init__(self, main_window):
    self._build_ui()
    self._refresh_data()  # Spawns thread NOW

# CORRECT - defer 500ms
def __init__(self, main_window):
    self._build_ui()
    GLib.timeout_add(500, self._initial_refresh)
```

**3. Optimized Status Timer**:
- Increased interval from 5s to 10s
- Delayed first update by 2 seconds
- Removed redundant subprocess calls (pgrep, socket check)
- Added overlap prevention flag

### Files Changed
- [MOD] `src/gtk_ui/app.py` - Lazy loading, optimized timers
- [MOD] `src/gtk_ui/panels/dashboard.py` - Deferred initial refresh

### Expected Improvement
- Startup time: 5-10s → <2s
- Initial responsiveness: Immediate
- Thread count at startup: 12+ → 1-2
- Subprocess calls at startup: 10+ → 1-2

### Prevention
- New panels must NOT spawn threads in `__init__`
- Use `GLib.timeout_add(500, self._initial_refresh)` for deferred loading
- Prefer single comprehensive status calls over multiple subprocess invocations
- Test startup performance: `time python3 src/launcher.py --gtk`


---

## Issue #25: rnsd PermissionError on /etc/reticulum/storage/ratchets

### Symptom
rnsd crashes in a background thread with:
```
PermissionError: [Errno 13] Permission denied: '/etc/reticulum/storage/ratchets'
```
Additionally, `/etc/reticulum/identity` is never created, and the TUI "Show local identity" shows "No identity provided, cannot continue."

### Root Cause
RNS added **key ratcheting** support which requires a `ratchets/` subdirectory under storage. `Identity.persist_job()` runs in a background thread and calls `os.makedirs(ratchetdir)`. The install script didn't create this directory, and `ReticulumPaths.ensure_system_dirs()` was defined but never called at runtime.

### Fix (v0.5.x, 2026-02-09)
**Self-healing at runtime** — MeshForge now creates the directories automatically:
1. `startup_checks.check_all()` calls `ensure_system_dirs()` at TUI launch
2. `rns_bridge._init_rns_main_thread()` calls it before RNS init
3. `install_noc.sh` creates `storage/ratchets/` during install
4. `check_rns_storage_permissions()` diagnostic detects the issue
5. After fixing dirs, MeshForge auto-restarts rnsd via `apply_config_and_restart()`

### Files
- `src/utils/paths.py` — `ETC_RATCHETS`, `ensure_system_dirs()`
- `src/gateway/rns_bridge.py` — Self-heal in `_init_rns_main_thread()`
- `src/launcher_tui/startup_checks.py` — Self-heal in `check_all()`
- `src/core/diagnostics/checks/rns.py` — `check_rns_storage_permissions()`
- `scripts/install_noc.sh` — Pre-create dirs
- `src/launcher_tui/rns_menu_mixin.py` — Fixed `rnid` invocation

### Status: RESOLVED


---

## Issue #26: ReticulumPaths Fallback Copies Cause Config Divergence

### Symptom
`.reticulum` interface configuration is "lost" between sessions. RNS config changes made in the TUI have no effect. rnsd uses a different config file than what MeshForge reads/writes.

### Root Cause
**Four separate copies** of `ReticulumPaths` existed in the codebase:
1. `src/utils/paths.py` — **Canonical** (correct: checks `/etc/reticulum`, XDG, `~/.reticulum`)
2. `src/launcher_tui/main.py` — Fallback (missing `get_interfaces_dir`, `ensure_system_dirs`)
3. `src/launcher_tui/rns_menu_mixin.py` — Fallback (missing `ensure_system_dirs`)
4. `src/core/diagnostics/checks/rns.py` — Fallback (**WRONG: skipped `/etc/reticulum` and XDG entirely**)
5. `src/gateway/rns_bridge.py` — Fallback (missing `get_interfaces_dir`, `ensure_system_dirs`)

The diagnostics fallback (`rns.py`) was the worst — it went directly to `~/.reticulum` without checking `/etc/reticulum/config` first. If a user had both `/etc/reticulum/config` (used by rnsd) and `~/.reticulum/config` (fallback), the diagnostics would read the wrong file and report incorrect status.

Additionally, when running with sudo:
- User edits: `/home/user/.reticulum/config`
- rnsd reads: `/root/.reticulum/config` OR `/etc/reticulum/config`
- Result: silent divergence, changes have no effect

### Fix (v0.5.x, 2026-02-09)
**Eliminated all fallback copies.** Every file now imports directly:
```python
# NO try/except, NO fallback class
from utils.paths import ReticulumPaths
```
This ensures ONE definition is used everywhere. If `utils.paths` is unavailable, the import fails immediately with a clear `ImportError` — better than silently using a wrong path.

### Files Changed
- `src/launcher_tui/main.py` — Removed 16-line fallback
- `src/launcher_tui/rns_menu_mixin.py` — Removed 20-line fallback
- `src/gateway/rns_bridge.py` — Removed 20-line fallback
- `src/core/diagnostics/checks/rns.py` — Removed 20-line WRONG fallback

### Prevention
- **NEVER** duplicate `ReticulumPaths`. Always import from `utils/paths.py`.
- `utils/paths.py` is the SINGLE SOURCE OF TRUTH for all path resolution.
- If a file needs `ReticulumPaths`, import it. No try/except fallback.

### Status: RESOLVED


---

## Issue #28: API Proxy Steals fromradio Packets from Native Web Client

**Date Identified**: 2026-02-10
**Severity**: Critical (breaks meshtasticd web client at :9443)

### Symptom
When MeshForge is running, the Meshtastic web client at `ip:9443` shows
no data. Wireshark/traffic inspector shows empty responses. The gateway
bridge works fine (RX green), NomadNet talks to other RNS nodes normally.
Only the native web client is broken.

### Root Cause
`MeshtasticApiProxy` (in `gateway/meshtastic_api_proxy.py`) was **enabled
by default** (`enable_api_proxy=True` in `MapServer.__init__`).

When enabled, it runs a background thread that continuously polls
`GET /api/v1/fromradio` from meshtasticd's HTTP API on port 9443.
This endpoint is **queue-based** — each GET pops the next protobuf packet.
Once MeshForge consumes a packet, it's gone from meshtasticd's buffer.

The proxy code literally documents this: *"MeshForge becomes the sole
consumer of meshtasticd's /api/v1/fromradio"*.

Result: the native web client polls the same endpoint but gets nothing
because MeshForge already drained the queue.

**Why the gateway is unaffected:** The gateway uses TCP port 4403 (the
meshtastic Python library's `TCPInterface`), which is a completely
separate channel from the HTTP API on port 9443.

### Connection Architecture
```
Port 4403 (TCP protobuf) ── Gateway Bridge (meshtastic_handler.py)
                            └── Works independently, unaffected

Port 9443 (HTTP API)     ── MeshtasticApiProxy (DRAINS THE QUEUE)
                            └── Native web client gets nothing
```

### Fix Applied
1. **Default `enable_api_proxy` to `False`** in `MapServer.__init__`
2. **Added `--enable-api-proxy` CLI flag** for explicit opt-in
3. **`/mesh/` redirects to native `:9443`** when proxy is disabled
4. **Clear logging** when proxy is disabled explaining coexistence

### When to Enable the Proxy
Only enable when you specifically need:
- Multiple browser tabs viewing the web client simultaneously
- Phantom node filtering (MQTT nodes without User data crash React)
- MeshForge-owned packet inspection/logging

```bash
# Opt in to API proxy (disables native web client at :9443)
python -m utils.map_data_service --enable-api-proxy
```

### Files Involved
- `src/utils/map_data_service.py` — `enable_api_proxy` default changed to `False`
- `src/utils/map_http_handler.py` — `/mesh/` redirects to native :9443
- `src/gateway/meshtastic_api_proxy.py` — the proxy itself (unchanged)

### Prevention
Never enable the API proxy by default. The gateway (TCP:4403) and
web client (HTTP:9443) are separate channels and should coexist.

### Status: RESOLVED

