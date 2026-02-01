# Session Notes: File Size Refactoring

**Date**: 2026-02-01
**Branch**: `claude/session-management-setup-uBUFr`
**Session ID**: 01UjAMVUWHAjUtCZX1eeGyfb

## Completed This Session

### 1. Codebase Analysis
- Reviewed recent commits (NGINX reliability, Config API RESTful patterns)
- Identified 6 files over 1500 lines requiring refactoring
- Confirmed NomadNet Text UI fix already in place (commit 180911c)

### 2. map_data_service.py Refactoring ✅
**Before**: 2129 lines (single file)
**After**: Split into 3 modules

| File | Lines | Purpose |
|------|-------|---------|
| `src/utils/map_data_service.py` | 378 | MapServer + CLI + re-exports |
| `src/utils/map_data_collector.py` | 993 | MapDataCollector class |
| `src/utils/map_http_handler.py` | 833 | MapRequestHandler class |

- All imports/exports preserved for backward compatibility
- Syntax verified with py_compile

## Remaining Tasks

### File Size Reductions (Priority Order)
1. **knowledge_base.py** (1860 lines) - IN PROGRESS
   - Split plan: Core class + query methods → `knowledge_base.py` (~200 lines)
   - Content loaders → `knowledge_content.py` (~1700 lines)

2. **diagnostic_engine.py** (1857 lines) - PENDING
3. **core/diagnostics/engine.py** (1767 lines) - PENDING
4. **traffic_inspector.py** (1716 lines) - PENDING
5. **rns_bridge.py** (1702 lines) - PENDING

### Feature Additions
1. **RNS/RNSD tools menu** - Add to TUI (mirror GTK panel functionality)
2. **Device config wizard** - Complete setup flow in TUI

### Documentation
1. **CLAUDE.md** - Update with service_check examples (partially done per systemctl_refactor_next.md)

## Files Created This Session
- `/home/user/meshforge/src/utils/map_data_collector.py` (NEW)
- `/home/user/meshforge/src/utils/map_http_handler.py` (NEW)
- `/home/user/meshforge/src/utils/map_data_service.py` (MODIFIED - reduced to wrapper)

## Current Git Status
- Working on `alpha` branch
- Uncommitted changes:
  - Modified: `src/utils/map_data_service.py`
  - New: `src/utils/map_data_collector.py`
  - New: `src/utils/map_http_handler.py`

## Next Session Pickup

1. **Commit current work** - map_data_service split
2. **Continue knowledge_base.py** - Already analyzed structure:
   ```
   Lines 30-70: Data classes (keep in knowledge_base.py)
   Lines 72-120: KnowledgeBase __init__ + core methods
   Lines 121-350: _load_rf_knowledge
   Lines 351-547: _load_meshtastic_knowledge
   Lines 548-696: _load_reticulum_knowledge
   Lines 697-775: _load_hardware_knowledge
   Lines 776-881: _load_troubleshooting_guides
   Lines 882-924: _load_best_practices
   Lines 925-1176: _load_rns_troubleshooting
   Lines 1177-1301: _load_aredn_knowledge
   Lines 1302-1675: _load_rf_fundamentals_extended
   Lines 1676-1777: _load_mqtt_knowledge
   Lines 1778-1861: Query methods (keep in knowledge_base.py)
   ```
3. **Then remaining files** in priority order
4. **Feature additions** after file cleanup

## Commands for Next Session

```bash
# Check current state
git status
wc -l src/utils/knowledge_base.py

# Verify syntax after changes
python3 -m py_compile src/utils/map_data_service.py src/utils/map_data_collector.py src/utils/map_http_handler.py

# Commit when ready
git add src/utils/map_data_service.py src/utils/map_data_collector.py src/utils/map_http_handler.py
git commit -m "refactor: Split map_data_service.py into collector and handler modules"
```

## Notes
- User requested deep thinking approach
- Watch for session entropy (context overload)
- Work systematically with task list
- All files over 1500 lines should be split when adding new features (per CLAUDE.md)
