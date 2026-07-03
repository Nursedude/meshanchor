# Environment Variables

> **Configuration SSOT is NOT environment variables.** Persistent settings live
> in `~/.config/meshanchor/*.json` managed by `SettingsManager`
> (`src/utils/common.py`). There is **no `.env` file mechanism** — a dead
> `utils/config.py` dotenv loader was removed 2026-07-03 after an audit found
> it had zero importers. If you were setting values in a `.env` file, they
> were never read; use the TUI settings menus or the JSON config files.
>
> Environment variables are used only as **runtime overrides and daemon
> knobs** — things systemd drop-ins, cron lines, and test harnesses set.

## AI assistant

| Variable | Read by | Purpose |
|----------|---------|---------|
| `ANTHROPIC_API_KEY` | `utils/claude_assistant.py`, TUI AI tools | Enables PRO-tier AI features. Absent = Standalone tier. |

## Runtime knobs

| Variable | Read by | Purpose |
|----------|---------|---------|
| `MESHANCHOR_ORACLE_ENABLED` | `oracle/responder.py`, `gateway/meshcore_handler.py` | Master switch for the mesh oracle. |
| `MESHANCHOR_CHAT_API` | `chat_client.py`, `handlers/chat_pane.py` | Chat-pane API endpoint override. |
| `MESHANCHOR_DELIVERY_COUNTERS_DB` | `gateway/delivery_counters.py` | Delivery-counters DB path override. |
| `MESHANCHOR_FLEET_CONFIG` | `utils/fleet_config.py` | Fleet config path override. |
| `MESHANCHOR_EDITION` | `core/edition.py` | Edition override; wins over config + marker files. |
| `CALLSIGN` / `HAM_CALLSIGN` | `amateur/callsign.py`, `diagnostics/diagnose.py` | Operator callsign for amateur-radio features. |
| `ENABLE_EMOJI` / `DISABLE_EMOJI` | `utils/emoji.py` | Force emoji rendering on/off (auto-detected otherwise). |

## RNS parity knobs (MESHFORGE_-prefixed by design)

`src/utils/rns_init.py` is byte-identical with MeshForge (parity-mirrored —
both apps share one rnsd per box), so its knobs keep the `MESHFORGE_` prefix:

| Variable | Purpose |
|----------|---------|
| `MESHFORGE_RNS_PROBE_TIMEOUT` | Bounded AF_UNIX probe timeout in the `open_reticulum()` chokepoint. |
| `MESHFORGE_RNS_WAIT_FOR_RNSD_TIMEOUT` | Wait for an enabled rnsd to bind before proceeding (boot race). |
| `MESHFORGE_LAB_RNS_INIT_TIMEOUT` | Lab-harness override for RNS init timeout. |

## Standard/system variables

`SUDO_USER`, `DISPLAY`, `SSH_*`, `XDG_*`, `TERM`, `LANG` are read for
**environment detection** (real-user home per MF001, headless/emoji
detection) — they are not MeshAnchor knobs.

## Maintenance

When adding a new env var to `src/`, add a row here. Enumerate current
ground truth with:

```bash
grep -rnoE "(os\.environ(\.get)?|os\.getenv)\(['\"][A-Z][A-Z0-9_]+" src/
```
