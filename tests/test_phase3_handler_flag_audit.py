"""
Phase 3 handler feature-flag audit smoke tests.

Verifies that every handler whose logic genuinely requires Meshtastic / RNS /
Gateway / MQTT subsystems has the correct ``feature_flag=`` value on its
``menu_items()`` rows, so that under the MESHCORE-only deployment profile
the Optional Gateways submenu and the RNS submenu collapse to just the
cross-radio entries (HAM, AREDN, Favorites, Services).

Policy: **opt-in flagging only** — handlers without a clear technical
dependency stay unflagged (always-visible).
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
# Fixtures
# ---------------------------------------------------------------------------

class _FakeDialog:
    def msgbox(self, *a, **k): pass
    def menu(self, *a, **k): return None
    def yesno(self, *a, **k): return False
    def inputbox(self, *a, **k): return ""
    def textbox(self, *a, **k): pass
    def gauge(self, *a, **k): pass
    def set_status_bar(self, *a, **k): pass


# Mirrors deployment_profiles.PROFILES[ProfileName.MESHCORE].feature_flags
MESHCORE_FLAGS = {
    "meshtastic": False,
    "meshcore": True,
    "rns": False,
    "gateway": False,
    "mqtt": False,
    "maps": False,
    "tactical": False,
}

FULL_FLAGS = {
    "meshtastic": True,
    "meshcore": True,
    "rns": True,
    "gateway": True,
    "mqtt": True,
    "maps": True,
    "tactical": True,
}


def _build_registry(feature_flags):
    from handler_protocol import TUIContext
    from handler_registry import HandlerRegistry
    from handlers import get_all_handlers

    ctx = TUIContext(dialog=_FakeDialog(), feature_flags=feature_flags)
    reg = HandlerRegistry(ctx)
    for cls in get_all_handlers():
        reg.register(cls())
    return reg


# ---------------------------------------------------------------------------
# Per-handler flag assertions (the matrix from Phase 3)
# ---------------------------------------------------------------------------

# Tag -> expected flag string (after Phase 3). Each entry is (tag, flag).
EXPECTED_FLAGS = [
    # Meshtastic-bound handlers
    ("meshtastic", "meshtastic"),       # radio_menu
    ("automation", "meshtastic"),
    ("traffic", "meshtastic"),          # classifier
    # RNS-bound handlers (mesh_networks section)
    ("rns", "rns"),                     # rns_menu
    ("nomadnet", "rns"),
    # RNS-bound handlers (rns section)
    ("config", "rns"),                  # rns_config
    ("edit", "rns"),
    ("logging", "rns"),
    ("check", "rns"),
    ("diag", "rns"),                    # rns_diagnostics
    ("repair", "rns"),
    ("drift", "rns"),
    ("ifaces", "rns"),                  # rns_interfaces
    ("monitor", "rns"),                 # rns_monitor
    ("sniffer", "rns"),                 # rns_sniffer
    ("tools", "rns"),                   # rns_tools (MN-3)
    # Gateway-bound (cross-protocol)
    ("gateway", "gateway"),
    ("dual_failover", "gateway"),
    ("load_balancer", "gateway"),
    ("mesh_alerts", "gateway"),
    ("messaging", "gateway"),
    ("preflight", "gateway"),           # gateway_preflight (MN-2)
    ("export", "gateway"),              # gateway_preflight (MN-2)
    ("test_gateway_rx", "gateway"),     # test_gateway_rx (MN-2)
    # MQTT-bound
    ("mqtt", "mqtt"),
    ("broker-menu", "mqtt"),
]

# Tags that must REMAIN unflagged (always-visible cross-radio entries)
ALWAYS_VISIBLE_TAGS = {"ham", "aredn", "favorites", "services"}

# Sections this audit covers. Other sections (maps_viz, system, etc.) may
# legitimately reuse a tag like "traffic" with different meaning, so the
# audit must scope by section.
PHASE3_SECTIONS = {"mesh_networks", "rns"}


def _rows_in_phase3_sections():
    """Yield (section, tag, flag) for every menu_items() row whose handler
    lives in a Phase 3 section."""
    from handlers import get_all_handlers
    for cls in get_all_handlers():
        if cls.menu_section not in PHASE3_SECTIONS:
            continue
        for tag, _desc, flag in cls().menu_items():
            yield cls.menu_section, tag, flag


@pytest.mark.parametrize("tag,expected_flag", EXPECTED_FLAGS)
def test_handler_row_has_expected_flag(tag, expected_flag):
    """Each row above must carry the expected feature_flag string within
    the mesh_networks/rns sections."""
    found = [
        flag for section, item_tag, flag in _rows_in_phase3_sections()
        if item_tag == tag
    ]
    assert found, f"Tag {tag!r} not found in mesh_networks/rns sections"
    assert all(f == expected_flag for f in found), (
        f"Tag {tag!r} expected flag {expected_flag!r}, got {found!r}"
    )


@pytest.mark.parametrize("tag", sorted(ALWAYS_VISIBLE_TAGS))
def test_always_visible_tag_remains_unflagged(tag):
    """Cross-radio rows must keep flag=None per opt-in policy."""
    found = [
        flag for section, item_tag, flag in _rows_in_phase3_sections()
        if item_tag == tag
    ]
    assert found, f"Tag {tag!r} not found in mesh_networks/rns sections"
    assert all(f is None for f in found), (
        f"Tag {tag!r} should remain unflagged (None), got {found!r}"
    )


# ---------------------------------------------------------------------------
# Profile-level integration: registry-side filtering
# ---------------------------------------------------------------------------

def test_meshcore_profile_marks_all_gateway_rows_off():
    """Under MESHCORE flags every gated row is still SHOWN, marked [off].

    Asserted the opposite until 2026-09-16 — that the rows vanished. They
    are shown and marked now: someone new to the domain cannot go looking
    for a capability they have never been shown.
    """
    reg = _build_registry(MESHCORE_FLAGS)

    rows = dict(reg.get_menu_items("mesh_networks"))
    rows.update(dict(reg.get_menu_items("rns")))

    gated_tags = {tag for tag, flag in EXPECTED_FLAGS
                  if not MESHCORE_FLAGS.get(flag, True)}
    missing = gated_tags - set(rows)
    assert not missing, (
        f"MESHCORE profile REMOVED gated tags instead of marking them: "
        f"{sorted(missing)}"
    )
    unmarked = [t for t in gated_tags if not rows[t].startswith("[off] ")]
    assert not unmarked, (
        f"gated rows rendered with no [off] mark: {sorted(unmarked)}"
    )


def test_meshcore_profile_keeps_cross_radio_rows():
    """The unflagged cross-radio rows (HAM, AREDN, Favorites, Services)
    must still be visible under MESHCORE."""
    reg = _build_registry(MESHCORE_FLAGS)
    visible = {tag for tag, _ in reg.get_menu_items("mesh_networks")}
    missing = ALWAYS_VISIBLE_TAGS - visible
    assert not missing, (
        f"MESHCORE profile dropped always-visible tags: {sorted(missing)}"
    )


def test_meshcore_profile_rns_section_is_fully_marked_not_empty():
    """Every row in the rns section is flagged 'rns', so under MESHCORE
    every one of them is marked [off] — and the section is NOT empty.

    A section that collapses to nothing tells the operator the tool does
    not exist. A section where every row reads [off] tells them what the
    box could do and why this one does not.
    """
    reg = _build_registry(MESHCORE_FLAGS)
    items = reg.get_menu_items("rns")
    assert items, "rns section collapsed to empty; rows must be marked"
    unmarked = [t for t, d in items if not d.startswith("[off] ")]
    assert not unmarked, f"rns rows not marked under MESHCORE: {unmarked}"


def test_full_profile_shows_everything():
    """Under FULL feature flags every gated tag is visible again."""
    reg = _build_registry(FULL_FLAGS)
    visible_in_mesh = {tag for tag, _ in reg.get_menu_items("mesh_networks")}
    visible_in_rns = {tag for tag, _ in reg.get_menu_items("rns")}
    expected_visible = {tag for tag, _ in EXPECTED_FLAGS}
    missing = expected_visible - (visible_in_mesh | visible_in_rns)
    assert not missing, (
        f"FULL profile missing tags: {sorted(missing)}"
    )
