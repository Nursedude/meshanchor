"""
Phase 8 visibility + service-status regression tests.

Verifies the post-rework gap closures:

1. A profile MARKS a row, it never removes one — the registry returns
   every row on every profile, gated ones prefixed ``[off]``.
2. Selecting a marked row explains itself via ``msgbox`` instead of
   dispatching, intercepted CENTRALLY in ``registry.dispatch`` rather than
   per menu loop, and the explanation never tells the operator to leave
   the TUI (MF018).
3. ``startup_checks.SERVICES_TO_CHECK`` includes ``meshanchor-daemon``
   (the MeshCore-primary daemon) and lists it BEFORE meshtasticd/rnsd.
4. ``DashboardHandler._SERVICE_DISPLAY`` puts MeshCore first.
5. ``_show_node_counts`` consults the node tracker for MeshCore counts.
"""

import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
LAUNCHER_TUI = SRC / "launcher_tui"

sys.path.insert(0, str(LAUNCHER_TUI))
sys.path.insert(0, str(SRC))

sys.path.insert(0, os.path.dirname(__file__))
from handler_test_utils import make_handler_context  # noqa: E402


# ---------------------------------------------------------------------------
# 1. Optional Gateways visibility — entries always render
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def main_source() -> str:
    return (LAUNCHER_TUI / "main.py").read_text()


def _gating_ctx(flags):
    """A context whose profile declares ``flags``, with a recording dialog."""
    from handler_protocol import TUIContext  # noqa: F401 — via handler_test_utils
    ctx = make_handler_context(feature_flags=dict(flags))
    ctx.profile = SimpleNamespace(name="monitor", display_name="Monitor")
    return ctx


def _gating_registry(flags):
    from handler_registry import HandlerRegistry
    from handlers.rns_menu import RNSMenuHandler
    ctx = _gating_ctx(flags)
    registry = HandlerRegistry(ctx)
    registry.register(RNSMenuHandler())
    ctx.registry = registry
    return ctx, registry


def test_a_gated_row_is_marked_not_removed():
    """The menu is the SAME LENGTH with the flag on or off."""
    _ctx_on, reg_on = _gating_registry({"rns": True})
    _ctx_off, reg_off = _gating_registry({"rns": False})
    on = reg_on.get_menu_items("mesh_networks")
    off = reg_off.get_menu_items("mesh_networks")
    assert len(on) == len(off), (
        "a profile changed how many rows exist — it must only change what "
        f"they SAY. on={on} off={off}")
    assert dict(on)["rns"] != dict(off)["rns"], "gated row was not marked"
    assert dict(off)["rns"].startswith("[off] ")
    # The label still says what the tool IS — that is the whole point.
    assert "Reticulum" in dict(off)["rns"]


def test_selecting_a_marked_row_explains_instead_of_dispatching():
    ctx, registry = _gating_registry({"rns": False})
    handler = registry._tag_index["mesh_networks"]["rns"]
    reached = []
    handler.execute = lambda *a, **k: reached.append(a)

    assert registry.dispatch("mesh_networks", "rns") is True, (
        "must return True — the tag IS owned; returning False would reach "
        "the not-wired tripwire and report a bug that does not exist")
    assert not reached, "a row the profile marks off must not run"
    assert ctx.dialog.last_msgbox_text, "refused silently — the worst option"
    body = ctx.dialog.last_msgbox_text
    assert "monitor" in body.lower(), "must name the profile responsible"
    assert "rns" in body.lower(), "must name the flag responsible"
    assert "Deployment Profile" in body, "must say how to change it"


def test_the_refusal_never_sends_the_operator_out_of_the_app():
    """MF018 — in-domain remediation.

    This repo's previous ``_FEATURE_HINTS`` table told the operator to quit
    and run ``python3 src/launcher.py --profile gateway``. An app that
    answers a question by sending you to a shell has failed at the thing it
    is for. The explanation is derived from flag + profile now, so there is
    no table of strings that can drift back into saying this.
    """
    ctx, registry = _gating_registry({"rns": False})
    registry.dispatch("mesh_networks", "rns")
    body = ctx.dialog.last_msgbox_text
    for leak in ("python3 ", "src/launcher.py", "--profile ", "sudo "):
        assert leak not in body, (
            f"the refusal tells the operator to leave the TUI: {leak!r}")


def test_an_allowed_row_still_runs():
    ctx, registry = _gating_registry({"rns": True})
    handler = registry._tag_index["mesh_networks"]["rns"]
    reached = []
    handler.execute = lambda *a, **k: reached.append(a)
    registry.dispatch("mesh_networks", "rns")
    assert reached, "an enabled row must dispatch normally"


def test_no_menu_loop_carries_its_own_gating_short_circuit(main_source: str):
    """Interception lives in ONE place.

    Three per-loop short-circuits used to sit in ``_optional_gateways_menu``.
    A rule implemented per loop is a rule that is missing from one of them —
    which is how this repo ran two designs at once.
    """
    assert "_show_disabled_feature_hint" not in main_source
    assert "_FEATURE_HINTS" not in main_source


def test_the_launcher_loads_its_own_profile():
    """Defect that made every flag inert: nobody handed the TUI a profile.

    Both construction sites build ``MeshAnchorLauncher()`` with no
    argument, and when the TUI is launched as a subprocess the CLI's
    ``--profile`` is in another process entirely. So gating that waits to
    be HANDED a profile never executes — which is why this repo could
    carry feature flags on every handler and never mark a single row.

    The launcher loads the SAVED profile itself.
    """
    import main as tui_main
    assert hasattr(tui_main.MeshAnchorLauncher, "_load_deployment_profile")

    called = []

    class _Stub:
        feature_flags = {"rns": False}
        display_name = "Monitor"
        name = "monitor"

    fake_mod = SimpleNamespace(load_profile=lambda: (called.append(1), _Stub())[1])
    launcher = SimpleNamespace(
        _profile=None,
        _feature_flags={},
        _tui_context=SimpleNamespace(profile=None, feature_flags={}),
        _registry=SimpleNamespace(section_names=[], get_gated_items=lambda s: []),
    )
    with patch.dict(sys.modules, {"utils.deployment_profiles": fake_mod}):
        tui_main.MeshAnchorLauncher._load_deployment_profile(launcher)

    assert called, "the launcher never asked for a saved profile"
    assert launcher._tui_context.feature_flags == {"rns": False}
    assert launcher._tui_context.profile is not None


def test_the_constructor_actually_calls_the_loader():
    """The method existing and working is NOT the method running.

    Drilled 2026-09-16: deleting the call from ``__init__`` left the test
    above green, because it invokes the loader directly. That is the same
    defect class the port is fixing — a mechanism wired in one place and
    believed to run everywhere. Read the constructor's own call graph.
    """
    import ast
    tree = ast.parse((LAUNCHER_TUI / "main.py").read_text())
    cls = next(n for n in ast.walk(tree)
               if isinstance(n, ast.ClassDef) and n.name == "MeshAnchorLauncher")
    init = next(n for n in cls.body
                if isinstance(n, ast.FunctionDef) and n.name == "__init__")
    calls = {n.func.attr for n in ast.walk(init)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
    assert "_load_deployment_profile" in calls, (
        "__init__ never calls _load_deployment_profile — every feature flag "
        "in the tree is inert, which is exactly the bug this replaced")


def test_gating_uses_the_saved_profile_not_service_detection():
    """``load_or_detect_profile`` would mark RNS off when rnsd is merely down.

    Auto-detection reads which services are RUNNING. Gating on it takes the
    tool away at exactly the moment it is needed, and does it silently. A
    saved profile is a human declaration; a detection is a guess.

    Read from the AST, not the source text: the function's own docstring
    names ``load_or_detect_profile`` in order to rule it out, and a
    substring check cannot tell prose from a call.
    """
    import ast
    tree = ast.parse((LAUNCHER_TUI / "main.py").read_text())
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef)
              and n.name == "_load_deployment_profile")
    called = {n.func.id for n in ast.walk(fn)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    imported = {alias.name for n in ast.walk(fn)
                if isinstance(n, ast.ImportFrom) for alias in n.names}
    assert "load_profile" in imported | called
    assert "load_or_detect_profile" not in imported | called


def test_top_level_rows_are_marked_never_hidden(main_source: str):
    """Maps and Tactical used to be dropped from the main menu outright."""
    start = main_source.find("def _run_main_menu(self)")
    end = main_source.find("    def ", start + 1)
    body = main_source[start:end]
    assert 'if self._feature_enabled("maps"):' not in body
    assert 'if self._feature_enabled("tactical"):' not in body
    assert "mark_label" in body, "top-level rows must be marked"


# ---------------------------------------------------------------------------
# 3. SERVICES_TO_CHECK ordering — MeshCore daemon first
# ---------------------------------------------------------------------------

def test_services_to_check_includes_meshanchor_daemon():
    from startup_checks import StartupChecker
    assert "meshanchor-daemon" in StartupChecker.SERVICES_TO_CHECK


def test_services_to_check_lists_meshcore_daemon_first():
    """meshanchor-daemon must precede meshtasticd in iteration order."""
    from startup_checks import StartupChecker
    keys = list(StartupChecker.SERVICES_TO_CHECK.keys())
    assert keys.index("meshanchor-daemon") < keys.index("meshtasticd")
    assert keys.index("meshanchor-daemon") < keys.index("rnsd")


def test_meshanchor_daemon_uses_config_api_port():
    from startup_checks import StartupChecker, CONFIG_API_PORT
    cfg = StartupChecker.SERVICES_TO_CHECK["meshanchor-daemon"]
    assert cfg["port"] == CONFIG_API_PORT
    assert cfg["systemd"] is True


# ---------------------------------------------------------------------------
# 4. Dashboard service display — MeshCore first
# ---------------------------------------------------------------------------

def test_dashboard_display_order_meshcore_first():
    from handlers.dashboard import DashboardHandler
    units = [u for u, _ in DashboardHandler._SERVICE_DISPLAY]
    assert units[0] == "meshanchor-daemon"
    assert units.index("meshanchor-daemon") < units.index("meshtasticd")
    assert units.index("meshanchor-daemon") < units.index("rnsd")


def test_dashboard_display_uses_friendly_labels():
    from handlers.dashboard import DashboardHandler
    labels = dict(DashboardHandler._SERVICE_DISPLAY)
    assert labels["meshanchor-daemon"] == "MeshAnchor (MeshCore)"
    assert "Meshtastic" in labels["meshtasticd"]
    assert "RNS" in labels["rnsd"] or "Reticulum" in labels["rnsd"]


def test_service_status_renders_meshcore_before_meshtasticd(capsys):
    """Iterating the dashboard prints MeshCore row before Meshtastic."""
    from handlers.dashboard import DashboardHandler
    from startup_checks import ServiceRunState

    h = DashboardHandler()
    h.set_context(make_handler_context())

    def _info(state):
        m = MagicMock()
        m.state = state
        return m

    mock_env = MagicMock()
    mock_env.services = {
        "meshtasticd": _info(ServiceRunState.STOPPED),
        "rnsd": _info(ServiceRunState.RUNNING),
        "meshanchor-daemon": _info(ServiceRunState.RUNNING),
    }
    h.ctx.env_state = mock_env
    h.ctx.wait_for_enter = MagicMock()
    h._service_status_display()
    out = capsys.readouterr().out
    mc_idx = out.find("MeshAnchor (MeshCore)")
    mt_idx = out.find("Meshtastic Gateway")
    assert mc_idx >= 0, "MeshCore row missing from output"
    assert mt_idx >= 0, "Meshtastic row missing from output"
    assert mc_idx < mt_idx, "MeshCore must render before Meshtastic"


# ---------------------------------------------------------------------------
# 5. Node counts include MeshCore
# ---------------------------------------------------------------------------

def test_node_counts_include_meshcore(capsys):
    from handlers.dashboard import DashboardHandler

    fake_tracker = MagicMock()
    fake_tracker.get_meshcore_nodes.return_value = [{"id": "abc"}, {"id": "def"}]

    fake_module = MagicMock()
    fake_module.get_node_tracker.return_value = fake_tracker

    h = DashboardHandler()
    h.set_context(make_handler_context())
    h.ctx.wait_for_enter = MagicMock()

    with patch.dict(sys.modules, {"gateway.node_tracker": fake_module}):
        with patch("handlers.dashboard.get_http_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.is_available = False
            mock_get_client.return_value = mock_client
            with patch("handlers.dashboard.subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=0, stdout="")
                h._show_node_counts()
    out = capsys.readouterr().out
    assert "MeshCore nodes:" in out
    assert "2" in out  # the count we mocked


def test_dashboard_menu_label_says_meshcore_first():
    """The 'Node Count' menu description names MeshCore before Meshtastic."""
    from handlers.dashboard import DashboardHandler
    h = DashboardHandler()
    items = dict((tag, desc) for tag, desc, _ in h.menu_items())
    nodes_desc = items["nodes"]
    assert nodes_desc.index("MeshCore") < nodes_desc.index("Meshtastic")
