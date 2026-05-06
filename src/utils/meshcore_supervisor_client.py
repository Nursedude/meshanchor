"""Client SDK for the MeshCore radio supervisor.

The supervisor (``src/supervisor/meshcore_radio.py``) owns the radio
session. Other MeshAnchor processes (gateway bridge, TUI, CLI) talk
to it via the Unix socket protocol in ``supervisor/protocol.py``.

This module is the **sync-friendly** facade those processes use. A
single background thread owns the socket; callers issue requests with
:py:meth:`call`, register event handlers with :py:meth:`on_event`,
and tear down with :py:meth:`close`.

Why sync (not async): the gateway bridge handler today is
thread-based, and MeshAnchor's TUI is whiptail-driven. An
``asyncio.run_coroutine_threadsafe`` boundary on every call would
double the moving parts. The supervisor itself is async; clients
don't need to be.
"""

from __future__ import annotations

import errno
import json
import logging
import os
import socket
import threading
import time
from queue import Empty, Queue
from typing import Any, Callable, Dict, List, Optional

from supervisor import protocol

logger = logging.getLogger(__name__)


class SupervisorUnavailable(Exception):
    """Supervisor socket is missing, refused, or returned a hello mismatch."""


class SupervisorTimeout(Exception):
    """A request didn't get a reply within the timeout."""


class SupervisorRemoteError(Exception):
    """The supervisor replied with an error frame."""


def is_supervisor_running(
    socket_path: str = protocol.DEFAULT_SOCKET_PATH,
    *,
    timeout: float = 0.5,
) -> bool:
    """Quick liveness probe. Open + close the socket; don't issue a request.

    Used by the bridge daemon to decide whether to instantiate the
    supervisor-backed handler or fall back to the in-process one.
    """
    if not os.path.exists(socket_path):
        return False
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            s.connect(socket_path)
            return True
    except (OSError, socket.timeout):
        return False


class MeshCoreSupervisorClient:
    """Sync client for the supervisor's Unix socket.

    Lifecycle::

        client = MeshCoreSupervisorClient()
        client.connect()                          # raises if socket dead
        client.on_event("channel_message", cb)    # register handler
        info = client.call("get_radio_info")      # blocking RPC
        client.send_message_channel(0, "hello")
        client.close()
    """

    def __init__(
        self,
        socket_path: str = protocol.DEFAULT_SOCKET_PATH,
        *,
        connect_timeout_s: float = 2.0,
        request_timeout_s: float = 10.0,
    ) -> None:
        self.socket_path = socket_path
        self.connect_timeout_s = connect_timeout_s
        self.request_timeout_s = request_timeout_s

        self._sock: Optional[socket.socket] = None
        self._reader_thread: Optional[threading.Thread] = None
        self._stop = threading.Event()

        self._next_id = 1
        self._id_lock = threading.Lock()
        self._pending: Dict[int, Queue[Dict[str, Any]]] = {}
        self._pending_lock = threading.Lock()

        self._event_handlers: Dict[str, List[Callable[[Dict[str, Any]], None]]] = {}
        self._handlers_lock = threading.Lock()

        self._hello: Optional[Dict[str, Any]] = None
        self._connected = False

    # ------------------------------------------------------------------
    # connect / close
    # ------------------------------------------------------------------

    def connect(self) -> Dict[str, Any]:
        """Open the socket, read the hello, start the reader thread.

        Returns the hello dict so the caller can log who owns the radio
        and what mode it's in.
        """
        if not os.path.exists(self.socket_path):
            raise SupervisorUnavailable(
                f"socket {self.socket_path} does not exist — is "
                "meshcore-radio.service running?"
            )
        try:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.settimeout(self.connect_timeout_s)
            sock.connect(self.socket_path)
        except OSError as e:
            raise SupervisorUnavailable(
                f"cannot connect to {self.socket_path}: {e}"
            ) from e

        # Read the hello frame inline; reader thread takes over after.
        sock.settimeout(self.connect_timeout_s)
        hello_line = self._readline_blocking(sock)
        try:
            hello = protocol.decode(hello_line)
        except protocol.ProtocolError as e:
            sock.close()
            raise SupervisorUnavailable(f"bad hello frame: {e}") from e
        if hello.get("type") != "hello":
            sock.close()
            raise SupervisorUnavailable(
                f"expected hello, got {hello.get('type')}"
            )
        version = hello.get("version", 0)
        if version != protocol.PROTOCOL_VERSION:
            sock.close()
            raise SupervisorUnavailable(
                f"protocol version mismatch: client={protocol.PROTOCOL_VERSION} "
                f"supervisor={version}"
            )

        sock.settimeout(None)  # blocking — reader thread handles framing
        self._sock = sock
        self._hello = hello
        self._connected = True
        self._stop.clear()
        self._reader_thread = threading.Thread(
            target=self._reader_loop,
            name="meshcore-supervisor-reader",
            daemon=True,
        )
        self._reader_thread.start()

        logger.info("Supervisor: connected (owner=%s mode=%s device=%s)",
                    hello.get("owner"), hello.get("mode"),
                    hello.get("device"))
        return hello

    def close(self) -> None:
        if not self._connected:
            return
        self._connected = False
        self._stop.set()
        try:
            if self._sock is not None:
                self._sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            if self._sock is not None:
                self._sock.close()
        except OSError:
            pass
        self._sock = None
        if self._reader_thread is not None:
            self._reader_thread.join(timeout=2.0)
        # Wake any callers blocked on call() — let them see the disconnect.
        with self._pending_lock:
            for q in self._pending.values():
                q.put({"type": "error", "error": "client closed"})
            self._pending.clear()

    @property
    def hello(self) -> Optional[Dict[str, Any]]:
        return self._hello

    @property
    def connected(self) -> bool:
        return self._connected

    # ------------------------------------------------------------------
    # request / response
    # ------------------------------------------------------------------

    def call(
        self,
        method: str,
        *,
        timeout: Optional[float] = None,
        **args: Any,
    ) -> Any:
        """Issue one request, block for the response.

        Raises :py:class:`SupervisorTimeout` if no reply within the
        timeout, :py:class:`SupervisorRemoteError` if the supervisor
        replies with an error.
        """
        if not self._connected or self._sock is None:
            raise SupervisorUnavailable("client not connected")
        with self._id_lock:
            req_id = self._next_id
            self._next_id += 1
        q: Queue[Dict[str, Any]] = Queue(maxsize=1)
        with self._pending_lock:
            self._pending[req_id] = q

        try:
            frame = protocol.make_request(req_id, method, **args)
        except protocol.ProtocolError as e:
            with self._pending_lock:
                self._pending.pop(req_id, None)
            raise ValueError(str(e)) from e

        try:
            self._sock.sendall(frame)
        except OSError as e:
            with self._pending_lock:
                self._pending.pop(req_id, None)
            raise SupervisorUnavailable(f"socket send failed: {e}") from e

        wait = timeout if timeout is not None else self.request_timeout_s
        try:
            reply = q.get(timeout=wait)
        except Empty:
            with self._pending_lock:
                self._pending.pop(req_id, None)
            raise SupervisorTimeout(
                f"no reply for {method} within {wait:.1f}s"
            )
        finally:
            with self._pending_lock:
                self._pending.pop(req_id, None)

        if reply.get("type") == "error":
            raise SupervisorRemoteError(reply.get("error", "unknown error"))
        return reply.get("result")

    # convenience wrappers ------------------------------------------------

    def status(self) -> Dict[str, Any]:
        return self.call("status")

    def get_radio_info(self) -> Any:
        return self.call("get_radio_info")

    def get_contacts(self) -> Any:
        return self.call("get_contacts")

    def get_channels(self) -> Any:
        return self.call("get_channels")

    def send_message_channel(self, channel: int, text: str) -> Any:
        return self.call("send_message", kind="channel",
                         target=channel, text=text)

    def send_message_contact(self, target: Any, text: str) -> Any:
        return self.call("send_message", kind="contact",
                         target=target, text=text)

    def ping(self) -> Any:
        return self.call("ping", timeout=2.0)

    # ------------------------------------------------------------------
    # event subscription
    # ------------------------------------------------------------------

    def on_event(
        self,
        kind: str,
        handler: Callable[[Dict[str, Any]], None],
    ) -> None:
        """Register a callback for a given event kind. Handlers run on
        the reader thread — keep them quick and don't block."""
        if kind not in protocol.EVENT_KINDS:
            raise ValueError(f"unknown event kind: {kind}")
        with self._handlers_lock:
            self._event_handlers.setdefault(kind, []).append(handler)

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    def _readline_blocking(self, sock: socket.socket) -> bytes:
        """Read until '\\n' or EOF. Used pre-thread for the hello frame."""
        chunks: List[bytes] = []
        while True:
            try:
                chunk = sock.recv(1)
            except socket.timeout as e:
                raise SupervisorUnavailable("timed out reading hello") from e
            if not chunk:
                raise SupervisorUnavailable("connection closed before hello")
            if chunk == b"\n":
                return b"".join(chunks)
            chunks.append(chunk)
            if len(chunks) > 64 * 1024:
                raise SupervisorUnavailable("hello too large")

    def _reader_loop(self) -> None:
        """Drain frames from the socket and dispatch them."""
        sock = self._sock
        if sock is None:
            return
        buf = b""
        try:
            while not self._stop.is_set():
                try:
                    chunk = sock.recv(4096)
                except OSError as e:
                    if e.errno in (errno.EBADF, errno.ENOTCONN):
                        return
                    logger.debug("Reader recv error: %s", e)
                    return
                if not chunk:
                    return
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    if not line.strip():
                        continue
                    self._dispatch_line(line)
        finally:
            self._connected = False
            with self._pending_lock:
                for q in self._pending.values():
                    try:
                        q.put_nowait({"type": "error",
                                      "error": "supervisor disconnected"})
                    except Exception:
                        pass
                self._pending.clear()
            logger.info("Supervisor reader thread exiting")

    def _dispatch_line(self, line: bytes) -> None:
        try:
            frame = protocol.decode(line)
        except protocol.ProtocolError as e:
            logger.warning("Bad frame from supervisor: %s", e)
            return
        ftype = frame.get("type")
        if ftype in ("response", "error"):
            req_id = frame.get("id")
            if not isinstance(req_id, int):
                return
            with self._pending_lock:
                q = self._pending.get(req_id)
            if q is not None:
                try:
                    q.put_nowait(frame)
                except Exception:
                    pass
            return
        if ftype == "event":
            self._fire_event(frame)
            return
        logger.debug("Unhandled frame type: %s", ftype)

    def _fire_event(self, frame: Dict[str, Any]) -> None:
        kind = frame.get("event")
        data = frame.get("data") or {}
        if not isinstance(kind, str):
            return
        with self._handlers_lock:
            handlers = list(self._event_handlers.get(kind, []))
        for handler in handlers:
            try:
                handler(data)
            except Exception as e:
                logger.warning("Event handler for %s raised: %s", kind, e)
