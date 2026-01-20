# MeshForge Product Recommendations

**Date:** 2026-01-19
**Goal:** Make MeshForge the go-to tool for LoRa mesh network operations

---

## Executive Summary

MeshForge has strong foundations but needs polish in three areas:
1. **First-run experience** - Zero to working in under 5 minutes
2. **Operational reliability** - Things that "just work"
3. **Workflow automation** - Reduce repetitive tasks

---

## Priority 1: First-Run Experience (HIGH IMPACT)

### 1.1 Smart Setup Wizard

**Current State:** Setup wizard exists but requires user to know what they want.

**Recommendation:** Auto-detecting wizard that:
```
1. Scan hardware → "Found: Meshtoad SX1262 on SPI"
2. Detect services → "meshtasticd: not installed"
3. Suggest config → "Recommended: MEDIUM_FAST for your region"
4. One-click install → "Install & Configure"
```

**Implementation:**
- `src/core/auto_detect.py` - Hardware + service detection
- `src/setup_wizard.py` - Enhanced with auto-suggestions
- Templates pre-selected based on detected hardware

### 1.2 Quick Start Modes

**Recommendation:** Pre-configured "personas" for common use cases:

| Mode | Config | Use Case |
|------|--------|----------|
| **Monitor Only** | No radio config needed | Just watch the mesh |
| **Single Node** | Meshtastic + basic config | Personal node |
| **Gateway** | Meshtastic + RNS bridge | Bridge networks |
| **Full NOC** | All services + monitoring | Network operations |

**Implementation:**
```python
# launcher.py
def quick_start():
    mode = select_mode()  # Monitor, Node, Gateway, NOC
    apply_preset(mode)    # Pre-configured templates
    verify_and_launch()   # Double-tap verification
```

### 1.3 Health Check on Startup

**Recommendation:** 3-second startup diagnostic:
```
✓ meshtasticd: running (4403)
✓ Hardware: Meshtoad SX1262
⚠ rnsd: not running (optional)
✓ Config: MEDIUM_FAST, US region

Ready to go! [Start] [Configure]
```

---

## Priority 2: Operational Reliability (STABILITY)

### 2.1 Service Orchestration Improvements

**Current State:** Graceful mode added, but services still require manual intervention.

**Recommendation:** Self-healing service management:
```python
class ServiceOrchestrator:
    def monitor_loop(self):
        while True:
            for service in self.critical_services:
                if not service.is_healthy():
                    self.attempt_recovery(service)
                    self.notify_user(service.status)
            sleep(30)
```

**Features:**
- Auto-restart on crash (with backoff)
- Health endpoint for each service
- Notification on state change (not polling UI)

### 2.2 Connection Resilience

**Current State:** Auto-reconnect implemented, but UI doesn't always reflect state.

**Recommendation:** Connection state machine with clear UI feedback:
```
DISCONNECTED → CONNECTING → CONNECTED → (health check) → HEALTHY
                    ↓                           ↓
                RETRY (backoff)           DEGRADED → RECONNECTING
```

**UI:** Traffic light indicator (green/yellow/red) always visible in status bar.

### 2.3 Config Validation Before Apply

**Current State:** Some validation exists, but user can still break things.

**Recommendation:** Pre-flight validation with preview:
```
Before applying config:
  ✓ YAML syntax valid
  ✓ Hardware compatible with config
  ⚠ Region changed: US → EU_868 (requires restart)
  ✗ Frequency conflict with channel 2

[Apply Anyway] [Fix Issues] [Cancel]
```

---

## Priority 3: Workflow Automation (EFFICIENCY)

### 3.1 Zapier-Style Triggers + Actions

**Inspiration:** Zapier's event-driven architecture.

**Recommendation:** Built-in automation rules:

```yaml
# .meshforge/automations/low_battery_alert.yaml
trigger:
  event: node.telemetry
  condition: battery_level < 20

action:
  - log: "Low battery on {node_id}: {battery_level}%"
  - notify: "Node {node_name} needs charging"
```

**Use Cases:**
- Alert when node goes offline
- Auto-log position updates
- Trigger backup when config changes
- Send test message on gateway start

### 3.2 Scheduled Operations

**Recommendation:** Cron-like scheduler for maintenance:
```
# Daily health report at 6 AM
0 6 * * * meshforge report --email

# Weekly config backup
0 0 * * 0 meshforge backup --config

# Hourly node count log
0 * * * * meshforge nodes --count >> /var/log/meshforge/nodes.log
```

### 3.3 CLI Improvements

**Current State:** CLI exists but not fully featured.

**Recommendation:** First-class CLI for scripting:
```bash
# Quick status
meshforge status

# Send message
meshforge send "Test" --to "!abc123"

# Start gateway with specific config
meshforge gateway start --config gateway-short-turbo.yaml

# Export node list
meshforge nodes --format json > nodes.json
```

---

## Priority 4: User Interface Polish

### 4.1 Consistent Navigation (Already Fixed)

**Status:** Back-out pattern implemented across all TUI menus.

### 4.2 Real-Time Updates

**Current State:** Polling-based UI updates (5-10 second intervals).

**Recommendation:** Event-driven updates using existing MessageListener pattern:
- Node list updates when nodes discovered
- Message list updates on RX (already implemented today)
- Service status updates on state change

### 4.3 Dashboard Improvements

**Recommendation:** At-a-glance operational dashboard:

```
┌─────────────────────────────────────────┐
│ MeshForge NOC - Regional               │
├─────────────────────────────────────────┤
│ Services     │ Network      │ Health    │
│ ● meshtasticd│ Nodes: 12    │ Score: 95 │
│ ● rnsd       │ Online: 10   │ ▓▓▓▓▓▓▓░░ │
│ ○ hamclock   │ Messages: 47 │           │
├─────────────────────────────────────────┤
│ Recent Activity                         │
│ 14:32 Node !abc joined                  │
│ 14:30 Message from !xyz                 │
│ 14:28 Gateway connected to Regional    │
└─────────────────────────────────────────┘
```

### 4.4 Map Improvements

**Current State:** Basic Leaflet map with node markers.

**Recommendation:**
- Node trails (movement history)
- Link quality visualization (SNR-based line colors)
- Coverage estimation (Fresnel zone overlay)
- Cluster view for dense areas

---

## Priority 5: Integration & Ecosystem

### 5.1 MeshForge Skill (Created Today)

**Status:** `.claude/skills/meshforge/SKILL.md` created.

**Next Steps:**
- Add reference files for common operations
- Include troubleshooting decision tree
- Bundle config templates as assets

### 5.2 API Endpoints

**Recommendation:** RESTful API for external tools:
```
GET  /api/status          # Overall health
GET  /api/nodes           # Node list
GET  /api/messages        # Message history
POST /api/send            # Send message
POST /api/config          # Update config
WS   /api/events          # Real-time event stream
```

### 5.3 Webhook Support

**Recommendation:** Outbound webhooks for integration:
```yaml
# .meshforge/webhooks.yaml
on_node_offline:
  url: https://slack.com/webhook/xxx
  payload:
    text: "Node {node_id} went offline at {timestamp}"

on_message_received:
  url: https://my-app.com/meshforge/rx
  payload:
    from: "{from_id}"
    content: "{content}"
```

---

## Implementation Roadmap

| Phase | Focus | Effort | Impact |
|-------|-------|--------|--------|
| **1** | First-run wizard improvements | 2 days | HIGH |
| **2** | Dashboard + real-time updates | 3 days | HIGH |
| **3** | CLI improvements | 2 days | MEDIUM |
| **4** | Automation rules | 5 days | HIGH |
| **5** | API + webhooks | 5 days | MEDIUM |
| **6** | Map improvements | 3 days | LOW |

---

## Quick Wins (Can Do Today)

1. ✅ Back-out pattern in all menus
2. ✅ Pre-flight service check in gateway
3. ✅ RX message display in UI
4. Add startup health summary (30 min)
5. Add traffic light status indicator (1 hour)
6. Quick start mode selector (2 hours)

---

## Competitive Differentiation

**What makes MeshForge unique:**

1. **Bridge capability** - Only tool that connects Meshtastic AND RNS
2. **NOC focus** - Built for operators, not just hobbyists
3. **Multi-UI** - GTK, TUI, Web, CLI - use what fits your environment
4. **Self-contained** - Works offline, no cloud dependency
5. **HAM-friendly** - Built by hams, for hams (WH6GXZ)

---

*Recommendations based on codebase analysis, Zapier patterns, and user workflow research.*
