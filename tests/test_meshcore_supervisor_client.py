"""Tests for utils.meshcore_supervisor_client.

Spins up a tiny stand-in supervisor (an ad-hoc Unix-socket server
that speaks the protocol) so we can exercise the client without the
real radio supervisor.
"""

from __future__ import annotations

import json
import os
import socket
import socketserver
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Dict, List

import pytest

from supervisor import protocol
from utils.meshcore_supervisor_client import (
    MeshCoreSupervisorClient,
    SupervisorRemoteError,
    SupervisorTimeout,
    SupervisorUnavailable,
    is_supervisor_running,
)


class FakeSupervisor:
    """Minimal protocol-speaking stand-in. Runs in a thread."""

    def __init__(
        self,
        *,
        owner: str = "test-owner",
        mode: str = "serial",
        device: str = "/dev/ttyMeshCore",
        connected: bool = True,
        version: int = protocol.PROTOCOL_VERSION,
        delay_hello_s: float = 0.0,
    ) -> None:
        self.owner = owner
        self.mode = mode
        self.device = device
        self.connected = connected
        self.version = version
        self.delay_hello_s = delay_hello_s
        self.handlers: Dict[str, Any] = {}
        self.received_requests: List[Dict[str, Any]] = []
        self._tmpdir = tempfile.mkdtemp(prefix="meshcore-fake-")
        self.socket_path = str(Path(self._tmpdir) / "fake.sock")
        self._sock: socket.socket | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._clients: List[socket.socket] = []
        self._clients_lock = threading.Lock()

    def register(self, method: str, fn) -> None:
        self.handlers[method] = fn

    def start(self) -> None:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.bind(self.socket_path)
        s.listen(4)
        s.settimeout(0.2)
        self._sock = s
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        assert self._sock is not None
        while not self._stop.is_set():
            try:
                client, _ = self._sock.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            with self._clients_lock:
                self._clients.append(client)
            threading.Thread(
                target=self._handle, args=(client,), daemon=True,
            ).start()

    def _handle(self, client: socket.socket) -> None:
        try:
            if self.delay_hello_s > 0:
                time.sleep(self.delay_hello_s)
            hello = {
                "type": "hello", "version": self.version,
                "owner": self.owner, "mode": self.mode,
                "device": self.device, "connected": self.connected,
            }
            try:
                client.sendall((json.dumps(hello) + "\n").encode())
            except (BrokenPipeError, ConnectionResetError, OSError):
                return  # client disconnected before hello — fine in tests
            buf = b""
            client.settimeout(2.0)
            while not self._stop.is_set():
                try:
                    chunk = client.recv(4096)
                except socket.timeout:
                    continue
                except OSError:
                    return
                if not chunk:
                    return
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    if not line.strip():
                        continue
                    try:
                        frame = protocol.decode(line)
                    except protocol.ProtocolError:
                        continue
                    if frame.get("type") != "request":
                        continue
                    self.received_requests.append(frame)
                    self._respond(client, frame)
        finally:
            try:
                client.close()
            except OSError:
                pass

    def _respond(self, client: socket.socket, req: Dict[str, Any]) -> None:
        req_id = req["id"]
        method = req["method"]
        args = req.get("args") or {}
        handler = self.handlers.get(method)
        if handler is None:
            client.sendall(protocol.make_error(req_id, f"no handler: {method}"))
            return
        try:
            result = handler(args)
            client.sendall(protocol.make_response(req_id, result))
        except Exception as e:
            client.sendall(protocol.make_error(req_id, str(e)))

    def push_event(self, kind: str, data: Dict[str, Any]) -> None:
        frame = protocol.make_event(kind, data)
        with self._clients_lock:
            dead: List[socket.socket] = []
            for c in self._clients:
                try:
                    c.sendall(frame)
                except OSError:
                    dead.append(c)
            for c in dead:
                self._clients.remove(c)

    def stop(self) -> None:
        self._stop.set()
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        with self._clients_lock:
            for c in self._clients:
                try:
                    c.close()
                except OSError:
                    pass
            self._clients.clear()
        try:
            os.unlink(self.socket_path)
        except FileNotFoundError:
            pass
        try:
            os.rmdir(self._tmpdir)
        except OSError:
            pass


@pytest.fixture
def fake():
    f = FakeSupervisor()
    f.start()
    try:
        yield f
    finally:
        f.stop()


class TestProbe:
    def test_missing_socket_returns_false(self, tmp_path):
        assert is_supervisor_running(str(tmp_path / "no.sock")) is False

    def test_live_socket_returns_true(self, fake):
        assert is_supervisor_running(fake.socket_path) is True


class TestConnectAndHello:
    def test_connect_reads_hello(self, fake):
        c = MeshCoreSupervisorClient(fake.socket_path)
        try:
            hello = c.connect()
            assert hello["owner"] == "test-owner"
            assert hello["device"] == "/dev/ttyMeshCore"
            assert c.connected
            assert c.hello == hello
        finally:
            c.close()

    def test_connect_rejects_version_mismatch(self):
        f = FakeSupervisor(version=protocol.PROTOCOL_VERSION + 1)
        f.start()
        try:
            c = MeshCoreSupervisorClient(f.socket_path)
            with pytest.raises(SupervisorUnavailable):
                c.connect()
        finally:
            f.stop()

    def test_connect_missing_socket_raises(self, tmp_path):
        c = MeshCoreSupervisorClient(str(tmp_path / "nope.sock"))
        with pytest.raises(SupervisorUnavailable):
            c.connect()


class TestRpc:
    def test_call_returns_result(self, fake):
        fake.register("status", lambda args: {"connected": True})
        c = MeshCoreSupervisorClient(fake.socket_path)
        try:
            c.connect()
            assert c.status() == {"connected": True}
        finally:
            c.close()

    def test_call_propagates_error(self, fake):
        def boom(_args):
            raise RuntimeError("nope")
        fake.register("get_contacts", boom)
        c = MeshCoreSupervisorClient(fake.socket_path)
        try:
            c.connect()
            with pytest.raises(SupervisorRemoteError, match="nope"):
                c.get_contacts()
        finally:
            c.close()

    def test_call_times_out(self, fake):
        # Handler that never replies — fake's _respond would normally
        # answer. Register one that sleeps past the timeout.
        def slow(_args):
            time.sleep(2.0)
            return None
        fake.register("ping", slow)
        c = MeshCoreSupervisorClient(fake.socket_path,
                                     request_timeout_s=0.3)
        try:
            c.connect()
            with pytest.raises(SupervisorTimeout):
                c.call("ping", timeout=0.3)
        finally:
            c.close()

    def test_send_message_channel_round_trip(self, fake):
        fake.register("send_message",
                      lambda args: {"sent": True, **args})
        c = MeshCoreSupervisorClient(fake.socket_path)
        try:
            c.connect()
            result = c.send_message_channel(0, "hello mesh")
            assert result["sent"] is True
            assert result["kind"] == "channel"
            assert result["target"] == 0
            assert result["text"] == "hello mesh"
        finally:
            c.close()

    def test_call_when_not_connected_raises(self, tmp_path):
        c = MeshCoreSupervisorClient(str(tmp_path / "x.sock"))
        with pytest.raises(SupervisorUnavailable):
            c.call("status")


class TestEvents:
    def test_event_handler_fires(self, fake):
        c = MeshCoreSupervisorClient(fake.socket_path)
        received: List[Dict[str, Any]] = []
        c.on_event("channel_message", received.append)
        try:
            c.connect()
            fake.push_event("channel_message",
                            {"text": "hi", "channel": 0})
            # Reader thread runs the callback; give it a moment.
            for _ in range(20):
                if received:
                    break
                time.sleep(0.05)
            assert received
            assert received[0]["text"] == "hi"
        finally:
            c.close()

    def test_unknown_event_kind_rejected_at_subscribe(self, fake):
        c = MeshCoreSupervisorClient(fake.socket_path)
        with pytest.raises(ValueError):
            c.on_event("not_a_kind", lambda d: None)

    def test_handler_exception_does_not_crash_reader(self, fake):
        c = MeshCoreSupervisorClient(fake.socket_path)
        c.on_event("ack", lambda d: (_ for _ in ()).throw(RuntimeError("oops")))
        survived: List[Dict[str, Any]] = []
        c.on_event("ack", survived.append)
        try:
            c.connect()
            fake.push_event("ack", {"id": 1})
            for _ in range(20):
                if survived:
                    break
                time.sleep(0.05)
            assert survived  # second handler still ran
        finally:
            c.close()


class TestCloseSemantics:
    def test_close_unblocks_pending_call(self, fake):
        # Handler that sleeps; we close mid-wait and expect the queue
        # entry to be drained with an error reply.
        def slow(_args):
            time.sleep(5.0)
            return None
        fake.register("ping", slow)
        c = MeshCoreSupervisorClient(fake.socket_path,
                                     request_timeout_s=10.0)
        c.connect()

        result_box: List[Any] = []
        err_box: List[Exception] = []

        def caller():
            try:
                result_box.append(c.call("ping", timeout=5.0))
            except Exception as e:
                err_box.append(e)

        t = threading.Thread(target=caller, daemon=True)
        t.start()
        time.sleep(0.2)  # let the request hit the wire
        c.close()
        t.join(timeout=3.0)
        assert not t.is_alive()
        assert err_box  # caller saw the close
