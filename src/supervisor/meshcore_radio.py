"""MeshCore radio supervisor.

A long-running process that owns the MeshCore companion radio
session and exposes it to other MeshAnchor processes over a
Unix-domain socket. Lets the gateway bridge restart without flapping
the radio, and gives the TUI / CLI a way to talk to the radio
without racing the bridge for the serial port.

Lifecycle
---------

::

    start
      └── connect_radio() ── ReconnectStrategy backoff on failure
            ├── acquire_for_connect(owner='meshcore-radio')
            ├── MeshCore.create_serial / create_tcp
            ├── register_persistent(...)
            └── subscribe to RX events → broadcast to clients
      └── unix_server.serve_forever()
      └── on SIGTERM: close server, disconnect radio, unregister, exit

Run with:
    python3 -m supervisor.meshcore_radio --socket /run/meshanchor/...
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
import time
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional, Set

from gateway.config import GatewayConfig
from gateway.reconnect import ReconnectConfig, ReconnectStrategy
from supervisor import protocol
from utils.boundary_timing import timed_boundary
from utils.meshcore_connection import (
    ConnectionMode,
    acquire_for_connect,
    get_connection_manager,
)
from utils.safe_import import safe_import

logger = logging.getLogger("meshcore-radio")

_meshcore_mod, _HAS_MESHCORE = safe_import("meshcore")


# Match the bridge handler — keep these in sync if either side learns new
# event types. The gateway handler still owns RNS-side translation; the
# supervisor only delivers raw event payloads.
_EVENT_TYPE_MAP = {
    "CONTACT_MSG_RECV": "contact_message",
    "CHANNEL_MSG_RECV": "channel_message",
    "ADVERTISEMENT": "advertisement",
    "ACK": "ack",
}


class MeshCoreRadioSupervisor:
    """Single-process supervisor that owns one MeshCore radio."""

    def __init__(
        self,
        socket_path: str,
        device_path: str,
        baud_rate: int = 115200,
        connection_type: str = "serial",
        tcp_host: str = "localhost",
        tcp_port: int = 4000,
        *,
        socket_mode: int = 0o660,
        health_probe_interval_s: float = 30.0,
        reconnect_config: Optional[ReconnectConfig] = None,
    ) -> None:
        self.socket_path = socket_path
        self.device_path = device_path
        self.baud_rate = baud_rate
        self.connection_type = connection_type
        self.tcp_host = tcp_host
        self.tcp_port = tcp_port
        self.socket_mode = socket_mode
        self.health_probe_interval_s = health_probe_interval_s
        self._reconnect = ReconnectStrategy(
            config=reconnect_config or ReconnectConfig(
                initial_delay=1.0,
                max_delay=30.0,
                multiplier=2.0,
                jitter=0.1,
                max_attempts=10000,  # supervisor never gives up — service-level concern
            )
        )

        self._stop = asyncio.Event()
        self._meshcore: Any = None
        self._meshcore_lock = asyncio.Lock()  # serialise client ops + reconnect
        self._connected_at: Optional[float] = None

        self._clients: Set[asyncio.StreamWriter] = set()
        self._clients_lock = asyncio.Lock()

        self._server: Optional[asyncio.AbstractServer] = None

    # ------------------------------------------------------------------
    # entry points
    # ------------------------------------------------------------------

    async def run(self) -> int:
        """Run the supervisor until SIGTERM / SIGINT."""
        self._install_signal_handlers()
        self._prepare_socket_dir()
        try:
            self._server = await asyncio.start_unix_server(
                self._handle_client, path=self.socket_path,
            )
            os.chmod(self.socket_path, self.socket_mode)
            logger.info("Listening on %s (mode=%o)", self.socket_path,
                        self.socket_mode)
        except OSError as e:
            logger.error("Failed to bind unix socket %s: %s",
                         self.socket_path, e)
            return 2

        radio_task = asyncio.create_task(self._radio_loop(),
                                         name="radio_loop")
        server_task = asyncio.create_task(self._serve_until_stop(),
                                          name="server_loop")
        try:
            await self._stop.wait()
        finally:
            logger.info("Shutting down")
            radio_task.cancel()
            server_task.cancel()
            await self._close_clients()
            await self._disconnect_radio()
            await asyncio.gather(radio_task, server_task,
                                 return_exceptions=True)
            self._server.close()
            try:
                os.unlink(self.socket_path)
            except FileNotFoundError:
                pass
        return 0

    def request_stop(self) -> None:
        if not self._stop.is_set():
            logger.info("Stop requested")
            self._stop.set()

    # ------------------------------------------------------------------
    # radio lifecycle
    # ------------------------------------------------------------------

    async def _radio_loop(self) -> None:
        """Connect → run → on disconnect, back off and retry."""
        while not self._stop.is_set():
            try:
                await self._connect_and_register()
                self._reconnect.record_success()
                await self._run_until_disconnect()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning("Radio loop error: %s", e)

            if self._stop.is_set():
                break

            self._reconnect.record_failure()
            delay = self._reconnect.get_delay()
            logger.info("Reconnect in %.1fs (attempt %d)", delay,
                        self._reconnect.attempts)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=delay)
            except asyncio.TimeoutError:
                pass

    async def _connect_and_register(self) -> None:
        if not _HAS_MESHCORE:
            raise RuntimeError(
                "meshcore_py not installed — pip install meshcore"
            )

        loop = asyncio.get_running_loop()
        with acquire_for_connect(owner="meshcore-radio",
                                 lock_timeout=30.0) as got_lock:
            if not got_lock:
                raise RuntimeError(
                    "could not acquire MESHCORE_CONNECTION_LOCK — "
                    "another owner active or stuck"
                )

            MeshCore = _meshcore_mod.MeshCore
            if self.connection_type == "serial":
                logger.info("Connecting to MeshCore via serial: %s",
                            self.device_path)
                with timed_boundary("meshcore.connect_serial",
                                    target=self.device_path,
                                    threshold_s=5.0):
                    self._meshcore = await MeshCore.create_serial(
                        self.device_path, self.baud_rate,
                    )
                mode = ConnectionMode.SERIAL
                device = self.device_path
            elif self.connection_type == "tcp":
                logger.info("Connecting to MeshCore via TCP: %s:%d",
                            self.tcp_host, self.tcp_port)
                with timed_boundary("meshcore.connect_tcp",
                                    target=f"{self.tcp_host}:{self.tcp_port}",
                                    threshold_s=5.0):
                    self._meshcore = await MeshCore.create_tcp(
                        self.tcp_host, self.tcp_port,
                    )
                mode = ConnectionMode.TCP
                device = f"{self.tcp_host}:{self.tcp_port}"
            else:
                raise RuntimeError(
                    f"unsupported connection_type: {self.connection_type}"
                )

            self._subscribe_events()
            try:
                await self._meshcore.start_auto_message_fetching()
            except Exception as e:
                # Auto-fetch is a meshcore_py convenience; if it isn't
                # available the supervisor still works — clients can poll.
                logger.debug("start_auto_message_fetching failed: %s", e)

            get_connection_manager().register_persistent(
                self._meshcore, loop,
                owner="meshcore-radio",
                mode=mode,
                device=device,
            )
            self._connected_at = time.time()
            logger.info("MeshCore radio connected and registered")

        await self._broadcast_event("connection_state", {
            "connected": True,
            "device": device,
            "mode": mode.value,
        })

    async def _run_until_disconnect(self) -> None:
        """Periodic health probe — exit (and trigger reconnect) on failure."""
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(
                    self._stop.wait(),
                    timeout=self.health_probe_interval_s,
                )
                return  # stop event fired
            except asyncio.TimeoutError:
                pass
            if not await self._probe_radio():
                logger.warning("Health probe failed — triggering reconnect")
                await self._disconnect_radio()
                await self._broadcast_event("connection_state", {
                    "connected": False,
                    "reason": "health_probe_failed",
                })
                return

    async def _probe_radio(self) -> bool:
        """Cheap liveness probe. ``True`` if radio responds."""
        if self._meshcore is None:
            return False
        async with self._meshcore_lock:
            try:
                with timed_boundary("meshcore.probe_health", threshold_s=5.0):
                    if hasattr(self._meshcore, "commands"):
                        # get_contacts is the lightest universally-supported call
                        await asyncio.wait_for(
                            self._meshcore.commands.get_contacts(),
                            timeout=5.0,
                        )
                    return True
            except Exception as e:
                logger.debug("Health probe error: %s", e)
                return False

    async def _disconnect_radio(self) -> None:
        """Tear down the live MeshCore session and unregister."""
        meshcore = self._meshcore
        self._meshcore = None
        self._connected_at = None
        if meshcore is not None:
            try:
                if hasattr(meshcore, "disconnect"):
                    await meshcore.disconnect()
                elif hasattr(meshcore, "close"):
                    await meshcore.close()
            except Exception as e:
                logger.debug("Disconnect error: %s", e)
        try:
            get_connection_manager().unregister_persistent()
        except Exception as e:
            logger.debug("Unregister error: %s", e)

    # ------------------------------------------------------------------
    # event subscription → broadcast
    # ------------------------------------------------------------------

    def _subscribe_events(self) -> None:
        if not self._meshcore or not _HAS_MESHCORE:
            return
        EventType = _meshcore_mod.EventType
        for upstream, downstream in _EVENT_TYPE_MAP.items():
            evt = getattr(EventType, upstream, None)
            if evt is None:
                logger.debug("EventType.%s not on this meshcore_py — skipping",
                             upstream)
                continue
            self._meshcore.subscribe(
                evt,
                self._make_event_forwarder(downstream),
            )

    def _make_event_forwarder(
        self, kind: str,
    ) -> Callable[[Any], Awaitable[None]]:
        async def _forward(event: Any) -> None:
            payload = self._serialize_event(event)
            await self._broadcast_event(kind, payload)
        return _forward

    def _serialize_event(self, event: Any) -> Dict[str, Any]:
        """Best-effort mapping of meshcore_py event → JSON-safe dict."""
        if isinstance(event, dict):
            return event
        if hasattr(event, "payload"):
            payload = event.payload
            if isinstance(payload, dict):
                return payload
            return {"payload": payload}
        if hasattr(event, "__dict__"):
            return {k: v for k, v in event.__dict__.items()
                    if not k.startswith("_")}
        return {"value": repr(event)}

    # ------------------------------------------------------------------
    # client connections
    # ------------------------------------------------------------------

    async def _serve_until_stop(self) -> None:
        if self._server is None:
            return
        try:
            await self._stop.wait()
        finally:
            self._server.close()
            await self._server.wait_closed()

    async def _handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        peer = writer.get_extra_info("peername") or "unix"
        logger.info("Client connected: %s", peer)
        async with self._clients_lock:
            self._clients.add(writer)
        try:
            mgr = get_connection_manager()
            hello = protocol.Hello(
                owner=mgr.get_persistent_owner() or "meshcore-radio",
                mode=(mgr.get_mode().value if mgr.get_mode() else None),
                device=mgr.get_device(),
                connected=mgr.has_persistent(),
            )
            writer.write(protocol.encode(hello))
            await writer.drain()

            while not self._stop.is_set():
                line = await reader.readline()
                if not line:
                    return
                await self._dispatch_request(line, writer)
        except (ConnectionResetError, BrokenPipeError):
            pass
        except Exception as e:
            logger.warning("Client error: %s", e)
        finally:
            async with self._clients_lock:
                self._clients.discard(writer)
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
            logger.info("Client disconnected: %s", peer)

    async def _dispatch_request(
        self,
        line: bytes,
        writer: asyncio.StreamWriter,
    ) -> None:
        try:
            frame = protocol.decode(line)
        except protocol.ProtocolError as e:
            logger.warning("Bad frame: %s", e)
            return
        if frame.get("type") != "request":
            return
        req_id = frame.get("id")
        method = frame.get("method")
        args = frame.get("args") or {}
        if not isinstance(req_id, int) or method not in protocol.METHODS:
            writer.write(protocol.make_error(
                req_id if isinstance(req_id, int) else 0,
                f"unknown method: {method}",
            ))
            await writer.drain()
            return
        try:
            result = await self._invoke(method, args)
            writer.write(protocol.make_response(req_id, result))
        except Exception as e:
            logger.warning("Method %s failed: %s", method, e)
            writer.write(protocol.make_error(req_id, str(e)))
        await writer.drain()

    async def _invoke(self, method: str, args: Dict[str, Any]) -> Any:
        if method == "ping":
            return {"ok": True, "ts": time.time()}
        if method == "status":
            return self._status_payload()
        if self._meshcore is None:
            raise RuntimeError("radio not connected")
        async with self._meshcore_lock:
            if method == "get_radio_info":
                return await self._call_command("get_radio_info")
            if method == "get_contacts":
                return await self._call_command("get_contacts")
            if method == "get_channels":
                return await self._call_command("get_channels")
            if method == "send_message":
                return await self._send_message(args)
        raise RuntimeError(f"unhandled method: {method}")

    async def _call_command(self, name: str) -> Any:
        commands = getattr(self._meshcore, "commands", None)
        if commands is None:
            raise RuntimeError("meshcore.commands not available")
        fn = getattr(commands, name, None)
        if fn is None:
            raise RuntimeError(f"command not supported: {name}")
        with timed_boundary(f"meshcore.{name}", threshold_s=3.0):
            evt = await asyncio.wait_for(fn(), timeout=10.0)
        return self._serialize_event(evt)

    async def _send_message(self, args: Dict[str, Any]) -> Dict[str, Any]:
        kind = args.get("kind")
        target = args.get("target")
        text = args.get("text", "")
        if not text:
            raise ValueError("text required")
        commands = getattr(self._meshcore, "commands", None)
        if commands is None:
            raise RuntimeError("meshcore.commands not available")
        if kind == "channel":
            channel = int(target if target is not None else 0)
            with timed_boundary("meshcore.send_chan_msg", threshold_s=5.0):
                await asyncio.wait_for(
                    commands.send_chan_msg(channel, text),
                    timeout=10.0,
                )
            return {"sent": True, "kind": "channel", "channel": channel}
        if kind == "contact":
            with timed_boundary("meshcore.send_msg", threshold_s=5.0):
                await asyncio.wait_for(
                    commands.send_msg(target, text),
                    timeout=10.0,
                )
            return {"sent": True, "kind": "contact", "target": str(target)}
        raise ValueError(f"unknown kind: {kind}")

    def _status_payload(self) -> Dict[str, Any]:
        mgr = get_connection_manager()
        return {
            "connected": mgr.has_persistent(),
            "owner": mgr.get_persistent_owner(),
            "mode": mgr.get_mode().value if mgr.get_mode() else None,
            "device": mgr.get_device(),
            "uptime_s": (time.time() - self._connected_at)
                        if self._connected_at else 0.0,
            "reconnect_attempts": self._reconnect.attempts,
            "clients": len(self._clients),
        }

    async def _broadcast_event(self, kind: str, data: Dict[str, Any]) -> None:
        try:
            frame = protocol.make_event(kind, data)
        except protocol.ProtocolError:
            return
        async with self._clients_lock:
            dead: List[asyncio.StreamWriter] = []
            for client in self._clients:
                try:
                    client.write(frame)
                    await client.drain()
                except Exception:
                    dead.append(client)
            for client in dead:
                self._clients.discard(client)
                try:
                    client.close()
                except Exception:
                    pass

    async def _close_clients(self) -> None:
        async with self._clients_lock:
            for client in list(self._clients):
                try:
                    client.close()
                    await client.wait_closed()
                except Exception:
                    pass
            self._clients.clear()

    # ------------------------------------------------------------------
    # signals + filesystem
    # ------------------------------------------------------------------

    def _install_signal_handlers(self) -> None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, self.request_stop)
            except (NotImplementedError, RuntimeError):
                # Windows / non-main thread — fall back to default handling.
                pass

    def _prepare_socket_dir(self) -> None:
        socket_dir = Path(self.socket_path).parent
        try:
            socket_dir.mkdir(parents=True, exist_ok=True)
        except PermissionError as e:
            logger.warning("Cannot create %s: %s", socket_dir, e)
        # If a stale socket from a prior run is still around, clear it.
        try:
            if Path(self.socket_path).exists():
                os.unlink(self.socket_path)
        except OSError as e:
            logger.warning("Cannot clear stale socket %s: %s",
                           self.socket_path, e)


# ---------------------------------------------------------------------------
# CLI entry
# ---------------------------------------------------------------------------


def _build_supervisor_from_config() -> MeshCoreRadioSupervisor:
    """Pull device path and connection type from gateway.json."""
    gw = GatewayConfig.load()
    mc = getattr(gw, "meshcore", None)
    if mc is None:
        raise RuntimeError("MeshCore section missing from gateway config")
    return MeshCoreRadioSupervisor(
        socket_path=protocol.DEFAULT_SOCKET_PATH,
        device_path=getattr(mc, "device_path", "/dev/ttyMeshCore"),
        baud_rate=int(getattr(mc, "baud_rate", 115200)),
        connection_type=getattr(mc, "connection_type", "serial"),
        tcp_host=getattr(mc, "tcp_host", "localhost"),
        tcp_port=int(getattr(mc, "tcp_port", 4000)),
    )


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="meshcore-radio",
        description="MeshCore radio supervisor (Session 2 of MeshCore charter)",
    )
    parser.add_argument("--socket", default=protocol.DEFAULT_SOCKET_PATH)
    parser.add_argument("--device", default=None,
                        help="Override device_path from gateway.json")
    parser.add_argument("--connection-type",
                        choices=("serial", "tcp"), default=None)
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=args.log_level.upper(),
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    sup = _build_supervisor_from_config()
    if args.socket:
        sup.socket_path = args.socket
    if args.device:
        sup.device_path = args.device
    if args.connection_type:
        sup.connection_type = args.connection_type

    return asyncio.run(sup.run())


if __name__ == "__main__":
    raise SystemExit(main())
