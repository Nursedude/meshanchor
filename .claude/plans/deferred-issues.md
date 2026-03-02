# Deferred Issues — From Code Quality Review (2026-02-26)

> Create these as GitHub issues manually. `gh` CLI not authenticated in this env.
> **Updated**: 2026-03-02 — All 3 issues COMPLETE. Session 3 consolidation finished.

---

## Issue 1: Consolidate logging modules — MOSTLY COMPLETE

**Labels**: refactor

**Done**:
- `logging_utils.py` merged into `logging_config.py` (PR #977, 2026-02-26)
- All 9 `logging.basicConfig()` calls replaced with `setup_logging()` from canonical module (2026-02-27)
  - Files: agent.py, diagnose.py, meshtasticd_config.py, orchestrator.py, bridge_cli.py, device_backup.py, map_data_service.py, space_weather.py, telemetry_poller.py
- `logger.py` documented as installer-only (intentionally separate)

**Remaining**: `logging_structured.py` and `log_parser.py` kept as-is (specialized, no overlap).
**Status**: No further action needed — logging is consolidated around `logging_config.py`.

---

## ~~Issue 2: Extract BaseMessageHandler from gateway handlers~~ COMPLETED

**Status**: DONE — PR #977 (2026-02-26)
**What was done**: `BaseMessageHandler` ABC extracted with shared constructor, `_truncate_if_needed`, and `_notify_status`. Logging consolidation (`logging_utils.py` merged into `logging_config.py`).

---

## ~~Issue 3: Migrate TUI mixins to command registry pattern~~ COMPLETED

**Status**: DONE — Session 3 (2026-02-26 through 2026-03-02)

**What was done**:
- 49 mixins migrated to 60 self-contained handler files via Protocol + BaseHandler + TUIContext pattern
- `main.py` reduced from 1,947 → 1,148 lines (41% reduction)
- 8,776 lines of dead code removed (18 utils + 3 tests)
- 9 files exceeding 1,500 lines split to comply with guideline
- Logging consolidated (4 → 2 modules)
- 36 pre-existing test failures resolved
- Subprocess timeout hardening (MF004) verified across all handlers
- `rns_diagnostics.py` split from 2,261 → 1,403 lines (3 modules)

**Key PRs**: #988–#1000, #1012, #1014

---

*From `.claude/audits/code-review-quality-2026-02-26.md`*
