# Deferred Issues — From Code Quality Review (2026-02-26)

> Create these as GitHub issues manually. `gh` CLI not authenticated in this env.

---

## Issue 1: Consolidate logging modules (logging_config.py + logging_utils.py)

**Labels**: refactor

Four logging modules in `src/utils/` with overlapping `setup_logging()` and `get_logger()`.

**Approach**: Keep `logging_config.py` as canonical, merge component features from `logging_utils.py`, extract `LogContext`/decorators to `logging_helpers.py`. Leave `logger.py` (installer) and `logging_structured.py` (JSON) as-is. Update all 14 import sites.

**Risk**: Medium — initialization order across TUI, daemon, installer entry points.

---

## Issue 2: Extract BaseMessageHandler from gateway handlers

**Labels**: refactor

All 3 handlers (`meshtastic_handler.py`, `mqtt_bridge_handler.py`, `meshcore_handler.py`) share identical 10-parameter constructor and 4-method interface (`run_loop`, `send_text`, `disconnect`, `queue_send`).

**Approach**: Create `gateway/base_handler.py` with ABC. Rename `_mesh_to_rns_queue`/`_outbound_queue` → `_message_queue`. Move `_truncate_if_needed` and `_notify_status` to base class.

**Risk**: Medium — requires coordinated renames across 3 handler files + tests.

---

## Issue 3: Migrate 49 TUI mixins to command registry pattern

**Labels**: refactor, architecture

`MeshForgeLauncher` inherits from 49 separate mixins (lines 110-156 in `launcher_tui/main.py`). Each is used exactly once. This is a god-class split by file, not true composition.

**Approach**: Migrate to plugin-based command registry where each mixin becomes a standalone handler registered with the launcher. Launcher dispatches by menu key, not method inheritance.

**Effort**: 2-3 days. Requires comprehensive test coverage first.

---

*From `.claude/audits/code-review-quality-2026-02-26.md`*
