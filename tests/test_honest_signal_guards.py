"""Honest-signal guard suite (Issues #74-#77; ported from MeshForge 2026-06-08).

The TUI must not show a hardcoded success for an action whose result was never
checked — it works, or it says exactly how it didn't. MeshAnchor carries the
same TUI-handler lineage as MeshForge, so it carries the same defect class; this
is the regression home for it here.

  * TestApplyConfigRestartReturnChecked — no handler discards
    apply_config_and_restart()'s (ok, msg) (the MF020 contract).
  * TestReportActionHelper — the shared confirm-or-honest dialog primitive.
  * TestMF020LintRule — the lint rule fires on the bad shape, stays quiet on
    the honest one and outside the handler tree.
"""
import os
import re
import sys
import importlib.util
from pathlib import Path
from unittest.mock import MagicMock

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
HANDLERS_DIR = REPO_ROOT / "src" / "launcher_tui" / "handlers"

sys.path.insert(0, str(REPO_ROOT / "src" / "launcher_tui"))
sys.path.insert(0, str(REPO_ROOT / "src"))

_lint_path = REPO_ROOT / "scripts" / "lint.py"
_spec = importlib.util.spec_from_file_location("lint", _lint_path)
lint = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lint)

_BARE_APPLY = re.compile(r'^_?apply_config_and_restart\s*\(')


class TestApplyConfigRestartReturnChecked:
    """apply_config_and_restart() returns (success, msg) precisely so callers
    surface a failed restart. A bare-statement call drops it and shows a
    hardcoded "restarted" even when the daemon stayed down (#74-#77)."""

    def test_no_bare_apply_config_and_restart_in_handlers(self):
        violations = []
        for root, _dirs, files in os.walk(HANDLERS_DIR):
            for fn in files:
                if not fn.endswith(".py"):
                    continue
                fp = Path(root) / fn
                with open(fp, encoding="utf-8", errors="ignore") as f:
                    for n, line in enumerate(f, 1):
                        s = line.strip()
                        if s.startswith("#"):
                            continue
                        if _BARE_APPLY.match(s):
                            violations.append(f"{fp.relative_to(REPO_ROOT)}:{n}")
        assert not violations, (
            "apply_config_and_restart() return discarded (MF020 / honest-signal "
            "#74-#77) — bind 'ok, msg = ...' and surface restart failure:\n  "
            + "\n  ".join(violations)
        )


class TestReportActionHelper:
    """TUIContext.report_action — the shared confirm-or-honest dialog primitive."""

    def _ctx(self):
        from handler_protocol import TUIContext
        return TUIContext(dialog=MagicMock())

    def test_success_shows_success_dialog_and_returns_true(self):
        ctx = self._ctx()
        assert ctx.report_action(True, "Applied", "did it") is True
        ctx.dialog.msgbox.assert_called_once_with("Applied", "did it")

    def test_failure_shows_failure_dialog_and_returns_false(self):
        ctx = self._ctx()
        assert ctx.report_action(False, "Applied", "did it", "Restart Failed", "nope") is False
        ctx.dialog.msgbox.assert_called_once_with("Restart Failed", "nope")

    def test_failure_default_title_and_body(self):
        ctx = self._ctx()
        ctx.report_action(False, "Applied", "did it")
        title, body = ctx.dialog.msgbox.call_args[0]
        assert title == "Action Failed"
        assert "did not complete" in body

    def test_truthiness_is_coerced_to_bool(self):
        ctx = self._ctx()
        assert ctx.report_action(0, "Applied", "did it") is False
        assert ctx.report_action(1, "Applied", "did it") is True


class TestMF020LintRule:
    """MF020: fire on a discarded apply_config_and_restart() in a TUI handler;
    stay quiet on the honest bound form and outside the handler tree."""

    def _handler_file(self, tmp_path: Path, body: str) -> Path:
        d = tmp_path / "src" / "launcher_tui" / "handlers"
        d.mkdir(parents=True)
        fp = d / "fake_handler.py"
        fp.write_text(body)
        return fp

    def _mf020(self, issues):
        return [i for i in issues if i.code == "MF020"]

    def test_fires_on_bare_call(self, tmp_path):
        fp = self._handler_file(
            tmp_path, "def go(self):\n    apply_config_and_restart('meshtasticd')\n")
        assert self._mf020(lint.MeshAnchorLinter().lint_file(str(fp)))

    def test_fires_on_aliased_bare_call(self, tmp_path):
        fp = self._handler_file(
            tmp_path, "def go(self):\n    _apply_config_and_restart('meshtasticd')\n")
        assert self._mf020(lint.MeshAnchorLinter().lint_file(str(fp)))

    def test_quiet_when_result_is_bound(self, tmp_path):
        fp = self._handler_file(
            tmp_path,
            "def go(self):\n    ok, msg = apply_config_and_restart('meshtasticd')\n"
            "    self.ctx.report_action(ok, 'A', 'b', 'C', msg)\n")
        assert not self._mf020(lint.MeshAnchorLinter().lint_file(str(fp)))

    def test_quiet_outside_handler_tree(self, tmp_path):
        d = tmp_path / "src" / "utils"
        d.mkdir(parents=True)
        fp = d / "elsewhere.py"
        fp.write_text("def go():\n    apply_config_and_restart('meshtasticd')\n")
        assert not self._mf020(lint.MeshAnchorLinter().lint_file(str(fp)))
