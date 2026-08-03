"""
Pytest configuration for MeshAnchor test suite.

Handles CI-specific settings:
- Auto-skip hardware-dependent tests in CI
- Timeout defaults
- Fixtures for common mocks
- Shared TUI handler test infrastructure (FakeDialog, make_handler_context)
"""

import os
import sys
import warnings
import weakref
from contextlib import ExitStack

import pytest
from unittest.mock import MagicMock, patch

# Ensure src and launcher_tui are importable for handler tests
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src', 'launcher_tui'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

# Detect CI environment
CI = os.environ.get('CI', 'false').lower() == 'true'
MESHANCHOR_CI = os.environ.get('MESHANCHOR_CI', 'false').lower() == 'true'


def pytest_configure(config):
    """Register custom markers."""
    config.addinivalue_line(
        "markers", "hardware: mark test as requiring hardware (skipped in CI)"
    )
    config.addinivalue_line(
        "markers", "slow: mark test as slow (may be skipped with --fast)"
    )
    config.addinivalue_line(
        "markers", "network: mark test as requiring network access"
    )


def pytest_sessionfinish(session, exitstatus):
    """Shut down global background state before pytest closes IO.

    - event_bus thread pool: background workers in src/utils/event_bus.py
      otherwise fire callbacks (e.g. StatusBar._on_service_event) that log
      to pytest-closed streams, producing `ValueError: I/O operation on
      closed file` noise.
    - meshtastic thread guard: src/utils/meshtastic_connection.py globally
      mutates `threading.excepthook` on import. Restore it here so the
      hook doesn't leak into later tooling that shares the process.
    """
    try:
        from utils.event_bus import event_bus
        event_bus.shutdown()
    except Exception as e:
        warnings.warn(f"event_bus shutdown failed: {e}", stacklevel=2)

    try:
        from utils.meshtastic_connection import uninstall_meshtastic_thread_guard
        uninstall_meshtastic_thread_guard()
    except ImportError:
        pass
    except Exception as e:
        warnings.warn(f"thread guard uninstall failed: {e}", stacklevel=2)


@pytest.fixture(autouse=True)
def _reset_event_bus_subscribers():
    """Clear event_bus subscribers between tests.

    Prevents stale callbacks (e.g. a StatusBar instance from a prior test)
    from firing on the shared thread pool after their owning test has torn
    down, which would otherwise log to a pytest-closed stream.
    """
    yield
    try:
        from utils.event_bus import event_bus
        event_bus.clear_subscribers()
    except Exception as e:
        warnings.warn(f"event_bus.clear_subscribers failed: {e}", stacklevel=2)


def _clear_all_service_check_caches():
    """Clear the TTL cache regardless of how the test imported the
    module. Some tests use `from src.utils.service_check import ...`
    and others use `from utils.service_check import ...`; Python
    treats those as two separate modules with their own caches, so
    we have to clear every loaded variant.
    """
    import sys
    for module_name in ("utils.service_check", "src.utils.service_check"):
        mod = sys.modules.get(module_name)
        if mod is not None:
            try:
                mod.clear_service_cache()
            except Exception:
                pass


@pytest.fixture(autouse=True)
def _reset_service_check_cache():
    """Clear the `check_service` TTL cache between every test.

    The cache cuts production load from dashboard polling but it
    persists across in-process pytest runs, so a real `check_service`
    call in test A pollutes the cache and short-circuits a mocked
    `check_service` call in test B (e.g. test_status_consistency
    mocks subprocess.run but never sees the call because the cache
    hits first). Clearing before AND after keeps tests independent
    regardless of order.
    """
    _clear_all_service_check_caches()
    yield
    _clear_all_service_check_caches()


# Track RNSMeshtasticBridge instances so we can stop leaked background threads.
# Threads like _bridge_loop otherwise keep calling emit_service_status after
# pytest closes captured streams, producing "I/O operation on closed file" noise.
# Module-level WeakSet is per-process; each pytest-xdist worker has its own.
_live_bridges: "weakref.WeakSet" = weakref.WeakSet()


def _install_bridge_tracker():
    """Wrap RNSMeshtasticBridge.__init__ once to register instances."""
    try:
        from gateway.rns_bridge import RNSMeshtasticBridge
    except Exception:
        return

    if getattr(RNSMeshtasticBridge.__init__, "_meshanchor_tracked", False):
        return

    original_init = RNSMeshtasticBridge.__init__

    def tracked_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        _live_bridges.add(self)

    tracked_init._meshanchor_tracked = True  # type: ignore[attr-defined]
    RNSMeshtasticBridge.__init__ = tracked_init


_install_bridge_tracker()


@pytest.fixture(autouse=True)
def _stop_leaked_bridges():
    """Stop any RNSMeshtasticBridge instances still running after a test."""
    yield
    for bridge in list(_live_bridges):
        try:
            if getattr(bridge, "_running", False):
                bridge.stop()
            else:
                # Ensure background threads that only check _stop_event wake up.
                stop_event = getattr(bridge, "_stop_event", None)
                if stop_event is not None:
                    stop_event.set()
        except Exception as e:
            warnings.warn(f"bridge teardown failed: {e}", stacklevel=2)


@pytest.fixture(autouse=True)
def _isolate_node_cache_files(tmp_path_factory):
    """Keep the node-cache writers out of the operator's live data directory.

    ``UnifiedNodeTracker._save_cache`` writes TWO files: the one
    ``get_cache_file()`` names (``~/.config/meshanchor/node_cache.json``) and
    an operator-owned copy at ``MeshAnchorPaths.rns_nodes_cache_path()``
    (``~/.cache/meshanchor/rns_nodes.json``). Any test that constructs a bare
    ``UnifiedNodeTracker()`` therefore reads and overwrites real fleet data —
    on meshanchor-server those are 11.3 MB and 8.9 MB of live node state that
    the map and the LXMF probes read.

    Autouse and suite-wide on purpose. A per-file guard only holds until the
    next test file constructs a tracker, so this is a gate, not a convention:
    a test's verdict must not depend on which box ran it, and neither must its
    side effects (feedback_tests_must_pin_ambient_state).

    Tests that assert on cache CONTENT patch these to their own tmp paths;
    an inner patch wins, so this only catches what would otherwise escape.

    BOTH import aliases are patched. ``sys.path`` carries the repo root and
    ``src/``, so ``gateway.node_tracker`` and ``src.gateway.node_tracker`` are
    two distinct module objects holding two distinct ``UnifiedNodeTracker``
    classes — patching one leaves the other writing to the live path, and
    MeshAnchor's tests use both (``test_gateway_integration`` imports via
    ``src.``). Patching only the alias in front of you is precisely the
    half-covered guard this fixture exists to prevent.
    """
    root = tmp_path_factory.mktemp("node_cache_isolation")
    targets = [
        ('utils.paths.MeshAnchorPaths.rns_nodes_cache_path', root / "rns_nodes.json"),
        ('src.utils.paths.MeshAnchorPaths.rns_nodes_cache_path', root / "rns_nodes.json"),
        ('gateway.node_tracker.UnifiedNodeTracker.get_cache_file', root / "node_cache.json"),
        ('src.gateway.node_tracker.UnifiedNodeTracker.get_cache_file', root / "node_cache.json"),
    ]
    with ExitStack() as stack:
        patched = 0
        for target, value in targets:
            try:
                stack.enter_context(patch(target, return_value=value))
                patched += 1
            except (ImportError, AttributeError, ModuleNotFoundError):
                # An alias that is not importable in this environment cannot
                # be the one writing to the live path either.
                continue
        if not patched:
            raise RuntimeError(
                "node cache isolation patched NOTHING — the suite would write "
                "to the operator's live node_cache.json / rns_nodes.json"
            )
        yield root


def pytest_collection_modifyitems(config, items):
    """Auto-skip certain tests in CI environment."""
    if not (CI or MESHANCHOR_CI):
        return

    skip_hardware = pytest.mark.skip(reason="Hardware not available in CI")
    skip_network = pytest.mark.skip(reason="Network tests skipped in CI")

    for item in items:
        # Skip hardware-marked tests
        if "hardware" in item.keywords:
            item.add_marker(skip_hardware)

        # Skip network-marked tests in CI
        if "network" in item.keywords:
            item.add_marker(skip_network)

        # Auto-detect likely hardware tests by name
        test_name = item.name.lower()
        if any(kw in test_name for kw in ['real_device', 'physical', 'actual_hardware']):
            item.add_marker(skip_hardware)


@pytest.fixture
def mock_meshtastic():
    """Mock meshtastic module for tests that don't need real hardware."""
    mock_module = MagicMock()
    mock_interface = MagicMock()
    mock_interface.nodes = {}
    mock_interface.myInfo = MagicMock()
    mock_interface.myInfo.my_node_num = 12345678

    mock_module.serial_interface.SerialInterface.return_value = mock_interface
    mock_module.tcp_interface.TCPInterface.return_value = mock_interface

    with patch.dict('sys.modules', {
        'meshtastic': mock_module,
        'meshtastic.serial_interface': mock_module.serial_interface,
        'meshtastic.tcp_interface': mock_module.tcp_interface,
    }):
        yield mock_module


@pytest.fixture
def mock_rns():
    """Mock RNS module for tests that don't need real Reticulum."""
    mock_module = MagicMock()

    with patch.dict('sys.modules', {
        'RNS': mock_module,
    }):
        yield mock_module


@pytest.fixture
def no_network():
    """Block network access for isolated tests."""
    import socket
    original_socket = socket.socket

    def guarded_socket(*args, **kwargs):
        raise OSError("Network access blocked in test")

    with patch.object(socket, 'socket', guarded_socket):
        yield


# =============================================================================
# TUI Handler Test Infrastructure
# =============================================================================

class FakeDialog:
    """Full-featured dialog stub for handler unit testing.

    Supports programmable return sequences for menu/inputbox/yesno,
    call recording for assertion, and attribute tracking.

    Usage:
        dialog = FakeDialog()
        dialog._menu_returns = ["status", "back"]  # pops from front
        dialog._yesno_returns = [True, False]
        dialog._inputbox_returns = ["localhost"]

        # After handler runs:
        assert dialog.last_msgbox_title == "Service Status"
        assert len(dialog.calls) == 3
    """

    def __init__(self):
        self.calls = []  # [(method, args, kwargs), ...]
        self._menu_returns = []
        self._inputbox_returns = []
        self._yesno_returns = []
        self._radiolist_returns = []
        self._checklist_returns = []
        self.last_msgbox_title = None
        self.last_msgbox_text = None

    def msgbox(self, title, text, **kwargs):
        self.calls.append(('msgbox', (title, text), kwargs))
        self.last_msgbox_title = title
        self.last_msgbox_text = text

    def menu(self, title, text, choices, **kwargs):
        self.calls.append(('menu', (title, text, choices), kwargs))
        if self._menu_returns:
            return self._menu_returns.pop(0)
        return None  # Exits menu loop

    def yesno(self, title, text, **kwargs):
        self.calls.append(('yesno', (title, text), kwargs))
        if self._yesno_returns:
            return self._yesno_returns.pop(0)
        return False

    def inputbox(self, title, text, init="", **kwargs):
        self.calls.append(('inputbox', (title, text), {'init': init, **kwargs}))
        if self._inputbox_returns:
            return self._inputbox_returns.pop(0)
        return init

    def radiolist(self, title, text, choices, **kwargs):
        self.calls.append(('radiolist', (title, text, choices), kwargs))
        if self._radiolist_returns:
            return self._radiolist_returns.pop(0)
        return None

    def checklist(self, title, text, choices, **kwargs):
        self.calls.append(('checklist', (title, text, choices), kwargs))
        if self._checklist_returns:
            return self._checklist_returns.pop(0)
        return []

    def textbox(self, path, **kwargs):
        self.calls.append(('textbox', (path,), kwargs))

    def gauge(self, text, percent, **kwargs):
        self.calls.append(('gauge', (text, percent), kwargs))

    def set_status_bar(self, bar):
        self.calls.append(('set_status_bar', (bar,), {}))


def make_handler_context(**overrides):
    """Factory for TUIContext with test defaults.

    Accepts any TUIContext field as a keyword override.

    Usage:
        ctx = make_handler_context()
        ctx = make_handler_context(feature_flags={"maps": True})
        ctx = make_handler_context(dialog=custom_dialog)
    """
    from handler_protocol import TUIContext
    defaults = dict(
        dialog=FakeDialog(),
        feature_flags={},
    )
    defaults.update(overrides)
    return TUIContext(**defaults)
