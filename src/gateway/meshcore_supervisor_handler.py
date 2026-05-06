"""Drop-in MeshCore handler that delegates to the supervisor.

When ``meshcore-radio.service`` is running, the supervisor process owns
the serial port — the bridge daemon must NOT open the radio itself or
it deadlocks on the OS-level exclusive open. This handler instead
talks to the supervisor over its Unix socket.

The class implements the same surface as
:py:class:`gateway.meshcore_handler.MeshCoreHandler` so the bridge,
the persistent queue, and the chat HTTP API don't notice the
difference:

* :py:class:`BaseMessageHandler` abstract API (``connect`` /
  ``disconnect`` / ``run_loop`` / ``send_text`` / ``queue_send`` /
  ``test_connection``).
* Chat-buffer API used by ``utils.config_api`` (``record_chat_message``
  / ``get_recent_chat`` / ``get_known_channels``).
* Active-handler registration via ``meshcore_handler._set_active_handler``
  so ``get_active_handler()`` returns this instance.

Selection happens in :py:mod:`gateway.rns_bridge`: if
``utils.meshcore_supervisor_client.is_supervisor_running()`` returns
True at bridge start, this handler is instantiated; otherwise the
in-process :py:class:`MeshCoreHandler` is used.

This handler is intentionally thin — RX events from the supervisor
arrive as JSON dicts, get wrapped in a ``meshcore_py``-shaped shim,
and flow through ``CanonicalMessage.from_meshcore`` exactly like the
in-process handler. All routing / dedup / health logic lives in the
bridge above us, unchanged.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from types import SimpleNamespace
from typing import Any, Callable, Deque, Dict, List, Optional

from .base_handler import BaseMessageHandler
from .canonical_message import CanonicalMessage, Protocol
from .meshcore_handler import _clear_active_handler, _set_active_handler
from utils.meshcore_supervisor_client import (
    MeshCoreSupervisorClient,
    SupervisorRemoteError,
    SupervisorTimeout,
    SupervisorUnavailable,
    is_supervisor_running,
)

logger = logging.getLogger(__name__)


# Map supervisor event kind → the meshcore_py event-type string that
# CanonicalMessage.from_meshcore matches on. The supervisor's protocol
# uses lowercase snake_case; from_meshcore uppercases and substring-checks.
_EVENT_TYPE_NAME = {
    "contact_message": "CONTACT_MSG_RECV",
    "channel_message": "CHANNEL_MSG_RECV",
    "advertisement": "ADVERTISEMENT",
    "ack": "ACK",
}

CHAT_BUFFER_MAX = 200


class MeshCoreSupervisorHandler(BaseMessageHandler):
    """BaseMessageHandler implementation backed by the radio supervisor."""

    def __init__(
        self,
        config: Any,
        node_tracker: Any,
        health: Any,
        stop_event: threading.Event,
        stats: Dict[str, Any],
        stats_lock: threading.Lock,
        message_queue: Any,
        message_callback: Optional[Callable] = None,
        status_callback: Optional[Callable] = None,
        should_bridge: Optional[Callable] = None,
        *,
        socket_path: Optional[str] = None,
    ) -> None:
        super().__init__(
            config=config,
            node_tracker=node_tracker,
            health=health,
            stop_event=stop_event,
            stats=stats,
            stats_lock=stats_lock,
            message_queue=message_queue,
            message_callback=message_callback,
            status_callback=status_callback,
            should_bridge=should_bridge,
        )
        self._socket_path = socket_path
        self._client: Optional[MeshCoreSupervisorClient] = None
        self._chat_buffer: Deque[Dict[str, Any]] = deque(maxlen=CHAT_BUFFER_MAX)
        self._chat_buffer_lock = threading.Lock()
        self._chat_seq = 0

    # ------------------------------------------------------------------
    # BaseMessageHandler abstract methods
    # ------------------------------------------------------------------

    def connect(self) -> bool:
        """Open the supervisor socket and subscribe to events."""
        if self._client is not None and self._client.connected:
            return True
        kwargs: Dict[str, Any] = {}
        if self._socket_path:
            kwargs["socket_path"] = self._socket_path
        client = MeshCoreSupervisorClient(**kwargs)
        try:
            hello = client.connect()
        except SupervisorUnavailable as e:
            logger.warning("Supervisor handler: connect failed: %s", e)
            return False

        client.on_event("contact_message", self._on_contact_event)
        client.on_event("channel_message", self._on_channel_event)
        client.on_event("advertisement", self._on_advertisement_event)
        client.on_event("ack", self._on_ack_event)
        client.on_event("connection_state", self._on_connection_state_event)

        self._client = client
        self._connected = True
        _set_active_handler(self)
        try:
            self.health.record_connection_event(
                "meshcore", "connected",
                f"via supervisor (owner={hello.get('owner')}, "
                f"device={hello.get('device')})"
            )
        except Exception:
            pass
        self._notify_status("meshcore_connected")
        logger.info(
            "MeshCore supervisor handler connected (owner=%s mode=%s device=%s)",
            hello.get("owner"), hello.get("mode"), hello.get("device"),
        )
        return True

    def run_loop(self) -> None:
        """Block until stop_event fires.

        All real work happens in the client's reader thread (events) and
        the bridge's persistent-queue thread (TX). This loop just owns the
        connect/disconnect lifecycle and re-attempts if the supervisor is
        not running yet at startup.
        """
        backoff = 1.0
        while not self._stop_event.is_set():
            if not self._connected:
                if self.connect():
                    backoff = 1.0
                else:
                    self._stop_event.wait(min(backoff, 30.0))
                    backoff = min(backoff * 2.0, 30.0)
                    continue
            # Connected — check the client's reader thread is alive; if it
            # exited (supervisor died), reconnect.
            client = self._client
            if client is None or not client.connected:
                logger.info("Supervisor handler: client disconnected, reconnecting")
                self._connected = False
                continue
            self._stop_event.wait(timeout=5.0)
        self.disconnect()

    def disconnect(self) -> None:
        if self._client is not None:
            try:
                self._client.close()
            except Exception as e:
                logger.debug("Supervisor handler close error: %s", e)
        self._client = None
        self._connected = False
        _clear_active_handler(self)
        self._notify_status("meshcore_disconnected")

    def send_text(
        self,
        message: str,
        destination: Optional[str] = None,
        channel: int = 0,
    ) -> bool:
        """TX a message. Returns True on success."""
        if not self._connected or self._client is None:
            return False
        text = self._truncate_if_needed(message)
        try:
            if destination:
                self._client.send_message_contact(destination, text)
            else:
                self._client.send_message_channel(int(channel or 0), text)
        except (SupervisorUnavailable, SupervisorTimeout,
                SupervisorRemoteError) as e:
            logger.warning("Supervisor TX failed: %s", e)
            return False
        # Mirror to chat buffer so the chat HTTP API shows the outbound.
        self.record_chat_message(
            direction="tx",
            text=text,
            channel=int(channel) if destination is None else None,
            destination=destination,
        )
        return True

    def queue_send(self, payload: Dict) -> bool:
        """Persistent-queue sender. Same shape as MeshCoreHandler.queue_send."""
        text = payload.get("content") or payload.get("text") or ""
        if not text:
            return False
        destination = payload.get("destination") or payload.get("destination_address")
        is_broadcast = payload.get("is_broadcast", destination is None)
        channel = payload.get("channel", 0)
        return self.send_text(
            text,
            destination=None if is_broadcast else destination,
            channel=int(channel or 0),
        )

    def test_connection(self) -> bool:
        if self._client is None:
            return is_supervisor_running(
                self._socket_path
                or _default_socket_path()
            )
        try:
            self._client.ping()
            return True
        except (SupervisorUnavailable, SupervisorTimeout,
                SupervisorRemoteError):
            return False

    # ------------------------------------------------------------------
    # Chat buffer API (same surface as MeshCoreHandler)
    # ------------------------------------------------------------------

    def record_chat_message(
        self,
        direction: str,
        text: str,
        channel: Optional[int] = None,
        sender: Optional[str] = None,
        destination: Optional[str] = None,
    ) -> None:
        with self._chat_buffer_lock:
            self._chat_seq += 1
            self._chat_buffer.append({
                "id": self._chat_seq,
                "ts": time.time(),
                "direction": direction,
                "channel": channel,
                "sender": sender,
                "destination": destination,
                "text": text,
            })

    def get_recent_chat(
        self,
        since_id: int = 0,
        limit: int = 200,
    ) -> List[Dict[str, Any]]:
        with self._chat_buffer_lock:
            out = [e for e in self._chat_buffer if e["id"] > since_id]
        return out[-limit:] if limit and len(out) > limit else out

    def get_known_channels(self) -> List[Dict[str, Any]]:
        with self._chat_buffer_lock:
            seen: Dict[int, float] = {}
            for entry in self._chat_buffer:
                ch = entry.get("channel")
                if ch is None:
                    continue
                seen[ch] = max(seen.get(ch, 0.0), entry["ts"])
        return [
            {"channel": ch, "last_seen": ts}
            for ch, ts in sorted(seen.items())
        ]

    # ------------------------------------------------------------------
    # Event handlers — supervisor IPC dict → CanonicalMessage
    # ------------------------------------------------------------------

    def _on_contact_event(self, data: Dict[str, Any]) -> None:
        self._handle_message_event("contact_message", data)

    def _on_channel_event(self, data: Dict[str, Any]) -> None:
        self._handle_message_event("channel_message", data)

    def _on_advertisement_event(self, data: Dict[str, Any]) -> None:
        # Build a NODEINFO message just like the in-process handler does.
        msg = self._dict_to_canonical("advertisement", data)
        if msg is None:
            return
        try:
            self.node_tracker.update_from_canonical(msg)
        except Exception as e:
            logger.debug("node_tracker update failed: %s", e)
        with self._stats_lock:
            self.stats["meshcore_advertisements"] = (
                self.stats.get("meshcore_advertisements", 0) + 1
            )

    def _on_ack_event(self, data: Dict[str, Any]) -> None:
        with self._stats_lock:
            self.stats["meshcore_acks_rx"] = (
                self.stats.get("meshcore_acks_rx", 0) + 1
            )

    def _on_connection_state_event(self, data: Dict[str, Any]) -> None:
        connected = bool(data.get("connected"))
        # Supervisor's persistent owner state changed. We stay attached to
        # the supervisor either way — let the health monitor know what the
        # radio's underlying state is so the bridge surface reflects it.
        try:
            if connected:
                self.health.record_connection_event(
                    "meshcore", "radio_up",
                    f"device={data.get('device')}"
                )
            else:
                self.health.record_connection_event(
                    "meshcore", "radio_down",
                    str(data.get("reason", "unknown"))
                )
        except Exception:
            pass

    def _handle_message_event(
        self,
        kind: str,
        data: Dict[str, Any],
    ) -> None:
        msg = self._dict_to_canonical(kind, data)
        if msg is None:
            return
        # Mirror to chat buffer regardless of routing decision — operator
        # verification surfaces always show what passed through.
        self.record_chat_message(
            direction="rx",
            text=msg.content or "",
            channel=getattr(msg, "channel", None),
            sender=msg.source_address,
            destination=msg.destination_address,
        )
        with self._stats_lock:
            self.stats["meshcore_messages_rx"] = (
                self.stats.get("meshcore_messages_rx", 0) + 1
            )
        if self._message_callback:
            try:
                self._message_callback(msg)
            except Exception as e:
                logger.warning("Supervisor handler message_callback raised: %s", e)

    def _dict_to_canonical(
        self,
        kind: str,
        data: Dict[str, Any],
    ) -> Optional[CanonicalMessage]:
        """Wrap a supervisor event dict in a meshcore_py-shaped shim and
        run it through the canonical converter."""
        type_name = _EVENT_TYPE_NAME.get(kind, kind.upper())
        # CanonicalMessage.from_meshcore handles dict payloads natively
        # (line 188 of canonical_message.py) — no need to fake meshcore_py
        # objects, just give it event.payload + event.type.
        shim = SimpleNamespace(payload=data, type=type_name)
        try:
            return CanonicalMessage.from_meshcore(shim)
        except Exception as e:
            logger.debug("Failed to convert %s event to canonical: %s", kind, e)
            return None


def _default_socket_path() -> str:
    """Inline import so the supervisor protocol module isn't loaded at
    every check."""
    from supervisor.protocol import DEFAULT_SOCKET_PATH
    return DEFAULT_SOCKET_PATH
