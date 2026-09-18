"""MF025 must tell the reader WHAT to do, at the moment it refuses them.

⚠️ Why this exists — the evidence is a diff, not a theory (2026-09-18). A
session added a ~25-line comment to rns_bridge.py explaining a live RF echo
loop, tripped MF025 at 1,515 lines, and did NOT split the file. It moved the
rationale into base_handler.py, then — still 3 lines over — reworded a log
string and joined a paren to land on exactly 1,500. That session had read
CLAUDE.md's "ALWAYS split files exceeding 1,500 lines" an hour earlier.

Splitting is expensive; trimming is free; and the cheapest lines to cut are
the ones with no executable weight — the WHY. A bare "split the file" also
invites INVENTING a seam, which scatters one concept across files: the
"guard on one leg and not its twin" defect class that cost three separate
bugs in that same session.

A file sitting at exactly the cap cannot document its own situation in its
own docstring — any added line fails — so the hint has to live in the gate.
"""

import importlib.util
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_LINT = _REPO / "scripts" / "lint.py"
_spec = importlib.util.spec_from_file_location("lint_mod", _LINT)
lint = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lint)


@pytest.fixture
def oversized(tmp_path):
    """Build a throwaway repo_root holding one over-cap file."""
    def _make(rel: str, lines: int = 1501):
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("# x\n" * lines)
        return str(p)
    return _make


class TestSplitHintsAreNotStale:
    """A hint that outlives its subject is worse than no hint — it sends the
    next reader to a file that no longer exists. Same class as a probe
    blaming a systemd unit that was never installed."""

    def test_every_hint_key_names_a_real_file(self):
        for rel in lint.MF025_SPLIT_HINTS:
            assert (_REPO / rel).is_file(), (
                f"MF025_SPLIT_HINTS points at {rel!r}, which does not exist — "
                "delete the entry (or fix the path) when an arc moves a file")

    def test_hint_keys_are_repo_relative_posix_paths(self):
        for rel in lint.MF025_SPLIT_HINTS:
            assert not rel.startswith("/") and "\\" not in rel, rel
            assert rel.startswith("src/"), (
                f"{rel!r} — MF025 only inspects src/, so a key outside it can "
                "never fire")


class TestTheHintReachesTheMessage:

    def test_a_hinted_file_carries_its_hint(self, oversized):
        rel = "src/gateway/rns_bridge.py"
        f = oversized(rel)
        issues = lint.check_file_size_ratchet(
            [f], repo_root=str(Path(f).parents[2]))
        assert len(issues) == 1
        msg = issues[0].message
        assert "_rns_bridge_xform" in msg, (
            "MF025 refused the edit without naming the proven seam — the "
            "reader is left to invent one or trim comments, which is what "
            "actually happened on 2026-09-18")
        assert "PARITY PORT" in msg

    def test_the_hint_warns_against_trimming(self, oversized):
        """The observed failure mode, named explicitly in the message."""
        for rel in ("src/gateway/rns_bridge.py",
                    "src/gateway/meshcore_handler.py"):
            f = oversized(rel)
            issues = lint.check_file_size_ratchet(
                [f], repo_root=str(Path(f).parents[2]))
            assert "trim comments to fit" in issues[0].message, rel

    def test_meshcore_handler_is_told_there_is_NO_upstream_seam(
            self, oversized):
        """The two hinted files have different problems and must not be given
        the same advice: MeshAnchor LEADS on MeshCore, so there is nothing to
        port and a split there is a genuine design decision."""
        f = oversized("src/gateway/meshcore_handler.py")
        msg = lint.check_file_size_ratchet(
            [f], repo_root=str(Path(f).parents[2]))[0].message
        assert "NO " in msg and "upstream seam" in msg
        assert "_rns_bridge_xform" not in msg, "wrong file's advice"

    def test_an_unhinted_file_still_gets_the_plain_rule(self, oversized):
        """No hint must not mean no error — the cap still bites."""
        f = oversized("src/utils/something_unhinted.py")
        issues = lint.check_file_size_ratchet(
            [f], repo_root=str(Path(f).parents[2]))
        assert len(issues) == 1
        assert issues[0].code == "MF025"
        assert "trim comments to fit" not in issues[0].message

    def test_a_file_under_the_cap_reports_nothing(self, oversized):
        f = oversized("src/gateway/rns_bridge.py", lines=1500)
        assert lint.check_file_size_ratchet(
            [f], repo_root=str(Path(f).parents[2])) == []
