"""Cross-section rows have ONE declaration — the registry alias.

Ported from MeshForge (review 2026-09-16, R1/R4/F4) on 2026-09-17, after
the gap was MEASURED live on meshanchor-server: under the ``meshcore``
profile (rns / meshtastic off) the primary menu listed NomadNet and
Channels as available — plain 2-tuples, never marked — and selecting one
answered "not in this profile". Screen said available, keypress said no.

Each cross-section row used to be three copies of one fact: a
hand-copied label in the menu loop, a hand-written dispatch fallback, and
the owner's own row. ``MeshAnchorLauncher.CROSS_SECTION_ROWS`` is now the
one declaration (four tags per row, NO label); ``HandlerRegistry.alias``
renders the row from its owner's label and flag, counts it when the owner
is off, and dispatches it to the owner — so the rendered label and the
refusal's title are the same string.
"""
import logging
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
LAUNCHER_TUI = SRC / "launcher_tui"
sys.path.insert(0, str(LAUNCHER_TUI))
sys.path.insert(0, str(SRC))

import main as tui_main                          # noqa: E402
from handler_protocol import TUIContext          # noqa: E402
from handler_registry import HandlerRegistry     # noqa: E402
from handlers import get_all_handlers            # noqa: E402
from utils.deployment_profiles import PROFILES, ProfileName  # noqa: E402

OFF = HandlerRegistry.OFF_MARK
ROWS = tui_main.MeshAnchorLauncher.CROSS_SECTION_ROWS


@pytest.fixture(autouse=True)
def _quiet_registry_logs():
    """Silence per-handler registration lines for THIS test only, and
    restore — a module-scope disable leaked across the whole session in
    MeshForge (CI red on eca86534)."""
    logging.disable(logging.INFO)
    try:
        yield
    finally:
        logging.disable(logging.NOTSET)


class RecordingDialog:
    def __init__(self):
        self.msgboxes = []

    def msgbox(self, title, text, **kw):
        self.msgboxes.append((title, text))

    def menu(self, *a, **k):
        return None

    def yesno(self, *a, **k):
        return False


def _make(profile=None):
    ctx = TUIContext(dialog=RecordingDialog())
    if profile is not None:
        ctx.profile = profile
        ctx.feature_flags = dict(profile.feature_flags)
    registry = HandlerRegistry(ctx)
    for cls in get_all_handlers():
        registry.register(cls())
    tui_main.MeshAnchorLauncher._declare_cross_section_rows(registry)
    ctx.registry = registry
    holder = SimpleNamespace(_registry=registry, _tui_context=ctx)
    return ctx, registry, holder


def _rows(holder, section, legacy=()):
    return tui_main.MeshAnchorLauncher._build_section_menu(
        holder, section, list(legacy))


MESHCORE = PROFILES[ProfileName.MESHCORE]


class TestTheDeclaration:

    def test_carries_no_label(self):
        for entry in ROWS:
            assert len(entry) == 4, entry
            assert all(" " not in part for part in entry), (
                f"a label crept into the declaration: {entry!r}")

    def test_every_row_is_actually_cross_section_and_owned(self):
        sections = {}
        for cls in get_all_handlers():
            h = cls()
            for item in h.menu_items():
                sections.setdefault(h.menu_section, set()).add(item[0])
        for screen, tag, osec, otag in ROWS:
            assert screen != osec, (screen, tag)
            assert tag not in sections.get(screen, set()), (screen, tag)
            assert otag in sections.get(osec, set()), (osec, otag)

    def test_an_alias_to_nothing_fails_at_declaration(self):
        _ctx, registry, _h = _make()
        with pytest.raises(ValueError):
            registry.alias("dashboard", "ghost", "system", "no-such-tag")

    def test_an_alias_over_an_owned_tag_fails_at_declaration(self):
        _ctx, registry, _h = _make()
        owned = next(iter(registry._tag_index["dashboard"]))
        with pytest.raises(ValueError):
            registry.alias("dashboard", owned, "system", "network")

    def test_a_second_declaration_of_the_same_row_is_refused(self):
        """Last-one-wins would accept a contradiction in the SSOT table
        silently (review 2026-09-17 #3)."""
        _ctx, registry, _h = _make()
        with pytest.raises(ValueError):
            registry.alias("dashboard", "network", "system", "network")


HANDLER_SOURCES = sorted((LAUNCHER_TUI / "handlers").glob("*.py"))


class TestTheLauncherKeepsNoCopy:
    """The three copies are gone: no legacy label for an aliased tag, and
    no hand-written dispatch fallback to its owner — in main.py AND in
    every handler-built sub-menu. The first port scanned only main.py and
    missed rns_menu.py's own copy of the nomadnet row (review 2026-09-17
    #1)."""

    def test_no_handler_dispatches_an_owner_by_hand(self):
        for _s, _t, osec, otag in ROWS:
            pat = re.compile(
                r'dispatch\(\s*["\']' + re.escape(osec) + r'["\']\s*,\s*["\']'
                + re.escape(otag) + r'["\']\s*\)')
            for f in [LAUNCHER_TUI / "main.py"] + HANDLER_SOURCES:
                assert not pat.search(f.read_text()), (f.name, osec, otag)

    def test_no_handler_carries_a_label_for_an_aliased_tag(self):
        """A handler-built sub-menu's own row table must not name an
        aliased tag — the alias arrives through get_menu_items()."""
        aliased_by_screen = {}
        for s, t, _os, _ot in ROWS:
            aliased_by_screen.setdefault(s, set()).add(t)
        for f in HANDLER_SOURCES:
            src = f.read_text()
            for screen, tags in aliased_by_screen.items():
                if f'get_menu_items("{screen}")' not in src:
                    continue          # this handler does not build that screen
                for tag in tags:
                    assert not re.search(r'["\']' + re.escape(tag) + r'["\']\s*:\s*["\']', src), (
                        f.name, screen, tag, "own_items still carries the aliased row")

    def test_no_legacy_block_lists_an_aliased_tag_on_its_screen(self):
        """Per SCREEN: a legacy block is tied to the section its loop
        renders (the ``_build_section_menu("<section>", legacy`` call
        that follows it). Configuration's own shadowed 'channels' row is
        NOT a copy of the meshcore/channels alias — same tag, other screen."""
        src = (LAUNCHER_TUI / "main.py").read_text()
        aliased = {(s, t) for s, t, _os, _ot in ROWS}
        found = set()
        for m in re.finditer(r"(?<![a-z_])legacy = \[(.*?)\]", src, re.DOTALL):
            after = src[m.end():m.end() + 400]
            sec = re.search(r'_build_section_menu\(\s*"([a-z_]+)"', after)
            assert sec, "a legacy block with no _build_section_menu call after it"
            for tag in re.findall(r'\(\s*["\']([^"\']+)["\']', m.group(1)):
                found.add((sec.group(1), tag))
        assert not (found & aliased), sorted(found & aliased)



class TestRenderedFromTheOwner:

    def test_the_rendered_label_is_the_owners(self):
        _ctx, registry, holder = _make()
        for screen, tag, osec, otag in ROWS:
            here = dict(_rows(holder, screen))[tag]
            owner = dict(registry.get_menu_items(osec))[otag]
            assert here == owner, (screen, tag, here, owner)

    def test_meshcore_profile_marks_the_rows_its_owners_turn_off(self):
        """THE live defect: on the MeshCore box these read as available."""
        _ctx, _registry, holder = _make(MESHCORE)
        primary = dict(_rows(holder, "meshcore"))
        assert primary["nomadnet"].startswith(OFF), primary["nomadnet"]
        assert primary["channels"].startswith(OFF), primary["channels"]
        conf = dict(_rows(holder, "configuration"))
        assert conf["rns-config"].startswith(OFF), conf["rns-config"]

    def test_an_unflagged_owner_stays_unmarked(self):
        _ctx, _registry, holder = _make(MESHCORE)
        dash = dict(_rows(holder, "dashboard"))
        assert not dash["network"].startswith(OFF), dash["network"]

    def test_the_menu_is_the_same_length_with_or_without_a_profile(self):
        _c1, _r1, ungated = _make()
        _c2, _r2, gated = _make(MESHCORE)
        for screen in {s for s, _t, _os, _ot in ROWS}:
            assert [t for t, _d in _rows(ungated, screen)] == \
                   [t for t, _d in _rows(gated, screen)], screen


class TestRefusalNamesTheRowOnScreen:

    def test_nomadnet_refusal_title_matches_its_rendered_label(self):
        ctx, registry, holder = _make(MESHCORE)
        rendered = dict(_rows(holder, "meshcore"))["nomadnet"]
        assert rendered.startswith(OFF)
        owner = registry._tag_index["mesh_networks"]["nomadnet"]
        reached = []
        owner.execute = lambda *a, **k: reached.append(a)
        assert registry.dispatch("meshcore", "nomadnet") is True
        assert not reached, "a row the profile excludes was actually run"
        title, body = ctx.dialog.msgboxes[-1]
        shown = rendered[len(OFF):].strip().split("  ")[0]
        assert title.startswith(shown), (title, rendered)
        assert "MeshAnchor Settings" in body, "must say how to change it, in-app"

    def test_an_aliased_row_dispatches_to_its_owner_when_on(self):
        ctx, registry, _h = _make()          # no profile: everything on
        owner = registry._tag_index["system"]["network"]
        reached = []
        owner.execute = lambda tag, *a, **k: reached.append(tag)
        assert registry.dispatch("dashboard", "network") is True
        assert reached == ["network"], reached


class TestTheGatedCountCountsRows:

    def test_count_equals_marks_on_every_screen(self):
        _ctx, registry, holder = _make(MESHCORE)
        for section in registry.section_names:
            marked = sorted(t for t, d in _rows(holder, section)
                            if d.startswith(OFF))
            counted = sorted(t for t, _d, _f in registry.get_gated_items(section))
            assert marked == counted, (section, marked, counted)


class TestTheRealPrimaryLoopRendersTheMark:
    """Drive the REAL _meshcore_primary_menu, not a copy of its rows."""

    def test_meshcore_primary_menu_marks_nomadnet_under_meshcore(self):
        _ctx, _registry, holder = _make(MESHCORE)
        seen = []

        def fake_menu(title, subtitle, choices):
            seen.append(list(choices))
            return "back"

        holder.dialog = SimpleNamespace(menu=fake_menu)
        holder._build_section_menu = (
            lambda *a: tui_main.MeshAnchorLauncher._build_section_menu(
                holder, *a))
        tui_main.MeshAnchorLauncher._meshcore_primary_menu(holder)
        assert seen, "the loop rendered nothing"
        rows = dict(seen[0])
        assert rows["nomadnet"].startswith(OFF), rows["nomadnet"]
        assert rows["channels"].startswith(OFF), rows["channels"]
        assert "optional_gateways" in rows, "the sub-menu opener must survive"


class TestUnreadableFlagFailsClosed:
    """Review 2026-09-17 #2: when the owner is registered but its LIVE
    menu_items() no longer lists the tag, the flag is unreadable. Under a
    profile that must read as REFUSED on the screen, in the count and at
    the keypress — never as "unflagged, run it" (honest-failure-modes #1:
    the degraded value must not wear a healthy one)."""

    @staticmethod
    def _drop_owner_row(registry, osec, otag):
        from unittest.mock import patch as _patch
        owner = registry._tag_index[osec][otag]
        orig = type(owner).menu_items
        return _patch.object(type(owner), "menu_items",
                             lambda self: [r for r in orig(self) if r[0] != otag])

    def test_rendered_marked_counted_and_refused_under_a_profile(self):
        ctx, registry, holder = _make(MESHCORE)
        owner = registry._tag_index["system"]["network"]
        reached = []
        owner.execute = lambda *a, **k: reached.append(a)
        with self._drop_owner_row(registry, "system", "network"):
            rows = dict(_rows(holder, "dashboard"))
            assert rows["network"].startswith(OFF), rows["network"]
            assert "network" in [t for t, _d, _f in registry.get_gated_items("dashboard")]
            assert registry.dispatch("dashboard", "network") is True
        assert not reached, "an unreadable flag ran the action under a profile"
        title, body = ctx.dialog.msgboxes[-1]
        assert "cannot verify profile" in title, title
        assert "About > Version" in body

    def test_without_a_profile_it_still_runs(self):
        """No profile = nothing gates; the drift is logged, the action runs."""
        _ctx, registry, holder = _make()
        owner = registry._tag_index["system"]["network"]
        reached = []
        owner.execute = lambda *a, **k: reached.append(a)
        with self._drop_owner_row(registry, "system", "network"):
            rows = dict(_rows(holder, "dashboard"))
            assert rows["network"] == "network"
            assert registry.dispatch("dashboard", "network") is True
        assert reached


class TestTheRnsSubMenuRendersTheAlias:
    """The seventh copy (review 2026-09-17 #1): drive the REAL
    RNSMenuHandler._rns_submenu under meshcore and read the row."""

    def test_rns_submenu_shows_nomadnet_marked_from_its_owner(self):
        ctx, registry, _h = _make(MESHCORE)
        seen = []

        def fake_menu(title, subtitle, choices):
            seen.append(list(choices))
            return "back"

        ctx.dialog.menu = fake_menu
        handler = next(h for h in registry._handlers.values()
                       if type(h).__name__ == "RNSMenuHandler")
        handler._rns_submenu()
        assert seen, "the RNS sub-menu rendered nothing"
        rows = dict(seen[0])
        owner = dict(registry.get_menu_items("mesh_networks"))["nomadnet"]
        assert rows["nomadnet"] == owner, (rows["nomadnet"], owner)
        assert rows["nomadnet"].startswith(OFF)
        assert list(rows)[0] == "nomadnet", "ordering kept nomadnet first"
