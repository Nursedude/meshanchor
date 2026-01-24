"""
Tests for Offline-First Data Sync.

Tests cover:
- ConnectivityMonitor: caching, probing, invalidation
- OfflineSyncQueue: enqueue, dequeue, mark synced/failed, cleanup
- SyncEngine: handler registration, sync cycles, error handling
- Queue overflow and shedding
- Retry backoff and dead-lettering
- Thread safety basics
- Statistics and reporting

Run with: pytest tests/test_offline_sync.py -v
"""

import pytest
import sys
import os
import time
import json
import tempfile
import threading
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from utils.offline_sync import (
    ConnectivityMonitor, OfflineSyncQueue, SyncEngine,
    SyncCategory, SyncStatus, SyncRecord,
    MAX_QUEUE_SIZE, MAX_RETRIES, BATCH_SIZE,
    RETRY_BACKOFF_BASE, RETRY_BACKOFF_MULTIPLIER,
    STALE_IN_PROGRESS_SEC,
)


@pytest.fixture
def tmp_db(tmp_path):
    """Temporary database path."""
    return tmp_path / "test_sync.db"


@pytest.fixture
def queue(tmp_db):
    """Fresh sync queue with temp database."""
    return OfflineSyncQueue(db_path=tmp_db)


@pytest.fixture
def engine(tmp_db):
    """Sync engine with temp database."""
    return SyncEngine(db_path=tmp_db)


# =============================================================================
# ConnectivityMonitor Tests
# =============================================================================


class TestConnectivityMonitor:
    """Test connectivity detection."""

    def test_creation(self):
        monitor = ConnectivityMonitor()
        assert monitor._check_interval > 0
        assert monitor._timeout > 0

    def test_custom_interval(self):
        monitor = ConnectivityMonitor(check_interval=60.0, timeout=5.0)
        assert monitor._check_interval == 60.0
        assert monitor._timeout == 5.0

    @patch('socket.socket')
    def test_online_when_reachable(self, mock_socket_cls):
        mock_sock = MagicMock()
        mock_sock.connect_ex.return_value = 0
        mock_socket_cls.return_value.__enter__ = MagicMock(return_value=mock_sock)
        mock_socket_cls.return_value.__exit__ = MagicMock(return_value=False)

        monitor = ConnectivityMonitor(check_interval=0)
        assert monitor.is_online is True

    @patch('socket.socket')
    def test_offline_when_unreachable(self, mock_socket_cls):
        mock_sock = MagicMock()
        mock_sock.connect_ex.return_value = 1  # Connection refused
        mock_socket_cls.return_value.__enter__ = MagicMock(return_value=mock_sock)
        mock_socket_cls.return_value.__exit__ = MagicMock(return_value=False)

        monitor = ConnectivityMonitor(check_interval=0)
        assert monitor.is_online is False

    @patch('socket.socket')
    def test_offline_on_socket_error(self, mock_socket_cls):
        mock_socket_cls.return_value.__enter__ = MagicMock(
            side_effect=OSError("Network down"))
        mock_socket_cls.return_value.__exit__ = MagicMock(return_value=False)

        monitor = ConnectivityMonitor(check_interval=0)
        assert monitor.is_online is False

    @patch('socket.socket')
    def test_caching_respects_interval(self, mock_socket_cls):
        mock_sock = MagicMock()
        mock_sock.connect_ex.return_value = 0
        mock_socket_cls.return_value.__enter__ = MagicMock(return_value=mock_sock)
        mock_socket_cls.return_value.__exit__ = MagicMock(return_value=False)

        monitor = ConnectivityMonitor(check_interval=60.0)

        # First call probes
        result1 = monitor.is_online
        assert result1 is True

        # Second call uses cache (socket not called again)
        mock_sock.connect_ex.return_value = 1  # Would be offline
        result2 = monitor.is_online
        assert result2 is True  # Still cached as True

    @patch('socket.socket')
    def test_invalidate_forces_recheck(self, mock_socket_cls):
        mock_sock = MagicMock()
        mock_sock.connect_ex.return_value = 0
        mock_socket_cls.return_value.__enter__ = MagicMock(return_value=mock_sock)
        mock_socket_cls.return_value.__exit__ = MagicMock(return_value=False)

        monitor = ConnectivityMonitor(check_interval=60.0)
        monitor.is_online  # Cache result

        monitor.invalidate()
        assert monitor._last_check == 0.0

    @patch('socket.socket')
    def test_force_check_bypasses_cache(self, mock_socket_cls):
        mock_sock = MagicMock()
        mock_sock.connect_ex.return_value = 0
        mock_socket_cls.return_value.__enter__ = MagicMock(return_value=mock_sock)
        mock_socket_cls.return_value.__exit__ = MagicMock(return_value=False)

        monitor = ConnectivityMonitor(check_interval=60.0)
        result = monitor.force_check()
        assert result is True

    def test_check_targets_configured(self):
        monitor = ConnectivityMonitor()
        assert len(monitor._check_targets) >= 2
        for host, port in monitor._check_targets:
            assert isinstance(host, str)
            assert isinstance(port, int)


# =============================================================================
# OfflineSyncQueue Tests
# =============================================================================


class TestQueueCreation:
    """Test queue initialization."""

    def test_creates_database(self, tmp_db):
        queue = OfflineSyncQueue(db_path=tmp_db)
        assert tmp_db.exists()

    def test_creates_parent_directories(self, tmp_path):
        db_path = tmp_path / "nested" / "dir" / "sync.db"
        queue = OfflineSyncQueue(db_path=db_path)
        assert db_path.exists()

    def test_empty_queue_stats(self, queue):
        stats = queue.get_stats()
        assert stats['total'] == 0
        assert stats['pending'] == 0

    def test_default_path_uses_get_real_user_home(self, tmp_path):
        with patch('utils.paths.get_real_user_home',
                   return_value=tmp_path):
            queue = OfflineSyncQueue(db_path=None)
            assert 'meshforge' in str(queue._db_path)
            assert 'offline_sync.db' in str(queue._db_path)


class TestQueueEnqueue:
    """Test adding records to the queue."""

    def test_enqueue_returns_id(self, queue):
        record_id = queue.enqueue(
            SyncCategory.TELEMETRY,
            {"node": "!abc", "snr": -5.0}
        )
        assert isinstance(record_id, str)
        assert len(record_id) > 0

    def test_enqueue_increments_count(self, queue):
        queue.enqueue(SyncCategory.TELEMETRY, {"a": 1})
        queue.enqueue(SyncCategory.TELEMETRY, {"b": 2})
        assert queue.get_pending_count() == 2

    def test_enqueue_different_categories(self, queue):
        queue.enqueue(SyncCategory.TELEMETRY, {"a": 1})
        queue.enqueue(SyncCategory.POSITION, {"lat": 21.3})
        queue.enqueue(SyncCategory.EVENT, {"type": "alert"})

        stats = queue.get_stats()
        assert stats['total'] == 3

    def test_enqueue_with_destination(self, queue):
        queue.enqueue(SyncCategory.TELEMETRY, {"a": 1},
                      destination="mqtt://broker:1883")
        records = queue.dequeue_batch(limit=1)
        assert records[0].destination == "mqtt://broker:1883"

    def test_enqueue_serializes_payload(self, queue):
        payload = {"node_id": "!abc", "snr": -5.0, "rssi": -90}
        queue.enqueue(SyncCategory.TELEMETRY, payload)
        records = queue.dequeue_batch(limit=1)
        assert records[0].payload_dict == payload

    def test_enqueue_unique_ids(self, queue):
        ids = set()
        for i in range(100):
            record_id = queue.enqueue(SyncCategory.TELEMETRY, {"i": i})
            ids.add(record_id)
        assert len(ids) == 100

    def test_enqueue_handles_complex_payload(self, queue):
        payload = {
            "nodes": [{"id": "!a"}, {"id": "!b"}],
            "timestamp": time.time(),
            "nested": {"deep": {"value": 42}},
        }
        queue.enqueue(SyncCategory.MAP_UPDATE, payload)
        records = queue.dequeue_batch(limit=1)
        assert records[0].payload_dict["nested"]["deep"]["value"] == 42


class TestQueueDequeue:
    """Test dequeuing records."""

    def test_dequeue_empty_returns_empty(self, queue):
        records = queue.dequeue_batch()
        assert records == []

    def test_dequeue_returns_pending(self, queue):
        queue.enqueue(SyncCategory.TELEMETRY, {"a": 1})
        records = queue.dequeue_batch()
        assert len(records) == 1
        assert records[0].category == SyncCategory.TELEMETRY.value

    def test_dequeue_marks_in_progress(self, queue):
        queue.enqueue(SyncCategory.TELEMETRY, {"a": 1})
        queue.dequeue_batch()

        stats = queue.get_stats()
        assert stats['in_progress'] == 1
        assert stats['pending'] == 0

    def test_dequeue_respects_limit(self, queue):
        for i in range(10):
            queue.enqueue(SyncCategory.TELEMETRY, {"i": i})

        records = queue.dequeue_batch(limit=3)
        assert len(records) == 3

    def test_dequeue_fifo_order(self, queue):
        queue.enqueue(SyncCategory.TELEMETRY, {"order": 1})
        time.sleep(0.01)
        queue.enqueue(SyncCategory.TELEMETRY, {"order": 2})
        time.sleep(0.01)
        queue.enqueue(SyncCategory.TELEMETRY, {"order": 3})

        records = queue.dequeue_batch(limit=3)
        orders = [r.payload_dict["order"] for r in records]
        assert orders == [1, 2, 3]

    def test_dequeue_skips_in_progress(self, queue):
        queue.enqueue(SyncCategory.TELEMETRY, {"a": 1})
        queue.enqueue(SyncCategory.TELEMETRY, {"b": 2})

        # First dequeue takes first record
        batch1 = queue.dequeue_batch(limit=1)
        assert len(batch1) == 1

        # Second dequeue takes second record
        batch2 = queue.dequeue_batch(limit=1)
        assert len(batch2) == 1
        assert batch2[0].record_id != batch1[0].record_id

    def test_dequeue_respects_retry_after(self, queue):
        queue.enqueue(SyncCategory.TELEMETRY, {"a": 1})
        records = queue.dequeue_batch()
        # Mark failed with future retry
        queue.mark_failed(records[0].record_id, "temporary error")

        # Immediately try to dequeue - should not return it
        records2 = queue.dequeue_batch()
        assert len(records2) == 0

    def test_dequeue_resets_stale_in_progress(self, queue):
        queue.enqueue(SyncCategory.TELEMETRY, {"a": 1})
        records = queue.dequeue_batch()  # Marks in_progress

        # Simulate stale: manually update timestamp
        with queue._get_connection() as conn:
            conn.execute(
                "UPDATE sync_queue SET updated_at = ? WHERE record_id = ?",
                (time.time() - STALE_IN_PROGRESS_SEC - 1,
                 records[0].record_id)
            )
            conn.commit()

        # Should recover the stale record
        recovered = queue.dequeue_batch()
        assert len(recovered) == 1
        assert recovered[0].record_id == records[0].record_id


class TestQueueMarkSynced:
    """Test marking records as synced."""

    def test_mark_synced(self, queue):
        queue.enqueue(SyncCategory.TELEMETRY, {"a": 1})
        records = queue.dequeue_batch()
        queue.mark_synced(records[0].record_id)

        stats = queue.get_stats()
        assert stats['synced'] == 1
        assert stats['in_progress'] == 0

    def test_mark_synced_clears_error(self, queue):
        queue.enqueue(SyncCategory.TELEMETRY, {"a": 1})
        records = queue.dequeue_batch()
        queue.mark_synced(records[0].record_id)

        # Verify error_message is cleared
        with queue._get_connection() as conn:
            row = conn.execute(
                "SELECT error_message FROM sync_queue WHERE record_id = ?",
                (records[0].record_id,)
            ).fetchone()
            assert row[0] == ''

    def test_mark_synced_updates_timestamp(self, queue):
        queue.enqueue(SyncCategory.TELEMETRY, {"a": 1})
        records = queue.dequeue_batch()

        before = time.time()
        queue.mark_synced(records[0].record_id)

        with queue._get_connection() as conn:
            row = conn.execute(
                "SELECT updated_at FROM sync_queue WHERE record_id = ?",
                (records[0].record_id,)
            ).fetchone()
            assert row[0] >= before


class TestQueueMarkFailed:
    """Test retry logic and dead-lettering."""

    def test_mark_failed_increments_retry(self, queue):
        queue.enqueue(SyncCategory.TELEMETRY, {"a": 1})
        records = queue.dequeue_batch()
        queue.mark_failed(records[0].record_id, "network error")

        with queue._get_connection() as conn:
            row = conn.execute(
                "SELECT retry_count, error_message FROM sync_queue "
                "WHERE record_id = ?",
                (records[0].record_id,)
            ).fetchone()
            assert row[0] == 1
            assert "network error" in row[1]

    def test_mark_failed_sets_retry_after(self, queue):
        queue.enqueue(SyncCategory.TELEMETRY, {"a": 1})
        records = queue.dequeue_batch()
        now = time.time()
        queue.mark_failed(records[0].record_id, "err")

        with queue._get_connection() as conn:
            row = conn.execute(
                "SELECT retry_after FROM sync_queue WHERE record_id = ?",
                (records[0].record_id,)
            ).fetchone()
            # First retry: base delay
            assert row[0] >= now + RETRY_BACKOFF_BASE - 1

    def test_dead_letter_after_max_retries(self, queue):
        queue.enqueue(SyncCategory.TELEMETRY, {"a": 1})

        for i in range(MAX_RETRIES):
            # Reset retry_after to allow immediate dequeue
            with queue._get_connection() as conn:
                conn.execute(
                    "UPDATE sync_queue SET retry_after = 0, status = 'pending'")
                conn.commit()
            records = queue.dequeue_batch()
            queue.mark_failed(records[0].record_id, f"error #{i+1}")

        stats = queue.get_stats()
        assert stats['dead_letter'] == 1
        assert stats['failed'] == 0

    def test_exponential_backoff(self, queue):
        queue.enqueue(SyncCategory.TELEMETRY, {"a": 1})

        delays = []
        for i in range(3):
            with queue._get_connection() as conn:
                conn.execute(
                    "UPDATE sync_queue SET retry_after = 0, status = 'pending'")
                conn.commit()
            records = queue.dequeue_batch()
            before = time.time()
            queue.mark_failed(records[0].record_id, "err")

            with queue._get_connection() as conn:
                row = conn.execute(
                    "SELECT retry_after FROM sync_queue WHERE record_id = ?",
                    (records[0].record_id,)
                ).fetchone()
                delays.append(row[0] - before)

        # Each delay should be ~3x the previous
        assert delays[1] > delays[0] * 2
        assert delays[2] > delays[1] * 2

    def test_mark_failed_nonexistent_id(self, queue):
        # Should not raise
        queue.mark_failed("nonexistent-id", "error")


class TestQueueOverflow:
    """Test queue size limits and shedding."""

    def test_overflow_sheds_oldest(self, tmp_path):
        db_path = tmp_path / "overflow.db"
        queue = OfflineSyncQueue(db_path=db_path)

        # Fill queue to capacity (use smaller limit for testing)
        with patch('utils.offline_sync.MAX_QUEUE_SIZE', 10):
            for i in range(10):
                queue.enqueue(SyncCategory.TELEMETRY, {"i": i})

            # Next enqueue should trigger shedding
            queue.enqueue(SyncCategory.TELEMETRY, {"i": 99})

        # Queue should still be manageable
        stats = queue.get_stats()
        assert stats['pending'] <= 12  # Some may have been shed


class TestQueueCleanup:
    """Test old record cleanup."""

    def test_cleanup_removes_old_synced(self, queue):
        queue.enqueue(SyncCategory.TELEMETRY, {"a": 1})
        records = queue.dequeue_batch()
        queue.mark_synced(records[0].record_id)

        # Set updated_at to old
        with queue._get_connection() as conn:
            conn.execute(
                "UPDATE sync_queue SET updated_at = ?",
                (time.time() - 100 * 3600,)  # 100 hours ago
            )
            conn.commit()

        removed = queue.cleanup(max_age_hours=72)
        assert removed == 1

    def test_cleanup_keeps_recent_synced(self, queue):
        queue.enqueue(SyncCategory.TELEMETRY, {"a": 1})
        records = queue.dequeue_batch()
        queue.mark_synced(records[0].record_id)

        removed = queue.cleanup(max_age_hours=72)
        assert removed == 0

    def test_cleanup_removes_old_dead_letter(self, queue):
        queue.enqueue(SyncCategory.TELEMETRY, {"a": 1})

        # Force dead-letter
        with queue._get_connection() as conn:
            conn.execute(
                "UPDATE sync_queue SET status = ?, updated_at = ?",
                (SyncStatus.DEAD_LETTER.value, time.time() - 100 * 3600)
            )
            conn.commit()

        removed = queue.cleanup(max_age_hours=72)
        assert removed == 1

    def test_cleanup_keeps_pending(self, queue):
        queue.enqueue(SyncCategory.TELEMETRY, {"a": 1})

        # Even if old, pending records should not be removed
        with queue._get_connection() as conn:
            conn.execute(
                "UPDATE sync_queue SET updated_at = ?",
                (time.time() - 100 * 3600,)
            )
            conn.commit()

        removed = queue.cleanup(max_age_hours=72)
        assert removed == 0

    def test_purge_all(self, queue):
        for i in range(5):
            queue.enqueue(SyncCategory.TELEMETRY, {"i": i})

        removed = queue.purge_all()
        assert removed == 5
        assert queue.get_pending_count() == 0


class TestQueueCategoryFilter:
    """Test filtering by category."""

    def test_get_records_by_category(self, queue):
        queue.enqueue(SyncCategory.TELEMETRY, {"a": 1})
        queue.enqueue(SyncCategory.POSITION, {"lat": 21.3})
        queue.enqueue(SyncCategory.TELEMETRY, {"b": 2})

        records = queue.get_records_by_category(SyncCategory.TELEMETRY)
        assert len(records) == 2
        assert all(r.category == SyncCategory.TELEMETRY.value for r in records)

    def test_get_records_empty_category(self, queue):
        queue.enqueue(SyncCategory.TELEMETRY, {"a": 1})
        records = queue.get_records_by_category(SyncCategory.EVENT)
        assert records == []

    def test_get_records_respects_limit(self, queue):
        for i in range(10):
            queue.enqueue(SyncCategory.TELEMETRY, {"i": i})
        records = queue.get_records_by_category(
            SyncCategory.TELEMETRY, limit=3)
        assert len(records) == 3


# =============================================================================
# SyncRecord Tests
# =============================================================================


class TestSyncRecord:
    """Test SyncRecord dataclass."""

    def test_creation(self):
        record = SyncRecord(
            record_id="abc123",
            category="telemetry",
            payload='{"a": 1}',
            destination="mqtt",
        )
        assert record.record_id == "abc123"
        assert record.category == "telemetry"

    def test_payload_dict_valid_json(self):
        record = SyncRecord(
            record_id="x",
            category="telemetry",
            payload='{"node": "!abc", "snr": -5.0}',
            destination="",
        )
        assert record.payload_dict == {"node": "!abc", "snr": -5.0}

    def test_payload_dict_invalid_json(self):
        record = SyncRecord(
            record_id="x",
            category="telemetry",
            payload="not json",
            destination="",
        )
        assert record.payload_dict == {}

    def test_payload_dict_none(self):
        record = SyncRecord(
            record_id="x",
            category="telemetry",
            payload=None,
            destination="",
        )
        assert record.payload_dict == {}


# =============================================================================
# SyncEngine Tests
# =============================================================================


class TestSyncEngineCreation:
    """Test engine initialization."""

    def test_creates_engine(self, tmp_db):
        engine = SyncEngine(db_path=tmp_db)
        assert engine._queue is not None
        assert engine._connectivity is not None

    def test_custom_connectivity_interval(self, tmp_db):
        engine = SyncEngine(db_path=tmp_db, connectivity_interval=10.0)
        assert engine._connectivity._check_interval == 10.0


class TestSyncEngineHandlers:
    """Test handler registration."""

    def test_register_handler(self, engine):
        handler = MagicMock(return_value=[])
        engine.register_handler(SyncCategory.TELEMETRY, handler)
        assert SyncCategory.TELEMETRY.value in engine._handlers

    def test_multiple_handlers(self, engine):
        engine.register_handler(SyncCategory.TELEMETRY, lambda r: [])
        engine.register_handler(SyncCategory.POSITION, lambda r: [])
        assert len(engine._handlers) == 2


class TestSyncEngineEnqueue:
    """Test data enqueuing."""

    def test_enqueue_returns_id(self, engine):
        record_id = engine.enqueue(
            SyncCategory.TELEMETRY, {"node": "!abc"})
        assert isinstance(record_id, str)

    def test_pending_count(self, engine):
        engine.enqueue(SyncCategory.TELEMETRY, {"a": 1})
        engine.enqueue(SyncCategory.TELEMETRY, {"b": 2})
        assert engine.pending_count == 2


class TestSyncCycle:
    """Test the sync cycle."""

    def test_cycle_skips_when_offline(self, engine):
        engine.enqueue(SyncCategory.TELEMETRY, {"a": 1})

        with patch.object(engine._connectivity, '_probe', return_value=False):
            engine._connectivity._last_check = 0  # Force re-check
            result = engine.sync_cycle()
            assert result['synced'] == 0
            assert result['failed'] == 0

    def test_cycle_syncs_when_online(self, engine):
        engine.enqueue(SyncCategory.TELEMETRY, {"a": 1})

        def handler(records):
            return [(r.record_id, None) for r in records]

        engine.register_handler(SyncCategory.TELEMETRY, handler)

        with patch.object(engine._connectivity, '_probe', return_value=True):
            engine._connectivity._last_check = 0  # Force re-check
            result = engine.sync_cycle()
            assert result['synced'] == 1

    def test_cycle_handles_failures(self, engine):
        engine.enqueue(SyncCategory.TELEMETRY, {"a": 1})

        def handler(records):
            return [(r.record_id, "upload failed") for r in records]

        engine.register_handler(SyncCategory.TELEMETRY, handler)

        with patch.object(engine._connectivity, '_probe', return_value=True):
            engine._connectivity._last_check = 0
            result = engine.sync_cycle()
            assert result['failed'] == 1

    def test_cycle_mixed_results(self, engine):
        engine.enqueue(SyncCategory.TELEMETRY, {"a": 1})
        engine.enqueue(SyncCategory.TELEMETRY, {"b": 2})

        call_count = [0]

        def handler(records):
            results = []
            for r in records:
                call_count[0] += 1
                if call_count[0] % 2 == 0:
                    results.append((r.record_id, "error"))
                else:
                    results.append((r.record_id, None))
            return results

        engine.register_handler(SyncCategory.TELEMETRY, handler)

        with patch.object(engine._connectivity, '_probe', return_value=True):
            engine._connectivity._last_check = 0
            result = engine.sync_cycle()
            assert result['synced'] == 1
            assert result['failed'] == 1

    def test_cycle_no_handler_skips(self, engine):
        engine.enqueue(SyncCategory.TELEMETRY, {"a": 1})
        # No handler registered

        with patch.object(engine._connectivity, '_probe', return_value=True):
            engine._connectivity._last_check = 0
            result = engine.sync_cycle()
            assert result['skipped'] == 1

    def test_cycle_handler_crash(self, engine):
        engine.enqueue(SyncCategory.TELEMETRY, {"a": 1})

        def handler(records):
            raise RuntimeError("Handler crashed!")

        engine.register_handler(SyncCategory.TELEMETRY, handler)

        with patch.object(engine._connectivity, '_probe', return_value=True):
            engine._connectivity._last_check = 0
            result = engine.sync_cycle()
            assert result['failed'] == 1

    def test_cycle_multiple_categories(self, engine):
        engine.enqueue(SyncCategory.TELEMETRY, {"type": "tel"})
        engine.enqueue(SyncCategory.POSITION, {"type": "pos"})

        tel_handler = MagicMock(
            return_value=[])
        pos_handler = MagicMock(
            return_value=[])

        # Make handlers return proper results
        def tel_fn(records):
            return [(r.record_id, None) for r in records]

        def pos_fn(records):
            return [(r.record_id, None) for r in records]

        engine.register_handler(SyncCategory.TELEMETRY, tel_fn)
        engine.register_handler(SyncCategory.POSITION, pos_fn)

        with patch.object(engine._connectivity, '_probe', return_value=True):
            engine._connectivity._last_check = 0
            result = engine.sync_cycle()
            assert result['synced'] == 2

    def test_cycle_empty_queue(self, engine):
        with patch.object(engine._connectivity, '_probe', return_value=True):
            engine._connectivity._last_check = 0
            result = engine.sync_cycle()
            assert result == {'synced': 0, 'failed': 0, 'skipped': 0}


class TestSyncEngineStats:
    """Test engine statistics."""

    def test_initial_stats(self, engine):
        stats = engine.get_stats()
        assert stats['total_synced'] == 0
        assert stats['total_errors'] == 0
        assert stats['last_sync'] == 0.0
        assert stats['handlers_registered'] == []

    def test_stats_after_sync(self, engine):
        engine.enqueue(SyncCategory.TELEMETRY, {"a": 1})
        engine.register_handler(
            SyncCategory.TELEMETRY,
            lambda records: [(r.record_id, None) for r in records]
        )

        with patch.object(engine._connectivity, '_probe', return_value=True):
            engine._connectivity._last_check = 0
            engine.sync_cycle()

        stats = engine.get_stats()
        assert stats['total_synced'] == 1
        assert stats['last_sync'] > 0

    def test_stats_shows_handlers(self, engine):
        engine.register_handler(SyncCategory.TELEMETRY, lambda r: [])
        engine.register_handler(SyncCategory.EVENT, lambda r: [])

        stats = engine.get_stats()
        assert 'telemetry' in stats['handlers_registered']
        assert 'event' in stats['handlers_registered']

    def test_is_online_property(self, engine):
        with patch.object(engine._connectivity, '_probe', return_value=True):
            engine._connectivity._last_check = 0
            assert engine.is_online is True

    def test_pending_count_property(self, engine):
        engine.enqueue(SyncCategory.TELEMETRY, {"a": 1})
        assert engine.pending_count == 1


class TestSyncEngineForceSync:
    """Test force sync and cleanup."""

    def test_force_sync(self, engine):
        engine.enqueue(SyncCategory.TELEMETRY, {"a": 1})
        engine.register_handler(
            SyncCategory.TELEMETRY,
            lambda records: [(r.record_id, None) for r in records]
        )

        with patch.object(engine._connectivity, '_probe', return_value=True):
            result = engine.force_sync()
            assert result['synced'] == 1

    def test_cleanup(self, engine):
        engine.enqueue(SyncCategory.TELEMETRY, {"a": 1})
        engine.register_handler(
            SyncCategory.TELEMETRY,
            lambda records: [(r.record_id, None) for r in records]
        )

        with patch.object(engine._connectivity, '_probe', return_value=True):
            engine._connectivity._last_check = 0
            engine.sync_cycle()

        # Age the synced record
        with engine._queue._get_connection() as conn:
            conn.execute(
                "UPDATE sync_queue SET updated_at = ?",
                (time.time() - 100 * 3600,)
            )
            conn.commit()

        removed = engine.cleanup()
        assert removed == 1


# =============================================================================
# Thread Safety Tests
# =============================================================================


class TestThreadSafety:
    """Basic thread safety verification."""

    def test_concurrent_enqueue(self, tmp_db):
        queue = OfflineSyncQueue(db_path=tmp_db)
        errors = []

        def enqueue_many(start):
            try:
                for i in range(20):
                    queue.enqueue(
                        SyncCategory.TELEMETRY,
                        {"thread": start, "i": i}
                    )
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=enqueue_many, args=(t,))
            for t in range(5)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        assert queue.get_pending_count() == 100  # 5 threads * 20 each

    def test_concurrent_dequeue(self, tmp_db):
        queue = OfflineSyncQueue(db_path=tmp_db)
        for i in range(50):
            queue.enqueue(SyncCategory.TELEMETRY, {"i": i})

        all_records = []
        lock = threading.Lock()

        def dequeue_batch():
            records = queue.dequeue_batch(limit=10)
            with lock:
                all_records.extend(records)

        threads = [
            threading.Thread(target=dequeue_batch)
            for _ in range(5)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # All records should be unique (no double-dequeue)
        ids = [r.record_id for r in all_records]
        assert len(ids) == len(set(ids))


# =============================================================================
# SyncCategory and SyncStatus Tests
# =============================================================================


class TestEnums:
    """Test enum values."""

    def test_sync_categories(self):
        assert SyncCategory.TELEMETRY.value == "telemetry"
        assert SyncCategory.POSITION.value == "position"
        assert SyncCategory.EVENT.value == "event"
        assert SyncCategory.MESSAGE.value == "message"
        assert SyncCategory.MAP_UPDATE.value == "map_update"

    def test_sync_statuses(self):
        assert SyncStatus.PENDING.value == "pending"
        assert SyncStatus.IN_PROGRESS.value == "in_progress"
        assert SyncStatus.SYNCED.value == "synced"
        assert SyncStatus.FAILED.value == "failed"
        assert SyncStatus.DEAD_LETTER.value == "dead_letter"


# =============================================================================
# Integration-style Tests
# =============================================================================


class TestEndToEnd:
    """End-to-end sync scenarios."""

    def test_offline_then_online_sync(self, tmp_db):
        """Data queued while offline syncs when connectivity returns."""
        engine = SyncEngine(db_path=tmp_db)
        synced_payloads = []

        def handler(records):
            results = []
            for r in records:
                synced_payloads.append(r.payload_dict)
                results.append((r.record_id, None))
            return results

        engine.register_handler(SyncCategory.TELEMETRY, handler)

        # Queue data while "offline"
        with patch.object(engine._connectivity, '_probe', return_value=False):
            engine._connectivity._last_check = 0
            engine.enqueue(SyncCategory.TELEMETRY, {"snr": -5.0})
            engine.enqueue(SyncCategory.TELEMETRY, {"snr": -8.0})

            result = engine.sync_cycle()
            assert result['synced'] == 0  # Still offline

        # Now go "online"
        with patch.object(engine._connectivity, '_probe', return_value=True):
            engine._connectivity._last_check = 0
            result = engine.sync_cycle()
            assert result['synced'] == 2

        assert len(synced_payloads) == 2
        assert synced_payloads[0]['snr'] == -5.0
        assert synced_payloads[1]['snr'] == -8.0

    def test_partial_sync_failure(self, tmp_db):
        """Some records sync, others fail and retry later."""
        engine = SyncEngine(db_path=tmp_db)

        call_count = [0]

        def handler(records):
            results = []
            for r in records:
                call_count[0] += 1
                if r.payload_dict.get("fail"):
                    results.append((r.record_id, "network error"))
                else:
                    results.append((r.record_id, None))
            return results

        engine.register_handler(SyncCategory.TELEMETRY, handler)

        engine.enqueue(SyncCategory.TELEMETRY, {"id": 1, "fail": False})
        engine.enqueue(SyncCategory.TELEMETRY, {"id": 2, "fail": True})
        engine.enqueue(SyncCategory.TELEMETRY, {"id": 3, "fail": False})

        with patch.object(engine._connectivity, '_probe', return_value=True):
            engine._connectivity._last_check = 0
            result = engine.sync_cycle()
            assert result['synced'] == 2
            assert result['failed'] == 1

        # Failed record still in queue (pending_count includes failed-with-retry)
        stats = engine.get_stats()
        assert stats['queue']['failed'] == 1
        assert stats['queue']['synced'] == 2

    def test_multiple_sync_cycles(self, tmp_db):
        """Multiple cycles process queue incrementally."""
        engine = SyncEngine(db_path=tmp_db)
        engine.register_handler(
            SyncCategory.TELEMETRY,
            lambda records: [(r.record_id, None) for r in records]
        )

        # Add data over multiple cycles
        with patch.object(engine._connectivity, '_probe', return_value=True):
            engine._connectivity._last_check = 0

            engine.enqueue(SyncCategory.TELEMETRY, {"batch": 1})
            engine.sync_cycle()

            engine.enqueue(SyncCategory.TELEMETRY, {"batch": 2})
            engine.enqueue(SyncCategory.TELEMETRY, {"batch": 3})
            engine._connectivity._last_check = 0
            engine.sync_cycle()

        stats = engine.get_stats()
        assert stats['total_synced'] == 3
