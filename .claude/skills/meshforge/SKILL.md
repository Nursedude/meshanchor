---
name: MeshAnchor
description: >
  MeshAnchor NOC (Network Operations Center) assistant for LoRa mesh network development.
  Handles Meshtastic and RNS (Reticulum) network operations, configuration, debugging, and development.

  Use when working with: (1) Meshtasticd configuration and service management, (2) RNS/Reticulum
  network setup and bridging, (3) LoRa radio configuration (presets, frequencies, regions),
  (4) MeshAnchor TUI development, (5) Gateway bridge between Meshtastic and RNS,
  (6) RF calculations and link budgets, (7) Node discovery and monitoring.

  Triggers: meshtastic, meshtasticd, rnsd, reticulum, lora, meshanchor, gateway, rnode, nomadnet
---

# MeshAnchor Development Assistant

## Project Context

MeshAnchor is a Network Operations Center (NOC) bridging Meshtastic and Reticulum mesh networks.
First open-source tool to unify these incompatible mesh ecosystems.

**Version:** 0.5.5-beta
**Callsign:** WH6GXZ (Nursedude)

## Development Principles

```
1. Make it work       ← First priority
2. Make it reliable   ← Security, testing
3. Make it maintainable ← Clean code, docs
4. Make it fast       ← Only when proven necessary
```

## Security Rules (MUST FOLLOW)

### MF001: Path.home() - NEVER use directly
```python
# WRONG
config = Path.home() / ".config" / "meshanchor"

# CORRECT
from utils.paths import get_real_user_home
config = get_real_user_home() / ".config" / "meshanchor"
```

### MF002: shell=True - NEVER use
```python
# WRONG
subprocess.run(f"cmd {arg}", shell=True)

# CORRECT
subprocess.run(["cmd", arg], timeout=30)
```

### MF003: Bare except - NEVER use
```python
# WRONG
except:
    pass

# CORRECT
except Exception as e:
    logger.debug("Error: %s", e)
```

### MF004: Always include timeout
```python
subprocess.run(["cmd"], timeout=30)  # Always specify timeout
```

## Key Ports

| Service | Port | Protocol |
|---------|------|----------|
| meshtasticd TCP API | 4403 | TCP |
| meshtasticd Web UI | 9443 | HTTPS |
| RNS Shared Instance | 37428 | UDP |
| HamClock Live | 8081 | HTTP |
| HamClock API | 8082 | HTTP |
| MQTT | 1883 | TCP |

## Service Status Pattern

For systemd services, trust `systemctl` only:
```python
from utils.service_check import check_service

status = check_service('meshtasticd')
if status.available:
    # Service running
else:
    print(status.fix_hint)  # "sudo systemctl start meshtasticd"
```

## TUI Architecture

**Handler Registry Pattern** (Protocol + BaseHandler + TUIContext):
- 64 handlers, each a self-contained module in `handlers/`
- Dispatched by `handler_registry.py`
- See `handler_protocol.py` for the Protocol definition and TUIContext shared state

```python
# Handler pattern
from launcher_tui.handler_protocol import BaseHandler, TUIContext

class MyHandler(BaseHandler):
    def execute(self, ctx: TUIContext, **kwargs):
        # Handler logic here
        pass
```

## Common Commands

```bash
# Launch interfaces
sudo python3 src/launcher_tui/main.py  # Primary interface (TUI)
python3 src/standalone.py               # Zero-dependency mode

# Verify changes
python3 -m pytest tests/ -v       # Run tests
python3 scripts/lint.py            # Security lint

# Service management
sudo systemctl status meshtasticd
sudo systemctl start meshtasticd
systemctl status rnsd             # User service, no sudo
```

## Architecture Quick Reference

```
src/
├── launcher_tui/      # Terminal UI — PRIMARY INTERFACE
│   ├── main.py        # NOC launcher + handler registration
│   ├── handler_protocol.py  # CommandHandler Protocol + TUIContext + BaseHandler
│   ├── handler_registry.py  # HandlerRegistry — register/lookup/dispatch
│   ├── backend.py           # DialogBackend (whiptail/dialog abstraction)
│   └── handlers/            # 60 registered command handlers
├── gateway/           # RNS-Meshtastic bridge
│   ├── rns_bridge.py  # Main gateway (MQTT transport)
│   ├── canonical_message.py   # Multi-protocol message format
│   └── message_queue.py # SQLite queue
├── commands/          # Command modules
│   └── propagation.py # Space weather (NOAA primary)
├── utils/
│   ├── paths.py       # get_real_user_home()
│   ├── service_check.py # check_service() — SINGLE SOURCE OF TRUTH
│   └── rf.py          # RF calculations
├── monitoring/        # MQTT subscriber
└── __version__.py     # Version and changelog
```

## Persistent Issues Reference

See `.claude/foundations/persistent_issues.md` for detailed issue tracking.

Critical resolved issues:
- #1 Path.home() → Use get_real_user_home()
- #17 Connection contention → Shared connection manager
- #20 Service detection → Phases 1&2 complete

## For Detailed Reference

- Architecture: `.claude/foundations/domain_architecture.md`
- Index: `.claude/INDEX.md`
- Research: `.claude/research/` directory
- Knowledge Base: `src/utils/knowledge_base.py`
