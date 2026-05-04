"""
Phase 2 menu restructure smoke tests.

Verifies the MeshCore-primary menu structure landed in
``claude/mc-phase2-menu-restructure``:

- ``MeshCoreHandler.menu_section`` is ``"meshcore"`` (not ``"mesh_networks"``)
- ``main.py`` top-level menu lists "MeshCore" at slot #2 (not "Mesh Networks")
- ``main.py`` exposes ``_meshcore_primary_menu`` and ``_optional_gateways_menu``
  and no longer references ``_mesh_networks_menu``
- The "Optional Gateways" submenu is reachable via tag ``optional_gateways``
  inside ``_meshcore_primary_menu``

These are static / source-level checks rather than full TUI walks — the goal
is to guard the contract against regressions, not to drive the dialog stack.
"""

import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
LAUNCHER_TUI = SRC / "launcher_tui"

sys.path.insert(0, str(LAUNCHER_TUI))
sys.path.insert(0, str(SRC))


# ---------------------------------------------------------------------------
# Handler-section contract
# ---------------------------------------------------------------------------

def test_meshcore_handler_in_meshcore_section():
    """MeshCoreHandler must live in the ``meshcore`` section, not ``mesh_networks``."""
    from handlers.meshcore import MeshCoreHandler

    assert MeshCoreHandler.menu_section == "meshcore", (
        "MeshCoreHandler should live in the 'meshcore' section after the "
        "Phase 2 promotion. Found: " + repr(MeshCoreHandler.menu_section)
    )


def test_meshcore_handler_exposes_meshcore_tag():
    """MeshCoreHandler still exposes the ``meshcore`` action tag."""
    from handlers.meshcore import MeshCoreHandler

    handler = MeshCoreHandler()
    tags = [item[0] for item in handler.menu_items()]
    assert "meshcore" in tags


# ---------------------------------------------------------------------------
# main.py top-level menu source-level checks
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def main_source() -> str:
    return (LAUNCHER_TUI / "main.py").read_text()


def test_top_level_menu_promotes_meshcore(main_source: str):
    """Top-level menu slot #2 advertises MeshCore, not the old 'Mesh Networks'."""
    assert '("2", "MeshCore' in main_source, (
        "Top-level menu slot 2 should be 'MeshCore ...' (Phase 2 promotion). "
        "If you're intentionally restructuring further, update this test."
    )
    assert '("2", "Mesh Networks' not in main_source, (
        "'Mesh Networks' should no longer appear as a top-level slot label."
    )


def test_dispatch_routes_slot_2_to_meshcore(main_source: str):
    """The slot-2 dispatch entry points at the new MeshCore submenu."""
    assert '"2": ("MeshCore", self._meshcore_primary_menu)' in main_source


def test_optional_gateways_submenu_defined(main_source: str):
    """The renamed Optional Gateways submenu method exists."""
    assert "def _optional_gateways_menu(self)" in main_source
    # Old method name must not linger
    assert "def _mesh_networks_menu(self)" not in main_source


def test_meshcore_primary_menu_links_to_optional_gateways(main_source: str):
    """_meshcore_primary_menu must wire the optional_gateways tag to the
    renamed submenu."""
    assert "def _meshcore_primary_menu(self)" in main_source
    # Must include the bridging item AND the dispatch branch
    assert '"optional_gateways"' in main_source
    assert "self._optional_gateways_menu()" in main_source


def test_optional_gateways_menu_drops_meshcore_from_ordering(main_source: str):
    """MeshCore was promoted out of the Optional Gateways submenu, so its
    legacy item must not be re-added there."""
    # The legacy block in _optional_gateways_menu should not mention meshcore.
    # Locate the function and inspect it.
    start = main_source.find("def _optional_gateways_menu(self)")
    assert start != -1
    # Read a bounded window — function body is small.
    window = main_source[start : start + 2000]
    assert '("meshcore",' not in window, (
        "_optional_gateways_menu should not append a meshcore legacy entry — "
        "MeshCore is now its own primary submenu."
    )
