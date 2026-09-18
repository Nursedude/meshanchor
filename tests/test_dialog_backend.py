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


class TestCancelButtonLabel:
    """The Cancel button is the escape hatch that cannot scroll off.

    whiptail and dialog(1) spell the flag differently, and an unsupported
    flag does not degrade -- it kills the dialog. So the spelling comes
    from the DETECTED backend and an unrecognised one emits nothing.
    """

    def test_whiptail_spelling(self):
        be = _make_backend([(0, "a")])
        be.menu("T", "text", [("a", "A")], cancel_label="Back")
        args = be._run_calls[0]
        assert '--cancel-button' in args
        assert args[args.index('--cancel-button') + 1] == 'Back'

    def test_dialog_spelling(self):
        be = _make_backend([(0, "a")])
        be.backend = 'dialog'
        be.menu("T", "text", [("a", "A")], cancel_label="Back")
        args = be._run_calls[0]
        assert '--cancel-label' in args, "dialog(1) spells it --cancel-label"
        assert '--cancel-button' not in args

    def test_unknown_backend_emits_no_flag(self):
        be = _make_backend([(0, "a")])
        be.backend = None
        be.menu("T", "text", [("a", "A")], cancel_label="Back")
        assert not [a for a in be._run_calls[0] if a.startswith('--cancel')]

    def test_flag_precedes_the_menu_box_option(self):
        """A flag AFTER --menu would be parsed as a menu ITEM."""
        be = _make_backend([(0, "a")])
        be.menu("T", "text", [("a", "A")], cancel_label="Back")
        args = be._run_calls[0]
        assert args.index('--cancel-button') < args.index('--menu')

    def test_omitted_label_leaves_args_untouched(self):
        """No caller opts in -> byte-identical to the pre-change command.

        This is what keeps the ~119 handler menus (operational pickers,
        where Cancel correctly means "abort the operation") unchanged.
        """
        be = _make_backend([(0, "a")])
        be.menu("T", "text", [("a", "A")])
        assert be._run_calls[0][0] == '--title'
        assert not [a for a in be._run_calls[0] if a.startswith('--cancel')]


class TestCancelLabelInference:
    """The button's label is DERIVED from the menu's own rows.

    Ported from MeshForge 2026-09-18. A navigational menu carries a
    `back` row and maps menu()'s None to that same row -- one control,
    two faces, and only the row can scroll off a 24x80 terminal.

    Measured: MeshAnchor has 214 menu call sites, ~167 carrying a `back`
    row, and the "None means back" contract is spelled at least five
    different ways across them. No regex over call sites classifies that
    reliably; the presence of the ROW does.
    """

    def test_a_back_row_infers_the_back_label(self):
        be = _make_backend([(0, "x")])
        be.menu("T", "text", [("status", "Status"), ("back", "Back")])
        args = be._run_calls[0]
        assert '--cancel-button' in args
        assert args[args.index('--cancel-button') + 1] == 'Back'

    def test_no_back_row_keeps_whiptails_cancel(self):
        """An operational picker must NOT say Back."""
        be = _make_backend([(0, "x")])
        be.menu("T", "text", [("eth0", "eth0"), ("wlan0", "wlan0")])
        assert not [a for a in be._run_calls[0] if a.startswith('--cancel')]

    def test_explicit_label_beats_inference(self):
        be = _make_backend([(0, "x")])
        be.menu("T", "text", [("a", "A"), ("back", "Back")],
                cancel_label="Exit")
        args = be._run_calls[0]
        assert args[args.index('--cancel-button') + 1] == 'Exit'

    def test_explicit_cancel_is_the_opt_out(self):
        be = _make_backend([(0, "x")])
        be.menu("T", "text", [("a", "A"), ("back", "Back")],
                cancel_label="Cancel")
        args = be._run_calls[0]
        assert args[args.index('--cancel-button') + 1] == 'Cancel'

    def test_explicit_none_emits_no_flag(self):
        """None stays distinguishable from 'infer one for me'."""
        be = _make_backend([(0, "x")])
        be.menu("T", "text", [("a", "A"), ("back", "Back")],
                cancel_label=None)
        assert not [a for a in be._run_calls[0] if a.startswith('--cancel')]

    def test_inference_itself_tolerates_ragged_rows(self):
        """The HELPER is defensive, even though menu() is not.

        menu() has never accepted a ragged choices list -- `for tag, desc
        in choices` raises on one, and that contract predates this
        change. So this pins the helper directly rather than claiming
        menu() tolerates input it never did.
        """
        be = _make_backend([])
        assert be._infer_cancel_label(
            [(), None, ("back", "Back")], be._AUTO_CANCEL) == 'Back'
        assert be._infer_cancel_label(None, be._AUTO_CANCEL) is None
        assert be._infer_cancel_label([], be._AUTO_CANCEL) is None

    def test_back_must_be_the_TAG_not_the_label(self):
        """A row LABELLED 'Back' with another tag is not the back row."""
        be = _make_backend([(0, "x")])
        be.menu("T", "text", [("return_home", "Back")])
        assert not [a for a in be._run_calls[0] if a.startswith('--cancel')]

    def test_the_main_menu_still_says_cancel(self):
        """MA's top level has NO back row, so inference must not fire.

        Escape there still counts as a dialog FAILURE (see
        _run_main_menu), so a button promising Back would not keep its
        word. This pins that the inference does not accidentally grant
        one. Queued: DialogError, then the main menu can say Exit.
        """
        be = _make_backend([(0, "x")])
        be.menu("MeshAnchor NOC", "hint",
                [("1", "Dashboard"), ("a", "About"), ("x", "Exit")])
        assert not [a for a in be._run_calls[0] if a.startswith('--cancel')]
