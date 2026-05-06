"""Unit tests for utils.meshcore_connection.

Covers the lock/persistent-owner contract that the gateway handler and any
future short-lived consumers (TUI probes, CLI helpers) depend on.
"""

from __future__ import annotations

import asyncio
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from utils import meshcore_connection as mc


@pytest.fixture(autouse=True)
def _reset_singleton():
    """Each test gets a fresh manager + clean lock."""
    mc.reset_connection_manager()
    # Make sure no test leaks a held lock.
    while mc.MESHCORE_CONNECTION_LOCK.locked():
        try:
            mc.MESHCORE_CONNECTION_LOCK.release()
        except RuntimeError:
            break
    yield
    mc.reset_connection_manager()
    while mc.MESHCORE_CONNECTION_LOCK.locked():
        try:
            mc.MESHCORE_CONNECTION_LOCK.release()
        except RuntimeError:
            break


class TestSingleton:
    def test_get_returns_same_instance(self):
        a = mc.get_connection_manager()
        b = mc.get_connection_manager()
        assert a is b

    def test_reset_drops_singleton(self):
        a = mc.get_connection_manager()
        mc.reset_connection_manager()
        b = mc.get_connection_manager()
        assert a is not b


class TestPersistentRegistration:
    def test_register_publishes_state(self):
        loop = MagicMock(spec=asyncio.AbstractEventLoop)
        loop.is_running.return_value = True
        meshcore = MagicMock()
        mgr = mc.get_connection_manager()
        mgr.register_persistent(
            meshcore, loop,
            owner="gateway-bridge",
            mode=mc.ConnectionMode.SERIAL,
            device="/dev/ttyMeshCore",
        )
        assert mgr.has_persistent()
        assert mgr.get_meshcore() is meshcore
        assert mgr.get_loop() is loop
        assert mgr.get_persistent_owner() == "gateway-bridge"
        assert mgr.get_mode() is mc.ConnectionMode.SERIAL
        assert mgr.get_device() == "/dev/ttyMeshCore"

    def test_unregister_clears_state(self):
        loop = MagicMock(spec=asyncio.AbstractEventLoop)
        meshcore = MagicMock()
        mgr = mc.get_connection_manager()
        mgr.register_persistent(meshcore, loop, owner="test")
        mgr.unregister_persistent()
        assert not mgr.has_persistent()
        assert mgr.get_meshcore() is None
        assert mgr.get_persistent_owner() is None

    def test_unregister_when_empty_is_noop(self):
        # Should not raise even when nothing is registered.
        mc.get_connection_manager().unregister_persistent()

    def test_status_snapshot(self):
        mgr = mc.get_connection_manager()
        snap = mgr.status()
        assert snap["connected"] is False
        assert snap["owner"] is None
        assert snap["lock_held"] is False
        meshcore = MagicMock()
        loop = MagicMock(spec=asyncio.AbstractEventLoop)
        mgr.register_persistent(
            meshcore, loop, owner="x",
            mode=mc.ConnectionMode.TCP, device="localhost:4000",
        )
        snap = mgr.status()
        assert snap["connected"] is True
        assert snap["owner"] == "x"
        assert snap["mode"] == "tcp"
        assert snap["device"] == "localhost:4000"


class TestAcquireForConnect:
    def test_happy_path_acquires_and_releases(self):
        with mc.acquire_for_connect(owner="t") as got:
            assert got is True
            assert mc.MESHCORE_CONNECTION_LOCK.locked()
        assert not mc.MESHCORE_CONNECTION_LOCK.locked()

    def test_refuses_when_persistent_owner_active(self):
        loop = MagicMock(spec=asyncio.AbstractEventLoop)
        mc.get_connection_manager().register_persistent(
            MagicMock(), loop, owner="incumbent",
        )
        with mc.acquire_for_connect(owner="newcomer") as got:
            assert got is False
        # Lock must NOT have been taken on the refusal path.
        assert not mc.MESHCORE_CONNECTION_LOCK.locked()

    def test_releases_lock_even_if_caller_raises(self):
        with pytest.raises(RuntimeError):
            with mc.acquire_for_connect(owner="t") as got:
                assert got is True
                raise RuntimeError("boom")
        assert not mc.MESHCORE_CONNECTION_LOCK.locked()

    def test_lock_timeout_returns_false(self):
        # Pre-acquire the lock so the next attempt times out.
        mc.MESHCORE_CONNECTION_LOCK.acquire()
        try:
            with mc.acquire_for_connect(owner="t", lock_timeout=0.05) as got:
                assert got is False
        finally:
            mc.MESHCORE_CONNECTION_LOCK.release()


class TestMeshCoreConnection:
    def test_acquires_lock_inside_context(self):
        with mc.MeshCoreConnection() as conn:
            assert conn is not None
            assert mc.MESHCORE_CONNECTION_LOCK.locked()
        assert not mc.MESHCORE_CONNECTION_LOCK.locked()

    def test_returns_none_when_persistent_owner(self):
        loop = MagicMock(spec=asyncio.AbstractEventLoop)
        mc.get_connection_manager().register_persistent(
            MagicMock(), loop, owner="bridge",
        )
        with mc.MeshCoreConnection() as conn:
            assert conn is None
        assert not mc.MESHCORE_CONNECTION_LOCK.locked()

    def test_can_bypass_persistent_check(self):
        """``respect_persistent=False`` is the escape hatch for an explicit
        admin override (e.g., 'force reset radio' from the TUI)."""
        loop = MagicMock(spec=asyncio.AbstractEventLoop)
        mc.get_connection_manager().register_persistent(
            MagicMock(), loop, owner="bridge",
        )
        with mc.MeshCoreConnection(respect_persistent=False) as conn:
            assert conn is not None
            assert mc.MESHCORE_CONNECTION_LOCK.locked()
        assert not mc.MESHCORE_CONNECTION_LOCK.locked()

    def test_returns_none_on_lock_timeout(self):
        mc.MESHCORE_CONNECTION_LOCK.acquire()
        try:
            with mc.MeshCoreConnection(lock_timeout=0.05) as conn:
                assert conn is None
        finally:
            mc.MESHCORE_CONNECTION_LOCK.release()

    def test_probe_outside_context_raises(self):
        conn = mc.MeshCoreConnection(device_path="/dev/ttyMeshCore")
        with pytest.raises(RuntimeError):
            conn.probe()


class TestRunInRadioLoop:
    def test_raises_when_no_owner(self):
        with pytest.raises(mc.ConnectionError):
            mc.get_connection_manager().run_in_radio_loop(
                asyncio.sleep(0), timeout=0.1,
            )

    def test_raises_when_loop_not_running(self):
        loop = MagicMock(spec=asyncio.AbstractEventLoop)
        loop.is_running.return_value = False
        mc.get_connection_manager().register_persistent(
            MagicMock(), loop, owner="t",
        )
        with pytest.raises(mc.ConnectionError):
            mc.get_connection_manager().run_in_radio_loop(
                asyncio.sleep(0), timeout=0.1,
            )

    def test_round_trips_value_through_real_loop(self):
        # Stand up a real event loop in a thread to verify the
        # run_coroutine_threadsafe handoff works end-to-end.
        loop = asyncio.new_event_loop()
        ready = threading.Event()

        def _run():
            asyncio.set_event_loop(loop)
            ready.set()
            loop.run_forever()

        t = threading.Thread(target=_run, daemon=True)
        t.start()
        ready.wait()
        try:
            mc.get_connection_manager().register_persistent(
                MagicMock(), loop, owner="t",
            )

            async def _add():
                return 41 + 1

            assert mc.get_connection_manager().run_in_radio_loop(_add(), timeout=2.0) == 42
        finally:
            loop.call_soon_threadsafe(loop.stop)
            t.join(timeout=2.0)
            loop.close()


class TestValidateMeshCoreDevice:
    def test_missing_device_returns_clean_error(self):
        result = mc.validate_meshcore_device("/dev/does-not-exist")
        assert result["exists"] is False
        assert "not found" in (result["error"] or "")

    def test_skips_probe_when_persistent_owner_active(self):
        loop = MagicMock(spec=asyncio.AbstractEventLoop)
        mc.get_connection_manager().register_persistent(
            MagicMock(), loop, owner="bridge",
        )
        # Even an existing device path should NOT actually open the port
        # while the persistent owner is registered.
        with patch("os.path.exists", return_value=True):
            result = mc.validate_meshcore_device("/dev/ttyMeshCore")
        assert result["exists"] is True
        assert result["readable"] is True
        assert result["responds"] is True
        assert "persistent owner" in result["error"]


class TestDetectMeshCoreDevices:
    def test_includes_persistent_symlink_first(self):
        with patch("os.path.exists", side_effect=lambda p: p == "/dev/ttyMeshCore"):
            with patch("glob.glob", return_value=[]):
                devices = mc.detect_meshcore_devices()
        assert devices == ["/dev/ttyMeshCore"]

    def test_falls_back_to_ttyusb_ttyacm(self):
        def fake_glob(pattern):
            if pattern == "/dev/ttyACM*":
                return ["/dev/ttyACM0"]
            return []
        with patch("os.path.exists", return_value=False):
            with patch("glob.glob", side_effect=fake_glob):
                devices = mc.detect_meshcore_devices()
        assert "/dev/ttyACM0" in devices
