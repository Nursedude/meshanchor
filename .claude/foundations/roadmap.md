# MeshForge Roadmap

## Current Release: v0.4.6-beta

## Milestone: v0.5.0 - Reliability Release

### Completed in This Session
- [x] Auto-review system with 77 tests
- [x] Context-aware exception detection
- [x] 0 auto-review issues (clean codebase)
- [x] MQTT Nodeless Subscriber
- [x] Folium Coverage Map Generator
- [x] Persistent Message Queue

### Remaining for v0.5.0

#### High Priority

**Issue: Integrate MQTT nodeless mode into TUI/GTK**
```
Title: [Feature] Add MQTT nodeless dashboard panel
Labels: enhancement, ui
Description:
Create a dashboard panel that uses MQTTNodelessSubscriber to display:
- Live node count from MQTT
- Recent messages
- Connection status indicator
- One-click connect/disconnect

This enables users to monitor mesh without hardware.

Files involved:
- src/monitoring/mqtt_subscriber.py (done)
- src/gtk_ui/panels/mqtt_dashboard.py (update)
- src/tui/panes/ (new pane)
```

**Issue: Integrate coverage map with node tracker**
```
Title: [Feature] Add coverage map export to Map panel
Labels: enhancement, ui
Description:
Add "Export Coverage Map" button to Map panel that:
1. Collects current nodes
2. Generates Folium HTML
3. Opens in browser

Use CoverageMapGenerator from utils/coverage_map.py.

Files involved:
- src/utils/coverage_map.py (done)
- src/gtk_ui/panels/map.py (update)
```

**Issue: Wire message queue to gateway bridge**
```
Title: [Feature] Use persistent queue in gateway
Labels: enhancement, reliability
Description:
Integrate PersistentMessageQueue with RNS-Meshtastic bridge:
- Enqueue outgoing messages
- Register destination senders
- Start background processing
- Add queue stats to diagnostics

Files involved:
- src/gateway/message_queue.py (done)
- src/gateway/rns_bridge.py (update)
- src/gateway/node_tracker.py (update)
```

#### Medium Priority

**Issue: Add end-to-end tests for gateway**
```
Title: [Testing] Gateway integration tests
Labels: testing
Description:
Add tests for:
- Message queue persistence across restarts
- MQTT subscriber reconnection
- Coverage map generation with real data
```

**Issue: Documentation for new features**
```
Title: [Docs] Document MQTT nodeless, maps, and queue
Labels: documentation
Description:
Update docs with:
- How to use MQTT nodeless mode
- Coverage map examples
- Message queue configuration
```

---

## Milestone: v0.6.0 - Integration Release

### Gateway Enhancements
- [ ] Meshtastic-to-Meshtastic preset bridge
- [ ] RNS over Meshtastic transport
- [ ] AREDN mesh integration

### UI Improvements
- [ ] Real-time SNR graphs
- [ ] Message threading view
- [ ] Multi-network dashboard

### Reliability
- [ ] Message compression for LoRa
- [ ] Adaptive retry based on network conditions
- [ ] Circuit breaker for failing destinations

---

## Milestone: v1.0.0 - Stable Release

### Requirements
- [ ] Full test coverage (>80%)
- [ ] Security audit complete
- [ ] Documentation complete
- [ ] 30 days beta testing with no critical bugs
- [ ] Performance benchmarks documented

### Features
- [ ] Plugin marketplace
- [ ] Web dashboard (Flask-based)
- [ ] Mobile companion app API
- [ ] Multi-mesh topology visualization

---

## GitHub Issue Templates

### Feature Request Template
```markdown
**Feature Description**
[Clear description of what you want]

**Use Case**
[Why do you need this?]

**Proposed Implementation**
[If you have ideas]

**Files Likely Involved**
- src/...
- tests/...
```

### Bug Report Template
```markdown
**Bug Description**
[What happened]

**Expected Behavior**
[What should happen]

**Steps to Reproduce**
1. ...
2. ...
3. ...

**Environment**
- MeshForge version:
- OS:
- Python version:
- Hardware:

**Logs**
```
[Paste relevant logs]
```
```

---

## Priority Labels

- `P0-critical`: Security vulnerabilities, data loss
- `P1-high`: Core functionality broken
- `P2-medium`: Important features, significant bugs
- `P3-low`: Nice to have, minor issues

## Type Labels

- `bug`: Something isn't working
- `enhancement`: New feature or improvement
- `documentation`: Documentation only
- `testing`: Test coverage
- `security`: Security related
- `performance`: Performance improvement
