"""
Tests for PersistentMessageQueue — SQLite-backed message queue with
retry, deduplication, priority ordering, dead letter queue, lifecycle
tracking, and overflow management.

Uses in-memory SQLite (:memory:) for fast, isolated tests.

Run: python3 -m pytest tests/test_message_queue.py -v
"""

import json
import os
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from gateway.message_queue import (
    MessageLifecycleEvent,
    MessageLifecycleState,
    MessagePriority,
    MessageStatus,
    PersistentMessageQueue,
    QueuedMessage,
    RetryDecision,
    RetryPolicy,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def queue(tmp_path):
    """Queue backed by temp file (each _get_connection shares the same DB)."""
    db_file = str(tmp_path / "test_queue.db")
    q = PersistentMessageQueue(db_path=db_file)
    yield q
    q.stop_processing()


@pytest.fixture
def queue_with_policy(tmp_path):
    """Queue with retry policy backed by temp file."""
    db_file = str(tmp_path / "test_queue_policy.db")
    policy = RetryPolicy(max_tries=3, base_delay=0.01)
    q = PersistentMessageQueue(db_path=db_file, retry_policy=policy)
    yield q
    q.stop_processing()


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class TestEnums:
    """Test enum values."""

    def test_priority_ordering(self):
        assert MessagePriority.LOW.value < MessagePriority.NORMAL.value
        assert MessagePriority.NORMAL.value < MessagePriority.HIGH.value
        assert MessagePriority.HIGH.value < MessagePriority.URGENT.value

    def test_status_values(self):
        assert MessageStatus.PENDING.value == "pending"
        assert MessageStatus.DEAD_LETTER.value == "dead_letter"

    def test_lifecycle_states(self):
        assert MessageLifecycleState.CREATED.value == "created"
        assert MessageLifecycleState.ACK.value == "ack"


# ---------------------------------------------------------------------------
# QueuedMessage
# ---------------------------------------------------------------------------

class TestQueuedMessage:
    """Tests for QueuedMessage data class."""

    def test_to_dict_roundtrip(self):
        msg = QueuedMessage(
            id="test-1",
            payload={"text": "hello", "from": "!abc"},
            destination="meshtastic",
            priority=MessagePriority.HIGH,
        )
        d = msg.to_dict()
        restored = QueuedMessage.from_dict(d)
        assert restored.id == msg.id
        assert restored.payload == msg.payload
        assert restored.destination == msg.destination
        assert restored.priority == MessagePriority.HIGH

    def test_payload_serialized_as_json(self):
        msg = QueuedMessage(
            id="test-2",
            payload={"key": "value"},
            destination="rns",
        )
        d = msg.to_dict()
        assert isinstance(d["payload"], str)
        assert json.loads(d["payload"]) == {"key": "value"}

    def test_retry_after_none(self):
        msg = QueuedMessage(id="t", payload={}, destination="x")
        d = msg.to_dict()
        assert d["retry_after"] is None

    def test_retry_after_set(self):
        future = datetime.now() + timedelta(seconds=30)
        msg = QueuedMessage(id="t", payload={}, destination="x", retry_after=future)
        d = msg.to_dict()
        restored = QueuedMessage.from_dict(d)
        assert restored.retry_after is not None


# ---------------------------------------------------------------------------
# MessageLifecycleEvent
# ---------------------------------------------------------------------------

class TestMessageLifecycleEvent:
    """Tests for lifecycle event serialization."""

    def test_to_dict_roundtrip(self):
        event = MessageLifecycleEvent(
            message_id="msg-1",
            state=MessageLifecycleState.SENT,
            timestamp=datetime.now(),
            details="via meshtastic",
            node_id="!aabb",
            hop_count=2,
        )
        d = event.to_dict()
        restored = MessageLifecycleEvent.from_dict(d)
        assert restored.message_id == "msg-1"
        assert restored.state == MessageLifecycleState.SENT
        assert restored.node_id == "!aabb"
        assert restored.hop_count == 2


# ---------------------------------------------------------------------------
# RetryPolicy
# ---------------------------------------------------------------------------

class TestRetryPolicy:
    """Tests for NGINX-style retry policy."""

    def test_retriable_error_retries(self):
        policy = RetryPolicy(max_tries=3)
        decision = policy.should_retry("connection refused", attempt=1)
        assert decision.retry is True
        assert decision.delay > 0

    def test_non_retriable_error_no_retry(self):
        policy = RetryPolicy(max_tries=3)
        decision = policy.should_retry("permission denied", attempt=1)
        assert decision.retry is False
        assert "permanent_error" in decision.reason

    def test_max_attempts_exceeded(self):
        policy = RetryPolicy(max_tries=3)
        decision = policy.should_retry("timeout", attempt=3)
        assert decision.retry is False
        assert "max_attempts" in decision.reason

    def test_unknown_error_retries_once(self):
        policy = RetryPolicy(max_tries=5)
        d1 = policy.should_retry("something weird", attempt=1)
        assert d1.retry is True
        d2 = policy.should_retry("something weird", attempt=2)
        assert d2.retry is False

    def test_exponential_backoff_delay(self):
        policy = RetryPolicy(base_delay=2.0, max_delay=60.0)
        assert policy.get_delay_for_attempt(1) == 2.0
        assert policy.get_delay_for_attempt(2) == 4.0
        assert policy.get_delay_for_attempt(3) == 8.0

    def test_delay_capped_at_max(self):
        policy = RetryPolicy(base_delay=2.0, max_delay=10.0)
        assert policy.get_delay_for_attempt(10) == 10.0

    def test_timeout_is_retriable(self):
        policy = RetryPolicy()
        d = policy.should_retry("operation timed out", attempt=1)
        assert d.retry is True

    def test_broken_pipe_is_retriable(self):
        policy = RetryPolicy()
        d = policy.should_retry("broken pipe", attempt=1)
        assert d.retry is True

    def test_message_too_large_not_retriable(self):
        policy = RetryPolicy()
        d = policy.should_retry("message too large", attempt=1)
        assert d.retry is False

    def test_for_meshtastic(self):
        p = RetryPolicy.for_meshtastic()
        assert p.max_tries == 3
        assert p.base_delay == 5.0

    def test_for_rns(self):
        p = RetryPolicy.for_rns()
        assert p.max_tries == 5
        assert p.base_delay == 2.0

    def test_classify_case_insensitive(self):
        policy = RetryPolicy()
        d = policy.should_retry("CONNECTION REFUSED by remote", attempt=1)
        assert d.retry is True


# ---------------------------------------------------------------------------
# PersistentMessageQueue — enqueue
# ---------------------------------------------------------------------------

class TestEnqueue:
    """Tests for message enqueue."""

    def test_enqueue_returns_id(self, queue):
        msg_id = queue.enqueue({"text": "hello"}, "meshtastic")
        assert msg_id is not None
        assert isinstance(msg_id, str)

    def test_enqueue_increments_stats(self, queue):
        queue.enqueue({"text": "hello"}, "meshtastic")
        assert queue._stats["enqueued"] == 1

    def test_enqueue_multiple(self, queue):
        id1 = queue.enqueue({"text": "one"}, "meshtastic")
        id2 = queue.enqueue({"text": "two"}, "rns")
        assert id1 != id2

    def test_enqueue_with_priority(self, queue):
        queue.enqueue({"text": "urgent"}, "meshtastic", priority=MessagePriority.URGENT)
        msgs = queue.get_pending()
        assert len(msgs) == 1
        assert msgs[0].priority == MessagePriority.URGENT


# ---------------------------------------------------------------------------
# PersistentMessageQueue — deduplication
# ---------------------------------------------------------------------------

class TestDeduplication:
    """Tests for message deduplication."""

    def test_duplicate_suppressed(self, queue):
        id1 = queue.enqueue({"text": "hello", "from": "a", "to": "b"}, "meshtastic")
        id2 = queue.enqueue({"text": "hello", "from": "a", "to": "b"}, "meshtastic")
        assert id1 is not None
        assert id2 is None
        assert queue._stats["deduplicated"] == 1

    def test_different_messages_not_deduplicated(self, queue):
        id1 = queue.enqueue({"text": "hello"}, "meshtastic")
        id2 = queue.enqueue({"text": "world"}, "meshtastic")
        assert id1 is not None
        assert id2 is not None

    def test_same_content_different_destination_not_deduplicated(self, queue):
        id1 = queue.enqueue({"text": "hello"}, "meshtastic")
        id2 = queue.enqueue({"text": "hello"}, "rns")
        assert id1 is not None
        assert id2 is not None

    def test_dedup_disabled(self, queue):
        id1 = queue.enqueue({"text": "hello"}, "meshtastic", deduplicate=False)
        id2 = queue.enqueue({"text": "hello"}, "meshtastic", deduplicate=False)
        assert id1 is not None
        assert id2 is not None


# ---------------------------------------------------------------------------
# PersistentMessageQueue — get_pending / priority ordering
# ---------------------------------------------------------------------------

class TestGetPending:
    """Tests for pending message retrieval."""

    def test_empty_queue(self, queue):
        assert queue.get_pending() == []

    def test_priority_ordering(self, queue):
        queue.enqueue({"text": "low"}, "meshtastic", priority=MessagePriority.LOW, deduplicate=False)
        queue.enqueue({"text": "urgent"}, "meshtastic", priority=MessagePriority.URGENT, deduplicate=False)
        queue.enqueue({"text": "normal"}, "meshtastic", priority=MessagePriority.NORMAL, deduplicate=False)

        msgs = queue.get_pending()
        assert len(msgs) == 3
        assert msgs[0].priority == MessagePriority.URGENT
        assert msgs[1].priority == MessagePriority.NORMAL
        assert msgs[2].priority == MessagePriority.LOW

    def test_filter_by_destination(self, queue):
        queue.enqueue({"text": "mesh"}, "meshtastic")
        queue.enqueue({"text": "rns"}, "rns")

        mesh_msgs = queue.get_pending(destination="meshtastic")
        assert len(mesh_msgs) == 1
        assert mesh_msgs[0].destination == "meshtastic"

    def test_limit(self, queue):
        for i in range(10):
            queue.enqueue({"text": f"msg{i}", "id": i}, "meshtastic", deduplicate=False)
        msgs = queue.get_pending(limit=3)
        assert len(msgs) == 3


# ---------------------------------------------------------------------------
# PersistentMessageQueue — status transitions
# ---------------------------------------------------------------------------

class TestStatusTransitions:
    """Tests for mark_in_progress, mark_delivered, mark_failed."""

    def test_mark_in_progress(self, queue):
        msg_id = queue.enqueue({"text": "test"}, "meshtastic")
        assert queue.mark_in_progress(msg_id) is True
        # Should not appear in pending anymore
        assert len(queue.get_pending()) == 0

    def test_mark_in_progress_idempotent(self, queue):
        msg_id = queue.enqueue({"text": "test"}, "meshtastic")
        queue.mark_in_progress(msg_id)
        # Second call fails (already in_progress, not pending)
        assert queue.mark_in_progress(msg_id) is False

    def test_mark_delivered(self, queue):
        msg_id = queue.enqueue({"text": "test"}, "meshtastic")
        queue.mark_in_progress(msg_id)
        assert queue.mark_delivered(msg_id) is True
        assert queue._stats["delivered"] == 1

    def test_mark_failed_schedules_retry(self, queue):
        msg_id = queue.enqueue({"text": "test"}, "meshtastic", max_retries=3)
        queue.mark_in_progress(msg_id)
        queue.mark_failed(msg_id, "connection reset")
        # Should be back to pending with retry_after set
        assert queue._stats["retried"] == 1

    def test_mark_failed_dead_letter_after_max(self, queue):
        msg_id = queue.enqueue({"text": "test"}, "meshtastic", max_retries=1)
        queue.mark_in_progress(msg_id)
        queue.mark_failed(msg_id, "error")
        # retry_count=1 >= max_retries=1 -> dead letter
        assert queue._stats["failed"] == 1
        dead = queue.get_dead_letters()
        assert len(dead) == 1

    def test_mark_failed_nonexistent(self, queue):
        assert queue.mark_failed("nonexistent", "error") is False


# ---------------------------------------------------------------------------
# PersistentMessageQueue — RetryPolicy integration
# ---------------------------------------------------------------------------

class TestRetryPolicyIntegration:
    """Tests for intelligent retry with RetryPolicy."""

    def test_permanent_error_goes_to_dead_letter(self, queue_with_policy):
        msg_id = queue_with_policy.enqueue({"text": "test"}, "meshtastic")
        queue_with_policy.mark_in_progress(msg_id)
        queue_with_policy.mark_failed(msg_id, "permission denied")
        assert queue_with_policy._stats["permanent_failures"] == 1
        dead = queue_with_policy.get_dead_letters()
        assert len(dead) == 1

    def test_transient_error_retries(self, queue_with_policy):
        msg_id = queue_with_policy.enqueue({"text": "test"}, "meshtastic")
        queue_with_policy.mark_in_progress(msg_id)
        queue_with_policy.mark_failed(msg_id, "connection refused")
        assert queue_with_policy._stats["retried"] == 1

    def test_set_retry_policy(self, queue):
        policy = RetryPolicy(max_tries=5)
        queue.set_retry_policy(policy)
        assert queue._retry_policy is policy


# ---------------------------------------------------------------------------
# PersistentMessageQueue — process_once
# ---------------------------------------------------------------------------

class TestProcessOnce:
    """Tests for single-batch processing."""

    def test_process_delivers_message(self, queue):
        sender = MagicMock(return_value=True)
        queue.register_sender("meshtastic", sender)
        queue.enqueue({"text": "hello"}, "meshtastic")

        processed = queue.process_once()
        assert processed == 1
        sender.assert_called_once()

    def test_process_failed_send(self, queue):
        sender = MagicMock(return_value=False)
        queue.register_sender("meshtastic", sender)
        queue.enqueue({"text": "hello"}, "meshtastic")

        processed = queue.process_once()
        assert processed == 1
        # Message should be scheduled for retry or dead_letter

    def test_process_exception(self, queue):
        sender = MagicMock(side_effect=RuntimeError("network down"))
        queue.register_sender("meshtastic", sender)
        queue.enqueue({"text": "hello"}, "meshtastic")

        processed = queue.process_once()
        assert processed == 1

    def test_success_callback(self, queue):
        sender = MagicMock(return_value=True)
        success_cb = MagicMock()
        queue.register_sender("meshtastic", sender)
        queue.register_success_callback(success_cb)
        queue.enqueue({"text": "hello"}, "meshtastic")

        queue.process_once()
        success_cb.assert_called_once()

    def test_failure_callback(self, queue):
        sender = MagicMock(side_effect=RuntimeError("fail"))
        failure_cb = MagicMock()
        queue.register_sender("meshtastic", sender)
        queue.register_failure_callback(failure_cb)
        queue.enqueue({"text": "hello"}, "meshtastic")

        queue.process_once()
        failure_cb.assert_called_once()

    def test_no_sender_registered(self, queue):
        queue.enqueue({"text": "hello"}, "meshtastic")
        processed = queue.process_once()
        assert processed == 0  # No sender for this destination


# ---------------------------------------------------------------------------
# PersistentMessageQueue — background processing
# ---------------------------------------------------------------------------

class TestBackgroundProcessing:
    """Tests for start/stop processing."""

    def test_start_stop(self, queue):
        queue.start_processing(interval=0.01)
        assert queue._processing is True
        queue.stop_processing()
        time.sleep(0.1)
        assert queue._processing is False

    def test_start_idempotent(self, queue):
        queue.start_processing(interval=0.01)
        queue.start_processing(interval=0.01)  # Should be no-op
        queue.stop_processing()

    def test_background_processes_messages(self, queue):
        sender = MagicMock(return_value=True)
        queue.register_sender("meshtastic", sender)
        queue.enqueue({"text": "bg-test"}, "meshtastic")

        queue.start_processing(interval=0.01)
        time.sleep(0.15)
        queue.stop_processing()

        sender.assert_called()


# ---------------------------------------------------------------------------
# PersistentMessageQueue — stats
# ---------------------------------------------------------------------------

class TestStats:
    """Tests for queue statistics."""

    def test_initial_stats(self, queue):
        stats = queue.get_stats()
        assert stats["enqueued"] == 0
        assert stats["pending"] == 0
        assert stats["queue_depth"] == 0
        assert stats["max_queue_size"] == 1000

    def test_stats_after_enqueue(self, queue):
        queue.enqueue({"text": "hello"}, "meshtastic")
        stats = queue.get_stats()
        assert stats["enqueued"] == 1
        assert stats["pending"] == 1
        assert stats["queue_depth"] == 1

    def test_queue_usage_pct(self, queue):
        queue.enqueue({"text": "hello"}, "meshtastic")
        stats = queue.get_stats()
        assert stats["queue_usage_pct"] == 0.1  # 1/1000 * 100

    def test_get_queue_depth(self, queue):
        queue.enqueue({"text": "a"}, "meshtastic")
        queue.enqueue({"text": "b"}, "rns")
        assert queue.get_queue_depth() == 2


# ---------------------------------------------------------------------------
# PersistentMessageQueue — overflow / shedding
# ---------------------------------------------------------------------------

class TestOverflow:
    """Tests for queue overflow management."""

    def test_queue_enforces_max_size(self, tmp_path):
        db_file = str(tmp_path / "overflow1.db")
        q = PersistentMessageQueue(db_path=db_file, max_queue_size=3)
        q.enqueue({"text": "1", "id": 1}, "meshtastic", priority=MessagePriority.LOW, deduplicate=False)
        q.enqueue({"text": "2", "id": 2}, "meshtastic", priority=MessagePriority.LOW, deduplicate=False)
        q.enqueue({"text": "3", "id": 3}, "meshtastic", priority=MessagePriority.LOW, deduplicate=False)
        # Queue is full, next enqueue should shed
        id4 = q.enqueue({"text": "4", "id": 4}, "meshtastic", priority=MessagePriority.NORMAL, deduplicate=False)
        assert id4 is not None
        assert q._stats["shed"] >= 1
        q.stop_processing()

    def test_high_priority_not_shed(self, tmp_path):
        db_file = str(tmp_path / "overflow2.db")
        q = PersistentMessageQueue(db_path=db_file, max_queue_size=2)
        q.enqueue({"text": "hi1"}, "meshtastic", priority=MessagePriority.HIGH, deduplicate=False)
        q.enqueue({"text": "hi2"}, "meshtastic", priority=MessagePriority.HIGH, deduplicate=False)
        # Can't shed high-priority, so next enqueue returns None
        id3 = q.enqueue({"text": "low"}, "meshtastic", priority=MessagePriority.LOW, deduplicate=False)
        assert id3 is None
        assert q._stats["shed_rejected"] >= 1
        q.stop_processing()


# ---------------------------------------------------------------------------
# PersistentMessageQueue — dead letter
# ---------------------------------------------------------------------------

class TestDeadLetter:
    """Tests for dead letter queue."""

    def test_dead_letter_retrieval(self, queue):
        msg_id = queue.enqueue({"text": "test"}, "meshtastic", max_retries=1)
        queue.mark_in_progress(msg_id)
        queue.mark_failed(msg_id, "permanent failure")

        dead = queue.get_dead_letters()
        assert len(dead) == 1
        assert dead[0].id == msg_id

    def test_retry_dead_letter(self, queue):
        msg_id = queue.enqueue({"text": "test"}, "meshtastic", max_retries=1)
        queue.mark_in_progress(msg_id)
        queue.mark_failed(msg_id, "error")

        assert queue.retry_dead_letter(msg_id) is True
        pending = queue.get_pending()
        assert any(m.id == msg_id for m in pending)

    def test_retry_nonexistent_dead_letter(self, queue):
        assert queue.retry_dead_letter("nonexistent") is False


# ---------------------------------------------------------------------------
# PersistentMessageQueue — cleanup
# ---------------------------------------------------------------------------

class TestCleanup:
    """Tests for cleanup and purge operations."""

    def test_clear_all(self, queue):
        queue.enqueue({"text": "a"}, "meshtastic")
        queue.enqueue({"text": "b"}, "rns")
        count = queue.clear_all()
        assert count == 2
        assert queue.get_queue_depth() == 0

    def test_cleanup_stale(self, queue):
        msg_id = queue.enqueue({"text": "test"}, "meshtastic")
        queue.mark_in_progress(msg_id)

        # Manually make it stale by backdating updated_at
        old_time = (datetime.now() - timedelta(seconds=600)).isoformat()
        with queue._get_connection() as conn:
            conn.execute(
                "UPDATE messages SET updated_at = ? WHERE id = ?",
                (old_time, msg_id)
            )

        count = queue.cleanup_stale()
        assert count == 1
        # Should be back to pending
        pending = queue.get_pending()
        assert len(pending) == 1

    def test_purge_old(self, queue):
        msg_id = queue.enqueue({"text": "test"}, "meshtastic")
        queue.mark_in_progress(msg_id)
        queue.mark_delivered(msg_id)

        # Backdate to 10 days ago
        old_time = (datetime.now() - timedelta(days=10)).isoformat()
        with queue._get_connection() as conn:
            conn.execute(
                "UPDATE messages SET updated_at = ? WHERE id = ?",
                (old_time, msg_id)
            )

        count = queue.purge_old(days=7)
        assert count == 1


# ---------------------------------------------------------------------------
# Message lifecycle tracking
# ---------------------------------------------------------------------------

class TestLifecycleTracking:
    """Tests for message lifecycle event recording and retrieval."""

    def test_record_event(self, queue):
        result = queue.record_lifecycle_event(
            "msg-1", MessageLifecycleState.CREATED, details="test"
        )
        assert result is True

    def test_get_message_trace(self, queue):
        queue.record_lifecycle_event("msg-1", MessageLifecycleState.CREATED)
        queue.record_lifecycle_event("msg-1", MessageLifecycleState.QUEUED)
        queue.record_lifecycle_event("msg-1", MessageLifecycleState.SENT)

        trace = queue.get_message_trace("msg-1")
        assert len(trace) == 3
        assert trace[0].state == MessageLifecycleState.CREATED
        assert trace[2].state == MessageLifecycleState.SENT

    def test_trace_empty_for_unknown(self, queue):
        assert queue.get_message_trace("unknown") == []

    def test_get_recent_events(self, queue):
        queue.record_lifecycle_event("msg-1", MessageLifecycleState.CREATED)
        queue.record_lifecycle_event("msg-2", MessageLifecycleState.SENT)

        events = queue.get_recent_events(limit=10)
        assert len(events) == 2

    def test_recent_events_with_filter(self, queue):
        queue.record_lifecycle_event("msg-1", MessageLifecycleState.CREATED)
        queue.record_lifecycle_event("msg-1", MessageLifecycleState.SENT)
        queue.record_lifecycle_event("msg-2", MessageLifecycleState.CREATED)

        sent = queue.get_recent_events(state_filter=MessageLifecycleState.SENT)
        assert len(sent) == 1
        assert sent[0].state == MessageLifecycleState.SENT

    def test_message_summary(self, queue):
        msg_id = queue.enqueue({"text": "hello"}, "meshtastic")
        queue.record_lifecycle_event(msg_id, MessageLifecycleState.CREATED)
        queue.record_lifecycle_event(msg_id, MessageLifecycleState.QUEUED)

        summary = queue.get_message_summary(msg_id)
        assert summary is not None
        assert summary["message_id"] == msg_id
        assert summary["destination"] == "meshtastic"
        assert len(summary["lifecycle"]["states_reached"]) == 2

    def test_message_summary_not_found(self, queue):
        assert queue.get_message_summary("nonexistent") is None

    def test_lifecycle_with_hop_count(self, queue):
        queue.record_lifecycle_event(
            "msg-1", MessageLifecycleState.RELAYED,
            node_id="!relay1", hop_count=2
        )
        trace = queue.get_message_trace("msg-1")
        assert trace[0].hop_count == 2
        assert trace[0].node_id == "!relay1"

    def test_purge_lifecycle_history(self, queue):
        queue.record_lifecycle_event("msg-1", MessageLifecycleState.CREATED)

        # Backdate the entry
        old_time = (datetime.now() - timedelta(days=40)).isoformat()
        with queue._get_connection() as conn:
            conn.execute(
                "UPDATE message_lifecycle SET timestamp = ?",
                (old_time,)
            )

        count = queue.purge_lifecycle_history(days=30)
        assert count == 1

    def test_failed_messages_with_reason(self, queue):
        msg_id = queue.enqueue({"text": "test"}, "meshtastic", max_retries=1)
        queue.mark_in_progress(msg_id)
        queue.mark_failed(msg_id, "radio timeout")
        queue.record_lifecycle_event(
            msg_id, MessageLifecycleState.FAILED, details="radio timeout"
        )

        results = queue.get_failed_messages_with_reason(hours=1)
        assert len(results) == 1
        assert results[0]["error_message"] == "radio timeout"
