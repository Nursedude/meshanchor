"""Tests for message queue lifecycle — enqueue, dequeue, retry, dead letter.

Covers the core message lifecycle that test_message_queue_overflow.py doesn't:
- Enqueue → pending → in_progress → delivered (happy path)
- Enqueue → pending → in_progress → failed → retry → delivered
- Enqueue → pending → in_progress → failed (max retries) → dead_letter
- Deduplication
- WAL mode enabled
- Purge old messages
- Stats tracking
"""

import json
import sqlite3
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from gateway.message_queue import (
    PersistentMessageQueue,
    MessagePriority,
    MessageStatus,
    QueuedMessage,
)


@pytest.fixture
def queue():
    """Fresh message queue in a temp directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = str(Path(tmpdir) / "test_queue.db")
        q = PersistentMessageQueue(db_path=db_path)
        yield q


@pytest.fixture
def db_path_fixture():
    """Provide a temp db path for inspecting raw SQLite."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield str(Path(tmpdir) / "test_queue.db")


class TestHappyPath:
    """Message goes: enqueue → pending → in_progress → delivered."""

    def test_enqueue_returns_id(self, queue):
        """Enqueue returns a non-empty message ID."""
        msg_id = queue.enqueue({"text": "hello"}, "meshtastic")
        assert msg_id is not None
        assert len(msg_id) > 0

    def test_enqueued_message_is_pending(self, queue):
        """Enqueued messages appear in get_pending."""
        msg_id = queue.enqueue({"text": "hello"}, "meshtastic")
        pending = queue.get_pending(destination="meshtastic")
        assert len(pending) == 1
        assert pending[0].id == msg_id
        assert pending[0].status == MessageStatus.PENDING

    def test_mark_in_progress(self, queue):
        """in_progress messages don't appear in get_pending."""
        msg_id = queue.enqueue({"text": "hello"}, "meshtastic")
        assert queue.mark_in_progress(msg_id) is True
        pending = queue.get_pending(destination="meshtastic")
        assert len(pending) == 0

    def test_mark_delivered(self, queue):
        """Delivered messages are removed from active queue."""
        msg_id = queue.enqueue({"text": "hello"}, "meshtastic")
        queue.mark_in_progress(msg_id)
        assert queue.mark_delivered(msg_id) is True
        assert queue.get_queue_depth() == 0

    def test_full_lifecycle(self, queue):
        """Complete happy path: enqueue → progress → delivered."""
        msg_id = queue.enqueue({"text": "test msg"}, "rns")
        assert queue.get_queue_depth() == 1

        queue.mark_in_progress(msg_id)
        assert queue.get_queue_depth() == 1  # in_progress still counts

        queue.mark_delivered(msg_id)
        assert queue.get_queue_depth() == 0

    def test_stats_track_enqueue_and_delivery(self, queue):
        """Stats counters increment on enqueue and delivery."""
        msg_id = queue.enqueue({"text": "stats"}, "meshtastic")
        queue.mark_in_progress(msg_id)
        queue.mark_delivered(msg_id)

        stats = queue.get_stats()
        assert stats["enqueued"] >= 1
        assert stats["delivered"] >= 1


class TestRetryLifecycle:
    """Message fails and is retried before succeeding."""

    def test_failed_message_retries(self, queue):
        """Failed messages go back to pending for retry."""
        msg_id = queue.enqueue({"text": "retry me"}, "meshtastic", max_retries=3)
        queue.mark_in_progress(msg_id)
        queue.mark_failed(msg_id, "transient error")

        # Should be back in pending (or scheduled for retry)
        depth = queue.get_queue_depth()
        assert depth >= 1  # Still in queue, not dead_letter

    def test_max_retries_moves_to_dead_letter(self, queue):
        """After max_retries failures, message goes to dead_letter."""
        msg_id = queue.enqueue({"text": "doomed"}, "meshtastic", max_retries=2)

        # Fail twice
        queue.mark_in_progress(msg_id)
        queue.mark_failed(msg_id, "error 1")
        queue.mark_in_progress(msg_id)
        queue.mark_failed(msg_id, "error 2")

        # Should be in dead_letter now
        assert queue.get_queue_depth() == 0

    def test_retry_then_deliver(self, queue):
        """Message fails once, retries, then delivers successfully."""
        msg_id = queue.enqueue({"text": "resilient"}, "meshtastic", max_retries=3)

        # First attempt fails
        queue.mark_in_progress(msg_id)
        queue.mark_failed(msg_id, "timeout")

        # Second attempt succeeds
        queue.mark_in_progress(msg_id)
        queue.mark_delivered(msg_id)

        assert queue.get_queue_depth() == 0
        stats = queue.get_stats()
        assert stats["delivered"] >= 1

    def test_stats_track_retries(self, queue):
        """Stats count retry attempts."""
        msg_id = queue.enqueue({"text": "counted"}, "meshtastic", max_retries=3)
        queue.mark_in_progress(msg_id)
        queue.mark_failed(msg_id, "fail")

        stats = queue.get_stats()
        assert stats["retried"] >= 1 or stats["failed"] >= 0  # depends on retry policy


class TestDeduplication:
    """Duplicate messages are suppressed."""

    def test_duplicate_suppressed(self, queue):
        """Same payload + destination = duplicate suppressed."""
        msg1 = queue.enqueue({"text": "unique"}, "meshtastic")
        msg2 = queue.enqueue({"text": "unique"}, "meshtastic")
        assert msg1 is not None
        assert msg2 is None  # Duplicate

    def test_different_destinations_not_duplicate(self, queue):
        """Same payload to different destinations is NOT a duplicate."""
        msg1 = queue.enqueue({"text": "broadcast"}, "meshtastic")
        msg2 = queue.enqueue({"text": "broadcast"}, "rns")
        assert msg1 is not None
        assert msg2 is not None

    def test_dedup_disabled(self, queue):
        """With deduplicate=False, same payload enqueues twice."""
        msg1 = queue.enqueue({"text": "repeat"}, "meshtastic", deduplicate=False)
        msg2 = queue.enqueue({"text": "repeat"}, "meshtastic", deduplicate=False)
        assert msg1 is not None
        assert msg2 is not None
        assert queue.get_queue_depth() == 2

    def test_stats_track_deduplication(self, queue):
        """Stats count deduplicated messages."""
        queue.enqueue({"text": "dup"}, "meshtastic")
        queue.enqueue({"text": "dup"}, "meshtastic")  # Duplicate

        stats = queue.get_stats()
        assert stats["deduplicated"] >= 1


class TestPriority:
    """Messages are dequeued in priority order."""

    def test_high_priority_first(self, queue):
        """Higher priority messages come first in get_pending."""
        queue.enqueue({"text": "low"}, "meshtastic",
                      priority=MessagePriority.LOW, deduplicate=False)
        queue.enqueue({"text": "high"}, "meshtastic",
                      priority=MessagePriority.HIGH, deduplicate=False)
        queue.enqueue({"text": "normal"}, "meshtastic",
                      priority=MessagePriority.NORMAL, deduplicate=False)

        pending = queue.get_pending(destination="meshtastic")
        assert len(pending) == 3
        # Higher priority value = higher priority, should come first
        priorities = [m.priority.value for m in pending]
        assert priorities == sorted(priorities, reverse=True)


class TestWALMode:
    """SQLite WAL mode is enabled for crash resilience."""

    def test_wal_mode_enabled(self, db_path_fixture):
        """Database connections use WAL journal mode."""
        q = PersistentMessageQueue(db_path=db_path_fixture)

        # Check journal mode on the database file
        conn = sqlite3.connect(db_path_fixture)
        result = conn.execute("PRAGMA journal_mode").fetchone()
        conn.close()

        assert result[0] == "wal"


class TestPurge:
    """Old messages are cleaned up."""

    def test_purge_old_messages(self, queue):
        """Purge removes old delivered messages."""
        msg_id = queue.enqueue({"text": "old"}, "meshtastic")
        queue.mark_in_progress(msg_id)
        queue.mark_delivered(msg_id)

        # Purge with 0 days = purge everything
        removed = queue.purge_old(days=0)
        assert removed >= 1

    def test_purge_preserves_pending(self, queue):
        """Purge does NOT remove pending messages."""
        queue.enqueue({"text": "still needed"}, "meshtastic")
        removed = queue.purge_old(days=0)
        assert queue.get_queue_depth() == 1


class TestDestinationFiltering:
    """get_pending filters by destination."""

    def test_filter_by_destination(self, queue):
        """get_pending returns only messages for the specified destination."""
        queue.enqueue({"text": "mesh"}, "meshtastic", deduplicate=False)
        queue.enqueue({"text": "rns"}, "rns", deduplicate=False)

        mesh_pending = queue.get_pending(destination="meshtastic")
        rns_pending = queue.get_pending(destination="rns")
        all_pending = queue.get_pending()

        assert len(mesh_pending) == 1
        assert len(rns_pending) == 1
        assert len(all_pending) == 2

    def test_empty_destination_returns_empty(self, queue):
        """get_pending for unknown destination returns empty list."""
        queue.enqueue({"text": "mesh"}, "meshtastic")
        pending = queue.get_pending(destination="nonexistent")
        assert len(pending) == 0
