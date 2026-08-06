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


#: Modules that resolve an operator data store from ``get_real_user_home()``
#: at MODULE level and WRITE it. Each is patched at its own module attribute
#: (every import alias), which redirects only that module — patching
#: ``utils.paths.get_real_user_home`` itself would break the tests that assert
#: what it returns under sudo, and patching ``MeshAnchorPaths.get_config_dir``
#: (the SSOT classmethod) would break ``test_paths.py``'s pin of it. Isolation
#: belongs at the narrowest layer that still covers every writer.
#: Found by full-tree directory sweep, not by memory — see
#: ``_isolate_operator_data_stores``.
_OPERATOR_STORE_MODULES = (
    "commands.messaging",              # messages.db
    "gateway.message_queue",           # message_queue.db
    "commands.rns",                    # lxmf_storage/ (LXMF ratchets)
    "gateway._rns_bridge_connection",  # lxmf_storage/ (LXMF ratchets)
    "utils.map_data_collector",        # map_nodes.geojson, node_history.db
)


@pytest.fixture(scope="session", autouse=True)
def _isolate_operator_data_stores(tmp_path_factory):
    """Keep the suite out of the operator's message store, queue, LXMF
    ratchets, map cache and preflight-template directory.

    Sibling of ``_isolate_node_cache_files`` and
    ``_isolate_delivery_counters_db``, found the same way. Measured on
    VolcanoAI 2026-08-05 by sweeping the whole operator tree
    (``~/.config/meshanchor`` + ``~/.local/share/meshanchor`` +
    ``~/.cache/meshanchor``, 1,174 files) across one suite run, with an idle
    CONTROL run of equal length subtracted and md5 used to separate
    "rewritten identically" from real mutation. The control showed ZERO
    self-change — no MeshAnchor daemon runs on that box — so attribution was
    unusually clean. Six artifacts genuinely mutated:

        lxmf_storage/lxmf/ratchets/*.ratchets   LXMF crypto ratchet state
        messages.db                             message store
        message_queue.db                        persistent queue
        delivery_counters.db                    (see the sibling fixture)
        map_nodes.geojson                       map collector cache
        templates/exported_<ts>.json            LITTER — one NEW file per run

    The ratchets are the reason this is a gate and not a note: they are
    per-destination cryptographic state, and corrupting them fails opaquely
    and late.

    ⚠️ ``map_nodes.geojson`` is suite-caused HERE. On MeshForge the same
    filename is written by the live map daemon and is NOT pollution — which
    is exactly why the idle control run is part of the method. Re-run the
    control before trusting this list on a box where MeshAnchor daemons do
    run (meshanchor-server).

    Session-scoped: redirection is idempotent and O(1). The first version of
    the delivery-counters guard on MeshForge was function-scoped and cost
    ~45% of suite wall-clock (6:08 -> 8:54) plus a CANCELLED CI 3.9 job on
    its timeout — and `cancelled` is not `success`, so it read as "3 of 4
    green". Per-test isolation between cases that assert on CONTENT stays the
    job of those files' own fixtures, which patch inside this one and win.
    """
    root = tmp_path_factory.mktemp("operator_home_isolation")
    (root / ".config" / "meshanchor").mkdir(parents=True, exist_ok=True)

    from utils.paths import MeshAnchorPaths, get_real_user_home as _get_real_home
    real_home = _get_real_home()

    def _make_guarded_export(original):
        """Redirect an export that would land in the operator's REAL home.

        Deliberately NOT a blanket redirect. ``get_config_dir()`` is resolved
        at CALL time, so a test that has monkeypatched it to its own tmp dir
        is passed straight through and still asserts what it means to assert
        (``test_export_default_target_uses_meshanchor_config_dir``). Only a
        write that resolves under the real user home is diverted.
        """
        def guarded(live, target_dir=None):
            if target_dir is None:
                resolved = MeshAnchorPaths.get_config_dir() / "templates"
                try:
                    resolved.relative_to(real_home)
                except ValueError:
                    target_dir = resolved      # already isolated by the caller
                else:
                    target_dir = root / ".config" / "meshanchor" / "templates"
            return original(live, target_dir=target_dir)
        return guarded

    with ExitStack() as stack:
        patched = []
        for mod in _OPERATOR_STORE_MODULES:
            for alias in (mod, f"src.{mod}"):
                try:
                    stack.enter_context(
                        patch(f"{alias}.get_real_user_home", return_value=root))
                    patched.append(alias)
                except (ImportError, AttributeError, ModuleNotFoundError):
                    continue

        # templates/exported_<ts>.json — LITTER, a NEW file every run by
        # design ("timestamp suffix makes each export unique", so nothing
        # ever overwrites and nothing ever cleans up). 183 had accumulated
        # on VolcanoAI before this line existed.
        #
        # ⚠️ This module does NOT resolve the path through
        # ``get_real_user_home`` the way MeshForge's twin does — it goes
        # through ``MeshAnchorPaths.get_config_dir()`` (line ~431). The
        # MeshForge fixture's ``_OPERATOR_STORE_MODULES`` line ported
        # verbatim would patch a name this module never calls, cover
        # nothing, and still report itself as applied. Verified, not
        # inherited.
        #
        # Wrap the WRITER, not the path. Two earlier shapes were wrong:
        #   - patching ``MeshAnchorPaths.get_config_dir`` globally silences
        #     ``test_paths.py``'s pin of it (trap 4 / honest_failure_modes
        #     #5 — isolation must not disable the SSOT a test exists to
        #     pin);
        #   - rebinding this module's ``MeshAnchorPaths`` to a subclass
        #     that overrides ``get_config_dir`` broke inner-patch-wins,
        #     because a subclass attribute SHADOWS the parent attribute a
        #     test monkeypatches — measured: it failed
        #     ``test_capture_reads_gateway_config``, which redirects the
        #     config dir and writes its own gateway.json. It also
        #     needlessly redirected the module's gateway.json READ; only
        #     the export WRITES litter.
        # The real litter source is ``gateway_preflight._run_export``
        # calling ``export_current_as_template(live)`` with no target_dir
        # — not the two export tests, which are already isolated.
        # Reached under three aliases: sys.path carries src/ AND
        # src/launcher_tui.
        for alias in ("launcher_tui.handlers._gateway_preflight_template",
                      "src.launcher_tui.handlers._gateway_preflight_template",
                      "handlers._gateway_preflight_template"):
            try:
                mod = __import__(alias, fromlist=["export_current_as_template"])
                stack.enter_context(patch(
                    f"{alias}.export_current_as_template",
                    _make_guarded_export(mod.export_current_as_template)))
                patched.append(alias)
            except (ImportError, AttributeError, ModuleNotFoundError):
                continue

        # device_config.yaml — the RADIO config. It does not exist on
        # VolcanoAI today and the sweep never saw it, but the WRITER does
        # (`utils/device_config_store.py`, both `save_device_setting` and
        # `save_device_settings` funnel through `_get_config_path`), so the
        # only thing standing between the suite and a radio-bound file is
        # that no test has called it yet. On MeshForge this was the one file
        # here that could do real-world harm: its header says "Re-applied
        # automatically after meshtasticd restart", and a wrong preset
        # written on a SHORT_TURBO gateway would take it off the air. Gate
        # the chokepoint now rather than after the file appears.
        for alias in ("utils.device_config_store", "src.utils.device_config_store"):
            try:
                stack.enter_context(patch(
                    f"{alias}._get_config_path",
                    return_value=root / ".config" / "meshanchor" / "device_config.yaml"))
                patched.append(alias)
            except (ImportError, AttributeError, ModuleNotFoundError):
                continue

        if not patched:
            raise RuntimeError(
                "operator data-store isolation patched NOTHING — the suite "
                "would write the operator's messages.db / message_queue.db / "
                "LXMF ratchets / map_nodes.geojson / template litter"
            )
        yield root


@pytest.fixture(scope="session", autouse=True)
def _isolate_delivery_counters_db(tmp_path_factory):
    """Keep delivery-counter writes out of the operator's live DB.

    ``delivery_counters`` is SQLite-backed at
    ``~/.local/share/meshanchor/delivery_counters.db``, and the coupling is
    INDIRECT: a test never names it — it constructs a
    ``PersistentMessageQueue`` or a bridge, and lifecycle events flow
    through. That is why per-file fixtures kept missing it.

    Measured on VolcanoAI 2026-08-05: **queued 46,916 / sent 4,745 /
    confirmed 0** — a 9.9:1 queued:sent ratio with nothing ever confirmed,
    on a box that runs no gateway. A real gateway runs queued ~= sent with
    large confirmed counts, so that whole DB is suite exhaust. MeshForge's
    manager box showed the same signature (9.5:1) and its garbage was
    briefly reasoned about AS fleet evidence before the write schedule gave
    it away.

    ⚠️ MeshForge needed FOUR env seams here; MeshAnchor has exactly ONE
    (``MESHANCHOR_DELIVERY_COUNTERS_DB``). That is a verified absence, not
    an assumption — the other three (``_DELIVERY_SNAPSHOT_STATE``,
    ``_CONTENT_ID_VIEW_STATE``, ``_QUEUE_STATS_STATE``) have no MeshAnchor
    equivalent because the state files behind them do not exist here.
    MeshForge shipped this guard MISSING its fourth seam because
    verification watched only the two files already in mind; the check is a
    full-tree sweep, never a hand-picked list. Re-run the sweep if the
    gateway grows a new state file.

    Suite-wide and autouse on purpose: a gate, not a convention. A file that
    sets the env var itself still wins (both point at tmp dirs), and a test
    that needs the real resolution deletes the var explicitly.
    """
    root = tmp_path_factory.mktemp("delivery_counters_isolation")
    var = "MESHANCHOR_DELIVERY_COUNTERS_DB"
    prior = os.environ.get(var)
    os.environ[var] = str(root / "delivery_counters.db")
    for alias in ("gateway.delivery_counters", "src.gateway.delivery_counters"):
        try:
            __import__(alias)
            sys.modules[alias]._reset_singleton_for_tests()
        except (ImportError, AttributeError, ModuleNotFoundError, KeyError):
            continue
    yield root
    if prior is None:
        os.environ.pop(var, None)
    else:
        os.environ[var] = prior


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
