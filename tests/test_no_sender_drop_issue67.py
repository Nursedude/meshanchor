"""
Issue #67 regression: PersistentMessageQueue.enqueue must short-circuit
when no sender is registered for the destination.

Observation that motivated this fix (meshanchor-server, 2026-05-18):
  - meshanchor-server boots with meshtastic.enabled=false in config
  - RNS->Mesh inbounds fail direct send (handler is None)
  - _process_rns_to_mesh requeues via _requeue_failed_message →
    _persistent_queue.enqueue(destination='meshtastic', ...)
  - register_sender('meshtastic', ...) was NEVER called because
    self._mesh_handler is None
  - process_once iterates _send_callbacks.items() — 'meshtastic' is not
    in there, so the queued rows are never even attempted
  - retry_count stays at 0; rows accumulate ~1500/day, ~10k/week

The fix: enqueue() consults has_sender() and drops with a rate-limited
INFO log if no sender is registered. The drop is a strict improvement:
the row would never have been delivered anyway, and now it doesn't
clutter the queue (or the dedup window).
"""

import sys
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


@pytest.fixture
def queue(tmp_path):
    from gateway.message_queue import PersistentMessageQueue
    q = PersistentMessageQueue(db_path=str(tmp_path / "no_sender.db"))
    try:
        yield q
    finally:
        q.stop_processing()


def _payload(text="hi"):
    return {"message": text, "source_id": "abcdef", "destination_id": "", "metadata": {}}


class TestHasSenderAPI:
    """The has_sender accessor is the public surface the bridge uses."""

    def test_has_sender_false_when_unregistered(self, queue):
        assert queue.has_sender("meshtastic") is False

    def test_has_sender_true_after_register(self, queue):
        queue.register_sender("meshtastic", lambda payload: True)
        assert queue.has_sender("meshtastic") is True

    def test_register_then_drop_state_clears(self, queue):
        # Pile up some drops, then register — register should clear the
        # rate-limit state so the next drop (if any) logs immediately.
        for _ in range(3):
            queue.enqueue(_payload(), destination="meshtastic")
        assert queue._no_sender_log_state.get("meshtastic") is not None
        queue.register_sender("meshtastic", lambda payload: True)
        assert "meshtastic" not in queue._no_sender_log_state


class TestEnqueueShortCircuit:
    """Enqueue returns None and writes nothing when no sender exists."""

    def test_enqueue_returns_none_with_no_sender(self, queue):
        assert queue.enqueue(_payload(), destination="meshtastic") is None

    def test_no_row_written_when_dropped(self, queue):
        queue.enqueue(_payload(), destination="meshtastic")
        # The DB should have zero rows for that destination
        with queue._get_connection() as conn:
            n = conn.execute(
                "SELECT COUNT(*) FROM messages WHERE destination=?",
                ("meshtastic",),
            ).fetchone()[0]
        assert n == 0

    def test_drop_counted_in_stats(self, queue):
        for _ in range(5):
            queue.enqueue(_payload(), destination="meshtastic")
        stats = queue.get_stats()
        assert stats.get("no_sender_dropped") == 5
        # Sanity: enqueued counter should NOT have moved
        assert stats.get("enqueued", 0) == 0

    def test_enqueue_after_register_works_normally(self, queue):
        sent = []
        queue.register_sender("meshtastic", lambda payload: sent.append(payload) or True)
        msg_id = queue.enqueue(_payload(), destination="meshtastic")
        assert msg_id is not None
        with queue._get_connection() as conn:
            n = conn.execute(
                "SELECT COUNT(*) FROM messages WHERE destination=?",
                ("meshtastic",),
            ).fetchone()[0]
        assert n == 1


class TestRateLimitedLog:
    """The drop log fires at most once per NO_SENDER_LOG_INTERVAL per dest."""

    def test_first_drop_logs_immediately(self, queue, caplog):
        import logging
        caplog.set_level(logging.INFO, logger="gateway.message_queue")
        queue.enqueue(_payload(), destination="meshtastic")
        msgs = [r.message for r in caplog.records if "no sender" in r.message]
        assert len(msgs) == 1
        assert "meshtastic" in msgs[0]
        assert "dropping enqueue" in msgs[0]

    def test_second_drop_within_window_does_not_relog(self, queue, caplog):
        import logging
        caplog.set_level(logging.INFO, logger="gateway.message_queue")
        queue.enqueue(_payload(), destination="meshtastic")
        queue.enqueue(_payload("second"), destination="meshtastic")
        msgs = [r.message for r in caplog.records if "no sender" in r.message]
        # Exactly one INFO line even though we dropped twice
        assert len(msgs) == 1

    def test_drop_after_window_relogs_with_suppressed_count(self, queue, caplog):
        import logging
        caplog.set_level(logging.INFO, logger="gateway.message_queue")
        # Manually backdate the first-drop timestamp so the second drop
        # crosses the rate-limit window without sleeping.
        queue.enqueue(_payload(), destination="meshtastic")
        # Pretend the first log happened > NO_SENDER_LOG_INTERVAL ago,
        # with 4 suppressed since.
        queue._no_sender_log_state["meshtastic"] = (
            time.time() - queue.NO_SENDER_LOG_INTERVAL - 1, 4
        )
        queue.enqueue(_payload("late"), destination="meshtastic")
        msgs = [r.message for r in caplog.records if "no sender" in r.message]
        assert len(msgs) == 2
        # The second line names the suppressed count
        assert "4 additional drops suppressed" in msgs[1]

    def test_separate_destinations_log_independently(self, queue, caplog):
        import logging
        caplog.set_level(logging.INFO, logger="gateway.message_queue")
        queue.enqueue(_payload(), destination="meshtastic")
        queue.enqueue(_payload(), destination="meshcore")
        msgs = [r.message for r in caplog.records if "no sender" in r.message]
        # One per destination on first drop
        assert len(msgs) == 2
        assert any("meshtastic" in m for m in msgs)
        assert any("meshcore" in m for m in msgs)


class TestIssue67SiteRegression:
    """Mirror the exact code path that hit production on meshanchor-server.

    The bridge's _requeue_failed_message calls _persistent_queue.enqueue
    with destination='meshtastic' when send_to_meshtastic fails because
    _mesh_handler is None. Pre-Issue-#67 these rows accumulated forever.
    """

    def test_requeue_to_unregistered_meshtastic_does_not_persist(self, queue):
        # Simulate the requeue path: enqueue with destination='meshtastic'
        # when only 'meshcore' has a registered sender.
        queue.register_sender("meshcore", lambda payload: True)
        # 14 inbounds (matches the production count at fix time)
        for i in range(14):
            queue.enqueue(_payload(f"msg{i}"), destination="meshtastic")
        with queue._get_connection() as conn:
            n_meshtastic = conn.execute(
                "SELECT COUNT(*) FROM messages WHERE destination='meshtastic'"
            ).fetchone()[0]
            n_meshcore = conn.execute(
                "SELECT COUNT(*) FROM messages WHERE destination='meshcore'"
            ).fetchone()[0]
        assert n_meshtastic == 0
        # And meshcore enqueue still works
        queue.enqueue(_payload("ok"), destination="meshcore")
        with queue._get_connection() as conn:
            n_meshcore = conn.execute(
                "SELECT COUNT(*) FROM messages WHERE destination='meshcore'"
            ).fetchone()[0]
        assert n_meshcore == 1
