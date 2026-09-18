"""Menu wiring guards — an unowned tag must never re-render silently.

PORTED FROM MESHFORGE (F9, 2026-09-16), where 77 dispatch sites across 62
modules were measured and exactly one had an ``else``. MeshAnchor carried
the same idiom at the same scale — 77 sites, the same three hybrids — and
was one step further behind: its EIGHT top-level section menus had no
tripwire either, and ``_system_menu`` / ``_about_menu`` called
``registry.dispatch()`` and DISCARDED the boolean, so an unowned tag was
indistinguishable from a handled one.

The defect is honest_failure_modes #1: ``dict.get`` returns ``None`` both
for "no handler owns this tag" and for the ordinary path, so ``if entry:``
maps a wiring bug onto the healthy domain. The operator presses a row,
the menu redraws, nothing happens, forever, with no witness (#9).

These guards are deliberately SHAPE-based. They ask whether a menu CAN
report an unowned tag, never which tag is unowned. MeshForge first tried
a semantic analyzer that predicted the missing tags: 10-for-10 false
positives, then 22-for-22, and after three rewrites it could speak about
only 8 of 169 menus. A checker that forecasts the defect is a re-check,
not a gate.
"""

import ast
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src', 'launcher_tui'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

TUI_DIR = Path(__file__).resolve().parent.parent / "src" / "launcher_tui"


def _dispatch_sites_by_function():
    """Every dispatch-table site, grouped by the function holding it.

    A site is ``NAME = TABLE.get(VAR)`` followed by ``if NAME:``, where
    TABLE is a dict literal assigned in that same function. The
    literal-dict requirement keeps runtime lookups out — a
    ``registries.get(svc_name)`` circuit-breaker read is not menu dispatch.
    """
    found = {}
    for path in sorted(TUI_DIR.rglob("*.py")):
        tree = ast.parse(path.read_text(), str(path))
        for fn in [n for n in ast.walk(tree)
                   if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
            tables = {t.id for n in ast.walk(fn)
                      if isinstance(n, ast.Assign) and isinstance(n.value, ast.Dict)
                      for t in n.targets if isinstance(t, ast.Name)}
            results = set()
            for n in ast.walk(fn):
                if (isinstance(n, ast.Assign) and len(n.targets) == 1
                        and isinstance(n.targets[0], ast.Name)
                        and isinstance(n.value, ast.Call)
                        and isinstance(n.value.func, ast.Attribute)
                        and n.value.func.attr == "get"
                        and len(n.value.args) == 1
                        and isinstance(n.value.args[0], ast.Name)
                        and isinstance(n.value.func.value, ast.Name)
                        and n.value.func.value.id in tables):
                    results.add(n.targets[0].id)
            sites = [n.lineno for n in ast.walk(fn)
                     if isinstance(n, ast.If) and isinstance(n.test, ast.Name)
                     and n.test.id in results]
            if sites:
                found.setdefault(f"{path.name}:{fn.name}",
                                 {"fn": fn, "sites": []})["sites"].extend(sites)
    return found


class TestDispatchSitesCanReportUnownedTags:
    def test_every_dispatch_site_has_a_tripwire(self):
        offenders = []
        for key, rec in _dispatch_sites_by_function().items():
            guards = sum(
                1 for n in ast.walk(rec["fn"])
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and n.func.attr in ("notify_unwired", "_notify_unwired"))
            if guards < len(rec["sites"]):
                offenders.append(
                    f"{key} — {len(rec['sites'])} dispatch site(s) at "
                    f"{sorted(rec['sites'])}, {guards} tripwire(s)")
        assert not offenders, (
            "menu-dispatch sites with no way to report an unowned tag:\n  "
            + "\n  ".join(offenders))


class TestSectionMenusWireTheTripwire:
    """The eight top-level submenus, which had NO tripwire before this port."""

    SECTION_MENUS = (
        "_dashboard_menu", "_meshcore_primary_menu", "_optional_gateways_menu",
        "_rf_sdr_menu", "_maps_viz_menu", "_configuration_menu",
        "_system_menu", "_about_menu",
    )

    def test_every_section_menu_can_report_an_unowned_tag(self):
        import inspect
        import main as tui_main
        missing = []
        for name in self.SECTION_MENUS:
            method = getattr(tui_main.MeshAnchorLauncher, name, None)
            if method is None:
                missing.append(f"{name} — no such method (renamed? deleted?)")
                continue
            if "_notify_unwired" not in inspect.getsource(method):
                missing.append(f"{name} — unknown tag re-renders silently")
        assert not missing, (
            "section menus with no unknown-tag tripwire:\n  " + "\n  ".join(missing))

    def test_main_choice_dispatch_has_a_tripwire(self):
        import inspect
        import main as tui_main
        src = inspect.getsource(tui_main.MeshAnchorLauncher._handle_main_choice)
        assert "_notify_unwired" in src

    def test_dispatch_return_value_is_not_discarded(self):
        """_system_menu and _about_menu threw the boolean away.

        `self._registry.dispatch(section, choice)` as a bare statement
        cannot tell a handled tag from an unowned one — the return value
        IS the answer, and discarding it is the silent re-render.
        """
        import main as tui_main
        src = Path(tui_main.__file__).read_text()
        tree = ast.parse(src)
        bare = []
        for node in ast.walk(tree):
            if (isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
                    and isinstance(node.value.func, ast.Attribute)
                    and node.value.func.attr == "dispatch"
                    and len(node.value.args) == 2
                    and isinstance(node.value.args[1], ast.Name)):
                bare.append(node.lineno)
        assert not bare, (
            f"registry.dispatch(section, <var>) called as a bare statement at "
            f"line(s) {bare} — the return value is the only signal that a tag "
            "was owned, so discarding it re-creates the silent re-render")



class TestNotifyUnwiredDialog:
    def test_tuicontext_notify_unwired_names_tag_and_screen(self):
        from types import SimpleNamespace
        from handler_protocol import TUIContext
        calls = []
        ctx = TUIContext(dialog=SimpleNamespace(
            msgbox=lambda t, b: calls.append((t, b))))
        ctx.notify_unwired("ghost-tag", "SomeHandler._some_menu")
        assert calls, "notify_unwired showed no dialog"
        title, body = calls[0]
        assert title == "Not wired"
        assert "ghost-tag" in body
        assert "SomeHandler._some_menu" in body


class TestMainMenuPin:
    """MeshAnchor's top-level rows, which nothing was asserting.

    Ported from MeshForge, but the EXPECTED list is re-derived from
    MeshAnchor's own live render — the two menus genuinely differ (row 2
    is MeshCore here, Mesh Networks there, and MeshAnchor has no
    Extensions row). Copying MeshForge's list would have pinned the wrong
    product.

    Both halves matter. ORDER is muscle memory — these are single
    keystrokes on a field terminal, and silently moving "Emergency Mode"
    is worse than losing it. DISPATCH catches the other direction: a row
    that still renders after the code behind it was deleted.
    """

    EXPECTED = [
        ("1", "Dashboard"),
        ("2", "MeshCore"),
        ("3", "RF & SDR"),
        ("4", "Maps & Viz"),
        ("5", "Configuration"),
        ("6", "System"),
        ("t", "Tactical Ops"),
        ("q", "Quick Actions"),
        ("e", "Emergency Mode"),
        ("a", "About"),
        ("x", "Exit"),
    ]

    @staticmethod
    def _render_rows():
        from types import SimpleNamespace
        import main as tui_main
        seen = []

        # **kwargs: the real menu() takes cancel_label=; a double with a
        # fixed arity turns a NEW keyword into a TypeError about the
        # stand-in rather than a finding about the code under test.
        def fake_menu(title, subtitle, choices, **kwargs):
            seen.append(list(choices))
            return "x"

        fake = SimpleNamespace(
            _get_menu_status_hint=lambda: "",
            # The launcher marks top-level rows through the registry's one
            # implementation, so the stub needs it. It used to carry a
            # _feature_enabled lambda instead; that wrapper is gone.
            _registry=SimpleNamespace(mark_label=lambda desc, flag: desc),
            _MAX_DIALOG_RETRIES=3,
            _handle_main_choice=lambda c: None,
            dialog=SimpleNamespace(menu=fake_menu, yesno=lambda *a: True),
        )
        tui_main.MeshAnchorLauncher._run_main_menu(fake)
        assert seen, "_run_main_menu rendered no menu at all"
        return seen[0]

    def test_rows_and_order_are_pinned(self):
        rows = self._render_rows()
        actual = [(tag, label.split("  ")[0].strip()) for tag, label in rows]
        assert actual == self.EXPECTED, (
            "the main menu changed. This is a pin, not a bug — if the "
            "change is intended, update EXPECTED in the same commit and "
            "say why in the message.\n"
            f"  expected: {self.EXPECTED}\n  actual:   {actual}")

    def test_every_main_row_has_a_dispatch_path(self):
        """A row whose handler was deleted must redden here, not re-render."""
        import main as tui_main
        from handlers import get_all_handlers

        registry_tags = {item[0] for cls in get_all_handlers()
                         for item in cls().menu_items()
                         if cls().menu_section == "main"}
        dict_tags = set()
        tree = ast.parse(Path(tui_main.__file__).read_text())
        for fn in ast.walk(tree):
            if isinstance(fn, ast.FunctionDef) and fn.name == "_handle_main_choice":
                for n in ast.walk(fn):
                    if isinstance(n, ast.Assign) and isinstance(n.value, ast.Dict):
                        dict_tags |= {k.value for k in n.value.keys
                                      if isinstance(k, ast.Constant)
                                      and isinstance(k.value, str)}
        # "x" is consumed by _run_main_menu itself, before dispatch.
        dispatchable = registry_tags | dict_tags | {"x"}
        orphans = [t for t, _ in self._render_rows() if t not in dispatchable]
        assert not orphans, (
            f"main-menu rows that reach no dispatch path: {orphans}. "
            f"registry 'main' owns {sorted(registry_tags)}, the "
            f"_handle_main_choice dict owns {sorted(dict_tags)}.")



# ------------------------------------------ the escape hatch is CHROME, not a row

class TestSectionMenusLabelTheirCancelButton:
    """A SECTION menu must label its Cancel button; the MAIN menu must not.

    A section loop treats Cancel/Escape (menu() returns None) as identical
    to selecting the "back" row. They are one control with two faces, and
    only one face survives a short terminal: the ROW is a list row (the
    mesh_networks section paints 17 of 20 at 24x80, and "back" is appended
    LAST, so "back" is what falls off), while the BUTTON is chrome and
    cannot scroll. Unlabelled, whiptail paints "Cancel", which reads as
    "abort", not "go up one level".

    The MAIN menu is deliberately EXCLUDED and that exclusion is pinned
    here, so the next reader sees a decision rather than an oversight:
    Escape at the top level still counts as a dialog FAILURE in
    _run_main_menu, so a button promising "Back" or "Exit" would not keep
    its word. Fixing that honestly needs DialogError threaded through the
    loop — queued in .claude/audits/review_provenance.md.

    Handler-level menus are out of scope by design: ~119 of them treat
    None as back too, but they are reached through their own surfaces and
    were never measured for the scroll symptom. Also queued.
    """

    MAIN_MENU_FN = '_run_main_menu'

    @staticmethod
    def _menu_calls_by_function():
        """[(enclosing function name, Call node)] for self.dialog.menu()."""
        src = (Path(__file__).resolve().parents[1]
               / 'src' / 'launcher_tui' / 'main.py').read_text()
        tree = ast.parse(src)
        found = []
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for node in ast.walk(fn):
                if (isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Attribute)
                        and node.func.attr == 'menu'
                        and isinstance(node.func.value, ast.Attribute)
                        and node.func.value.attr == 'dialog'):
                    found.append((fn.name, node))
        return found

    def test_every_section_menu_passes_cancel_label(self):
        calls = self._menu_calls_by_function()
        assert calls, "found no self.dialog.menu() calls — the parser drifted"
        unlabelled = [
            (fn, node.lineno) for fn, node in calls
            if fn != self.MAIN_MENU_FN
            and not any(kw.arg == 'cancel_label' for kw in node.keywords)
        ]
        assert not unlabelled, (
            f"section menu(s) without cancel_label=: {unlabelled}. A menu "
            "whose Cancel means 'back' must SAY so: pass "
            "cancel_label=BACK_LABEL. The 'back' row can scroll off a "
            "24x80 terminal; the button cannot.")

    def test_the_main_menu_is_deliberately_unlabelled(self):
        calls = self._menu_calls_by_function()
        main_calls = [n for fn, n in calls if fn == self.MAIN_MENU_FN]
        assert main_calls, (
            f"no menu() call found in {self.MAIN_MENU_FN} — if the main "
            "menu moved, move this pin with it")
        labelled = [
            n.lineno for n in main_calls
            if any(kw.arg == 'cancel_label' for kw in n.keywords)
        ]
        assert not labelled, (
            f"the MAIN menu got a cancel_label at line(s) {labelled}. "
            "Escape there still counts as a dialog FAILURE, so the button "
            "would not keep its promise. Fix _run_main_menu's None branch "
            "first (needs DialogError), then label it.")

    def test_no_call_site_hardcodes_the_label(self):
        """The row label and the button label must be ONE constant."""
        literals = [
            (fn, n.lineno, kw.value.value)
            for fn, n in self._menu_calls_by_function()
            for kw in n.keywords
            if kw.arg == 'cancel_label' and isinstance(kw.value, ast.Constant)
        ]
        assert not literals, (
            f"cancel_label hardcoded at {literals} — use BACK_LABEL so the "
            "button and the 'back' row can never disagree.")
