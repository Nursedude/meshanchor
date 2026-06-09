---
name: MeshAnchor
description: >
  MeshAnchor NOC (Network Operations Center) assistant — **MeshCore-primary** LoRa mesh with
  Meshtastic and RNS (Reticulum) as optional gateways. Sister project to MeshForge
  (Meshtastic-primary); shares TUI framework, gateway bridge, and CanonicalMessage contract.

  Use when working with: (1) MeshCore node fleet and primary radio operations,
  (2) Optional Meshtasticd/rnsd gateways feeding the MeshCore NOC, (3) LoRa radio configuration
  (presets, frequencies, regions), (4) MeshAnchor TUI development, (5) Cross-protocol bridge code,
  (6) RF calculations and link budgets, (7) Node discovery and monitoring.

  Triggers: meshcore, meshanchor, meshtastic, meshtasticd, rnsd, reticulum, lora, gateway, rnode, nomadnet
---

# MeshAnchor Development Assistant

> **Scope note (2026-06-09):** CLAUDE.md, `.claude/rules/security.md`, and
> `.claude/foundations/persistent_issues.md` are auto-loaded into every session —
> do NOT restate them here. This skill carries only operational reference that
> lives nowhere else. Version: read `src/__version__.py`, never hardcode it here.
> Handler count: read `handler_registry.py` registrations, never hardcode
> (a stale "64 handlers" copy survived multiple audits).

## Project Identity

MeshAnchor is **MeshCore-primary**: MeshCore is the home radio; Meshtastic/RNS come
in as optional gateways. Sister project [MeshForge](https://github.com/Nursedude/meshforge)
is the inverse (Meshtastic-primary) and the **lead repo for the RNS-reliability arc** —
RNS substrate changes land there first, then port here (`check_parity()` watches drift).

**Shared contract:** `src/gateway/canonical_message.py` must stay byte-compatible with
MeshForge's copy. Two flagships, one protocol.

## RNS client-config instance_name (Issue #32 + fleet-host bug, 2026-04-24)

Never hardcode `instance_name = default` in client-only RNS configs. Always:
```python
from utils.paths import ReticulumPaths
instance_name = ReticulumPaths.get_configured_instance_name()
# Write "instance_name = {instance_name}" into the client config
```
The shared-instance socket is namespaced `@rns/<instance_name>`. Mismatches cause an
empty path_table on boxes where rnsd runs under a non-default name.

## Key Ports

| Service | Port | Protocol | Notes |
|---------|------|----------|-------|
| meshtasticd TCP API | 4403 | TCP | PhoneAPI — single consumer (#17); never probe casually |
| meshtasticd Web UI | 9443 | HTTPS | |
| RNS shared instance | 37428 | TCP | legacy port; live IPC is the AF_UNIX `@rns/<instance>` socket — owner must be rnsd (#69): `sudo ss -xnpl \| grep "@rns/"` |
| HamClock Live / API | 8081 / 8082 | HTTP | |
| MQTT | 1883 | TCP | per-box broker islands — no fleet consensus |

## Service Quick Facts

- `rnsd` and `meshtasticd` are **system** services (`sudo systemctl …`); user units
  log via `journalctl _SYSTEMD_USER_UNIT=<unit>`.
- Service state ONLY via `check_service()` from `utils.service_check` (MF008).
- After editing `/etc/reticulum/config`: `sudo systemctl restart rnsd` (authkey
  derives from identity) — and never rapid-cycle rnsd fleet-wide (#69 race window).

## TUI Handler Pattern

Each menu action is a self-contained handler in `src/launcher_tui/handlers/`,
dispatched by `handler_registry.py`:

```python
from launcher_tui.handler_protocol import BaseHandler, TUIContext

class MyHandler(BaseHandler):
    def execute(self, ctx: TUIContext, **kwargs):
        ...
```

## Launch & Verify

```bash
sudo python3 src/launcher_tui/main.py   # Primary interface (TUI)
python3 src/standalone.py               # Zero-dependency RF tools

python3 scripts/lint.py --all           # Blocking gate (MF rules)
python3 -m pytest tests/ -v             # Full suite
python3 scripts/db_audit.py             # DBSpec inventory (MF013)
```

For honest test results in long sessions, redirect to a file and check the exit
code explicitly (`pytest … 1>/tmp/out.log 2>&1; echo EXIT=$?`) — never trust a
`| head`/`| tail`-truncated stream.

## For Detailed Reference

- Known issues & fixes: `.claude/foundations/persistent_issues.md` (auto-loaded)
- Architecture: `.claude/foundations/domain_architecture.md`
- Full doc index: `.claude/INDEX.md`
- Knowledge Base API: `src/utils/knowledge_base.py`
