"""Tests for the periodic advert heartbeat in MeshCoreHandler.

Without this heartbeat, the NOC's name/coords/pubkey age out of every
neighbor's contact-share TTL and portable nodes can't introduce others
to it. The heartbeat fires send_advert() every ``advert_heartbeat_sec``
and is cancelled on disconnect / shutdown.

Tested invariants:

1. Settings reader respects gateway.json[meshcore] — interval, flood,
   and "feature off" when interval <= 0.
2. _start_advert_heartbeat is a no-op when interval=0 or no event loop
   is running (so tests / pre-connect paths don't crash).
3. _fire_heartbeat_advert calls self._radio.send_advert with the
   configured flood flag, swallows exceptions.
4. _cancel_advert_heartbeat is idempotent and clears the task handle.
5. disconnect cancels an in-flight heartbeat so we don't try to write
   to a half-closed connection.
"""
import asyncio
import os
import sys
import threading
from queue import Queue
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

SRC_DIR = os.path.join(os.path.dirname(__file__), "..", "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from gateway.meshcore_handler import MeshCoreHandler, _clear_active_handler


@pytest.fixture
def mock_config():
    meshcore = SimpleNamespace(
        enabled=True, device_path="/dev/ttyUSB1", baud_rate=115200,
        connection_type="serial", tcp_host="localhost", tcp_port=4000,
        auto_fetch_messages=True, bridge_channels=True, bridge_dms=True,
        simulation_mode=True, channel_poll_interval_sec=5,
        advert_heartbeat_sec=600, advert_heartbeat_flood=False,
    )
    return SimpleNamespace(
        meshcore=meshcore,
        meshtastic=SimpleNamespace(host="localhost", port=4403),
    )


@pytest.fixture
def handler(mock_config):
    h = MeshCoreHandler(
        config=mock_config,
        node_tracker=MagicMock(),
        health=MagicMock(record_error=MagicMock(return_value="transient")),
        stop_event=threading.Event(),
        stats={},
        stats_lock=threading.Lock(),
        message_queue=Queue(maxsize=10),
    )
    yield h
    _clear_active_handler(h)


# ── settings reader ────────────────────────────────────────────────


class TestAdvertHeartbeatSettings:
    def test_reads_interval_and_flood_from_config(self, handler):
        handler.config.meshcore.advert_heartbeat_sec = 300
        handler.config.meshcore.advert_heartbeat_flood = True
        interval, flood = handler._advert_heartbeat_settings()
        assert interval == 300
        assert flood is True

    def test_zero_interval_means_disabled(self, handler):
        handler.config.meshcore.advert_heartbeat_sec = 0
        interval, flood = handler._advert_heartbeat_settings()
        assert interval == 0

    def test_missing_meshcore_config_returns_zero(self, handler):
        handler.config = SimpleNamespace()  # no .meshcore at all
        interval, flood = handler._advert_heartbeat_settings()
        assert interval == 0
        assert flood is False

    def test_missing_attrs_fall_back_to_defaults(self, handler):
        # Older config files that predate the heartbeat fields
        handler.config.meshcore = SimpleNamespace(enabled=True)
        interval, flood = handler._advert_heartbeat_settings()
        assert interval == 600  # default
        assert flood is False


# ── start / cancel ─────────────────────────────────────────────────


class TestStartAdvertHeartbeat:
    def test_start_is_noop_when_interval_zero(self, handler):
        handler.config.meshcore.advert_heartbeat_sec = 0
        # No event loop attached — must not crash, must not create a task.
        handler._loop = None
        handler._start_advert_heartbeat()
        assert handler._advert_heartbeat_task is None

    def test_start_is_noop_when_no_loop(self, handler):
        """Calling pre-connect (no event loop) must not crash."""
        handler.config.meshcore.advert_heartbeat_sec = 600
        handler._loop = None
        handler._start_advert_heartbeat()
        assert handler._advert_heartbeat_task is None

    def test_cancel_is_idempotent(self, handler):
        # No task set: still safe.
        handler._advert_heartbeat_task = None
        handler._cancel_advert_heartbeat()
        # Cancelled task: still safe.
        done_task = MagicMock()
        done_task.done.return_value = True
        handler._advert_heartbeat_task = done_task
        handler._cancel_advert_heartbeat()
        done_task.cancel.assert_not_called()
        assert handler._advert_heartbeat_task is None

    def test_cancel_calls_cancel_on_running_task(self, handler):
        running_task = MagicMock()
        running_task.done.return_value = False
        handler._advert_heartbeat_task = running_task
        handler._cancel_advert_heartbeat()
        running_task.cancel.assert_called_once()
        assert handler._advert_heartbeat_task is None


# ── single-shot fire ───────────────────────────────────────────────


class TestFireHeartbeatAdvert:
    def test_calls_radio_send_advert_with_flood_flag(self, handler):
        send_advert = AsyncMock(return_value={})
        handler._radio = MagicMock()
        handler._radio.send_advert = send_advert
        asyncio.run(handler._fire_heartbeat_advert(flood=True))
        send_advert.assert_awaited_once_with(flood=True)

    def test_swallows_exceptions(self, handler):
        """A wire-glitch must not kill the heartbeat loop."""
        handler._radio = MagicMock()
        handler._radio.send_advert = AsyncMock(side_effect=RuntimeError("link down"))
        # Must not raise.
        asyncio.run(handler._fire_heartbeat_advert(flood=False))


# ── loop end-to-end ────────────────────────────────────────────────


class TestAdvertHeartbeatLoop:
    def test_fires_immediately_then_waits(self, handler):
        """First advert is sent without waiting for the interval to elapse."""
        send_advert = AsyncMock(return_value={})
        handler._radio = MagicMock()
        handler._radio.send_advert = send_advert
        handler._connected = True

        async def runner():
            # Stop after the first fire by stopping the handler before
            # the wait elapses.
            task = asyncio.create_task(handler._advert_heartbeat_loop(60, False))
            await asyncio.sleep(0.05)  # let the immediate fire run
            handler._stop_event.set()
            try:
                await asyncio.wait_for(task, timeout=2.0)
            except asyncio.TimeoutError:
                task.cancel()
                raise

        asyncio.run(runner())
        # At least one fire happened; possibly a second mid-wait — both
        # are acceptable. The contract is "fires soon, not 60s from now".
        assert send_advert.await_count >= 1

    def test_loop_exits_on_disconnect(self, handler):
        """When _connected drops the loop must wind down on the next tick."""
        send_advert = AsyncMock(return_value={})
        handler._radio = MagicMock()
        handler._radio.send_advert = send_advert
        handler._connected = True

        async def runner():
            task = asyncio.create_task(handler._advert_heartbeat_loop(60, False))
            await asyncio.sleep(0.05)
            handler._connected = False
            handler._stop_event.set()
            await asyncio.wait_for(task, timeout=2.0)

        asyncio.run(runner())


# ── disconnect cancels the heartbeat ───────────────────────────────


class TestDisconnectCancelsHeartbeat:
    def test_async_disconnect_cancels_running_heartbeat(self, handler):
        """_async_disconnect must call _cancel_advert_heartbeat first."""
        with patch.object(handler, "_cancel_advert_heartbeat") as cancel:
            asyncio.run(handler._async_disconnect())
            cancel.assert_called_once()
