# MeshForge API Dependencies

> **Purpose**: Document API contracts and dependencies to prevent fix-break cycles.
> **Last Updated**: 2026-01-17
> **Maintainer**: Review when APIs change or tests fail unexpectedly.

---

## Core Principle

When changing a function's return type, parameters, or behavior:
1. Check this document for dependents
2. Update ALL listed files
3. Update the corresponding tests
4. Update this document

---

## Knowledge Base Module

### `knowledge_base.query()`

**Location**: `src/utils/knowledge_base.py:922`

**Signature**:
```python
def query(self, question: str, max_results: int = 3) -> List[Tuple[KnowledgeEntry, float]]
```

**Contract**:
- ALWAYS returns a list (never None)
- Empty list if no matches
- Each element is `(KnowledgeEntry, float)` tuple
- Results sorted by relevance (highest first)

**Callers**:
| File | Usage | Notes |
|------|-------|-------|
| `utils/claude_assistant.py` | `_ask_standalone()` | Checks `if not results` before access |
| `launcher_tui/ai_tools_mixin.py` | TUI knowledge query | Line 214 |
| `tests/test_ai_tools.py` | `TestKnowledgeBase` | Tests tuple unpacking |
| `tests/test_diagnostics.py` | Integration tests | Multiple usages |

**Breaking Change Impact**: 4 files

---

## Analytics Module

### `AnalyticsStore.get_network_health_history()`

**Location**: `src/utils/analytics.py:266`

**Signature**:
```python
def get_network_health_history(self, hours: int = 24) -> List[NetworkHealthMetrics]
```

**Contract**:
- ALWAYS returns a list (never None)
- Empty list if no data
- Results ordered newest-first
- Thread-safe

**Callers**:
| File | Usage | Notes |
|------|-------|-------|
| `web/blueprints/analytics.py` | Health API endpoint | Returns JSON |
| `tests/test_analytics.py` | `test_record_network_health` | Tests method name |

**Breaking Change Impact**: 2 files

**Historical Note**: Method was previously named `get_health_history()` - renamed for clarity.

---

## Service Check Module

### `check_service()`

**Location**: `src/utils/service_check.py:288`

**Signature**:
```python
def check_service(name: str, port: Optional[int] = None, host: str = 'localhost') -> ServiceStatus
```

**Contract**:
- ALWAYS returns ServiceStatus (never None)
- `ServiceStatus.available`: bool
- `ServiceStatus.state`: ServiceState enum
- `ServiceStatus.fix_hint`: Actionable string

**Callers**:
| File | Usage | Notes |
|------|-------|-------|
| `gtk_ui/panels/dashboard.py` | Service status display | Line 195 |
| `gtk_ui/panels/service.py` | Service panel | Line 242 |
| `gtk_ui/panels/rns_mixins/gateway.py` | Gateway panel | Line 390 |
| `tui/panes/dashboard.py` | TUI dashboard | Line 304 |
| `tui/app.py` | TUI main app | Line 387 |
| `main_web.py` | Web interface | Line 367 |
| `gateway/bridge_cli.py` | Bridge CLI | Lines 47, 56 |
| `tests/test_service_check.py` | Multiple tests | Tests mock patterns |

**Breaking Change Impact**: 11+ files

### `check_meshtasticd_responsive()`

**Location**: `src/utils/service_check.py:92`

**Contract**:
- Returns `(bool, str)` tuple
- Called internally by `check_service()` for meshtasticd
- Calls `check_port()` internally (relevant for mocking)

**Mock Note**: When mocking `check_service` for meshtasticd, also mock `check_meshtasticd_responsive` or `check_port` will be called twice.

---

## Diagnostic Engine Module

### `diagnose()`

**Location**: `src/utils/diagnostic_engine.py:1084`

**Signature**:
```python
def diagnose(message: str, category: Category, severity: Severity,
             context: Optional[Dict] = None, source: str = "") -> Optional[Diagnosis]
```

**Contract**:
- Returns `Diagnosis` if matched, `None` otherwise
- Callers MUST check `if diagnosis:` before access
- `Diagnosis.suggestions` is a list (may be empty)

**Callers**:
| File | Usage | Notes |
|------|-------|-------|
| `utils/claude_assistant.py` | Log analysis | Line 411, iterates suggestions |
| `launcher_tui/ai_tools_mixin.py` | TUI diagnostics | Line 106 |
| `gtk_ui/panels/diagnostics.py` | Import only | Imports but uses engine methods instead |
| `tests/test_diagnostics.py` | Multiple tests | Lines 369, 440 |

**Note**: `gateway/rns_bridge.py` uses `DiagnosticEngine` directly, not `diagnose()` convenience function.

**Breaking Change Impact**: 5+ files

---

## Device Detection Module

### `identify_device_model()`

**Location**: `src/commands/rnode.py:268`

**Signature**:
```python
def identify_device_model(vid: str, pid: str, product: str = '', manufacturer: str = '') -> str
```

**Contract**:
- ALWAYS returns a string (never None)
- Returns "Unknown USB Serial" if not identified
- VID/PID are hex strings (lowercase internally)

**Known VID/PID Mappings**:
| VID:PID | Device |
|---------|--------|
| `1a86:7523` | RNode (CH340G) |
| `1a86:55d4` | RNode (CH340) / T-Beam v1.x |
| `303a:1001` | T-Beam (ESP32-S3) / Heltec V3 |
| `10c4:ea60` | RNode (CP210x) / Heltec LoRa32 |

**Test Note**: Use correct VID/PID for device type. `1a86:7523` is generic CH340G, not T-Beam specific.

---

## Coverage Analyzer Module

### `CoverageAnalyzer.analyze_coverage()`

**Location**: `src/utils/analytics.py`

**Contract**:
- Returns `CoverageStats` object
- `CoverageStats.center_point`: `[lat, lon]` list (may be empty)
- Callers should use bounds checking for center_point access

**Callers**:
| File | Usage | Notes |
|------|-------|-------|
| `web/blueprints/analytics.py` | Coverage API | Uses safe extraction |

---

## Message Queue Module

### `MessageQueue._get_connection()`

**Location**: `src/gateway/message_queue.py:161`

**Contract**:
- Context manager for SQLite connections
- On exception: rollback then re-raise
- Connection always closed in finally block

**Note**: Exception IS re-raised after rollback - not swallowed.

---

## Dependency Update Checklist

When modifying any API above:

- [ ] Update function signature
- [ ] Update API Contract comment in code
- [ ] Update this document
- [ ] Update all callers listed
- [ ] Update corresponding tests
- [ ] Run full test suite: `python3 -m pytest tests/ -v`
- [ ] Run auto-review: Check for new issues

---

## Adding New APIs

When adding new public APIs:

1. Add API Contract comment to the function docstring
2. Add entry to this document
3. List all initial callers
4. Create tests that verify the contract
5. Consider thread-safety requirements

---

## Version History

| Date | Change | Impact |
|------|--------|--------|
| 2026-01-17 | Initial documentation | N/A |
| 2026-01-17 | Added API contracts to 5 key functions | Improved reliability |
| 2026-01-17 | Verified and corrected caller lists | Fixed 7 inaccurate entries |
