"""End-to-end tests for the supervisor process.

Stands up a real ``MeshCoreRadioSupervisor`` with a mock meshcore_py
in place, talks to it via a real ``MeshCoreSupervisorClient`` over a
real Unix socket. Verifies the bring-up sequence, RPC dispatch,
event broadcast, and the lock+register handshake into
``utils.meshcore_connection``.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List
from unittest.mock import MagicMock, patch

import pytest

from supervisor import meshcore_radio
from utils import meshcore_connection
from utils.meshcore_supervisor_client import MeshCoreSupervisorClient


class MockEventType:
    CONTACT_MSG_RECV = "CONTACT_MSG_RECV"
    CHANNEL_MSG_RECV = "CHANNEL_MSG_RECV"
    ADVERTISEMENT = "ADVERTISEMENT"
    ACK = "ACK"


class MockMeshCore:
    """Tiny stand-in for the real meshcore_py.MeshCore. Lets the
    supervisor's connect path complete and exposes the commands the
    supervisor invokes in response to client RPCs."""

    def __init__(self) -> None:
        self.commands = MagicMock()
        self.commands.get_radio_info = self._async_return({"node_num": 9999})
        self.commands.get_contacts = self._async_return([
            {"adv_name": "alpha", "public_key": b"\x01\x02"},
        ])
        self.commands.get_channels = self._async_return(
            [{"index": 0, "name": "Public"}]
        )
        self.commands.send_chan_msg = self._async_return(True)
        self.commands.send_msg = self._async_return(True)
        self._subscriptions: Dict[Any, List[Callable]] = {}

    @staticmethod
    def _async_return(value: Any):
        async def _coro(*args, **kwargs):
            return value
        return MagicMock(side_effect=_coro)

    def subscribe(self, evt: Any, callback: Callable) -> None:
        self._subscriptions.setdefault(evt, []).append(callback)

    async def start_auto_message_fetching(self) -> None:
        return None

    async def disconnect(self) -> None:
        return None

    async def emit(self, evt: Any, payload: Any) -> None:
        for cb in self._subscriptions.get(evt, []):
            await cb(payload)


@pytest.fixture(autouse=True)
def _isolate_connection_manager():
    meshcore_connection.reset_connection_manager()
    while meshcore_connection.MESHCORE_CONNECTION_LOCK.locked():
        try:
            meshcore_connection.MESHCORE_CONNECTION_LOCK.release()
        except RuntimeError:
            break
    yield
    meshcore_connection.reset_connection_manager()
    while meshcore_connection.MESHCORE_CONNECTION_LOCK.locked():
        try:
            meshcore_connection.MESHCORE_CONNECTION_LOCK.release()
        except RuntimeError:
            break


@pytest.fixture
def supervisor_under_test():
    """Run the supervisor in a thread with a mock meshcore module.

    Yields ``(supervisor, socket_path, mock_meshcore)``.
    """
    tmpdir = tempfile.mkdtemp(prefix="meshcore-sup-test-")
    socket_path = str(Path(tmpdir) / "sup.sock")
    mock_meshcore = MockMeshCore()

    mock_module = MagicMock()
    mock_module.MeshCore.create_serial = MagicMock(
        side_effect=lambda *a, **kw: _make_async_value(mock_meshcore)
    )
    mock_module.EventType = MockEventType

    sup = meshcore_radio.MeshCoreRadioSupervisor(
        socket_path=socket_path,
        device_path="/dev/ttyMeshCore",
        connection_type="serial",
        socket_mode=0o600,
        health_probe_interval_s=10.0,
    )

    started = threading.Event()
    finished = threading.Event()
    runner_loop_box: List[asyncio.AbstractEventLoop] = []
    exit_code_box: List[int] = []

    def _runner():
        loop = asyncio.new_event_loop()
        runner_loop_box.append(loop)
        asyncio.set_event_loop(loop)
        with patch.object(meshcore_radio, "_meshcore_mod", mock_module), \
             patch.object(meshcore_radio, "_HAS_MESHCORE", True):
            started.set()
            try:
                exit_code_box.append(loop.run_until_complete(sup.run()))
            finally:
                loop.close()
                finished.set()

    t = threading.Thread(target=_runner, daemon=True)
    t.start()
    started.wait(timeout=2.0)

    # Wait for the socket to bind + the persistent owner to register.
    deadline = time.time() + 5.0
    while time.time() < deadline:
        if (os.path.exists(socket_path)
                and meshcore_connection.get_connection_manager().has_persistent()):
            break
        time.sleep(0.05)
    else:
        pytest.fail("supervisor did not register persistent owner in time")

    try:
        yield sup, socket_path, mock_meshcore
    finally:
        # Tear down: signal stop on the supervisor's loop.
        if runner_loop_box:
            loop = runner_loop_box[0]
            loop.call_soon_threadsafe(sup.request_stop)
        finished.wait(timeout=5.0)
        try:
            os.unlink(socket_path)
        except FileNotFoundError:
            pass
        try:
            os.rmdir(tmpdir)
        except OSError:
            pass


def _make_async_value(value: Any):
    """Tiny helper: returns a coroutine that yields ``value``."""
    async def _coro():
        return value
    return _coro()


class TestSupervisorBringup:
    def test_persistent_owner_registered(self, supervisor_under_test):
        _, _, _ = supervisor_under_test
        mgr = meshcore_connection.get_connection_manager()
        assert mgr.has_persistent()
        assert mgr.get_persistent_owner() == "meshcore-radio"
        assert mgr.get_mode() is meshcore_connection.ConnectionMode.SERIAL
        assert mgr.get_device() == "/dev/ttyMeshCore"

    def test_socket_file_mode_is_locked_down(self, supervisor_under_test):
        _, socket_path, _ = supervisor_under_test
        mode = os.stat(socket_path).st_mode & 0o777
        # Constructor was called with socket_mode=0o600 — verify the
        # supervisor honored it.
        assert mode == 0o600


class TestRpcDispatch:
    def _client(self, socket_path: str) -> MeshCoreSupervisorClient:
        c = MeshCoreSupervisorClient(socket_path, request_timeout_s=3.0)
        c.connect()
        return c

    def test_status_includes_connection_state(self, supervisor_under_test):
        _, socket_path, _ = supervisor_under_test
        c = self._client(socket_path)
        try:
            status = c.status()
            assert status["connected"] is True
            assert status["owner"] == "meshcore-radio"
            assert status["mode"] == "serial"
            assert status["device"] == "/dev/ttyMeshCore"
            assert status["clients"] >= 1
        finally:
            c.close()

    def test_ping_does_not_touch_radio(self, supervisor_under_test):
        sup, socket_path, mock = supervisor_under_test
        # Make get_contacts blow up; ping should still work because it
        # doesn't go through the radio.
        async def _boom():
            raise RuntimeError("radio busy")
        mock.commands.get_contacts.side_effect = lambda *a, **kw: _boom()
        c = self._client(socket_path)
        try:
            assert c.ping()["ok"] is True
        finally:
            c.close()

    def test_get_radio_info_round_trips(self, supervisor_under_test):
        _, socket_path, _ = supervisor_under_test
        c = self._client(socket_path)
        try:
            info = c.get_radio_info()
            assert info["node_num"] == 9999
        finally:
            c.close()

    def test_send_message_channel_calls_command(self, supervisor_under_test):
        _, socket_path, mock = supervisor_under_test
        c = self._client(socket_path)
        try:
            result = c.send_message_channel(0, "supervisor test")
            assert result["sent"] is True
            mock.commands.send_chan_msg.assert_called()
        finally:
            c.close()

    def test_unknown_method_returns_error(self, supervisor_under_test):
        _, socket_path, _ = supervisor_under_test
        # Bypass the client's protocol guard to send a bad method.
        import socket as sk
        s = sk.socket(sk.AF_UNIX, sk.SOCK_STREAM)
        s.settimeout(2.0)
        s.connect(socket_path)
        # drain hello
        buf = b""
        while b"\n" not in buf:
            buf += s.recv(1024)
        line, _ = buf.split(b"\n", 1)
        assert b"hello" in line
        s.sendall(b'{"type":"request","id":1,"method":"definitely_bad",'
                  b'"args":{}}\n')
        reply = b""
        while b"\n" not in reply:
            chunk = s.recv(1024)
            if not chunk:
                break
            reply += chunk
        s.close()
        assert b"unknown method" in reply.lower() or b"error" in reply.lower()


class TestEventBroadcast:
    def test_radio_event_reaches_client(self, supervisor_under_test):
        sup, socket_path, mock = supervisor_under_test
        c = MeshCoreSupervisorClient(socket_path)
        received: List[Dict[str, Any]] = []
        c.on_event("channel_message", received.append)
        try:
            c.connect()
            # Re-subscribe via the supervisor's loop by emitting an event.
            sup_loop = sup._meshcore  # type: ignore[attr-defined]
            # The supervisor subscribed to MockEventType.CHANNEL_MSG_RECV;
            # fire that callback directly through the mock.
            for cb in mock._subscriptions.get(
                MockEventType.CHANNEL_MSG_RECV, []
            ):
                # cb is async and lives on the supervisor's loop
                fut = asyncio.run_coroutine_threadsafe(
                    cb({"text": "hi", "channel": 0}),
                    sup_loop._loop if hasattr(sup_loop, "_loop") else _find_loop(sup),
                )
                fut.result(timeout=2.0)
            for _ in range(20):
                if received:
                    break
                time.sleep(0.05)
            assert received
            assert received[0]["text"] == "hi"
        finally:
            c.close()


def _find_loop(sup) -> asyncio.AbstractEventLoop:
    """Pull the supervisor's loop out for run_coroutine_threadsafe.

    The supervisor doesn't expose its loop publicly — instead we go
    through ``meshcore_connection.get_connection_manager().get_loop()``,
    which the supervisor populates via register_persistent.
    """
    loop = meshcore_connection.get_connection_manager().get_loop()
    assert loop is not None
    return loop
