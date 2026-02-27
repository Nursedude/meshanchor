# Deferred Issues — From Code Quality Review (2026-02-26)

> Create these as GitHub issues manually. `gh` CLI not authenticated in this env.
> **Updated**: 2026-02-27 — Issue 2 completed (PR #977)

---

## Issue 1: Consolidate logging modules (logging_config.py + logging_utils.py)

**Labels**: refactor

Four logging modules in `src/utils/` with overlapping `setup_logging()` and `get_logger()`.

**Approach**: Keep `logging_config.py` as canonical, merge component features from `logging_utils.py`, extract `LogContext`/decorators to `logging_helpers.py`. Leave `logger.py` (installer) and `logging_structured.py` (JSON) as-is. Update all 14 import sites.

**Risk**: Medium — initialization order across TUI, daemon, installer entry points.

---

## ~~Issue 2: Extract BaseMessageHandler from gateway handlers~~ COMPLETED

**Status**: DONE — PR #977 (2026-02-26)
**What was done**: `BaseMessageHandler` ABC extracted with shared constructor, `_truncate_if_needed`, and `_notify_status`. Logging consolidation (`logging_utils.py` merged into `logging_config.py`).

---

## Issue 3: Migrate 46 TUI mixins to command registry pattern

**Labels**: refactor, architecture

`MeshForgeLauncher` inherits from 46 separate mixins (lines 115-161 in `launcher_tui/main.py`). Each is used exactly once. This is a god-class split by file, not true composition. Phase 1 handler registry infrastructure already exists (`handler_protocol.py`, `handler_registry.py`, `handlers/`).

**Approach**: Migrate to plugin-based command registry where each mixin becomes a standalone handler registered with the launcher. Launcher dispatches by menu key, not method inheritance.

**Effort**: 2-3 days. Requires comprehensive test coverage first.

---

*From `.claude/audits/code-review-quality-2026-02-26.md`*
