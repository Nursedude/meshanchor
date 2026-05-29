"""
Regression tests for the map-server SIGTERM shutdown deadlock.

main() runs server.start() -> serve_forever() on the MAIN thread, and Python
delivers SIGTERM/SIGINT to the main thread. The signal handler used to call
server.stop() -> BaseServer.shutdown() inline, which blocks until the
serve_forever() loop observes the stop flag — but that loop *is* the thread
running the handler, so it deadlocked and systemd SIGKILLed after
TimeoutStopSec on every stop/restart.

The fix offloads stop() to a separate daemon thread (_spawn_shutdown) so the
main thread's serve_forever() unwinds naturally. These tests pin that the
offload (a) doesn't block the caller even when stop() blocks, (b) runs stop()
on a different thread, (c) is a daemon thread, and (d) still removes the PID
file even if stop() raises.

Run: python3 -m pytest tests/test_map_daemon_shutdown.py -v
"""

import threading
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))

from utils.map_data_service import _spawn_shutdown


class TestSpawnShutdownDoesNotDeadlock:
    def test_does_not_block_caller_when_stop_blocks(self):
        """The whole point: a blocking stop() must NOT block the caller
        (the serving/main thread), or shutdown deadlocks."""
        release = threading.Event()
        stop_entered = threading.Event()

        class BlockingServer:
            def stop(self):
                stop_entered.set()
                # Simulate shutdown() blocking until serve_forever yields.
                release.wait(timeout=5)

        with patch('utils.map_data_service._remove_pid_file'):
            t = _spawn_shutdown(BlockingServer(), '/tmp/whatever.pid')
            # stop() should be running (on the other thread) but the caller
            # returned immediately rather than blocking on it.
            assert stop_entered.wait(timeout=2), "stop() never started"
            assert t.is_alive(), "shutdown thread should still be in stop()"
            release.set()
            t.join(timeout=5)
            assert not t.is_alive()

    def test_runs_stop_on_a_different_thread(self):
        """stop() must execute off the caller's thread."""
        caller_thread = threading.current_thread()
        ran_on = {}

        class RecordingServer:
            def stop(self):
                ran_on['thread'] = threading.current_thread()

        with patch('utils.map_data_service._remove_pid_file'):
            t = _spawn_shutdown(RecordingServer(), '/tmp/whatever.pid')
            t.join(timeout=5)

        assert ran_on['thread'] is not caller_thread

    def test_thread_is_daemon(self):
        """Daemon so it can never block process exit."""
        class NoopServer:
            def stop(self):
                pass

        with patch('utils.map_data_service._remove_pid_file'):
            t = _spawn_shutdown(NoopServer(), '/tmp/whatever.pid')
            assert t.daemon is True
            t.join(timeout=5)

    def test_pid_file_removed_even_if_stop_raises(self):
        """PID file cleanup must happen in a finally, regardless of stop()."""
        class FailingServer:
            def stop(self):
                raise RuntimeError("stop blew up")

        with patch('utils.map_data_service._remove_pid_file') as mock_rm:
            t = _spawn_shutdown(FailingServer(), '/tmp/whatever.pid')
            t.join(timeout=5)
            mock_rm.assert_called_once_with('/tmp/whatever.pid')
