# MeshForge Reliability & Diagnostics Development Plan

> **Mission**: Make MeshForge a dependable tool for HAMs, scientists, and network engineers
> who need to configure, test, and bridge RF and Ethernet mesh networks.
>
> **Problem Statement**: Inconsistent status reporting, false positives, and debugging loops
> waste hours and erode trust in the tool.

---

## Root Cause Analysis

### Why Status Displays Conflict (rnsd example)

**Found 5+ different implementations checking rnsd status:**

| Location | Method | Issues |
|----------|--------|--------|
| `service_check.py` | UDP → pgrep → systemd | ✓ CANONICAL - correct |
| `gtk_ui/rns_mixins/components.py` | UDP → pgrep → systemd | ⚠️ DUPLICATES service_check.py |
| `tui/panes/dashboard.py` | UDP → systemctl | ⚠️ Missing pgrep fallback |
| `commands/rns.py` | pgrep → systemd | ⚠️ **MISSING UDP check** |
| `commands/service.py` | systemd → pgrep | ⚠️ **MISSING UDP check** |

**Result**: Different panels query status differently → conflicting results → lost trust.

---

## Development Phases

### Phase 1: Single Source of Truth (Foundation)
**Goal**: Eliminate conflicting implementations

#### 1.1 Consolidate Service Checking
```
┌─────────────────────────────────────────────────────────────────┐
│                     service_check.py                             │
│         (SINGLE canonical implementation)                        │
│                                                                  │
│  check_service(name) → ServiceStatus                            │
│    ├── UDP port check (rnsd: 37428)                             │
│    ├── Process check (pgrep)                                    │
│    ├── Systemd check (systemctl)                                │
│    └── Returns: {available, state, message, fix_hint}          │
└─────────────────────────────────────────────────────────────────┘
                           │
        ┌──────────────────┼──────────────────┐
        ▼                  ▼                  ▼
   ┌─────────┐       ┌──────────┐       ┌─────────┐
   │  GTK UI │       │   TUI    │       │  CLI    │
   │ Panels  │       │ Dashboard│       │Commands │
   └─────────┘       └──────────┘       └─────────┘
```

**Files to modify:**
- [ ] `gtk_ui/panels/rns_mixins/components.py` - Remove `_check_rns_service()`, use `check_service()`
- [ ] `tui/panes/dashboard.py` - Replace inline checks with `check_service()`
- [ ] `commands/rns.py` - Replace `get_status()` with `check_service()`
- [ ] `commands/service.py` - Add UDP check for rnsd

#### 1.2 Status Correlation Engine
```python
# New: src/utils/status_correlator.py

class StatusCorrelator:
    """Cross-check multiple sources before reporting status."""

    def check_rnsd(self) -> CorrelatedStatus:
        sources = {
            'udp_port': self._check_udp_port(37428),
            'process': self._check_process('rnsd'),
            'systemd': self._check_systemd('rnsd'),
            'rns_api': self._check_rns_api(),  # RNS.Reticulum() test
        }

        # Require 2+ sources to agree
        return self._correlate(sources, quorum=2)
```

**Benefit**: If UDP says "running" but systemd says "stopped", report "DEGRADED" not "ON".

---

### Phase 2: Wireshark-Style Packet Monitor
**Goal**: Real-time visibility into mesh traffic

#### 2.1 Unified Message Bus
```
┌─────────────────────────────────────────────────────────────────┐
│                    MeshForge Message Bus                         │
│              (Central event stream for all traffic)              │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌─────────────┐     ┌─────────────┐     ┌─────────────┐       │
│  │ Meshtastic  │     │    RNS      │     │    MQTT     │       │
│  │  Listener   │────▶│  Listener   │────▶│  Listener   │       │
│  └─────────────┘     └─────────────┘     └─────────────┘       │
│         │                   │                   │                │
│         └───────────────────┼───────────────────┘                │
│                             ▼                                    │
│                    ┌─────────────────┐                          │
│                    │   Event Queue   │                          │
│                    │  (Thread-safe)  │                          │
│                    └─────────────────┘                          │
│                             │                                    │
│         ┌───────────────────┼───────────────────┐               │
│         ▼                   ▼                   ▼               │
│  ┌─────────────┐     ┌─────────────┐     ┌─────────────┐       │
│  │  GTK Panel  │     │  TUI Panel  │     │  Log File   │       │
│  │  (3-pane)   │     │  (3-pane)   │     │  (JSON)     │       │
│  └─────────────┘     └─────────────┘     └─────────────┘       │
└─────────────────────────────────────────────────────────────────┘
```

#### 2.2 Three-Pane Message View (Wireshark Pattern)
```
┌─────────────────────────────────────────────────────────────────┐
│  [Filter: from:!abc123 AND network:mesh ] [Apply] [Clear]       │
├─────────────────────────────────────────────────────────────────┤
│  MESSAGE LIST                                                    │
│  ┌────┬────────┬───────────┬───────────┬──────────┬───────────┐│
│  │ #  │ Time   │ From      │ To        │ Type     │ Status    ││
│  ├────┼────────┼───────────┼───────────┼──────────┼───────────┤│
│  │ 1  │ 14:32  │ !abc123   │ !def456   │ TEXT     │ ✓ ACK     ││
│  │ 2  │ 14:33  │ !def456   │ !abc123   │ TEXT     │ ⏳ PENDING││
│  │ 3  │ 14:35  │ RNS:a1b2  │ LXMF      │ MESSAGE  │ ✗ FAILED  ││
│  └────┴────────┴───────────┴───────────┴──────────┴───────────┘│
├─────────────────────────────────────────────────────────────────┤
│  MESSAGE DETAILS                                                 │
│  ▼ Routing                                                       │
│      Path: !abc123 → !relay1 → !def456                          │
│      Hop Count: 2 of 3                                          │
│      Latency: 1.2s                                              │
│  ▼ Diagnostics                                                   │
│      SNR: -8.5 dB (marginal)                                    │
│      RSSI: -95 dBm                                              │
│      ⚠️ Warning: High retry count (3)                           │
├─────────────────────────────────────────────────────────────────┤
│  RAW DATA                                                        │
│  { "from": 2882343476, "to": 4294967295, "decoded": {...} }    │
└─────────────────────────────────────────────────────────────────┘
```

---

### Phase 3: Meshtastic Error Code Integration
**Goal**: Decode and explain device errors automatically

#### 3.1 Error Code Database
```python
# src/utils/meshtastic_errors.py

CRITICAL_ERROR_CODES = {
    0: {"name": "None", "severity": "OK", "action": None},
    1: {"name": "TxWatchdog", "severity": "CRITICAL",
        "cause": "Radio transmit hardware failure",
        "action": "Check antenna connection, reboot device"},
    2: {"name": "SleepEnterWait", "severity": "WARNING",
        "cause": "Device stuck entering sleep mode",
        "action": "Power cycle device"},
    # ... full list from Meshtastic protobuf
    12: {"name": "FlashCorruption", "severity": "CRITICAL",
         "cause": "Flash filesystem corrupted",
         "action": "Factory reset required, backup config first"},
}

REBOOT_REASONS = {
    0: "Power on",
    1: "Hardware watchdog",
    2: "Software reset",
    3: "Deep sleep wake",
    # ...
}
```

#### 3.2 Automatic Error Detection
```python
class MeshtasticErrorMonitor:
    def on_node_info(self, node):
        if node.device_metrics:
            error_code = node.device_metrics.error_code
            if error_code > 0:
                diagnosis = CRITICAL_ERROR_CODES.get(error_code)
                self.emit_alert(
                    severity=diagnosis['severity'],
                    message=f"Device {node.user.short_name} error: {diagnosis['name']}",
                    action=diagnosis['action']
                )
```

---

### Phase 4: Message Routing Debugger
**Goal**: Track why messages fail to deliver

#### 4.1 Message Lifecycle Tracking
```
Message Lifecycle States:
┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐
│ CREATED  │───▶│  QUEUED  │───▶│   SENT   │───▶│   ACK    │
└──────────┘    └──────────┘    └──────────┘    └──────────┘
                     │               │               │
                     ▼               ▼               ▼
               ┌──────────┐    ┌──────────┐    ┌──────────┐
               │ BLOCKED  │    │ TIMEOUT  │    │ FAILED   │
               │(no conn) │    │(no reply)│    │(NAK/err) │
               └──────────┘    └──────────┘    └──────────┘
```

#### 4.2 Routing Diagnostics
```python
class MessageRoutingDebugger:
    def diagnose_failure(self, message_id: str) -> RoutingDiagnosis:
        msg = self.message_store.get(message_id)

        checks = [
            self._check_destination_exists(msg.to),
            self._check_route_available(msg.to),
            self._check_channel_match(msg),
            self._check_hop_limit(msg),
            self._check_connection_state(),
        ]

        for check in checks:
            if not check.passed:
                return RoutingDiagnosis(
                    cause=check.failure_reason,
                    suggestion=check.fix_suggestion,
                    evidence=check.evidence
                )
```

---

### Phase 5: Unified Dashboard with Correlation
**Goal**: Single view showing correlated health across all services

```
┌─────────────────────────────────────────────────────────────────┐
│  MESHFORGE HEALTH DASHBOARD                    [Refresh: 5s]    │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  SERVICES                        CORRELATION STATUS              │
│  ┌─────────────────────────────┐ ┌─────────────────────────────┐│
│  │ meshtasticd  ● ONLINE       │ │ All 4 checks agree: ONLINE  ││
│  │   UDP 4403   ✓              │ │   UDP: ✓  pgrep: ✓          ││
│  │   Process    ✓              │ │   systemd: ✓  API: ✓        ││
│  │   Systemd    ✓              │ └─────────────────────────────┘│
│  │   API Test   ✓              │                                │
│  ├─────────────────────────────┤ ┌─────────────────────────────┐│
│  │ rnsd         ● ONLINE       │ │ 3/4 checks agree: ONLINE    ││
│  │   UDP 37428  ✓              │ │   UDP: ✓  pgrep: ✓          ││
│  │   Process    ✓              │ │   systemd: ✗  API: ✓        ││
│  │   Systemd    ✗ (not enabled)│ │ Note: Running manually      ││
│  │   RNS API    ✓              │ └─────────────────────────────┘│
│  └─────────────────────────────┘                                │
│                                                                  │
│  RECENT ISSUES                                                   │
│  ┌─────────────────────────────────────────────────────────────┐│
│  │ 14:32 ⚠️ meshtasticd: Connection refused (client conflict)  ││
│  │        → Another client connected. Kill with: pkill nomadnet││
│  │ 14:28 ✓ rnsd: Service started successfully                  ││
│  │ 14:15 ⚠️ Message to !def456 timed out (3 retries)          ││
│  │        → Check if node is online, reduce hop limit          ││
│  └─────────────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────────────┘
```

---

## Implementation Schedule

### Sprint 1: Foundation (Eliminate False Positives)
**Duration**: 1 sprint
**Goal**: Single source of truth for status

- [ ] Consolidate all status checks to `service_check.py`
- [ ] Remove duplicate implementations
- [ ] Add status correlation (require 2+ sources to agree)
- [ ] Add regression tests for status consistency

### Sprint 2: Message Bus & Packet Monitor
**Duration**: 1-2 sprints
**Goal**: Real-time visibility

- [ ] Implement unified message bus
- [ ] Create three-pane message monitor (GTK)
- [ ] Create three-pane message monitor (TUI)
- [ ] Add display filters (Wireshark-style)

### Sprint 3: Error Code Integration
**Duration**: 1 sprint
**Goal**: Automatic error detection

- [ ] Import Meshtastic error codes from protobuf
- [ ] Add error monitoring to node tracker
- [ ] Display errors in dashboard with explanations
- [ ] Add RNS path/probe diagnostics

### Sprint 4: Routing Debugger
**Duration**: 1-2 sprints
**Goal**: Track message failures

- [ ] Implement message lifecycle tracking
- [ ] Add routing diagnostics
- [ ] Create visual route tracer
- [ ] Add timeout/retry analysis

### Sprint 5: Unified Dashboard
**Duration**: 1 sprint
**Goal**: Correlated health view

- [ ] Create health correlation dashboard
- [ ] Add historical trend graphs
- [ ] Implement alert system
- [ ] Add export/logging for post-mortem

---

## Success Criteria

### Phase 1 Complete When:
- [ ] All UI components use same status check function
- [ ] No conflicting status displays possible
- [ ] Tests verify consistency across GTK, TUI, CLI

### Phase 2 Complete When:
- [ ] Can filter messages by source, destination, type
- [ ] See message details including routing path
- [ ] Real-time updates without manual refresh

### Phase 3 Complete When:
- [ ] Device errors automatically decoded and explained
- [ ] Actionable fix suggestions displayed
- [ ] Error history tracked

### Phase 4 Complete When:
- [ ] Can trace why a message failed to deliver
- [ ] Get specific fix suggestion for each failure mode
- [ ] Message timeout causes clearly identified

### Phase 5 Complete When:
- [ ] Single dashboard shows all service health
- [ ] Status discrepancies flagged automatically
- [ ] Can export diagnostic data for support

---

## References

- [Wireshark Packet Analysis](https://www.fromdev.com/2025/10/wireshark-packet-analysis-for-network-troubleshooting.html)
- [Meshtastic Critical Error Codes](https://meshtastic.org/docs/development/device/error-codes/)
- [Meshtastic JS SDK CriticalErrorCode](https://js.meshtastic.org/enums/Protobuf.Mesh.CriticalErrorCode.html)
- [RNS Documentation](https://reticulum.network/)
- `.claude/research/meshtasticd_port_conflicts.md` - TCP single-client issue
- `.claude/research/meshtastic_broken_pipe_bug.md` - Cosmetic error
- `.claude/ui/wireshark_patterns.md` - UI design patterns

---

*Created: 2026-01-17*
*MeshForge Mission: Dependable mesh network tooling for HAMs and scientists*
