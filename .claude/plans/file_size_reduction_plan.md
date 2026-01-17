# MeshForge File Size Reduction Plan

> **Guideline**: Files over 1,500 lines should be split for maintainability.
> **Source**: CLAUDE.md File Size Guidelines

---

## Current Large Files (as of 2026-01-17)

| File | Lines | Priority | Recommendation |
|------|-------|----------|----------------|
| `gtk_ui/panels/mesh_tools.py` | 2,203 | HIGH | Extract GPS logic, map rendering |
| `gtk_ui/panels/hamclock.py` | 2,107 | HIGH | Extract HamClockAPIClient class |
| `launcher_tui/main.py` | 1,849 | MEDIUM | Extract menu handlers to modules |
| `gtk_ui/panels/tools.py` | 1,842 | MEDIUM | Split by tool category |
| `tui/app.py` | 1,796 | MEDIUM | Extract pane classes |
| `core/diagnostics/engine.py` | 1,677 | LOW | Extract rule definitions |
| `gtk_ui/panels/ham_tools.py` | 1,657 | LOW | Extract calculator logic |
| `gtk_ui/app.py` | 1,532 | LOW | Extract signal handlers |

---

## Extraction Patterns

### Pattern 1: API Client Extraction (hamclock.py)

**Before:**
```python
# hamclock.py - 2,107 lines
class HamClockPanel(Gtk.Box):
    def _fetch_space_weather(self): ...
    def _parse_dx_data(self): ...
    def _update_aurora(self): ...
```

**After:**
```python
# hamclock_api.py - new file
class HamClockAPIClient:
    def fetch_space_weather(self): ...
    def parse_dx_data(self): ...
    def get_aurora_status(self): ...

# hamclock.py - reduced
from .hamclock_api import HamClockAPIClient

class HamClockPanel(Gtk.Box):
    def __init__(self):
        self.api = HamClockAPIClient()
```

### Pattern 2: Menu Handler Extraction (launcher_tui/main.py)

**Before:**
```python
# main.py - 1,849 lines
class TUILauncher:
    def handle_network_menu(self): ...
    def handle_radio_menu(self): ...
    def handle_tools_menu(self): ...
```

**After:**
```python
# handlers/network.py
def handle_network_menu(app): ...

# handlers/radio.py
def handle_radio_menu(app): ...

# main.py - reduced
from .handlers import network, radio, tools
```

### Pattern 3: Pane Module Extraction (tui/app.py)

**Before:**
```python
# app.py - 1,796 lines with embedded pane classes
class DashboardPane(Container): ...
class ServicesPane(Container): ...
class ConfigPane(Container): ...
```

**After:**
```python
# panes/dashboard.py
class DashboardPane(Container): ...

# panes/services.py
class ServicesPane(Container): ...

# app.py - reduced, imports panes
from .panes import DashboardPane, ServicesPane
```

---

## Implementation Order

### Sprint 2.1: High Priority (>2000 lines)
1. `mesh_tools.py` → Extract `GPSHandler`, `MapRenderer`
2. `hamclock.py` → Extract `HamClockAPIClient`

### Sprint 2.2: Medium Priority (1500-2000 lines)
3. `launcher_tui/main.py` → Extract handlers/
4. `gtk_ui/panels/tools.py` → Split by category
5. `tui/app.py` → Move panes to panes/

### Sprint 2.3: Lower Priority (<1700 lines)
6. `core/diagnostics/engine.py` → Extract rules
7. `ham_tools.py` → Extract calculators
8. `gtk_ui/app.py` → Extract signal handlers

---

## Success Criteria

- [ ] No file exceeds 1,500 lines
- [ ] All tests still pass after extraction
- [ ] Import structure remains clean
- [ ] No circular imports introduced

---

## Notes

- Each extraction should be a single focused commit
- Run tests after each extraction
- Update API dependencies document if public APIs change
- Preserve backward compatibility for any exported functions

---

*Created: 2026-01-17*
*Part of MeshForge Infrastructure Improvements*
