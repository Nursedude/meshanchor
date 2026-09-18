"""DialogBackend.menu() — Cancel and Escape are ANSWERS, not failures.

Ported from MeshForge review F4 (2026-09-18). MeshAnchor carried the
ORIGINAL blanket retry: menu() re-ran the dialog on ANY non-zero exit,
so the first Cancel and the first Escape were spent re-rendering the
same menu and the user had to press twice on every submenu.

Safe to stop retrying those because the problem the blanket retry was
papering over -- stale terminal input making whiptail exit immediately
-- is fixed at the source: _run() calls termios.tcflush() before every
dialog.

The retry is KEPT for genuine dialog failure (subprocess death, exotic
exit codes), which is the case it was originally added for.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src',
                                'launcher_tui'))

from backend import DialogBackend  # noqa: E402


def _make_backend(run_results):
    """DialogBackend whose _run pops canned (code, output) results."""
    be = DialogBackend.__new__(DialogBackend)
    be.backend = 'whiptail'
    be.width = 78
    be.height = 22
    be.list_height = 14
    be._status_bar = None
    be._run_calls = []

    def fake_run(args, timeout=None):
        be._run_calls.append(args)
        if not run_results:
            # Say what happened, not "pop from empty list". An extra _run
            # IS the defect under test (the blanket retry re-rendering a
            # menu the user already answered), so it must read as that.
            raise AssertionError(
                f"menu() called _run {len(be._run_calls)} time(s) but only "
                f"{len(be._run_calls) - 1} result(s) were canned — it "
                "retried an exit code it should have accepted as an answer")
        return run_results.pop(0)

    be._run = fake_run
    return be


class TestMenuCancelSemantics:
    """One press, not two. The count of _run calls IS the finding."""

    def test_cancel_returns_none_without_retry(self):
        be = _make_backend([(1, "")])
        assert be.menu("T", "text", [("a", "A")]) is None
        assert len(be._run_calls) == 1, (
            "Cancel re-rendered the menu — the user must press twice")

    def test_escape_returns_none_without_retry(self):
        be = _make_backend([(255, "")])
        assert be.menu("T", "text", [("a", "A")]) is None
        assert len(be._run_calls) == 1, (
            "Escape re-rendered the menu — the user must press twice")

    def test_selection_returns_the_tag(self):
        be = _make_backend([(0, "alpha")])
        assert be.menu("T", "text", [("alpha", "A")]) == "alpha"
        assert len(be._run_calls) == 1

    def test_genuine_failure_still_retries_once(self):
        """The case the retry was actually added for is preserved."""
        be = _make_backend([(-1, ""), (0, "alpha")])
        assert be.menu("T", "text", [("alpha", "A")]) == "alpha"
        assert len(be._run_calls) == 2, (
            "a genuine dialog failure must still get its one retry")

    def test_cancel_after_a_genuine_failure_is_not_retried_again(self):
        """Retry, then the user cancels -> answer, not a third attempt."""
        be = _make_backend([(-1, ""), (1, "")])
        assert be.menu("T", "text", [("a", "A")]) is None
        assert len(be._run_calls) == 2
