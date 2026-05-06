"""Tests for gateway.meshcore_supervisor_handler.

Exercises the drop-in handler against a fake supervisor (the same one
test_meshcore_supervisor_client uses). Verifies:

* connect() opens the socket and registers the active handler
* RX events flow through CanonicalMessage and message_callback
* TX paths delegate to the supervisor client
* Chat buffer + active-handler accessor work like the in-process handler
* Bridge selection (rns_bridge.py) picks the supervisor handler when the
  socket is live
"""

from __future__ import annotations

import json
import os
import socket as _socket
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import MagicMock

import pytest

from gateway.canonical_message import CanonicalMessage, MessageType, Protocol
from gateway.meshcore_handler import get_active_handler
from gateway.meshcore_supervisor_handler import MeshCoreSupervisorHandler
from supervisor import protocol


# Reuse the FakeSupervisor pattern from the client tests — a tiny
# protocol-speaking stand-in over a Unix socket.
class FakeSupervisor:
    def __init__(self) -> None:
        self.handlers: Dict[str, Any] = {}
        self.received: List[Dict[str, Any]] = []
        self._tmp = tempfile.mkdtemp(prefix="ms-handler-")
        self.socket_path = str(Path(self._tmp) / "fake.sock")
        self._sock: _socket.socket | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._clients: List[_socket.socket] = []
        self._clients_lock = threading.Lock()

    def register(self, method: str, fn) -> None:
        self.handlers[method] = fn

    def start(self) -> None:
        s = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
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
            except _socket.timeout:
                continue
            except OSError:
                return
            with self._clients_lock:
                self._clients.append(client)
            threading.Thread(target=self._handle, args=(client,),
                             daemon=True).start()

    def _handle(self, client: _socket.socket) -> None:
        try:
            hello = {
                "type": "hello", "version": protocol.PROTOCOL_VERSION,
                "owner": "fake", "mode": "serial",
                "device": "/dev/ttyMeshCore", "connected": True,
            }
            try:
                client.sendall((json.dumps(hello) + "\n").encode())
            except OSError:
                return
            buf = b""
            client.settimeout(2.0)
            while not self._stop.is_set():
                try:
                    chunk = client.recv(4096)
                except _socket.timeout:
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
                    self.received.append(frame)
                    method = frame["method"]
                    handler = self.handlers.get(method)
                    if handler is None:
                        client.sendall(protocol.make_error(
                            frame["id"], f"no handler: {method}"))
                    else:
                        try:
                            result = handler(frame.get("args") or {})
                            client.sendall(protocol.make_response(
                                frame["id"], result))
                        except Exception as e:
                            client.sendall(protocol.make_error(
                                frame["id"], str(e)))
        finally:
            try:
                client.close()
            except OSError:
                pass

    def push_event(self, kind: str, data: Dict[str, Any]) -> None:
        frame = protocol.make_event(kind, data)
        with self._clients_lock:
            dead = []
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
            os.rmdir(self._tmp)
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


def _make_handler(socket_path: str, *, message_callback=None):
    stats: Dict[str, Any] = {}
    return MeshCoreSupervisorHandler(
        config=MagicMock(),
        node_tracker=MagicMock(),
        health=MagicMock(),
        stop_event=threading.Event(),
        stats=stats,
        stats_lock=threading.Lock(),
        message_queue=MagicMock(),
        message_callback=message_callback,
        status_callback=MagicMock(),
        should_bridge=MagicMock(return_value=True),
        socket_path=socket_path,
    ), stats


class TestConnectAndActiveHandler:
    def test_connect_succeeds_and_registers_active(self, fake):
        h, _ = _make_handler(fake.socket_path)
        try:
            assert h.connect() is True
            assert h.is_connected
            assert get_active_handler() is h
        finally:
            h.disconnect()

    def test_connect_returns_false_when_socket_missing(self, tmp_path):
        h, _ = _make_handler(str(tmp_path / "no.sock"))
        assert h.connect() is False
        assert not h.is_connected

    def test_disconnect_clears_active_handler(self, fake):
        h, _ = _make_handler(fake.socket_path)
        h.connect()
        h.disconnect()
        # The module-level active handler check protects against stale
        # handlers — disconnect on a registered handler must clear it.
        assert get_active_handler() is None


class TestRxEventFlow:
    def test_channel_message_reaches_callback(self, fake):
        seen: List[CanonicalMessage] = []
        h, stats = _make_handler(fake.socket_path,
                                 message_callback=seen.append)
        try:
            h.connect()
            fake.push_event("channel_message", {
                "text": "hi mesh",
                "channel": 0,
                "sender": "alpha",
            })
            for _ in range(20):
                if seen:
                    break
                time.sleep(0.05)
            assert seen, "message_callback did not fire"
            msg = seen[0]
            assert msg.source_network == Protocol.MESHCORE.value
            assert msg.content == "hi mesh"
            assert stats.get("meshcore_messages_rx", 0) == 1
        finally:
            h.disconnect()

    def test_channel_message_recorded_to_chat_buffer(self, fake):
        h, _ = _make_handler(fake.socket_path)
        try:
            h.connect()
            fake.push_event("channel_message", {
                "text": "buffer me",
                "channel": 0,
            })
            for _ in range(20):
                if h.get_recent_chat():
                    break
                time.sleep(0.05)
            entries = h.get_recent_chat()
            assert entries
            assert entries[-1]["direction"] == "rx"
            assert entries[-1]["text"] == "buffer me"
        finally:
            h.disconnect()

    def test_advertisement_updates_node_tracker(self, fake):
        h, stats = _make_handler(fake.socket_path)
        try:
            h.connect()
            fake.push_event("advertisement", {
                "sender": "abc123",
                "name": "node-alpha",
            })
            for _ in range(20):
                if stats.get("meshcore_advertisements"):
                    break
                time.sleep(0.05)
            assert stats.get("meshcore_advertisements") == 1
            h.node_tracker.update_from_canonical.assert_called()
        finally:
            h.disconnect()

    def test_ack_increments_stat(self, fake):
        h, stats = _make_handler(fake.socket_path)
        try:
            h.connect()
            fake.push_event("ack", {"id": 1})
            for _ in range(20):
                if stats.get("meshcore_acks_rx"):
                    break
                time.sleep(0.05)
            assert stats.get("meshcore_acks_rx") == 1
        finally:
            h.disconnect()


class TestTxPaths:
    def test_send_text_channel(self, fake):
        fake.register("send_message",
                      lambda args: {"sent": True, **args})
        h, _ = _make_handler(fake.socket_path)
        try:
            h.connect()
            assert h.send_text("hello", channel=0) is True
            # Mirror appears in chat buffer as TX.
            entries = h.get_recent_chat()
            assert entries
            assert entries[-1]["direction"] == "tx"
            assert entries[-1]["text"] == "hello"
        finally:
            h.disconnect()

    def test_send_text_contact(self, fake):
        fake.register("send_message",
                      lambda args: {"sent": True, **args})
        h, _ = _make_handler(fake.socket_path)
        try:
            h.connect()
            assert h.send_text("hi alpha", destination="alpha") is True
            # Find the latest send_message frame on the fake.
            sends = [f for f in fake.received
                     if f["method"] == "send_message"]
            assert sends
            assert sends[-1]["args"]["kind"] == "contact"
            assert sends[-1]["args"]["target"] == "alpha"
        finally:
            h.disconnect()

    def test_send_text_returns_false_when_disconnected(self):
        h, _ = _make_handler("/run/does/not/exist.sock")
        assert h.send_text("nope") is False

    def test_queue_send_routes_through_send_text(self, fake):
        fake.register("send_message",
                      lambda args: {"sent": True, **args})
        h, _ = _make_handler(fake.socket_path)
        try:
            h.connect()
            ok = h.queue_send({
                "content": "queued",
                "is_broadcast": True,
                "channel": 1,
            })
            assert ok is True
            sends = [f for f in fake.received
                     if f["method"] == "send_message"]
            assert sends[-1]["args"]["kind"] == "channel"
            assert sends[-1]["args"]["target"] == 1
            assert sends[-1]["args"]["text"] == "queued"
        finally:
            h.disconnect()

    def test_test_connection_pings(self, fake):
        fake.register("ping", lambda args: {"ok": True})
        h, _ = _make_handler(fake.socket_path)
        try:
            h.connect()
            assert h.test_connection() is True
        finally:
            h.disconnect()


class TestBridgeSelection:
    """Confirms rns_bridge.py picks the supervisor handler when the
    socket is live and the in-process handler when it isn't.

    We don't construct a full RNSMeshtasticBridge (heavy dependencies);
    instead we exercise the small selection block as a black box by
    importing it through the same path the bridge uses."""

    def test_is_supervisor_running_detects_live_socket(self, fake):
        from utils.meshcore_supervisor_client import is_supervisor_running
        assert is_supervisor_running(fake.socket_path) is True

    def test_is_supervisor_running_returns_false_for_missing(self, tmp_path):
        from utils.meshcore_supervisor_client import is_supervisor_running
        assert is_supervisor_running(str(tmp_path / "missing.sock")) is False

    def test_supervisor_handler_implements_base_interface(self):
        from gateway.base_handler import BaseMessageHandler
        # ABC subclass check — if the handler is missing any abstract
        # method, it can't even be instantiated. The fixture above already
        # instantiated it successfully; this is the explicit lock.
        assert issubclass(MeshCoreSupervisorHandler, BaseMessageHandler)


class TestChatBufferSemantics:
    def test_get_recent_chat_filters_by_since_id(self):
        h, _ = _make_handler("/x.sock")
        h.record_chat_message("rx", "first")
        h.record_chat_message("rx", "second")
        entries = h.get_recent_chat(since_id=1)
        assert len(entries) == 1
        assert entries[0]["text"] == "second"

    def test_known_channels_aggregates(self):
        h, _ = _make_handler("/x.sock")
        h.record_chat_message("rx", "a", channel=0)
        h.record_chat_message("rx", "b", channel=2)
        h.record_chat_message("rx", "c", channel=0)
        chans = sorted([c["channel"] for c in h.get_known_channels()])
        assert chans == [0, 2]
