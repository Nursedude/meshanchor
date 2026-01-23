"""Tests for message queue overflow protection and cleanup policies."""

import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from gateway.message_queue import (
    PersistentMessageQueue,
    MessagePriority,
    MessageStatus,
)


class TestQueueDepth:
    """Tests for queue depth monitoring."""

    @pytest.fixture
    def queue(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "test_queue.db")
            q = PersistentMessageQueue(db_path=db_path)
            yield q

    def test_empty_queue_depth(self, queue):
        """Empty queue has depth 0."""
        assert queue.get_queue_depth() == 0

    def test_depth_counts_pending(self, queue):
        """Depth counts pending messages."""
        queue.enqueue({"text": "msg1"}, "meshtastic", deduplicate=False)
        queue.enqueue({"text": "msg2"}, "meshtastic", deduplicate=False)
        assert queue.get_queue_depth() == 2

    def test_depth_counts_in_progress(self, queue):
        """Depth counts in_progress messages."""
        msg_id = queue.enqueue({"text": "msg1"}, "meshtastic")
        queue.mark_in_progress(msg_id)
        assert queue.get_queue_depth() == 1

    def test_depth_excludes_delivered(self, queue):
        """Depth does not count delivered messages."""
        msg_id = queue.enqueue({"text": "msg1"}, "meshtastic")
        queue.mark_in_progress(msg_id)
        queue.mark_delivered(msg_id)
        assert queue.get_queue_depth() == 0

    def test_depth_excludes_dead_letter(self, queue):
        """Depth does not count dead_letter messages."""
        msg_id = queue.enqueue({"text": "msg1"}, "meshtastic", max_retries=1)
        queue.mark_in_progress(msg_id)
        queue.mark_failed(msg_id, "fail1")
        # After 1 retry, retry_count is 1, with max_retries=1 it goes to dead_letter
        queue.mark_in_progress(msg_id)
        queue.mark_failed(msg_id, "fail2")
        assert queue.get_queue_depth() == 0


class TestQueueOverflow:
    """Tests for queue size limits and overflow shedding."""

    @pytest.fixture
    def small_queue(self):
        """Queue with small max size for testing overflow."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "test_queue.db")
            q = PersistentMessageQueue(db_path=db_path, max_queue_size=5)
            yield q

    def test_enqueue_within_limit(self, small_queue):
        """Messages enqueue normally when under limit."""
        for i in range(5):
            msg_id = small_queue.enqueue(
                {"text": f"msg{i}"}, "meshtastic", deduplicate=False
            )
            assert msg_id is not None
        assert small_queue.get_queue_depth() == 5

    def test_overflow_sheds_lowest_priority(self, small_queue):
        """Overflow sheds lowest priority messages first."""
        # Fill with LOW priority
        for i in range(5):
            small_queue.enqueue(
                {"text": f"low{i}"}, "meshtastic",
                priority=MessagePriority.LOW, deduplicate=False
            )

        # Enqueue a NORMAL priority — should shed one LOW
        msg_id = small_queue.enqueue(
            {"text": "normal1"}, "meshtastic",
            priority=MessagePriority.NORMAL, deduplicate=False
        )
        assert msg_id is not None
        assert small_queue.get_queue_depth() == 5  # Still at max

    def test_overflow_sheds_oldest_first(self, small_queue):
        """When priorities are equal, oldest messages are shed first."""
        # Fill with LOW priority
        ids = []
        for i in range(5):
            msg_id = small_queue.enqueue(
                {"text": f"low{i}"}, "meshtastic",
                priority=MessagePriority.LOW, deduplicate=False
            )
            ids.append(msg_id)

        # Enqueue another LOW — the oldest LOW should be shed
        new_id = small_queue.enqueue(
            {"text": "low_new"}, "meshtastic",
            priority=MessagePriority.LOW, deduplicate=False
        )
        assert new_id is not None

        # The first enqueued message should be gone
        pending = small_queue.get_pending()
        pending_ids = [m.id for m in pending]
        assert ids[0] not in pending_ids
        assert new_id in pending_ids

    def test_overflow_never_sheds_high_priority(self, small_queue):
        """HIGH and URGENT messages are never shed."""
        # Fill with HIGH priority
        for i in range(5):
            small_queue.enqueue(
                {"text": f"high{i}"}, "meshtastic",
                priority=MessagePriority.HIGH, deduplicate=False
            )

        # Try to enqueue another — should be rejected (can't shed HIGH)
        msg_id = small_queue.enqueue(
            {"text": "another"}, "meshtastic",
            priority=MessagePriority.NORMAL, deduplicate=False
        )
        assert msg_id is None  # Rejected

    def test_overflow_never_sheds_urgent(self, small_queue):
        """URGENT messages are never shed."""
        # Fill with URGENT priority
        for i in range(5):
            small_queue.enqueue(
                {"text": f"urgent{i}"}, "meshtastic",
                priority=MessagePriority.URGENT, deduplicate=False
            )

        msg_id = small_queue.enqueue(
            {"text": "another"}, "meshtastic", deduplicate=False
        )
        assert msg_id is None  # Rejected

    def test_overflow_does_not_shed_in_progress(self, small_queue):
        """In-progress messages are never shed."""
        # Fill queue with 5 messages, mark all in_progress
        ids = []
        for i in range(5):
            msg_id = small_queue.enqueue(
                {"text": f"msg{i}"}, "meshtastic",
                priority=MessagePriority.LOW, deduplicate=False
            )
            small_queue.mark_in_progress(msg_id)
            ids.append(msg_id)

        # Try to enqueue — all are in_progress, can't shed
        msg_id = small_queue.enqueue(
            {"text": "new"}, "meshtastic", deduplicate=False
        )
        assert msg_id is None  # Rejected

    def test_stats_track_shed_count(self, small_queue):
        """Stats track number of shed messages."""
        # Fill queue
        for i in range(5):
            small_queue.enqueue(
                {"text": f"msg{i}"}, "meshtastic",
                priority=MessagePriority.LOW, deduplicate=False
            )

        # Trigger 3 overflows
        for i in range(3):
            small_queue.enqueue(
                {"text": f"new{i}"}, "meshtastic",
                priority=MessagePriority.LOW, deduplicate=False
            )

        stats = small_queue.get_stats()
        assert stats["shed"] == 3

    def test_stats_track_rejected_count(self, small_queue):
        """Stats track number of rejected messages."""
        # Fill with HIGH priority (unshedable)
        for i in range(5):
            small_queue.enqueue(
                {"text": f"high{i}"}, "meshtastic",
                priority=MessagePriority.HIGH, deduplicate=False
            )

        # Try to enqueue 2 more — both rejected
        small_queue.enqueue({"text": "x"}, "meshtastic", deduplicate=False)
        small_queue.enqueue({"text": "y"}, "meshtastic", deduplicate=False)

        stats = small_queue.get_stats()
        assert stats["shed_rejected"] == 2

    def test_unlimited_queue_size(self):
        """max_queue_size=0 means unlimited."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "test_queue.db")
            q = PersistentMessageQueue(db_path=db_path, max_queue_size=0)

            # Should enqueue without limit
            for i in range(50):
                msg_id = q.enqueue(
                    {"text": f"msg{i}"}, "meshtastic", deduplicate=False
                )
                assert msg_id is not None
            assert q.get_queue_depth() == 50

    def test_stats_include_queue_metrics(self, small_queue):
        """Stats include depth, max size, and usage percentage."""
        small_queue.enqueue({"text": "msg1"}, "meshtastic", deduplicate=False)
        small_queue.enqueue({"text": "msg2"}, "meshtastic", deduplicate=False)

        stats = small_queue.get_stats()
        assert stats["queue_depth"] == 2
        assert stats["max_queue_size"] == 5
        assert stats["queue_usage_pct"] == 40.0


class TestCleanupStale:
    """Tests for stale in_progress message recovery."""

    @pytest.fixture
    def queue(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "test_queue.db")
            q = PersistentMessageQueue(db_path=db_path)
            yield q

    def test_cleanup_stale_resets_old_messages(self, queue):
        """Stale in_progress messages are reset to pending."""
        msg_id = queue.enqueue({"text": "test"}, "meshtastic")
        queue.mark_in_progress(msg_id)

        # Manually backdate the updated_at to simulate staleness
        with queue._get_connection() as conn:
            old_time = (datetime.now() - timedelta(seconds=600)).isoformat()
            conn.execute(
                "UPDATE messages SET updated_at = ? WHERE id = ?",
                (old_time, msg_id)
            )

        reset = queue.cleanup_stale()
        assert reset == 1

        # Message should be pending again
        pending = queue.get_pending()
        assert len(pending) == 1
        assert pending[0].id == msg_id

    def test_cleanup_stale_ignores_recent(self, queue):
        """Recently started in_progress messages are not reset."""
        msg_id = queue.enqueue({"text": "test"}, "meshtastic")
        queue.mark_in_progress(msg_id)

        # Should not reset — it's fresh
        reset = queue.cleanup_stale()
        assert reset == 0

    def test_cleanup_stale_ignores_pending(self, queue):
        """Pending messages are not affected by stale cleanup."""
        queue.enqueue({"text": "test"}, "meshtastic")
        reset = queue.cleanup_stale()
        assert reset == 0


class TestAutoCleanup:
    """Tests for automatic periodic cleanup."""

    @pytest.fixture
    def queue(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "test_queue.db")
            q = PersistentMessageQueue(db_path=db_path)
            yield q

    def test_auto_cleanup_triggers_on_interval(self, queue):
        """Auto-cleanup runs when interval has elapsed."""
        # Enqueue and deliver a message
        msg_id = queue.enqueue({"text": "old"}, "meshtastic")
        queue.mark_in_progress(msg_id)
        queue.mark_delivered(msg_id)

        # Backdate the delivered message
        with queue._get_connection() as conn:
            old_time = (datetime.now() - timedelta(days=2)).isoformat()
            conn.execute(
                "UPDATE messages SET updated_at = ? WHERE id = ?",
                (old_time, msg_id)
            )

        # Force cleanup interval to have passed
        queue._last_auto_cleanup = 0

        # Enqueue a new message — should trigger auto-cleanup
        queue.enqueue({"text": "new"}, "rns", deduplicate=False)

        # The old delivered message should be purged
        stats = queue.get_stats()
        assert stats["delivered"] == 0

    def test_auto_cleanup_skips_when_recent(self, queue):
        """Auto-cleanup does not run if interval has not elapsed."""
        msg_id = queue.enqueue({"text": "old"}, "meshtastic")
        queue.mark_in_progress(msg_id)
        queue.mark_delivered(msg_id)

        # Backdate the message
        with queue._get_connection() as conn:
            old_time = (datetime.now() - timedelta(days=2)).isoformat()
            conn.execute(
                "UPDATE messages SET updated_at = ? WHERE id = ?",
                (old_time, msg_id)
            )

        # Set last cleanup to now — should skip
        queue._last_auto_cleanup = time.time()

        # Enqueue another message
        queue.enqueue({"text": "new"}, "rns", deduplicate=False)

        # Old message should NOT be purged (cleanup didn't run)
        stats = queue.get_stats()
        assert stats["delivered"] == 1


class TestOverflowWithMixedPriority:
    """Tests for overflow behavior with mixed priority messages."""

    @pytest.fixture
    def queue(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "test_queue.db")
            q = PersistentMessageQueue(db_path=db_path, max_queue_size=5)
            yield q

    def test_shed_prefers_low_over_normal(self, queue):
        """When both LOW and NORMAL exist, LOW is shed first."""
        # 3 LOW, 2 NORMAL
        low_ids = []
        for i in range(3):
            low_ids.append(queue.enqueue(
                {"text": f"low{i}"}, "meshtastic",
                priority=MessagePriority.LOW, deduplicate=False
            ))
        normal_ids = []
        for i in range(2):
            normal_ids.append(queue.enqueue(
                {"text": f"normal{i}"}, "meshtastic",
                priority=MessagePriority.NORMAL, deduplicate=False
            ))

        # Enqueue HIGH — should shed oldest LOW
        high_id = queue.enqueue(
            {"text": "high1"}, "meshtastic",
            priority=MessagePriority.HIGH, deduplicate=False
        )
        assert high_id is not None

        pending = queue.get_pending()
        pending_ids = [m.id for m in pending]
        assert low_ids[0] not in pending_ids  # Oldest LOW shed
        assert normal_ids[0] in pending_ids   # NORMAL kept
        assert high_id in pending_ids         # HIGH kept

    def test_shed_normal_when_no_low_available(self, queue):
        """NORMAL messages can be shed when no LOW messages exist."""
        # Fill with NORMAL
        normal_ids = []
        for i in range(5):
            normal_ids.append(queue.enqueue(
                {"text": f"normal{i}"}, "meshtastic",
                priority=MessagePriority.NORMAL, deduplicate=False
            ))

        # Enqueue another NORMAL — oldest NORMAL shed
        new_id = queue.enqueue(
            {"text": "normal_new"}, "meshtastic",
            priority=MessagePriority.NORMAL, deduplicate=False
        )
        assert new_id is not None
        assert queue.get_queue_depth() == 5

        pending = queue.get_pending()
        pending_ids = [m.id for m in pending]
        assert normal_ids[0] not in pending_ids

    def test_multiple_sheds_in_succession(self, queue):
        """Multiple rapid enqueues at capacity work correctly."""
        # Fill with LOW
        for i in range(5):
            queue.enqueue(
                {"text": f"low{i}"}, "meshtastic",
                priority=MessagePriority.LOW, deduplicate=False
            )

        # Enqueue 10 more — each should shed one
        for i in range(10):
            msg_id = queue.enqueue(
                {"text": f"new{i}"}, "meshtastic",
                priority=MessagePriority.LOW, deduplicate=False
            )
            assert msg_id is not None

        assert queue.get_queue_depth() == 5
        stats = queue.get_stats()
        assert stats["shed"] == 10
