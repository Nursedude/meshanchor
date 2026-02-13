# MeshForge Reliability Engineering Plan

**Date:** 2026-02-13
**Author:** Dude AI (Claude) - MeshForge NOC
**Scope:** Harden core reliability while enabling parallel feature development
**Target:** Single Pi reference node, designed for multi-node future

---

## The Discovery: Five Health Systems That Don't Talk

MeshForge already has sophisticated health infrastructure. The problem is none of it
is connected:

```
┌─────────────────────────────────────────────────────────────────┐
│                    CURRENT STATE (disconnected)                  │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  service_check.py ──── 26 ad-hoc callers (no loop)             │
│                                                                  │
│  event_bus.py ──────── MessageEvent/ServiceEvent (NEVER EMITS) │
│                                                                  │
│  health_score.py ───── 0-100 scoring (ORPHANED, no consumers)  │
│                                                                  │
│  shared_health_state.py ── SQLite state (DISCONNECTED)         │
│                                                                  │
│  active_health_probe.py ── NGINX-style probes (NO CHECKS)     │
│                                                                  │
│  bridge_health.py ──── Gateway-specific (LOCAL only)           │
│                                                                  │
│  startup_health.py ─── One-shot at launch (NEVER AGAIN)        │
│                                                                  │
│  diagnostic_engine.py ─┐ TWO diagnostic engines                │
│  core/diagnostics/     ┘  (different APIs, both incomplete)    │
│                                                                  │
│  status_bar.py ──────── 10s cache, shows service dots          │
│                          (but doesn't use any of the above)    │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

## The Target: One Unified Health Loop

```
┌─────────────────────────────────────────────────────────────────┐
│                    TARGET STATE (unified)                        │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌──────────────────────────────────┐                           │
│  │  HealthMonitor (30s loop)        │  ← active_health_probe   │
│  │  ├─ check meshtasticd            │                           │
│  │  ├─ check rnsd + port 37428      │                           │
│  │  ├─ check mosquitto              │                           │
│  │  ├─ check blocking interfaces    │                           │
│  │  └─ check gateway bridge         │                           │
│  └──────────┬───────────────────────┘                           │
│             │                                                    │
│             ▼                                                    │
│  ┌──────────────────────────────────┐                           │
│  │  EventBus (pub/sub)              │  ← event_bus.py           │
│  │  emit: ServiceEvent              │                           │
│  │  emit: HealthEvent               │                           │
│  │  emit: DiagnosticEvent           │                           │
│  └──┬──────────┬──────────┬─────────┘                           │
│     │          │          │                                      │
│     ▼          ▼          ▼                                      │
│  StatusBar  Gateway   SharedHealthState                         │
│  (TUI)     (circuit  (SQLite, multi-process,                   │
│             breaker)  metrics export)                           │
│                                                                  │
│  StatusBar reads → health_score.py → 0-100 with categories     │
│  Diagnostics unified → diagnostic_engine.py (single API)       │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

---

## Phase 1: Wire the Health Loop (Reliability Foundation)

**Goal:** Continuous health monitoring with event-driven status updates.
**Effort:** ~3 sessions
**Impact:** Eliminates "no visibility" and "can't tell what's broken"

### 1.1 Register Built-in Health Checks

**File:** `src/utils/active_health_probe.py`

The probe framework exists. Add concrete checks:

```python
# Built-in checks to register:
def check_meshtasticd() -> bool:
    """TCP connect to port 4403, timeout 2s"""

def check_rnsd() -> bool:
    """Process running AND port 37428 accepting connections"""

def check_mosquitto() -> bool:
    """TCP connect to port 1883, timeout 2s"""

def check_rnsd_interfaces() -> bool:
    """No blocking interfaces detected"""

def check_gateway_bridge() -> bool:
    """Bridge process running, both sides connected"""
```

Each check: simple, fast (<2s), no side effects. Returns bool.

### 1.2 Connect Probe to EventBus

**File:** `src/utils/active_health_probe.py`

On state transition (healthy→unhealthy or unhealthy→healthy), emit ServiceEvent:

```python
from utils.event_bus import emit_service_status

def _on_state_change(self, name, old_state, new_state):
    emit_service_status(
        service_name=name,
        available=(new_state == State.HEALTHY),
        message=f"{name}: {old_state.name} → {new_state.name}"
    )
```

### 1.3 StatusBar Subscribes to Events

**File:** `src/launcher_tui/status_bar.py`

Instead of polling `check_service()` on every dialog, subscribe to the event bus:

```python
# On init:
event_bus.subscribe("service_status", self._on_service_event)

def _on_service_event(self, event):
    self._service_cache[event.service_name] = event.available
    # Next dialog render picks up cached state (no additional check)
```

### 1.4 Start Health Monitor at TUI Launch

**File:** `src/launcher_tui/main.py` → `run()` method

```python
from utils.active_health_probe import get_health_monitor

# After startup_checks:
self._health_monitor = get_health_monitor()
self._health_monitor.start()  # Background thread, 30s interval
```

**Outcome:** Every 30 seconds, MeshForge knows exactly what's working. StatusBar
reflects reality within 30s of any change. No ad-hoc polling needed.

---

## Phase 2: Circuit Breakers (Cascading Failure Isolation)

**Goal:** One subsystem failing doesn't take others down.
**Effort:** ~2 sessions
**Impact:** Eliminates "one thing breaks everything"

### 2.1 Gateway Subsystem Isolation

**File:** `src/gateway/rns_bridge.py`

The bridge currently treats RNS failure and Meshtastic failure the same way.
Add independent circuit breakers:

```python
class SubsystemState(Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"     # Working but with issues
    DISCONNECTED = "disconnected"  # Not connected, will retry
    DISABLED = "disabled"     # User or config says don't use

class GatewayBridge:
    def __init__(self):
        self._mesh_state = SubsystemState.DISCONNECTED
        self._rns_state = SubsystemState.DISCONNECTED
        self._bridge_state = SubsystemState.DISCONNECTED  # Both connected = can bridge
```

**Key behaviors:**
- Meshtastic goes down → `_mesh_state = DISCONNECTED`, keep RNS running, queue messages
- RNS goes down → `_rns_state = DISCONNECTED`, keep Meshtastic running, queue messages
- Both down → `_bridge_state = DISCONNECTED`, retry both independently
- Never auto-fix anything. Just track state and retry connections.

### 2.2 Message Queue During Degraded State

**File:** `src/gateway/message_queue.py` (exists, SQLite-backed)

When one side is disconnected, queue messages for delivery when reconnected:

```python
# In bridge loop:
if self._mesh_state == SubsystemState.HEALTHY and self._rns_state == SubsystemState.DISCONNECTED:
    # Receive from Meshtastic, queue for RNS
    self._message_queue.enqueue(msg, destination="rns")
elif self._rns_state == SubsystemState.HEALTHY:
    # Drain RNS queue
    for msg in self._message_queue.drain(destination="rns"):
        self._bridge_to_rns(msg)
```

### 2.3 TUI Shows Subsystem Independence

**Status bar update:**
```
mesh:* rns:- mqtt:* bridge:DEGRADED(mesh→queue)
```

Instead of just "bridge:stopped", show which side is down and what's happening
to messages.

**Outcome:** meshtasticd can restart without killing RNS connectivity.
rnsd can restart without losing Meshtastic messages. Each subsystem
retries independently with its own backoff timer.

---

## Phase 3: Diagnose-Don't-Fix Policy (Fix Persistence)

**Goal:** Fixes stick because they're deliberate, not reactive.
**Effort:** ~2 sessions
**Impact:** Eliminates "fix doesn't stick" and auto-fix regressions

### 3.1 Separate Diagnose from Repair in TUI

**Current:** Error handlers scattered through code try to auto-fix
**Target:** Diagnostic output + explicit repair menu

```
RNS Menu:
  [1] RNS Status          ← shows current state, no changes
  [2] RNS Diagnostics     ← deep analysis, reports issues
  [3] RNS Repair Wizard   ← USER CHOOSES to fix, step by step
  [4] RNS Config Editor   ← manual config editing
```

### 3.2 Repair Wizard Pattern

**New file:** `src/launcher_tui/repair_wizard_mixin.py`

Every repair action follows this pattern:

```python
def _repair_step(self, title, diagnosis, fix_description, fix_action):
    """Standard repair step with verification."""
    # 1. Show what's wrong
    self.dialog.msgbox(f"Issue: {diagnosis}")

    # 2. Explain the fix
    choice = self.dialog.yesno(
        f"Proposed fix: {fix_description}\n\nProceed?"
    )
    if choice != "yes":
        return False

    # 3. Execute fix
    success, result = fix_action()

    # 4. VERIFY fix worked
    verified = self._verify_fix(title)

    # 5. Report outcome
    if verified:
        self.dialog.msgbox(f"Fixed: {title}\nVerified: {result}")
    else:
        self.dialog.msgbox(f"Fix applied but verification FAILED.\n{result}")

    return verified
```

### 3.3 Remove Auto-Fix from Error Handlers

**Files to audit:** `rns_menu_mixin.py`, `nomadnet_client_mixin.py`

Search for any code that modifies system state (config files, service restarts,
permission changes) from error handlers. Move all such logic to the repair wizard.

Error handlers should ONLY:
1. Log the error
2. Emit a diagnostic event
3. Show user-friendly message with "Run Repair Wizard?" option

### 3.4 Post-Action Verification Gate

**New pattern for all service operations:**

```python
def _restart_service_verified(self, service_name, timeout=15):
    """Restart service and verify it reached healthy state."""
    # 1. Restart
    success, msg = apply_config_and_restart(service_name)
    if not success:
        return False, msg

    # 2. Wait for healthy (poll every 2s, up to timeout)
    for i in range(timeout // 2):
        time.sleep(2)
        status = check_service(service_name)
        if status.available:
            # 3. For rnsd: also verify port 37428 is bound
            if service_name == 'rnsd':
                if not self._check_port(37428):
                    continue  # Still initializing
            return True, f"{service_name} running and verified"

    return False, f"{service_name} started but health check failed after {timeout}s"
```

**Outcome:** Every fix is deliberate, verified, and reported. No more blind restarts.
No more auto-fix loops. User always knows what happened.

---

## Phase 4: Unified Diagnostics (Single API)

**Goal:** One diagnostic system, one API, persistent history.
**Effort:** ~1 session
**Impact:** Consistent diagnostic experience across TUI, CLI, and future web

### 4.1 Merge Two Diagnostic Engines

**Keep:** `src/utils/diagnostic_engine.py` (rule-based, SQLite history)
**Absorb:** `src/core/diagnostics/engine.py` (callback-driven checks)

Move the check suite (services, network, hardware, RNS, Meshtastic) into
the rule-based engine as registered check functions:

```python
# In diagnostic_engine.py:
engine.register_check("rns_storage", check_rns_storage_permissions)
engine.register_check("meshtastic_service", check_meshtastic_service)
engine.register_check("blocking_interfaces", check_blocking_interfaces)
```

### 4.2 Emit Diagnostic Events

When diagnostics find issues, emit to event bus:

```python
event_bus.emit("diagnostic", DiagnosticEvent(
    check_name="blocking_interfaces",
    severity=Severity.WARNING,
    message="Meshtastic Gateway interface enabled but meshtasticd not running",
    fix_hint="Disable interface or start meshtasticd"
))
```

### 4.3 Recurring Issue Detection

The diagnostic engine already has `get_recurring_issues()`. Wire it into
the status bar:

```
MeshForge v0.5.4 | mesh:* | rns:- | ⚠ 2 recurring issues
```

User can select "View Alerts" to see what's recurring and what to fix.

**Outcome:** One place to add diagnostic checks. Persistent history catches
patterns. Recurring issues surfaced proactively.

---

## Phase 5: Metrics & Telemetry Pipeline (Observability)

**Goal:** Know what happened, when, and why — after the fact.
**Effort:** ~2 sessions (parallel with other work)
**Impact:** Post-incident analysis, trend detection, Grafana dashboards

### 5.1 Wire Health Score to Shared Health State

**Files:** `health_score.py` → `shared_health_state.py`

Every 30 seconds (alongside health probe), compute health score and persist:

```python
# In health monitor loop:
snapshot = health_scorer.get_snapshot(node_data, metrics)
shared_state.record_health(snapshot.overall_score, snapshot.categories)
```

### 5.2 Metrics Export (Already Split, Needs Wiring)

**Files:** `src/utils/metrics/` (prometheus.py, influxdb.py, common.py)

The metrics export modules exist but need data sources. Wire:
- Health score → Prometheus gauge
- Service state → Prometheus gauge (0/1)
- Message counts → Prometheus counter
- Bridge latency → Prometheus histogram

### 5.3 Structured Log Events

**File:** `src/utils/logging_utils.py`

Add structured JSON log entries for key events:

```python
def log_event(component, event_type, data):
    """Structured event log for post-incident analysis."""
    logger.info(json.dumps({
        "ts": datetime.utcnow().isoformat(),
        "component": component,
        "event": event_type,
        "data": data
    }))
```

Events to log:
- Service state transitions
- Bridge connection/disconnection
- Message sent/received/queued/dropped
- Config changes
- Repair actions taken

**Outcome:** Grafana dashboard showing real-time and historical health.
Structured logs enabling "what happened at 3am" investigations.

---

## Phase 6: Integration Tests (Lifecycle Verification)

**Goal:** Catch the multi-step failure sequences that unit tests miss.
**Effort:** Ongoing (add test per failure discovered)
**Impact:** Prevents regression of known failure patterns

### 6.1 Scenario Tests

```python
class TestRNSLifecycle:
    """Test the failure sequences from session notes."""

    def test_blocking_interface_cascade(self):
        """Session 7: meshtasticd stops → rnsd hangs → tools fail."""
        # 1. Start with Meshtastic interface enabled
        # 2. Mock meshtasticd as unreachable
        # 3. Verify rnsd detection reports blocking interface
        # 4. Verify auto-fix does NOT overwrite config

    def test_sudo_identity_mismatch(self):
        """Session 4: root context + user rnsd = auth failure."""
        # 1. Mock SUDO_USER as empty
        # 2. Mock rnsd running as 'wh6gxz'
        # 3. Verify mismatch detection triggers
        # 4. Verify NomadNet launch drops privileges

    def test_config_survives_restart(self):
        """Session 7: custom interfaces preserved across restarts."""
        # 1. Create config with custom interfaces
        # 2. Trigger various error conditions
        # 3. Verify config file unchanged

    def test_permission_self_healing(self):
        """Session 5: ratchets dir created at runtime."""
        # 1. Remove ratchets directory
        # 2. Initialize RNS bridge
        # 3. Verify directory recreated with correct permissions
```

### 6.2 Health Monitor Integration Test

```python
def test_health_monitor_detects_service_failure():
    """Health loop detects service going down within 30s."""
    # 1. Start health monitor with mock services
    # 2. All healthy initially
    # 3. Mock meshtasticd going down
    # 4. Verify ServiceEvent emitted within one cycle
    # 5. Verify status bar reflects change
```

---

## Implementation Priority & Parallel Tracks

### Track A: Reliability Core (Sequential, blocks on each phase)

| Phase | Sessions | Depends On | Delivers |
|-------|----------|------------|----------|
| **Phase 1**: Health Loop | 3 | Nothing | Continuous visibility |
| **Phase 2**: Circuit Breakers | 2 | Phase 1 events | Failure isolation |
| **Phase 3**: Diagnose-Don't-Fix | 2 | Phase 1 diagnostics | Fix persistence |
| **Phase 4**: Unified Diagnostics | 1 | Phase 3 pattern | Single API |

### Track B: Observability (Parallel, independent)

| Phase | Sessions | Depends On | Delivers |
|-------|----------|------------|----------|
| **Phase 5**: Metrics Pipeline | 2 | Phase 1 health data | Grafana, history |
| **Phase 6**: Integration Tests | Ongoing | Phase 1-3 patterns | Regression safety |

### Track C: Features (Parallel, uses hardened core)

Non-gateway features (RF tools, propagation, MQTT monitoring, coverage maps)
can continue development on Track C without blocking on Track A.

```
Week 1-2:  Phase 1 (health loop)     | Phase 5 start (metrics)
Week 3:    Phase 2 (circuit breakers) | Phase 5 finish
Week 4:    Phase 3 (diagnose-don't-fix)| Phase 6 start (tests)
Week 5:    Phase 4 (unified diagnostics)| Phase 6 ongoing
```

---

## What This Solves for Each Use Case

### Use Case 1: Meshtastic ↔ MeshForge ↔ RNS

**Before:** Gateway connects, something breaks, silent failure, user investigates
**After:**
- Health monitor detects meshtasticd or rnsd failure within 30s
- Status bar shows `mesh:- rns:* bridge:DEGRADED(mesh→queue)`
- Messages queued automatically, delivered when Meshtastic reconnects
- If rnsd hangs on blocking interface, diagnostic event emitted with fix hint
- User runs Repair Wizard to disable blocking interface (with consent)
- Post-repair verification confirms port 37428 is listening

### Use Case 2: LoRa ↔ MeshForge ↔ LoRa (MQTT bridge)

**Before:** Broker config unclear, no visibility into message flow
**After:**
- Health monitor checks mosquitto every 30s
- Metrics show messages/second per topic
- Bridge status shows both radio connections independently
- If one radio drops, other keeps running, messages queue
- Data path diagnostic tests all 6 collection paths on demand

### Both Use Cases:

- **Visibility:** Status bar + health score + structured logs = always know what's happening
- **Isolation:** Circuit breakers = one failure doesn't cascade
- **Persistence:** Verified fixes + no auto-overwrite = changes stick
- **Diagnostics:** Single engine + recurring issue detection = patterns surfaced early

---

## Guiding Principles (from the diagnosis)

1. **Connect, don't create.** Five health systems exist. Wire them together.
2. **Diagnose, don't fix.** Show what's wrong. Let the user (or repair wizard) act.
3. **Verify, don't assume.** Every action confirmed with a health check.
4. **Isolate, don't cascade.** Each subsystem has its own lifecycle.
5. **Complexity of the fix must never exceed complexity of the problem.**

---

*Plan authored: 2026-02-13*
*Based on forensic review of 8 sessions, 28 persistent issues, 5 failure patterns*
*For: WH6GXZ (Nursedude) - MeshForge Architect*
