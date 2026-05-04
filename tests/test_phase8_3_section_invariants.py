"""
Phase 8.3 — Section invariants regression tests.

These tests guard the MeshCore-primary architectural boundary against
future drift. The rules:

* ``meshcore`` section handlers must NOT import meshtastic-only
  utilities. The MeshCore primary submenu is the canonical control
  surface for the attached radio; any Meshtastic dependency creeping
  in there means the rework's separation is leaking.

* ``rns`` section handlers must NOT depend on the gateway daemon's
  MeshCore chat API (``:8081/chat/*``) or the in-process MeshCore
  handler. RNS clients have their own RNS / LXMF transport and
  conflating them with the MeshCore chat ring buffer would make the
  boundaries muddy.

* All handlers in the ``rns`` section must declare
  ``feature_flag="rns"`` so the section collapses cleanly under
  profiles where RNS is off (already enforced by
  ``test_phase3_handler_flag_audit.py``; we add a section-level
  symmetry check here for ``meshcore``).

* The MeshCore "radio" tag is owned by exactly one handler so the
  primary radio control surface stays a single canonical entry point.

Also includes routing tests for Phase 8.3a — ``commands.messaging``
must accept ``network="meshcore"`` and surface MeshCore-first
auto-routing.
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
# Helpers — section → handler-files map
# ---------------------------------------------------------------------------

# Forbidden import patterns per section. We grep handler source files
# (and their service-ops mixins) rather than instantiating handlers,
# so the test stays cheap and side-effect-free.
_MESHTASTIC_FORBIDDEN_IN_MESHCORE = (
    "from commands import meshtastic",
    "from commands.meshtastic",
    "import commands.meshtastic",
    "cmd_meshtastic",
)

_MESHCORE_FORBIDDEN_IN_RNS = (
    "from gateway.meshcore_handler",
    "import gateway.meshcore_handler",
    "from utils.chat_client",
    "/chat/send",
    "/chat/messages",
)


def _section_files(section: str) -> list:
    """Return the handler source files registered for ``section``.

    Pulls the live class list from the registry (so newly-added
    handlers automatically participate) and resolves each class's
    module path. Also includes adjacent ``_*_service_ops.py`` mixins
    that the handler imports — those carry most of the I/O code.
    """
    from handlers import get_all_handlers

    files = []
    for cls in get_all_handlers():
        if getattr(cls, "menu_section", None) != section:
            continue
        mod = sys.modules.get(cls.__module__)
        if mod is None:
            continue
        path = getattr(mod, "__file__", None)
        if not path:
            continue
        p = Path(path)
        if p.is_file():
            files.append(p)
    # Also include any sibling service-ops mixin files in the same
    # directory whose stem starts with "_" — handlers commonly delegate
    # to those.
    if files:
        handler_dir = files[0].parent
        # Be selective: we only pull in the underscore-prefixed mixins
        # for the SAME named handlers we're testing, not every helper.
        for cls in get_all_handlers():
            if getattr(cls, "menu_section", None) != section:
                continue
            stem = Path(sys.modules[cls.__module__].__file__).stem
            mixin = handler_dir / f"_{stem}_service_ops.py"
            if mixin.is_file() and mixin not in files:
                files.append(mixin)
    return files


def _grep_lines(files, needles):
    """Return list of (file, lineno, line) for any line containing any needle."""
    hits = []
    for f in files:
        try:
            text = f.read_text()
        except OSError:
            continue
        for i, line in enumerate(text.splitlines(), start=1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            for needle in needles:
                if needle in line:
                    hits.append((f, i, line.rstrip()))
                    break
    return hits


# ---------------------------------------------------------------------------
# meshcore section invariants
# ---------------------------------------------------------------------------

def test_meshcore_section_has_handlers():
    files = _section_files("meshcore")
    assert files, (
        "meshcore section has no handler files — registry may be empty "
        "or the section name has changed."
    )


def test_meshcore_section_no_meshtastic_imports():
    files = _section_files("meshcore")
    hits = _grep_lines(files, _MESHTASTIC_FORBIDDEN_IN_MESHCORE)
    if hits:
        msg = "Meshtastic-specific imports leaked into meshcore section:\n"
        for f, ln, line in hits:
            msg += f"  {f.name}:{ln}: {line}\n"
        msg += (
            "\nThe MeshCore primary submenu is the canonical control "
            "surface for the attached radio. If you need Meshtastic CLI "
            "behaviour, route through a handler in the mesh_networks "
            "section (RadioMenuHandler) instead."
        )
        pytest.fail(msg)


def test_meshcore_section_radio_tag_owned_by_one_handler():
    """The 'radio' menu tag must have a single canonical owner.

    Multiple handlers claiming 'radio' in the meshcore section would
    produce a duplicate entry in the MeshCore primary submenu.
    """
    from handlers import get_all_handlers
    owners = []
    for cls in get_all_handlers():
        if getattr(cls, "menu_section", None) != "meshcore":
            continue
        for tag, *_ in cls().menu_items():
            if tag == "radio":
                owners.append(cls.__name__)
    assert len(owners) <= 1, (
        f"More than one meshcore handler owns the 'radio' tag: {owners}. "
        f"The MeshCore radio config menu must be canonical."
    )


# ---------------------------------------------------------------------------
# rns section invariants
# ---------------------------------------------------------------------------

def test_rns_section_has_handlers():
    files = _section_files("rns")
    assert files, "rns section has no handler files registered"


def test_rns_section_no_meshcore_chat_api_imports():
    files = _section_files("rns")
    hits = _grep_lines(files, _MESHCORE_FORBIDDEN_IN_RNS)
    if hits:
        msg = "MeshCore chat API leaked into rns section:\n"
        for f, ln, line in hits:
            msg += f"  {f.name}:{ln}: {line}\n"
        msg += (
            "\nRNS clients use their own RNS/LXMF transport. If you need "
            "to send chat through the MeshCore chat API, that's a "
            "meshcore-section concern (see ChatPaneHandler)."
        )
        pytest.fail(msg)


def test_rns_section_handlers_declare_rns_flag():
    """All rns handlers must gate on ``feature_flag="rns"``.

    Symmetry to test_phase3_handler_flag_audit.test_meshcore_profile_rns_section_collapses.
    """
    from handlers import get_all_handlers
    leaks = []
    for cls in get_all_handlers():
        if getattr(cls, "menu_section", None) != "rns":
            continue
        for tag, _desc, flag in cls().menu_items():
            if flag != "rns":
                leaks.append((cls.__name__, tag, flag))
    assert not leaks, (
        f"rns section handlers must declare feature_flag='rns'; found:\n  "
        + "\n  ".join(f"{n}.{t} = {f!r}" for n, t, f in leaks)
    )


# ---------------------------------------------------------------------------
# meshcore section gating symmetry
# ---------------------------------------------------------------------------

def test_meshcore_section_handlers_declare_meshcore_flag_or_none():
    """meshcore-section items either gate on 'meshcore' or stay always-on (None).

    Anything else (gating on, say, 'meshtastic' or 'rns') is a
    miscategorisation — that handler belongs in a different section.
    """
    from handlers import get_all_handlers
    leaks = []
    for cls in get_all_handlers():
        if getattr(cls, "menu_section", None) != "meshcore":
            continue
        for tag, _desc, flag in cls().menu_items():
            if flag not in (None, "meshcore"):
                leaks.append((cls.__name__, tag, flag))
    assert not leaks, (
        "meshcore-section handlers must gate on 'meshcore' or None:\n  "
        + "\n  ".join(f"{n}.{t} = {f!r}" for n, t, f in leaks)
    )


# ---------------------------------------------------------------------------
# Phase 8.3a — messaging routing invariants
# ---------------------------------------------------------------------------

def test_messaging_handler_offers_meshcore_route():
    """The TUI route picker exposes a 'meshcore' option."""
    src = (LAUNCHER_TUI / "handlers" / "messaging.py").read_text()
    assert '"meshcore"' in src, (
        "handlers/messaging.py route picker must offer a 'meshcore' "
        "option (Phase 8.3a)."
    )
    # Order: MeshCore should appear before Meshtastic in the picker.
    mc_idx = src.find('"meshcore"')
    mt_idx = src.find('"meshtastic"')
    assert mc_idx > 0 and mt_idx > 0
    # First occurrences — the route-picker tuple list:
    assert mc_idx < mt_idx, (
        "MeshCore should be listed before Meshtastic in the route picker."
    )


def test_send_message_accepts_meshcore_network():
    """commands.messaging.send_message has a meshcore branch."""
    src = (SRC / "commands" / "messaging.py").read_text()
    assert 'network == "meshcore"' in src, (
        "send_message() must handle network='meshcore' (Phase 8.3a)."
    )
    assert "_send_via_meshcore" in src
    assert "_meshcore_chat_api_reachable" in src


def test_auto_routing_prefers_meshcore_for_broadcast():
    """Auto-routing should prefer MeshCore first for broadcast."""
    src = (SRC / "commands" / "messaging.py").read_text()
    # The auto-routing block should call the reachability probe and
    # fall back to meshtastic, not default unconditionally to meshtastic
    # like the pre-Phase-8 behaviour.
    auto_block_start = src.find('if network == "auto":')
    assert auto_block_start > 0
    auto_block_end = src.find("# Chunk message", auto_block_start)
    if auto_block_end < 0:
        auto_block_end = auto_block_start + 1500
    auto_block = src[auto_block_start:auto_block_end]
    assert "_meshcore_chat_api_reachable" in auto_block, (
        "Auto-routing must probe the MeshCore chat API and prefer it "
        "for broadcast (Phase 8.3a)."
    )
    assert 'network = "meshcore"' in auto_block


# ---------------------------------------------------------------------------
# Phase 8.3b — radio_menu placement invariant
# ---------------------------------------------------------------------------

def test_radio_menu_handler_in_mesh_networks_section():
    """RadioMenuHandler is the Meshtastic radio CLI, must stay under
    Optional Gateways (mesh_networks), not surface as a generic radio."""
    from handlers.radio_menu import RadioMenuHandler
    assert RadioMenuHandler.menu_section == "mesh_networks"
    items = RadioMenuHandler().menu_items()
    flags = {flag for _tag, _desc, flag in items}
    assert flags == {"meshtastic"}, (
        f"RadioMenuHandler items must all be gated on 'meshtastic'; "
        f"found {flags!r}."
    )


def test_meshcore_handler_owns_canonical_radio_config():
    """MeshCore inner submenu has a 'radio' entry — the canonical
    primary radio control after the Phase 1-7 rework. The tag lives
    inside ``_meshcore_menu`` (a per-handler sub-menu), not on the
    registry-level ``menu_items()`` list, so we grep the source.
    """
    src = (LAUNCHER_TUI / "handlers" / "meshcore.py").read_text()
    assert '"radio", "Radio Config' in src, (
        "MeshCore submenu must expose a 'radio' entry pointing at the "
        "canonical LoRa params + channels + TX power menu."
    )
    assert '"radio": ("MeshCore Radio Config"' in src, (
        "MeshCore radio entry must dispatch to _meshcore_radio_menu."
    )
