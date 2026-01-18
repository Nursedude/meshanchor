# MeshForge Technical Debt Reduction Plan

> Created: 2026-01-18
> Status: Planning Phase
> Estimated Impact: 25-35% code reduction in gtk_ui/panels

## Philosophy

**Slow and deliberate** - Each phase should be completed and verified before starting the next. Regression prevention is paramount.

---

## Phase 1: Quick Wins (Low Risk)

### 1.1 Import Boilerplate Consolidation

**Problem**: 86 try/except ImportError blocks repeated across panels.

**Solution**: Create `utils/safe_import.py`

```python
def safe_import(module: str, class_name: str = None, default=None):
    """Import with automatic fallback."""
    try:
        module_obj = __import__(module, fromlist=[class_name] if class_name else [])
        if class_name:
            return (getattr(module_obj, class_name), True)
        return (module_obj, True)
    except ImportError:
        return (default, False)

# Usage:
check_service, HAS_SERVICE_CHECK = safe_import('utils.service_check', 'check_service')
```

**Files to Update**: All gtk_ui/panels/*.py with try/except ImportError
**Estimated Savings**: 200-300 lines
**Risk**: LOW - Purely mechanical refactor

### 1.2 Configuration Centralization

**Problem**: `SETTINGS_DEFAULTS` defined in 36 different locations.

**Solution**: Create `utils/settings_schema.py`

```python
PANEL_DEFAULTS = {
    "ham_tools": {
        "hamclock_url": "http://localhost",
        "hamclock_api_port": 8082,
        "hamclock_live_port": 8081,
    },
    "aredn": {...},
    "meshbot": {...},
    # ... all panels
}

def get_panel_defaults(panel_name: str) -> dict:
    """Get defaults for a panel, merged with global defaults."""
    return {**GLOBAL_DEFAULTS, **PANEL_DEFAULTS.get(panel_name, {})}
```

**Files to Update**: All panels with SETTINGS_DEFAULTS
**Estimated Savings**: 400-600 lines
**Risk**: LOW - Values don't change, just location

---

## Phase 2: Core Refactoring (Medium Risk)

### 2.1 Threading/GLib Pattern Abstraction

**Problem**: 861 instances of same threading pattern:
```python
def _on_click(self, btn):
    def worker():
        result = slow_op()
        GLib.idle_add(self.update, result)
    threading.Thread(target=worker, daemon=True).start()
```

**Solution**: Add to `utils/gtk_helpers.py`

```python
def run_async(work_fn, success_fn, error_fn=None):
    """Background work with UI callback."""
    def worker():
        try:
            result = work_fn()
            GLib.idle_add(success_fn, result)
        except Exception as e:
            if error_fn:
                GLib.idle_add(error_fn, e)
    threading.Thread(target=worker, daemon=True).start()
```

**Estimated Savings**: 2,000-3,000 lines
**Risk**: MEDIUM - Requires careful testing of async behavior

### 2.2 Subprocess Wrapper

**Problem**: 165 subprocess.run() calls with inconsistent error handling.

**Solution**: Create `utils/subprocess_wrapper.py`

```python
def run_command_safe(cmd: list, timeout: int = 30) -> tuple[bool, str, str]:
    """Safe subprocess with consistent error handling.

    Returns: (success, stdout, stderr)
    """
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return (result.returncode == 0, result.stdout, result.stderr)
    except subprocess.TimeoutExpired:
        return (False, "", f"Timeout after {timeout}s")
    except FileNotFoundError:
        return (False, "", f"Command not found: {cmd[0]}")
```

**Estimated Savings**: 300-500 lines
**Risk**: MEDIUM - Must ensure all callers handle tuple return

---

## Phase 3: Architecture (Higher Risk)

### 3.1 Standard Panel Base Class

**Problem**: Manual margin/layout code repeated 792 times.

**Solution**: Create base class panels inherit from

```python
class StandardPanel(Gtk.Box):
    """Base class for all MeshForge panels."""

    def __init__(self, main_window=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.main_window = main_window
        self._setup_standard_margins()
        self._pending_timers = []

    def _setup_standard_margins(self):
        for margin in ['start', 'end', 'top', 'bottom']:
            getattr(self, f'set_margin_{margin}')(20)

    def schedule_timer(self, delay_ms: int, callback) -> int:
        timer_id = GLib.timeout_add(delay_ms, callback)
        self._pending_timers.append(timer_id)
        return timer_id

    def cleanup(self):
        """Cancel all timers - override to add panel-specific cleanup."""
        for timer_id in self._pending_timers:
            GLib.source_remove(timer_id)
        self._pending_timers.clear()
```

**Estimated Savings**: 400-600 lines
**Risk**: HIGH - Changes inheritance hierarchy, requires careful migration

### 3.2 Mixin Consolidation

**Problem**: HamClock split across 5 files (2,689 total lines).

**Recommendation**: Consolidate related mixins:
- Merge `hamclock_service_mixin.py` + `hamclock_api_mixin.py` → `hamclock_backend.py`
- Merge `hamclock_display_mixin.py` + `hamclock_features_mixin.py` → `hamclock_features.py`

**Target**: Max 3 mixins per major feature
**Estimated Savings**: 500-800 lines
**Risk**: HIGH - Requires understanding of all mixin interactions

---

## Phase 4: Polish (Low Priority)

### 4.1 API Fetching Abstraction

Create `utils/async_http.py` for common fetch patterns.

### 4.2 Widget Factory

Create factory methods for common UI patterns (labeled entries, status boxes, etc.)

---

## Implementation Guidelines

### Before Each Phase

1. Create feature branch: `claude/tech-debt-phase-N`
2. Run full test suite: `python3 -m pytest tests/ -v`
3. Run auto-review: `python3 scripts/lint.py`
4. Document current line counts

### During Refactoring

1. One file at a time
2. Run tests after each file
3. Commit frequently with clear messages
4. No behavioral changes - pure refactoring

### After Each Phase

1. Run full test suite
2. Manual smoke test of GTK UI
3. Compare line counts
4. Update this document with results

---

## Metrics to Track

| Metric | Before | After Phase 1 | After Phase 2 | After Phase 3 |
|--------|--------|---------------|---------------|---------------|
| Total gtk_ui lines | 27,799 | - | - | - |
| Import boilerplate | 86 | - | - | - |
| Config definitions | 36 | - | - | - |
| GLib.idle_add calls | 861 | - | - | - |
| subprocess.run calls | 165 | - | - | - |

---

## Files NOT to Touch

These files are stable and should not be refactored without explicit need:

- `utils/service_check.py` - Working correctly
- `utils/event_bus.py` - Working correctly
- `gateway/rns_bridge.py` - Critical path, stable
- `gateway/message_queue.py` - Critical path, stable

---

## Notes

- Large file issue (#6) appears resolved: main.py at 1336 lines, hamclock.py at 1518 lines
- Security issue (MF001) in intercept.py fixed 2026-01-18
- Auto-review false positive for raise-after-except fixed 2026-01-18
