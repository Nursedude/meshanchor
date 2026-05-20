"""Tests for the MA017 systemd-sandbox-path audit in scripts/lint.py.

Ported from MeshForge's MF017 (2026-05-19 pattern-audit Finding #5).
Pins the audit behavior so an Issue #58-class drift on a hardened
MeshAnchor unit can't recur silently. The rule: a hardened systemd
unit (ProtectHome=read-only) must whitelist all three canonical
MeshAnchor data buckets in ReadWritePaths=, OR mark the omission
with an inline '# audit-skip: <reason>' comment.

Also pins the audit-skip-doesn't-short-circuit-other-units behavior
that was the MF017 bug on the MeshForge side (Finding #4); this port
incorporates the fix from the start.
"""
import os
import sys
from pathlib import Path

import pytest

# scripts/lint.py is not packaged as a module; import by file path.
import importlib.util
_lint_path = Path(__file__).parent.parent / "scripts" / "lint.py"
_spec = importlib.util.spec_from_file_location("lint", _lint_path)
lint = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lint)


HARDENED_UNIT_OK = """\
[Unit]
Description=Test

[Service]
User=alice
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=@HOME@/.config/meshanchor @HOME@/.cache/meshanchor @HOME@/.local/share/meshanchor

[Install]
WantedBy=multi-user.target
"""

HARDENED_UNIT_MISSING_DATA_DIR = """\
[Unit]
Description=Test

[Service]
User=alice
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=@HOME@/.config/meshanchor @HOME@/.cache/meshanchor

[Install]
WantedBy=multi-user.target
"""

UNHARDENED_UNIT = """\
[Unit]
Description=Test

[Service]
User=alice
ProtectSystem=false
ProtectHome=false

[Install]
WantedBy=multi-user.target
"""

HARDENED_UNIT_AUDIT_SKIP = """\
[Unit]
Description=Test

[Service]
User=alice
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=@HOME@/.config/meshanchor  # audit-skip: only needs config

[Install]
WantedBy=multi-user.target
"""


def _write_unit(repo_root: Path, fname: str, content: str) -> Path:
    scripts_dir = repo_root / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    (scripts_dir / fname).write_text(content)
    return scripts_dir / fname


class TestMA017HappyPath:
    """A unit with all three meshanchor buckets produces no issues."""

    def test_complete_whitelist_no_issues(self, tmp_path):
        _write_unit(tmp_path, "ok.service", HARDENED_UNIT_OK)
        issues = lint.check_systemd_sandbox_paths(repo_root=str(tmp_path))
        assert issues == []


class TestMA017CatchesIssue58Shape:
    """The shape that bit Issue #58 on MeshForge: hardened unit + missing bucket."""

    def test_missing_data_dir_is_flagged(self, tmp_path):
        _write_unit(tmp_path, "drift.service", HARDENED_UNIT_MISSING_DATA_DIR)
        issues = lint.check_systemd_sandbox_paths(repo_root=str(tmp_path))
        assert len(issues) == 1
        assert issues[0].code == "MA017"
        assert ".local/share/meshanchor" in issues[0].message
        assert "drift.service" in issues[0].file
        # Audit-skip is mentioned as the escape hatch
        assert "audit-skip" in issues[0].message


class TestMA017IgnoresUnhardenedUnits:
    """Units without ProtectHome=read-only aren't subject to the trap."""

    def test_unhardened_unit_produces_no_issues(self, tmp_path):
        _write_unit(tmp_path, "loose.service", UNHARDENED_UNIT)
        issues = lint.check_systemd_sandbox_paths(repo_root=str(tmp_path))
        assert issues == []


class TestMA017AuditSkipMarker:
    """An inline '# audit-skip: <reason>' opts out of the check for ITS unit only."""

    def test_audit_skip_comment_suppresses_issue(self, tmp_path):
        _write_unit(tmp_path, "skip.service", HARDENED_UNIT_AUDIT_SKIP)
        issues = lint.check_systemd_sandbox_paths(repo_root=str(tmp_path))
        assert issues == []

    def test_audit_skip_on_one_unit_does_not_suppress_others(self, tmp_path):
        """Mirror of MeshForge MF017 Finding #4 regression — incorporated
        from the start in MA017. An audit-skip marker on one unit must
        NOT short-circuit the audit of every later unit in the directory.
        """
        # Alphabetical order: "aa-skip.service" comes before "bb-drift.service".
        # If MA017 had inherited the buggy `return issues` shape from MF017,
        # bb-drift's missing bucket wouldn't be detected.
        _write_unit(tmp_path, "aa-skip.service", HARDENED_UNIT_AUDIT_SKIP)
        _write_unit(tmp_path, "bb-drift.service", HARDENED_UNIT_MISSING_DATA_DIR)
        issues = lint.check_systemd_sandbox_paths(repo_root=str(tmp_path))
        assert len(issues) == 1, (
            f"expected 1 issue from bb-drift.service, got {len(issues)}: "
            f"{[(i.file, i.code, i.message) for i in issues]}"
        )
        assert "bb-drift.service" in issues[0].file
        assert ".local/share/meshanchor" in issues[0].message


class TestMA017HandlesMissingScriptsDir:
    """A repo without scripts/ shouldn't crash the audit."""

    def test_missing_scripts_dir_returns_no_issues(self, tmp_path):
        # tmp_path has no scripts/ subdir
        issues = lint.check_systemd_sandbox_paths(repo_root=str(tmp_path))
        assert issues == []


class TestMA017RealRepoUnits:
    """The real `scripts/*.service` content must pass MA017 — locks the
    invariant that ships in the repo right now. The two units that the
    pattern-audit Finding #6 flagged (meshanchor.service and
    meshcore-radio.service) carry inline `# audit-skip:` markers
    explaining why their omission is deliberate, so they pass.
    """

    def test_real_scripts_systemd_passes(self):
        repo_root = str(Path(__file__).parent.parent)
        issues = lint.check_systemd_sandbox_paths(repo_root=repo_root)
        assert issues == [], (
            "MA017 audit found drift in real scripts/*.service units:\n  "
            + "\n  ".join(f"{i.file}:{i.line} {i.message}" for i in issues)
        )
