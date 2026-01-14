# MeshForge Persistent Issues & Resolution Patterns

> **Purpose**: Document recurring issues and their proper fixes to prevent regression.
> This serves as institutional memory for development.

---

## Issue #1: Path.home() Returns /root with sudo

### Symptom
User config files, logs, and settings created in `/root/.config/meshforge/` instead of `/home/<user>/.config/meshforge/` when MeshForge is run with sudo.

### Root Cause
`Path.home()` returns the current effective user's home directory. When running `sudo python3 src/launcher.py`, the effective user is root, so `Path.home()` returns `/root`.

### Impact
- Settings don't persist between sessions
- Logs go to wrong location
- User sees "file not found" errors
- Features appear "broken" when they work correctly in isolation

### Proper Fix
**ALWAYS use `get_real_user_home()` from `utils/paths.py`** instead of `Path.home()`:

```python
# WRONG - breaks with sudo
from pathlib import Path
config_file = Path.home() / ".config" / "meshforge" / "settings.json"

# CORRECT - works with sudo
from utils.paths import get_real_user_home
config_file = get_real_user_home() / ".config" / "meshforge" / "settings.json"
```

### Files with this pattern (50+ instances as of 2026-01-06)
Many files still use `Path.home()`. Priority fixes completed:
- [x] `utils/paths.py` - Core path utilities (FIXED 2026-01-06)
- [x] `utils/common.py` - CONFIG_DIR, get_data_dir, get_cache_dir (FIXED 2026-01-06)
- [x] `utils/logging_utils.py` - LOG_DIR (FIXED earlier)
- [x] `gtk_ui/panels/hamclock.py` - Settings fallback (FIXED 2026-01-06)

### Prevention
- Use `from utils.paths import get_real_user_home, MeshForgePaths`
- Grep for `Path.home()` before committing
- Add to code review checklist

---

## Issue #2: WebKit Disabled When Running as Root

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

## Issue #3: Services Not Started/Verified

### Symptom
Features dependent on services (rnsd, meshtasticd, HamClock) fail silently because services aren't running.

### Root Cause
Code assumes services are already running instead of checking and providing feedback.

### Examples
- RNS node tracker created but `.start()` never called
- HamClock panel connects but doesn't verify service is running
- Features fail with no actionable feedback

### Proper Fix
1. **Use centralized `check_service()` utility** for pre-flight checks
2. **Provide actionable error messages** with fix hints
3. **Offer fix suggestions** (start service button, installation link)

### Implementation Pattern (Recommended)
```python
# Use the centralized service checker
from utils.service_check import check_service, ServiceState

# Before starting gateway/feature that requires services
def _on_start(self, button):
    status = check_service('meshtasticd')
    if not status.available:
        self._show_error(status.message, status.fix_hint)
        return
    # Proceed with operation...

# Quick port check
from utils.service_check import check_port
if check_port(4403):  # meshtasticd port
    connect_to_meshtasticd()
```

### Known Services (in `utils/service_check.py`)
| Service | Port | systemd name |
|---------|------|--------------|
| meshtasticd | 4403 | meshtasticd |
| rnsd | None | rnsd |
| hamclock | 8080 | hamclock |
| mosquitto | 1883 | mosquitto |

### Legacy Pattern (for reference)
```python
def _on_connection_failed(self, error):
    error_str = str(error).lower()
    if 'connection refused' in error_str:
        self.status_label.set_label("Connection refused - is service running?")
    elif 'name or service not known' in error_str:
        self.status_label.set_label("Host not found - check URL")
```

---

## Issue #4: Silent Debug-Level Logging

### Symptom
Errors occur but user/developer sees no indication because logs are at DEBUG level.

### Root Cause
Over-cautious logging to avoid "spam" means real errors are hidden.

### Proper Fix
Use appropriate log levels:
- **ERROR**: Something broke, needs attention
- **WARNING**: Something unusual, might be a problem
- **INFO**: User-visible operations (connected, saved, etc.)
- **DEBUG**: Internal details for developers

```python
# WRONG - hides important info
logger.debug(f"Connection failed: {error}")

# CORRECT - visible in normal logging
logger.info(f"[Component] Connection failed: {error}")
```

---

## Issue #5: Duplicate Utility Functions

### Symptom
Same fix implemented multiple times in different files, then only some get updated.

### Root Cause
No single source of truth for common utilities.

### Example
`_get_real_user_home()` was defined in:
- `utils/common.py`
- `utils/logging_utils.py`
- `utils/network_diagnostics.py`
- `utils/paths.py`

When one gets fixed, others remain broken.

### Proper Fix
**Single source of truth**: Define once in `utils/paths.py`, import everywhere else.

```python
# In any file needing this utility:
from utils.paths import get_real_user_home

# NOT: def _get_real_user_home(): ...  # local copy
```

---

## Development Checklist

Before committing, verify:

- [ ] No new `Path.home()` calls added (use `get_real_user_home()`)
- [ ] Error messages are actionable, not generic
- [ ] Log levels appropriate (INFO for user actions, ERROR for failures)
- [ ] Services are verified before use (use `check_service()`)
- [ ] subprocess calls have timeout parameters (MF004)
- [ ] Utilities imported from central location, not duplicated

---

## Quick Reference: Import Patterns

```python
# Paths - use these instead of Path.home()
from utils.paths import get_real_user_home, get_real_username
from utils.paths import MeshForgePaths, ReticulumPaths

# Settings
from utils.common import SettingsManager, CONFIG_DIR

# Logging
from utils.logging_utils import get_logger

# Service availability checks - use before service-dependent operations
from utils.service_check import check_service, check_port, ServiceState
```

---

---

## Issue #6: Large Files Exceeding Guidelines

### Symptom
Files exceed the 1,500 line guideline from CLAUDE.md, making them difficult to navigate, test, and maintain.

### Current Status (2026-01-10)

**Python files over 1,500 lines:**

| File | Lines | Priority | Suggested Split |
|------|-------|----------|-----------------|
| `src/main_web.py` | 3,524 | HIGH | Flask blueprints |
| `src/gtk_ui/panels/rns.py` | 3,162 | HIGH | Extract config editor |
| `src/gtk_ui/panels/tools.py` | 2,869 | MEDIUM | Split by tool category |
| `src/gtk_ui/panels/radio_config.py` | 2,496 | MEDIUM | Extract channel config |
| `src/gtk_ui/panels/mesh_tools.py` | 1,921 | LOW | Extract RF calculations |
| `src/gtk_ui/panels/hamclock.py` | 1,894 | LOW | Extract API client |
| `src/core/diagnostics/engine.py` | 1,685 | LOW | Extract rules |
| `src/tui/app.py` | 1,650 | LOW | Extract panes to modules |
| `src/gtk_ui/panels/ham_tools.py` | 1,611 | LOW | Extract tool groups |

**Markdown files over 1,000 lines:**

| File | Lines | Action |
|------|-------|--------|
| `.claude/dude_ai_university.md` | 1,206 | Consider splitting by topic |
| `.claude/foundations/ai_development_practices.md` | 1,069 | Review for outdated content |

### Proper Fix

**Priority 1: main_web.py (3,524 lines)**
```python
# Split into Flask blueprints:
src/web/
├── __init__.py           # Flask app factory
├── routes/
│   ├── api.py            # /api/* routes
│   ├── monitor.py        # /monitor/* routes
│   └── config.py         # /config/* routes
└── templates/            # Jinja templates
```

**Priority 2: rns.py (3,162 lines)**
```python
# Extract config editor:
src/gtk_ui/panels/
├── rns.py                # Main RNS panel
└── rns_config_editor.py  # Config editing dialog
```

### Prevention
- Check file length before adding new features
- Split files proactively at 1,000 lines
- Use `wc -l src/**/*.py | sort -rn | head -10` to monitor

---

## Issue #6: Textual TUI height: 1fr CSS Conflicts

### Symptom
TUI dashboard shows no visual output despite code executing correctly:
- Logs confirm widgets are queried successfully
- Data is fetched (247 nodes)
- Widget update calls complete
- BUT: Status cards stuck on "Checking...", Log panel empty, no text visible

### Root Causes (TWO related issues)

#### Cause 1: ScrollableContainer breaks height: 1fr
`DashboardPane` extended `ScrollableContainer`, but the Log widget CSS used `height: 1fr`.

#### Cause 2: CSS `overflow-y: auto` creates same problem
Even with `Container`, CSS `overflow-y: auto` on parent creates a scroll context:

```css
/* BREAKS height: 1fr in children */
TabPane > Container {
    height: 100%;
    overflow-y: auto;  /* Creates scroll context - breaks 1fr! */
}

.log-panel {
    height: 1fr;  /* Calculates to 0 in scroll context */
}
```

**Why this fails:**
- `height: 1fr` means "take one fraction of remaining space"
- Scroll contexts (ScrollableContainer OR `overflow-y: auto`) can expand infinitely
- "Remaining space" in infinite container = undefined/zero
- Widget calculates to height: 0 → invisible

### Proper Fix

**1. Use Container, not ScrollableContainer:**
```python
# WRONG
class DashboardPane(ScrollableContainer):
    pass

# CORRECT
class DashboardPane(Container):
    pass
```

**2. Never use `overflow-y: auto` on parents of `height: 1fr` children:**
```css
/* WRONG */
TabPane > Container {
    height: 100%;
    overflow-y: auto;
}

/* CORRECT */
TabPane > Container {
    height: 100%;
    /* NO overflow-y: auto */
}
```

**3. Make on_mount async and log errors:**
```python
# WRONG - silent failures
def on_mount(self):
    try:
        ...
    except Exception as e:
        pass  # Silent!

# CORRECT
async def on_mount(self):
    try:
        ...
    except Exception as e:
        logger.error(f"on_mount failed: {e}")
```

### Prevention
- Use `Container` as base class for TUI panes (not ScrollableContainer)
- Never add `overflow-y: auto` to parents of `height: 1fr` widgets
- All `on_mount` methods should be `async def on_mount(self):`
- Never use `except: pass` - always log errors
- Test TUI changes visually, not just via logs

---

---

## Issue #7: Missing File References in Launchers

### Symptom
TUI or launcher crashes when selecting a menu option because the referenced file doesn't exist.

### Example
`launcher_tui.py` line 349 referenced `gateway/bridge_cli.py` which didn't exist:
```python
subprocess.run([sys.executable, str(self.src_dir / 'gateway' / 'bridge_cli.py')])
```

### Root Cause
Adding menu options that reference new scripts without creating the scripts first.

### Proper Fix
1. **Create the script before referencing it**
2. **Add verification step**: Check all file references exist before committing
3. **Use commands layer when possible**: Instead of running scripts, use commands module

### Prevention
Run this verification before committing launcher changes:
```bash
# Check all referenced files exist
for f in src/main_gtk.py src/main.py src/web_monitor.py src/cli/diagnose.py \
         src/gateway/bridge_cli.py src/monitor.py src/launcher.py; do
  [ -f "$f" ] && echo "OK: $f" || echo "MISSING: $f"
done
```

---

## Issue #8: Outdated Fallback Version Strings

### Symptom
Application shows old version number even after version bump.

### Root Cause
Fallback version strings in try/except blocks don't get updated:
```python
try:
    from __version__ import __version__
except ImportError:
    __version__ = "0.4.3"  # Outdated!
```

### Proper Fix
1. Search for hardcoded version strings when bumping version
2. Use `grep -r "0\.4\." src/` to find all occurrences

### Prevention
```bash
# Before releasing, search for version strings
grep -rn "0\.[0-9]\.[0-9]" src/*.py | grep -v __version__.py
```

---

## Issue #9: Broad Exception Swallowing

### Symptom
Real errors are hidden because `except Exception: pass` catches everything.

### Example
```python
# BAD - hides all errors
try:
    proc.communicate(input=str(percent), timeout=1)
except Exception:
    pass

# GOOD - specific exceptions with explanation
try:
    proc.communicate(input=str(percent), timeout=1)
except (subprocess.TimeoutExpired, OSError):
    # Gauge display timeout - non-critical, UI continues
    pass
```

### Proper Fix
1. **Use specific exception types**
2. **Add comment explaining why silence is acceptable**
3. **Log at DEBUG level if truly non-critical**

### Prevention
- Grep for `except.*:.*pass` before committing
- Code review should flag broad exception handlers

---

---

## Issue #10: Lambda Closure Bug in Loops

### Symptom
Clicking different buttons (like Install buttons for different RNS components) always triggers the action for the **last** item in the loop, not the intended one.

### Root Cause
When creating lambdas inside a `for` loop, the loop variable is captured **by reference**, not by value:

```python
# WRONG - classic closure bug
for component in self.COMPONENTS:
    btn = Gtk.Button(label=component['name'])
    btn.connect("clicked", lambda b: self._install(component))  # All buttons install last component!
```

By the time any button is clicked, `component` has the value from the last iteration.

### Impact
- Wrong component gets installed
- Wrong action gets triggered
- Debugging is confusing because logs show wrong item being processed

### Proper Fix
Use a **default argument** to capture the value at iteration time:

```python
# CORRECT - capture by value using default argument
for component in self.COMPONENTS:
    btn = Gtk.Button(label=component['name'])
    btn.connect("clicked", lambda b, c=component: self._install(c))  # Each button gets its own copy
```

The `c=component` creates a new binding at each iteration, capturing the current value.

### Files Fixed (2026-01-12)
- [x] `src/gtk_ui/panels/rns.py:288` - Component install buttons
- [x] `src/gtk_ui/panels/rns_mixins/components.py:107` - Component install buttons (mixin)

### Prevention
- Search for `lambda.*for.*in` or `connect.*lambda` patterns before committing
- When using lambdas in loops, ALWAYS use default argument pattern
- Code review should flag any `lambda b: self._method(loop_var)` inside loops

---

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

## Issue #12: RNS "Address Already in Use" When Connecting as Client

### Symptom
GTK crashes or shows errors like:
```
[Error] The interface "Default Interface" could not be created
[Error] The contained exception was: [Errno 98] Address already in use
```

This happens when MeshForge tries to connect to an existing rnsd instance.

### Root Cause
`RNS.Reticulum()` reads the user's `~/.reticulum/config` which defines interfaces (like AutoInterface). Even when connecting to a shared instance, RNS tries to create these interfaces, which fails because rnsd already bound those ports.

### Wrong Fix (documented but not implemented)
The old workaround in `fresh_install_test.md` said to manually edit `~/.reticulum/config` to disable AutoInterface. This requires user intervention and doesn't scale.

### Proper Fix (2026-01-13)
MeshForge now creates a client-only config in `/tmp/meshforge_rns_client/` with:
- `share_instance = Yes`
- No interface definitions

This allows connecting to rnsd without trying to bind ports.

### Location
`src/gateway/node_tracker.py` - `_init_rns_main_thread()` method

### Prevention
- When connecting to shared RNS instances, always use a client-only config
- Never call `RNS.Reticulum()` without a configdir when rnsd is running

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

*Last updated: 2026-01-14 - Added startup performance / lazy loading fix*
