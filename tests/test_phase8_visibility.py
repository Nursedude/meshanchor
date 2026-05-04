"""
Phase 8 visibility + service-status regression tests.

Verifies the post-rework gap closures:

1. ``_optional_gateways_menu`` always renders Meshtastic / RNS / Gateway
   entries — they no longer vanish when feature flags are off.
2. Selecting a disabled feature shows an enable-hint via ``msgbox``
   instead of dispatching the handler (which would crash on missing deps).
3. ``startup_checks.SERVICES_TO_CHECK`` includes ``meshanchor-daemon``
   (the MeshCore-primary daemon) and lists it BEFORE meshtasticd/rnsd.
4. ``DashboardHandler._SERVICE_DISPLAY`` puts MeshCore first.
5. ``_show_node_counts`` consults the node tracker for MeshCore counts.
"""

import os
import sys
from pathlib import Path
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


def test_optional_gateways_always_renders_meshtastic(main_source: str):
    """Meshtastic row is always added to the legacy list, regardless of flag."""
    start = main_source.find("def _optional_gateways_menu(self)")
    end = main_source.find("def ", start + 1)
    body = main_source[start:end]
    # The row is appended unconditionally. The label is conditional, but the
    # tuple itself must always be in the list.
    assert '"meshtastic"' in body
    assert '"rns"' in body
    assert '"gateway"' in body
    # Old gating pattern is gone
    assert 'if self._feature_enabled("meshtastic"):' not in body
    assert 'if self._feature_enabled("rns"):' not in body
    assert 'if self._feature_enabled("gateway"):' not in body


def test_disabled_feature_dispatch_short_circuits(main_source: str):
    """Selecting a disabled radio routes to _show_disabled_feature_hint."""
    start = main_source.find("def _optional_gateways_menu(self)")
    end = main_source.find("def ", start + 1)
    body = main_source[start:end]
    assert "self._show_disabled_feature_hint(\"meshtastic\")" in body
    assert "self._show_disabled_feature_hint(\"rns\")" in body
    assert "self._show_disabled_feature_hint(\"gateway\")" in body


def test_show_disabled_feature_hint_defined(main_source: str):
    """The hint helper exists and references the active profile."""
    assert "def _show_disabled_feature_hint(self, feature: str)" in main_source
    assert "_FEATURE_HINTS" in main_source


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
